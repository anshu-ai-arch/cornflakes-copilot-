"""
Exactly-Once Action Execution Engine and Crash Recovery Protocol.
Solves:
- Trap T2: Double-click approval -> exactly one refund, one ledger entry.
- Trap T3: Worker killed after action API call but before DB status write ->
  On resume, reconciles with target system using action idempotency key,
  verifies completion, and marks done without re-executing.
"""

from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.clock import Clock
from app.db.models import Incident, IncidentAction, IncidentEvent
from app.tools.deterministic import ActionClient


class WorkerKilledMidActionException(Exception):
    """Simulates SIGKILL / worker crash between external call and local status write (Trap T3)."""
    pass


class ExactlyOnceExecutor:
    @staticmethod
    async def execute_action(
        action_dict: Dict[str, Any],
        incident_id: str,
        db: AsyncSession,
        simulate_worker_kill: bool = False
    ) -> Dict[str, Any]:
        """
        Executes an action with two-phase idempotency and crash recovery protocol.
        """
        idempotency_key = action_dict["idempotency_key"]
        action_type = action_dict["action_type"]
        target_id = action_dict["target_id"]
        params = action_dict.get("parameters", {})

        # Step 1: Query or create the local action record
        stmt = select(IncidentAction).where(IncidentAction.idempotency_key == idempotency_key)
        res = await db.execute(stmt)
        action_record = res.scalars().first()

        if not action_record:
            action_record = IncidentAction(
                incident_id=incident_id,
                idempotency_key=idempotency_key,
                action_type=action_type,
                target_id=target_id,
                parameters=params,
                status="PENDING",
                created_at=Clock.now()
            )
            db.add(action_record)
            await db.commit()

        # Step 2 (Crash Recovery Check - Trap T3):
        # Query target platform to check if action was already executed in a previous aborted flight
        platform_check = ActionClient.check_action_status(idempotency_key)
        if platform_check.get("executed"):
            # Target system already completed this action! Reconcile local state without re-calling!
            now = Clock.now()
            action_record.status = "COMPLETED"
            action_record.result = platform_check.get("action")
            action_record.completed_at = now
            await db.commit()
            return {
                "status": "reconciled_after_crash",
                "message": "Action was already executed on platform before crash; reconciled state.",
                "idempotency_key": idempotency_key,
                "action": platform_check.get("action")
            }

        # Step 3: Mark IN_FLIGHT locally before dispatching to network
        action_record.status = "IN_FLIGHT"
        await db.commit()

        # Step 4: Dispatch action to target platform API with Idempotency Key
        if action_type == "refund_credits":
            call_res = ActionClient.execute_refund(
                job_id=target_id,
                amount_cents=params.get("amount_cents", 0),
                reason=params.get("reason", "Refund"),
                idempotency_key=idempotency_key
            )
        elif action_type == "disable_provider":
            call_res = ActionClient.execute_disable_provider(
                provider_id=target_id,
                reason=params.get("reason", "Disabled by copilot"),
                idempotency_key=idempotency_key
            )
        elif action_type == "enable_provider":
            call_res = ActionClient.execute_enable_provider(
                provider_id=target_id,
                reason=params.get("reason", "Enabled by copilot"),
                idempotency_key=idempotency_key
            )
        elif action_type == "requeue_job":
            call_res = ActionClient.execute_requeue(
                job_id=target_id,
                idempotency_key=idempotency_key
            )
        elif action_type == "retry_shot":
            call_res = ActionClient.execute_retry_shot(
                job_id=params.get("job_id", target_id),
                shot_id=target_id,
                idempotency_key=idempotency_key
            )
        else:
            call_res = {"status": "failed", "error": f"Unknown action {action_type}"}

        # Step 5 (Simulate Trap T3 Chaos):
        # Crash right after network call succeeds, before local status write!
        if simulate_worker_kill:
            raise WorkerKilledMidActionException(
                f"Simulated SIGKILL crash after external call for key '{idempotency_key}'!"
            )

        # Step 6: Mark local action COMPLETED
        now = Clock.now()
        action_record.status = "COMPLETED"
        action_record.result = call_res
        action_record.completed_at = now
        await db.commit()

        return call_res

    @staticmethod
    async def execute_all_for_incident(
        incident: Incident,
        db: AsyncSession,
        simulate_worker_kill: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Executes all proposed actions for an incident, updating state_data and emitting events.
        """
        state_data = dict(incident.state_data or {})
        actions = state_data.get("proposed_actions", [])
        results = []

        for act in actions:
            res = await ExactlyOnceExecutor.execute_action(
                action_dict=act,
                incident_id=incident.id,
                db=db,
                simulate_worker_kill=simulate_worker_kill
            )
            results.append({"action": act, "result": res})

        # Update checkpoint
        now = Clock.now()
        state_data["execution_results"] = results
        incident.execution_results = results
        incident.state_data = state_data
        incident.current_node = "verify"
        incident.status = "EXECUTED"
        incident.updated_at = now
        flag_modified(incident, "state_data")
        await db.commit()

        return results
