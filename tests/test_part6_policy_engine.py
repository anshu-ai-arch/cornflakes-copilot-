"""
Tests for Part 6:
1. Trap T7: LLM proposes refund above job's charge -> Blocked by policy with reason code.
2. Trap T10: Proposal disables a healthy provider -> Blocked: not currently failing.
3. Requirement 9: Auto-approve safe actions (retry_shot under cost cap).
4. Money action strictly requires human approval.
5. Requirement 14: Dry-run preview computation.
"""

import pytest
from app.engine.policy import PolicyEngine, compute_dry_run_preview
from app.tools.deterministic import DeterministicTools
from mock_platform.state import PlatformState


@pytest.fixture(autouse=True)
def setup_state():
    PlatformState.reset()
    yield
    PlatformState.reset()


def test_t7_refund_above_actual_charge_blocked():
    """
    TRAP T7: LLM proposes refund above the job's charge.
    Expected: Blocked by policy; reason code POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE.
    """
    # job_101 was charged 450 cents ($4.50)
    evidence = {
        "ledger": DeterministicTools.ledger_for_job("job_101").model_dump(),
        "job": DeterministicTools.get_job("job_101").model_dump()
    }

    # Propose excessive refund of 5000 cents ($50.00)
    malicious_action = {
        "action_type": "refund_credits",
        "target_id": "job_101",
        "parameters": {"amount_cents": 5000, "reason": "Accidental over-refund"},
        "idempotency_key": "test_refund_over_cap"
    }

    verdict = PolicyEngine.evaluate_all([malicious_action], evidence)

    assert verdict["is_blocked"] is True
    assert verdict["status"] == "BLOCKED"
    assert len(verdict["violations"]) == 1
    violation = verdict["violations"][0]
    assert violation["code"] == "POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE"
    assert "exceeds actual charge of $4.50" in violation["message"]


def test_t10_disabling_healthy_provider_blocked():
    """
    TRAP T10: Proposal disables a healthy provider.
    Expected: Blocked: not currently failing (POLICY_VIOLATION_PROVIDER_NOT_FAILING).
    """
    # Provider 'luma' is HEALTHY in seed data
    evidence = {
        "provider_status": DeterministicTools.provider_status("luma").model_dump()
    }
    assert evidence["provider_status"]["data"]["status"] == "HEALTHY"

    # Action proposes to disable healthy provider luma
    dangerous_action = {
        "action_type": "disable_provider",
        "target_id": "luma",
        "parameters": {"reason": "Erroneous proposal"},
        "idempotency_key": "test_disable_luma"
    }

    verdict = PolicyEngine.evaluate_all([dangerous_action], evidence)

    assert verdict["is_blocked"] is True
    assert verdict["status"] == "BLOCKED"
    assert len(verdict["violations"]) == 1
    violation = verdict["violations"][0]
    assert violation["code"] == "POLICY_VIOLATION_PROVIDER_NOT_FAILING"
    assert "not currently failing" in violation["message"]


def test_auto_approve_safe_actions():
    """
    Requirement 9: A small allowlist (e.g. retry_shot under a cost cap)
    may run without a human.
    """
    evidence = {
        "job": DeterministicTools.get_job("job_102").model_dump()
    }

    # Cheap shot retry costing 25 cents (under 50 cents cap)
    safe_action = {
        "action_type": "retry_shot",
        "target_id": "shot_102_3",
        "parameters": {"job_id": "job_102", "shot_id": "shot_102_3", "cost_cents": 25},
        "idempotency_key": "test_retry_cheap"
    }

    verdict = PolicyEngine.evaluate_all([safe_action], evidence)

    assert verdict["is_blocked"] is False
    assert verdict["requires_human"] is False
    assert verdict["status"] == "ALLOW_AUTO"


def test_money_action_requires_human_approval():
    """Verify refund action touching money strictly requires human approval."""
    evidence = {
        "ledger": DeterministicTools.ledger_for_job("job_101").model_dump(),
        "job": DeterministicTools.get_job("job_101").model_dump()
    }

    valid_refund_action = {
        "action_type": "refund_credits",
        "target_id": "job_101",
        "parameters": {"amount_cents": 450, "reason": "Legitimate refund"},
        "idempotency_key": "test_valid_refund"
    }

    verdict = PolicyEngine.evaluate_all([valid_refund_action], evidence)

    assert verdict["is_blocked"] is False
    assert verdict["requires_human"] is True
    assert verdict["status"] == "REQUIRE_HUMAN"


def test_dry_run_preview_computation():
    """Requirement 14: Compute dry run preview diff before approval."""
    evidence = {
        "ledger": DeterministicTools.ledger_for_job("job_101").model_dump(),
        "provider_status": DeterministicTools.provider_status("runway").model_dump()
    }

    action = {
        "action_type": "refund_credits",
        "target_id": "job_101",
        "parameters": {"amount_cents": 450}
    }

    preview = compute_dry_run_preview(action, evidence)
    assert "[DRY-RUN]" in preview
    assert "$4.50" in preview
    assert "job_101" in preview
