"""
Mock Platform API Service.
Runs as internal service (port 8001) or in-process.
Provides deterministic endpoints for tools and idempotency-guaranteed actions.
"""

from fastapi import FastAPI, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from app.core.clock import Clock
from mock_platform.state import PlatformState

app = FastAPI(title="Cornflakes Mock Platform API", version="1.0.0")


# --- Schemas ---

class RefundRequest(BaseModel):
    job_id: str
    amount_cents: int
    reason: str


class RequeueRequest(BaseModel):
    job_id: str


class RetryShotRequest(BaseModel):
    job_id: str
    shot_id: str


class ProviderActionRequest(BaseModel):
    provider_id: str
    reason: str


class SetProviderStatusRequest(BaseModel):
    provider_id: str
    status: str  # HEALTHY, OUT_OF_FUNDS, DEGRADED, DISABLED
    balance_cents: Optional[int] = None


# --- Tool Queries ---

@app.get("/platform/jobs/{job_id}")
async def get_job(job_id: str):
    job = PlatformState.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return {
        "job": job,
        "as_of": Clock.now_iso(),
        "source": "platform_job_store"
    }


@app.get("/platform/jobs/{job_id}/shots")
async def get_shots(job_id: str):
    shots = PlatformState.shots.get(job_id)
    if shots is None:
        raise HTTPException(status_code=404, detail=f"No shots found for job '{job_id}'")
    failed_shots = [s for s in shots if s["status"] == "FAILED"]
    return {
        "job_id": job_id,
        "total_shots": len(shots),
        "failed_shots": failed_shots,
        "failed_count": len(failed_shots),
        "all_shots": shots,
        "as_of": Clock.now_iso(),
        "source": "platform_shot_store"
    }


