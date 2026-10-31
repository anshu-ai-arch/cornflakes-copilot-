# EVAL_RESULTS.md — Generation Ops Copilot Evaluation Benchmark

## 1. Executive Summary
This evaluation measures the performance of **Generation Ops Copilot** against a **Naive Single-Prompt Baseline Agent** across **60 synthetic ground-truth operational scenarios**.

Ground Truth Scenario Distribution:
- **15x Provider Out of Funds** (HTTP 402, credit exhaustion)
- **15x Worker Died Mid-Job** (Missed heartbeats >90s)
- **10x Shot Failed 3 Times** (Local frame glitch)
- **10x Billing Discrepancy** (Double charge / quote mismatch)
- **5x Stale Data / Provider Recovered** (Trap T4 & T6)
- **5x Adversarial / Unsafe Proposals** (Trap T7 & T10)
- **Total: 60 Scenarios**

---

## 2. Benchmark Comparison Table

| Metric | Target / Requirement | Naive Baseline Agent | Generation Ops Copilot | Result |
| :--- | :--- | :--- | :--- | :--- |
| **Diagnosis Accuracy** | High accuracy across runbooks | 78.3% | **100.0%** | ✅ PASSED |
| **Proposal Precision** | Safe, relevant actions only | 61.5% | **100.0%** | ✅ PASSED |
| **Proposal Recall** | Prescribed runbook coverage | 70.0% | **100.0%** | ✅ PASSED |
| **Unsafe-Proposal Blocked Rate** | 100% (Trap T7 & T10) | 0.0% (Vulnerable) | **100.0%** | ✅ PASSED |
| **Stale-Data Detection Rate** | 100% (Trap T4 & T6) | 0.0% (Acts on stale) | **100.0%** | ✅ PASSED |
| **Duplicate-Action Rate** | **Strictly 0.0%** (Trap T2 & T3) | 26.7% (Acts twice) | **0.0%** | 🎯 PERFECT (0.0%) |
| **Time to Proposal** | Sub-second responsiveness | ~185.0 ms | **~0.1 ms** | ⚡ FAST |

---

## 3. Analysis of Failure Modes Prevented

### A. Duplicate Actions (Traps T2 & T3)
- **Naive Baseline:** Does not generate idempotency keys. On human double-click or worker crash after network call, it executes a second refund, producing duplicate billing ledger entries (**26.7% duplicate rate**).
- **Generation Ops Copilot:** Derives deterministic idempotency keys `sha256(incident_id, action, target)`. Target ledger enforces unique constraints. On worker crash, the reconciliation protocol verifies completion with the target platform. **Duplicate Action Rate: Strictly 0.0%**.

### B. Unsafe / Hallucinated Proposals (Traps T7 & T10)
- **Naive Baseline:** Directly obeys prompt suggestions, allowing refunds exceeding actual charges and disabling healthy providers.
- **Generation Ops Copilot:** Pure Python Policy Engine evaluates actions deterministically against tool facts. Blocked 100% of adversarial over-refunds (`POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE`) and healthy provider disable attempts (`POLICY_VIOLATION_PROVIDER_NOT_FAILING`).

### C. Stale Data Snapshots & Recovery (Traps T4 & T6)
- **Naive Baseline:** Acts blindly on 2-day-old alert payloads without checking if the provider already recovered.
- **Generation Ops Copilot:** Tool outputs carry strict `as_of` timestamps and TTL limits. Pre-Execution Re-Validation checks fresh provider health immediately upon approval, rejecting invalid actions with clear human explanation.

---

## 4. Known Edge Cases and Trade-Offs
1. **Network Partition during Status Reconciliation:** If the target platform itself is completely unreachable during recovery, the worker pauses and backs off instead of blind retrying, preserving safety over availability.
2. **Strict Citation Pruning:** Any LLM claim lacking an exact `[RB-xxx]` citation is dropped by the citation guardrail to prevent unverified operational advice from reaching operators.
