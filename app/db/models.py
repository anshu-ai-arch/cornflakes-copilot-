"""
Database Models for Incident State Machine, Idempotency, and Audit Logs.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    JSON,
    ForeignKey,
    UniqueConstraint,
    Index,
    Text,
    Boolean
)
from sqlalchemy.orm import relationship
from app.db.session import Base
from app.core.clock import Clock


def current_virtual_time() -> datetime:
    """Returns the current virtual clock timestamp."""
    return Clock.now()


class Incident(Base):
    """
    Core persistent state machine model for an incident.
    Survives all server restarts and worker deaths.
    """
    __tablename__ = "incidents"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=False, default="default_tenant", index=True)
    
    # Alert Deduplication Fingerprint (Trap T1)
    # Unique constraint ensures duplicate alerts map to the same incident record
    fingerprint = Column(String(128), unique=True, nullable=False, index=True)
    
    error_type = Column(String(128), nullable=False)
    entity_id = Column(String(128), nullable=False)
    
    # Lifecycle status: QUEUED, TRIAGING, DIAGNOSED, PROPOSED, AWAITING_APPROVAL,
    # EXECUTING, COMPLETED, BLOCKED, REJECTED, STALE_BLOCKED
    status = Column(String(64), nullable=False, default="QUEUED", index=True)
    current_node = Column(String(64), nullable=False, default="init")
    
    # Complete JSON state checkpoint (evidence, diagnosis, actions, policy, approval)
    state_data = Column(JSON, nullable=False, default=dict)
    
    # Counter for identical duplicate alerts merged into this incident
    duplicate_count = Column(Integer, nullable=False, default=1)
    
    # Worker Lease management (Multi-worker concurrency & crash-recovery)
    lease_worker_id = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=current_virtual_time)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=current_virtual_time, onupdate=current_virtual_time)

    # Relationships
    actions = relationship("IncidentAction", back_populates="incident", cascade="all, delete-orphan")
    events = relationship("IncidentEvent", back_populates="incident", cascade="all, delete-orphan")


class IncidentAction(Base):
    """
    Tracks action execution with strict Idempotency Keys (Traps T2, T3).
    Ensures that an action (e.g. refund_credits) is executed exactly once.
    """
    __tablename__ = "incident_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(64), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Idempotency key: hash(incident_id, action_type, target_id)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    
    action_type = Column(String(64), nullable=False)  # refund_credits, retry_shot, etc.
    target_id = Column(String(128), nullable=False)    # job_123, runway, etc.
    parameters = Column(JSON, nullable=False, default=dict)
    
    # Status: PENDING, IN_FLIGHT, COMPLETED, FAILED, REJECTED
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=current_virtual_time)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    incident = relationship("Incident", back_populates="actions")


class IncidentEvent(Base):
    """
    Append-only audit trail and Server-Sent Events (SSE) log.
    Resumable via event id.
    """
    __tablename__ = "incident_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String(64), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=current_virtual_time)

    incident = relationship("Incident", back_populates="events")


class MockLedger(Base):
    """
    Append-only billing ledger for the platform.
    Used for verifying refunds and preventing double-charges.
    """
    __tablename__ = "mock_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, default="default_tenant")
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    amount_cents = Column(Integer, nullable=False)
    transaction_type = Column(String(32), nullable=False)  # CHARGE, REFUND, RESERVATION
    reason = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=current_virtual_time)
