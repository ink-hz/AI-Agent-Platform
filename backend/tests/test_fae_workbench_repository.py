from datetime import UTC, date, datetime, timedelta

import pytest

from app.fae_workbench.repository import (
    FAE_SOURCE_ENVIRONMENT,
    FaeWorkbenchReadError,
    PsycopgFaeWorkbenchRepository,
)


NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
PERIOD_START = NOW - timedelta(days=7)
PERIOD_END = NOW


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        if self.connection.error:
            raise RuntimeError(self.connection.raw_user_id)
        self.connection.executed.append((statement, params))
        self.rows = self.connection.responses.pop(0)
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, responses, *, error=False, raw_user_id="person-12345"):
        self.responses = list(responses)
        self.error = error
        self.raw_user_id = raw_user_id
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self)


def repository_with_rows(*, summary, trend, attention):
    connection = _Connection(([summary], trend, attention))
    repository = PsycopgFaeWorkbenchRepository(
        "postgresql://platform", connect=lambda *_args, **_kwargs: connection
    )
    return repository, connection


def repository_with_turn_exists(found):
    connection = _Connection(([{"turn_key": "fae:turn-1"}] if found else [],))
    repository = PsycopgFaeWorkbenchRepository(
        "postgresql://platform", connect=lambda *_args, **_kwargs: connection
    )
    return repository, connection


def test_snapshot_uses_one_period_and_keeps_feedback_units_distinct():
    repository, connection = repository_with_rows(
        summary={
            "session_count": 12,
            "active_subject_count": 7,
            "negative_feedback_events": 3,
            "negative_turn_count": 2,
            "abnormal_session_count": 1,
            "p50_duration_ms": 820,
            "p95_duration_ms": 3100,
            "data_as_of": NOW,
        },
        trend=[{"day": date(2026, 8, 31), "sessions": 12, "negative_turns": 2}],
        attention=[{
            "session_key": "fae:session-1",
            "title": "设备掉线",
            "last_active_at": NOW,
            "reason": "fallback",
        }],
    )

    result = repository.snapshot(PERIOD_START, PERIOD_END)

    assert result.session_count == 12
    assert result.negative_feedback_events == 3
    assert result.negative_turn_count == 2
    assert result.data_as_of == NOW
    normalized = " ".join(connection.executed[0][0].split())
    assert "s.agent_id = 'ai-fae-agent'" in normalized
    assert "s.source_kind = 'fae'" in normalized
    assert connection.executed[0][1] == (PERIOD_START, PERIOD_END)
    assert connection.executed[1][1] == (PERIOD_START, PERIOD_END)
    assert connection.executed[2][1] == (PERIOD_START, PERIOD_END, 10)


def test_turn_scope_requires_both_fae_agent_and_source():
    repository, connection = repository_with_turn_exists(True)

    assert repository.fae_turn_exists("fae:turn-1") is True

    statement, params = connection.executed[-1]
    assert "agent_id='ai-fae-agent'" in "".join(statement.split())
    assert "source_kind='fae'" in "".join(statement.split())
    assert params == (["fae:turn-1"],)


def test_two_hundred_turn_keys_use_one_bounded_query():
    keys = [f"fae:turn-{index}" for index in range(200)]
    connection = _Connection(([{"turn_key": key} for key in keys],))
    repository = PsycopgFaeWorkbenchRepository(
        "postgresql://platform", connect=lambda *_args, **_kwargs: connection
    )

    assert repository.fae_turn_keys(keys) == set(keys)
    assert len(connection.executed) == 1
    assert "turn_key=any(%s)" in "".join(connection.executed[0][0].split())
    assert connection.executed[0][1] == (keys,)


def test_local_repository_rejects_nonproduction_environment():
    with pytest.raises(ValueError, match="^fae_workbench_production_required$"):
        PsycopgFaeWorkbenchRepository(
            "postgresql://platform", source_environment="staging"
        )

    assert FAE_SOURCE_ENVIRONMENT == "production"


def test_snapshot_preserves_unavailable_freshness_and_null_duration_percentiles():
    repository, connection = repository_with_rows(
        summary={
            "session_count": 0,
            "active_subject_count": 0,
            "negative_feedback_events": 0,
            "negative_turn_count": 0,
            "abnormal_session_count": 0,
            "p50_duration_ms": None,
            "p95_duration_ms": None,
            "data_as_of": None,
        },
        trend=[],
        attention=[],
    )

    result = repository.snapshot(PERIOD_START, PERIOD_END)

    assert result.data_as_of is None
    assert result.p50_duration_ms is None
    assert result.p95_duration_ms is None
    assert "percentile_cont(0.5)" in connection.executed[0][0]
    assert "duration_ms >= 0" in connection.executed[0][0]
    assert "t.turn_key is not null" in connection.executed[0][0]


def test_query_failures_are_private_and_stable():
    raw_user_id = "person-very-private-12345"
    connection = _Connection([], error=True, raw_user_id=raw_user_id)
    repository = PsycopgFaeWorkbenchRepository(
        "postgresql://platform", connect=lambda *_args, **_kwargs: connection
    )

    with pytest.raises(FaeWorkbenchReadError, match="^fae_workbench_query_failed$") as error:
        repository.snapshot(PERIOD_START, PERIOD_END)

    assert raw_user_id not in str(error.value)
