"""
Config: loads and saves user configuration.
Stored at ~/.strata/config.json (migrated from ~/.dirsync/ if present).
"""
import json
import shutil
import uuid
import socket
from pathlib import Path

# Bumped on each release. Read by the updater to compare against GitHub.
# Keep in sync with pyproject.toml's `version`.
APP_VERSION = "0.1.0"

CONFIG_DIR = Path.home() / ".strata"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DIR = CONFIG_DIR / "state"

# Legacy location from when the project was named DirSync. We migrate on
# first launch so existing users don't lose their R2 credentials and have
# to reconfigure from scratch. Can be removed in v1.0+ once nobody is on
# the old name anymore.
LEGACY_CONFIG_DIR = Path.home() / ".dirsync"

DEFAULTS = {
    "device_id": "",  # filled per-install below
    "device_name": "",
    "r2_account_id": "",
    "r2_access_key": "",
    "r2_secret_key": "",
    "r2_bucket": "",
    "sync_dir": "",
    # New preferences (added in v0.1.0)
    "autostart_enabled": False,
    "check_for_updates": True,
}


def _migrate_from_legacy():
    """
    One-time move of ~/.dirsync to ~/.strata when upgrading from the old name.

    We use shutil.move rather than copy+delete: keeps file timestamps, and
    means a partial migration on a power-loss won't leave two divergent config
    dirs. If both exist (somehow), we leave .strata alone — the user's newer
    config wins.
    """
    if CONFIG_DIR.exists() or not LEGACY_CONFIG_DIR.exists():
        return
    try:
        shutil.move(str(LEGACY_CONFIG_DIR), str(CONFIG_DIR))
    except OSError:
        # Migration failure is non-fatal — user just gets a fresh config and
        # has to re-enter R2 credentials. Worse than nothing, but the app
        # still runs.
        pass


def load_config() -> dict:
    _migrate_from_legacy()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        # Backfill any missing keys for users upgrading from older versions —
        # avoids KeyError when new prefs are added.
        changed = False
        for k, v in DEFAULTS.items():
            if k not in cfg:
                cfg[k] = v
                changed = True
        if changed:
            save_config(cfg)
        return cfg

    # First run
    cfg = dict(DEFAULTS)
    cfg["device_id"] = str(uuid.uuid4())
    cfg["device_name"] = socket.gethostname()
    cfg["sync_dir"] = str(Path.home() / "Strata")
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return cfg


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def is_configured(config: dict) -> bool:
    required = ["r2_account_id", "r2_access_key", "r2_secret_key", "r2_bucket", "sync_dir"]
    return all(config.get(k) for k in required)
