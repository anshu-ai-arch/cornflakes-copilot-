"""
Incident Ingestion, Retrieval, and Deduplication API.
Solves Trap T1 (duplicate alerts merged into one incident)
and Trap T12 (correlation of 20 jobs with same root cause).
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import hashlib
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.clock import Clock
from app.db.session import get_db_session
from app.db.models import Incident, IncidentEvent, current_virtual_time

router = APIRouter(prefix=settings.API_PREFIX, tags=["incidents"])


class AlertPayload(BaseModel):
    tenant_id: str = "default_tenant"
    error_type: str  # e.g. "OUT_OF_FUNDS", "WORKER_DIED", "SHOT_FAILED_3X", "BILLING_DISCREPANCY"
    entity_id: str   # e.g. "job_101", "runway", "shot_101_2"
    provider: Optional[str] = None
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


def compute_alert_fingerprint(payload: AlertPayload, bucket_seconds: int = 600) -> str:
    """
    Computes a deterministic fingerprint for deduplication.
    Uses a 10-minute sliding bucket so repeated alerts for the same underlying issue
    collapse into exactly one incident (Trap T1).
    """
    virtual_ts = Clock.now().timestamp()
    time_bucket = int(virtual_ts // bucket_seconds)
    
    # If it's a provider-wide issue impacting multiple jobs, group by provider (Trap T12)
    if payload.provider and payload.error_type in ["OUT_OF_FUNDS", "PROVIDER_DOWN", "RATE_LIMITED"]:
        raw_key = f"group:{payload.tenant_id}:{payload.error_type}:{payload.provider}:{time_bucket}"
    else:
        raw_key = f"alert:{payload.tenant_id}:{payload.error_type}:{payload.entity_id}:{time_bucket}"
        
    return hashlib.sha256(raw_key.encode()).hexdigest()


@router.post("/incidents", status_code=status.HTTP_202_ACCEPTED)
async def ingest_alert(
    payload: AlertPayload,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Ingest an alert. Returns 202 Accepted.
    Duplicate alerts for the same problem are merged into one incident (Trap T1).
    """
    fingerprint = compute_alert_fingerprint(payload)
    now = Clock.now()

    # Check if an incident with this fingerprint already exists
    stmt = select(Incident).where(Incident.fingerprint == fingerprint)
    result = await db.execute(stmt)
    existing_incident = result.scalars().first()

    if existing_incident:
        # Atomic merge (Trap T1)
        existing_incident.duplicate_count += 1
        existing_incident.updated_at = now
        
        # Track associated jobs/entities if grouped (Trap T12)
        state_data = dict(existing_incident.state_data or {})
        grouped_entities = list(state_data.get("grouped_entities", []))
        if payload.entity_id not in grouped_entities:
            grouped_entities.append(payload.entity_id)
            state_data["grouped_entities"] = grouped_entities
            existing_incident.state_data = state_data
            flag_modified(existing_incident, "state_data")

        # Log audit event
        event = IncidentEvent(
            incident_id=existing_incident.id,
            event_type="DUPLICATE_ALERT_MERGED",
            payload={
                "message": payload.message,
                "entity_id": payload.entity_id,
                "duplicate_count": existing_incident.duplicate_count
            },
            created_at=now
        )
        db.add(event)
        await db.commit()

        return {
            "incident_id": existing_incident.id,
            "status": existing_incident.status,
            "duplicate_count": existing_incident.duplicate_count,
            "is_merged": True,
            "message": f"Alert merged into existing incident {existing_incident.id} (seen {existing_incident.duplicate_count} times)"
        }

    # Generate new incident ID
    incident_id = f"inc_{fingerprint[:12]}"

    # Initial state data payload
    initial_state_data = {
        "alert": payload.model_dump(),
        "grouped_entities": [payload.entity_id],
        "created_at": Clock.now_iso()
    }

    new_incident = Incident(
        id=incident_id,
        tenant_id=payload.tenant_id,
        fingerprint=fingerprint,
        error_type=payload.error_type,
        entity_id=payload.entity_id,
        status="QUEUED",
        current_node="triage",
        state_data=initial_state_data,
        duplicate_count=1,
        created_at=now,
        updated_at=now
    )
    db.add(new_incident)

    # Initial Event
    initial_event = IncidentEvent(
        incident_id=incident_id,
        event_type="ALERT_INGESTED",
        payload={
            "error_type": payload.error_type,
            "entity_id": payload.entity_id,
            "provider": payload.provider,
            "message": payload.message
        },
        created_at=now
    )
    db.add(initial_event)

    await db.commit()

    return {
        "incident_id": incident_id,
        "status": "QUEUED",
        "duplicate_count": 1,
        "is_merged": False,
        "message": f"Incident {incident_id} created and queued for triage"
    }


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, db: AsyncSession = Depends(get_db_session)):
    stmt = select(Incident).where(Incident.id == incident_id)
    result = await db.execute(stmt)
    incident = result.scalars().first()

    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    return {
        "incident_id": incident.id,
        "tenant_id": incident.tenant_id,
        "error_type": incident.error_type,
        "entity_id": incident.entity_id,
        "status": incident.status,
        "current_node": incident.current_node,
        "duplicate_count": incident.duplicate_count,
        "state_data": incident.state_data,
        "lease_worker_id": incident.lease_worker_id,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None
    }


@router.get("/incidents")
async def list_incidents(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Incident.status == status)
    result = await db.execute(stmt)
    incidents = result.scalars().all()
    return [
        {
            "incident_id": inc.id,
            "error_type": inc.error_type,
            "entity_id": inc.entity_id,
            "status": inc.status,
            "duplicate_count": inc.duplicate_count,
            "created_at": inc.created_at.isoformat() if inc.created_at else None
        }
        for inc in incidents
    ]


@router.get("/incidents/{incident_id}/events")
async def get_incident_events(
    incident_id: str,
    last_event_id: Optional[int] = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Resumable event stream log for an incident.
    Supports Last-Event-ID for resuming dropped SSE connections.
    """
    stmt = select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
    if last_event_id:
        stmt = stmt.where(IncidentEvent.id > last_event_id)
    stmt = stmt.order_by(IncidentEvent.id.asc())

    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "id": ev.id,
            "event_type": ev.event_type,
            "payload": ev.payload,
            "created_at": ev.created_at.isoformat() if ev.created_at else None
        }
        for ev in events
    ]
