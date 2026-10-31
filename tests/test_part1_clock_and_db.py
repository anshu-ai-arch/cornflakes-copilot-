"""
Tests for Part 1:
1. Virtual Clock Service & Time Travel (2 days later in milliseconds)
2. Database Models and Schema Initialization
3. FastAPI Clock and Health Endpoints
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.clock import Clock, ClockService
from app.db.models import Base, Incident, IncidentAction, IncidentEvent, MockLedger
from app.main import app


@pytest.fixture(autouse=True)
def reset_clock_fixture():
    Clock.reset()
    yield
    Clock.reset()


def test_clock_service_time_travel():
    """Verify that virtual clock can fast-forward 2 days in milliseconds."""
    initial_time = Clock.now()
    two_days_seconds = 2 * 24 * 3600  # 172800s

    # Fast-forward 2 days
    future_time = Clock.advance(two_days_seconds)
    diff = (future_time - initial_time).total_seconds()
    assert abs(diff - two_days_seconds) < 1.0
    assert Clock.get_offset() == two_days_seconds

    # Reset back to now
    reset_time = Clock.reset()
    assert Clock.get_offset() == 0.0
    assert abs((Clock.now() - datetime.now(timezone.utc)).total_seconds()) < 1.0


@pytest.mark.asyncio
async def test_database_schema_initialization():
    """Verify that all core models create valid tables and constraints."""
    test_db_url = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(test_db_url)
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession)
    async with session_factory() as session:
        # Test creating an Incident with deduplication fingerprint
        incident = Incident(
            id="inc_test_001",
            tenant_id="tenant_alpha",
            fingerprint="fp_hash_12345",
            error_type="OUT_OF_FUNDS",
            entity_id="runway",
            status="QUEUED",
            current_node="triage",
            state_data={"test": True}
        )
        session.add(incident)
        
        # Test creating an IncidentAction with Idempotency Key
        action = IncidentAction(
            incident_id="inc_test_001",
            idempotency_key="act_hash_refund_99",
            action_type="refund_credits",
            target_id="job_99",
            parameters={"amount_cents": 500},
            status="PENDING"
        )
        session.add(action)
        
        # Test creating a MockLedger entry
        ledger_entry = MockLedger(
            job_id="job_99",
            tenant_id="tenant_alpha",
            idempotency_key="act_hash_refund_99",
            amount_cents=500,
            transaction_type="REFUND",
            reason="Platform failure"
        )
        session.add(ledger_entry)
        
        await session.commit()
        
        # Verify retrieved data
        retrieved_inc = await session.get(Incident, "inc_test_001")
        assert retrieved_inc is not None
        assert retrieved_inc.fingerprint == "fp_hash_12345"
        assert retrieved_inc.status == "QUEUED"
        
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_api_clock_endpoints():
    """Verify clock advance and reset via HTTP API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check health
        health_resp = await client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "healthy"

        # Check clock
        clock_resp = await client.get("/v1/clock")
        assert clock_resp.status_code == 200
        assert clock_resp.json()["clock_offset_seconds"] == 0.0

        # Advance clock by 2 days (172800 seconds)
        advance_resp = await client.post("/v1/clock/advance", json={"seconds": 172800})
        assert advance_resp.status_code == 200
        assert advance_resp.json()["advanced_by_seconds"] == 172800
        assert advance_resp.json()["total_offset_seconds"] == 172800

        # Verify time advanced
        clock_resp2 = await client.get("/v1/clock")
        assert clock_resp2.json()["clock_offset_seconds"] == 172800

        # Reset clock
        reset_resp = await client.post("/v1/clock/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["total_offset_seconds"] == 0.0
