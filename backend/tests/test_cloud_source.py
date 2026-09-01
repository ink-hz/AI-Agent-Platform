from datetime import UTC, datetime, timedelta

from app.cloud_replica.source import (
    ATTACHMENT_SQL,
    REVIEW_EVENT_SQL,
    REVIEW_EVIDENCE_SQL,
    REVIEW_INBOX_SQL,
    REVIEW_ISSUE_SQL,
    REVIEW_LINK_SQL,
    REVIEW_REPLAY_SQL,
    SESSION_SQL,
    TRACE_SQL,
    TRACE_STEP_SQL,
    TURN_SQL,
    ReplicaSource,
)
from app.observability.models import Page
from app.operations.models import EventFilters, OperationalEvent
from app.review.scope_sql import (
    CANONICAL_EVENT_PAIR_INVALID_SQL,
    HISTORICAL_LINK_EVENT_INVALID_SQL,
)


def test_source_queries_are_explicit_bounded_and_never_touch_restricted_fields():
    statements = (SESSION_SQL, TURN_SQL, ATTACHMENT_SQL, TRACE_SQL, TRACE_STEP_SQL)
    combined = "\n".join(statements).lower()

    assert "select *" not in combined
    for forbidden in (
        "details",
        "sources",
        "input_summary",
        "output_summary",
        "safe_metadata",
        "error_summary",
        "review_writer",
        "attachment_objects",
        "provider_",
    ):
        assert forbidden not in combined
    assert "last_active_at >= %(retention_floor)s" in SESSION_SQL
    assert (
        "greatest(last_active_at, coalesce(source_synced_at, last_active_at))"
        " as replica_updated_at"
    ) in " ".join(SESSION_SQL.lower().split())
    assert "(replica_updated_at, session_key) > (%(after)s, %(after_key)s)" in SESSION_SQL
    assert "replica_updated_at <= %(through)s" in SESSION_SQL
    assert "order by replica_updated_at, session_key" in SESSION_SQL.lower()
    for statement in (TURN_SQL, ATTACHMENT_SQL, TRACE_SQL, TRACE_STEP_SQL):
        assert "%(through)s" in statement
    assert "join platform_read.turns" in ATTACHMENT_SQL.lower()
    assert "a.turn_key = t.native_id" in ATTACHMENT_SQL.lower()
    assert "t.turn_key as turn_key" in ATTACHMENT_SQL.lower()


def test_review_projection_proves_nested_scope_and_inbox_issue_ownership():
    issue_sql = " ".join(REVIEW_ISSUE_SQL.lower().split())
    inbox_sql = " ".join(REVIEW_INBOX_SQL.lower().split())

    for proof in (
        "issue.origin_turn_key",
        "linked_turn.agent_id is distinct from issue.agent_id",
        "canonical.agent_id is distinct from issue.agent_id",
        "walk.cycle",
        "linked_turn.source_kind is distinct from 'fae'",
        "as scope_valid",
    ):
        assert proof in issue_sql
    assert "join platform_review.feedback_issues linked_issue" in inbox_sql
    assert "linked_issue.agent_id=feedback.agent_id" in inbox_sql
    assert "where link.issue_id=issue.id and link.active and (" not in issue_sql
    assert "feedback_replay_runs replay" in issue_sql
    for event_type in (
        "turn_linked",
        "turn_linked_from_release_handoff",
        "link_moved_in",
        "link_moved_out",
    ):
        assert event_type in issue_sql
    assert "true as scope_valid" in inbox_sql


def test_cloud_issue_scope_binds_move_direction_link_identity_and_referenced_issues():
    source = " ".join(REVIEW_ISSUE_SQL.lower().split())
    predicate = " ".join(HISTORICAL_LINK_EVENT_INVALID_SQL.lower().split())

    assert predicate in source
    assert "event.before->>'id' is distinct from event.after->>'id'" in source
    assert "event.before->>'issue_id' is distinct from issue.id::text" in source
    assert "event.after->>'issue_id' is distinct from issue.id::text" in source
    assert "historical_before_issue.agent_id is distinct from issue.agent_id" in source
    assert "historical_after_issue.agent_id is distinct from issue.agent_id" in source
    assert "historical_link.id is null" in source
    assert "merge_walk.current_id=historical_link.issue_id" in source


