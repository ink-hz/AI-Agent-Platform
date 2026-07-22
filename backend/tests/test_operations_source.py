from datetime import datetime, timedelta, timezone

from app.fleet.catalog import AgentCatalog, AgentProfile
from app.operations.source import PsycopgOperationsSource


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)


class RecordingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[str] = []
        self.params: list[tuple] = []
        self.connection_kwargs: dict = {}

    def execute(self, statement, params):
        self.statements.append(statement)
        self.params.append(tuple(params))
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def fake_source(*, usage_rows=(), execution_rows=(), catalog=None):
    rows = list(usage_rows or execution_rows)
    cursor = RecordingCursor(rows)
    def connect(*_args, **kwargs):
        cursor.connection_kwargs = kwargs
        return RecordingConnection(cursor)

    source = PsycopgOperationsSource(
        "postgresql://unused",
        connect=connect,
        catalog=catalog,
    )
    return source, cursor


def test_usage_query_returns_answered_turns_only():
    source, cursor = fake_source(
        usage_rows=[
            {
                "turn_key": "metabot:turn-1",
                "agent_id": "hr-bot",
                "source_kind": "metabot",
                "created_at": NOW,
            }
        ]
    )

    occurrences = source.fetch_usage(NOW - timedelta(minutes=5), NOW)

    assert occurrences[0].turn_key == "metabot:turn-1"
    assert occurrences[0].agent_id == "hr-bot"
    assert "nullif(btrim(t.answer), '') is not null" in cursor.statements[0]
    assert cursor.params == [(NOW - timedelta(minutes=5), NOW)]
    assert "default_transaction_read_only=on" in cursor.connection_kwargs["options"]
    assert "statement_timeout=5000" in cursor.connection_kwargs["options"]
    assert cursor.connection_kwargs["connect_timeout"] == 3
    assert cursor.connection_kwargs["row_factory"] is not None


def test_usage_query_uses_ingestion_watermark_but_preserves_event_time():
    occurred_at = NOW - timedelta(days=2)
    source, cursor = fake_source(
        usage_rows=[
            {
                "turn_key": "fae:late-turn",
                "agent_id": "ai-fae-agent",
                "source_kind": "fae",
                "created_at": occurred_at,
                "source_synced_at": NOW,
            }
        ]
    )

    occurrences = source.fetch_usage(NOW - timedelta(hours=1), NOW)

    statement = cursor.statements[0].lower()
    assert "coalesce(t.source_synced_at, t.created_at) > %s" in statement
    assert "coalesce(t.source_synced_at, t.created_at) <= %s" in statement
    assert occurrences[0].occurred_at == occurred_at


def test_execution_query_emits_only_supported_explicit_signals():
    source, cursor = fake_source(
        execution_rows=[
            {
                "turn_key": "fae:turn-1",
                "session_key": "fae:session-1",
                "agent_id": "ai-fae-agent",
                "source_kind": "fae",
                "created_at": NOW,
                "signal_type": "fallback",
            }
        ]
    )

    signals = source.fetch_execution(NOW - timedelta(minutes=5), NOW)

    assert [item.signal_type for item in signals] == ["fallback"]
    statement = cursor.statements[0].lower()
    assert statement.count("union all") == 3
    assert "platform_read.traces" in statement
    assert "platform_read.trace_steps" in statement
    assert cursor.params == [
        (NOW - timedelta(minutes=5), NOW) * 4,
    ]


def test_execution_query_uses_ingestion_watermark_but_preserves_event_time():
    occurred_at = NOW - timedelta(days=2)
    source, cursor = fake_source(
        execution_rows=[
            {
                "turn_key": "fae:late-turn",
                "session_key": "fae:session-1",
                "agent_id": "ai-fae-agent",
                "source_kind": "fae",
                "created_at": occurred_at,
                "source_synced_at": NOW,
                "signal_type": "fallback",
            }
        ]
    )

    signals = source.fetch_execution(NOW - timedelta(hours=1), NOW)

    statement = cursor.statements[0].lower()
    after_clause = "coalesce(t.source_synced_at, t.created_at) > %s"
    through_clause = "coalesce(t.source_synced_at, t.created_at) <= %s"
    assert statement.count(after_clause) == 4
    assert statement.count(through_clause) == 4
    assert signals[0].occurred_at == occurred_at


def test_source_deduplicates_signals_and_discards_unknown_agents():
    catalog = AgentCatalog(
        profiles={
            "known-agent": AgentProfile(
                id="known-agent",
                name="原始名称",
                domain="业务",
                description="说明",
                glyph="AI",
                accent="blue",
                visibility="system",
            )
        },
        aliases={},
        unresolved_aliases=set(),
    )
    duplicate = {
        "turn_key": "metabot:turn-1",
        "session_key": "metabot:session-1",
        "agent_id": "known-agent",
        "source_kind": "metabot",
        "created_at": NOW,
        "signal_type": "tool_error",
    }
    source, _ = fake_source(
        execution_rows=[
            duplicate,
            dict(duplicate),
            {
                **duplicate,
                "turn_key": "metabot:turn-2",
                "agent_id": "unknown-agent",
            },
        ],
        catalog=catalog,
    )

    signals = source.fetch_execution(NOW - timedelta(minutes=5), NOW)

    assert len(signals) == 1
    assert signals[0].agent_id == "known-agent"
    assert signals[0].agent_name == "原始名称"
    assert signals[0].agent_visibility == "system"


def test_usage_source_discards_unknown_agents():
    catalog = AgentCatalog(profiles={}, aliases={}, unresolved_aliases=set())
    source, _ = fake_source(
        usage_rows=[
            {
                "turn_key": "metabot:turn-1",
                "agent_id": "unknown-agent",
                "source_kind": "metabot",
                "created_at": NOW,
            }
        ],
        catalog=catalog,
    )

    assert source.fetch_usage(NOW - timedelta(minutes=5), NOW) == ()
