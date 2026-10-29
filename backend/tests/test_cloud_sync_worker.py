"""Worker/configuration boundaries; no business source or model calls."""
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


def profile(tmp_path):
    from app.cloud_sync.models import SourceProfile
    return SourceProfile("ai-fae-agent", "fae", str(tmp_path / "source-dsn"), "ai_fae")


def config_file(tmp_path, **overrides):
    paths = {}
    for key in ("replica_database_url_file", "replica_encryption_key_file", "identity_key_file"):
        p = tmp_path / key
        p.write_bytes(b"x" * 32)
        p.chmod(0o600)
        paths[key] = str(p)
    value = {**paths, "interval_seconds": 10, "include_verified_subject": True,
             "sources": [{"agent_id": "ai-fae-agent", "kind": "fae",
                          "dsn_file": str(tmp_path / "source-dsn"),
                          "expected_database": "ai_fae", "schema": "public"}],
             **overrides}
    p = tmp_path / "worker.json"
    p.write_text(json.dumps(value))
    p.chmod(0o600)
    return p


def test_config_loads_paths_and_explicit_identity_mode(tmp_path):
    from app.cloud_sync.config import load_config
    value = load_config(config_file(tmp_path))
    assert value.interval_seconds == 10
    assert value.include_verified_subject is True
    assert value.sources[0].agent_id == "ai-fae-agent"
    assert value.identity_key_file == str(tmp_path / "identity_key_file")


@pytest.mark.parametrize("overrides", [
    {"interval_seconds": True}, {"interval_seconds": 0},
    {"interval_seconds": 61}, {"include_verified_subject": "true"},
    {"password": "must-not-appear-in-error"}, {"sources": []},
    {"identity_key_file": "relative/key"},
])
def test_config_rejects_unsafe_or_ambiguous_values(tmp_path, overrides):
    from app.cloud_sync.config import CloudSyncConfigError, load_config
    with pytest.raises(CloudSyncConfigError) as error:
        load_config(config_file(tmp_path, **overrides))
    assert "must-not-appear" not in str(error.value)


def test_config_rejects_world_readable_and_symlink_profile(tmp_path):
    from app.cloud_sync.config import CloudSyncConfigError, load_config
    p = config_file(tmp_path)
    p.chmod(0o644)
    with pytest.raises(CloudSyncConfigError):
        load_config(p)
    p.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(CloudSyncConfigError):
        load_config(link)


def snapshot(tmp_path):
    from app.cloud_replica.models import RawSession, RawTurn, VerifiedSubject
    from app.cloud_sync.models import AgentSnapshot
    session = RawSession(
        session_key="fae:00000000-0000-4000-8000-000000000001",
        agent_id="ai-fae-agent", source_kind="fae", channel="fae",
        title="Fixture question", user_identity=None,
        primary_sender_name=None, primary_sender_department=None,
        created_at=NOW, last_active_at=NOW,
        verified_subject=VerifiedSubject("00000000-0000-4000-8000-000000000002", "partner_operator"),
        turns=(RawTurn("fae:00000000-0000-4000-8000-000000000003", 0,
                       "Fixture question", "Fixture answer", NOW, outcome="resolved"),),
    )
    return AgentSnapshot("ai-fae-agent", "fae", NOW, (session,), True, 5)


class Store:
    def __init__(self):
        self.published = []
        self.failed = []

    @contextmanager
    def observation_cycle(self, agent_id):
        yield object()

    def publish_snapshot(self, snap, records, *, lease):
        assert lease is not None
        self.published.append((snap, records))
        return SimpleNamespace(status="published", changed_sessions=1, deleted_sessions=0,
                               source_sessions=1, source_turns=1)

    def record_failure(self, agent_id, code, attempted_at):
        self.failed.append((agent_id, code, attempted_at))


def worker(tmp_path, source_factory, **kwargs):
    from app.cloud_replica.sanitize import SanitizationPolicy
    from app.cloud_sync.worker import CloudSyncWorker
    store = Store()
    return CloudSyncWorker(
        store=store, identity_key=b"k" * 32, policy=SanitizationPolicy(),
        include_verified_subject=True, source_factory=source_factory,
        clock=lambda: NOW, **kwargs,
    ), store


def test_worker_preserves_exact_existing_identity_and_question_contract(tmp_path):
    from app.cloud_replica.crypto import stable_id
    snap = snapshot(tmp_path)
    w, store = worker(tmp_path, lambda p: SimpleNamespace(read_snapshot=lambda: snap))
    result = w.run_once(profile(tmp_path))
    assert result.status == "published"
    record = store.published[0][1][0]
    assert record["key"] == stable_id("session", snap.sessions[0].session_key, b"k" * 32)
    expected_actor = "verified-subject:partner_operator:00000000-0000-4000-8000-000000000002"
    assert record["user_id"] == stable_id("user:fae", expected_actor, b"k" * 32)
    assert record["turns"][0]["answer"]["text"] == "Fixture answer"
    assert record["verified_subject"]["subject_type"] == "partner_operator"
    assert not store.failed


def test_source_failure_retains_data_and_does_not_log_payload(tmp_path, capsys):
    def fail():
        raise ValueError("password=do-not-expose; customer-private-body")
    w, store = worker(tmp_path, lambda p: SimpleNamespace(read_snapshot=fail))
    assert w.run_once(profile(tmp_path)) is None
    assert not store.published
    assert store.failed == [("ai-fae-agent", "source_unavailable", NOW)]
    output = capsys.readouterr()
    assert "do-not-expose" not in output.out + output.err
    assert "customer-private" not in output.out + output.err


