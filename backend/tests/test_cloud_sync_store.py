import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_observability_migration_runner import (
    observability_database,  # noqa: F401 -- pytest fixture dependency
)

from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
from app.cloud_replica.protocol import BatchLimits, BatchState, encode_batch
from app.cloud_replica.store import ReplicaStore, import_verified_stream

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 10, 29, tzinfo=UTC)
AGENT = "ai-fae-agent"


def record(key="a", agent=AGENT, turns=1):
    return {
        "kind": "session",
        "key": key * 52,
        "user_id": "b" * 52,
        "agent_id": agent,
        "source_kind": "fae",
        "channel": "web",
        "title": {"text": "example"},
        "primary_sender_name": None,
        "primary_sender_department": None,
        "created_at": "2026-10-01T00:00:00Z",
        "last_active_at": "2026-10-02T00:00:00Z",
        "turns": [{"question": {"text": "question"}}] * turns,
        "sanitizer_policy_version": "test-v1",
    }


def snapshot(at=NOW, agent=AGENT, complete=True, count=1):
    return SimpleNamespace(
        agent_id=agent,
        source_kind="fae",
        source_observed_at=at,
        sessions=(object(),) * count,
        complete=complete,
        elapsed_ms=14,
    )


def role_dsn(dsn, role):
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(netloc=f"{role}@{parts.hostname}:{parts.port}"))


def set_publisher(
    conn,
    agent=AGENT,
    source_kind="fae",
    mode="cloud_direct",
    publisher="cloud",
    writer="cloud_fae_test",
    instance=None,
):
    conn.execute(
        "select platform_replica.set_agent_publisher(%s,%s,%s,%s,%s,%s)",
        (agent, source_kind, mode, publisher, writer, instance),
    )


@pytest.fixture(scope="module")
def sync_db(request):
    dsn = request.getfixturevalue("observability_database")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute((ROOT / "migrations/008_cloud_replica.sql").read_text())
        conn.execute(
            (ROOT / "migrations/cloud_agent_sync/001_agent_sources.sql").read_text()
        )
        conn.execute("create role cloud_fae_test login in role platform_cloud_sync")
        conn.execute("create role cloud_other_test login in role platform_cloud_sync")
        conn.execute("create role office_test login in role platform_replica_import")
    return dsn


@pytest.fixture
def environment(sync_db):
    from app.cloud_sync.store import CloudSyncStore

    with psycopg.connect(sync_db, autocommit=True) as conn:
        conn.execute(
            "truncate platform_replica.sessions, platform_replica.agents, "
            "platform_replica.generations, platform_replica.import_audit, "
            "platform_replica.management_projections, platform_replica.agent_sources cascade"
        )
        set_publisher(conn)
        set_publisher(conn, agent="ai-daq-fae-agent", writer="cloud_other_test")
    cipher = FieldCipher(b"e" * 32)
    return SimpleNamespace(
        dsn=sync_db,
        cipher=cipher,
        cloud=CloudSyncStore(role_dsn(sync_db, "cloud_fae_test"), cipher=cipher),
        other=CloudSyncStore(role_dsn(sync_db, "cloud_other_test"), cipher=cipher),
        office=ReplicaStore(role_dsn(sync_db, "office_test"), cipher=cipher),
    )


def session_row(env, key="a"):
    with psycopg.connect(env.dsn) as conn:
        return conn.execute(
            "select display_payload, payload_nonce, updated_at, payload_sha256 "
            "from platform_replica.sessions where session_key=%s",
            (key * 52,),
        ).fetchone()


