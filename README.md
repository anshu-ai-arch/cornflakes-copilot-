# 🌽 Cornflakes Media — Generation Ops Copilot

> **Safe Incident Triage, Human Approval & Durable Workflows**  
> **Candidate:** Anshu Tiwari  
> **Evaluation Standard:** 100% Deterministic Safety, Zero Paid APIs, Exactly-Once Execution, Durable State Machine  
> **Repository:** [https://github.com/anshu-ai-arch/cornflakes-copilot-.git](https://github.com/anshu-ai-arch/cornflakes-copilot-.git)  

---

## 📌 Executive Deliverables Checklist (Section 7 Compliance)

| Deliverable | Description | Status | Reference / Location |
| :--- | :--- | :---: | :--- |
| **1. Clean Clone Repo** | Single-command start (`docker compose up --build`), 2+ workers, PostgreSQL 16, Mock Platform, UI | ✅ **Verified** | [`docker-compose.yml`](docker-compose.yml), [`Dockerfile`](Dockerfile) |
| **2. Comprehensive README** | How to run, scenarios, clock control, test gates, chaos triggers, walkthrough script | ✅ **Verified** | [`README.md`](README.md) |
| **3. Architecture Document** | State machine, LLM boundary, staleness rules, idempotency, policy code, trade-offs | ✅ **Verified** | [`ARCHITECTURE.md`](ARCHITECTURE.md) (2 pages concise) |
| **4. Benchmark Results** | 60 synthetic ground-truth scenarios, baseline comparison, 0.0% duplicate actions | ✅ **Verified** | [`EVAL_RESULTS.md`](EVAL_RESULTS.md) |
| **5. Automated Tests & Chaos** | 33 automated tests covering Traps T1–T12 + cross-platform live chaos suite | ✅ **Verified** | [`tests/`](tests/), [`scripts/chaos.py`](scripts/chaos.py), [`scripts/chaos.sh`](scripts/chaos.sh) |
| **6. Demo & Presentation** | 5-minute video recording guide and 20-minute live technical walkthrough script | ✅ **Verified** | See Sections 7 & 8 below |

---

## 🏛️ The Core Invariant

The entire system is architected around one non-negotiable invariant:
> **"Numbers and facts come from deterministic tools, never from the model; the model only reads tool results and writes the explanation and the proposal; code decides what is allowed."**

```
Alert Ingest
    │
    ▼
[TRIAGE] ──────────► Deterministic Tools (as_of timestamp, source, TTL limits)
    │
    ▼
[DIAGNOSE] ────────► Mock LLM (Structured JSON) + BM25 Runbook RAG (Citations enforced)
    │
    ▼
[PROPOSE] ─────────► Rule Fallback Tier (100% operational with LLM turned off)
    │
    ▼
[POLICY] ──────────► Code as Law (Refund caps, provider failure proof, dry-run diff)
    │
    ▼
[WAIT APPROVAL] ───► Zero workers held; state persisted in PostgreSQL; row lease released
    │
    ▼
[EXECUTE] ─────────► Exactly-Once: sha256(incident, action, target) + platform outbox check
    │
    ▼
[VERIFY] ──────────► Post-execution state reconciliation & append-only audit trail
```

---

## ⚡ Quickstart (One Single Command)

To spin up the complete production environment (PostgreSQL 16, FastAPI API, Worker 1, Worker 2, Mock Video Platform, and the Warm Cream Operations Dashboard):

### Universal Docker (Mac, Linux & Windows):
```bash
docker compose up --build -d
```

### Windows (One-Click Launcher):
Double-click `run.bat` or run in terminal:
```cmd
run.bat
```
*(Automatically detects Docker, or falls back to high-performance local mode with SQLite and embedded worker)*.

### Access Endpoints:
- **Operations Dashboard (Warm Cream UI):** [http://localhost:8000](http://localhost:8000)
- **Interactive OpenAPI Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Mock Video Platform API:** [http://localhost:8001](http://localhost:8001)
- **Prometheus Metrics:** [http://localhost:8000/metrics?format=prometheus](http://localhost:8000/metrics?format=prometheus)

---

## 🎯 Verification Commands (Cross-Platform)

| Target | Mac / Linux | Windows (PowerShell / CMD) | Inside Docker (Any OS) |
| :--- | :--- | :--- | :--- |
| **Run All 33 Tests** | `make test` | `test.bat` | `docker compose exec api pytest tests/ -v` |
| **60-Scenario Benchmark** | `make eval` | `eval.bat` | `docker compose exec api python -m eval.evaluator` |
| **Run Live Chaos Suite** | `make chaos` | `chaos.bat` | `docker compose exec api python scripts/chaos.py` |
| **Stop All Containers** | `make down` | `docker compose down` | `docker compose down` |

*(Note: The 60-scenario evaluation benchmark can also be executed live directly from the web browser via the **`📊 Evaluation & Benchmarks`** tab).*

---

## 🛡️ Traps We Designed For (T1 – T12 Verification Matrix)

Every trap specified in Section 6 of the brief is guarded by code and covered by automated test suites:

| Trap # | Scenario | Expected Behavior | Automated Test Reference |
| :--- | :--- | :--- | :--- |
| **T1** | Same alert delivered 10 times | Merged into exactly 1 incident with `duplicate_count = 10` | [`tests/test_part3_incident_api.py::test_t1_same_alert_delivered_10_times`](tests/test_part3_incident_api.py) |
| **T2** | Human double-clicks approve on refund | Exactly 1 refund; strictly 1 ledger entry recorded | [`tests/test_part8_exactly_once_execution.py::test_t2_double_click_approve_produces_single_refund`](tests/test_part8_exactly_once_execution.py) |
| **T3** | Worker killed (`SIGKILL`) after external refund call but before status write | On revival, worker reconciles with platform outbox; zero duplicate refunds | [`tests/test_part8_exactly_once_execution.py::test_t3_worker_killed_after_refund_call_before_status_write`](tests/test_part8_exactly_once_execution.py) |
| **T4** | Approval arrives 2 days later; provider recovered in interim | Pre-execution revalidation rejects approval with clear explanation (`409 Conflict`) | [`tests/test_part7_approval_and_staleness.py::test_t4_approval_arrives_two_days_later_provider_recovered`](tests/test_part7_approval_and_staleness.py) |
| **T5** | All services restarted while waiting for approval | Zero state held in memory; incident resumes in exact same state from DB | [`tests/test_part4_state_machine_and_workers.py::test_t5_survives_restarts_while_awaiting_approval`](tests/test_part4_state_machine_and_workers.py) |
| **T6** | Provider status snapshot is 2 days old | Stale data detected via TTL; marked `STALE_BLOCKED` with plain-language age | [`tests/test_part7_approval_and_staleness.py::test_t6_staleness_guard_two_day_old_provider_status`](tests/test_part7_approval_and_staleness.py) |
| **T7** | LLM proposes refund above the job's actual charge | Blocked by Policy Engine with `POLICY_VIOLATION_REFUND_EXCEEDS_CHARGE` | [`tests/test_part6_policy_engine.py::test_t7_refund_above_actual_charge_blocked`](tests/test_part6_policy_engine.py) |
| **T8** | LLM states a failure count unsupported by tools | Fact Normalizer replaces hallucinated number with verified tool fact | [`tests/test_part5_llm_and_rag.py::test_t8_hallucinated_number_replaced_by_tool_value`](tests/test_part5_llm_and_rag.py) |
| **T9** | LLM switched off (`MOCK_LLM_ENABLED=false`) | Deterministic rule-based fallback decision tree executes flawlessly | [`tests/test_part5_llm_and_rag.py::test_t9_llm_turned_off_rule_based_diagnosis`](tests/test_part5_llm_and_rag.py) |
| **T10**| Proposal disables a healthy provider | Blocked by Policy Engine with `POLICY_VIOLATION_PROVIDER_NOT_FAILING` | [`tests/test_part6_policy_engine.py::test_t10_disabling_healthy_provider_blocked`](tests/test_part6_policy_engine.py) |
| **T11**| Runbook claim without explicit citation | Citation Enforcer drops uncited sentence from diagnosis | [`tests/test_part5_llm_and_rag.py::test_t11_uncited_claim_dropped_from_answer`](tests/test_part5_llm_and_rag.py) |
| **T12**| Same root cause hits 20 jobs in 1 minute | Outage correlation clusters jobs into 1 parent incident; no alert storm | [`tests/test_part3_incident_api.py::test_t12_same_root_cause_hits_20_jobs`](tests/test_part3_incident_api.py) |

---

## ⏰ Injectable Virtual Clock & Time Travel

The platform features an injectable `ClockService` so that multi-day timeouts, expirations, and recovery scenarios execute deterministically in milliseconds:

- **Advance Clock by 2 Days (172,800 seconds):**
  ```bash
  curl -X POST http://localhost:8000/v1/clock/advance -H "Content-Type: application/json" -d '{"seconds": 172800}'
  ```
- **Reset Virtual Clock to Real Time:**
  ```bash
  curl -X POST http://localhost:8000/v1/clock/reset
  ```
- **From Web UI:** Click the dedicated time travel buttons in the top navbar (`+1h`, `+2 Days`, `Reset`).

---

## 🎨 Operations Dashboard (Warm Cream Aesthetic)

The web console at `http://localhost:8000` replaces generic AI dark-mode templates with a bespoke, warm editorial cream palette (`#FAF7EE`) inspired by corn silk and natural grain. Key capabilities include:

1. **Live Ops Center:**
   - **Stepped Agent Pipeline Animation:** Watch the agent traverse `Triage` $\rightarrow$ `Diagnose` $\rightarrow$ `Policy Check` with live spinners (~650ms per stage).
   - **Evidence Dossier:** Every deterministic tool output tagged with its exact `as_of` virtual timestamp and freshness pill.
   - **Dry-Run Diff Preview:** Visual dollar delta (`+$1.50`) and job state change before human sign-off.
   - **Live SSE Event Stream:** Resumable audit trail capturing all state transitions and policy decisions.
2. **Evaluation & Benchmarks Tab:**
   - Dynamic SVG comparison graphs comparing **Generation Ops Copilot** against the **Naive Single-Prompt Baseline**.
   - Interactive 12-Traps Defense Matrix with `[⚡ Test in Live Ops Center ➔]` one-click triggers.
   - 7-Layer Deterministic Safety Architecture breakdown.

---

## 📹 5-Minute Video Recording Guide (Deliverable 6)

If submitting a recorded Loom / screen video, follow this 5-minute pacing guide:

- **00:00 – 01:00 (Introduction & Architecture):** Show `docker-compose.yml` running 2 workers and PostgreSQL. State the Core Invariant: *"Facts from tools, reasoning from LLM, authorization from code."*
- **01:00 – 02:00 (Live Stepped Triage & Dry Run):** Select an incident on `http://localhost:8000`. Click *Triage & Diagnose Pipeline*. Show the 650ms stepped progression, BM25 runbook citation (`[RB-001]`), and the Dry-Run visual diff.
- **02:00 – 03:00 (Staleness & Time Travel — Traps T4 & T6):** Click `+2 Days` on the top bar. Attempt human approval. Show how the Pre-Execution Re-Validation Guard intercepts and rejects the action because the provider already recovered.
- **03:00 – 04:00 (Evaluation Benchmark):** Switch to the *Evaluation & Benchmarks* tab. Click *Run Benchmark Now*. Point to the **0.0% Duplicate Action Rate** vs **26.7% Naive Baseline**.
- **04:00 – 05:00 (Automated Tests & Chaos):** In terminal, run `make test` (showing 33/33 tests passing in <1s) and `make chaos` (demonstrating T1, T2, T3, T5, T12). Conclude.

---

## 🎤 20-Minute Live Walkthrough Defense Script (For Anshu)

When presenting live to senior engineering evaluators, structure your defense across these 4 acts:

### Act 1: The Core Invariant (Minutes 0–4)
> *"I designed this system around a foundational rule: facts and numbers come from deterministic tools with timestamps; the model is only an explainer and proposer; code decides what is allowed. The LLM has zero execution privileges and is never asked to sum balances or parse dates."*
- Point to: [`app/tools/envelope.py`](app/tools/envelope.py) and [`app/engine/nodes.py`](app/engine/nodes.py).

### Act 2: Distributed Durability & Idempotency (Minutes 4–10)
> *"We address Traps T2 and T3 using deterministic action idempotency keys: `sha256(incident_id + action_type + target_id)`. If a worker process is killed (`kill -9`) right after dispatching an external refund HTTP call but before writing status to PostgreSQL, on revival it reconciles with the target platform's outbox. It detects the completed action and marks local state done without firing a second refund. Our Duplicate Action Rate is strictly 0.0%."*
- Point to: [`app/engine/executor.py`](app/engine/executor.py) and [`tests/test_part8_exactly_once_execution.py`](tests/test_part8_exactly_once_execution.py).

### Act 3: Staleness Protocol & Time Travel (Minutes 10–15)
> *"Data has a clock. Acting on a snapshot that was valid 10 minutes ago but invalid now is an operational disaster. Every tool envelope carries a TTL. Furthermore, approvals pause for days without holding worker threads. When approval arrives, the Pre-Execution Revalidation Guard freshly checks provider status. If the provider recovered, it rejects approval with HTTP 409."*
- Demonstrate: Top navbar `+2 Days` button and [`app/engine/staleness.py`](app/engine/staleness.py).

### Act 4: Scope Decisions & Benchmark Results (Minutes 15–20)
> *"I deliberately avoided heavy opaque frameworks like Temporal or Celery in favor of an explicit, auditable relational state machine with row-level leases. We evaluated both agents across 60 ground-truth incidents: our Copilot achieved 100% policy enforcement and 0.0% duplicate actions, while the naive baseline failed over 26% of idempotency checks."*
- Point to: [`ARCHITECTURE.md`](ARCHITECTURE.md) Section 7 and [`EVAL_RESULTS.md`](EVAL_RESULTS.md).
