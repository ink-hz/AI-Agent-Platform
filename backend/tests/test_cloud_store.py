from datetime import UTC, datetime
import json

from app.cloud_replica.crypto import FieldCipher
import pytest

from app.cloud_replica.store import ReplicaStore, ReplicaStoreError


def test_prepared_session_encrypts_complete_display_payload_with_bound_aad():
    cipher = FieldCipher(b"e" * 32)
    store = ReplicaStore("postgresql://replica", cipher=cipher)
    record = {
        "kind": "session",
        "key": "a" * 52,
        "user_id": "b" * 52,
        "agent_id": "hr-bot",
        "source_kind": "metabot",
        "channel": "feishu",
        "created_at": "2026-08-11T08:00:00.000000Z",
        "last_active_at": "2026-08-11T08:01:00.000000Z",
        "title": {"text": "已脱敏标题"},
        "primary_sender_name": "洛奇",
        "primary_sender_department": "市场部",
        "turns": [{"question": {"text": "已脱敏问题"}}],
        "sanitizer_policy_version": "test-v1",
    }

    prepared = store.prepare_session(record)
    serialized = json.dumps(prepared.encrypted, ensure_ascii=False)

    assert "已脱敏标题" not in serialized
    assert "已脱敏问题" not in serialized
    assert prepared.payload_sha256
    assert cipher.decrypt(
        prepared.encrypted, f"1:session:{'a' * 52}"
    ) == json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_prepared_session_exposes_only_safe_index_columns():
    store = ReplicaStore("postgresql://replica", cipher=FieldCipher(b"e" * 32))
    record = {
        "kind": "session",
        "key": "a" * 52,
        "user_id": "b" * 52,
        "agent_id": "hr-bot",
        "source_kind": "metabot",
        "channel": "feishu",
        "created_at": "2026-08-11T08:00:00.000000Z",
        "last_active_at": "2026-08-11T08:01:00.000000Z",
        "title": {"text": "客户内容"},
        "primary_sender_name": "洛奇",
        "primary_sender_department": "市场部",
        "turns": [],
        "sanitizer_policy_version": "test-v1",
    }

    prepared = store.prepare_session(record)

    assert prepared.session_key == "a" * 52
    assert prepared.user_id == "b" * 52
    assert prepared.agent_id == "hr-bot"
    assert prepared.last_active_at == datetime(2026, 8, 11, 8, 1, tzinfo=UTC)
    assert not hasattr(prepared, "title")
    assert not hasattr(prepared, "question")


@pytest.mark.parametrize(
    "record",
    [
        {
            "kind": "review_issue_projection",
            "key": "5e18847c-91c0-4a35-bc3c-ddc39dac6ff3",
            "agent_id": "hr-bot",
            "updated_at": "2026-08-11T08:01:00.000000Z",
            "title": {"text": "已脱敏标题"},
            "status": "open",
            "priority": "P1",
            "failure_layer": "model",
            "owner_display": None,
            "linked_turn_count": 2,
            "sanitizer_policy_version": "test-v1",
        },
        {
            "kind": "review_inbox_projection",
            "key": "a" * 52,
            "agent_id": "hr-bot",
            "first_feedback_at": "2026-08-11T08:01:00.000000Z",
            "turn_key": "c" * 52,
            "feedback_count": 2,
            "sanitizer_policy_version": "test-v1",
        },
        {
            "kind": "operation_event_projection",
            "key": "b" * 52,
            "agent_id": "hr-bot",
            "occurred_at": "2026-08-11T08:01:00.000000Z",
            "event_type": "usage_change",
            "severity": "attention",
            "summary": {"text": "已脱敏标题"},
            "sanitizer_policy_version": "test-v1",
        },
    ],
)
def test_prepared_management_projection_encrypts_display_fields(
    record,
):
    store = ReplicaStore("postgresql://replica", cipher=FieldCipher(b"e" * 32))

    prepared = store.prepare_management(record)

    assert prepared.projection_kind == record["kind"]
    assert prepared.record_key == record["key"]
    assert prepared.agent_id == "hr-bot"
    assert prepared.occurred_at == datetime(2026, 8, 11, 8, 1, tzinfo=UTC)
    assert "已脱敏标题" not in json.dumps(prepared.encrypted, ensure_ascii=False)


def test_platform_operation_projection_uses_safe_non_null_index_agent():
    store = ReplicaStore("postgresql://replica", cipher=FieldCipher(b"e" * 32))
    record = {
        "kind": "operation_event_projection",
        "key": "d" * 52,
        "agent_id": None,
        "occurred_at": "2026-08-11T08:01:00.000000Z",
        "event_type": "data_access_recovered",
        "event_family": "recovery",
        "severity": "info",
        "status": "historical",
        "title": {"text": "flywheel data access recovered"},
        "summary": {"text": "The source is readable again."},
        "source_kind": "flywheel",
        "sanitizer_policy_version": "test-v1",
    }

    prepared = store.prepare_management(record)

    assert prepared.agent_id == "platform"
    assert prepared.projection_kind == "operation_event_projection"
    assert prepared.record_key == "d" * 52


def test_unknown_or_sensitive_management_projection_is_rejected():
    store = ReplicaStore("postgresql://replica", cipher=FieldCipher(b"e" * 32))
    with pytest.raises(ReplicaStoreError, match="record_invalid"):
        store.prepare_management(
            {
                "kind": "review_issue_projection",
                "key": "5e18847c-91c0-4a35-bc3c-ddc39dac6ff3",
                "agent_id": "hr-bot",
                "updated_at": "2026-08-11T08:01:00.000000Z",
                "raw_feedback": "不能出现在副本",
                "sanitizer_policy_version": "test-v1",
            }
        )