@pytest.mark.postgres
def test_changed_only_monotonic_complete_publication_and_failure(environment):
    env = environment
    first = env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    assert first.changed_sessions == 1
    previous = session_row(env)
    assert (
        env.cloud.publish_snapshot(
            snapshot(NOW + timedelta(seconds=10)), (record(turns=7),)
        ).changed_sessions
        == 0
    )
    assert session_row(env) == previous
    assert (
        env.cloud.publish_snapshot(snapshot(NOW), (record(turns=0),)).status == "stale"
    )
    env.cloud.record_failure(AGENT, "source_unavailable", NOW + timedelta(seconds=20))
    state = env.cloud.source_statuses()[AGENT]
    assert state["last_source_success_at"] == NOW + timedelta(seconds=10)
    assert state["last_error"] == "source_unavailable"
    assert state["source_turns"] == 7
    assert state["source_sessions"] == 1
    assert (
        env.cloud.publish_snapshot(
            snapshot(NOW + timedelta(seconds=30), count=0), ()
        ).deleted_sessions
        == 1
    )
    assert session_row(env) is None


@pytest.mark.postgres
def test_old_signed_office_batch_is_acknowledged_without_overwrite(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    original = session_row(env)
    private = Ed25519PrivateKey.generate()
    state = BatchState(
        "legacy-office",
        1,
        None,
        NOW - timedelta(minutes=1),
        NOW,
        NOW,
        NOW + timedelta(minutes=15),
        "test-v1",
    )
    payload = encode_batch((record(turns=0),), state, BatchSigner(private))
    result = import_verified_stream(
        io.BytesIO(payload),
        BatchVerifier(private.public_key()),
        BatchLimits(),
        env.office,
    )
    assert result.status == "imported"
    assert result.skipped_session_count == 1
    assert session_row(env) == original
    replay = import_verified_stream(
        io.BytesIO(payload),
        BatchVerifier(private.public_key()),
        BatchLimits(),
        env.office,
    )
    assert replay.status == "replayed"


@pytest.mark.postgres
def test_cross_agent_database_ownership_and_incomplete_snapshot(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    previous = session_row(env)
    with pytest.raises(Exception, match="ownership|publish_failed"):
        env.other.publish_snapshot(
            snapshot(NOW + timedelta(seconds=1)), (record(turns=2),)
        )
    with pytest.raises(Exception, match="incomplete"):
        env.cloud.publish_snapshot(
            snapshot(NOW + timedelta(seconds=2), complete=False), ()
        )
    with (
        pytest.raises(psycopg.Error),
        psycopg.connect(role_dsn(env.dsn, "office_test")) as conn,
    ):
        conn.execute(
            "delete from platform_replica.sessions where agent_id=%s", (AGENT,)
        )
    with (
        pytest.raises(psycopg.Error),
        psycopg.connect(role_dsn(env.dsn, "cloud_other_test")) as conn,
    ):
        conn.execute(
            "update platform_replica.sessions set payload_sha256=%s where agent_id=%s",
            ("0" * 64, AGENT),
        )
    with (
        pytest.raises(psycopg.Error),
        psycopg.connect(role_dsn(env.dsn, "cloud_fae_test")) as conn,
    ):
        conn.execute(
            "update platform_replica.agent_sources set writer_role='cloud_fae_test' where agent_id='ai-daq-fae-agent'"
        )
    assert session_row(env) == previous


def test_publication_store_and_ownership_migration_are_provided():
    assert (ROOT / "app/cloud_sync/store.py").exists(), "owned publisher missing"
    assert (ROOT / "migrations/cloud_agent_sync/001_agent_sources.sql").exists()


@pytest.mark.postgres
def test_atomic_rollback_when_a_session_key_belongs_to_another_agent(environment):
    env = environment
    # Shared dedicated worker logins may be assigned several agents, but a key
    # may never move between them even if that login can publish both agents.
    with psycopg.connect(env.dsn) as conn:
        set_publisher(conn, agent="ai-daq-fae-agent")
    env.cloud.publish_snapshot(snapshot(), (record(),))
    prior = env.cloud.source_statuses()["ai-daq-fae-agent"]
    with pytest.raises(Exception, match="publish_failed|scope"):
        env.cloud.publish_snapshot(
            snapshot(NOW + timedelta(seconds=1), agent="ai-daq-fae-agent", count=2),
            (
                record(key="c", agent="ai-daq-fae-agent"),
                record(agent="ai-daq-fae-agent"),
            ),
        )
    assert session_row(env, "c") is None
    assert env.cloud.source_statuses()["ai-daq-fae-agent"] == prior


@pytest.mark.postgres
def test_office_management_projection_and_local_source_progress_are_independent(
    environment,
):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    with psycopg.connect(env.dsn) as conn:
        set_publisher(
            conn, "metabot-local", "metabot", "office", "office", None, "legacy-office"
        )
        set_publisher(
            conn, "remote-admin", "admin", "office", "office", None, "legacy-office"
        )
    projection = {
        "kind": "review_feedback_totals_projection",
        "key": "c" * 52,
        "agent_id": AGENT,
        "observed_at": "2026-10-29T00:00:00Z",
        "feedback_rows": 3,
        "negative_rows": 1,
        "negative_turns": 1,
        "positive_rows": 2,
        "sanitizer_policy_version": "test-v1",
    }
    private = Ed25519PrivateKey.generate()
    state = BatchState(
        "legacy-office",
        1,
        None,
        NOW - timedelta(minutes=1),
        NOW,
        NOW,
        NOW + timedelta(minutes=15),
        "test-v1",
    )
    payload = encode_batch((record(turns=0), projection), state, BatchSigner(private))
    result = import_verified_stream(
        io.BytesIO(payload),
        BatchVerifier(private.public_key()),
        BatchLimits(),
        env.office,
    )
    assert result.record_count == 1
    assert result.skipped_session_count == 1
    statuses = env.cloud.source_statuses()
    assert statuses["metabot-local"]["last_source_success_at"] == NOW
    assert statuses["remote-admin"]["last_source_success_at"] is None
    assert statuses[AGENT]["source_turns"] == 7
    with psycopg.connect(env.dsn) as conn:
        assert (
            conn.execute(
                "select count(*) from platform_replica.management_projections"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "select skipped_session_count from platform_replica.import_audit"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.postgres
def test_session_write_and_source_watermark_rollback_together(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    previous = session_row(env)
    prior_state = env.cloud.source_statuses()[AGENT]
    with psycopg.connect(env.dsn) as conn:
        conn.execute("""create function platform_replica.test_reject_progress() returns trigger
            language plpgsql as $$ begin raise exception 'test fault'; end $$""")
        conn.execute(
            "create trigger test_reject_progress before update on platform_replica.agent_sources "
            "for each row execute function platform_replica.test_reject_progress()"
        )
    try:
        with pytest.raises(Exception, match="publish_failed"):
            env.cloud.publish_snapshot(
                snapshot(NOW + timedelta(seconds=1)), (record(turns=7),)
            )
        assert session_row(env) == previous
        assert env.cloud.source_statuses()[AGENT] == prior_state
    finally:
        with psycopg.connect(env.dsn) as conn:
            conn.execute(
                "drop trigger test_reject_progress on platform_replica.agent_sources"
            )
            conn.execute("drop function platform_replica.test_reject_progress()")


@pytest.mark.postgres
def test_delayed_success_does_not_erase_a_more_recent_failure(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    env.cloud.record_failure(AGENT, "source_unavailable", NOW + timedelta(seconds=20))
    env.cloud.publish_snapshot(
        snapshot(NOW + timedelta(seconds=10)), (record(turns=2),)
    )
    state = env.cloud.source_statuses()[AGENT]
    assert state["last_attempt_at"] == NOW + timedelta(seconds=20)
    assert state["last_error"] == "source_unavailable"
    assert state["last_source_success_at"] == NOW + timedelta(seconds=10)


@pytest.mark.postgres
def test_publishers_are_serialized_and_other_agents_progress_independently(environment):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    from app.cloud_sync.store import CloudSyncStore

    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))

    def publisher(name):
        return CloudSyncStore(
            role_dsn(env.dsn, "cloud_fae_test"),
            cipher=env.cipher,
            connect=lambda dsn, **kw: psycopg.connect(dsn, application_name=name, **kw),
        )

    def wait_for_lock(name):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with psycopg.connect(env.dsn) as conn:
                waiting = conn.execute(
                    "select count(*) from pg_stat_activity where application_name=%s and wait_event_type='Lock'",
                    (name,),
                ).fetchone()[0]
            if waiting:
                return
            sleep(0.01)
        pytest.fail(f"{name} did not reach ownership lock")

    with psycopg.connect(env.dsn) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
        blocker.execute(
            "select platform_replica.lock_agent_writer(%s, true)",
            (AGENT,),
        )
        newer = pool.submit(
            publisher("cloud_sync_new").publish_snapshot,
            snapshot(NOW + timedelta(seconds=20)),
            (record(turns=7),),
        )
        wait_for_lock("cloud_sync_new")
        older = pool.submit(
            publisher("cloud_sync_old").publish_snapshot,
            snapshot(NOW + timedelta(seconds=10)),
            (record(turns=2),),
        )
        wait_for_lock("cloud_sync_old")
        independent = env.other.publish_snapshot(
            snapshot(agent="ai-daq-fae-agent"),
            (record(key="c", agent="ai-daq-fae-agent"),),
        )
        assert independent.changed_sessions == 1
        blocker.commit()
        assert newer.result(timeout=3).status == "published"
        assert older.result(timeout=3).status == "stale"
    assert env.cloud.source_statuses()[AGENT]["source_turns"] == 7


@pytest.mark.postgres
def test_migration_is_idempotent_reader_and_worker_grants_are_scoped(environment):
    env = environment
    with psycopg.connect(env.dsn) as conn:
        conn.execute(
            (ROOT / "migrations/cloud_agent_sync/001_agent_sources.sql").read_text()
        )
        assert conn.execute(
            "select has_table_privilege('platform_replica_read', 'platform_replica.agent_sources', 'select')"
        ).fetchone()[0]
        assert not conn.execute(
            "select has_column_privilege('platform_cloud_sync', 'platform_replica.agent_sources', 'publisher', 'update')"
        ).fetchone()[0]
        assert not conn.execute(
            "select has_table_privilege('platform_replica_import', 'platform_replica.agent_sources', 'delete')"
        ).fetchone()[0]
        assert not conn.execute(
            "select has_table_privilege('platform_cloud_sync', 'platform_replica.management_projections', 'insert')"
        ).fetchone()[0]
        assert not conn.execute(
            "select has_table_privilege('platform_cloud_sync', 'platform_replica.sessions', 'truncate')"
        ).fetchone()[0]
        assert (
            conn.execute(
                "select count(*) from platform_replica.agent_source_ownership_audit where agent_id=%s",
                (AGENT,),
            ).fetchone()[0]
            > 0
        )
        set_publisher(conn, mode="office", publisher="office", writer=None)
        assert (
            conn.execute(
                "select ownership_epoch from platform_replica.agent_sources where agent_id=%s",
                (AGENT,),
            ).fetchone()[0]
            == 2
        )
    with pytest.raises(Exception, match="ownership"):
        env.cloud.publish_snapshot(snapshot(), (record(),))


@pytest.mark.postgres
def test_complete_snapshot_rejects_missing_encoded_sessions_before_deletion(
    environment,
):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    previous = session_row(env)
    with pytest.raises(Exception, match="snapshot_record_count"):
        env.cloud.publish_snapshot(snapshot(NOW + timedelta(seconds=10)), ())
    assert session_row(env) == previous


@pytest.mark.postgres
def test_cloud_ownership_can_only_be_seeded_for_canonical_direct_sources(environment):
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(environment.dsn) as conn,
    ):
        set_publisher(conn, agent="unregistered-agent")


@pytest.mark.postgres
def test_office_retention_cleans_eligible_rows_while_preserving_cloud_and_native(
    environment,
):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    cloud_payload = session_row(env)
    with psycopg.connect(env.dsn) as conn:
        set_publisher(
            conn, "metabot-local", "metabot", "office", "office", None, "legacy-office"
        )
        set_publisher(conn, "native-agent", "native", "native", "native", None, None)
    local = record(key="c", agent="metabot-local")
    local.update(source_kind="metabot", channel="feishu")
    unclaimed = record(key="d", agent="unclaimed-local")
    unclaimed.update(source_kind="metabot", channel="feishu")
    projection = {
        "kind": "review_feedback_totals_projection",
        "key": "e" * 52,
        "agent_id": AGENT,
        "observed_at": "2026-10-29T00:00:00Z",
        "feedback_rows": 3,
        "negative_rows": 1,
        "negative_turns": 1,
        "positive_rows": 2,
        "sanitizer_policy_version": "test-v1",
    }
    private = Ed25519PrivateKey.generate()
    state = BatchState(
        "legacy-office",
        1,
        None,
        NOW - timedelta(minutes=1),
        NOW,
        NOW,
        NOW + timedelta(minutes=15),
        "test-v1",
    )
    payload = encode_batch((local, unclaimed, projection), state, BatchSigner(private))
    import_verified_stream(
        io.BytesIO(payload),
        BatchVerifier(private.public_key()),
        BatchLimits(),
        env.office,
    )
    # A descriptor accidentally present in legacy agents must not be removed by
    # an unrelated office retention job either.
    with psycopg.connect(env.dsn) as conn:
        conn.execute(
            "insert into platform_replica.agents (agent_id,display_payload,payload_nonce,payload_sha256,updated_at) "
            "values ('native-agent','x','x',%s,now())",
            ("0" * 64,),
        )
    cutoff = datetime(2028, 1, 1, tzinfo=UTC)
    dry = env.office.expire(now=cutoff, dry_run=True)
    assert (dry.session_count, dry.agent_count, dry.management_count) == (2, 2, 1)
    assert session_row(env) == cloud_payload
    result = env.office.expire(now=cutoff)
    assert (result.session_count, result.agent_count, result.management_count) == (
        2,
        2,
        1,
    )
    assert session_row(env) == cloud_payload
    assert session_row(env, "c") is None
    assert session_row(env, "d") is None
    with psycopg.connect(env.dsn) as conn:
        assert conn.execute(
            "select agent_id from platform_replica.agents order by agent_id"
        ).fetchall() == [(AGENT,), ("native-agent",)]
        assert conn.execute(
            "select deleted_session_count,deleted_agent_count,deleted_management_count from platform_replica.retention_audit order by retention_run_id desc limit 1"
        ).fetchone() == (2, 2, 1)


@pytest.mark.postgres
def test_first_ownership_seed_waits_for_unclaimed_office_write_before_cloud_success(
    environment,
):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    with psycopg.connect(env.dsn) as conn:
        # Fixture-only reconstruction of the pre-registry starting state.
        conn.execute("truncate platform_replica.agent_sources")
        set_publisher(conn, agent="ai-daq-fae-agent", writer="cloud_other_test")
    office = psycopg.connect(role_dsn(env.dsn, "office_test"))
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        office.execute(
            "update platform_replica.sessions set payload_sha256=%s where agent_id=%s",
            ("0" * 64, AGENT),
        )

        def first_seed():
            with psycopg.connect(env.dsn, application_name="cloud_first_seed") as conn:
                set_publisher(conn)

        seed = pool.submit(first_seed)
        deadline = monotonic() + 3
        while monotonic() < deadline and not seed.done():
            with psycopg.connect(env.dsn) as conn:
                waiting = conn.execute(
                    "select count(*) from pg_stat_activity where application_name='cloud_first_seed' and wait_event_type='Lock'"
                ).fetchone()[0]
            if waiting:
                break
            sleep(0.01)
        assert not seed.done(), "initial ownership crossed an uncommitted office write"
        from app.cloud_sync.store import CloudSyncStore

        publisher = CloudSyncStore(
            role_dsn(env.dsn, "cloud_fae_test"),
            cipher=env.cipher,
            connect=lambda dsn, **kw: psycopg.connect(
                dsn, application_name="cloud_first_publication", **kw
            ),
        )
        publication = pool.submit(
            publisher.publish_snapshot,
            snapshot(NOW + timedelta(seconds=10)),
            (record(turns=7),),
        )
        deadline = monotonic() + 3
        while monotonic() < deadline and not publication.done():
            with psycopg.connect(env.dsn) as conn:
                waiting = conn.execute(
                    "select count(*) from pg_stat_activity where application_name='cloud_first_publication' and wait_event_type='Lock'"
                ).fetchone()[0]
            if waiting:
                break
            sleep(0.01)
        assert waiting, "publication did not reach the agent transaction barrier"
        # A different agent continues while this first seed is blocked.
        assert (
            env.other.publish_snapshot(
                snapshot(agent="ai-daq-fae-agent"),
                (record(key="c", agent="ai-daq-fae-agent"),),
            ).changed_sessions
            == 1
        )
        assert not publication.done(), "cloud success crossed the office commit barrier"
        office.commit()
        seed.result(timeout=3)
        assert publication.result(timeout=3).changed_sessions == 1
        assert env.cloud.source_statuses()[AGENT]["source_turns"] == 7
    finally:
        office.rollback()
        office.close()
        pool.shutdown(wait=True)


@pytest.mark.postgres
def test_controlled_ownership_api_resets_only_changed_source_state(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(turns=7),))
    previous = session_row(env)
    status = env.cloud.source_statuses()[AGENT]
    with psycopg.connect(env.dsn) as conn:
        audit_before = conn.execute(
            "select count(*) from platform_replica.agent_source_ownership_audit where agent_id=%s",
            (AGENT,),
        ).fetchone()[0]
        set_publisher(conn)
        assert (
            conn.execute(
                "select count(*) from platform_replica.agent_source_ownership_audit where agent_id=%s",
                (AGENT,),
            ).fetchone()[0]
            == audit_before
        )
    assert env.cloud.source_statuses()[AGENT] == status
    with psycopg.connect(env.dsn) as conn:
        set_publisher(conn, mode="office", publisher="office", writer=None)
    assert session_row(env) == previous
    switched = env.cloud.source_statuses()[AGENT]
    assert switched["ownership_epoch"] == status["ownership_epoch"] + 1
    assert switched["last_source_success_at"] is None
    assert switched["source_sessions"] is None
    assert switched["last_published_at"] == status["last_published_at"]
    for sql in (
        "insert into platform_replica.agent_sources (agent_id,source_kind,mode,publisher) values ('bypass','metabot','office','office')",
        "update platform_replica.agent_sources set publisher='native',mode='native' where agent_id='ai-fae-agent'",
        "delete from platform_replica.agent_sources where agent_id='ai-fae-agent'",
    ):
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            psycopg.connect(env.dsn) as conn,
        ):
            conn.execute(sql)
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(role_dsn(env.dsn, "cloud_fae_test")) as conn,
    ):
        set_publisher(conn)


