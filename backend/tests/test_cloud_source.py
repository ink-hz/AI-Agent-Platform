from datetime import UTC, datetime, timedelta

from app.cloud_replica.source import (
    ATTACHMENT_SQL,
    SESSION_SQL,
    TRACE_SQL,
    TRACE_STEP_SQL,
    TURN_SQL,
    ReplicaSource,
)
from app.observability.models import Page
from app.operations.models import EventFilters, OperationalEvent


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
