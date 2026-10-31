"""
Tests for Part 7:
1. Trap T4: Approval arrives 2 days later; provider already recovered -> Re-validated and rejected with explanation.
2. Trap T6: Provider status is 2 days old -> Stale data detected; marked STALE and unapprovable.
3. Legitimate human approval transition to execute.
4. Human rejection flow.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.clock import Clock
from app.db.models import Base, Incident
from app.db.session import get_db_session
from app.main import app
from app.engine.staleness import StalenessGuard
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
async def test_t4_approval_arrives_two_days_later_provider_recovered(test_db):
    """
    TRAP T4: Approval arrives two days later; provider already recovered.
    Expected: Re-validated; rejected with explanation.
    """
    # 1. Setup incident awaiting approval to disable provider 'runway'
    now = Clock.now()
    now_iso = Clock.now_iso()
    async with test_db() as session:
        inc = Incident(
            id="inc_t4_revalidation",
            tenant_id="tenant_alpha",
            fingerprint="fp_t4",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="AWAITING_APPROVAL",
            current_node="await_approval",
            state_data={
                "evidence": {
                    "provider_status": {
                        "as_of": now_iso,
                        "ttl_seconds": 300000,  # TTL long enough to allow testing revalidation logic
                        "data": {"provider_id": "runway", "status": "OUT_OF_FUNDS"}
                    }
                },
                "proposed_actions": [
                    {
                        "action_type": "disable_provider",
                        "target_id": "runway",
                        "parameters": {"reason": "Account out of funds"},
                        "idempotency_key": "key_disable_runway"
                    }
                ]
            },
            created_at=now,
            updated_at=now
        )
        session.add(inc)
        await session.commit()

    # 2. Fast forward virtual clock by 2 days (172800s)
    Clock.advance(172800)

    # 3. In the meantime, provider runway was topped up and recovered to HEALTHY!
    PlatformState.providers["runway"]["status"] = "HEALTHY"
    PlatformState.providers["runway"]["balance_cents"] = 50000

    # 4. Human arrives 2 days later and clicks 'Approve'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/incidents/inc_t4_revalidation/approve",
            json={
                "approver": "ops_director@cornflakes.com",
                "reason": "Late review, approving disable"
            }
        )

        # Must reject with 409 Conflict
        assert resp.status_code == 409
        data = resp.json()
        assert data["status"] == "REJECTED_STALE_CONDITION"
        # Must clearly explain that provider already recovered!
        explanation = data["rejection_reason"]
        assert "Condition changed" in explanation
        assert "runway" in explanation
        assert "HEALTHY" in explanation
        assert "Disabling a healthy provider is disallowed" in explanation

    # 5. Verify database state is marked REJECTED_STALE_CONDITION
    async with test_db() as session:
        db_inc = await session.get(Incident, "inc_t4_revalidation")
        assert db_inc.status == "REJECTED_STALE_CONDITION"
        assert db_inc.current_node == "rejected"
        assert db_inc.state_data["approval"]["status"] == "REJECTED_STALE_CONDITION"


def test_t6_staleness_guard_two_day_old_provider_status():
    """
    TRAP T6: Provider status is 2 days old.
    Expected: Detected as stale, plain English description, marked unapprovable.
    """
    now = Clock.now()
    initial_iso = Clock.now_iso()

    # Fast forward clock 2 days (172800s)
    Clock.advance(172800)

    evidence = {
        "provider_status": {
            "as_of": initial_iso,  # 2 days old!
            "ttl_seconds": 60,     # TTL is 60s
            "source": "platform_provider_registry",
            "data": {"provider_id": "runway", "status": "OUT_OF_FUNDS"}
        }
    }

    # Evaluate without allowing automatic refetch to test stale blocking
    is_stale, updated_ev, warnings = StalenessGuard.evaluate_evidence_staleness(
        evidence, allow_refetch=False
    )

    assert is_stale is True
    assert len(warnings) == 1
    warning_text = warnings[0]
    # Verify plain words mention 2 day(s) old and 60s limit
    assert "2 day(s) old" in warning_text
    assert "limit of 60s" in warning_text
    assert "proposal blocked from approval" in warning_text


@pytest.mark.asyncio
async def test_valid_approval_and_rejection_flows(test_db):
    """Verify legitimate approval transitions to execute, and rejection works."""
    now = Clock.now()
    now_iso = Clock.now_iso()

    # Provider runway is failing
    PlatformState.providers["runway"]["status"] = "OUT_OF_FUNDS"

    async with test_db() as session:
        inc = Incident(
            id="inc_valid_approval",
            tenant_id="tenant_alpha",
            fingerprint="fp_valid_approval",
            error_type="OUT_OF_FUNDS",
            entity_id="job_101",
            status="AWAITING_APPROVAL",
            current_node="await_approval",
            state_data={
                "evidence": {
                    "provider_status": {
                        "as_of": now_iso,
                        "ttl_seconds": 120,
                        "data": {"provider_id": "runway", "status": "OUT_OF_FUNDS"}
                    }
                },
                "proposed_actions": [
                    {
                        "action_type": "disable_provider",
                        "target_id": "runway",
                        "parameters": {"reason": "Out of funds"},
                        "idempotency_key": "valid_key_1"
                    }
                ]
            },
            created_at=now,
            updated_at=now
        )
        session.add(inc)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Legitimate approve
        approve_resp = await client.post(
            "/v1/incidents/inc_valid_approval/approve",
            json={
                "approver": "anshu.tiwari@cornflakes.com",
                "reason": "Confirmed provider outage"
            }
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "APPROVED_HUMAN"
        assert approve_resp.json()["current_node"] == "execute"

        # Check rejection endpoint on another incident
        async with test_db() as session:
            inc2 = Incident(
                id="inc_to_reject",
                tenant_id="tenant_alpha",
                fingerprint="fp_to_reject",
                error_type="OUT_OF_FUNDS",
                entity_id="job_101",
                status="AWAITING_APPROVAL",
                current_node="await_approval",
                state_data={},
                created_at=now,
                updated_at=now
            )
            session.add(inc2)
            await session.commit()

        reject_resp = await client.post(
            "/v1/incidents/inc_to_reject/reject",
            json={
                "approver": "anshu.tiwari@cornflakes.com",
                "reason": "Not needed"
            }
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "REJECTED"