@pytest.mark.postgres
def test_existing_takeover_and_publication_share_lock_order_without_blocking_other_agents(
    environment,
):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    writer = psycopg.connect(role_dsn(env.dsn, "cloud_fae_test"))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        writer.execute("select platform_replica.lock_agent_writer(%s,true)", (AGENT,))
        writer.execute(
            "update platform_replica.agent_sources set last_attempt_at=%s where agent_id=%s",
            (NOW, AGENT),
        )

        def takeover():
            with psycopg.connect(env.dsn, application_name="existing_takeover") as conn:
                conn.execute("set statement_timeout='2s'")
                set_publisher(conn, mode="office", publisher="office", writer=None)

        switch = pool.submit(takeover)
        deadline = monotonic() + 2
        waiting = False
        while monotonic() < deadline and not switch.done():
            with psycopg.connect(env.dsn) as conn:
                waiting = bool(
                    conn.execute(
                        "select count(*) from pg_stat_activity where application_name='existing_takeover' and wait_event_type='Lock'"
                    ).fetchone()[0]
                )
            if waiting:
                break
            sleep(0.01)
        assert waiting and not switch.done()
        assert (
            env.other.publish_snapshot(
                snapshot(agent="ai-daq-fae-agent"),
                (record(key="c", agent="ai-daq-fae-agent"),),
            ).changed_sessions
            == 1
        )
        writer.commit()
        switch.result(timeout=3)
        assert env.cloud.source_statuses()[AGENT]["publisher"] == "office"
    finally:
        writer.rollback()
        writer.close()
        pool.shutdown(wait=True)


