"""
Staleness Detection and Pre-Execution Re-Validation Guard.
Handles:
- Tool TTL age checking and plain English explanations (Trap T6)
- Pre-Execution Re-Validation before executing approved actions (Trap T4)
- Approval expiration after configured TTL
"""

from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timezone
from app.core.clock import Clock
from app.core.config import settings
from app.tools.deterministic import DeterministicTools
from app.tools.envelope import ToolResult


class StalenessGuard:
    @staticmethod
    def evaluate_evidence_staleness(
        evidence: Dict[str, Any],
        allow_refetch: bool = True
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        """
        Trap T6: Evaluates age of each tool result against its TTL.
        If data is older than limit, attempts re-fetch.
        If re-fetch fails, marks as stale.
        Returns: (is_stale: bool, updated_evidence: Dict, plain_english_warnings: List[str])
        """
        is_stale = False
        warnings = []
        updated_evidence = dict(evidence)

        for tool_name, tool_envelope in evidence.items():
            if not isinstance(tool_envelope, dict):
                continue

            as_of_str = tool_envelope.get("as_of")
            ttl_seconds = tool_envelope.get("ttl_seconds", 60)

            if not as_of_str:
                continue

            # Calculate age relative to virtual clock
            try:
                as_of_dt = datetime.fromisoformat(as_of_str)
                current_dt = Clock.now()
                if as_of_dt.tzinfo is None:
                    as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
                if current_dt.tzinfo is None:
                    current_dt = current_dt.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (current_dt - as_of_dt).total_seconds())
            except Exception:
                age_seconds = 0.0

            if age_seconds > ttl_seconds:
                # Data is past TTL
                age_hours = int(age_seconds // 3600)
                age_days = int(age_seconds // 86400)
                
                if age_days >= 1:
                    age_desc = f"{age_days} day(s) old ({int(age_seconds)}s)"
                elif age_hours >= 1:
                    age_desc = f"{age_hours} hour(s) old"
                else:
                    age_desc = f"{int(age_seconds)} second(s) old"

                warning_msg = (
                    f"Data for tool '{tool_name}' is {age_desc}, which exceeds configured limit of {ttl_seconds}s."
                )

                if allow_refetch:
                    # Attempt re-fetch
                    refetched = StalenessGuard._refetch_tool(tool_name, tool_envelope)
                    if refetched:
                        updated_evidence[tool_name] = refetched.model_dump()
                        warnings.append(f"{warning_msg} Successfully re-fetched fresh snapshot.")
                        continue

                # If re-fetch not possible or failed -> mark STALE!
                is_stale = True
                tool_envelope["is_stale"] = True
                warnings.append(f"{warning_msg} Stale data detected; proposal blocked from approval.")

        return is_stale, updated_evidence, warnings

    @staticmethod
    def _refetch_tool(tool_name: str, envelope: Dict[str, Any]) -> Optional[ToolResult]:
        """Attempts fresh query for the given tool."""
        try:
            data = envelope.get("data", {})
            if tool_name == "provider_status":
                prov_id = data.get("provider_id")
                if prov_id:
                    return DeterministicTools.provider_status(prov_id)
            elif tool_name == "get_job":
                job_id = data.get("job", {}).get("job_id") or data.get("job_id")
                if job_id:
                    return DeterministicTools.get_job(job_id)
            elif tool_name == "ledger_for_job":
                job_id = data.get("job_id")
                if job_id:
                    return DeterministicTools.ledger_for_job(job_id)
            elif tool_name == "queue_depth":
                return DeterministicTools.queue_depth()
        except Exception:
            return None
        return None

    @staticmethod
    def revalidate_before_execution(
        proposed_actions: List[Dict[str, Any]],
        evidence: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Trap T4: Re-validation guard executed immediately when approval arrives.
        Checks if the real-world situation changed while waiting for approval:
        - Did provider already recover? (e.g. topped up, status is HEALTHY)
        - Did job already finish?
        Returns: (is_valid: bool, rejection_reason: Optional[str])
        """
        for act in proposed_actions:
            action_type = act.get("action_type")
            target_id = act.get("target_id")

            # Check 1: Provider recovery check (Trap T4)
            if action_type == "disable_provider":
                # Freshly query provider status from target system
                fresh_status = DeterministicTools.provider_status(target_id)
                status_val = fresh_status.data.get("status")
                balance = fresh_status.data.get("balance_cents", 0)

                if status_val == "HEALTHY":
                    # Provider recovered while waiting for approval!
                    return False, (
                        f"Approval rejected: Condition changed since incident creation. "
                        f"Provider '{target_id}' has already recovered (status is HEALTHY, balance ${balance/100:.2f}). "
                        f"Disabling a healthy provider is disallowed."
                    )

            # Check 2: Job already finished check
            elif action_type in ["requeue_job", "retry_shot"]:
                job_id = target_id if action_type == "requeue_job" else act.get("parameters", {}).get("job_id")
                if job_id:
                    fresh_job = DeterministicTools.get_job(job_id)
                    current_status = fresh_job.data.get("job", {}).get("status")
                    if current_status == "SUCCEEDED":
                        return False, (
                            f"Approval rejected: Job '{job_id}' has already SUCCEEDED. "
                            f"Requeue or retry action is no longer valid."
                        )

        return True, None
