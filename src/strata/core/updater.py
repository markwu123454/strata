"""
Autoupdate via GitHub Releases.

Strategy: poll the GitHub releases API, compare semver, download the .msi asset
for the latest release, then launch it. The MSI installer will replace the
existing install in place — no Python-level patching, no signing dance beyond
what Briefcase already does.

We deliberately do NOT auto-apply updates. The flow is:
  1. Background check on startup + every N hours
  2. If newer version exists, fire on_update_available(version, url, notes)
  3. UI shows a notification; user clicks "Download & Install"
  4. download_and_launch() fetches the MSI, runs it, then quits the app
     so the installer can replace the bundle

Version comparison uses tuple-of-ints, which handles 1.2.3-style tags fine.
Pre-release suffixes (-rc1, -beta) are not matched as updates — keeps users
on stable.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

GITHUB_REPO = "markwu123454/strata"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "Strata-Updater"
CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # every 6 hours

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class UpdateInfo:
    version: str          # e.g. "1.2.3" (no leading v)
    download_url: str     # direct asset URL for the .msi
    notes: str            # release body, possibly empty
    asset_name: str       # filename, e.g. "Strata-1.2.3.msi"


def _parse_version(s: str) -> Optional[tuple[int, int, int]]:
    m = VERSION_RE.match(s.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _fetch_latest_release() -> Optional[dict]:
    req = urllib.request.Request(
        RELEASES_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def _pick_msi_asset(release: dict) -> Optional[tuple[str, str]]:
    """Return (asset_name, download_url) for the .msi asset, if present."""
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".msi"):
            return name, asset.get("browser_download_url", "")
    return None


def check_for_update(current_version: str) -> Optional[UpdateInfo]:
    """
    Synchronous check. Returns UpdateInfo if a newer stable release exists with
    a downloadable MSI asset, else None. Network errors → None (silent).
    """
    current = _parse_version(current_version)
    if current is None:
        return None

    release = _fetch_latest_release()
    if release is None or release.get("draft") or release.get("prerelease"):
        return None

    tag = release.get("tag_name", "")
    latest = _parse_version(tag)
    if latest is None or latest <= current:
        return None

    asset = _pick_msi_asset(release)
    if asset is None:
        return None

    name, url = asset
    if not url:
        return None

    return UpdateInfo(
        version=".".join(str(p) for p in latest),
        download_url=url,
        notes=release.get("body") or "",
        asset_name=name,
    )


def download_installer(
    info: UpdateInfo,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Optional[Path]:
    """
    Download the MSI to a temp file. Returns the path on success, None on
    failure. on_progress receives (bytes_downloaded, total_bytes); total may
    be 0 if the server doesn't send Content-Length.
    """
    req = urllib.request.Request(info.download_url, headers={"User-Agent": USER_AGENT})
    tmp_dir = Path(tempfile.gettempdir()) / "strata-update"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / info.asset_name

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 64 * 1024
            with open(target, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    downloaded += len(buf)
                    if on_progress:
                        on_progress(downloaded, total)
        return target
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def launch_installer_and_quit(installer_path: Path, quit_callback: Callable[[], None]) -> bool:
    """
    Spawn the MSI installer detached from this process, then call quit_callback
    so the running app exits — the MSI cannot replace files the running app
    has open. Returns True if launch succeeded.

    We use msiexec /i with /passive so the user sees progress but doesn't have
    to click through. Adjust if you want full silent install.
    """
    if not installer_path.exists():
        return False

    try:
        if sys.platform == "win32":
            # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                ["msiexec", "/i", str(installer_path), "/passive", "/norestart"],
                creationflags=0x00000008 | 0x00000200,
                close_fds=True,
            )
        else:
            # Non-Windows: just open the file and let the OS handle it.
            # Mostly here so dev testing on macOS doesn't crash.
            subprocess.Popen(["open", str(installer_path)], close_fds=True)
    except OSError:
        return False

    # Give the installer a moment to spawn before we tear down the app
    threading.Timer(0.5, quit_callback).start()
    return True


# ── Background poller ─────────────────────────────────────────────────────────


class UpdateChecker:
    """
    Runs check_for_update() on a loop in a background thread.

    on_update_available is called from the worker thread — the UI layer is
    responsible for marshalling back to the main thread (use root.after).
    """

    def __init__(
        self,
        current_version: str,
        on_update_available: Callable[[UpdateInfo], None],
        interval_seconds: int = CHECK_INTERVAL_SECONDS,
    ):
        self.current_version = current_version
        self.on_update_available = on_update_available
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_notified_version: Optional[str] = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def check_now(self):
        """Force an immediate check on a one-shot thread (non-blocking)."""
        threading.Thread(target=self._do_check, daemon=True).start()

    def _loop(self):
        # First check after a short delay so we don't slow startup
        if self._stop.wait(30):
            return
        while not self._stop.is_set():
            self._do_check()
            if self._stop.wait(self.interval):
                return

    def _do_check(self):
        info = check_for_update(self.current_version)
        if info is None:
            return
        # Don't re-notify for the same version on every poll
        if info.version == self._last_notified_version:
            return
        self._last_notified_version = info.version
        try:
            self.on_update_available(info)
        except Exception:
            # Never let UI bugs kill the poller
            pass