@pytest.mark.postgres
def test_direct_row_write_cannot_invert_advisory_and_session_row_locks(environment):
    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    with psycopg.connect(role_dsn(env.dsn, "cloud_fae_test")) as publisher:
        publisher.execute(
            "select platform_replica.lock_agent_writer(%s,true)", (AGENT,)
        )
        with (
            pytest.raises(psycopg.errors.LockNotAvailable, match="agent_writer_busy"),
            psycopg.connect(role_dsn(env.dsn, "office_test")) as office,
        ):
            office.execute("set statement_timeout='1s'")
            office.execute(
                "update platform_replica.sessions set payload_sha256=%s where agent_id=%s",
                ("0" * 64, AGENT),
            )
        publisher.execute(
            "update platform_replica.sessions set updated_at=updated_at where agent_id=%s",
            (AGENT,),
        )


@pytest.mark.postgres
def test_publication_waits_for_existing_takeover_before_rechecking_authority(
    environment,
):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic, sleep

    from app.cloud_sync.store import CloudSyncStore

    env = environment
    env.cloud.publish_snapshot(snapshot(), (record(),))
    previous = session_row(env)
    operator = psycopg.connect(env.dsn)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        set_publisher(operator, mode="office", publisher="office", writer=None)
        publisher = CloudSyncStore(
            role_dsn(env.dsn, "cloud_fae_test"),
            cipher=env.cipher,
            connect=lambda dsn, **kw: psycopg.connect(
                dsn, application_name="cloud_after_takeover", **kw
            ),
        )
        publication = pool.submit(
            publisher.publish_snapshot,
            snapshot(NOW + timedelta(seconds=10)),
            (record(turns=7),),
        )
        deadline = monotonic() + 2
        waiting = False
        while monotonic() < deadline and not publication.done():
            with psycopg.connect(env.dsn) as conn:
                waiting = bool(
                    conn.execute(
                        "select count(*) from pg_stat_activity where application_name='cloud_after_takeover' and wait_event_type='Lock'"
                    ).fetchone()[0]
                )
            if waiting:
                break
            sleep(0.01)
        assert waiting and not publication.done()
        assert (
            env.other.publish_snapshot(
                snapshot(agent="ai-daq-fae-agent"),
                (record(key="c", agent="ai-daq-fae-agent"),),
            ).changed_sessions
            == 1
        )
        operator.commit()
        with pytest.raises(Exception, match="agent_ownership_invalid"):
            publication.result(timeout=3)
        assert session_row(env) == previous
        assert env.cloud.source_statuses()[AGENT]["last_source_success_at"] is None
    finally:
        operator.rollback()
        operator.close()
        pool.shutdown(wait=True)
