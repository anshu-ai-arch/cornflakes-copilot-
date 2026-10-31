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

        # Formulate diagnosis explanation
        if provider_data.get("status") == "OUT_OF_FUNDS":
            prov_id = provider_data.get("provider_id", "runway")
            explanation = (
                f"Provider '{prov_id}' has exhausted its balance and returned HTTP 402 [{top_rb}]. "
                f"Customer generation halted after {stated_failed_shots} failed shots [{top_rb}]."
            )
            citations = [top_rb]
            rec_actions = ["disable_provider", "refund_credits"]
        elif "WORKER" in job_data.get("error", "").upper():
            explanation = f"Worker heartbeat missed; worker presumed dead [{top_rb}]."
            citations = [top_rb]
            rec_actions = ["requeue_job"]
        elif actual_failed_shots > 0:
            explanation = (
                f"Individual shot rendering failed {stated_failed_shots} times [{top_rb}]. "
                f"Retrying shot alone preserves finished stages [{top_rb}]."
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
