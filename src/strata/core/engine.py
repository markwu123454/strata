"""
Layer 4: Sync engine
Orchestrates Start Session, End Session, and Quick Pull.
This is the core of the application.
"""

import time
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from strata.core.r2 import R2Client, R2Error
from strata.core.manifest import Manifest, hash_file, hash_directory, find_out_of_session_changes, find_changed_files
from strata.core.lock import LockManager, LockInfo


# Files/dirs to never sync
IGNORE = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"}
IGNORE_PREFIXES = (".", "_sync/")

REMOTE_MANIFEST_KEY = "_sync/manifest.json"


class SyncStatus(Enum):
    IDLE = "idle"
    STARTING = "starting"
    SYNCING = "syncing"
    ENDING = "ending"
    ERROR = "error"


@dataclass
class OutOfSessionChange:
    path: str
    status: str  # "modified" | "added" | "deleted"


@dataclass
class StartSessionResult:
    success: bool
    lock_info: LockInfo | None = None
    out_of_session_changes: list[OutOfSessionChange] = field(default_factory=list)
    error: str | None = None


@dataclass
class EndSessionResult:
    success: bool
    files_uploaded: int = 0
    files_deleted: int = 0
    error: str | None = None


@dataclass
class QuickPullResult:
    """Result of a Quick Pull (no-session download).

    `lock_info` is informational only — Quick Pull does not honor or modify
    the lock. If another device is mid-session, the caller may want to surface
    that but Quick Pull itself proceeds either way.
    """
    success: bool
    lock_info: LockInfo | None = None
    error: str | None = None


