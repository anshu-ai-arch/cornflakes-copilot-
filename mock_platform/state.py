"""
Mock Platform In-Memory State & Fixtures.
Provides realistic state for Cornflakes Media video generation:
- Jobs & Shot tickets
- Provider balances & health (e.g. runway, pika, elevenlabs)
- Append-only billing ledger
- Action idempotency store (crucial for Trap T3 crash recovery)
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from app.core.clock import Clock


class MockPlatformState:
    _instance: Optional["MockPlatformState"] = None

    def __init__(self):
        self.reset()

    @classmethod
    def get_instance(cls) -> "MockPlatformState":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reset(self):
        """Reset mock platform state to baseline seed data."""
        now_iso = Clock.now_iso()

        # Providers: runway, pika, luma
        self.providers: Dict[str, Dict[str, Any]] = {
            "runway": {
                "provider_id": "runway",
                "name": "Runway Gen-3",
                "status": "HEALTHY",  # HEALTHY, OUT_OF_FUNDS, DEGRADED, DISABLED
                "balance_cents": 25000,
                "error_rate_pct": 0.0,
                "updated_at": now_iso
            },
            "pika": {
                "provider_id": "pika",
                "name": "Pika 2.0",
                "status": "HEALTHY",
                "balance_cents": 18000,
                "error_rate_pct": 0.0,
                "updated_at": now_iso
            },
            "luma": {
                "provider_id": "luma",
                "name": "Luma Dream Machine",
                "status": "HEALTHY",
                "balance_cents": 50000,
                "error_rate_pct": 0.0,
                "updated_at": now_iso
            }
        }

        # Jobs
        self.jobs: Dict[str, Dict[str, Any]] = {
            "job_101": {
                "job_id": "job_101",
                "tenant_id": "tenant_alpha",
                "title": "Cyberpunk Short Film",
                "status": "FAILED",  # QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED
                "stage": "video_generation",
                "provider": "runway",
                "attempts": 3,
                "max_attempts": 3,
                "worker_id": "worker_legacy_9",
                "error": "Provider 'runway' returned HTTP 402: Account balance insufficient (OUT_OF_FUNDS)",
                "actual_charge_cents": 450,
                "quoted_cost_cents": 450,
                "created_at": now_iso,
                "updated_at": now_iso
            },
            "job_102": {
                "job_id": "job_102",
                "tenant_id": "tenant_beta",
                "title": "Space Odyssey Scene 4",
                "status": "FAILED",
                "stage": "shot_rendering",
                "provider": "pika",
                "attempts": 3,
                "max_attempts": 3,
                "worker_id": "worker_legacy_3",
                "error": "Shot render failed 3 times: Frame glitch in shot_3",
                "actual_charge_cents": 600,
                "quoted_cost_cents": 600,
                "created_at": now_iso,
                "updated_at": now_iso
            },
            "job_103": {
                "job_id": "job_103",
                "tenant_id": "tenant_gamma",
                "title": "Documentary Intro",
                "status": "RUNNING",
                "stage": "upscaling",
                "provider": "luma",
                "attempts": 1,
                "max_attempts": 3,
                "worker_id": "worker_died_mid_job",
                "error": "Worker lease expired; heartbeat missed for 90s (WORKER_DIED)",
                "actual_charge_cents": 300,
                "quoted_cost_cents": 300,
                "created_at": now_iso,
                "updated_at": now_iso
            }
        }

        # Shots per job
        self.shots: Dict[str, List[Dict[str, Any]]] = {
            "job_101": [
                {"shot_id": "shot_101_1", "status": "SUCCEEDED", "cost_cents": 150, "attempts": 1, "error": None},
                {"shot_id": "shot_101_2", "status": "FAILED", "cost_cents": 150, "attempts": 3, "error": "Provider out of funds"},
                {"shot_id": "shot_101_3", "status": "PENDING", "cost_cents": 150, "attempts": 0, "error": None}
            ],
            "job_102": [
                {"shot_id": "shot_102_1", "status": "SUCCEEDED", "cost_cents": 200, "attempts": 1, "error": None},
                {"shot_id": "shot_102_2", "status": "SUCCEEDED", "cost_cents": 200, "attempts": 1, "error": None},
                {"shot_id": "shot_102_3", "status": "FAILED", "cost_cents": 200, "attempts": 3, "error": "Glitch in frame 45"}
            ],
            "job_103": [
                {"shot_id": "shot_103_1", "status": "RUNNING", "cost_cents": 300, "attempts": 1, "error": None}
            ]
        }

        # Ledger entries (Append-only)
        self.ledger: List[Dict[str, Any]] = [
            {
                "id": 1,
                "job_id": "job_101",
                "tenant_id": "tenant_alpha",
                "idempotency_key": "init_charge_job_101",
                "amount_cents": 450,
                "transaction_type": "CHARGE",
                "reason": "Initial generation charge",
                "created_at": now_iso
            },
            {
                "id": 2,
                "job_id": "job_102",
                "tenant_id": "tenant_beta",
                "idempotency_key": "init_charge_job_102",
                "amount_cents": 600,
                "transaction_type": "CHARGE",
                "reason": "Initial generation charge",
                "created_at": now_iso
            },
            {
                "id": 3,
                "job_id": "job_103",
                "tenant_id": "tenant_gamma",
                "idempotency_key": "init_charge_job_103",
                "amount_cents": 300,
                "transaction_type": "CHARGE",
                "reason": "Initial generation charge",
                "created_at": now_iso
            }
        ]

        # Logs
        self.logs: Dict[str, List[Dict[str, Any]]] = {
            "job_101": [
                {"timestamp": now_iso, "level": "INFO", "message": "Job 101 started on worker_legacy_9"},
                {"timestamp": now_iso, "level": "INFO", "message": "Shot 101_1 rendered successfully"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Runway API returned 402 Payment Required: credits exhausted"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Shot 101_2 failed after 3 attempts"}
            ],
            "job_102": [
                {"timestamp": now_iso, "level": "INFO", "message": "Job 102 started on worker_legacy_3"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Shot 102_3 failed attempt 1 (glitch)"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Shot 102_3 failed attempt 2 (glitch)"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Shot 102_3 failed attempt 3 (glitch, max retries reached)"}
            ],
            "job_103": [
                {"timestamp": now_iso, "level": "INFO", "message": "Job 103 processing on worker_died_mid_job"},
                {"timestamp": now_iso, "level": "WARN", "message": "Worker heartbeat missed at T+30s"},
                {"timestamp": now_iso, "level": "ERROR", "message": "Worker lease expired at T+90s; worker presumed dead"}
            ]
        }

        # Queue depth
        self.queue_info: Dict[str, Any] = {
            "depth": 14,
            "running_workers": 6,
            "avg_wait_seconds": 12.5,
            "updated_at": now_iso
        }

        # Action execution audit store (Keyed by Idempotency Key - For Trap T2 & T3)
        self.executed_actions: Dict[str, Dict[str, Any]] = {}


PlatformState = MockPlatformState.get_instance()
