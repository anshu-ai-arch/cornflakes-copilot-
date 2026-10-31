"""
Tests for Part 4:
1. State Machine Sequential Progression (triage -> diagnose -> propose -> policy -> await_approval)
2. Trap T5: Workflow pauses at AWAITING_APPROVAL and survives restarts with zero worker held.
3. Multi-Worker Lease Concurrency & Crash Recovery (Worker 1 vs Worker 2).
"""

import pytest
import pytest_asyncio
from datetime import timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.core.clock import Clock
from app.db.models import Base, Incident, IncidentAction, IncidentEvent
from app.engine.runner import advance_incident_state
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
async def test_session_factory():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_state_machine_progression_to_approval(test_session_factory):
    """
    Verify linear node progression: triage -> diagnose -> propose -> policy -> await_approval.
    Check that state checkpoints are persisted in DB after every node.
    """
    async with test_session_factory() as session:
        # Create an incident for job_101
        now = Clock.now()
        incident = Incident(
            id="inc_progression_1",
            tenant_id="tenant_alpha",
            fingerprint="fp_progression_1",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="QUEUED",
            current_node="triage",
            state_data={"alert": {"entity_id": "job_101"}},
            created_at=now,
            updated_at=now
        )
        session.add(incident)
        await session.commit()

        # Step 1: Run triage node
        can_continue = await advance_incident_state(incident, session, "worker-1")
        assert can_continue is True
        assert incident.current_node == "diagnose"
        assert incident.status == "TRIAGED"
        assert "job" in incident.state_data["evidence"]
        assert "ledger" in incident.state_data["evidence"]
        assert incident.state_data["evidence"]["job"]["data"]["job"]["job_id"] == "job_101"

        # Step 2: Run diagnose node
        can_continue = await advance_incident_state(incident, session, "worker-1")
        assert can_continue is True
        assert incident.current_node == "propose"
        assert incident.status == "DIAGNOSED"
        assert incident.state_data["diagnosis"]["category"] == "PROVIDER_OUT_OF_FUNDS"
        assert "RB-001" in incident.state_data["diagnosis"]["citations"]

        # Step 3: Run propose node
        can_continue = await advance_incident_state(incident, session, "worker-1")
        assert can_continue is True
        assert incident.current_node == "policy"
        assert incident.status == "PROPOSED"
        actions = incident.state_data["proposed_actions"]
        assert len(actions) == 2
        types = [a["action_type"] for a in actions]
        assert "disable_provider" in types
        assert "refund_credits" in types

        # Step 4: Run policy node -> should pause at await_approval
        can_continue = await advance_incident_state(incident, session, "worker-1")
        assert can_continue is False  # Pauses!
        assert incident.current_node == "await_approval"
        assert incident.status == "AWAITING_APPROVAL"
        assert incident.state_data["policy_verdict"]["requires_human"] is True

        # Check that proposed actions were persisted to incident_actions table with idempotency keys
        stmt = select(IncidentAction).where(IncidentAction.incident_id == "inc_progression_1")
        act_res = await session.execute(stmt)
        act_rows = act_res.scalars().all()
        assert len(act_rows) == 2
        for r in act_rows:
            assert r.idempotency_key is not None
            assert r.status == "PENDING"


@pytest.mark.asyncio
async def test_t5_survives_restarts_while_awaiting_approval(test_session_factory):
    """
    TRAP T5: All services restarted while waiting for approval.
    Expected: Incident resumes in the exact same state without loss.
    """
    # 1. Setup incident that has progressed to AWAITING_APPROVAL
    async with test_session_factory() as session:
        now = Clock.now()
        incident = Incident(
            id="inc_t5_survive",
            tenant_id="tenant_alpha",
            fingerprint="fp_t5",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="QUEUED",
            current_node="triage",
            state_data={"alert": {"entity_id": "job_101"}},
            created_at=now,
            updated_at=now
        )
        session.add(incident)
        await session.commit()

        # Run until pause
        worker1 = IncidentWorker(worker_id="worker-1")
        await worker1.run_incident_to_pause("inc_t5_survive", session)

        # Verify state in DB
        db_inc = await session.get(Incident, "inc_t5_survive")
        assert db_inc.status == "AWAITING_APPROVAL"
        assert db_inc.current_node == "await_approval"
        assert db_inc.lease_worker_id is None  # Zero worker memory held!

    # 2. Simulate complete restart & time jump (2 days later)
    Clock.advance(172800)  # 2 days

    # 3. Re-open database connection as a revived worker
    async with test_session_factory() as session2:
        revived_inc = await session2.get(Incident, "inc_t5_survive")
        assert revived_inc is not None
        assert revived_inc.status == "AWAITING_APPROVAL"
        assert revived_inc.current_node == "await_approval"
        assert "evidence" in revived_inc.state_data
        assert "diagnosis" in revived_inc.state_data
        assert len(revived_inc.state_data["proposed_actions"]) == 2
        # Lease remains free for human approval
        assert revived_inc.lease_worker_id is None


@pytest.mark.asyncio
async def test_multi_worker_concurrency_and_lease_claiming(test_session_factory):
    """
    Verify concurrency safety with 2 workers:
    - Worker 1 claims task; Worker 2 gets None (no duplicate processing).
    - If Worker 1 dies, Worker 2 re-claims after lease expiry.
    """
    async with test_session_factory() as session:
        now = Clock.now()
        inc = Incident(
            id="inc_lease_test",
            tenant_id="tenant_alpha",
            fingerprint="fp_lease_test",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="QUEUED",
            current_node="triage",
            state_data={},
            created_at=now,
            updated_at=now
        )
        session.add(inc)
        await session.commit()

        worker1 = IncidentWorker(worker_id="worker-1", lease_seconds=30)
        worker2 = IncidentWorker(worker_id="worker-2", lease_seconds=30)

        # Worker 1 claims task
        claimed_by_w1 = await worker1.claim_task(session)
        assert claimed_by_w1 is not None
        assert claimed_by_w1.lease_worker_id == "worker-1"

        # Worker 2 attempts to claim simultaneously -> Should return None
        claimed_by_w2 = await worker2.claim_task(session)
        assert claimed_by_w2 is None, "Worker 2 must not claim a task already leased by Worker 1"

        # Simulate Worker 1 crashing: Advance clock by 35s (exceeding 30s lease)
        Clock.advance(35)

        # Worker 2 attempts claim now -> Should successfully reclaim abandoned task!
        reclaimed_by_w2 = await worker2.claim_task(session)
        assert reclaimed_by_w2 is not None
        assert reclaimed_by_w2.id == "inc_lease_test"
        assert reclaimed_by_w2.lease_worker_id == "worker-2"
