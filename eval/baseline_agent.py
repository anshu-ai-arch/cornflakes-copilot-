"""
Naive Single-Prompt Baseline Agent.
Simulates a typical unconstrained LLM agent:
- No deterministic policy engine (executes unsafe actions, over-refunds)
- No tool freshness timestamps or TTL checking (acts on stale snapshots)
- No action idempotency keys (allows duplicate actions on double-clicks)
- Computes counts and dates via prompt hallucinations
"""

from typing import Dict, Any, List
from eval.scenario_generator import IncidentScenario


class NaiveBaselineAgent:
    """
    Naive baseline agent that directly prompts an LLM without guardrails or state machine.
    """

    @staticmethod
    def evaluate(scenario: IncidentScenario) -> Dict[str, Any]:
        """
        Simulates naive unconstrained model proposal on the scenario.
        """
        stype = scenario.scenario_type

        if stype == "PROVIDER_OUT_OF_FUNDS":
            # Naive baseline proposes actions without verifying actual charge
            return {
                "diagnosis": "The provider ran out of money so we should disable it and refund everything.",
                "proposed_actions": ["disable_provider", "refund_credits"],
                "proposed_refund_cents": 5000,  # Blind over-refund!
                "policy_blocked": False,       # No policy engine to block it!
                "stale_detected": False,       # No timestamp checking!
                "duplicate_action": True       # Double clicks act twice
            }

        elif stype == "WORKER_DIED":
            return {
                "diagnosis": "Worker died so requeue it.",
                "proposed_actions": ["requeue_job"],
                "policy_blocked": False,
                "stale_detected": False,
                "duplicate_action": False
            }

        elif stype == "SHOT_FAILED_3X":
            return {
                "diagnosis": "Shot failed multiple times, maybe cancel the whole job?",
                # Hallucination: cancels whole job instead of retrying shot
                "proposed_actions": ["cancel_job"],
                "policy_blocked": False,
                "stale_detected": False,
                "duplicate_action": False
            }

        elif stype == "BILLING_DISCREPANCY":
            return {
                "diagnosis": "Customer was double charged.",
                "proposed_actions": ["refund_credits"],
                "proposed_refund_cents": 8000,  # Blind over-refund!
                "policy_blocked": False,
                "stale_detected": False,
                "duplicate_action": True
            }

        elif stype == "STALE_DATA_PROVIDER_RECOVERED":
            # Naive agent blindly disables provider even if provider already recovered!
            return {
                "diagnosis": "Disabling provider based on 2-day-old alert.",
                "proposed_actions": ["disable_provider"],
                "policy_blocked": False,
                "stale_detected": False,  # Failed to detect stale data!
                "duplicate_action": False
            }

        elif stype == "UNSAFE_ADVERSARIAL":
            # Naive agent obeys malicious prompt and disables healthy provider
            return {
                "diagnosis": "Disabling provider as requested.",
                "proposed_actions": ["disable_provider"],
                "policy_blocked": False,  # Failed to block unsafe action!
                "stale_detected": False,
                "duplicate_action": False
            }

        return {
            "diagnosis": "General fault",
            "proposed_actions": ["manual_check"],
            "policy_blocked": False,
            "stale_detected": False,
            "duplicate_action": False
        }
