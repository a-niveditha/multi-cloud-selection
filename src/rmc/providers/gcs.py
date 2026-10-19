"""Google Cloud Storage adapter (google-cloud-storage).

Upload: Blob.upload_from_filename with SDK retries off. if_generation_match=0 makes
the upload fail rather than overwrite an existing object (except the throughput
probe, which reuses its key with overwrite=True).
Verify: get_blob (metadata only, no download) and compare blob.md5_hash (base64)
with the local MD5.

UNVERIFIED ASSUMPTIONS - check on the real bucket in the pilot:
  * Files over 8 MiB are sent as a RESUMABLE upload (one extra request to start the
    session, so roughly one extra RTT compared with the single S3 PUT). This is
    how the SDK behaves for a 10 MB file; we do not work around it, but it should
    be mentioned when comparing per-provider transfer times.
  * md5_hash is present for normal (non-composite) objects, which is our case.
"""
from __future__ import annotations

import os

from rmc.models import LocalFile
from rmc.providers.base import StorageProvider


class GCSProvider(StorageProvider):
    enforce_deadline = True

    def __init__(self, name: str, bucket, sdk_timeout: tuple[float, float] = (10, 30), clock=None):
        super().__init__(name, clock)
        self.bucket = bucket                    # google.cloud.storage.Bucket
        self.sdk_timeout = sdk_timeout          # (connect, read) seconds, per HTTP request

    @classmethod
    def from_env(cls, name: str, pcfg: dict, tcfg: dict, clock=None) -> "GCSProvider":
        from google.cloud import storage

        # credentials: GOOGLE_APPLICATION_CREDENTIALS (path to a key file outside the repo)
        client = storage.Client(project=os.environ.get("RMC_GCS_PROJECT") or None)
        bucket = client.bucket(os.environ[pcfg["bucket_env"]])
        return cls(name, bucket, (tcfg["sdk_connect_timeout_s"], tcfg["sdk_read_timeout_s"]), clock)

    def put_object(self, file: LocalFile, key: str, timeout_s: float, overwrite: bool = False) -> None:
        blob = self.bucket.blob(key)
        blob.upload_from_filename(file.path, timeout=self.sdk_timeout, retry=None, checksum=None,
                                  if_generation_match=None if overwrite else 0)

    def verify(self, file: LocalFile, key: str) -> bool:
        blob = self.bucket.get_blob(key, timeout=self.sdk_timeout, retry=None)
        return blob is not None and blob.md5_hash == file.md5_b64 and blob.size == file.size_bytes

    def delete_object(self, key: str) -> None:
        self.bucket.delete_blob(key, timeout=self.sdk_timeout)
