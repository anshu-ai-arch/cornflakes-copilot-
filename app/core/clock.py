"""
Virtual Injectable Clock Service.
Allows instantaneous time-travel (e.g. fast-forwarding 2 days in seconds)
so that staleness and expiration test scenarios (like T4, T6) run deterministically.
"""

from datetime import datetime, timezone, timedelta
import threading
from typing import Optional


class ClockService:
    _instance: Optional["ClockService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._offset_seconds: float = 0.0

    @classmethod
    def get_instance(cls) -> "ClockService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def now(self) -> datetime:
        """Returns the current virtual UTC time."""
        return datetime.now(timezone.utc) + timedelta(seconds=self._offset_seconds)

    def advance(self, seconds: float) -> datetime:
        """Advance the virtual clock by the given number of seconds."""
        with self._lock:
            self._offset_seconds += float(seconds)
            return self.now()

    def reset(self) -> datetime:
        """Reset the virtual clock back to real system time."""
        with self._lock:
            self._offset_seconds = 0.0
            return self.now()

    def get_offset(self) -> float:
        """Returns the current clock offset in seconds."""
        return self._offset_seconds

    def now_iso(self) -> str:
        """Returns the virtual time as an ISO-8601 formatted string."""
        return self.now().isoformat()


# Global convenient singleton accessor
Clock = ClockService.get_instance()
