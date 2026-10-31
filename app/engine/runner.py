"""
State Machine Orchestration Runner.
Executes graph nodes sequentially, commits state checkpoints to PostgreSQL
after every single node, and cleanly pauses when human approval is required.
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.clock import Clock
from app.db.models import Incident, IncidentEvent, IncidentAction
from app.engine.state import IncidentState
from app.engine.nodes import (
    triage_node,
    diagnose_node,
    propose_node,
    policy_node,
    execute_node,
    verify_node
)


async def advance_incident_state(
    incident: Incident,
    db: AsyncSession,
    worker_id: str
) -> bool:
    """
    Executes one node in the state machine for the given incident,
    commits state to the database, and returns True if more steps are immediately runnable.
    """
    # Reconstruct IncidentState from JSON checkpoint
    state_dict = dict(incident.state_data or {})
    state_dict.setdefault("incident_id", incident.id)
    state_dict.setdefault("tenant_id", incident.tenant_id)
    state_dict.setdefault("error_type", incident.error_type)
    state_dict.setdefault("entity_id", incident.entity_id)
    state_dict.setdefault("status", incident.status)
    state_dict.setdefault("current_node", incident.current_node)

    state = IncidentState(**state_dict)
    current_node = state.current_node
    prev_node = current_node

    # Dispatch to node handler
    if current_node in ["init", "triage"]:
        state = await triage_node(state)
    elif current_node == "diagnose":
        state = await diagnose_node(state)
    elif current_node == "propose":
        state = await propose_node(state)
    elif current_node == "policy":
        state = await policy_node(state)
        # Sync proposed actions into incident_actions table with idempotency keys
        for act in state.proposed_actions:
            action_row = IncidentAction(
                incident_id=incident.id,
                idempotency_key=act["idempotency_key"],
                action_type=act["action_type"],
                target_id=act["target_id"],
                parameters=act.get("parameters", {}),
                status="PENDING",
                created_at=Clock.now()
            )
            db.add(action_row)
    elif current_node == "execute":
        from app.engine.executor import ExactlyOnceExecutor
        results = []
        for act in state.proposed_actions:
            res = await ExactlyOnceExecutor.execute_action(
                action_dict=act,
                incident_id=incident.id,
                db=db
            )
            results.append({"action": act, "result": res})
        state.execution_results = results
        state.current_node = "verify"
        state.status = "EXECUTED"
        state.updated_at = Clock.now_iso()
    elif current_node == "verify":
        state = await verify_node(state)
    else:
        # Terminal state reached or waiting for human
        return False

    # Persist checkpoint atomically
    now = Clock.now()
    incident.current_node = state.current_node
    incident.status = state.status
    incident.state_data = state.model_dump()
    flag_modified(incident, "state_data")
    incident.updated_at = now

    # Emit SSE Event
    event = IncidentEvent(
        incident_id=incident.id,
        event_type=f"NODE_{prev_node.upper()}_COMPLETED",
        payload={
            "completed_node": prev_node,
            "next_node": state.current_node,
            "status": state.status,
            "worker_id": worker_id
        },
        created_at=now
    )
    db.add(event)
    await db.commit()

    # Determine if we should continue immediately or pause
    if state.status in ["AWAITING_APPROVAL", "COMPLETED", "BLOCKED", "REJECTED"]:
        return False
    return True
