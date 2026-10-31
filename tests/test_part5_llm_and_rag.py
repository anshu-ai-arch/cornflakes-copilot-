"""
Tests for Part 5:
1. Runbook RAG BM25 Retrieval Quality (12 Runbooks)
2. Trap T8: Hallucinated failure count replaced by tool value
3. Trap T9: LLM turned off -> Rule-based fallback diagnosis still works
4. Trap T11: Runbook claim without citation dropped from answer
5. Malformed JSON auto-repair
"""

import pytest
from app.rag.engine import RAG
from app.llm.mock_llm import MockLLM
from app.llm.boundary import execute_diagnose_boundary, repair_json
from app.tools.deterministic import DeterministicTools
from mock_platform.state import PlatformState


@pytest.fixture(autouse=True)
def setup_state():
    PlatformState.reset()
    yield
    PlatformState.reset()


def test_runbook_rag_bm25_retrieval():
    """Verify BM25 retrieves accurate runbooks for key operational queries."""
    # 1. Out of funds query -> RB-001
    res1 = RAG.retrieve("provider runway out of funds payment required 402 balance empty")
    assert len(res1) > 0
    assert res1[0]["id"] == "RB-001"
    assert "Provider Out of Funds" in res1[0]["title"]

    # 2. Worker crash query -> RB-002
    res2 = RAG.retrieve("worker heartbeat missed 90s worker died lease expired")
    assert len(res2) > 0
    assert res2[0]["id"] == "RB-002"

    # 3. Shot failed 3 times -> RB-003
    res3 = RAG.retrieve("shot render failed 3 times frame glitch max retries")
    assert len(res3) > 0
    assert res3[0]["id"] == "RB-003"

    # 4. Double charge -> RB-004
    res4 = RAG.retrieve("double charge billing discrepancy credit debit mismatch")
    assert len(res4) > 0
    assert res4[0]["id"] == "RB-004"


@pytest.mark.asyncio
async def test_t8_hallucinated_number_replaced_by_tool_value():
    """
    TRAP T8: LLM states a failure count that the tools do not support.
    Expected: Number replaced by the tool value or dropped.
    """
    # job_101 has 1 failed shot (shot_101_2)
    shots_tool = DeterministicTools.list_failed_shots("job_101")
    actual_failed = shots_tool.data["failed_shots_count"]
    assert actual_failed == 1

    # Set runway to OUT_OF_FUNDS for job_101 scenario
    PlatformState.providers["runway"]["status"] = "OUT_OF_FUNDS"

    evidence = {
        "job": DeterministicTools.get_job("job_101").model_dump(),
        "shots": shots_tool.model_dump(),
        "provider_status": DeterministicTools.provider_status("runway").model_dump(),
    }

    # Execute with hallucination switch active (LLM claims 1 + 5 = 6 failures!)
    result = await execute_diagnose_boundary(
        evidence=evidence,
        query_text="provider runway out of funds on job_101",
        inject_hallucinated_number=True
    )

    # The guardrail normalizer must detect discrepancy and override to tool value
    assert result.get("stated_failure_count") == actual_failed
    assert result.get("hallucination_corrected") is True
    # The text explanation must reflect the tool value '1', not hallucinated '6'
    assert f"{actual_failed} failed shots" in result["diagnosis"]
    assert "6 failed shots" not in result["diagnosis"]


@pytest.mark.asyncio
async def test_t9_llm_turned_off_rule_based_diagnosis():
    """
    TRAP T9: LLM turned off.
    Expected: Rule-based diagnosis still works accurately.
    """
    PlatformState.providers["runway"]["status"] = "OUT_OF_FUNDS"

    evidence = {
        "job": DeterministicTools.get_job("job_101").model_dump(),
        "shots": DeterministicTools.list_failed_shots("job_101").model_dump(),
        "provider_status": DeterministicTools.provider_status("runway").model_dump(),
    }

    # Simulate LLM turned off
    result = await execute_diagnose_boundary(
        evidence=evidence,
        query_text="provider runway out of funds payment required",
        llm_enabled=False
    )

    assert result["is_fallback"] is True
    assert "RB-001" in result["citations"]
    assert "disable_provider" in result["recommended_actions"]
    assert "refund_credits" in result["recommended_actions"]
    assert "runway" in result["diagnosis"].lower()


@pytest.mark.asyncio
async def test_t11_uncited_claim_dropped_from_answer():
    """
    TRAP T11: Runbook claim without a citation is dropped from the answer.
    """
    evidence = {
        "job": DeterministicTools.get_job("job_101").model_dump(),
        "shots": DeterministicTools.list_failed_shots("job_101").model_dump(),
        "provider_status": DeterministicTools.provider_status("runway").model_dump(),
    }

    # Inject uncited claim
    result = await execute_diagnose_boundary(
        evidence=evidence,
        query_text="job_101 runway issue",
        inject_uncited_claim=True
    )

    # Valid cited claim should be kept
    assert "[RB-001]" in result["diagnosis"]
    # Uncited sentence about "server memory leak" MUST be dropped!
    assert "server memory leak" not in result["diagnosis"].lower()


def test_malformed_json_repair():
    """Verify 1-time auto repair fixes trailing commas and unbalanced brackets."""
    malformed_raw = '{\n  "diagnosis": "Test [RB-001]",\n  "confidence": 0.9,\n  "citations": ["RB-001"],\n'
    repaired = repair_json(malformed_raw)
    assert repaired is not None
    assert repaired["diagnosis"] == "Test [RB-001]"
    assert repaired["citations"] == ["RB-001"]
