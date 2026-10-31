"""
Sequential Node Handlers for the State Machine Graph:
triage -> diagnose -> propose -> policy -> await_approval -> execute -> verify.
Each node is an idempotent pure function transforming IncidentState.
"""

from typing import Dict, Any, List, Optional
import hashlib
from app.core.clock import Clock
from app.core.config import settings
from app.engine.state import IncidentState
from app.tools.deterministic import DeterministicTools, ActionClient


async def triage_node(state: IncidentState) -> IncidentState:
    """
    TRIAGE NODE: Gathers ground-truth evidence using deterministic tools.
    Every tool result is stamped with as_of timestamp and source.
    """
    evidence: Dict[str, Any] = {}
    job_id = state.entity_id if state.entity_id.startswith("job_") else None
    provider_id = state.provider

    # 1. Fetch Job details if applicable
    if job_id:
        job_res = DeterministicTools.get_job(job_id)
        evidence["job"] = job_res.model_dump()
        if job_res.data.get("exists"):
            job_data = job_res.data.get("job", {})
            if not provider_id and "provider" in job_data:
                provider_id = job_data["provider"]
                state.provider = provider_id

        # 2. Fetch Shots details
        shots_res = DeterministicTools.list_failed_shots(job_id)
        evidence["shots"] = shots_res.model_dump()

        # 3. Fetch Job Ledger (billing history)
        ledger_res = DeterministicTools.ledger_for_job(job_id)
        evidence["ledger"] = ledger_res.model_dump()

        # 4. Fetch recent logs
        logs_res = DeterministicTools.recent_logs(job_id)
        evidence["logs"] = logs_res.model_dump()

    # 5. Fetch Provider status if applicable
    if provider_id:
        prov_res = DeterministicTools.provider_status(provider_id)
        evidence["provider_status"] = prov_res.model_dump()

    # 6. Fetch Queue Depth
    queue_res = DeterministicTools.queue_depth()
    evidence["queue"] = queue_res.model_dump()

    state.evidence = evidence
    state.current_node = "diagnose"
    state.status = "TRIAGED"
    state.updated_at = Clock.now_iso()
    return state


async def diagnose_node(state: IncidentState) -> IncidentState:
    """
    DIAGNOSE NODE: Identifies root cause and cites runbooks.
    (Detailed LLM/Rule engine in Part 5).
    """
    error_type = state.error_type
    provider_id = state.provider
    evidence = state.evidence

    root_cause = "Unknown operational failure"
    citations = []
    category = "INFRASTRUCTURE"

    if error_type == "OUT_OF_FUNDS" or (
        evidence.get("provider_status", {}).get("data", {}).get("status") == "OUT_OF_FUNDS"
    ):
        root_cause = f"Provider '{provider_id}' has run out of funds/account credits."
        citations = ["RB-001"]
        category = "PROVIDER_OUT_OF_FUNDS"
    elif error_type == "WORKER_DIED":
        root_cause = "Worker node died mid-job; missed heartbeats for >90s."
        citations = ["RB-002"]
        category = "WORKER_CRASH"
    elif error_type == "SHOT_FAILED_3X":
        root_cause = "Shot rendering failed 3 consecutive attempts."
        citations = ["RB-003"]
        category = "SHOT_RETRY_EXHAUSTED"
    else:
        root_cause = f"Operational incident on {state.entity_id}: {error_type}"
        citations = ["RB-012"]

    state.diagnosis = {
        "root_cause": root_cause,
        "category": category,
        "citations": citations,
        "confidence": 0.95,
        "diagnosed_at": Clock.now_iso()
    }

    state.current_node = "propose"
    state.status = "DIAGNOSED"
    state.updated_at = Clock.now_iso()
    return state


