from datetime import UTC, datetime, timedelta
import io
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.cloud_replica import exporter as exporter_module
from app.cloud_replica.crypto import BatchSigner, BatchVerifier
from app.cloud_replica.exporter import ReplicaExporter
from app.cloud_replica.models import (
    OperationEventProjection,
    RawAttachment,
    RawSession,
    RawTurn,
    ReviewInboxProjection,
)
from app.cloud_replica.protocol import BatchLimits, decode_and_verify_batch
from app.cloud_replica.sanitize import SanitizationPolicy


class _Source:
    def __init__(self, sessions):
        self.sessions = sessions

    def fetch_sessions(self, *, after, after_key, through, limit):
        assert after < through
        assert after_key == ""
        assert limit == 100
        return self.sessions


def _raw_session(now):
    return RawSession(
        session_key="raw-session-uuid",
        agent_id="hr-bot",
        source_kind="metabot",
        channel="feishu",
        title="客户甲的项目鹰",
        user_identity="on_27882925f0e4f159846581dd8144ad63",
        primary_sender_name="洛奇",
        primary_sender_department="市场部",
        created_at=now,
        last_active_at=now,
        turns=(
            RawTurn(
                turn_key="raw-turn-uuid",
                turn_index=1,
                question="联系 alice@example.com，看附件 secret-customer.pdf",
                answer="候选人张三参与项目鹰",
                created_at=now,
                outcome="success",
                attachments=(
                    RawAttachment(
                        attachment_id="raw-attachment-uuid",
                        direction="incoming",
                        display_name="secret-customer.pdf",
                        mime_type="application/pdf",
                        size_bytes=1000,
                        received_or_generated_at=now,
                        archive_status="archived",
                        delivery_status="delivered",
                    ),
                ),
                sources=({"path": "/Users/neo/source.md"},),
                details={"secret": "never"},
            ),
        ),
    )


def _exporter(tmp_path, now, source, private_key):
    policy = SanitizationPolicy(
        version="test-v1",
        customers=("客户甲",),
        candidates=("张三",),
        projects=("项目鹰",),
    )
    return ReplicaExporter(
        source=source,
        policy=policy,
        identity_key=b"i" * 32,
        signer=BatchSigner(private_key),
        source_instance_id="local-platform-1",
        state_path=tmp_path / "state.json",
        queue_dir=tmp_path / "queue",
        clock=lambda: now,
    )


def test_exporter_queues_only_sanitized_hmac_identified_records(tmp_path):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    exporter = _exporter(tmp_path, now, _Source((_raw_session(now),)), private)

    result = exporter.export_batch(
        after=now - timedelta(minutes=5), through=now, limit=100
    )
    payload = result.batch_path.read_bytes()
    decoded = decode_and_verify_batch(
        io.BytesIO(payload), BatchVerifier(private.public_key()), BatchLimits()
    )
    serialized = payload.decode("utf-8")

    assert result.sequence == 1
    assert result.record_count == 1
    assert result.batch_path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600
    record = decoded.records[0]
    assert record["kind"] == "session"
    assert len(record["key"]) >= 40
    assert len(record["user_id"]) >= 40
    assert len(record["turns"][0]["key"]) >= 40
    for forbidden in (
        "raw-session-uuid",
        "raw-turn-uuid",
        "raw-attachment-uuid",
        "on_27882925f0e4f159846581dd8144ad63",
        "secret-customer.pdf",
        "alice@example.com",
        "客户甲",
        "张三",
        "项目鹰",
        "/Users/neo/source.md",
        '"details"',
        '"sources"',
    ):
        assert forbidden not in serialized


def test_exporter_signs_management_projection_manifest_counts(tmp_path):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()

    class ManagementSource(_Source):
        def fetch_management_projections(self, *, through):
            assert through == now
            return (
                ReviewInboxProjection("hr-bot", "raw-turn", 2, now),
                OperationEventProjection(
                    "raw-event", "hr-bot", "execution_failure", "critical",
                    "联系 alice@example.com", now,
                ),
            )

    exporter = _exporter(tmp_path, now, ManagementSource(()), private)
    result = exporter.export_batch(
        after=now - timedelta(minutes=5), through=now, limit=100
    )
    payload = result.batch_path.read_bytes()
    decoded = decode_and_verify_batch(
        io.BytesIO(payload), BatchVerifier(private.public_key()), BatchLimits()
    )

    assert decoded.header.record_counts == {
        "operation_event_projection": 1,
        "review_inbox_projection": 1,
    }
    assert {record["kind"] for record in decoded.records} == set(
        decoded.header.record_counts
    )
    assert "alice@example.com" not in payload.decode("utf-8")


