"""
Deterministic Tools Implementation.
Each tool returns facts stamped with as_of timestamp, source, and TTL.
The LLM never computes counts, totals, or dates.
"""

from typing import Dict, Any, List, Optional
from app.core.clock import Clock
from app.core.config import settings
from app.tools.envelope import ToolResult
from mock_platform.state import PlatformState


class DeterministicTools:
    """
    Suite of deterministic tools that query the platform state of record.
    Can operate in-process (direct memory for blazing fast tests/eval)
    or over HTTP.
    """

    @staticmethod
    def get_job(job_id: str) -> ToolResult:
        ttl = settings.TOOL_TTLS.get("get_job", 120)
        job = PlatformState.jobs.get(job_id)
        if not job:
            return ToolResult(
                data={"error": f"Job '{job_id}' not found", "exists": False},
                as_of=Clock.now_iso(),
                source="platform_job_store",
                ttl_seconds=ttl
            )
        return ToolResult(
            data={"job": job, "exists": True},
            as_of=Clock.now_iso(),
            source="platform_job_store",
            ttl_seconds=ttl
        )

    @staticmethod
    def list_failed_shots(job_id: str) -> ToolResult:
        ttl = settings.TOOL_TTLS.get("list_failed_shots", 120)
        shots = PlatformState.shots.get(job_id, [])
        failed = [s for s in shots if s.get("status") == "FAILED"]
        return ToolResult(
            data={
                "job_id": job_id,
                "total_shots_count": len(shots),
                "failed_shots_count": len(failed),  # Tool computes counts, not LLM!
                "failed_shots": failed,
                "all_shots": shots
            },
            as_of=Clock.now_iso(),
            source="platform_shot_store",
            ttl_seconds=ttl
        )

    @staticmethod
    def provider_status(provider_id: str) -> ToolResult:
        ttl = settings.TOOL_TTLS.get("provider_status", 60)
        provider = PlatformState.providers.get(provider_id)
        if not provider:
            return ToolResult(
                data={"error": f"Provider '{provider_id}' not found", "exists": False},
                as_of=Clock.now_iso(),
                source="platform_provider_registry",
                ttl_seconds=ttl
            )
        return ToolResult(
            data={
                "provider_id": provider_id,
                "name": provider["name"],
                "status": provider["status"],
                "balance_cents": provider["balance_cents"],
                "is_failing": provider["status"] in ["OUT_OF_FUNDS", "DEGRADED", "DISABLED"]
            },
            as_of=Clock.now_iso(),
            source="platform_provider_registry",
            ttl_seconds=ttl
        )

    @staticmethod
    def ledger_for_job(job_id: str) -> ToolResult:
        ttl = settings.TOOL_TTLS.get("ledger_for_job", 300)
        entries = [e for e in PlatformState.ledger if e["job_id"] == job_id]
        total_charged = sum(e["amount_cents"] for e in entries if e["transaction_type"] == "CHARGE")
        total_refunded = sum(e["amount_cents"] for e in entries if e["transaction_type"] == "REFUND")
        net_charged = total_charged - total_refunded
        return ToolResult(
            data={
                "job_id": job_id,
                "entries": entries,
                "total_charged_cents": total_charged,    # Tool computes totals, not LLM!
                "total_refunded_cents": total_refunded,
                "net_charged_cents": net_charged
            },
            as_of=Clock.now_iso(),
            source="platform_ledger_db",
            ttl_seconds=ttl
        )

    @staticmethod
    def queue_depth() -> ToolResult:
        ttl = settings.TOOL_TTLS.get("queue_depth", 30)
        return ToolResult(
            data=dict(PlatformState.queue_info),
            as_of=Clock.now_iso(),
            source="platform_queue_manager",
            ttl_seconds=ttl
        )

    @staticmethod
    def recent_logs(entity_id: str) -> ToolResult:
        ttl = settings.TOOL_TTLS.get("recent_logs", 60)
        logs = PlatformState.logs.get(entity_id, [])
        return ToolResult(
            data={
                "entity_id": entity_id,
                "log_count": len(logs),
                "logs": logs
            },
            as_of=Clock.now_iso(),
            source="platform_log_aggregator",
            ttl_seconds=ttl
        )