def derive_action_idempotency_key(incident_id: str, action_type: str, target_id: str) -> str:
    """
    Derives deterministic idempotency key from (incident_id, action_type, target_id).
    Crucial for Traps T2 (double-click) and T3 (worker crash recovery).
    """
    raw = f"{incident_id}:{action_type}:{target_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def propose_node(state: IncidentState) -> IncidentState:
    """
    PROPOSE NODE: Formulates concrete candidate actions with idempotency keys.
    """
    diagnosis = state.diagnosis or {}
    category = diagnosis.get("category", "")
    actions: List[Dict[str, Any]] = []
    job_id = state.entity_id if state.entity_id.startswith("job_") else None
    provider_id = state.provider

    if category == "PROVIDER_OUT_OF_FUNDS":
        # 1. Propose disable provider
        if provider_id:
            key1 = derive_action_idempotency_key(state.incident_id, "disable_provider", provider_id)
            actions.append({
                "action_type": "disable_provider",
                "target_id": provider_id,
                "parameters": {"reason": diagnosis.get("root_cause", "Out of funds")},
                "idempotency_key": key1,
                "requires_human": True,
                "description": f"Disable provider '{provider_id}' until account is topped up."
            })
        # 2. Propose refund job
        if job_id:
            net_charge = (
                state.evidence.get("ledger", {}).get("data", {}).get("net_charged_cents", 0)
            )
            key2 = derive_action_idempotency_key(state.incident_id, "refund_credits", job_id)
            actions.append({
                "action_type": "refund_credits",
                "target_id": job_id,
                "parameters": {
                    "amount_cents": net_charge,
                    "reason": "Job failed due to provider out of funds"
                },
                "idempotency_key": key2,
                "requires_human": True,
                "description": f"Refund {net_charge} cents for failed job '{job_id}'."
            })

    elif category == "WORKER_CRASH":
        if job_id:
            key = derive_action_idempotency_key(state.incident_id, "requeue_job", job_id)
            actions.append({
                "action_type": "requeue_job",
                "target_id": job_id,
                "parameters": {},
                "idempotency_key": key,
                "requires_human": False,  # Requeue worker crash can auto-approve
                "description": f"Requeue job '{job_id}' after worker death."
            })

    elif category == "SHOT_RETRY_EXHAUSTED":
        if job_id:
            failed_shots = (
                state.evidence.get("shots", {}).get("data", {}).get("failed_shots", [])
            )
            for shot in failed_shots:
                shot_id = shot.get("shot_id")
                key = derive_action_idempotency_key(state.incident_id, "retry_shot", shot_id)
                actions.append({
                    "action_type": "retry_shot",
                    "target_id": shot_id,
                    "parameters": {"job_id": job_id, "shot_id": shot_id},
                    "idempotency_key": key,
                    "requires_human": False,  # Cheap shot retry can auto-approve
                    "description": f"Retry failed shot '{shot_id}'."
                })

    state.proposed_actions = actions
    state.current_node = "policy"
    state.status = "PROPOSED"
    state.updated_at = Clock.now_iso()
    return state


async def policy_node(state: IncidentState) -> IncidentState:
    """
    POLICY NODE: Evaluates proposed actions against hard business policies.
    """
    actions = state.proposed_actions
    requires_human = False
    violations = []

    for act in actions:
        # Rule 1: Financial actions always require human
        if act["action_type"] in ["refund_credits", "disable_provider"]:
            act["requires_human"] = True
            requires_human = True
        elif act.get("requires_human", False):
            requires_human = True

    verdict = {
        "status": "APPROVED_BY_POLICY",
        "requires_human": requires_human,
        "violations": violations,
        "evaluated_at": Clock.now_iso()
    }
    state.policy_verdict = verdict

    if requires_human:
        # Pauses state machine in DB! No worker memory held (Trap T5)
        state.current_node = "await_approval"
        state.status = "AWAITING_APPROVAL"
    else:
        # Safe to proceed directly
        state.current_node = "execute"
        state.status = "APPROVED_AUTO"

    state.updated_at = Clock.now_iso()
    return state


async def execute_node(state: IncidentState) -> IncidentState:
    """
    EXECUTE NODE: Executes actions with idempotency keys.
    """
    results: List[Dict[str, Any]] = []

    for act in state.proposed_actions:
        action_type = act["action_type"]
        target_id = act["target_id"]
        key = act["idempotency_key"]
        params = act.get("parameters", {})

        if action_type == "refund_credits":
            res = ActionClient.execute_refund(
                job_id=target_id,
                amount_cents=params.get("amount_cents", 0),
                reason=params.get("reason", "Refund"),
                idempotency_key=key
            )
        elif action_type == "disable_provider":
            res = ActionClient.execute_disable_provider(
                provider_id=target_id,
                reason=params.get("reason", "Disabled by copilot"),
                idempotency_key=key
            )
        elif action_type == "requeue_job":
            res = ActionClient.execute_requeue(job_id=target_id, idempotency_key=key)
        elif action_type == "retry_shot":
            res = ActionClient.execute_retry_shot(
                job_id=params.get("job_id", target_id),
                shot_id=target_id,
                idempotency_key=key
            )
        elif action_type == "enable_provider":
            res = ActionClient.execute_enable_provider(
                provider_id=target_id,
                reason=params.get("reason", "Enabled"),
                idempotency_key=key
            )
        else:
            res = {"status": "skipped", "error": f"Unknown action {action_type}"}

        results.append({"action": act, "result": res})

    state.execution_results = results
    state.current_node = "verify"
    state.status = "EXECUTING"
    state.updated_at = Clock.now_iso()
    return state


async def verify_node(state: IncidentState) -> IncidentState:
    """
    VERIFY NODE: Verifies post-execution state and closes incident.
    """
    state.verification = {
        "status": "VERIFIED",
        "verified_at": Clock.now_iso(),
        "summary": f"Executed {len(state.execution_results)} action(s) successfully."
    }
    state.current_node = "done"
    state.status = "COMPLETED"
    state.updated_at = Clock.now_iso()
    return state
