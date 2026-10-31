"""
Tests for Part 2:
1. Deterministic Tools Contract (as_of timestamp, source, TTL)
2. Tool Fact Computations (counts, sums)
3. Staleness Protocol and Clock Travel
4. Action Execution Idempotency & Recovery Check
"""

import pytest
from app.core.clock import Clock
from app.tools.deterministic import DeterministicTools, ActionClient
from mock_platform.state import PlatformState


@pytest.fixture(autouse=True)
def setup_teardown():
    Clock.reset()
    PlatformState.reset()
    yield
    Clock.reset()
    PlatformState.reset()


def test_deterministic_tools_contract():
    """Verify all 6 tools return stamped envelope with as_of, source, and TTL."""
    # 1. get_job
    job_res = DeterministicTools.get_job("job_101")
    assert job_res.source == "platform_job_store"
    assert job_res.as_of is not None
    assert job_res.ttl_seconds == 120
    assert job_res.data["exists"] is True
    assert job_res.data["job"]["job_id"] == "job_101"

    # 2. list_failed_shots
    shots_res = DeterministicTools.list_failed_shots("job_101")
    assert shots_res.source == "platform_shot_store"
    assert shots_res.data["total_shots_count"] == 3
    assert shots_res.data["failed_shots_count"] == 1  # Computed by tool, not LLM!
    assert len(shots_res.data["failed_shots"]) == 1

    # 3. provider_status
    provider_res = DeterministicTools.provider_status("runway")
    assert provider_res.source == "platform_provider_registry"
    assert provider_res.data["provider_id"] == "runway"
    assert provider_res.data["status"] == "HEALTHY"
    assert provider_res.ttl_seconds == 60

    # 4. ledger_for_job
    ledger_res = DeterministicTools.ledger_for_job("job_101")
    assert ledger_res.source == "platform_ledger_db"
    assert ledger_res.data["total_charged_cents"] == 450
    assert ledger_res.data["total_refunded_cents"] == 0
    assert ledger_res.data["net_charged_cents"] == 450  # Computed by tool!

    # 5. queue_depth
    queue_res = DeterministicTools.queue_depth()
    assert queue_res.source == "platform_queue_manager"
    assert queue_res.data["depth"] == 14

    # 6. recent_logs
    logs_res = DeterministicTools.recent_logs("job_101")
    assert logs_res.source == "platform_log_aggregator"
    assert logs_res.data["log_count"] == 4


def test_staleness_protocol_and_virtual_clock():
    """Verify that tool outputs detect age and mark themselves STALE when TTL is exceeded."""
    provider_res = DeterministicTools.provider_status("runway")
    assert provider_res.ttl_seconds == 60
    assert provider_res.check_is_stale() is False
    assert "Fresh" in provider_res.plain_english_age()

    # Fast-forward virtual clock by 65 seconds (exceeding 60s TTL)
    Clock.advance(65)
    assert provider_res.check_is_stale() is True
    assert provider_res.is_stale is True
    assert "minutes old" in provider_res.plain_english_age()

    # Fast-forward 2 days (Trap T6 scenario)
    Clock.advance(172800)
    assert provider_res.check_is_stale() is True
    assert "2 days old (STALE)" in provider_res.plain_english_age()


def test_action_idempotency_and_ledger():
    """Verify that actions executed with identical idempotency keys never execute twice."""
    idempotency_key = "test_refund_key_abc_123"

    # Initial ledger state
    initial_entries_count = len(PlatformState.ledger)

    # 1. First execution
    res1 = ActionClient.execute_refund(
        job_id="job_101",
        amount_cents=450,
        reason="Shot failed due to provider error",
        idempotency_key=idempotency_key
    )
    assert res1["status"] == "success"
    assert len(PlatformState.ledger) == initial_entries_count + 1

    # 2. Second execution with identical key (simulating double click or retry)
    res2 = ActionClient.execute_refund(
        job_id="job_101",
        amount_cents=450,
        reason="Shot failed due to provider error",
        idempotency_key=idempotency_key
    )
    assert res2["status"] == "already_executed"
    # Exactly one refund entry in ledger!
    assert len(PlatformState.ledger) == initial_entries_count + 1

    # 3. Crash recovery check (Trap T3)
    recovery_check = ActionClient.check_action_status(idempotency_key)
    assert recovery_check["executed"] is True
    assert recovery_check["action"]["action_type"] == "refund_credits"
    assert recovery_check["action"]["status"] == "COMPLETED"
