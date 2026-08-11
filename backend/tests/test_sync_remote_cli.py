from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace

from app.review.handoff import ImportResult, load_outbox_item
from app.review.models import BackfillReport
from app.sync_remote import cli
from app.sync_remote.importer import SyncResult


def write_item(path: Path, state: str) -> None:
    payload = {
        "schema_version": 1,
        "idempotency_key": "sha256:" + path.stem.ljust(64, "0")[:64],
        "batch": {},
        "release": {},
        "handoff": {
            "state": state,
            "attempt_count": 0,
            "last_error": None,
            "acknowledged_at": None,
            "result": None,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)


def test_outbox_sync_skips_acknowledged_and_retries_blocked(tmp_path):
    os.chmod(tmp_path, 0o700)
    acknowledged = tmp_path / "a.json"
    blocked = tmp_path / "b.json"
    write_item(acknowledged, "acknowledged")
    write_item(blocked, "blocked")

    class Importer:
        def __init__(self):
            self.paths = []

        def import_path(self, path):
            self.paths.append(path)
            payload = load_outbox_item(path)
            payload["handoff"]["state"] = "blocked"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            return ImportResult("blocked", "source_turn_missing")

    importer = Importer()
    summary = cli.sync_feedback_closure_outbox(tmp_path, importer)

    assert importer.paths == [blocked]
    assert summary == {
        "prepared": 0,
        "pending": 0,
        "acknowledged": 1,
        "blocked": 1,
        "terminal_failed": 0,
        "invalid": 0,
    }


def test_fae_source_success_is_not_rolled_back_by_blocked_handoff(
    monkeypatch,
    capsys,
    tmp_path,
):
    source = SyncResult(
        run_id="run-1",
        source_kind="fae",
        status="succeeded",
        source_counts={"turn_feedback": 1},
        applied_counts={"turn_feedback": 1},
        validation={},
    )
    coordinated = SimpleNamespace(
        source_sync=source,
        review_backfill=BackfillReport(1, 1, 1, 1, 0, 0, 1, 1, 1, 1),
    )
    config = SimpleNamespace(
        remote_ssh_host="host",
        remote_ssh_key_path="key",
        sync_database_url="postgresql://sync",
        sync_database_url_file="unused",
        registry_path="registry.yaml",
        feedback_closure_outbox_dir=str(tmp_path),
    )
    calls = []
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "default_sources", lambda *_args: {"fae": object()})
    monkeypatch.setattr(cli, "export_source", lambda _source: object())
    monkeypatch.setattr(
        cli,
        "import_bundle_with_review",
        lambda *_args, **_kwargs: coordinated,
    )
    monkeypatch.setattr(cli, "resolve_review_database_url", lambda _config: "review")
    monkeypatch.setattr(cli, "PsycopgReviewRepository", lambda _dsn: object())
    monkeypatch.setattr(cli, "YamlRepository", lambda _path: object())
    monkeypatch.setattr(cli, "HandoffImporter", lambda *_args: object())
    monkeypatch.setattr(
        cli,
        "sync_feedback_closure_outbox",
        lambda *_args: calls.append("handoff") or {
            "prepared": 0,
            "pending": 0,
            "acknowledged": 1,
            "blocked": 1,
            "terminal_failed": 0,
            "invalid": 0,
        },
    )

    exit_code = cli.main(["--source", "fae"])

    assert exit_code == 1
    assert calls == ["handoff"]
    output = capsys.readouterr()
    lines = [json.loads(line) for line in output.out.splitlines()]
    assert asdict(source) == lines[0]["source_sync"]
    assert lines[-1]["closure_handoff"]["blocked"] == 1
