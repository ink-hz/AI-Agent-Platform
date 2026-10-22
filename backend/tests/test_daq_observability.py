import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.observability.models import SyncStatus
from app.observability.repository import PsycopgObservabilityRepository
from app.registry.repository import RegistryError, YamlRepository
from app.sync_remote.config import SyncSource, TableExport, default_sources
from app.sync_remote.export import (
    ExportBundle,
    ExportError,
    build_remote_program,
    parse_export,
)
from app.sync_remote.importer import (
    ImportValidationError,
    normalize_row,
    normalize_trace_span,
    validate_bundle,
)
from test_observability_repository import FakeConnect

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
SID = "00000000-0000-4000-8000-000000000001"
TID = "00000000-0000-4000-8000-000000000002"
OWNER = "00000000-0000-4000-8000-000000000003"


def test_daq_source_is_independent_and_schema_bound():
    source = default_sources("host", "key")["daq"]
    assert source.kind == "daq"
    assert source.remote_container == "ai-daq-fae-backend"
    assert source.trace_jsonl_path == "/app/data/daq_traces.jsonl"
    program = build_remote_program(source)
    assert "DAQ_DATABASE_URL" in program
    assert "POSTGRES_PASSWORD" not in program
    assert "daq_installation_identity" in program
    assert "daq_fae" in program
    assert "REPEATABLE READ READ ONLY" in program
    assert "source_identity" in program
    assert "table_complete" in program


def test_daq_program_rejects_non_allowlisted_tables():
    source = SyncSource(
        "daq",
        "host",
        "key",
        "python",
        "ai-daq-fae-backend",
        (TableExport("public.chat_sessions", "chat_sessions"),),
    )
    with pytest.raises(ExportError, match="source_table_not_allowed"):
        build_remote_program(source)


def test_daq_parser_requires_authenticated_complete_snapshot():
    with pytest.raises(ExportError, match="source_identity_unverified"):
        parse_export("daq", "")
    marker = json.dumps(
        {
            "record_type": "source_identity",
            "agent_id": "ai-daq-fae-agent",
            "schema": "daq_fae",
        }
    )
    with pytest.raises(ExportError, match="source_snapshot_incomplete"):
        parse_export("daq", marker)
    with pytest.raises(ExportError, match="source_identity_unverified"):
        parse_export("daq", marker.replace("ai-daq-fae-agent", "ai-fae-agent"))
    with pytest.raises(ExportError, match="source_table_not_allowed"):
        parse_export(
            "daq",
            marker
            + "\n"
            + json.dumps(
                {
                    "record_type": "table_row",
                    "table": "admin_chat_sessions",
                    "row": {"id": SID},
                }
            ),
        )


def test_daq_import_preserves_native_ids_ownership_and_versions_in_own_schema():
    row = normalize_row(
        "daq",
        "chat_sessions",
        {
            "id": SID,
            "external_session_id": "daq:external",
            "user_id": OWNER,
            "owner_subject_id": OWNER,
            "owner_subject_type": "enterprise_member",
            "metadata": {"authentication_mode": "platform_enterprise"},
        },
        NOW,
    )
    assert row.target_schema == "platform_source_daq"
    assert row.values["id"] == SID
    assert row.values["owner_subject_id"] == OWNER
    assert row.values["details"] == {"authentication_mode": "platform_enterprise"}
    turn = normalize_row(
        "daq",
        "chat_turns",
        {
            "id": TID,
            "metadata": {
                "agent_id": "ai-daq-fae-agent",
                "runtime_release": "runtime-sha",
                "knowledge_release": "knowledge-sha",
            },
            "question_at": None,
        },
        NOW,
    )
    assert turn.target_schema == "platform_source_daq"
    assert turn.values["details"]["runtime_release"] == "runtime-sha"
    assert turn.preserve_on_null_columns == ("question_at", "answer_at")
    span = normalize_trace_span(
        {
            "trace_id": "trace",
            "span_id": "root",
            "type": "root",
            "node": "daq_chat",
            "started_at": NOW,
            "metadata": {"agent_id": "ai-daq-fae-agent"},
        },
        NOW,
        source_kind="daq",
    )
    assert span.target_schema == "platform_source_daq"
    assert span.values["details"]["is_root"] is True


