"""
Tests for Part 8: Exactly-Once Execution & Crash Recovery Protocol.
1. Trap T2: Human double-clicks approve on a refund -> One refund, one ledger entry.
2. Trap T3: Worker killed after refund call but before status write ->
   On resume, no second refund (verified on target platform, then marked done).
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.core.clock import Clock
from app.db.models import Base, Incident, IncidentAction
from app.db.session import get_db_session
from app.main import app
from app.engine.executor import ExactlyOnceExecutor, WorkerKilledMidActionException
from app.worker.runner import IncidentWorker
from mock_platform.state import PlatformState


@pytest.fixture(autouse=True)
def setup_teardown():
    Clock.reset()
    PlatformState.reset()
    yield
    Clock.reset()
    PlatformState.reset()


@pytest.fixture
async def test_db():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db
    yield session_factory
    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_t2_double_click_approve_produces_single_refund(test_db):
    """
    TRAP T2: Human double-clicks approve on a refund.
    Expected: Exactly one refund; exactly one ledger entry.
    """
    # 1. Setup incident awaiting approval for a $4.50 refund on job_101
    now = Clock.now()
    now_iso = Clock.now_iso()
    PlatformState.providers["runway"]["status"] = "OUT_OF_FUNDS"

    async with test_db() as session:
        inc = Incident(
            id="inc_t2_double_click",
            tenant_id="tenant_alpha",
            fingerprint="fp_t2",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="AWAITING_APPROVAL",
            current_node="await_approval",
            state_data={
                "evidence": {
                    "job": {"data": {"job": {"job_id": "job_101"}}},
                    "ledger": {"data": {"total_charged_cents": 450, "net_charged_cents": 450}},
                    "provider_status": {
                        "as_of": now_iso,
                        "ttl_seconds": 120,
                        "data": {"provider_id": "runway", "status": "OUT_OF_FUNDS"}
                    }
                },
                "proposed_actions": [
                    {
                        "action_type": "refund_credits",
                        "target_id": "job_101",
                        "parameters": {"amount_cents": 450, "reason": "Triage refund"},
                        "idempotency_key": "t2_idempotency_key_refund_job_101"
                    }
                ]
            },
            created_at=now,
            updated_at=now
        )
        session.add(inc)
        await session.commit()

    # Initial ledger refunds count
    initial_refunds = [
        e for e in PlatformState.ledger
        if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
    ]
    assert len(initial_refunds) == 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First click: Approve
        resp1 = await client.post(
            "/v1/incidents/inc_t2_double_click/approve",
            json={"approver": "ops_engineer@cornflakes.com", "reason": "First click"}
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "APPROVED_HUMAN"

        # Second click: Double-click right away!
        resp2 = await client.post(
            "/v1/incidents/inc_t2_double_click/approve",
            json={"approver": "ops_engineer@cornflakes.com", "reason": "Second click accidental"}
        )
        # Second click MUST be rejected because incident is already approved
        assert resp2.status_code == 400
        assert "Cannot approve" in resp2.json()["detail"]

    # Run worker to execute the approved action
    async with test_db() as session:
        worker = IncidentWorker(worker_id="worker-1")
        await worker.run_incident_to_pause("inc_t2_double_click", session)

        # Check final incident status in DB
        db_inc = await session.get(Incident, "inc_t2_double_click")
        assert db_inc.status == "COMPLETED"

    # CRUCIAL ASSERTION: Ledger has strictly ONE refund entry!
    final_refunds = [
        e for e in PlatformState.ledger
        if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
    ]
    assert len(final_refunds) == 1
    assert final_refunds[0]["amount_cents"] == 450
    assert final_refunds[0]["idempotency_key"] == "t2_idempotency_key_refund_job_101"


@pytest.mark.asyncio
async def test_t3_worker_killed_after_refund_call_before_status_write(test_db):
    """
    TRAP T3: Worker killed after refund call but before status write.
    Expected: On resume, no second refund (verified on platform, then marked done).
    """
    action_dict = {
        "action_type": "refund_credits",
        "target_id": "job_101",
        "parameters": {"amount_cents": 450, "reason": "Crash recovery test"},
        "idempotency_key": "t3_crash_recovery_refund_key"
    }

    # Initial ledger refunds count
    initial_refunds = [
        e for e in PlatformState.ledger
        if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
    ]
    assert len(initial_refunds) == 0

    # 1. Flight 1: Worker starts action, platform executes refund, but worker dies with SIGKILL!
    async with test_db() as session:
        with pytest.raises(WorkerKilledMidActionException):
            await ExactlyOnceExecutor.execute_action(
                action_dict=action_dict,
                incident_id="inc_t3_crash",
                db=session,
                simulate_worker_kill=True
            )

        # Check DB state at crash time: Action row exists but was left IN_FLIGHT
        stmt = select(IncidentAction).where(IncidentAction.idempotency_key == "t3_crash_recovery_refund_key")
        act_res = await session.execute(stmt)
        crashed_action = act_res.scalars().first()
        assert crashed_action is not None
        assert crashed_action.status == "IN_FLIGHT"

    # Platform ledger has 1 refund from the call before the crash
    mid_refunds = [
        e for e in PlatformState.ledger
        if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
    ]
    assert len(mid_refunds) == 1

    # 2. Flight 2: Worker revives and resumes execution
    async with test_db() as session2:
        resumed_res = await ExactlyOnceExecutor.execute_action(
            action_dict=action_dict,
            incident_id="inc_t3_crash",
            db=session2,
            simulate_worker_kill=False
        )

        # Worker must have reconciled without calling refund again!
        assert resumed_res["status"] == "reconciled_after_crash"

        # Check DB state: Local status is now cleanly COMPLETED
        stmt = select(IncidentAction).where(IncidentAction.idempotency_key == "t3_crash_recovery_refund_key")
        act_res2 = await session2.execute(stmt)
        reconciled_action = act_res2.scalars().first()
        assert reconciled_action.status == "COMPLETED"

    # CRUCIAL ASSERTION FOR T3: Platform ledger STILL has strictly ONE refund entry!
    final_refunds = [
        e for e in PlatformState.ledger
        if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
    ]
    assert len(final_refunds) == 1, (
        f"Expected exactly 1 refund in ledger, found {len(final_refunds)}! Double refund occurred!"
    )
    assert final_refunds[0]["idempotency_key"] == "t3_crash_recovery_refund_key"
