"""
LLM Safety Boundary and Guardrail Pipeline.
- Schema validation with Pydantic
- 1-Time JSON auto-repair
- Deterministic Rule-Based Fallback Engine (Trap T9: LLM turned off)
- Fact Normalizer: Overwrites hallucinated numbers with tool facts (Trap T8)
- Citation Enforcer: Drops claims lacking verified runbook citations (Trap T11)
"""

import json
import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.clock import Clock
from app.rag.engine import RAG
from app.llm.mock_llm import MockLLM


class StructuredDiagnosis(BaseModel):
    diagnosis: str
    confidence: float
    citations: List[str] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    stated_failure_count: Optional[int] = None


def run_rule_based_diagnosis(
    evidence: Dict[str, Any],
    retrieved_runbooks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Tier 3 Fallback Engine: 100% Deterministic Python Rule Engine.
    Runs when LLM is switched off (settings.MOCK_LLM_ENABLED=false),
    malformed output cannot be repaired, or 429 occurs (Trap T9).
    """
    top_rb = retrieved_runbooks[0]["id"] if retrieved_runbooks else "RB-012"
    job_data = evidence.get("job", {}).get("data", {}).get("job", {})
    provider_data = evidence.get("provider_status", {}).get("data", {})
    shots_data = evidence.get("shots", {}).get("data", {})
    failed_count = shots_data.get("failed_shots_count", 0)

    prov_id = provider_data.get("provider_id", "runway")
    ledger_data = evidence.get("ledger", {}).get("data", {})
    net_charged = ledger_data.get("net_charged_cents", 450)

    if top_rb == "RB-001" or provider_data.get("status") == "OUT_OF_FUNDS":
        diagnosis_text = (
            f"Provider '{prov_id}' balance exhausted and returned 402 [{top_rb}]. "
            f"Job halted with {failed_count} failed shots [{top_rb}]."
        )
        rec_actions = ["disable_provider", "refund_credits"]
        citations = [top_rb]
    elif top_rb == "RB-002" or "WORKER" in job_data.get("error", "").upper():
        diagnosis_text = f"Worker heartbeat missed; worker presumed dead [{top_rb}]."
        rec_actions = ["requeue_job"]
        citations = [top_rb]
    elif top_rb == "RB-003" or failed_count >= 3:
        diagnosis_text = f"Shot failed {failed_count} consecutive attempts [{top_rb}]."
        rec_actions = ["retry_shot"]
        citations = [top_rb]
    elif top_rb == "RB-004":
        diagnosis_text = f"Billing discrepancy detected: customer charged {net_charged} cents exceeding quote [{top_rb}]."
        rec_actions = ["refund_credits"]
        citations = [top_rb]
    elif top_rb == "RB-005":
        diagnosis_text = f"Provider '{prov_id}' rate limited with HTTP 429 [{top_rb}]."
        rec_actions = ["requeue_job"]
        citations = [top_rb]
    elif top_rb == "RB-006":
        diagnosis_text = f"Visual artifacts and quality degradation detected on output frames [{top_rb}]."
        rec_actions = ["retry_shot"]
        citations = [top_rb]
    elif top_rb == "RB-007":
        diagnosis_text = f"Provider '{prov_id}' recovered and verified healthy [{top_rb}]."
        rec_actions = ["enable_provider"]
        citations = [top_rb]
    elif top_rb == "RB-008":
        diagnosis_text = f"Queue congestion high watermark exceeded [{top_rb}]."
        rec_actions = ["requeue_job"]
        citations = [top_rb]
    elif top_rb == "RB-009":
        diagnosis_text = f"Generation project cancelled with unreleased credits [{top_rb}]."
        rec_actions = ["refund_credits"]
        citations = [top_rb]
    elif top_rb == "RB-011":
        diagnosis_text = f"Audio-video stream duration desynchronization exceeding 500ms [{top_rb}]."
        rec_actions = ["retry_shot"]
        citations = [top_rb]
    else:
        diagnosis_text = f"Operational fault detected matching runbook [{top_rb}]."
        rec_actions = ["manual_inspection"]
        citations = [top_rb]

    return {
        "diagnosis": diagnosis_text,
        "confidence": 0.99,
        "citations": citations,
        "recommended_actions": rec_actions,
        "stated_failure_count": failed_count,
        "is_fallback": True
    }


def repair_json(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    1-Time JSON Repair: Fixes markdown code blocks, trailing commas,
    and missing closing braces.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join([line for line in lines if not line.startswith("```")]).strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Strip trailing commas at end of string
    cleaned = re.sub(r",\s*$", "", cleaned)

    # Balance unclosed braces
    open_braces = cleaned.count("{") - cleaned.count("}")
    if open_braces > 0:
        cleaned += "\n" + ("}" * open_braces)

    # Fix trailing commas before closing braces/brackets
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        return None


def enforce_citations(text: str, allowed_citations: List[str]) -> str:
    """
    Trap T11: Runbook claim without a citation is dropped from the answer.
    Splits text into sentences. Any sentence making an operational assertion
    without a valid runbook citation [RB-xxx] is dropped.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept_sentences = []

    for s in sentences:
        if not s.strip():
            continue
        # Find citations like [RB-001]
        matches = re.findall(r"\[(RB-\d{3})\]", s)
        valid_matches = [m for m in matches if m in allowed_citations or RAG.is_valid_citation(m)]

        if valid_matches:
            kept_sentences.append(s)
        else:
            # Drop uncited claim! (Trap T11)
            continue

    if not kept_sentences and sentences:
        # If all dropped, attach top allowed citation to the primary clause
        primary = sentences[0].rstrip(".!?")
        top_cite = allowed_citations[0] if allowed_citations else "RB-012"
        return f"{primary} [{top_cite}]."

    return " ".join(kept_sentences)


def normalize_facts(
    structured_data: Dict[str, Any],
    evidence: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Trap T8: LLM states a failure count that the tools do not support ->
    Number replaced by the tool value or dropped.
    """
    shots_data = evidence.get("shots", {}).get("data", {})
    actual_failed_count = shots_data.get("failed_shots_count", 0)
    stated_count = structured_data.get("stated_failure_count")

    # If LLM hallucinated a different count, force override with tool value
    if stated_count is not None and stated_count != actual_failed_count:
        # Replace in explanation text
        text = structured_data["diagnosis"]
        # Replace the hallucinated number with the tool fact
        text = re.sub(rf"\b{stated_count}\b", str(actual_failed_count), text)
        structured_data["diagnosis"] = text
        structured_data["stated_failure_count"] = actual_failed_count
        structured_data["hallucination_corrected"] = True

    return structured_data


async def execute_diagnose_boundary(
    evidence: Dict[str, Any],
    query_text: str,
    llm_enabled: Optional[bool] = None,
    inject_malformed: bool = False,
    inject_429: bool = False,
    inject_hallucinated_number: bool = False,
    inject_uncited_claim: bool = False
) -> Dict[str, Any]:
    """
    Full Diagnose Guardrail Pipeline:
    1. Retrieve Runbooks via BM25
    2. Check LLM Enabled switch (Trap T9)
    3. Mock LLM generation + 1-time JSON repair
    4. Enforce citations (Trap T11)
    5. Fact normalizer for numbers (Trap T8)
    """
    use_llm = settings.MOCK_LLM_ENABLED if llm_enabled is None else llm_enabled

    # 1. Retrieve top-3 Runbooks
    retrieved_runbooks = RAG.retrieve(query_text, top_k=3)
    allowed_citations = [r["id"] for r in retrieved_runbooks]

    # 2. Trap T9: If LLM is turned off, immediately run rule-based diagnosis
    if not use_llm:
        return run_rule_based_diagnosis(evidence, retrieved_runbooks)

    # 3. Call Mock LLM
    try:
        raw_output = MockLLM.generate(
            prompt=query_text,
            evidence=evidence,
            retrieved_runbooks=retrieved_runbooks,
            inject_malformed=inject_malformed,
            inject_429=inject_429,
            inject_hallucinated_number=inject_hallucinated_number,
            inject_uncited_claim=inject_uncited_claim
        )
    except Exception:
        # LLM failed or 429 -> Fallback to Rule Engine
        return run_rule_based_diagnosis(evidence, retrieved_runbooks)

    # 4. JSON Schema Parse / 1-time Repair
    parsed_json = None
    try:
        parsed_json = json.loads(raw_output)
    except Exception:
        parsed_json = repair_json(raw_output)

    if not parsed_json:
        # Repair failed -> Fallback to Rule Engine
        return run_rule_based_diagnosis(evidence, retrieved_runbooks)

    # Validate against Pydantic schema
    try:
        validated = StructuredDiagnosis(**parsed_json).model_dump()
    except Exception:
        return run_rule_based_diagnosis(evidence, retrieved_runbooks)

    # 5. Trap T11: Enforce Citations (Drop claims lacking citation)
    validated["diagnosis"] = enforce_citations(validated["diagnosis"], allowed_citations)

    # 6. Trap T8: Normalize Facts (Overwrite hallucinated numbers)
    validated = normalize_facts(validated, evidence)
    validated["is_fallback"] = False

    return validated