def test_cloud_issue_scope_uses_shared_canonical_event_pair_audit():
    source = " ".join(REVIEW_ISSUE_SQL.lower().split())
    predicate = " ".join(CANONICAL_EVENT_PAIR_INVALID_SQL.lower().split())

    assert predicate in source
    assert "issue.canonical_issue_id is not null and not exists" in source
    assert "canonical_source.issue_id=issue.id" in source
    assert "canonical_target.issue_id=issue.canonical_issue_id" in source
    assert "canonical_source.event_type='issue_merged'" in source
    assert "canonical_target.event_type='issue_absorbed'" in source


def test_cloud_issue_scope_qualifies_unnested_feedback_key():
    source = " ".join(REVIEW_ISSUE_SQL.lower().split())

    assert (
        "from unnest(link.source_feedback_keys) as stored_feedback(feedback_key)"
        in source
    )
    assert (
        "linked_feedback.feedback_key=stored_feedback.feedback_key"
        in source
    )
    assert "linked_feedback.feedback_key=feedback_key" not in source


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Cursor(_Context):
    def __init__(self, rows_by_marker, calls):
        self._rows_by_marker = rows_by_marker
        self._calls = calls
        self._rows = []

    def execute(self, sql, params=None):
        self._calls.append((sql, params))
        marker = next((key for key in self._rows_by_marker if key in sql), None)
        self._rows = self._rows_by_marker.get(marker, [])
        return self

    def fetchall(self):
        return self._rows


class _Connection(_Context):
    def __init__(self, rows_by_marker, calls):
        self._rows_by_marker = rows_by_marker
        self._calls = calls

    def transaction(self):
        self._calls.append(("TRANSACTION", None))
        return _Context()

    def cursor(self):
        return _Cursor(self._rows_by_marker, self._calls)


def test_source_uses_read_only_repeatable_read_and_one_upper_watermark():
    now = datetime(2026, 8, 11, tzinfo=UTC)
    calls = []
    connect_arguments = {}
    rows = {
        "from platform_read.sessions": [
            {
                "session_key": "s1",
                "agent_id": "hr-bot",
                "source_kind": "metabot",
                "channel": "feishu",
                "title": "title",
                "user_identity": "raw-user",
                "created_at": now,
                "last_active_at": now - timedelta(days=3),
                "replica_updated_at": now,
                "primary_sender_name": "洛奇",
                "primary_sender_department": "市场部",
            }
        ],
        "from platform_read.turns": [
            {
                "turn_key": "t1",
                "session_key": "s1",
                "turn_index": 1,
                "question": "q",
                "answer": "a",
                "created_at": now,
                "question_at": now,
                "answer_at": now,
                "question_time_status": "exact",
                "answer_time_status": "exact",
                "outcome": "success",
                "fallback_used": False,
                "duration_ms": 100,
                "trace_key": "tr1",
            }
        ],
        "from platform_read.attachments": [],
        "from platform_read.traces": [],
        "from platform_read.trace_steps": [],
    }

    def connect(dsn, **kwargs):
        connect_arguments.update(dsn=dsn, **kwargs)
        return _Connection(rows, calls)

    source = ReplicaSource("postgresql://safe", connection_factory=connect)
    result = source.fetch_sessions(
        after=now - timedelta(minutes=5),
        after_key="s0",
        through=now,
        limit=10,
    )

    assert len(result) == 1
    assert result[0].turns[0].question == "q"
    assert result[0].last_active_at == now - timedelta(days=3)
    assert result[0].replication_cursor_at == now
    assert "default_transaction_read_only=on" in connect_arguments["options"]
    assert "statement_timeout=10000" in connect_arguments["options"]
    assert calls[0][0] == "TRANSACTION"
    assert "repeatable read" in calls[1][0].lower()
    query_params = [params for sql, params in calls if "platform_read." in sql]
    assert query_params
    assert all(params["through"] == now for params in query_params)
    assert query_params[0]["after_key"] == "s0"
    assert query_params[0]["retention_floor"] == now - timedelta(days=365)


