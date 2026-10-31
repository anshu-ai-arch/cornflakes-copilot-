"""
Policy as Code Engine and Dry-Run Calculator.
Enforces hard business boundaries that the LLM cannot change:
- Refund <= Actual Job Charge (Trap T7)
- Max Refund per Incident and per Day
- Disable Provider ONLY if verified failing by tool (Trap T10)
- Money actions require named human approver
- Safe Auto-Approve allowlist under cost cap (Requirement 9)
- Dry-Run impact computation (Requirement 14)
"""

from typing import Dict, Any, List, Tuple
from app.core.config import settings
from app.core.clock import Clock
from app.tools.deterministic import DeterministicTools


class PolicyViolation:
    def __init__(self, code: str, message: str, action: Dict[str, Any]):
        self.code = code
        self.message = message
        self.action = action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "action_type": self.action.get("action_type"),
            "target_id": self.action.get("target_id")
        }


def compute_dry_run_preview(action: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    """
    Requirement 14: Shows exactly what each proposed action would change before approval.
    """
    action_type = action.get("action_type")
    target_id = action.get("target_id")
    params = action.get("parameters", {})

    if action_type == "refund_credits":
        amount_cents = params.get("amount_cents", 0)
        dollars = f"${amount_cents / 100:.2f}"
        ledger_data = evidence.get("ledger", {}).get("data", {})
        net_charged = ledger_data.get("net_charged_cents", 0)
        remaining = max(0, net_charged - amount_cents)
        return (
            f"[DRY-RUN] Will refund {dollars} to customer for job '{target_id}'. "
            f"Net job charge will decrease from ${net_charged/100:.2f} to ${remaining/100:.2f}."
        )

    elif action_type == "disable_provider":
        prov_status = evidence.get("provider_status", {}).get("data", {}).get("status", "UNKNOWN")
        return (
            f"[DRY-RUN] Will transition provider '{target_id}' status from '{prov_status}' to 'DISABLED'. "
            f"Active dispatcher will halt routing new film tickets to '{target_id}'."
        )

    elif action_type == "enable_provider":
        return f"[DRY-RUN] Will transition provider '{target_id}' status to 'HEALTHY' and resume routing."

    elif action_type == "requeue_job":
        return f"[DRY-RUN] Will reset attempts counter to 0 for job '{target_id}' and re-insert into ready queue."

    elif action_type == "retry_shot":
        shot_id = params.get("shot_id", target_id)
        return f"[DRY-RUN] Will reset attempt counter to 0 for shot ticket '{shot_id}' and schedule isolated re-render."

    return f"[DRY-RUN] Will execute action '{action_type}' on target '{target_id}'."


class PolicyEngine:
    @staticmethod
    def evaluate_action(
        action: Dict[str, Any],
        evidence: Dict[str, Any],
        daily_refunded_cents: int = 0
    ) -> Tuple[bool, bool, List[PolicyViolation]]:
        """
        Evaluates a single action against deterministic policy rules.
        Returns: (is_allowed: bool, requires_human: bool, violations: List[PolicyViolation])
        """
        action_type = action.get("action_type")
        target_id = action.get("target_id")
        params = action.get("parameters", {})
        violations: List[PolicyViolation] = []

        # Default stances
        requires_human = False

        # --- Rule 1 & 2 & 3: Refund Policy Checks (Trap T7) ---
        if action_type == "refund_credits":
            requires_human = True  # Money touches require named human!
            amount_cents = params.get("amount_cents", 0)

            # Check job actual charge from deterministic tool
            ledger_data = evidence.get("ledger", {}).get("data", {})
            actual_charged_cents = ledger_data.get("total_charged_cents", 0)

            # Trap T7: Proposed refund cannot exceed actual charge!
            if amount_cents > actual_charged_cents:
                violations.append(PolicyViolation(
                    code="POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE",
                    message=(
                        f"Proposed refund of ${amount_cents/100:.2f} exceeds "
                        f"actual charge of ${actual_charged_cents/100:.2f} for job '{target_id}'."
                    ),
                    action=action
                ))

            # Max per-incident limit
            if amount_cents > settings.MAX_REFUND_PER_INCIDENT_CENTS:
                violations.append(PolicyViolation(
                    code="POLICY_VIOLATION_MAX_INCIDENT_REFUND_EXCEEDED",
                    message=(
                        f"Refund of ${amount_cents/100:.2f} exceeds max per-incident cap "
                        f"of ${settings.MAX_REFUND_PER_INCIDENT_CENTS/100:.2f}."
                    ),
                    action=action
                ))

            # Daily tenant limit
            if (daily_refunded_cents + amount_cents) > settings.MAX_REFUND_PER_DAY_CENTS:
                violations.append(PolicyViolation(
                    code="POLICY_VIOLATION_DAILY_REFUND_CAP_EXCEEDED",
                    message=(
                        f"Daily refund cap of ${settings.MAX_REFUND_PER_DAY_CENTS/100:.2f} "
                        f"would be exceeded by this refund."
                    ),
                    action=action
                ))

        # --- Rule 4: Disable Provider Policy (Trap T10) ---
        elif action_type == "disable_provider":
            requires_human = True  # Disruptive action requires human

            # Deterministic check: Provider MUST be currently failing according to tool!
            prov_tool = DeterministicTools.provider_status(target_id)
            is_failing = prov_tool.data.get("is_failing", False)
            prov_status = prov_tool.data.get("status")

            if not is_failing:
                # Trap T10: Cannot disable a healthy provider!
                violations.append(PolicyViolation(
                    code="POLICY_VIOLATION_PROVIDER_NOT_FAILING",
                    message=(
                        f"Cannot disable provider '{target_id}' because tool status "
                        f"is '{prov_status}' (not currently failing)."
                    ),
                    action=action
                ))

        # --- Rule 5 & 6: Safe Auto-Approve Allowlist (Requirement 9) ---
        elif action_type == "retry_shot":
            # Auto-approvable if under cost cap
            shot_cost = params.get("cost_cents", 25)
            if shot_cost <= settings.AUTO_APPROVE_COST_CAP_CENTS:
                requires_human = False  # Auto-approve!
            else:
                requires_human = True

        elif action_type == "requeue_job":
            job_data = evidence.get("job", {}).get("data", {}).get("job", {})
            attempts = job_data.get("attempts", 0)
            max_attempts = job_data.get("max_attempts", 3)
            if attempts < max_attempts:
                requires_human = False  # Auto-approve requeue
            else:
                requires_human = True

        else:
            # All other unknown/disruptive actions default to requiring human
            requires_human = True

        is_allowed = len(violations) == 0
        return is_allowed, requires_human, violations

    @staticmethod
    def evaluate_all(
        actions: List[Dict[str, Any]],
        evidence: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates a batch of proposed actions.
        Produces full policy verdict and attaches dry-run previews.
        """
        all_violations = []
        requires_human = False
        is_blocked = False

        for act in actions:
            # Compute dry-run preview for human inspection
            act["dry_run_preview"] = compute_dry_run_preview(act, evidence)

            allowed, needs_human, viols = PolicyEngine.evaluate_action(act, evidence)
            act["is_allowed"] = allowed
            act["requires_human"] = needs_human

            if not allowed:
                is_blocked = True
                for v in viols:
                    all_violations.append(v.to_dict())

            if needs_human:
                requires_human = True

        status_str = "BLOCKED" if is_blocked else ("REQUIRE_HUMAN" if requires_human else "ALLOW_AUTO")

        return {
            "status": status_str,
            "is_blocked": is_blocked,
            "requires_human": requires_human,
            "violations": all_violations,
            "evaluated_at": Clock.now_iso()
        }
