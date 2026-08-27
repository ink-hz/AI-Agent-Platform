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
from app.fleet.catalog import AgentCatalog
from app.operations.models import EventFilters, UsageLeader


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
        or record.get("observed_at")
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


def test_operation_projection_restores_safe_event_semantics():
    cipher = FieldCipher(b"m" * 32)
    record = {
        "kind": "operation_event_projection", "key": "e" * 52,
        "agent_id": "ai-admin-agent",
        "event_type": "remote_sync_unavailable",
        "event_family": "data",
        "severity": "attention",
        "status": "active",
        "title": {"text": "ai-admin-agent synchronization is unavailable"},
        "summary": {"text": "The latest synchronization failed."},
        "source_kind": "admin",
        "occurred_at": "2026-08-14T08:00:00.000000Z",
        "sanitizer_policy_version": "v2",
    }
    repository = ReplicaOperationsRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record)]), now=lambda: NOW,
    )

    event = repository.list_events(EventFilters(), 50, 0).items[0]

    assert event.event_family == "data"
    assert event.status == "active"
    assert event.title == "ai-admin-agent synchronization is unavailable"
    assert event.source_kind == "admin"


def test_old_operation_projection_uses_explicit_compatibility_defaults():
    cipher = FieldCipher(b"m" * 32)
    record = {
        "kind": "operation_event_projection", "key": "f" * 52,
        "agent_id": "hr-bot", "event_type": "unknown_old_event",
        "severity": "info", "summary": {"text": "Legacy"},
        "occurred_at": "2026-08-14T08:00:00.000000Z",
        "sanitizer_policy_version": "v2",
    }
    repository = ReplicaOperationsRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record)]), now=lambda: NOW,
    )

    event = repository.list_events(EventFilters(), 50, 0).items[0]

    assert event.event_family == "execution"
    assert event.status == "historical"
    assert event.title == "unknown_old_event"
    assert event.source_kind == "cloud-replica"


def test_excluded_agents_are_absent_from_review_projections():
    cipher = FieldCipher(b"m" * 32)
    visible_issue_id = uuid4()
    hidden_issue_id = uuid4()
    records = [
        {
            "kind": "review_issue_projection", "key": str(visible_issue_id),
            "agent_id": "hr-bot", "status": "open", "priority": "P1",
            "title": {"text": "Visible"}, "failure_layer": "model",
            "owner_display": None, "linked_turn_count": 1,
            "updated_at": "2026-08-14T08:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
        {
            "kind": "review_issue_projection", "key": str(hidden_issue_id),
            "agent_id": "fae-bot", "status": "open", "priority": "P1",
            "title": {"text": "Hidden"}, "failure_layer": "model",
            "owner_display": None, "linked_turn_count": 1,
            "updated_at": "2026-08-14T07:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
    ]
    repository = ReplicaReviewRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record) for record in records]),
        now=lambda: NOW, catalog=AgentCatalog.default(),
    )

    assert [item["agent_id"] for item in repository.list_issues()] == ["hr-bot"]
    assert repository.list_issues(agent_id="fae-bot") == []
    assert repository.get_issue_detail(hidden_issue_id) is None
    assert repository.get_issue_detail(visible_issue_id) is not None


