from datetime import UTC, datetime
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from app.cloud_replica import cli


def _private(path, value: bytes):
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def test_export_cli_uses_file_secrets_and_prints_only_aggregate_metadata(
    tmp_path, monkeypatch, capsys
):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    database = _private(tmp_path / "database", b"postgresql://sensitive-dsn")
    dictionary = _private(tmp_path / "dictionary", b"customers: []\n")
    identity = _private(tmp_path / "identity", b"i" * 32)
    signing = _private(tmp_path / "signing", private)
    monkeypatch.setenv("PLATFORM_REPLICA_SOURCE_DATABASE_URL_FILE", str(database))
    monkeypatch.setenv("PLATFORM_REPLICA_SANITIZER_DICTIONARY_FILE", str(dictionary))
    monkeypatch.setenv("PLATFORM_REPLICA_IDENTITY_KEY_FILE", str(identity))
    monkeypatch.setenv("PLATFORM_REPLICA_SIGNING_PRIVATE_KEY_FILE", str(signing))
    monkeypatch.setenv("PLATFORM_REPLICA_EXPORT_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("PLATFORM_REPLICA_EXPORT_QUEUE_DIR", str(tmp_path / "queue"))
    monkeypatch.setenv("PLATFORM_REPLICA_SOURCE_INSTANCE_ID", "local-platform-1")

    class EmptySource:
        def fetch_sessions(self, **_kwargs):
            return ()

    monkeypatch.setattr(cli, "ReplicaSource", lambda database_url: EmptySource())

    assert cli.main(["export"], clock=lambda: now) == 0
    output = capsys.readouterr().out
    value = json.loads(output)

    assert value["sequence"] == 1
    assert value["record_count"] == 0
    assert "digest" in value
    assert "sensitive-dsn" not in output
    assert str(database) not in output