def test_daq_validation_checks_own_turns_and_foreign_identity():
    bundle = ExportBundle(
        "daq",
        {
            "chat_sessions": ({"id": SID, "external_session_id": "daq:s"},),
            "chat_turns": (
                {
                    "id": TID,
                    "session_id": "missing",
                    "trace_id": "trace",
                    "metadata": {"agent_id": "ai-daq-fae-agent"},
                },
            ),
        },
        (),
        0,
    )
    assert validate_bundle(bundle)["orphan_turn_sessions"] == 1
    assert validate_bundle(bundle)["turns_without_root_span"] == 1
    with pytest.raises(ImportValidationError, match="source_identity_unverified"):
        validate_bundle(
            ExportBundle(
                "daq",
                {"chat_sessions": ({"id": SID, "external_session_id": "camera:s"},)},
                (),
                0,
            )
        )
    with pytest.raises(ImportValidationError, match="source_identity_unverified"):
        validate_bundle(
            ExportBundle(
                "daq",
                {
                    "chat_turns": (
                        {
                            "id": TID,
                            "session_id": SID,
                            "trace_id": "trace",
                            "metadata": {"agent_id": "ai-fae-agent"},
                        },
                    )
                },
                (),
                0,
            )
        )


def test_daq_zero_sessions_has_fae_family_and_daq_sync_status():
    agents = PsycopgObservabilityRepository(
        "unused", connect=FakeConnect([[]])
    ).list_agents()
    daq = next(a for a in agents if a.id == "ai-daq-fae-agent")
    assert daq.source_kind == "fae"
    assert daq.deployment == "Alibaba Cloud"
    assert daq.freshness == "stale"
    assert (
        SyncStatus(
            source_kind="daq", status="succeeded", started_at=NOW, freshness="fresh"
        ).source_kind
        == "daq"
    )


def test_daq_registry_health_has_canonical_identity_without_replay():
    daq = YamlRepository(str(ROOT / "registry.yaml")).get_agent_by_flywheel_id(
        "ai-daq-fae-agent"
    )
    assert daq is not None
    assert daq.id == "daq"
    assert daq.health.url.endswith("/daq/health")
    assert daq.replay_targets == []
    assert daq.review_evidence is None


def test_daq_registry_rejects_camera_identity(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        "version: 1\nagents:\n  - id: daq\n    name: DAQ\n"
        "    entry_url: http://example/daq/\n"
        "    health: {url: http://example/daq/health}\n"
        "    flywheel_agent_id: ai-fae-agent\n"
    )
    with pytest.raises(RegistryError, match="canonical Catalog identity"):
        YamlRepository(str(path))


def test_daq_trace_rejects_foreign_root_and_orphan_span():
    base = {
        "chat_sessions": ({"id": SID, "external_session_id": "daq:s"},),
        "chat_turns": (
            {
                "id": TID,
                "session_id": SID,
                "trace_id": "trace",
                "metadata": {"agent_id": "ai-daq-fae-agent"},
            },
        ),
    }
    for span in (
        {"trace_id": "foreign-trace", "span_id": "s"},
        {
            "trace_id": "trace",
            "span_id": "root",
            "type": "root",
            "metadata": {"agent_id": "ai-fae-agent"},
        },
    ):
        with pytest.raises(ImportValidationError, match="source_trace_unverified"):
            validate_bundle(ExportBundle("daq", base, (span,), 0))


