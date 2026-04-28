"""
Config: loads and saves user configuration.
Stored at ~/.strata/config.json (migrated from ~/.dirsync/ if present).

Schema (v1.1+):
    {
      "device_id": "...",          # global, one per install
      "device_name": "...",        # global
      "autostart_enabled": false,  # global
      "check_for_updates": true,   # global
      "active_profile": "default", # name of currently-selected profile
      "profiles": [
        {
          "name": "default",
          "r2_account_id": "...",
          "r2_access_key": "...",
          "r2_secret_key": "...",
          "r2_bucket": "...",
          "sync_dir": "..."
        },
        ...
      ]
    }

Pre-1.1 the R2/sync keys lived at the top level (single bucket only). We
auto-migrate on load — see _migrate_to_profiles.
"""
import json
import re
import shutil
import uuid
import socket
from pathlib import Path

# Bumped on each release. Read by the updater to compare against GitHub.
# Keep in sync with pyproject.toml's `version`.
APP_VERSION = "1.2.0"

CONFIG_DIR = Path.home() / ".strata"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DIR = CONFIG_DIR / "state"

# Legacy location from when the project was named DirSync. We migrate on
# first launch so existing users don't lose their R2 credentials and have
# to reconfigure from scratch. Can be removed in v1.0+ once nobody is on
# the old name anymore.
LEGACY_CONFIG_DIR = Path.home() / ".dirsync"

# Per-profile required fields. Used by is_configured to decide whether a
# profile is usable (engine can be built from it).
PROFILE_REQUIRED = ("r2_account_id", "r2_access_key", "r2_secret_key", "r2_bucket", "sync_dir")

# Per-profile keys that the settings UI round-trips. Anything not in here
# stays untouched on save.
PROFILE_KEYS = ("name",) + PROFILE_REQUIRED

# Device-level (global) defaults.
GLOBAL_DEFAULTS = {
    "device_id": "",
    "device_name": "",
    "autostart_enabled": False,
    "check_for_updates": True,
    "active_profile": "default",
    "profiles": [],
}

# Filesystem-safe profile name: lowercase letters, digits, underscore, hyphen.
# Profile names map to subdirectories under STATE_DIR for per-profile manifests,
# so anything that would explode on Windows or include path separators is out.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_valid_profile_name(name: str) -> bool:
    return bool(name) and bool(_NAME_RE.match(name))


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


def _migrate_to_profiles(cfg: dict) -> tuple[dict, bool]:
    """
    Migrate a pre-1.1 flat config into the profiles schema.

    Old layout had r2_*/sync_dir at the top level. We wrap those into a
    single profile named "default" and remove them from the top level.
    Returns (cfg, changed).

    Idempotent: if "profiles" already exists, this is a no-op.
    """
    if "profiles" in cfg and isinstance(cfg["profiles"], list):
        return cfg, False

    # Pull the old flat fields out, if present. Missing fields just become
    # empty strings — same as a fresh profile waiting to be filled in.
    old_profile = {"name": "default"}
    for k in PROFILE_REQUIRED:
        old_profile[k] = cfg.pop(k, "")

    cfg["profiles"] = [old_profile]
    cfg.setdefault("active_profile", "default")
    return cfg, True


def load_config() -> dict:
    _migrate_from_legacy()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())

        # Run schema migration before backfilling globals — the migration
        # may create the "profiles" key, and we don't want to backfill
        # GLOBAL_DEFAULTS["profiles"] (an empty list) over a populated one.
        cfg, migrated = _migrate_to_profiles(cfg)

        # Backfill any missing global keys for users upgrading from older
        # versions — avoids KeyError when new prefs are added.
        changed = migrated
        for k, v in GLOBAL_DEFAULTS.items():
            if k not in cfg:
                cfg[k] = v
                changed = True

        # Make sure active_profile points to something real. If the user
        # deleted the active profile by hand-editing the file, fall back
        # to the first one (or empty string if none exist).
        names = [p.get("name", "") for p in cfg.get("profiles", [])]
        if cfg.get("active_profile") not in names:
            cfg["active_profile"] = names[0] if names else ""
            changed = True

        if changed:
            save_config(cfg)
        return cfg

    # First run — fresh config with one empty default profile.
    cfg = dict(GLOBAL_DEFAULTS)
    cfg["device_id"] = str(uuid.uuid4())
    cfg["device_name"] = socket.gethostname()
    cfg["profiles"] = [{
        "name": "default",
        "r2_account_id": "",
        "r2_access_key": "",
        "r2_secret_key": "",
        "r2_bucket": "",
        "sync_dir": str(Path.home() / "Strata"),
    }]
    cfg["active_profile"] = "default"
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return cfg


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_profile(config: dict, name: str) -> dict | None:
    """Return the profile dict with the given name, or None."""
    for p in config.get("profiles", []):
        if p.get("name") == name:
            return p
    return None


def get_active_profile(config: dict) -> dict | None:
    """Return the currently-active profile dict, or None if there are no
    profiles or the active_profile pointer is stale."""
    return get_profile(config, config.get("active_profile", ""))


def is_profile_configured(profile: dict | None) -> bool:
    """A profile is 'configured' when all R2 fields and a sync_dir are set."""
    if not profile:
        return False
    return all(profile.get(k) for k in PROFILE_REQUIRED)


def is_configured(config: dict) -> bool:
    """Backwards-compatible: the *active* profile is fully configured.

    Kept for code paths that still ask the global question (e.g. enabling
    the Start Session menu item). For multi-profile-aware checks, use
    is_profile_configured directly.
    """
    return is_profile_configured(get_active_profile(config))


def profile_state_dir(profile_name: str) -> Path:
    """Where this profile's last_session_manifest lives.

    Each profile gets its own subdirectory under STATE_DIR so manifests
    don't collide. Profile names are validated via is_valid_profile_name
    before we ever build a path from one.
    """
    if not is_valid_profile_name(profile_name):
        # Defensive: should never happen if settings UI validates, but if
        # it does we'd rather error loudly than silently write to STATE_DIR
        # itself and stomp another profile.
        raise ValueError(f"Invalid profile name: {profile_name!r}")
    d = STATE_DIR / profile_name
    d.mkdir(parents=True, exist_ok=True)
    return d
