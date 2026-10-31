"""
Tests for Part 10: UI and End-to-End Live Scenario Progression.
Verifies:
1. GET / serves the elegant cream operations UI.
2. Provider Out of Funds scenario moves from Ingest -> Triage -> Propose -> Await Approval -> Approve -> Complete.
3. False alarm scenario correctly blocked by Policy Engine (Trap T10).
4. Simulation state endpoints work as expected.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.clock import Clock
from app.db.models import Base, Incident
from app.db.session import get_db_session
from app.main import app
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
async def test_ui_served_and_endpoints(test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. UI served
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Cornflakes Media" in resp.text
        assert "Live Agent Scenarios" in resp.text

        # 2. Metrics endpoint
        metrics_resp = await client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert "incidents_total" in metrics_resp.json()


@pytest.mark.asyncio
async def test_e2e_provider_out_of_funds_scenario_progression(test_db):
    """
    End-to-End scenario test matching the UI button interaction:
    1. Set runway to OUT_OF_FUNDS
    2. Ingest alert -> status QUEUED
    3. Advance -> status AWAITING_APPROVAL with evidence, diagnosis, citations, policy PASS
    4. Approve -> status APPROVED_HUMAN
    5. Advance -> status COMPLETED with refund recorded in ledger
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Set simulation state
        state_resp = await client.post(
            "/v1/simulation/provider-state",
            json={"provider_id": "runway", "status": "OUT_OF_FUNDS", "balance_cents": 0}
        )
        assert state_resp.status_code == 200
        assert PlatformState.providers["runway"]["status"] == "OUT_OF_FUNDS"

        # 2. Ingest alert
        alert_payload = {
            "tenant_id": "default_tenant",
            "error_type": "OUT_OF_FUNDS",
            "entity_id": "job_101",
            "provider": "runway",
            "message": "Runway 402 Account balance depleted"
        }
        ingest_resp = await client.post("/v1/incidents", json=alert_payload)
        assert ingest_resp.status_code == 202
        incident_id = ingest_resp.json()["incident_id"]

        # 3. Fast-lane advance to pause
        advance_resp = await client.post(f"/v1/incidents/{incident_id}/advance")
        assert advance_resp.status_code == 200
        assert advance_resp.json()["status"] == "AWAITING_APPROVAL"

        # 4. Inspect incident details
        detail_resp = await client.get(f"/v1/incidents/{incident_id}")
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert data["status"] == "AWAITING_APPROVAL"
        state = data["state_data"]
        assert "job" in state["evidence"]
        assert "provider_status" in state["evidence"]
        assert len(state["diagnosis"]["citations"]) > 0
        assert len(state["proposed_actions"]) == 2  # refund + disable_provider
        assert state["policy_verdict"]["is_blocked"] is False

        # 5. Human authorizes actions
        approve_resp = await client.post(
            f"/v1/incidents/{incident_id}/approve",
            json={"approver": "anshu.tiwari@cornflakes.com", "reason": "Authorized per RB-001"}
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "APPROVED_HUMAN"

        # 6. Advance execution to completion
        complete_resp = await client.post(f"/v1/incidents/{incident_id}/advance")
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "COMPLETED"

        # 7. Verify ledger has exactly one refund
        refunds = [
            e for e in PlatformState.ledger
            if e["job_id"] == "job_101" and e["transaction_type"] == "REFUND"
        ]
        assert len(refunds) == 1
        assert refunds[0]["amount_cents"] == 450


@pytest.mark.asyncio
async def test_e2e_false_alarm_policy_blocked_t10(test_db):
    """
    TRAP T10 End-to-End:
    Alert claims OUT_OF_FUNDS, but tool verifies runway is HEALTHY.
    Policy Engine BLOCKS action with code POLICY_VIOLATION_PROVIDER_NOT_FAILING.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Runway is HEALTHY
        assert PlatformState.providers["runway"]["status"] == "HEALTHY"

        alert_payload = {
            "tenant_id": "default_tenant",
            "error_type": "OUT_OF_FUNDS",
            "entity_id": "job_101",
            "provider": "runway",
            "message": "False alarm claim"
        }
        ingest_resp = await client.post("/v1/incidents", json=alert_payload)
        incident_id = ingest_resp.json()["incident_id"]

        advance_resp = await client.post(f"/v1/incidents/{incident_id}/advance")
        assert advance_resp.status_code == 200
        assert advance_resp.json()["status"] == "BLOCKED"

        detail_resp = await client.get(f"/v1/incidents/{incident_id}")
        data = detail_resp.json()
        assert data["status"] == "BLOCKED"
        violations = data["state_data"]["policy_verdict"]["violations"]
        assert any(v["code"] == "POLICY_VIOLATION_PROVIDER_NOT_FAILING" for v in violations)
