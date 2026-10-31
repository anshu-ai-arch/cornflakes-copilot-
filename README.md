# 🌽 Cornflakes Media — Generation Ops Copilot

**Candidate:** Anshu Tiwari  
**Role:** Agentic AI: Safe Incident Triage, Human Approval & Durable Workflows  
**Evaluation Standard:** 100% Reproducible, Zero Paid APIs, Strictly Deterministic Safety Boundaries  

---

## ⚡ Quickstart (One Single Command)

To run the complete production environment (PostgreSQL 16, FastAPI API, Worker 1, Worker 2, Mock Platform, and the warm cream Operations Dashboard):

```bash
docker compose up --build
```

### Access Endpoints:
- **Operations Dashboard (Warm Cream UI):** [http://localhost:8000](http://localhost:8000)
- **Interactive OpenAPI Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Mock Video Platform API:** [http://localhost:8001](http://localhost:8001)
- **Prometheus Metrics:** [http://localhost:8000/metrics?format=prometheus](http://localhost:8000/metrics?format=prometheus)

---

## 🎯 Verification Commands

### 1. Run Automated Test Suite (29 Tests covering T1–T12)
```bash
make test
# OR: ./venv/bin/pytest tests/ -v
```

### 2. Run 60-Scenario Evaluation Benchmark
```bash
make eval
# OR: ./venv/bin/python -m eval.evaluator
```
Generates terminal comparison table and commits `EVAL_RESULTS.md`.

### 3. Run Live Chaos Suite (T1, T2, T3, T5, T12)
```bash
make chaos
# OR: bash scripts/chaos.sh
```

---

## 🛡️ Traps We Designed For (T1 – T12 Verification Matrix)

| Trap # | Scenario | Expected Behavior | Automated Test |
| :--- | :--- | :--- | :--- |
| **T1** | Same alert delivered 10 times | Merged into exactly one incident | `tests/test_part3_incident_api.py::test_t1_same_alert_delivered_10_times` |
| **T2** | Human double-clicks approve on refund | Exactly one refund; one ledger entry | `tests/test_part8_exactly_once_execution.py::test_t2_double_click_approve_produces_single_refund` |
| **T3** | Worker killed after refund call before status write | On resume, verifies on platform; no second refund | `tests/test_part8_exactly_once_execution.py::test_t3_worker_killed_after_refund_call_before_status_write` |
| **T4** | Approval arrives 2 days later; provider recovered | Re-validated; rejected with clear explanation | `tests/test_part7_approval_and_staleness.py::test_t4_approval_arrives_two_days_later_provider_recovered` |
| **T5** | All services restarted while waiting for approval | Incident resumes in the exact same state | `tests/test_part4_state_machine_and_workers.py::test_t5_survives_restarts_while_awaiting_approval` |
| **T6** | Provider status is 2 days old | Stale data detected; marked STALE & unapprovable | `tests/test_part7_approval_and_staleness.py::test_t6_staleness_guard_two_day_old_provider_status` |
| **T7** | LLM proposes refund above job's charge | Blocked by policy; code `POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE` | `tests/test_part6_policy_engine.py::test_t7_refund_above_actual_charge_blocked` |
| **T8** | LLM states failure count unsupported by tools | Hallucinated number replaced with tool fact | `tests/test_part5_llm_and_rag.py::test_t8_hallucinated_number_replaced_by_tool_value` |
| **T9** | LLM turned off (`MOCK_LLM_ENABLED=false`) | Rule-based diagnosis still works accurately | `tests/test_part5_llm_and_rag.py::test_t9_llm_turned_off_rule_based_diagnosis` |
| **T10**| Proposal disables a healthy provider | Blocked by policy; code `POLICY_VIOLATION_PROVIDER_NOT_FAILING` | `tests/test_part6_policy_engine.py::test_t10_disabling_healthy_provider_blocked` |
| **T11**| Runbook claim without citation | Sentence dropped from output | `tests/test_part5_llm_and_rag.py::test_t11_uncited_claim_dropped_from_answer` |
| **T12**| Same root cause hits 20 jobs in 1 minute | Bounded work; grouped under single incident | `tests/test_part3_incident_api.py::test_t12_same_root_cause_hits_20_jobs` |

---

## ⏰ Injectable Virtual Clock & Time Travel

To allow fast testing of 2-day expiration and recovery scenarios without sleeping in real time:
- **Advance Clock 2 Days:**
  ```bash
  curl -X POST http://localhost:8000/v1/clock/advance -H "Content-Type: application/json" -d '{"seconds": 172800}'
  ```
- **Reset Clock to Real Time:**
  ```bash
  curl -X POST http://localhost:8000/v1/clock/reset
  ```
- **From the Web UI:** Use the dedicated clock buttons in the top navbar (`+1h`, `+2 Days`, `Reset`).

---

## 🎨 Operations Dashboard (Warm Cream Palette)
The UI avoids the generic dark-mode AI aesthetic and uses a warm, editorial light-yellow cream palette inspired by corn silk and warm grain (`#FAF7EE`). It features:
- **Incident Board:** Status badges, age, pending approvals, and duplicate alert counters.
- **Evidence Dossier:** Every deterministic tool output displayed with its exact `as_of` timestamp and freshness indicator.
- **Dry-Run Mode (Requirement 14):** Visual diff showing the exact dollar and state changes before human approval.
- **One-Click Human Authorization:** Approve and Reject modal capturing approver identity and reasoning.
- **Live SSE Event Log:** Real-time resumable audit trail.

---

## 🎤 20-Minute Live Walkthrough Defense Script (For Anshu)

When walking through this project with senior engineering evaluators, structure your presentation into 4 distinct acts:

1. **Act 1: The Core Invariant (Minutes 0–4)**
   - *"I structured this system around one foundational rule: Facts and numbers come from deterministic tools with timestamps; the LLM is only an explainer and proposer; code decides what is allowed."*
   - Show `app/tools/envelope.py` and `app/engine/nodes.py`.

2. **Act 2: Distributed Durability & Idempotency (Minutes 4–10)**
   - Show **Trap T2** and **Trap T3** solution in `app/engine/executor.py`.
   - *"We use deterministic action idempotency keys: `hash(incident, action, target)`. If a worker process is killed right after the refund HTTP call before writing status to DB, on revival it queries the target platform. It reconciles without firing a second refund. Our Duplicate Action Rate is strictly 0.0%."*

3. **Act 3: Staleness Protocol & Time Travel (Minutes 10–15)**
   - Demonstrate **Trap T4** and **Trap T6** live on the dashboard.
   - Click `+2 Days` on the top bar. Show how the Pre-Execution Re-Validation Guard intercepts a late approval when the provider has recovered, rejecting it with a 409 Conflict.

4. **Act 4: Scope Decisions & Benchmark Results (Minutes 15–20)**
   - Walk through `ARCHITECTURE.md` Section 7 ("What We Built vs What We Deliberately Cut").
   - Run `make eval` live to display the 60-scenario benchmark comparison against the naive baseline.
