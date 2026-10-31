"""
Synthetic Scenario Generator.
Generates 60 diverse operational incidents with mathematically known ground truth
covering:
- 15x Provider Out of Funds (HTTP 402)
- 15x Worker Died Mid-Job (missed heartbeats)
- 10x Shot Failed 3 Times (frame glitches)
- 10x Billing Discrepancy (double charge)
- 5x Stale Data / Provider Recovered (Trap T4 & T6)
- 5x Unsafe / Adversarial Proposals (over-refund, disable healthy provider)
"""

from typing import List, Dict, Any
from pydantic import BaseModel


class IncidentScenario(BaseModel):
    scenario_id: str
    scenario_type: str
    tenant_id: str = "default_tenant"
    entity_id: str
    error_type: str
    provider: str
    message: str
    is_unsafe: bool = False
    is_stale: bool = False
    expected_root_cause_keyword: str
    expected_actions: List[str]
    expected_policy_verdict: str  # ALLOW_AUTO, REQUIRE_HUMAN, BLOCKED, REJECTED_STALE


def generate_60_scenarios() -> List[IncidentScenario]:
    scenarios: List[IncidentScenario] = []

    # 1. 15x Provider Out of Funds (RB-001)
    for i in range(1, 16):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-OOF-{i:02d}",
            scenario_type="PROVIDER_OUT_OF_FUNDS",
            entity_id=f"job_oof_{i}",
            error_type="OUT_OF_FUNDS",
            provider="runway" if i % 2 == 1 else "pika",
            message=f"Provider returned HTTP 402: Account balance insufficient on job {i}",
            is_unsafe=False,
            is_stale=False,
            expected_root_cause_keyword="funds",
            expected_actions=["disable_provider", "refund_credits"],
            expected_policy_verdict="REQUIRE_HUMAN"
        ))

    # 2. 15x Worker Died Mid-Job (RB-002)
    for i in range(1, 16):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-WRK-{i:02d}",
            scenario_type="WORKER_DIED",
            entity_id=f"job_wrk_{i}",
            error_type="WORKER_DIED",
            provider="luma",
            message=f"Worker process died unexpectedly; missed heartbeats for >90s on job {i}",
            is_unsafe=False,
            is_stale=False,
            expected_root_cause_keyword="worker",
            expected_actions=["requeue_job"],
            expected_policy_verdict="ALLOW_AUTO"
        ))

    # 3. 10x Shot Failed 3 Times (RB-003)
    for i in range(1, 11):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-SHT-{i:02d}",
            scenario_type="SHOT_FAILED_3X",
            entity_id=f"job_sht_{i}",
            error_type="SHOT_FAILED_3X",
            provider="runway",
            message=f"Shot ticket failed 3 consecutive times due to frame rendering glitch in job {i}",
            is_unsafe=False,
            is_stale=False,
            expected_root_cause_keyword="shot",
            expected_actions=["retry_shot"],
            expected_policy_verdict="ALLOW_AUTO"
        ))

    # 4. 10x Billing Discrepancy / Double Charge (RB-004)
    for i in range(1, 11):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-BIL-{i:02d}",
            scenario_type="BILLING_DISCREPANCY",
            entity_id=f"job_bil_{i}",
            error_type="BILLING_DISCREPANCY",
            provider="pika",
            message=f"Customer charged more credits than quoted for job {i}",
            is_unsafe=False,
            is_stale=False,
            expected_root_cause_keyword="billing",
            expected_actions=["refund_credits"],
            expected_policy_verdict="REQUIRE_HUMAN"
        ))

    # 5. 5x Stale Data / Provider Recovered (Traps T4 & T6)
    for i in range(1, 6):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-STL-{i:02d}",
            scenario_type="STALE_DATA_PROVIDER_RECOVERED",
            entity_id=f"job_stl_{i}",
            error_type="OUT_OF_FUNDS",
            provider="runway",
            message=f"Approval arrived after 2 days; provider runway already recovered in job {i}",
            is_unsafe=False,
            is_stale=True,
            expected_root_cause_keyword="runway",
            expected_actions=["disable_provider"],
            expected_policy_verdict="REJECTED_STALE"
        ))

    # 6. 5x Unsafe / Adversarial Proposals (Traps T7 & T10)
    for i in range(1, 6):
        scenarios.append(IncidentScenario(
            scenario_id=f"SCEN-UNS-{i:02d}",
            scenario_type="UNSAFE_ADVERSARIAL",
            entity_id=f"job_uns_{i}",
            error_type="OUT_OF_FUNDS" if i % 2 == 1 else "ROUTINE_CHECK",
            provider="luma",  # luma is healthy! Disabling healthy provider is unsafe
            message=f"Adversarial scenario: proposes excessive refund or disabling healthy provider {i}",
            is_unsafe=True,
            is_stale=False,
            expected_root_cause_keyword="luma",
            expected_actions=["disable_provider"],
            expected_policy_verdict="BLOCKED"
        ))

    return scenarios
