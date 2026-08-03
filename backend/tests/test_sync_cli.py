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
        sync_keychain_account="neo",
        sync_keychain_service="sync",
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "default_sources", lambda *_args: {"fae": object()})
    monkeypatch.setattr(
        cli,
        "export_source",
        lambda _source: ExportBundle("fae", {}, (), 0),
    )
    monkeypatch.setattr(cli, "_keychain_value", lambda *_args: "sync-dsn")
    monkeypatch.setattr(cli, "resolve_review_database_url", lambda _config: review_url)


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