def test_daq_cli_fails_before_export_when_mirror_schema_missing(monkeypatch, capsys):
    from app.sync_remote import cli
    from test_sync_cli import prepare

    prepare(monkeypatch, "review-dsn")
    monkeypatch.setattr(cli, "default_sources", lambda *_: {"daq": object()})
    monkeypatch.setattr(
        cli, "required_daq_schema_available", lambda *_: False, raising=False
    )
    monkeypatch.setattr(cli, "export_source", lambda *_: pytest.fail("must not export"))
    assert cli.main(["--source", "daq"]) == 1
    assert "daq: schema_preflight_failed" in capsys.readouterr().err


def test_replica_attachment_query_does_not_join_fae_family_native_uuid():
    from app.cloud_replica.source import ATTACHMENT_SQL

    assert "t.source_kind = 'metabot'" in ATTACHMENT_SQL


def test_daq_cloud_zero_sessions_has_fae_family():
    from app.cloud_replica.repository import ReplicaObservabilityRepository

    repository = ReplicaObservabilityRepository("unused", cipher=object())
    repository._records = list
    repository._latest_success = lambda: NOW
    repository._status_freshness = lambda: "fresh"
    daq = next(a for a in repository.list_agents() if a.id == "ai-daq-fae-agent")
    assert daq.source_kind == "fae"


