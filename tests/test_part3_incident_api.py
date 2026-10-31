"""
Tests for Part 3:
1. Trap T1: Same alert delivered 10 times -> Exactly 1 incident created with duplicate_count=10.
2. Trap T12: Same root cause hits 20 jobs in 1 minute -> Bounded work, grouped under one incident.
3. Incident Retrieval & Events Log.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.core.clock import Clock
from app.db.models import Base, Incident, IncidentEvent
from app.db.session import get_db_session
from app.main import app


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
async def test_t1_same_alert_delivered_10_times(test_db):
    """
    TRAP T1: The same alert delivered 10 times.
    Expected: Exactly one incident created, duplicate count incremented to 10.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alert_payload = {
            "tenant_id": "tenant_alpha",
            "error_type": "OUT_OF_FUNDS",
            "entity_id": "job_101",
            "provider": "runway",
            "message": "Runway account credits exhausted"
        }

        incident_ids = []
        duplicate_counts = []

        # Deliver the exact same alert 10 times
        for i in range(10):
            resp = await client.post(
                "/v1/incidents",
                json=alert_payload,
                headers={"Idempotency-Key": f"client_retry_{i}"}
            )
            assert resp.status_code == 202
            data = resp.json()
            incident_ids.append(data["incident_id"])
            duplicate_counts.append(data["duplicate_count"])

        # 1. All 10 deliveries must map to the same incident ID
        assert len(set(incident_ids)) == 1, "Expected exactly 1 unique incident ID for all 10 alerts"
        first_id = incident_ids[0]

        # 2. Duplicate count should increment from 1 to 10
        assert duplicate_counts[0] == 1
        assert duplicate_counts[-1] == 10

        # 3. Direct DB Check: Exactly 1 row in incidents table
        async with test_db() as session:
            stmt = select(Incident)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            assert len(rows) == 1, f"Expected 1 database row, found {len(rows)}"
            assert rows[0].id == first_id
            assert rows[0].duplicate_count == 10

            # Events log should have 1 ALERT_INGESTED and 9 DUPLICATE_ALERT_MERGED
            events_stmt = select(IncidentEvent).where(IncidentEvent.incident_id == first_id)
            ev_result = await session.execute(events_stmt)
            events = ev_result.scalars().all()
            assert len(events) == 10


@pytest.mark.asyncio
async def test_t12_same_root_cause_hits_20_jobs(test_db):
    """
    TRAP T12: Same root cause hits 20 jobs in one minute.
    Expected: Bounded work, no action storm, grouped under one incident.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        incident_ids = []

        # 20 different jobs fail in 1 minute due to same provider outage
        for job_num in range(1, 21):
            resp = await client.post(
                "/v1/incidents",
                json={
                    "tenant_id": "tenant_alpha",
                    "error_type": "OUT_OF_FUNDS",
                    "entity_id": f"job_{job_num}",
                    "provider": "runway",
                    "message": f"Runway provider returned 402 for job_{job_num}"
                }
            )
            assert resp.status_code == 202
            incident_ids.append(resp.json()["incident_id"])

        # All 20 alerts should be grouped under ONE parent incident
        assert len(set(incident_ids)) == 1, "Expected all 20 jobs to group under 1 parent incident"
        parent_id = incident_ids[0]

        # Check incident details via API
        detail_resp = await client.get(f"/v1/incidents/{parent_id}")
        assert detail_resp.status_code == 200
        incident_data = detail_resp.json()
        assert incident_data["duplicate_count"] == 20
        # All 20 jobs recorded in grouped_entities
        grouped_jobs = incident_data["state_data"]["grouped_entities"]
        assert len(grouped_jobs) == 20
        assert "job_1" in grouped_jobs
        assert "job_20" in grouped_jobs


@pytest.mark.asyncio
async def test_incident_events_retrieval(test_db):
    """Verify GET /v1/incidents/{id}/events returns event log and supports Last-Event-ID."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest alert
        resp = await client.post(
            "/v1/incidents",
            json={
                "tenant_id": "tenant_gamma",
                "error_type": "WORKER_DIED",
                "entity_id": "job_103",
                "message": "Worker heartbeat missed"
            }
        )
        inc_id = resp.json()["incident_id"]

        # Fetch events
        events_resp = await client.get(f"/v1/incidents/{inc_id}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) >= 1
        assert events[0]["event_type"] == "ALERT_INGESTED"
        first_event_id = events[0]["id"]

        # Fetch with Last-Event-ID equal to the latest event -> should be empty
        events_resumed = await client.get(
            f"/v1/incidents/{inc_id}/events",
            headers={"Last-Event-ID": str(first_event_id)}
        )
        assert events_resumed.status_code == 200
        assert len(events_resumed.json()) == 0
