"""
Layer 1: R2 client wrapper
Thin wrapper around boto3 for Cloudflare R2.
All R2 interactions go through here.
"""

import boto3
from botocore.exceptions import ClientError
from pathlib import Path
import json


class R2Client:
    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str):
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

    # --- File operations ---

    def upload_file(self, local_path: Path, remote_key: str) -> bool:
        """Upload a file to R2. Returns True on success."""
        try:
            self.s3.upload_file(str(local_path), self.bucket, remote_key)
            return True
        except Exception as e:
            print(f"[R2] Upload failed {remote_key}: {e}")
            return False

    def download_file(self, remote_key: str, local_path: Path) -> bool:
        """Download a file from R2 to a local path. Returns True on success."""
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, remote_key, str(local_path))
            return True
        except Exception as e:
            print(f"[R2] Download failed {remote_key}: {e}")
            return False

    def delete_file(self, remote_key: str) -> bool:
        """Delete a file from R2. Returns True on success."""
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=remote_key)
            return True
        except Exception as e:
            print(f"[R2] Delete failed {remote_key}: {e}")
            return False

    def file_exists(self, remote_key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=remote_key)
            return True
        except ClientError:
            return False

    # --- JSON helpers (used for manifest + lock) ---

    def get_json(self, remote_key: str) -> dict | None:
        """Fetch and parse a JSON file from R2. Returns None if not found."""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=remote_key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    def put_json(self, remote_key: str, data: dict) -> bool:
        """Upload a dict as JSON to R2. Returns True on success."""
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=remote_key,
                Body=json.dumps(data, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            return True
        except Exception as e:
            print(f"[R2] put_json failed {remote_key}: {e}")
            return False

    # --- Listing ---

    def list_files(self, prefix: str = "") -> list[str]:
        """List all keys in the bucket under a prefix. Excludes system keys."""
        keys = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Skip internal system files
                if not key.startswith("_sync/"):
                    keys.append(key)
        return keys
