"""
Application Configuration and Environment Settings.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Dict, Any


class Settings(BaseSettings):
    PROJECT_NAME: str = "Cornflakes Generation Ops Copilot"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/v1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cornflakes_ops"
    # When testing locally without Postgres running, can fall back to sqlite+aiosqlite:///./cornflakes.db
    SQLITE_FALLBACK_URL: str = "sqlite+aiosqlite:///./cornflakes.db"

    # Worker
    WORKER_ID: str = "worker-1"
    WORKER_LEASE_SECONDS: int = 30
    WORKER_HEARTBEAT_SECONDS: int = 10

    # Mock Platform
    MOCK_PLATFORM_URL: str = "http://localhost:8001"

    # LLM Settings
    MOCK_LLM_ENABLED: bool = True
    LLM_SEED: int = 42

    # Policy Defaults (in cents)
    MAX_REFUND_PER_INCIDENT_CENTS: int = 10000  # $100.00 max
    MAX_REFUND_PER_DAY_CENTS: int = 50000       # $500.00 max
    AUTO_APPROVE_COST_CAP_CENTS: int = 50       # $0.50 max for auto-approved shot retries

    # Tool TTL Limits (in seconds)
    TOOL_TTLS: Dict[str, int] = {
        "provider_status": 60,   # Provider status changes fast; 60s TTL
        "queue_depth": 30,       # Queue depth changes rapidly; 30s TTL
        "get_job": 120,          # Job state cached up to 2m
        "list_failed_shots": 120,
        "ledger_for_job": 300,
        "recent_logs": 60
    }

    # Approval Expiration TTL (e.g. 24 hours in virtual clock)
    APPROVAL_EXPIRY_SECONDS: int = 86400

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
