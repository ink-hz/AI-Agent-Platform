from datetime import UTC, datetime
import json

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.store import ReplicaStore


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


def test_retention_dry_run_counts_without_mutation():
    connection = _RetentionConnection()
    store = ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )

    result = store.expire(now=datetime(2026, 8, 11, tzinfo=UTC), dry_run=True)

    assert (result.session_count, result.agent_count) == (3, 2)
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
