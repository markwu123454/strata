# Strata

Sync a directory between laptops via Cloudflare R2. Session-based — only one
device works at a time, eliminating merge conflicts entirely.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

On first run, click Settings in the tray icon and fill in:
- Cloudflare R2 Account ID
- Bucket name (create it in the R2 dashboard first)
- R2 Access Key ID + Secret (create an API token with read/write access)
- Sync directory (folder to keep in sync)
- Device name (e.g. "MacBook Pro", "Work Laptop")

## How it works

**Start Session** — acquires a lock in R2, pulls all files to your local folder.

**End Session** — uploads only changed files, saves a local manifest, releases the lock.

The other laptop can then Start Session and pull your changes.

## Edge cases handled

- Files modified outside a session → prompts you to keep or discard before starting
- Lock held by another device → shows which device, lets you force-take if it crashed
- Interrupted upload → checksum verified before replacing local file
- Files open during pull → temp file written first, then atomic rename
- Quit with active session → warning shown

## Project structure

```
strata/
  main.py                        # Entry point, tray icon
  config.py                      # Config loading/saving (~/.strata/config.json)
  requirements.txt
  core/
    r2.py                        # R2 client wrapper (boto3)
    manifest.py                  # File hashing + local state tracking
    lock.py                      # Session lock (stored in R2)
    engine.py                    # Sync orchestration (start/end session)
  ui/
    out_of_session_dialog.py     # "Files changed outside session" dialog
    lock_dialog.py               # "Session locked by another device" dialog
    settings_dialog.py           # R2 credentials + config
```

## Config file

Stored at `~/.strata/config.json`. Edit manually or via Settings dialog.

```json
{
  "device_id": "auto-generated UUID",
  "device_name": "MacBook Pro",
  "r2_account_id": "...",
  "r2_access_key": "...",
  "r2_secret_key": "...",
  "r2_bucket": "my-sync-bucket",
  "sync_dir": "/Users/you/Strata"
}
```

## R2 bucket structure

```
<your files>/          # mirrors your sync directory exactly
_sync/
  lock.json            # session lock (device + timestamp + token)
  manifest.json        # hashes of all files at last session end
```
