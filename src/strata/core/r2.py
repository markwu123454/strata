"""
Layer 1: R2 client wrapper
Thin wrapper around boto3 for Cloudflare R2.
All R2 interactions go through here.

Error handling philosophy:
  - Operations that must succeed for the session to be valid (get_json,
    put_json, list_files) raise R2Error on failure so the engine can
    surface a clear message in the UI instead of silently returning None.
  - File transfer helpers (upload_file, download_file, delete_file) still
    return bool — a single bad file shouldn't abort the whole session —
    but they now log the full exception type so timeouts vs. auth failures
    vs. missing keys are distinguishable in the console.
  - All network calls use explicit connect/read timeouts so a blocked or
    unreachable endpoint fails fast instead of hanging the UI thread.
"""

import json
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointResolutionError

__all__ = ["R2Client", "R2Error"]


class R2Error(Exception):
    """Raised when an R2 operation fails in a way that should abort the
    current engine operation (lock read/write, manifest read/write, listing).

    The message is meant to be shown directly in the UI, so keep it concise
    and human-readable — no raw boto3 stack traces.
    """


def _friendly(exc: Exception, context: str) -> str:
    """Turn a raw exception into a short UI-friendly message."""
    name = type(exc).__name__

    if isinstance(exc, ConnectTimeoutError) or "ConnectTimeout" in name:
        return (
            f"{context}: connection timed out. "
            "Check that R2 is reachable from this network "
            "(some school/corporate firewalls block S3 endpoints)."
        )
    if "ReadTimeout" in name:
        return (
            f"{context}: read timed out. "
            "The server connected but stopped responding — try again."
        )
    if isinstance(exc, EndpointResolutionError) or "EndpointResolution" in name:
        return (
            f"{context}: could not resolve endpoint. "
            "Check your Account ID in Settings."
        )

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "403"):
            return (
                f"{context}: access denied ({code}). "
                "Check your Access Key and Secret Key in Settings."
            )
        if code in ("NoSuchBucket", "404"):
            return (
                f"{context}: bucket not found. "
                "Check your Bucket Name in Settings."
            )
        return f"{context}: R2 error {code} — {exc}"

    msg = str(exc)
    if "SSL" in msg or "certificate" in msg.lower() or "CERTIFICATE" in msg:
        return (
            f"{context}: SSL error — your network may be intercepting HTTPS "
            "traffic (common on school/corporate networks). "
            "Try on a different network."
        )

    return f"{context}: {name}: {exc}"


# Timeouts applied to every boto3 call. Values are deliberately conservative
# so a blocked endpoint fails fast rather than leaving the UI stuck loading.
_BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 2, "mode": "standard"},
)


class R2Client:
    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str):
        # Stored on the instance so StrataApp._rebuild_engines can compare
        # a profile's current credentials against a running engine and decide
        # whether to reuse or rebuild it.
        self.account_id = account_id
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=_BOTO_CONFIG,
        )

    # ── Connectivity check ─────────────────────────────────────────────────

    def check_connectivity(self):
        """Cheap sanity-check that R2 is reachable and credentials work.
        Raises R2Error with a clear message on any failure.

        Uses head_bucket — one round-trip, no data transferred. Called at
        the start of start_session / quick_pull so problems surface before
        we've acquired the lock or touched local files.
        """
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchBucket", "404"):
                raise R2Error(
                    f"Bucket '{self.bucket}' not found. "
                    "Check your Bucket Name in Settings."
                ) from e
            if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
                raise R2Error(
                    "Invalid credentials. "
                    "Check your Access Key and Secret Key in Settings."
                ) from e
            # 403/AccessDenied with a real bucket is fine — Cloudflare R2
            # returns 403 on head_bucket for restricted keys. We'll get the
            # right error on individual operations if something's actually wrong.
            if code not in ("403", "AccessDenied"):
                raise R2Error(_friendly(e, "Connectivity check")) from e
        except Exception as e:
            raise R2Error(_friendly(e, "Connectivity check")) from e

    # ── File operations ────────────────────────────────────────────────────

    def upload_file(self, local_path: Path, remote_key: str) -> bool:
        """Upload a file to R2. Returns True on success, False on failure."""
        try:
            self.s3.upload_file(str(local_path), self.bucket, remote_key)
            return True
        except Exception as e:
            print(f"[R2] Upload failed {remote_key}: {type(e).__name__}: {e}")
            return False

    def download_file(self, remote_key: str, local_path: Path) -> bool:
        """Download a file from R2. Returns True on success, False on failure."""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, remote_key, str(local_path))
            return True
        except Exception as e:
            print(f"[R2] Download failed {remote_key}: {type(e).__name__}: {e}")
            return False

    def delete_file(self, remote_key: str) -> bool:
        """Delete a file from R2. Returns True on success, False on failure."""
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=remote_key)
            return True
        except Exception as e:
            print(f"[R2] Delete failed {remote_key}: {type(e).__name__}: {e}")
            return False

    def file_exists(self, remote_key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=remote_key)
            return True
        except ClientError:
            return False

    # ── JSON helpers (used for manifest + lock) ────────────────────────────

    def get_json(self, remote_key: str) -> dict | None:
        """Fetch and parse a JSON file from R2.

        Returns None if the key doesn't exist (normal: no manifest yet, no
        lock held). Raises R2Error on any other failure — silently returning
        None for a network error would cause the lock manager to think nobody
        holds the lock and attempt to acquire it, which is wrong.
        """
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=remote_key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise R2Error(_friendly(e, f"Reading {remote_key}")) from e
        except Exception as e:
            raise R2Error(_friendly(e, f"Reading {remote_key}")) from e

    def put_json(self, remote_key: str, data: dict):
        """Upload a dict as JSON to R2. Raises R2Error on failure.

        Intentionally doesn't return bool — callers that write the lock or
        manifest must know if it failed; silently continuing would leave
        the system in an inconsistent state.
        """
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=remote_key,
                Body=json.dumps(data, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except Exception as e:
            raise R2Error(_friendly(e, f"Writing {remote_key}")) from e

    # ── Listing ────────────────────────────────────────────────────────────

    def list_files(self, prefix: str = "") -> list[str]:
        """List all keys in the bucket. Raises R2Error on failure — a
        listing error during _pull_all means we can't know what to download,
        so aborting is safer than silently skipping files."""
        keys = []
        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.startswith("_sync/"):
                        keys.append(key)
        except R2Error:
            raise
        except Exception as e:
            raise R2Error(_friendly(e, "Listing bucket")) from e
        return keys
