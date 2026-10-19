import pytest

from rmc.config import load_config
from rmc.models import LocalFile, RunContext


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def payload(tmp_path):
    """A small payload file (64 KiB) so tests are fast."""
    p = tmp_path / "payload.bin"
    p.write_bytes(bytes(range(256)) * 256)
    return LocalFile.from_path(p)


@pytest.fixture
def ctx():
    return RunContext(machine_id="test-machine", code_version="abc123", block_id="B01", config_hash="cfg0")