def test_export_failure_preserves_existing_state_and_queue(tmp_path, monkeypatch):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    exporter = _exporter(tmp_path, now, _Source((_raw_session(now),)), private)
    exporter.state_path.parent.mkdir(parents=True, exist_ok=True)
    exporter.state_path.write_text('{"existing":true}\n', encoding="utf-8")
    exporter.state_path.chmod(0o600)
    exporter.queue_dir.mkdir(parents=True, exist_ok=True)
    existing = exporter.queue_dir / "existing.jsonl"
    existing.write_bytes(b"existing")
    before_state = exporter.state_path.read_bytes()
    before_queue = {path.name: path.read_bytes() for path in exporter.queue_dir.iterdir()}

    monkeypatch.setattr(exporter, "_load_state", lambda _after: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        exporter.export_batch(after=now - timedelta(minutes=5), through=now, limit=100)

    assert exporter.state_path.read_bytes() == before_state
    assert {path.name: path.read_bytes() for path in exporter.queue_dir.iterdir()} == before_queue


def test_state_commit_failure_removes_only_the_new_batch(tmp_path, monkeypatch):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    exporter = _exporter(tmp_path, now, _Source((_raw_session(now),)), private)
    exporter.queue_dir.mkdir(parents=True)
    existing = exporter.queue_dir / "existing.jsonl"
    existing.write_bytes(b"existing")

    monkeypatch.setattr(
        "app.cloud_replica.exporter._atomic_replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk failure")),
    )

    with pytest.raises(OSError, match="disk failure"):
        exporter.export_batch(
            after=now - timedelta(minutes=5), through=now, limit=100
        )

    assert not exporter.state_path.exists()
    assert {path.name for path in exporter.queue_dir.iterdir()} == {"existing.jsonl"}


def test_replay_has_same_logical_records_but_fresh_envelope(tmp_path):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    private = Ed25519PrivateKey.generate()
    source = _Source((_raw_session(now),))
    first_exporter = _exporter(tmp_path / "first", now, source, private)
    second_exporter = _exporter(
        tmp_path / "second", now + timedelta(seconds=1), source, private
    )

    first = first_exporter.export_batch(
        after=now - timedelta(minutes=5), through=now, limit=100
    )
    second = second_exporter.export_batch(
        after=now - timedelta(minutes=5), through=now, limit=100
    )
    verifier = BatchVerifier(private.public_key())
    first_batch = decode_and_verify_batch(
        io.BytesIO(first.batch_path.read_bytes()), verifier, BatchLimits()
    )
    second_batch = decode_and_verify_batch(
        io.BytesIO(second.batch_path.read_bytes()), verifier, BatchLimits()
    )

    assert first_batch.records == second_batch.records
    assert first_batch.header.created_at != second_batch.header.created_at
    assert first_batch.digest != second_batch.digest


