"""
Layer 3: Session lock
Manages the distributed lock stored in R2.
Only one device can hold the session at a time.
"""

import time
import secrets
from dataclasses import dataclass
from strata.core.r2 import R2Client


LOCK_KEY = "_sync/lock.json"


@dataclass
class LockInfo:
    device_id: str
    device_name: str
    acquired_at: float
    token: str  # Random token to detect race conditions

    def age_minutes(self) -> float:
        return (time.time() - self.acquired_at) / 60

    def acquired_at_str(self) -> str:
        import datetime
        dt = datetime.datetime.fromtimestamp(self.acquired_at)
        return dt.strftime("%b %d at %I:%M %p")


class LockManager:
    def __init__(self, r2: R2Client, device_id: str, device_name: str):
        self.r2 = r2
        self.device_id = device_id
        self.device_name = device_name
        self._token: str | None = None

    def get_current_lock(self) -> LockInfo | None:
        """Fetch current lock from R2. Returns None if unlocked."""
        data = self.r2.get_json(LOCK_KEY)
        if data is None:
            return None
        return LockInfo(
            device_id=data["device_id"],
            device_name=data["device_name"],
            acquired_at=data["acquired_at"],
            token=data["token"],
        )

    def is_locked_by_me(self) -> bool:
        """Check if we currently hold the lock."""
        if self._token is None:
            return False
        lock = self.get_current_lock()
        return lock is not None and lock.token == self._token

    def acquire(self) -> tuple[bool, LockInfo | None]:
        """
        Try to acquire the lock.
        Returns (success, existing_lock_if_failed).
        Uses read-write-verify to reduce (but not eliminate) race conditions.
        """
        existing = self.get_current_lock()
        if existing is not None:
            return False, existing

        # Write our lock
        token = secrets.token_hex(16)
        self.r2.put_json(LOCK_KEY, {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "acquired_at": time.time(),
            "token": token,
        })

        # Verify we won (handles near-simultaneous acquire)
        time.sleep(0.5)
        current = self.get_current_lock()
        if current and current.token == token:
            self._token = token
            return True, None
        else:
            # Someone else won the race
            return False, current

    def release(self) -> bool:
        """Release the lock. Only works if we hold it."""
        if not self.is_locked_by_me():
            return False
        self.r2.delete_file(LOCK_KEY)
        self._token = None
        return True

    def force_release(self) -> bool:
        """Force release regardless of who holds the lock. Use with confirmation."""
        self.r2.delete_file(LOCK_KEY)
        self._token = None
        return True