class _RetentionTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("begin")

    def __exit__(self, error_type, *_args):
        self.events.append("rollback" if error_type else "commit")


class _RetentionCursor:
    def __init__(self, statements):
        self.statements = statements
        self.result = None

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self.statements.append((normalized, params))
        if normalized.startswith("select count(*)") and "sessions where expires_at" in normalized:
            self.result = {"count": 3}
        elif normalized.startswith("select count(*)") and "management_projections" in normalized:
            self.result = {"count": 4}
        elif normalized.startswith("select count(*)") and "from platform_replica.agents" in normalized:
            self.result = {"count": 2}
        else:
            self.result = None

    def fetchone(self):
        return self.result


class _RetentionConnection:
    def __init__(self):
        self.events = []
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _RetentionTransaction(self.events)

    def cursor(self):
        return _RetentionCursor(self.statements)


class _ResetCursor:
    def __init__(self, statements, source_ids, counts):
        self.statements = statements
        self.source_ids = source_ids
        self.counts = counts
        self.result = None

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self.statements.append((normalized, params))
        if normalized.startswith("select source_instance_id"):
            self.result = [
                {"source_instance_id": source_id} for source_id in self.source_ids
            ]
        elif "as session_count" in normalized:
            self.result = self.counts
        else:
            self.result = None

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result


class _ResetConnection(_RetentionConnection):
    def __init__(self, source_ids, **counts):
        super().__init__()
        self.source_ids = source_ids
        self.counts = {
            "session_count": 0,
            "agent_count": 0,
            "other_audit_count": 0,
            "runtime_count": 0,
            "aggregate_count": 0,
            "management_count": 0,
            **counts,
        }

    def cursor(self):
        return _ResetCursor(self.statements, self.source_ids, self.counts)


def test_retention_dry_run_counts_without_mutation():
    connection = _RetentionConnection()
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    result = store.expire(now=datetime(2026, 8, 11, tzinfo=UTC), dry_run=True)

    assert (result.session_count, result.agent_count, result.management_count) == (3, 2, 4)
    assert result.dry_run is True
    assert not any(sql.startswith("delete") for sql, _ in connection.statements)
    assert not any("retention_audit" in sql for sql, _ in connection.statements)


def test_retention_deletes_expired_sessions_then_orphan_agents_and_audits():
    connection = _RetentionConnection()
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    result = store.expire(now=datetime(2026, 8, 11, tzinfo=UTC), dry_run=False)
    sql = [statement for statement, _ in connection.statements]

    assert result.dry_run is False
    assert next(index for index, value in enumerate(sql) if "delete from platform_replica.sessions" in value) < next(
        index for index, value in enumerate(sql) if "delete from platform_replica.agents" in value
    )
    assert any("insert into platform_replica.retention_audit" in value for value in sql)
    assert connection.events == ["begin", "commit"]


def test_reset_test_generation_removes_only_empty_or_exclusively_synthetic_replica():
    connection = _ResetConnection(
        ["synthetic-acceptance"], session_count=1, agent_count=1
    )
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    store.reset_test_generation("synthetic-acceptance")

    sql = [statement for statement, _ in connection.statements]
    deletes = [statement for statement in sql if statement.startswith("delete")]
    assert "lock table platform_replica.generations in exclusive mode" in sql
    assert deletes == [
        "delete from platform_replica.sessions",
        "delete from platform_replica.management_projections",
        "delete from platform_replica.agents",
        "delete from platform_replica.import_audit",
        "delete from platform_replica.generations",
    ]
    assert connection.events == ["begin", "commit"]


def test_reset_test_generation_refuses_when_any_other_source_exists():
    connection = _ResetConnection(["synthetic-acceptance", "production-local"])
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(ReplicaStoreError, match="test_reset_refused"):
        store.reset_test_generation("synthetic-acceptance")

    assert not any(
        statement.startswith("delete") for statement, _ in connection.statements
    )
    assert connection.events == ["begin", "rollback"]


def test_reset_test_generation_refuses_orphaned_data_without_generation():
    connection = _ResetConnection([], session_count=1, agent_count=1)
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(ReplicaStoreError, match="test_reset_refused"):
        store.reset_test_generation("synthetic-acceptance")

    assert not any(
        statement.startswith("delete") for statement, _ in connection.statements
    )


def test_sql_check_constraint_allows_every_python_projection_kind():
    """The Python allowlist and the database CHECK must not drift.

    A kind accepted by ``prepare_management`` but missing from the SQL
    constraint fails the whole batch at import time with a check violation,
    which is exactly how the feedback totals projection first broke.
    """
    import pathlib
    import re

    from app.cloud_replica.store import _MANAGEMENT_KEYS

    sql = (
        pathlib.Path(__file__).parents[1] / "migrations" / "008_cloud_replica.sql"
    ).read_text(encoding="utf-8")
    # The final constraint wins: read the idempotent `add constraint` block.
    blocks = re.findall(
        r"add constraint management_projections_projection_kind_check\s*"
        r"check \(projection_kind in \(([^)]*)\)\)",
        sql,
    )
    assert blocks, "projection_kind constraint not found"
    allowed = set(re.findall(r"'([a-z_]+)'", blocks[-1]))

    assert set(_MANAGEMENT_KEYS) == allowed
