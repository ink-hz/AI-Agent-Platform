from datetime import UTC, datetime
import io
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.cloud_replica import cli
from app.cloud_replica.store import ReplicaImportResult, ReplicaRetentionResult


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


def test_import_cli_returns_only_aggregate_result(monkeypatch, capsys):
    class Store:
        def import_batch(self, batch):
            return ReplicaImportResult(
                status="imported",
                sequence=batch.header.sequence,
                record_count=len(batch.records),
                digest=batch.digest,
            )

    monkeypatch.setattr(cli, "_store_from_environment", lambda: Store())
    monkeypatch.setattr(cli, "_verifier_from_environment", lambda: object())
    monkeypatch.setattr(
        cli,
        "decode_and_verify_batch",
        lambda stream, verifier, limits: type(
            "Batch",
            (),
            {
                "header": type("Header", (), {"sequence": 7})(),
                "records": ({"safe": True},),
                "digest": "d" * 64,
            },
        )(),
    )

    assert cli.main(["import"], input_stream=io.BytesIO(b"signed")) == 0
    output = capsys.readouterr().out

    assert '"sequence": 7' in output
    assert "signed" not in output


def test_retention_cli_supports_dry_run(monkeypatch, capsys):
    class Store:
        def expire(self, *, now, dry_run):
            assert dry_run is True
            return ReplicaRetentionResult(True, 4, 1)

    monkeypatch.setattr(cli, "_store_from_environment", lambda: Store())

    assert cli.main(["retention", "--dry-run"]) == 0
    value = json.loads(capsys.readouterr().out)

    assert value == {
        "agent_count": 1,
        "dry_run": True,
        "session_count": 4,
        "status": "completed",
    }


def test_migrate_cli_runs_only_replica_migration(monkeypatch, capsys):
    calls = []

    class Store:
        def migrate(self):
            calls.append("migrate")

    monkeypatch.setattr(cli, "_store_from_environment", lambda: Store())

    assert cli.main(["migrate"]) == 0
    assert calls == ["migrate"]
    assert json.loads(capsys.readouterr().out) == {"status": "migrated"}


def test_reset_test_generation_cli_requires_explicit_source(monkeypatch, capsys):
    calls = []

    class Store:
        def reset_test_generation(self, source_instance_id):
            calls.append(source_instance_id)

    monkeypatch.setattr(cli, "_store_from_environment", lambda: Store())

    assert cli.main([
        "reset-test-generation", "--source-instance-id", "synthetic-acceptance"
    ]) == 0
    assert calls == ["synthetic-acceptance"]
    assert json.loads(capsys.readouterr().out) == {"status": "reset"}


def test_canary_cli_writes_without_printing_path_or_payload(monkeypatch, tmp_path, capsys):
    output = tmp_path / "canary.jsonl"
    calls = []

    def create(path, _clock):
        calls.append(path)
        print('{"status":"created"}')
        return 0

    monkeypatch.setattr(
        cli,
        "_create_canary",
        create,
    )

    assert cli.main(["canary", "--output", str(output)]) == 0
    assert calls == [str(output)]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "created"}
    assert str(output) not in captured.out


def test_backup_and_restore_stream_cli_never_persists_plaintext(tmp_path, monkeypatch, capsys):
    recovery = X25519PrivateKey.generate()
    private = recovery.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = recovery.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_path = _private(tmp_path / "recovery-private", private)
    public_path = _private(tmp_path / "recovery-public", public)
    backup_path = tmp_path / "replica.orb"
    plaintext = b"synthetic sanitized database stream"
    monkeypatch.setenv("PLATFORM_REPLICA_BACKUP_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv("PLATFORM_REPLICA_BACKUP_PATH", str(backup_path))

    assert cli.main(["backup"], input_stream=io.BytesIO(plaintext)) == 0
    assert plaintext not in backup_path.read_bytes()
    assert backup_path.stat().st_mode & 0o777 == 0o600
    capsys.readouterr()

    monkeypatch.setenv("PLATFORM_REPLICA_BACKUP_PRIVATE_KEY_FILE", str(private_path))
    restored = io.BytesIO()
    assert cli.main(["restore-stream"], output_stream=restored) == 0
    assert restored.getvalue() == plaintext