class ActionClient:
    """
    Client for executing actions against the platform with mandatory Idempotency Keys.
    """

    @staticmethod
    def check_action_status(idempotency_key: str) -> Dict[str, Any]:
        """
        Check if action was already recorded by the target platform (Trap T3).
        """
        record = PlatformState.executed_actions.get(idempotency_key)
        if record:
            return {"executed": True, "action": record}
        return {"executed": False, "action": None}

    @staticmethod
    def execute_refund(job_id: str, amount_cents: int, reason: str, idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in PlatformState.executed_actions:
            return {"status": "already_executed", "action": PlatformState.executed_actions[idempotency_key]}

        job = PlatformState.jobs.get(job_id)
        if not job:
            return {"status": "failed", "error": f"Job {job_id} not found"}

        now_iso = Clock.now_iso()
        ledger_entry = {
            "id": len(PlatformState.ledger) + 1,
            "job_id": job_id,
            "tenant_id": job.get("tenant_id", "default"),
            "idempotency_key": idempotency_key,
            "amount_cents": amount_cents,
            "transaction_type": "REFUND",
            "reason": reason,
            "created_at": now_iso
        }
        PlatformState.ledger.append(ledger_entry)

        action_record = {
            "idempotency_key": idempotency_key,
            "action_type": "refund_credits",
            "target_id": job_id,
            "amount_cents": amount_cents,
            "status": "COMPLETED",
            "executed_at": now_iso,
            "ledger_entry_id": ledger_entry["id"]
        }
        PlatformState.executed_actions[idempotency_key] = action_record
        return {"status": "success", "action": action_record}

    @staticmethod
    def execute_requeue(job_id: str, idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in PlatformState.executed_actions:
            return {"status": "already_executed", "action": PlatformState.executed_actions[idempotency_key]}

        job = PlatformState.jobs.get(job_id)
        if not job:
            return {"status": "failed", "error": f"Job {job_id} not found"}

        now_iso = Clock.now_iso()
        job["status"] = "QUEUED"
        job["attempts"] = 0
        job["updated_at"] = now_iso

        action_record = {
            "idempotency_key": idempotency_key,
            "action_type": "requeue_job",
            "target_id": job_id,
            "status": "COMPLETED",
            "executed_at": now_iso
        }
        PlatformState.executed_actions[idempotency_key] = action_record
        return {"status": "success", "action": action_record}

    @staticmethod
    def execute_retry_shot(job_id: str, shot_id: str, idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in PlatformState.executed_actions:
            return {"status": "already_executed", "action": PlatformState.executed_actions[idempotency_key]}

        shots = PlatformState.shots.get(job_id, [])
        shot = next((s for s in shots if s["shot_id"] == shot_id), None)
        if not shot:
            return {"status": "failed", "error": f"Shot {shot_id} not found"}

        now_iso = Clock.now_iso()
        shot["status"] = "PENDING"
        shot["attempts"] = 0
        shot["error"] = None

        action_record = {
            "idempotency_key": idempotency_key,
            "action_type": "retry_shot",
            "target_id": shot_id,
            "status": "COMPLETED",
            "executed_at": now_iso
        }
        PlatformState.executed_actions[idempotency_key] = action_record
        return {"status": "success", "action": action_record}

    @staticmethod
    def execute_disable_provider(provider_id: str, reason: str, idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in PlatformState.executed_actions:
            return {"status": "already_executed", "action": PlatformState.executed_actions[idempotency_key]}

        provider = PlatformState.providers.get(provider_id)
        if not provider:
            return {"status": "failed", "error": f"Provider {provider_id} not found"}

        now_iso = Clock.now_iso()
        provider["status"] = "DISABLED"
        provider["updated_at"] = now_iso

        action_record = {
            "idempotency_key": idempotency_key,
            "action_type": "disable_provider",
            "target_id": provider_id,
            "status": "COMPLETED",
            "executed_at": now_iso
        }
        PlatformState.executed_actions[idempotency_key] = action_record
        return {"status": "success", "action": action_record}

    @staticmethod
    def execute_enable_provider(provider_id: str, reason: str, idempotency_key: str) -> Dict[str, Any]:
        if idempotency_key in PlatformState.executed_actions:
            return {"status": "already_executed", "action": PlatformState.executed_actions[idempotency_key]}

        provider = PlatformState.providers.get(provider_id)
        if not provider:
            return {"status": "failed", "error": f"Provider {provider_id} not found"}

        now_iso = Clock.now_iso()
        provider["status"] = "HEALTHY"
        provider["updated_at"] = now_iso

        action_record = {
            "idempotency_key": idempotency_key,
            "action_type": "enable_provider",
            "target_id": provider_id,
            "status": "COMPLETED",
            "executed_at": now_iso
        }
        PlatformState.executed_actions[idempotency_key] = action_record
        return {"status": "success", "action": action_record}
