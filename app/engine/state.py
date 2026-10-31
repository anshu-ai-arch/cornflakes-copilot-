"""
Incident State Schema.
Represents the complete, typed state machine checkpoint.
Persisted into incidents.state_data JSONB column after every node.
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.core.clock import Clock


class IncidentState(BaseModel):
    incident_id: str
    tenant_id: str = "default_tenant"
    error_type: str
    entity_id: str
    provider: Optional[str] = None
    
    # State machine lifecycle
    status: str = "QUEUED"  # QUEUED, TRIAGING, DIAGNOSED, PROPOSED, AWAITING_APPROVAL, EXECUTING, COMPLETED, BLOCKED, REJECTED
    current_node: str = "init"
    
    # 1. Triage Node (Evidence Dossier with as_of timestamps)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    is_stale: bool = False
    
    # 2. Diagnose Node (LLM / Rule Root Cause & Citations)
    diagnosis: Optional[Dict[str, Any]] = None
    
    # 3. Propose Node (Candidate Actions + Idempotency Keys)
    proposed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    
    # 4. Policy Node (Policy Guardrails)
    policy_verdict: Optional[Dict[str, Any]] = None
    
    # 5. Approval Node (Human Decision)
    approval: Optional[Dict[str, Any]] = None
    
    # 6. Execution Node (Action Results & Ledger Entries)
    execution_results: List[Dict[str, Any]] = Field(default_factory=list)
    
    # 7. Verification Node
    verification: Optional[Dict[str, Any]] = None
    
    # 8. Agent Reasoning Trajectory (Chain of Thought & Tool Trail)
    trajectory: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Metadata
    grouped_entities: List[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: Clock.now_iso())