def test_busy_observation_cycle_skips_source_before_reading(tmp_path):
    def unexpected_read(profile):
        pytest.fail("a second observer must not acquire an older source snapshot")

    w, store = worker(tmp_path, unexpected_read)

    @contextmanager
    def busy(agent_id):
        yield None

    store.observation_cycle = busy
    assert w.run_once(profile(tmp_path)).status == "busy"
    assert not store.published and not store.failed


def test_incomplete_or_foreign_snapshot_is_not_published(tmp_path):
    from dataclasses import replace
    snap = replace(snapshot(tmp_path), complete=False)
    w, store = worker(tmp_path, lambda p: SimpleNamespace(read_snapshot=lambda: snap))
    assert w.run_once(profile(tmp_path)) is None
    assert not store.published
    assert store.failed[0][1] == "snapshot_invalid"


def test_one_source_failure_does_not_prevent_other_source_publication(tmp_path):
    from app.cloud_sync.models import SourceProfile
    snap = snapshot(tmp_path)
    def factory(p):
        def read():
            if p.kind == "admin":
                raise OSError("fixture unavailable")
            return snap
        return SimpleNamespace(read_snapshot=read)
    w, store = worker(tmp_path, factory)
    bad = SourceProfile("ai-admin-agent", "admin", str(tmp_path / "admin-dsn"), "ai_admin")
    assert w.run_once(bad) is None
    assert w.run_once(profile(tmp_path)).status == "published"
    assert len(store.failed) == len(store.published) == 1


def test_publication_failure_does_not_report_success(tmp_path):
    snap = snapshot(tmp_path)
    w, store = worker(tmp_path, lambda p: SimpleNamespace(read_snapshot=lambda: snap))
    def fail(*args, **kwargs):
        raise RuntimeError("fixture failed commit")
    store.publish_snapshot = fail
    assert w.run_once(profile(tmp_path)) is None
    assert store.failed[0][1] == "publication_unavailable"


def test_worker_keeps_existing_one_year_replica_retention(tmp_path):
    from dataclasses import replace
    from datetime import timedelta
    snap = snapshot(tmp_path)
    old = replace(snap.sessions[0], session_key="fae:00000000-0000-4000-8000-000000000099",
                  created_at=NOW - timedelta(days=500), last_active_at=NOW - timedelta(days=366))
    snap = replace(snap, sessions=(*snap.sessions, old))
    w, store = worker(tmp_path, lambda p: SimpleNamespace(read_snapshot=lambda: snap))
    assert w.run_once(profile(tmp_path)) is not None
    published_snapshot, records = store.published[0]
    assert len(published_snapshot.sessions) == len(records) == 1
    assert published_snapshot.complete is True


def test_polling_sources_run_independently_and_shutdown(tmp_path):
    import threading

    from app.cloud_sync.models import AgentSnapshot, SourceProfile
    started, good_finished, release = threading.Event(), threading.Event(), threading.Event()
    def factory(p):
        def read():
            if p.kind == "admin":
                started.set()
                assert release.wait(2)
                return AgentSnapshot("ai-admin-agent", "admin", NOW, (), True, 0)
            assert started.wait(1)
            good_finished.set()
            return snapshot(tmp_path)
        return SimpleNamespace(read_snapshot=read)
    w, store = worker(tmp_path, factory, emit=lambda value: None)
    stop = threading.Event()
    bad = SourceProfile("ai-admin-agent", "admin", str(tmp_path / "admin-dsn"), "ai_admin")
    thread = threading.Thread(target=w.run, args=((bad, profile(tmp_path)),),
                              kwargs={"interval_seconds": 10, "stop": stop})
    thread.start()
    try:
        assert good_finished.wait(1)
        assert thread.is_alive()
    finally:
        stop.set()
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert len(store.published) == 2


def test_health_uses_every_configured_source_not_another_recent_agent(tmp_path):
    from datetime import timedelta

    from app.cloud_sync.health import healthy_sources
    p = profile(tmp_path)
    states = {p.agent_id: {"publisher": "cloud", "mode": "cloud_direct",
                         "last_source_success_at": NOW - timedelta(seconds=61),
                         "last_failure_at": None}}
    assert healthy_sources(states, (p,), now=NOW, max_age_seconds=60) is False
    states[p.agent_id]["last_source_success_at"] = NOW
    assert healthy_sources(states, (p,), now=NOW, max_age_seconds=60) is True
    states[p.agent_id]["last_failure_at"] = NOW
    assert healthy_sources(states, (p,), now=NOW, max_age_seconds=60) is False
    assert healthy_sources({}, (p,), now=NOW, max_age_seconds=60) is False


@pytest.mark.parametrize("status,code", [("published", 0), ("unchanged", 0),
                                         ("busy", 1), ("stale", 1), (None, 1)])
def test_once_cli_requires_completed_source_publication(tmp_path, monkeypatch, status, code):
    from app.cloud_sync import cli

    monkeypatch.setattr(cli, "load_config", lambda path: SimpleNamespace(
        replica_database_url_file="dsn", replica_encryption_key_file="key",
        identity_key_file="identity", include_verified_subject=True,
        sources=(profile(tmp_path),)))
    monkeypatch.setattr(cli, "_read_dsn", lambda path: "fixture")
    monkeypatch.setattr(cli, "read_key_file", lambda *args, **kwargs: b"k" * 32)
    monkeypatch.setattr(cli, "CloudSyncStore", lambda *args, **kwargs: object())
    result = SimpleNamespace(status=status) if status else None
    monkeypatch.setattr(cli, "CloudSyncWorker", lambda **kwargs: SimpleNamespace(
        run_once=lambda p: result))
    assert cli.main(["once", "--config", "fixture"]) == code
