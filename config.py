"""
Config: loads and saves user configuration.
Stored at ~/.dirsync/config.json
"""

import json
import uuid
import socket
from pathlib import Path

CONFIG_DIR = Path.home() / ".dirsync"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DIR = CONFIG_DIR / "state"


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())

    # First run: generate a device ID
    default = {
        "device_id": str(uuid.uuid4()),
        "device_name": socket.gethostname(),
        "r2_account_id": "",
        "r2_access_key": "",
        "r2_secret_key": "",
        "r2_bucket": "",
        "sync_dir": str(Path.home() / "DirSync"),
    }
    CONFIG_FILE.write_text(json.dumps(default, indent=2))
    return default


def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def is_configured(config: dict) -> bool:
    required = ["r2_account_id", "r2_access_key", "r2_secret_key", "r2_bucket", "sync_dir"]
    return all(config.get(k) for k in required)
