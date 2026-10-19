"""Backblaze B2 adapter (b2sdk v2).

Upload: Bucket.upload_local_file with the local SHA-1, so B2 itself rejects a
corrupted body. Files under 100 MB go as a single (non-large-file) upload.
Verify: get_file_info_by_name (metadata only, no download) and compare
content_sha1 with the local SHA-1.

UNVERIFIED ASSUMPTIONS - check on the real bucket in the pilot:
  * b2sdk retries an upload up to 5 times internally (UploadManager.MAX_UPLOAD_ATTEMPTS).
    We set it to 1 on our instance so failures reach our failover. This is an
    internal attribute and could change between b2sdk versions.
  * b2sdk has no simple per-call socket timeout, so an abandoned upload thread may
    run for longer than with S3/GCS (see the limitation in providers/base.py).
  * Account authorisation (a network call) happens once in from_env(), outside
    any timed transfer.
"""
from __future__ import annotations

import os

from rmc.models import LocalFile
from rmc.providers.base import StorageProvider


class B2Provider(StorageProvider):
    enforce_deadline = True

    def __init__(self, name: str, bucket, clock=None):
        super().__init__(name, clock)
        self.bucket = bucket                    # b2sdk.v2.Bucket

    @classmethod
    def from_env(cls, name: str, pcfg: dict, tcfg: dict, clock=None) -> "B2Provider":
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        api = B2Api(InMemoryAccountInfo())
        api.authorize_account("production", os.environ["B2_APPLICATION_KEY_ID"], os.environ["B2_APPLICATION_KEY"])
        api.services.upload_manager.MAX_UPLOAD_ATTEMPTS = 1
        return cls(name, api.get_bucket_by_name(os.environ[pcfg["bucket_env"]]), clock)

    def put_object(self, file: LocalFile, key: str, timeout_s: float) -> None:
        self.bucket.upload_local_file(local_file=file.path, file_name=key, sha1_sum=file.sha1_hex)

    def verify(self, file: LocalFile, key: str) -> bool:
        info = self.bucket.get_file_info_by_name(key)
        return info.content_sha1 == file.sha1_hex and info.size == file.size_bytes

    def delete_object(self, key: str) -> None:
        info = self.bucket.get_file_info_by_name(key)
        self.bucket.delete_file_version(info.id_, key)