@app.get("/platform/providers/{provider_id}")
async def get_provider(provider_id: str):
    provider = PlatformState.providers.get(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    return {
        "provider": provider,
        "as_of": Clock.now_iso(),
        "source": "platform_provider_registry"
    }


@app.get("/platform/ledger/{job_id}")
async def get_ledger_for_job(job_id: str):
    entries = [e for e in PlatformState.ledger if e["job_id"] == job_id]
    total_charged = sum(e["amount_cents"] for e in entries if e["transaction_type"] == "CHARGE")
    total_refunded = sum(e["amount_cents"] for e in entries if e["transaction_type"] == "REFUND")
    net_charge = total_charged - total_refunded
    return {
        "job_id": job_id,
        "entries": entries,
        "total_charged_cents": total_charged,
        "total_refunded_cents": total_refunded,
        "net_charged_cents": net_charge,
        "as_of": Clock.now_iso(),
        "source": "platform_ledger_db"
    }


@app.get("/platform/queue")
async def get_queue():
    return {
        "queue": PlatformState.queue_info,
        "as_of": Clock.now_iso(),
        "source": "platform_queue_manager"
    }


@app.get("/platform/logs/{entity_id}")
async def get_logs(entity_id: str):
    logs = PlatformState.logs.get(entity_id, [])
    return {
        "entity_id": entity_id,
        "logs": logs,
        "count": len(logs),
        "as_of": Clock.now_iso(),
        "source": "platform_log_aggregator"
    }


# --- Action Execution with Strict Idempotency ---

@app.get("/platform/actions/{idempotency_key}")
async def check_action_status(idempotency_key: str):
    """
    Checks if an action with this idempotency key was already executed.
    Crucial for Trap T3 crash recovery (worker revived after network call).
    """
    record = PlatformState.executed_actions.get(idempotency_key)
    if record:
        return {"executed": True, "action": record}
    return {"executed": False, "action": None}


@app.post("/platform/actions/refund_credits")
async def refund_credits(
    req: RefundRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing mandatory 'Idempotency-Key' header")

    # Check for duplicate execution (Trap T2 / T3)
    if idempotency_key in PlatformState.executed_actions:
        # Return existing result idempotently
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_executed",
            "message": "Action was already executed with this idempotency key",
            "action": PlatformState.executed_actions[idempotency_key]
        }

    # Verify job exists
    job = PlatformState.jobs.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{req.job_id}' not found")

    # Record refund in append-only ledger
    now_iso = Clock.now_iso()
    ledger_entry = {
        "id": len(PlatformState.ledger) + 1,
        "job_id": req.job_id,
        "tenant_id": job.get("tenant_id", "default"),
        "idempotency_key": idempotency_key,
        "amount_cents": req.amount_cents,
        "transaction_type": "REFUND",
        "reason": req.reason,
        "created_at": now_iso
    }
    PlatformState.ledger.append(ledger_entry)

    # Store in executed_actions
    action_record = {
        "idempotency_key": idempotency_key,
        "action_type": "refund_credits",
        "target_id": req.job_id,
        "amount_cents": req.amount_cents,
        "status": "COMPLETED",
        "executed_at": now_iso,
        "ledger_entry_id": ledger_entry["id"]
    }
    PlatformState.executed_actions[idempotency_key] = action_record

    return {
        "status": "success",
        "message": f"Successfully refunded {req.amount_cents} cents for job {req.job_id}",
        "action": action_record
    }


@app.post("/platform/actions/requeue_job")
async def requeue_job(
    req: RequeueRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing mandatory 'Idempotency-Key' header")

    if idempotency_key in PlatformState.executed_actions:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_executed",
            "action": PlatformState.executed_actions[idempotency_key]
        }

    job = PlatformState.jobs.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{req.job_id}' not found")

    now_iso = Clock.now_iso()
    job["status"] = "QUEUED"
    job["attempts"] = 0
    job["updated_at"] = now_iso

    action_record = {
        "idempotency_key": idempotency_key,
        "action_type": "requeue_job",
        "target_id": req.job_id,
        "status": "COMPLETED",
        "executed_at": now_iso
    }
    PlatformState.executed_actions[idempotency_key] = action_record

    return {"status": "success", "message": f"Job {req.job_id} requeued", "action": action_record}


@app.post("/platform/actions/retry_shot")
async def retry_shot(
    req: RetryShotRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing mandatory 'Idempotency-Key' header")

    if idempotency_key in PlatformState.executed_actions:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_executed",
            "action": PlatformState.executed_actions[idempotency_key]
        }

    shots = PlatformState.shots.get(req.job_id, [])
    target_shot = next((s for s in shots if s["shot_id"] == req.shot_id), None)
    if not target_shot:
        raise HTTPException(status_code=404, detail=f"Shot '{req.shot_id}' not found for job '{req.job_id}'")

    now_iso = Clock.now_iso()
    target_shot["status"] = "PENDING"
    target_shot["attempts"] = 0
    target_shot["error"] = None

    action_record = {
        "idempotency_key": idempotency_key,
        "action_type": "retry_shot",
        "target_id": req.shot_id,
        "status": "COMPLETED",
        "executed_at": now_iso
    }
    PlatformState.executed_actions[idempotency_key] = action_record

    return {"status": "success", "message": f"Shot {req.shot_id} queued for retry", "action": action_record}


@app.post("/platform/actions/disable_provider")
async def disable_provider(
    req: ProviderActionRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing mandatory 'Idempotency-Key' header")

    if idempotency_key in PlatformState.executed_actions:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_executed",
            "action": PlatformState.executed_actions[idempotency_key]
        }

    provider = PlatformState.providers.get(req.provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{req.provider_id}' not found")

    now_iso = Clock.now_iso()
    provider["status"] = "DISABLED"
    provider["updated_at"] = now_iso

    action_record = {
        "idempotency_key": idempotency_key,
        "action_type": "disable_provider",
        "target_id": req.provider_id,
        "status": "COMPLETED",
        "executed_at": now_iso
    }
    PlatformState.executed_actions[idempotency_key] = action_record

    return {"status": "success", "message": f"Provider {req.provider_id} disabled", "action": action_record}


@app.post("/platform/actions/enable_provider")
async def enable_provider(
    req: ProviderActionRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing mandatory 'Idempotency-Key' header")

    if idempotency_key in PlatformState.executed_actions:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "already_executed",
            "action": PlatformState.executed_actions[idempotency_key]
        }

    provider = PlatformState.providers.get(req.provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{req.provider_id}' not found")

    now_iso = Clock.now_iso()
    provider["status"] = "HEALTHY"
    provider["updated_at"] = now_iso

    action_record = {
        "idempotency_key": idempotency_key,
        "action_type": "enable_provider",
        "target_id": req.provider_id,
        "status": "COMPLETED",
        "executed_at": now_iso
    }
    PlatformState.executed_actions[idempotency_key] = action_record

    return {"status": "success", "message": f"Provider {req.provider_id} enabled", "action": action_record}


# --- Admin Controls for Test Scenarios ---

@app.post("/platform/admin/reset")
async def reset_platform():
    PlatformState.reset()
    return {"status": "reset", "virtual_time": Clock.now_iso()}


@app.post("/platform/admin/set_provider_status")
async def set_provider_status(req: SetProviderStatusRequest):
    provider = PlatformState.providers.get(req.provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{req.provider_id}' not found")
    provider["status"] = req.status
    if req.balance_cents is not None:
        provider["balance_cents"] = req.balance_cents
    provider["updated_at"] = Clock.now_iso()
    return {"status": "updated", "provider": provider}
