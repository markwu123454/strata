"""
Layer 2: Manifest and hashing
Handles local state tracking and file hashing.
The manifest is the source of truth for what was synced last session.
"""

import hashlib
import json
import time
from pathlib import Path


MANIFEST_FILENAME = "last_session_manifest.json"
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for hashing large files


def hash_file(path: Path) -> str:
    """SHA-256 hash a file efficiently in chunks."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            sha256.update(chunk)
    return sha256.hexdigest()


def hash_directory(directory: Path, ignore: set[str] = None) -> dict[str, str]:
    """
    Hash all files in a directory recursively.
    Returns dict of { relative_path_str: hash }
    """
    ignore = ignore or set()
    hashes = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()  # always forward slashes, cross-platform safe
        if rel in ignore or any(rel.startswith(i) for i in ignore):
            continue
        try:
            hashes[rel] = hash_file(path)
        except (PermissionError, OSError):
            # File locked or unreadable, skip
            pass
    return hashes


class Manifest:
    """
    Tracks the state of the sync directory at the end of the last session.
    Stored locally — one per device.
    """

    def __init__(self, state_dir: Path):
        self.path = state_dir / MANIFEST_FILENAME
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                return {}
        return {}

    def save(self, file_hashes: dict[str, str], device_id: str):
        """Save a new manifest after a successful session end."""
        self._data = {
            "device_id": device_id,
            "session_end": time.time(),
            "files": file_hashes,
        }
        # Atomic write: write to temp then replace
        # Use replace() not rename() — on Windows, rename() fails if target exists
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self.path)

    def get_file_hash(self, relative_path: str) -> str | None:
        """Get the hash of a file from the last session. None if not in manifest."""
        return self._data.get("files", {}).get(relative_path)

    def get_all_hashes(self) -> dict[str, str]:
        return self._data.get("files", {})

    def exists(self) -> bool:
        """True if a manifest from a previous session exists."""
        return bool(self._data)

    def session_end_time(self) -> float | None:
        return self._data.get("session_end")


def find_out_of_session_changes(
    directory: Path,
    manifest: Manifest,
    ignore: set[str] = None,
) -> list[dict]:
    """
    Compare current local files against last session manifest.
    Returns list of changed files with their status.

    Each entry: { path, status }
    status: "modified" | "added" | "deleted"
    """
    current_hashes = hash_directory(directory, ignore)
    last_hashes = manifest.get_all_hashes()
    changes = []

    # Files that exist now
    for rel_path, current_hash in current_hashes.items():
        last_hash = last_hashes.get(rel_path)
        if last_hash is None:
            changes.append({"path": rel_path, "status": "added"})
        elif last_hash != current_hash:
            changes.append({"path": rel_path, "status": "modified"})

    # Files that were deleted locally since last session
    for rel_path in last_hashes:
        if rel_path not in current_hashes:
            changes.append({"path": rel_path, "status": "deleted"})

    return changes


def find_changed_files(
    directory: Path,
    manifest: Manifest,
    ignore: set[str] = None,
) -> list[str]:
    """
    Returns list of relative paths that changed since last session.
    Used during End Session to know what to upload.
    """
    current_hashes = hash_directory(directory, ignore)
    last_hashes = manifest.get_all_hashes()
    changed = []

    for rel_path, current_hash in current_hashes.items():
        if last_hashes.get(rel_path) != current_hash:
            changed.append(rel_path)

    # Deleted files
    for rel_path in last_hashes:
        if rel_path not in current_hashes:
            changed.append(rel_path)  # Will be handled as deletion in sync engine

    return changed