def test_replica_updated_composite_cursor_does_not_drop_late_sessions(tmp_path):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    activity_time = now - timedelta(days=3)
    replica_updated_at = now - timedelta(minutes=1)
    sessions = tuple(
        RawSession(
            session_key=f"session-{index:03d}",
            agent_id="hr-bot",
            source_kind="metabot",
            channel="feishu",
            title=None,
            user_identity=f"user-{index}",
            primary_sender_name="洛奇",
            primary_sender_department="市场部",
            created_at=activity_time,
            last_active_at=activity_time,
            replica_updated_at=replica_updated_at,
        )
        for index in range(101)
    )

    class PaginatedSource:
        def fetch_sessions(self, *, after, after_key, through, limit):
            eligible = tuple(
                session
                for session in sessions
                if (session.replication_cursor_at, session.session_key)
                > (after, after_key)
                and session.replication_cursor_at <= through
            )
            return eligible[:limit]

    private = Ed25519PrivateKey.generate()
    exporter = _exporter(tmp_path, now, PaginatedSource(), private)
    first = exporter.export_batch(
        after=now - timedelta(minutes=5), through=now, limit=100
    )
    second = exporter.export_batch(
        after=first.upper_watermark, through=now, limit=100
    )
    verifier = BatchVerifier(private.public_key())
    first_batch = decode_and_verify_batch(
        io.BytesIO(first.batch_path.read_bytes()), verifier, BatchLimits()
    )
    second_batch = decode_and_verify_batch(
        io.BytesIO(second.batch_path.read_bytes()), verifier, BatchLimits()
    )

    assert len(first_batch.records) == 100
    assert len(second_batch.records) == 1
    assert len({record["key"] for record in first_batch.records + second_batch.records}) == 101
    assert second.upper_watermark == now


def _write_export_state(path, *, watermark, next_sequence=765):
    path.write_text(
        json.dumps({
            "source_instance_id": "orbbec-platform-local-production",
            "next_sequence": next_sequence,
            "previous_digest": "a" * 64,
            "upper_watermark": watermark.isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "cursor_session_key": "old-cursor",
        }) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_rewind_export_state_preserves_digest_chain_and_resets_cursor(tmp_path):
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    current = now - timedelta(minutes=1)
    target = now - timedelta(hours=7)
    state_path = tmp_path / "state.json"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    _write_export_state(state_path, watermark=current)

    state = exporter_module.rewind_export_state(
        state_path=state_path,
        queue_dir=queue_dir,
        target=target,
        expected_next_sequence=765,
        now=now,
    )

    value = json.loads(state_path.read_text(encoding="utf-8"))
    assert state.upper_watermark == target
    assert state.cursor_session_key == ""
    assert value["source_instance_id"] == "orbbec-platform-local-production"
    assert value["next_sequence"] == 765
    assert value["previous_digest"] == "a" * 64
    assert value["upper_watermark"] == "2026-08-16T19:00:00.000000Z"
    assert value["cursor_session_key"] == ""
    assert state_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("case", "target", "expected_sequence"),
    (
        ("sequence", datetime(2026, 8, 16, 19, 0, tzinfo=UTC), 764),
        ("not_earlier", datetime(2026, 8, 17, 1, 59, tzinfo=UTC), 765),
        ("too_old", datetime(2025, 8, 16, 1, 59, tzinfo=UTC), 765),
        ("naive", datetime(2026, 8, 16, 19, 0), 765),
    ),
)
def test_rewind_export_state_rejects_invalid_guard_without_mutation(
    tmp_path, case, target, expected_sequence,
):
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    state_path = tmp_path / "state.json"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    _write_export_state(state_path, watermark=now - timedelta(minutes=1))
    before = state_path.read_bytes()

    with pytest.raises(RuntimeError, match="replica export rewind rejected"):
        exporter_module.rewind_export_state(
            state_path=state_path,
            queue_dir=queue_dir,
            target=target,
            expected_next_sequence=expected_sequence,
            now=now,
        )

    assert state_path.read_bytes() == before, case


def test_rewind_export_state_rejects_queued_batch_and_unsafe_state(tmp_path):
    now = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
    state_path = tmp_path / "state.json"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    _write_export_state(state_path, watermark=now - timedelta(minutes=1))
    before = state_path.read_bytes()
    (queue_dir / "batch-00000000000000000765.jsonl").write_text("queued")

    with pytest.raises(RuntimeError, match="replica export rewind rejected"):
        exporter_module.rewind_export_state(
            state_path=state_path, queue_dir=queue_dir,
            target=now - timedelta(hours=7), expected_next_sequence=765, now=now,
        )
    assert state_path.read_bytes() == before

    (queue_dir / "batch-00000000000000000765.jsonl").unlink()
    state_path.chmod(0o644)
    with pytest.raises(RuntimeError, match="replica export rewind rejected"):
        exporter_module.rewind_export_state(
            state_path=state_path, queue_dir=queue_dir,
            target=now - timedelta(hours=7), expected_next_sequence=765, now=now,
        )
