import json
from types import SimpleNamespace

from app.review.models import BackfillReport
from app.sync_remote import cli
from app.sync_remote.export import ExportBundle
from app.sync_remote.importer import CoordinatedSyncResult, SyncResult


SOURCE = SyncResult(
    run_id="run-1",
    source_kind="fae",
    status="succeeded",
    source_counts={"turn_feedback": 1},
    applied_counts={"turn_feedback": 1},
    validation={},
)
BACKFILL = BackfillReport(
    baseline_negative_rows=51,
    baseline_negative_turns=50,
    live_negative_rows=52,
    live_negative_turns=51,
    delta_negative_rows=1,
    delta_negative_turns=1,
    created_issues=1,
    created_links=1,
    created_events=1,
    linked_feedback_keys=52,
)


def prepare(monkeypatch, review_url):
    config = SimpleNamespace(
        remote_ssh_host="host",
        remote_ssh_key_path="key",
        sync_database_url_file="/private/sync-dsn",
        sync_database_url=None,
        registry_path="registry.yaml",
        feedback_closure_outbox_dir="/tmp/feedback-closure-outbox",
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "default_sources", lambda *_args: {"fae": object()})
    monkeypatch.setattr(
        cli,
        "export_source",
        lambda _source: ExportBundle("fae", {}, (), 0),
    )
    monkeypatch.setattr(cli, "read_secret_file", lambda *_args: "sync-dsn")
    monkeypatch.setattr(cli, "required_admin_schema_available", lambda *_args: True)
    monkeypatch.setattr(cli, "resolve_review_database_url", lambda _config: review_url)
    monkeypatch.setattr(cli, "YamlRepository", lambda _path: object())
    monkeypatch.setattr(cli, "HandoffImporter", lambda *_args: object())
    monkeypatch.setattr(
        cli,
        "sync_feedback_closure_outbox",
        lambda *_args: {
            "prepared": 0,
            "pending": 0,
            "acknowledged": 0,
            "blocked": 0,
            "terminal_failed": 0,
            "invalid": 0,
        },
    )


def test_cli_reports_source_and_review_sections(monkeypatch, capsys):
    prepare(monkeypatch, "review-dsn")
    monkeypatch.setattr(
        cli,
        "import_bundle_with_review",
        lambda *_args, **_kwargs: CoordinatedSyncResult(SOURCE, BACKFILL),
    )

    assert cli.main(["--source", "fae"]) == 0

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["source_sync"]["run_id"] == "run-1"
    assert rows[1]["review_backfill"]["status"] == "succeeded"
    assert rows[1]["review_backfill"]["created_issues"] == 1
    assert rows[2]["closure_handoff"]["blocked"] == 0


def test_cli_does_not_misreport_source_when_review_writer_is_unavailable(
    monkeypatch, capsys
):
    prepare(monkeypatch, None)
    monkeypatch.setattr(cli, "import_bundle", lambda *_args, **_kwargs: SOURCE)

    assert cli.main(["--source", "fae"]) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out)["source_sync"]["status"] == "succeeded"
    failure = json.loads(captured.err)
    assert failure["review_backfill"] == {
        "status": "failed",
        "reason": "review_database_unavailable",
    }


def test_cli_prefers_environment_sync_writer_dsn(monkeypatch):
    prepare(monkeypatch, "review-dsn")
    config = cli.load_config()
    config.sync_database_url = "env-sync-dsn"
    monkeypatch.setattr(cli, "read_secret_file", lambda *_args: (_ for _ in ()).throw(AssertionError("secret file must not be read")))
    seen = {}

    def import_coordinated(database_url, *_args, **_kwargs):
        seen["database_url"] = database_url
        return CoordinatedSyncResult(SOURCE, BACKFILL)

    monkeypatch.setattr(cli, "import_bundle_with_review", import_coordinated)

    assert cli.main(["--source", "fae"]) == 0
    assert seen["database_url"] == "env-sync-dsn"


def test_secret_file_failure_is_reported_as_sync_database_unavailable(
    monkeypatch, capsys
):
    prepare(monkeypatch, "review-dsn")

    def unavailable(*_args, **_kwargs):
        raise cli.SecretFileUnavailable("secret file unavailable")

    monkeypatch.setattr(cli, "read_secret_file", unavailable)

    assert cli.main(["--source", "fae"]) == 1
    assert "sync_database_unavailable" in capsys.readouterr().err


def test_admin_sync_fails_closed_before_export_when_schema_is_missing(
    monkeypatch, capsys
):
    prepare(monkeypatch, "review-dsn")
    config = cli.load_config()
    monkeypatch.setattr(
        cli, "default_sources", lambda *_args: {"admin": object()}
    )
    exported = []
    monkeypatch.setattr(cli, "export_source", lambda source: exported.append(source))
    monkeypatch.setattr(
        cli, "required_admin_schema_available", lambda *_args: False
    )

    assert cli.main(["--source", "admin"]) == 1
    assert exported == []
    assert capsys.readouterr().err.strip() == "admin: schema_preflight_failed"
