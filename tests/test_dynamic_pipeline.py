"""
Test Dynamic Agentic Pipeline Diversity.
Verifies that the agent is NOT hardcoding results:
Different alert types produce completely distinct root-cause diagnoses,
matching runbook citations (RB-001 through RB-005), and tailored remediation actions.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.clock import Clock
from app.db.models import Base
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
async def test_diverse_incidents_produce_distinct_agentic_results(test_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Define 4 completely distinct operational incidents
        scenarios = [
            {
                "payload": {
                    "tenant_id": "tenant_1",
                    "error_type": "OUT_OF_FUNDS",
                    "entity_id": "job_oof_test",
                    "provider": "runway",
                    "message": "Runway returned HTTP 402: Account balance insufficient"
                },
                "prep_state": lambda: PlatformState.providers["runway"].update({"status": "OUT_OF_FUNDS", "balance_cents": 0}),
                "expected_category": "PROVIDER_OUT_OF_FUNDS",
                "expected_citation": "RB-001",
                "expected_action_types": ["disable_provider", "refund_credits"],
                "expected_human_gate": True
            },
            {
                "payload": {
                    "tenant_id": "tenant_2",
                    "error_type": "WORKER_DIED",
                    "entity_id": "job_wrk_test",
                    "provider": "luma",
                    "message": "Worker lease expired; heartbeat missed for >90s on cluster node"
                },
                "prep_state": lambda: None,
                "expected_category": "WORKER_CRASH",
                "expected_citation": "RB-002",
                "expected_action_types": ["requeue_job"],
                "expected_human_gate": False
            },
            {
                "payload": {
                    "tenant_id": "tenant_3",
                    "error_type": "SHOT_FAILED_3X",
                    "entity_id": "job_sht_test",
                    "provider": "pika",
                    "message": "Shot render failed 3 consecutive times: frame rendering glitch"
                },
                "prep_state": lambda: None,
                "expected_category": "SHOT_RETRY_EXHAUSTED",
                "expected_citation": "RB-003",
                "expected_action_types": ["retry_shot"],
                "expected_human_gate": False
            },
            {
                "payload": {
                    "tenant_id": "tenant_4",
                    "error_type": "BILLING_DISCREPANCY",
                    "entity_id": "job_bil_test",
                    "provider": "pika",
                    "message": "Customer billed 450c on ledger for generation with 300c initial quote"
                },
                "prep_state": lambda: None,
                "expected_category": "BILLING_DISCREPANCY",
                "expected_citation": "RB-004",
                "expected_action_types": ["refund_credits"],
                "expected_human_gate": True
            },
            {
                "payload": {
                    "tenant_id": "tenant_5",
                    "error_type": "RATE_LIMITED",
                    "entity_id": "job_rate_test",
                    "provider": "runway",
                    "message": "Provider API returned HTTP 429 Too Many Requests (Rate limit quota reached)"
                },
                "prep_state": lambda: PlatformState.providers["runway"].update({"status": "DEGRADED"}),
                "expected_category": "RATE_LIMITED",
                "expected_citation": "RB-005",
                "expected_action_types": ["requeue_job"],
                "expected_human_gate": False
            }
        ]

        collected_results = []

        for scen in scenarios:
            scen["prep_state"]()
            ingest_resp = await client.post("/v1/incidents", json=scen["payload"])
            assert ingest_resp.status_code == 202
            inc_id = ingest_resp.json()["incident_id"]

            advance_resp = await client.post(f"/v1/incidents/{inc_id}/advance")
            assert advance_resp.status_code == 200

            detail_resp = await client.get(f"/v1/incidents/{inc_id}")
            assert detail_resp.status_code == 200
            data = detail_resp.json()
            state = data["state_data"]

            category = state["diagnosis"]["category"]
            citations = state["diagnosis"]["citations"]
            actions = [a["action_type"] for a in state["proposed_actions"]]
            requires_human = state["policy_verdict"]["requires_human"]

            # Verify specific scenario match
            assert category == scen["expected_category"]
            assert scen["expected_citation"] in citations
            assert set(actions) == set(scen["expected_action_types"])
            assert requires_human == scen["expected_human_gate"]

            collected_results.append({
                "error_type": scen["payload"]["error_type"],
                "category": category,
                "citations": citations,
                "actions": actions
            })

        # Crucial: Assert that categories and citations are distinct across scenarios
        distinct_categories = {r["category"] for r in collected_results}
        assert len(distinct_categories) == 5, f"Expected 5 distinct categories, got: {distinct_categories}"

        distinct_citations = {r["citations"][0] for r in collected_results}
        assert len(distinct_citations) == 5, f"Expected 5 distinct runbook citations, got: {distinct_citations}"
