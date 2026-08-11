from datetime import UTC, datetime, timedelta
import io

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.cloud_replica.crypto import BatchSigner, BatchVerifier, FieldCipher
from app.cloud_replica.protocol import BatchLimits, BatchState, encode_batch
from app.cloud_replica.store import (
    ReplicaStore,
    ReplicaStoreError,
    import_verified_stream,
)


class _Transaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("begin")
        return self

    def __exit__(self, error_type, *_args):
        self.events.append("rollback" if error_type else "commit")
        return False


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        self.connection.statements.append((normalized, params))
        if "from platform_replica.generations" in normalized:
            self.result = self.connection.generation
        elif "from platform_replica.import_audit" in normalized:
            self.result = self.connection.audit
        else:
            self.result = None
        if self.connection.fail_on and self.connection.fail_on in normalized:
            raise OSError("database write failed")
        return self

    def fetchone(self):
        return self.result


class _Connection:
    def __init__(self, events, generation=None, audit=None, fail_on=None):
        self.events = events
        self.generation = generation
        self.audit = audit
        self.fail_on = fail_on
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _Transaction(self.events)

    def cursor(self):
        return _Cursor(self)


def _encoded(private, *, sequence=1, previous=None, records=None):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    state = BatchState(
        source_instance_id="local-platform-1",
        sequence=sequence,
        previous_digest=previous,
        lower_watermark=now - timedelta(minutes=5),
        upper_watermark=now,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        sanitizer_policy_version="test-v1",
    )
    default_record = {
        "kind": "session",
        "key": "a" * 52,
        "user_id": "b" * 52,
        "agent_id": "hr-bot",
        "source_kind": "metabot",
        "channel": "feishu",
        "created_at": "2026-08-11T07:00:00.000000Z",
        "last_active_at": "2026-08-11T08:00:00.000000Z",
        "title": {"text": "安全标题"},
        "primary_sender_name": "洛奇",
        "primary_sender_department": "市场部",
        "turns": [],
        "sanitizer_policy_version": "test-v1",
    }
    return encode_batch(records or (default_record,), state, BatchSigner(private))


def _store(connection):
    return ReplicaStore(
        "postgresql://replica",
        cipher=FieldCipher(b"e" * 32),
        connection_factory=lambda *_args, **_kwargs: connection,
    )


def test_stream_is_fully_verified_before_transaction_begins():
    events = []
    private = Ed25519PrivateKey.generate()
    payload = _encoded(private)
    verifier = BatchVerifier(private.public_key())
    connection = _Connection(events)

    result = import_verified_stream(
        io.BytesIO(payload), verifier, BatchLimits(), _store(connection)
    )

    assert result.status == "imported"
    assert events == ["begin", "commit"]

    corrupted = bytearray(payload)
    corrupted[len(corrupted) // 2] ^= 1
    events.clear()
    with pytest.raises(Exception):
        import_verified_stream(
            io.BytesIO(bytes(corrupted)), verifier, BatchLimits(), _store(connection)
        )
    assert events == []


def test_exact_replay_is_a_noop():
    events = []
    private = Ed25519PrivateKey.generate()
    payload = _encoded(private)
    verifier = BatchVerifier(private.public_key())
    from app.cloud_replica.protocol import decode_and_verify_batch

    batch = decode_and_verify_batch(io.BytesIO(payload), verifier, BatchLimits())
    connection = _Connection(
        events,
        generation={"last_sequence": 1, "last_digest": batch.digest},
        audit={"digest": batch.digest},
    )

    result = _store(connection).import_batch(batch)

    assert result.status == "replayed"
    assert not any("insert into platform_replica.sessions" in sql for sql, _ in connection.statements)


@pytest.mark.parametrize(
    ("sequence", "previous", "generation", "error"),
    [
        (3, "a" * 64, {"last_sequence": 1, "last_digest": "a" * 64}, "sequence_gap"),
        (2, "b" * 64, {"last_sequence": 1, "last_digest": "a" * 64}, "predecessor_mismatch"),
    ],
)
def test_invalid_sequence_or_predecessor_rolls_back(
    sequence, previous, generation, error
):
    private = Ed25519PrivateKey.generate()
    verifier = BatchVerifier(private.public_key())
    from app.cloud_replica.protocol import decode_and_verify_batch

    batch = decode_and_verify_batch(
        io.BytesIO(_encoded(private, sequence=sequence, previous=previous)),
        verifier,
        BatchLimits(),
    )
    events = []
    connection = _Connection(events, generation=generation)

    with pytest.raises(ReplicaStoreError, match=error):
        _store(connection).import_batch(batch)

    assert events == ["begin", "rollback"]


def test_same_id_with_different_payload_in_one_batch_is_rejected():
    private = Ed25519PrivateKey.generate()
    verifier = BatchVerifier(private.public_key())
    from app.cloud_replica.protocol import decode_and_verify_batch

    first = {
        "kind": "session", "key": "a" * 52, "user_id": "b" * 52, "agent_id": "hr-bot",
        "source_kind": "metabot", "channel": "feishu",
        "created_at": "2026-08-11T07:00:00.000000Z",
        "last_active_at": "2026-08-11T08:00:00.000000Z", "title": {"text": "A"}, "turns": [],
        "primary_sender_name": "洛奇", "primary_sender_department": "市场部",
        "sanitizer_policy_version": "test-v1",
    }
    second = {**first, "title": {"text": "B"}}
    batch = decode_and_verify_batch(
        io.BytesIO(_encoded(private, records=(first, second))), verifier, BatchLimits()
    )

    with pytest.raises(ReplicaStoreError, match="record_conflict"):
        _store(_Connection([])).import_batch(batch)


def test_failed_upsert_rolls_back_without_watermark_commit():
    private = Ed25519PrivateKey.generate()
    verifier = BatchVerifier(private.public_key())
    connection = _Connection([], fail_on="insert into platform_replica.sessions")

    with pytest.raises(ReplicaStoreError, match="import_failed"):
        import_verified_stream(
            io.BytesIO(_encoded(private)), verifier, BatchLimits(), _store(connection)
        )

    assert connection.events == ["begin", "rollback"]
    assert not any("insert into platform_replica.generations" in sql for sql, _ in connection.statements)