class SyncEngine:
    def __init__(
        self,
        sync_dir: Path,
        state_dir: Path,
        r2: R2Client,
        device_id: str,
        device_name: str,
        on_status_change=None,  # callback(SyncStatus, message: str)
        on_progress=None,       # callback(current: int, total: int, filename: str)
    ):
        self.sync_dir = sync_dir
        self.state_dir = state_dir
        self.r2 = r2
        self.device_id = device_id
        self.device_name = device_name
        self.on_status_change = on_status_change or (lambda s, m: None)
        self.on_progress = on_progress or (lambda c, t, f: None)

        self.manifest = Manifest(state_dir)
        self.lock_manager = LockManager(r2, device_id, device_name)
        self.status = SyncStatus.IDLE

    def _set_status(self, status: SyncStatus, message: str = ""):
        self.status = status
        self.on_status_change(status, message)

    def _set_error(self, message: str):
        """Set ERROR status and surface the message. Centralised so every
        error path shows the same visual state."""
        self._set_status(SyncStatus.ERROR, message)

    def _should_ignore(self, rel_path: str) -> bool:
        filename = Path(rel_path).name
        if filename in IGNORE:
            return True
        for prefix in IGNORE_PREFIXES:
            if rel_path.startswith(prefix):
                return True
        return False

    def check_out_of_session_changes(self) -> list[OutOfSessionChange]:
        """Check for local changes made outside a session."""
        if not self.manifest.exists():
            return []
        raw = find_out_of_session_changes(self.sync_dir, self.manifest, IGNORE)
        return [OutOfSessionChange(**c) for c in raw]

    def peek_lock(self) -> LockInfo | None:
        """Read the current lock without trying to acquire it.

        Returns None if there's no lock OR if the read fails — a failed
        read here isn't worth aborting a Quick Pull over, so we treat it
        as "don't know" and proceed.
        """
        try:
            return self.lock_manager.get_current_lock()
        except R2Error:
            return None

    def start_session(self, discard_local_changes: bool = False) -> StartSessionResult:
        """
        Start a session:
        0. Connectivity check (fast-fail before acquiring lock)
        1. Check for out-of-session local changes
        2. Acquire lock
        3. Pull all files from R2
        """
        # Step 0: Connectivity check — do this before anything else so the
        # user gets a clear error immediately on a restricted network instead
        # of waiting for the lock acquire to time out and getting a
        # cryptic boto3 message (or no message at all).
        self._set_status(SyncStatus.STARTING, "Checking connection to R2...")
        try:
            self.r2.check_connectivity()
        except R2Error as e:
            self._set_error(str(e))
            return StartSessionResult(success=False, error=str(e))

        # Step 1: Out-of-session changes
        self._set_status(SyncStatus.STARTING, "Checking local state...")
        changes = self.check_out_of_session_changes()
        if changes and not discard_local_changes:
            self._set_status(SyncStatus.IDLE, "Out-of-session changes detected")
            return StartSessionResult(
                success=False,
                out_of_session_changes=changes,
            )

        # Step 2: Acquire lock
        self._set_status(SyncStatus.STARTING, "Acquiring session lock...")
        try:
            acquired, existing_lock = self.lock_manager.acquire()
        except R2Error as e:
            self._set_error(str(e))
            return StartSessionResult(success=False, error=str(e))

        if not acquired:
            self._set_status(SyncStatus.IDLE, "Session locked by another device")
            return StartSessionResult(success=False, lock_info=existing_lock)

        # Step 3: Pull everything from R2
        self._set_status(SyncStatus.SYNCING, "Pulling files from R2...")
        try:
            self._pull_all()
        except R2Error as e:
            # R2 problem mid-pull — release the lock so another device
            # isn't blocked, then surface the error.
            try:
                self.lock_manager.release()
            except Exception:
                pass
            self._set_error(str(e))
            return StartSessionResult(success=False, error=str(e))
        except Exception as e:
            try:
                self.lock_manager.release()
            except Exception:
                pass
            msg = f"Unexpected error during pull: {type(e).__name__}: {e}"
            self._set_error(msg)
            return StartSessionResult(success=False, error=msg)

        current_hashes = hash_directory(self.sync_dir, IGNORE)
        self.manifest.save(current_hashes, self.device_id)

        self._set_status(SyncStatus.IDLE, "Session started")
        return StartSessionResult(success=True)

    def end_session(self) -> EndSessionResult:
        """
        End a session:
        1. Upload changed files
        2. Save manifest
        3. Release lock
        """
        if not self.lock_manager.is_locked_by_me():
            return EndSessionResult(success=False, error="No active session on this device")

        self._set_status(SyncStatus.ENDING, "Computing changes...")

        try:
            uploaded, deleted = self._push_changes()
        except R2Error as e:
            self._set_error(str(e))
            return EndSessionResult(success=False, error=str(e))
        except Exception as e:
            msg = f"Unexpected error during upload: {type(e).__name__}: {e}"
            self._set_error(msg)
            return EndSessionResult(success=False, error=msg)

        self._set_status(SyncStatus.ENDING, "Saving state...")
        current_hashes = hash_directory(self.sync_dir, IGNORE)
        self.manifest.save(current_hashes, self.device_id)

        self.lock_manager.release()
        self._set_status(SyncStatus.IDLE, f"Session ended — {uploaded} uploaded, {deleted} deleted")

        return EndSessionResult(success=True, files_uploaded=uploaded, files_deleted=deleted)

    def force_take_session(self) -> StartSessionResult:
        """Force-break the lock and start a session. Requires user confirmation before calling."""
        try:
            self.lock_manager.force_release()
        except R2Error as e:
            self._set_error(str(e))
            return StartSessionResult(success=False, error=str(e))
        return self.start_session()

    def quick_pull(self) -> QuickPullResult:
        """Pull remote state into sync_dir without acquiring a lock.

        Does NOT save the local manifest — the manifest must keep reflecting
        the last actual session-end on this device. If we bumped it here,
        local edits the user made before quick-pulling would silently
        disappear from the next start_session's out-of-session-changes
        detection.
        """
        # Connectivity check first — same reason as start_session.
        self._set_status(SyncStatus.SYNCING, "Checking connection to R2...")
        try:
            self.r2.check_connectivity()
        except R2Error as e:
            self._set_error(str(e))
            return QuickPullResult(success=False, error=str(e))

        existing_lock = self.peek_lock()

        self._set_status(SyncStatus.SYNCING, "Quick pull...")
        try:
            self._pull_all()
        except R2Error as e:
            self._set_error(str(e))
            return QuickPullResult(success=False, error=str(e), lock_info=existing_lock)
        except Exception as e:
            msg = f"Unexpected error during pull: {type(e).__name__}: {e}"
            self._set_error(msg)
            return QuickPullResult(success=False, error=msg, lock_info=existing_lock)

        self._set_status(SyncStatus.IDLE, "Quick pull complete")
        return QuickPullResult(success=True, lock_info=existing_lock)

    def _pull_all(self):
        """Download all files from R2, overwriting local versions.

        Raises R2Error if the remote manifest or file listing can't be
        fetched. Individual file download failures are logged but don't
        abort the pull — a partial pull is better than no pull when only
        one file is flaky.
        """
        remote_manifest = self.r2.get_json(REMOTE_MANIFEST_KEY) or {}
        remote_hashes = remote_manifest.get("files", {})

        if remote_hashes:
            keys_to_pull = [k for k in remote_hashes if not self._should_ignore(k)]
        else:
            keys_to_pull = [k for k in self.r2.list_files() if not self._should_ignore(k)]

        def needs_download(key):
            local_path = self.sync_dir / Path(key)
            if not local_path.exists():
                return True
            expected = remote_hashes.get(key)
            if not expected:
                return True
            return hash_file(local_path) != expected

        keys_to_download = [k for k in keys_to_pull if needs_download(k)]
        skipped = len(keys_to_pull) - len(keys_to_download)
        if skipped:
            print(f"[Sync] {skipped} files already up to date, skipping")

        total = len(keys_to_download)
        if total == 0:
            self._set_status(SyncStatus.SYNCING, "All files already up to date")
            return

        for i, key in enumerate(keys_to_download):
            self.on_progress(i + 1, total, key)
            local_path = self.sync_dir / Path(key)
            tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")

            ok = self.r2.download_file(key, tmp_path)
            if not ok:
                tmp_path.unlink(missing_ok=True)
                continue

            expected_hash = remote_hashes.get(key)
            if expected_hash:
                actual_hash = hash_file(tmp_path)
                if actual_hash != expected_hash:
                    print(f"[Sync] Checksum mismatch for {key}, skipping")
                    tmp_path.unlink(missing_ok=True)
                    continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.replace(local_path)

        if remote_hashes:
            for local_file in self.sync_dir.rglob("*"):
                if not local_file.is_file():
                    continue
                rel = local_file.relative_to(self.sync_dir).as_posix()
                if self._should_ignore(rel):
                    continue
                if rel not in remote_hashes:
                    local_file.unlink()
                    print(f"[Sync] Removed local file not in R2: {rel}")

    def _push_changes(self) -> tuple[int, int]:
        """Upload changed files to R2. Returns (uploaded_count, deleted_count).
        Raises R2Error or RuntimeError on failure.
        """
        current_hashes = hash_directory(self.sync_dir, IGNORE)
        last_hashes = self.manifest.get_all_hashes()

        to_upload = []
        to_delete = []

        for rel_path, current_hash in current_hashes.items():
            if self._should_ignore(rel_path):
                continue
            if last_hashes.get(rel_path) != current_hash:
                to_upload.append(rel_path)

        for rel_path in last_hashes:
            if rel_path not in current_hashes:
                to_delete.append(rel_path)

        remote_manifest = self.r2.get_json(REMOTE_MANIFEST_KEY) or {}
        for rel_path in remote_manifest.get("files", {}):
            if rel_path not in current_hashes and rel_path not in to_delete:
                to_delete.append(rel_path)

        total = len(to_upload) + len(to_delete)

        for i, rel_path in enumerate(to_upload):
            self.on_progress(i + 1, total, rel_path)
            local_path = self.sync_dir / rel_path
            if not local_path.exists():
                continue
            ok = self.r2.upload_file(local_path, rel_path)
            if not ok:
                raise RuntimeError(f"Failed to upload {rel_path}")

        for i, rel_path in enumerate(to_delete):
            self.on_progress(len(to_upload) + i + 1, total, rel_path)
            self.r2.delete_file(rel_path)

        # put_json now raises R2Error on failure, so this will propagate
        # cleanly up to end_session's error handler.
        self.r2.put_json(REMOTE_MANIFEST_KEY, {
            "files": current_hashes,
            "updated_at": time.time(),
            "updated_by": self.device_id,
        })

        return len(to_upload), len(to_delete)
