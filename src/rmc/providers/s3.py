"""AWS S3 adapter (boto3).

Upload: one PutObject request with Content-MD5, so S3 itself rejects a corrupted body.
Verify: HeadObject (metadata only, no download) and compare the ETag with the local MD5.

UNVERIFIED ASSUMPTIONS - check on the real bucket in the pilot:
  * The ETag equals the hex MD5 only for single-part uploads with SSE-S3 (the
    default) or no encryption. With SSE-KMS the ETag is NOT the MD5 and verify()
    will always fail: keep the bucket on SSE-S3.
  * SDK retries are disabled (total_max_attempts=1) so a failure is seen by our
    failover, not hidden inside boto3.
"""
from __future__ import annotations

import os

from rmc.models import LocalFile
from rmc.providers.base import StorageProvider


class S3Provider(StorageProvider):
    enforce_deadline = True

    def __init__(self, name: str, bucket: str, client, clock=None):
        super().__init__(name, clock)
        self.bucket = bucket
        self.client = client

    @classmethod
    def from_env(cls, name: str, pcfg: dict, tcfg: dict, clock=None) -> "S3Provider":
        import boto3
        from botocore.config import Config

        client = boto3.client("s3", region_name=pcfg["region"], config=Config(
            connect_timeout=tcfg["sdk_connect_timeout_s"],
            read_timeout=tcfg["sdk_read_timeout_s"],
            retries={"mode": "standard", "total_max_attempts": 1},
            # Plain PUT with Content-MD5; no extra default CRC checksums.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ))  # credentials: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the environment
        return cls(name, os.environ[pcfg["bucket_env"]], client, clock)

    def put_object(self, file: LocalFile, key: str, timeout_s: float) -> None:
        with open(file.path, "rb") as body:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentMD5=file.md5_b64)

    def verify(self, file: LocalFile, key: str) -> bool:
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return head["ETag"].strip('"') == file.md5_hex and head["ContentLength"] == file.size_bytes

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
