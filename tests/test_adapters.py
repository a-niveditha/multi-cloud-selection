"""Adapter logic with FAKE SDK clients. These check our code (what we send, how we
compare checksums, the hard deadline); they do NOT prove the real SDKs behave this
way. That must be checked against real buckets in the pilot."""
import time
from types import SimpleNamespace

from rmc.providers.b2 import B2Provider
from rmc.providers.base import StorageProvider
from rmc.providers.gcs import GCSProvider
from rmc.providers.s3 import S3Provider


# ---- hard deadline ---------------------------------------------------------------
class SlowProvider(StorageProvider):
    enforce_deadline = True

    def put_object(self, file, key, timeout_s):
        time.sleep(1.0)               # ignores timeout_s, like a hung SDK call

    def verify(self, file, key):
        return True


class BrokenProvider(SlowProvider):
    def put_object(self, file, key, timeout_s):
        raise ConnectionResetError("reset by peer")


def test_hard_deadline_stops_waiting_for_a_hung_sdk(payload):
    start = time.perf_counter()
    r = SlowProvider("slow").upload(payload, "k", timeout_s=0.2)
    assert r.timed_out and not r.success
    assert 0.2 <= r.transfer_time_s < 0.6
    assert time.perf_counter() - start < 0.6


def test_sdk_exception_is_a_failure_not_a_timeout(payload):
    r = BrokenProvider("broken").upload(payload, "k", timeout_s=5)
    assert not r.success and not r.timed_out and "ConnectionResetError" in r.error


# ---- S3 ----------------------------------------------------------------------------
class FakeS3:
    def __init__(self, etag=None):
        self.objects, self.etag, self.put_kwargs = {}, etag, None

    def put_object(self, Bucket, Key, Body, ContentMD5):
        self.put_kwargs = dict(Bucket=Bucket, Key=Key, ContentMD5=ContentMD5)
        self.objects[Key] = len(Body.read())

    def head_object(self, Bucket, Key):
        return {"ETag": f'"{self.etag}"', "ContentLength": self.objects[Key]}


def test_s3_upload_sends_md5_and_verifies_etag(payload):
    client = FakeS3(etag=payload.md5_hex)
    r = S3Provider("aws_s3", "bkt", client).upload(payload, "k1", timeout_s=5)
    assert r.ok
    assert client.put_kwargs == {"Bucket": "bkt", "Key": "k1", "ContentMD5": payload.md5_b64}


def test_s3_etag_mismatch_fails_verification(payload):
    r = S3Provider("aws_s3", "bkt", FakeS3(etag="0" * 32)).upload(payload, "k1", timeout_s=5)
    assert r.success and r.verified is False and r.error == "checksum mismatch"


# ---- GCS ---------------------------------------------------------------------------
class FakeGCSBucket:
    def __init__(self, md5_b64=None):
        self.md5_b64, self.stored, self.upload_kwargs = md5_b64, {}, None

    def blob(self, key):
        bucket = self

        class Blob:
            def upload_from_filename(self, filename, **kw):
                bucket.upload_kwargs = kw
                with open(filename, "rb") as f:
                    bucket.stored[key] = len(f.read())
        return Blob()

    def get_blob(self, key, **kw):
        if key not in self.stored:
            return None
        return SimpleNamespace(md5_hash=self.md5_b64, size=self.stored[key])


def test_gcs_upload_disables_retries_and_never_overwrites(payload):
    bucket = FakeGCSBucket(md5_b64=payload.md5_b64)
    r = GCSProvider("gcs", bucket).upload(payload, "k1", timeout_s=5)
    assert r.ok
    assert bucket.upload_kwargs["retry"] is None and bucket.upload_kwargs["if_generation_match"] == 0


def test_gcs_md5_mismatch_or_missing_object_fails(payload):
    r = GCSProvider("gcs", FakeGCSBucket(md5_b64="wrong")).upload(payload, "k1", timeout_s=5)
    assert r.verified is False
    assert GCSProvider("gcs", FakeGCSBucket(payload.md5_b64)).verify(payload, "never-uploaded") is False


# ---- B2 ----------------------------------------------------------------------------
class FakeB2Bucket:
    def __init__(self, sha1=None):
        self.sha1, self.stored, self.upload_kwargs = sha1, {}, None

    def upload_local_file(self, local_file, file_name, sha1_sum):
        self.upload_kwargs = dict(file_name=file_name, sha1_sum=sha1_sum)
        with open(local_file, "rb") as f:
            self.stored[file_name] = len(f.read())

    def get_file_info_by_name(self, name):
        return SimpleNamespace(content_sha1=self.sha1, size=self.stored[name], id_="id1")


def test_b2_upload_sends_sha1_and_verifies(payload):
    bucket = FakeB2Bucket(sha1=payload.sha1_hex)
    r = B2Provider("b2", bucket).upload(payload, "k1", timeout_s=5)
    assert r.ok and bucket.upload_kwargs == {"file_name": "k1", "sha1_sum": payload.sha1_hex}


def test_b2_sha1_mismatch_fails(payload):
    r = B2Provider("b2", FakeB2Bucket(sha1="0" * 40)).upload(payload, "k1", timeout_s=5)
    assert r.success and r.verified is False
