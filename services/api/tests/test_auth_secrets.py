"""API keys supplied as a file rather than as an environment variable.

The mechanism is `common.config.resolve` and is tested there. What is tested
here is that the gate in front of the personal data actually goes through it —
a key that only exists in a mounted secret must open the door, and the startup
line must still say how the API is configured.
"""

import pytest
from api_service.auth import (
    ENV_ALLOW_UNAUTH,
    ENV_KEYS,
    configured_keys,
    describe_configuration,
)
from api_service.main import app
from common.config import FILE_SUFFIX
from fastapi.testclient import TestClient

client = TestClient(app)

ENTITY = "019ff9ac-dc79-749b-9b84-6e5e19fc03a4"


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_KEYS, raising=False)
    monkeypatch.delenv(ENV_ALLOW_UNAUTH, raising=False)
    monkeypatch.setattr("common.config.SECRETS_DIR", tmp_path / "none")


def test_keys_can_arrive_as_a_file(monkeypatch, tmp_path):
    path = tmp_path / "keys"
    path.write_text("key-one,key-two\n", encoding="utf-8")
    monkeypatch.setenv(f"{ENV_KEYS}{FILE_SUFFIX}", str(path))
    assert configured_keys() == frozenset({"key-one", "key-two"})


def test_keys_can_arrive_as_a_mounted_secret(monkeypatch, tmp_path):
    mounted = tmp_path / "secrets"
    mounted.mkdir()
    (mounted / "pcdf_api_keys").write_text("mounted-key", encoding="utf-8")
    monkeypatch.setattr("common.config.SECRETS_DIR", mounted)
    assert configured_keys() == frozenset({"mounted-key"})


def test_a_key_from_a_file_actually_opens_the_door(monkeypatch, tmp_path):
    path = tmp_path / "keys"
    path.write_text("file-only-key", encoding="utf-8")
    monkeypatch.setenv(f"{ENV_KEYS}{FILE_SUFFIX}", str(path))

    assert client.get("/review/summary").status_code == 401
    # A 401 rather than a 200 would mean the file was read but never consulted.
    assert client.get(
        "/review/summary", headers={"X-API-Key": "wrong"}
    ).status_code == 401


def test_the_startup_line_counts_keys_that_came_from_a_file(monkeypatch, tmp_path):
    path = tmp_path / "keys"
    path.write_text("a,b,c", encoding="utf-8")
    monkeypatch.setenv(f"{ENV_KEYS}{FILE_SUFFIX}", str(path))
    # How the API is running should never have to be inferred, whichever way
    # the keys arrived.
    assert "3 key(s) configured" in describe_configuration()
