from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4

from app.cloud_replica.crypto import FieldCipher
from app.cloud_replica.management_repository import (
    ReplicaOperationsRepository,
    ReplicaReviewRepository,
)
from app.operations.models import EventFilters


NOW = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)


def _raw(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _row(cipher: FieldCipher, record: dict) -> dict:
    canonical = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    encrypted = cipher.encrypt(
        canonical, f"2:{record['kind']}:{record['key']}"
    )
    time_value = (
        record.get("updated_at")
        or record.get("first_feedback_at")
        or record["occurred_at"]
    )
    return {
        "projection_kind": record["kind"],
        "record_key": record["key"],
        "agent_id": record["agent_id"],
        "occurred_at": datetime.fromisoformat(time_value.replace("Z", "+00:00")),
        "display_payload": _raw(encrypted["ciphertext"]),
        "payload_nonce": _raw(encrypted["nonce"]),
        "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        if "max(committed_at)" in sql:
            return _Result([{"committed_at": NOW}])
        kind = params[0]
        agent_id = params[1] if len(params) > 1 else None
        return _Result([
            row for row in self.rows
            if row["projection_kind"] == kind
            and (agent_id is None or row["agent_id"] == agent_id)
        ])


def _connect(rows):
    return lambda *_args, **_kwargs: _Connection(rows)


def test_review_projection_is_agent_scoped_and_read_only():
    cipher = FieldCipher(b"m" * 32)
    issue_id = uuid4()
    records = [
        {
            "kind": "review_issue_projection", "key": str(issue_id),
            "agent_id": "hr-bot", "status": "open", "priority": "P1",
            "title": {"text": "脱敏问题"}, "failure_layer": "model",
            "owner_display": None, "linked_turn_count": 2,
            "updated_at": "2026-08-14T08:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
        {
            "kind": "review_issue_projection", "key": str(uuid4()),
            "agent_id": "marketing-bot", "status": "closed", "priority": "P2",
            "title": {"text": "其他 Agent"}, "failure_layer": None,
            "owner_display": None, "linked_turn_count": 0,
            "updated_at": "2026-08-14T07:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
    ]
    repository = ReplicaReviewRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record) for record in records]),
        now=lambda: NOW,
    )

    issues = repository.list_issues(agent_id="hr-bot", limit=100, offset=0)
    detail = repository.get_issue_detail(issue_id)

    assert [item["agent_id"] for item in issues] == ["hr-bot"]
    assert detail is not None and detail["replica_read_only"] is True
    assert not hasattr(repository, "create_issue")


def test_operation_projection_filters_before_pagination():
    cipher = FieldCipher(b"m" * 32)
    records = [
        {
            "kind": "operation_event_projection", "key": "a" * 52,
            "agent_id": "hr-bot", "event_type": "execution_failure",
            "severity": "critical", "summary": {"text": "脱敏故障"},
            "occurred_at": "2026-08-14T08:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
        {
            "kind": "operation_event_projection", "key": "b" * 52,
            "agent_id": "marketing-bot", "event_type": "usage_change",
            "severity": "info", "summary": {"text": "其他事件"},
            "occurred_at": "2026-08-14T07:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
    ]
    repository = ReplicaOperationsRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record) for record in records]),
        now=lambda: NOW,
    )

    page = repository.list_events(
        EventFilters(agent_id="hr-bot", severity="critical"), 50, 0
    )

    assert page.total == 1
    assert page.items[0].agent_id == "hr-bot"
    assert page.items[0].summary == "脱敏故障"