def test_source_rejects_invalid_window_before_connecting():
    called = False

    def connect(*_args, **_kwargs):
        nonlocal called
        called = True

    now = datetime(2026, 8, 11, tzinfo=UTC)
    source = ReplicaSource("postgresql://safe", connection_factory=connect)

    try:
        source.fetch_sessions(
            after=now,
            after_key="",
            through=now - timedelta(seconds=1),
            limit=1,
        )
    except ValueError as error:
        assert str(error) == "invalid replica source window"
    else:
        raise AssertionError("invalid window was accepted")
    assert called is False


def test_management_source_includes_platform_level_operation_events():
    now = datetime(2026, 8, 11, tzinfo=UTC)
    calls = []

    class Operations:
        def list_events(self, filters: EventFilters, limit: int, offset: int):
            assert filters.date_to == now
            assert (limit, offset) == (10_000, 0)
            return Page(
                items=[OperationalEvent(
                    event_id="event-1",
                    agent_id=None,
                    agent_visibility="business",
                    event_type="data_access_recovered",
                    event_family="recovery",
                    severity="info",
                    status="historical",
                    title="flywheel data access recovered",
                    summary="The required business-data source is readable again.",
                    source_kind="flywheel",
                    occurred_at=now,
                    first_observed_at=now,
                    last_observed_at=now,
                    facts={"available": True},
                    fingerprint="data:flywheel:unavailable:recovered",
                )],
                total=1,
                limit=10_000,
                offset=0,
            )

    source = ReplicaSource(
        "postgresql://safe",
        connection_factory=lambda *_args, **_kwargs: _Connection({}, calls),
        operations_repository=Operations(),
    )

    projections = source.fetch_management_projections(through=now)

    operation = next(
        item for item in projections if item.__class__.__name__ == "OperationEventProjection"
    )
    assert operation.agent_id is None
    assert operation.event_family == "recovery"
    assert operation.status == "historical"
    assert operation.title == "flywheel data access recovered"
    assert operation.source_kind == "flywheel"


def test_management_source_marks_refreshed_inbox_scope_valid():
    now = datetime(2026, 8, 11, tzinfo=UTC)
    calls = []
    connect_arguments = {}
    rows = {
        "with recursive canonical_walk": [],
        "select feedback.agent_id": [{
            "agent_id": "ai-fae-agent",
            "turn_key": "fae:turn-1",
            "feedback_count": 1,
            "first_feedback_at": now,
            "scope_valid": True,
        }],
        "select agent_id,": [],
    }
    def connect(dsn, **kwargs):
        connect_arguments.update(dsn=dsn, **kwargs)
        return _Connection(rows, calls)

    source = ReplicaSource("postgresql://safe", connection_factory=connect)

    projections = source.fetch_management_projections(through=now)
    inbox = next(
        item for item in projections
        if item.__class__.__name__ == "ReviewInboxProjection"
    )

    assert inbox.scope_valid is True
    assert "statement_timeout=30000" in connect_arguments["options"]


