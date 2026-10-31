"""
Deterministic Tool Envelope.
Ensures every piece of data returned by tools is strictly stamped with:
- as_of (virtual timestamp)
- source (system of record)
- ttl_seconds (staleness limit)
"""

from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime, timezone
from app.core.clock import Clock


class ToolResult(BaseModel):
    data: Any
    as_of: str = Field(default_factory=lambda: Clock.now_iso())
    source: str
    ttl_seconds: int
    is_stale: bool = False

    def age_seconds(self) -> float:
        """Returns age of this data relative to the current virtual clock."""
        try:
            # Parse ISO format
            as_of_dt = datetime.fromisoformat(self.as_of)
            current_dt = Clock.now()
            # Ensure both are UTC
            if as_of_dt.tzinfo is None:
                as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
            if current_dt.tzinfo is None:
                current_dt = current_dt.replace(tzinfo=timezone.utc)
            return max(0.0, (current_dt - as_of_dt).total_seconds())
        except Exception:
            return 0.0

    def check_is_stale(self) -> bool:
        """True if the data age exceeds its configured TTL."""
        age = self.age_seconds()
        self.is_stale = age > self.ttl_seconds
        return self.is_stale

    def plain_english_age(self) -> str:
        """Human-readable explanation of data freshness for the UI/LLM."""
        age = int(self.age_seconds())
        if age < 60:
            return f"{age} seconds old (Fresh)"
        elif age < 3600:
            return f"{age // 60} minutes old"
        elif age < 86400:
            return f"{age // 3600} hours old"
        else:
            days = age // 86400
            return f"{days} days old ({'STALE' if self.check_is_stale() else 'Cached'})"
