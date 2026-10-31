"""
Deterministic Mock LLM Engine.
Simulates realistic LLM behaviors:
- Deterministic output based on (prompt, seed)
- 90% valid JSON, 10% malformed JSON
- 5% simulated HTTP 429 quota exhaustion
- Chaos switches:
    * inject_hallucinated_number: states counts not supported by tools (Trap T8)
    * inject_uncited_claim: asserts facts without a runbook citation (Trap T11)
"""

import json
import hashlib
from typing import Dict, Any, List, Optional
from app.core.config import settings


class MockLLMEngine:
    def __init__(self, seed: int = settings.LLM_SEED):
        self.seed = seed

    def generate(
        self,
        prompt: str,
        evidence: Dict[str, Any],
        retrieved_runbooks: List[Dict[str, Any]],
        inject_malformed: bool = False,
        inject_429: bool = False,
        inject_hallucinated_number: bool = False,
        inject_uncited_claim: bool = False
    ) -> str:
        """Generates deterministic mock LLM response string."""
        if inject_429:
            raise RuntimeError("HTTP 429: Rate limit exceeded / Quota exhausted")

        # Top retrieved runbook citation
        top_rb = retrieved_runbooks[0]["id"] if retrieved_runbooks else "RB-012"

        # Determine ground-truth diagnosis from evidence
        job_data = evidence.get("job", {}).get("data", {}).get("job", {})
        provider_data = evidence.get("provider_status", {}).get("data", {})
        shots_data = evidence.get("shots", {}).get("data", {})

        actual_failed_shots = shots_data.get("failed_shots_count", 0)

        # Hallucination switch for Trap T8
        stated_failed_shots = actual_failed_shots
        if inject_hallucinated_number:
            stated_failed_shots = actual_failed_shots + 5  # Hallucinate 5 extra failures!

        # Formulate diagnosis explanation dynamically based on top retrieved runbook & prompt
        prompt_upper = prompt.upper()
        prov_id = provider_data.get("provider_id", "runway")
        ledger_data = evidence.get("ledger", {}).get("data", {})
        net_charged = ledger_data.get("net_charged_cents", 450)

        if top_rb == "RB-001" or "OUT_OF_FUNDS" in prompt_upper or "402" in prompt:
            explanation = (
                f"Provider '{prov_id}' has exhausted its balance and returned HTTP 402 [{top_rb}]. "
                f"Customer generation halted after {stated_failed_shots} failed shots [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["disable_provider", "refund_credits"]
        elif top_rb == "RB-002" or "WORKER" in prompt_upper or "WORKER" in job_data.get("error", "").upper():
            explanation = f"Worker heartbeat missed for >90s; worker process terminated unexpectedly [{top_rb}]."
            citations = [top_rb]
            rec_actions = ["requeue_job"]
        elif top_rb == "RB-003" or "SHOT" in prompt_upper or actual_failed_shots >= 3:
            explanation = (
                f"Individual shot rendering failed {stated_failed_shots} times [{top_rb}]. "
                f"Retrying shot alone preserves finished stages [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["retry_shot"]
        elif top_rb == "RB-004" or "BILLING" in prompt_upper or "CHARGE" in prompt_upper:
            explanation = (
                f"Billing discrepancy detected: customer charged {net_charged} cents exceeding valid quote [{top_rb}]. "
                f"Adjustment refund required to reconcile ledger [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["refund_credits"]
        elif top_rb == "RB-005" or "RATE_LIMIT" in prompt_upper or "429" in prompt:
            explanation = (
                f"Provider '{prov_id}' returned HTTP 429 Too Many Requests due to temporary concurrency quotas [{top_rb}]. "
                f"Requeuing throttled generation with exponential backoff delay [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["requeue_job"]
        elif top_rb == "RB-006" or "FRAME" in prompt_upper or "GLITCH" in prompt_upper or "ARTIFACT" in prompt_upper:
            explanation = (
                f"Rendered frames exhibit visual artifacts and quality defects exceeding tolerance threshold [{top_rb}]. "
                f"Re-rendering defective shot ticket with randomized seed [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["retry_shot"]
        elif top_rb == "RB-007" or "RECOVERY" in prompt_upper or "RECOVERED" in prompt_upper:
            explanation = (
                f"Provider '{prov_id}' balance restored and verified healthy [{top_rb}]. "
                f"Re-enabling dispatch routing to active worker pool [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["enable_provider"]
        elif top_rb == "RB-008" or "CONGESTION" in prompt_upper or "QUEUE" in prompt_upper:
            explanation = (
                f"Dispatcher queue depth exceeds high watermark capacity [{top_rb}]. "
                f"Throttling background ingest and prioritizing critical jobs [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["requeue_job"]
        elif top_rb == "RB-009" or "CANCELLED" in prompt_upper:
            explanation = (
                f"Generation project cancelled while tickets remain unrendered [{top_rb}]. "
                f"Releasing unspent credits back to tenant account [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["refund_credits"]
        elif top_rb == "RB-010" or "STALE" in prompt_upper:
            explanation = (
                f"Data snapshot exceeds tool TTL freshness limit [{top_rb}]. "
                f"Refreshing ground truth facts before executing state transition [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["manual_inspection"]
        elif top_rb == "RB-011" or "DESYNC" in prompt_upper or "AUDIO" in prompt_upper:
            explanation = (
                f"Audio duration exceeds video frame stream duration by >500ms [{top_rb}]. "
                f"Re-rendering alignment ticket with speed compensation filter [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["retry_shot"]
        else:
            explanation = f"Operational issue identified according to runbook [{top_rb}]."
            citations = [top_rb]
            rec_actions = ["manual_inspection"]

        # Trap T11: Inject uncited claim
        if inject_uncited_claim:
            explanation += " Additionally, server memory leak occurred on cluster alpha without any runbook backing."

        structured_response = {
            "diagnosis": explanation,
            "confidence": 0.94,
            "citations": citations,
            "recommended_actions": rec_actions,
            "stated_failure_count": stated_failed_shots
        }

        raw_json = json.dumps(structured_response, indent=2)

        # 10% Malformed JSON simulation
        if inject_malformed:
            # Inject syntax error (trailing comma and unclosed bracket)
            raw_json = raw_json[:-2] + ",\n  \"broken_field\": "

        return raw_json


MockLLM = MockLLMEngine()