def test_management_source_projects_complete_feedback_repair_chain():
    now = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    issue_id = "2c387d17-f386-4df7-86fd-64c4676ec40a"
    link_id = "d0239736-85ab-429d-81cb-3566737a7b16"
    rows = {
        "with recursive canonical_walk": [{
            "id": issue_id, "agent_id": "ai-fae-agent",
            "status": "actionable", "priority": "P1", "title": "资料缺口",
            "failure_layer": "coverage", "owner": "FAE", "created_at": now,
            "updated_at": now, "linked_turn_count": 1,
            "linked_turn_keys": ["fae:turn-1"], "scope_valid": True,
            "origin_turn_key": "fae:turn-1", "secondary_layers": [],
            "root_cause": "资料没有覆盖", "impact_scope": "现场排障",
            "fix_ready_at": None, "disposition": "actionable",
            "canonical_issue_id": None, "disposition_reason": "", "row_version": 1,
        }],
        "from platform_review.feedback_issue_links detail_link": [{
            "id": link_id, "issue_id": issue_id, "agent_id": "ai-fae-agent",
            "source_turn_key": "fae:turn-1", "source_feedback_keys": [],
            "link_role": "primary", "linked_by": "owner", "linked_at": now,
            "active": True, "link_reason": "negative feedback",
            "source_question": "怎么处理", "source_answer": "旧回答",
            "source_turn_index": 1, "source_session_key": "fae:session-1",
            "source_created_at": now, "source_details": {}, "source_sources": [],
            "source_trace_key": "fae:trace-1", "source_outcome": "answered",
            "source_fallback_used": False, "source_context": [],
        }],
        "from platform_review.feedback_fix_evidence detail_evidence": [{
            "id": "f8cd7728-1acc-4b48-af95-ea02cd0e8b56", "issue_id": issue_id,
            "evidence_type": "merge", "repository": "AI-FAE-Agent",
            "reference": "修复提交", "url": "", "version": "",
            "commit_sha": "a" * 40, "release_manifest_ref": "", "environment": "",
            "observed_at": now, "observed_by": "owner",
            "verification_status": "pending", "verification_details": {},
        }],
        "from platform_review.feedback_replay_runs detail_replay": [{
            "id": "0c63d565-8c5a-4e80-936c-3db37829c218", "issue_id": issue_id,
            "issue_link_id": link_id, "attempt_no": 1, "expected_version": "v1",
            "actual_version": "v1", "expected_git_sha": "a" * 40,
            "actual_git_sha": "a" * 40, "configured_model": "claude-opus",
            "actual_model": "claude-opus", "answer": "修复后回答", "sources": [],
            "done": {}, "trace_id": "trace", "duration_ms": 100,
            "execution_status": "succeeded", "runtime_gate": "passed",
            "runtime_failure_reason": "", "semantic_verdict": "passed",
            "review_method": "human_fae", "reviewer": "owner",
            "review_reason": "通过", "started_at": now, "completed_at": now,
        }],
        "from platform_review.feedback_issue_events detail_event": [{
            "id": "79de04d1-4444-4382-ac58-b6c3533ece56", "issue_id": issue_id,
            "event_type": "issue_created", "actor": "owner", "reason": "反馈建项",
            "before": {}, "after": {"status": "pending_triage"}, "created_at": now,
        }],
        "select feedback.agent_id": [],
        "select agent_id,": [],
    }
    calls = []
    source = ReplicaSource(
        "postgresql://safe",
        connection_factory=lambda *_args, **_kwargs: _Connection(rows, calls),
    )

    projection = next(
        value for value in source.fetch_management_projections(through=now)
        if value.__class__.__name__ == "ReviewIssueProjection"
    )

    assert projection.detail_schema_version == 1
    assert projection.origin_turn_key == "fae:turn-1"
    assert projection.links[0]["source_session_key"] == "fae:session-1"
    assert projection.evidence[0]["reference"] == "修复提交"
    assert projection.replays[0]["answer"] == "修复后回答"
    assert projection.events[0]["reason"] == "反馈建项"
    assert projection.progress["status"] == "fixing"
    statements = "\n".join(sql for sql, _params in calls)
    for statement in (
        REVIEW_LINK_SQL, REVIEW_EVIDENCE_SQL, REVIEW_REPLAY_SQL, REVIEW_EVENT_SQL
    ):
        assert statement in statements