def test_daq_sync_does_not_run_camera_review_or_outbox(monkeypatch, capsys):
    from app.sync_remote import cli
    from app.sync_remote.importer import SyncResult
    from test_sync_cli import prepare

    prepare(monkeypatch, "review-dsn")
    monkeypatch.setattr(cli, "default_sources", lambda *_: {"daq": object()})
    monkeypatch.setattr(cli, "required_daq_schema_available", lambda *_: True)
    monkeypatch.setattr(
        cli,
        "import_bundle_with_review",
        lambda *_args, **_kwargs: pytest.fail("camera backfill must not run"),
    )
    monkeypatch.setattr(
        cli,
        "import_bundle",
        lambda *_args, **_kwargs: SyncResult("run", "daq", "succeeded", {}, {}, {}),
    )
    assert cli.main(["--source", "daq"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert rows[0]["source_sync"]["status"] == "succeeded"
    assert rows[1]["review_backfill"] == {
        "status": "not_enabled",
        "reason": "daq_observability_only",
    }


def test_daq_migration_runner_has_explicit_allowlist_and_dependency_check():
    source = (ROOT / "deploy/migrate-observability").read_text()
    assert "015_daq_observability.sql" in source
    assert "platform_source_daq.chat_sessions" in source
    assert "platform_read.sessions_without_daq" in source


@pytest.mark.parametrize(
    "outcome",
    [
        "resolved",
        "safe_abstained",
        "provider_configuration_error",
        "provider_unavailable",
        "budget_exhausted",
        "internal_error",
        "provider_error",
    ],
)
def test_daq_replica_keeps_authentic_machine_outcome_and_trace_status(outcome):
    from app.cloud_replica.models import RawSession, RawTraceAggregate, RawTurn
    from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session

    raw = RawSession(
        "daq:s",
        "ai-daq-fae-agent",
        "fae",
        "fae",
        None,
        None,
        None,
        None,
        NOW,
        NOW,
        turns=(
            RawTurn(
                "daq:t",
                0,
                "q",
                "a",
                NOW,
                outcome=outcome,
                trace=RawTraceAggregate(
                    "completed",
                    100,
                    None,
                    None,
                    "claude-opus-5-5",
                    None,
                    None,
                    None,
                    None,
                ),
            ),
        ),
    )
    record = sanitize_session(raw, SanitizationPolicy(version="test"))
    assert record.turns[0].outcome == outcome
    assert record.turns[0].trace.status == "completed"
    assert record.turns[0].trace.model_family == "claude"
    from dataclasses import replace

    bad = replace(raw, turns=(replace(raw.turns[0], outcome="secret arbitrary text"),))
    assert (
        sanitize_session(bad, SanitizationPolicy(version="test")).turns[0].outcome
        is None
    )


def test_daq_replica_preserves_allowlisted_channel_and_rejects_freeform():
    from dataclasses import replace

    from app.cloud_replica.models import RawSession
    from app.cloud_replica.sanitize import SanitizationPolicy, sanitize_session

    raw = RawSession(
        "daq:s", "ai-daq-fae-agent", "fae", "fae", None, None, None, None, NOW, NOW
    )
    policy = SanitizationPolicy(version="test")
    assert sanitize_session(raw, policy).channel == "fae"
    assert (
        sanitize_session(replace(raw, channel="arbitrary secret"), policy).channel
        is None
    )


def test_daq_sync_rule_maps_transport_to_own_agent(tmp_path):
    from datetime import timedelta

    from app.operations.rules import SyncObservation
    from test_operations_rules import make_engine

    engine, repository = make_engine(tmp_path)
    observation = SyncObservation(
        source_kind="daq",
        status="failed",
        completed_at=NOW,
        observed_at=NOW,
        last_success_at=NOW - timedelta(hours=37),
    )
    engine.evaluate_sync([observation], NOW)
    events = repository.list_active_attention("business")
    assert len(events) == 1
    assert events[0].agent_id == "ai-daq-fae-agent"
    assert events[0].fingerprint == "sync:daq:unavailable"


@pytest.mark.asyncio
async def test_daq_sync_scheduler_accepts_transport_without_losing_family(tmp_path):
    from app.operations.models import UsageBatch, UsageOccurrence
    from app.operations.repository import OperationsRepository
    from app.operations.scheduler import OperationsScheduler

    statuses = tuple(
        SyncStatus(
            source_kind=kind,
            status="succeeded",
            started_at=NOW,
            completed_at=NOW,
            last_success_at=NOW,
            freshness="fresh",
        )
        for kind in ("fae", "admin", "daq")
    )

    class Observability:
        async def sync_status(self):
            return statuses

    class Rules:
        def evaluate_sync(self, observations, now):
            assert [o.source_kind for o in observations] == ["fae", "admin", "daq"]

    class Source:
        def fetch_remote_usage_batch(self, kind, **kwargs):
            return (
                UsageBatch(
                    occurrences=(
                        UsageOccurrence(
                            turn_key="daq:t",
                            agent_id="ai-daq-fae-agent",
                            source_kind="fae",
                            occurred_at=NOW,
                        ),
                    ),
                    cumulative_totals={"ai-daq-fae-agent": 1},
                )
                if kind == "daq"
                else UsageBatch(occurrences=(), cumulative_totals={})
            )

    scheduler = OperationsScheduler(
        repository=OperationsRepository(str(tmp_path / "ops.sqlite")),
        observability_service=Observability(),
        operations_source=Source(),
        rule_engine=Rules(),
    )
    await scheduler.run_sync(NOW)
    batches, generations = await scheduler._remote_usage({}, NOW, initializing=False)
    occurrences, totals = scheduler._aligned_usage_inputs(batches)
    assert [o.source_kind for o in occurrences] == ["fae"]
    assert totals == {("ai-daq-fae-agent", "fae"): 1}
    assert "daq" in generations
    assert scheduler._source_kind("ai-daq-fae-agent") == "fae"


def test_daq_operations_source_separates_transport_and_family():
    from app.operations.source import PsycopgOperationsSource

    source = PsycopgOperationsSource("unused")
    statement, params = source._remote_usage_query(
        "daq", created_after=None, created_through=None
    )
    assert "t.agent_id=%s" in statement
    assert params == ("fae", "ai-daq-fae-agent")
    _, camera = source._remote_usage_query(
        "fae", created_after=None, created_through=None
    )
    assert camera == ("fae", "ai-fae-agent")


def test_runner_14_checks_verified_identity_through_daq_wrapper():
    runner = (ROOT / "deploy/migrate-observability").read_text()
    guard = runner.split("when :'expected_version'::integer = 14 then", 1)[1].split(
        "when :'expected_version'::integer = 15 then", 1
    )[0]
    assert "sessions_without_daq" in guard
