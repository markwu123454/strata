"""
Run-on-startup support (Windows only).

Uses HKCU\Software\Microsoft\Windows\CurrentVersion\Run which does NOT require
admin rights. Per-user only. Silent failure on non-Windows platforms — the
settings UI hides the option there anyway, but we keep the API stable so the
rest of the app doesn't need to branch.

The registry value points to the executable Briefcase installs. When running
in dev (python main.py) we point at sys.executable + the script path so it
still works for testing.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Strata"
LEGACY_APP_NAME = "DirSync"  # remove after a few releases
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _executable_command() -> str:
    """
    Build the command string to register.

    Frozen Briefcase build: sys.executable points at Strata.exe — use it as-is.
    Dev mode: wrap python + main.py and quote both halves so paths with spaces
    survive the registry round-trip.
    """
    exe = Path(sys.executable)

    # Briefcase/PyInstaller-style frozen apps: sys.frozen is set, or the
    # executable is named after the app rather than python.exe
    frozen = getattr(sys, "frozen", False) or exe.stem.lower() not in ("python", "pythonw")

    if frozen:
        return f'"{exe}"'

    # Dev mode — find main.py relative to this file
    # src/strata/core/autostart.py -> repo root depends on layout, so search up
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "main.py"
        if candidate.exists():
            return f'"{exe}" "{candidate}"'
        # Also check for the briefcase entry point
        candidate = parent / "src" / "strata" / "__main__.py"
        if candidate.exists():
            return f'"{exe}" -m strata'
    # Fallback — shouldn't happen but don't crash
    return f'"{exe}"'


def is_enabled() -> bool:
    """Return True if autostart is currently registered."""
    if not _is_windows():
        return False
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> bool:
    """Register app to run on user login. Returns True on success."""
    if not _is_windows():
        return False
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    try:
        cmd = _executable_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        # If the user previously enabled autostart under the old "DirSync"
        # name, clean it up — otherwise they'd get TWO copies launched on
        # login (the stale one would either fail silently if the old install
        # is gone, or worse, run an outdated build alongside the new one).
        _cleanup_legacy()
        return True
    except OSError:
        return False


def _cleanup_legacy():
    """Remove autostart entries from any previous app names. Best-effort."""
    if not _is_windows():
        return
    try:
        import winreg  # type: ignore
    except ImportError:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, LEGACY_APP_NAME)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def disable() -> bool:
    """Remove autostart entry. Returns True on success or if already absent."""
    if not _is_windows():
        return False
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def is_supported() -> bool:
    """Whether the current platform supports autostart at all."""
    return _is_windows()