def test_excluded_agents_are_absent_from_operation_projections():
    cipher = FieldCipher(b"m" * 32)
    records = [
        {
            "kind": "operation_event_projection", "key": "c" * 52,
            "agent_id": "hr-bot", "event_type": "execution_failure",
            "severity": "critical", "summary": {"text": "Visible"},
            "occurred_at": "2026-08-14T08:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
        {
            "kind": "operation_event_projection", "key": "d" * 52,
            "agent_id": "codex-assistant", "event_type": "execution_failure",
            "severity": "critical", "summary": {"text": "Hidden"},
            "occurred_at": "2026-08-14T07:00:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
    ]
    repository = ReplicaOperationsRepository(
        "postgresql://replica", cipher=cipher,
        connect=_connect([_row(cipher, record) for record in records]),
        now=lambda: NOW, catalog=AgentCatalog.default(),
    )

    page = repository.list_events(EventFilters(), 50, 0)
    hidden = repository.list_events(
        EventFilters(agent_id="codex-assistant"), 50, 0
    )

    assert [item.agent_id for item in page.items] == ["hr-bot"]
    assert hidden.total == 0


class _BriefConnection(_Connection):
    """Serves projection rows and the generation watermark."""

    def __init__(self, rows, session_counts, committed_at=NOW):
        super().__init__(rows)
        self.session_counts = session_counts
        self.committed_at = committed_at
        self.usage_windows: list[tuple] = []

    def execute(self, sql, params=()):
        if "max(committed_at)" in sql:
            return _Result([{"committed_at": self.committed_at}])
        return super().execute(sql, params)


def _brief_repository(connection):
    catalog = AgentCatalog.default()

    def usage_reader(date_from, date_to, visibility):
        connection.usage_windows.append((date_from, date_to, visibility))
        allowed = set(catalog.ids_for_visibility(visibility))
        values = [
            UsageLeader(
                agent_id=agent_id,
                agent_name=catalog.profile(agent_id, agent_id).name,
                conversations=count,
            )
            for agent_id, count in connection.session_counts.items()
            if agent_id in allowed and not catalog.is_excluded(agent_id)
        ]
        values.sort(key=lambda item: (-item.conversations, item.agent_id))
        return tuple(values)

    return ReplicaOperationsRepository(
        "postgresql://replica",
        cipher=FieldCipher(b"m" * 32),
        connect=lambda *_args, **_kwargs: connection,
        now=lambda: NOW,
        usage_reader=usage_reader,
    )


def test_usage_leaders_delegate_to_answered_turn_reader():
    connection = _BriefConnection(
        [],
        {
            "hr-bot": 3,
            "marketing-gtm-bot": 7,
            "test-bot": 99,
            "feishu-default": 42,
        },
    )
    repository = _brief_repository(connection)

    leaders = repository.usage_leaders(
        datetime(2026, 8, 13, 8, 0, tzinfo=UTC), NOW, "business"
    )

    assert [(item.agent_id, item.conversations) for item in leaders] == [
        ("marketing-gtm-bot", 7),
        ("hr-bot", 3),
    ]
    assert [item.agent_name for item in leaders] == ["Marketing GTM", "HR Agent"]
    assert connection.usage_windows == [
        (datetime(2026, 8, 13, 8, 0, tzinfo=UTC), NOW, "business")
    ]


def test_latest_run_reports_the_last_replica_import():
    repository = _brief_repository(_BriefConnection([], {}))

    run = repository.latest_run("replica_import")

    assert run is not None
    assert run.status == "succeeded"
    assert run.finished_at == NOW
    assert repository.latest_successful_run("replica_import") == run


def test_latest_run_is_absent_before_the_first_import():
    repository = _brief_repository(_BriefConnection([], {}, committed_at=None))

    assert repository.latest_run("replica_import") is None
    assert repository.latest_successful_run("replica_import") is None


def test_brief_reports_current_freshness_and_real_conversation_counts():
    from app.operations.service import OperationsService

    records = [
        {
            "kind": "operation_event_projection", "key": "c" * 52,
            "agent_id": "hr-bot", "event_type": "execution_failure",
            "severity": "critical", "summary": {"text": "执行失败"},
            "occurred_at": "2026-08-14T07:30:00.000000Z",
            "sanitizer_policy_version": "v2",
        },
    ]
    cipher = FieldCipher(b"m" * 32)
    connection = _BriefConnection(
        [_row(cipher, record) for record in records],
        {"hr-bot": 3, "ai-fae-agent": 5, "test-bot": 99},
    )
    service = OperationsService(
        _brief_repository(connection), intervals={"replica_import": 225.0}
    )

    brief = service.brief(now=NOW)

    assert brief.freshness.status == "current"
    assert brief.usage.conversations == 8
    assert brief.usage.active_agents == 2
    assert [item.agent_id for item in brief.usage.leaders] == [
        "ai-fae-agent",
        "hr-bot",
    ]
    # A critical event still blocks the healthy claim.
    assert [item.agent_id for item in brief.attention] == ["hr-bot"]
    assert brief.can_claim_healthy is False


def test_brief_goes_stale_when_the_replica_import_falls_behind():
    from app.operations.service import OperationsService

    connection = _BriefConnection(
        [],
        {"hr-bot": 1},
        committed_at=datetime(2026, 8, 14, 7, 50, tzinfo=UTC),
    )
    service = OperationsService(
        _brief_repository(connection), intervals={"replica_import": 225.0}
    )

    brief = service.brief(now=NOW)

    # 600s behind: past the 450s stale threshold, still inside the 900s budget
    # the projection reader enforces, so the warning is visible not fatal.
    assert brief.freshness.status == "stale"
    assert brief.can_claim_healthy is False
    assert brief.usage.conversations == 1


def test_brief_can_claim_healthy_without_attention_events():
    from app.operations.service import OperationsService

    service = OperationsService(
        _brief_repository(_BriefConnection([], {"hr-bot": 2})),
        intervals={"replica_import": 225.0},
    )

    brief = service.brief(now=NOW)

    assert brief.freshness.status == "current"
    assert brief.attention == []
    assert brief.can_claim_healthy is True
    assert brief.usage.conversations == 2


class _TotalsConnection(_Connection):
    """Serves projection rows plus the generation watermark."""

    def __init__(self, rows, committed_at=NOW):
        super().__init__(rows)
        self.committed_at = committed_at

    def execute(self, sql, params=()):
        if "max(committed_at)" in sql:
            return _Result([{"committed_at": self.committed_at}])
        return super().execute(sql, params)


def _totals_record(agent_id, feedback, negative, negative_turns, positive):
    return {
        "kind": "review_feedback_totals_projection",
        "key": f"{'t' * 44}{abs(hash(agent_id)) % 10}",
        "agent_id": agent_id,
        "feedback_rows": feedback,
        "negative_rows": negative,
        "negative_turns": negative_turns,
        "positive_rows": positive,
        "observed_at": "2026-08-14T08:00:00.000000Z",
        "sanitizer_policy_version": "v2",
    }


def _review_repository(rows):
    cipher = FieldCipher(b"m" * 32)
    return ReplicaReviewRepository(
        "postgresql://replica",
        cipher=cipher,
        connect=lambda *_a, **_k: _TotalsConnection(
            [_row(cipher, record) for record in rows]
        ),
        now=lambda: NOW,
    ), cipher


def test_overview_reports_real_feedback_totals_not_the_inbox():
    # A fully triaged Agent has an empty inbox but plenty of feedback. Counting
    # the inbox would report zero feedback for the best-reviewed Agent.
    repository, _ = _review_repository([
        _totals_record("hr-bot", 40, 12, 9, 28),
        _totals_record("ai-fae-agent", 60, 20, 15, 40),
    ])

    overview = repository.overview()

    assert overview["feedback_rows"] == 100
    assert overview["negative_rows"] == 32
    assert overview["negative_turns"] == 24
    assert overview["positive_rows"] == 68
    assert overview["feedback_totals_status"] == "resolved"


def test_overview_marks_totals_unavailable_instead_of_reporting_zero():
    repository, _ = _review_repository([])

    overview = repository.overview()

    assert overview["feedback_totals_status"] == "unavailable"
    for field in ("feedback_rows", "negative_rows", "negative_turns", "positive_rows"):
        assert overview[field] is None, field


def test_overview_totals_are_agent_scoped():
    repository, _ = _review_repository([
        _totals_record("hr-bot", 40, 12, 9, 28),
        _totals_record("ai-fae-agent", 60, 20, 15, 40),
    ])

    overview = repository.overview(agent_id="hr-bot")

    assert overview["feedback_rows"] == 40
    assert overview["positive_rows"] == 28
