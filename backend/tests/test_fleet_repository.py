from datetime import date, datetime, timezone

import pytest

from app.fleet.repository import FlywheelReadError, PsycopgFlywheelRepository


class FakeConnect:
    def __init__(self, responses):
        self.responses = list(responses)
        self.executed_sql: list[str] = []
        self.current = []

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def execute(self, sql, _params=None):
        self.executed_sql.append(sql)
        self.current = self.responses.pop(0)
        return self

    def fetchall(self):
        return self.current


def test_usage_query_counts_distinct_assistant_turns_and_maps_rows():
    checked_at = datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)
    fake = FakeConnect(
        [
            [
                {
                    "bot_id": "hr-bot",
                    "total_conversations": 14,
                    "conversations_last_7d": 4,
                    "conversations_previous_7d": 2,
                    "last_activity_at": checked_at,
                    "recent_summary": "入职需要哪些材料？",
                }
            ],
            [{"bot_id": "hr-bot", "date": date(2026, 7, 21), "conversations": 4}],
        ]
    )
    repository = PsycopgFlywheelRepository(
        "postgresql://unused",
        connect=fake,
        now=lambda: checked_at,
    )

    snapshot = repository.fetch_usage()

    sql = " ".join(fake.executed_sql).lower()
    assert "flywheel_analytics.messages" in sql
    assert "flywheel_analytics.conversations" in sql
    assert "role = 'assistant'" in sql
    assert "count(distinct" in sql
    assert "at time zone 'asia/shanghai'" in sql
    assert "interval '7 days'" not in sql
    assert "::date - 6" in sql
    assert "::date - 13" in sql
    assert "flywheel_core" not in sql
    assert snapshot.checked_at == checked_at
    assert snapshot.records[0].bot_id == "hr-bot"
    assert snapshot.records[0].total_conversations == 14
    assert snapshot.records[0].last_activity_at == checked_at
    assert snapshot.trend[0].date == date(2026, 7, 21)
    assert snapshot.trend[0].bot_id == "hr-bot"
    assert snapshot.trend[0].conversations == 4


def test_repository_wraps_driver_errors_without_secret():
    def broken(*_args, **_kwargs):
        raise RuntimeError("postgresql://user:password@localhost/private")

    repository = PsycopgFlywheelRepository(
        "postgresql://secret",
        connect=broken,
    )

    with pytest.raises(FlywheelReadError, match="flywheel query failed") as error:
        repository.fetch_usage()

    assert "password" not in str(error.value)
    assert "postgresql" not in str(error.value)


def test_repository_wraps_invalid_rows_for_cache_degradation():
    fake = FakeConnect(
        [
            [{"bot_id": "hr-bot", "total_conversations": "not-a-number"}],
            [],
        ]
    )
    repository = PsycopgFlywheelRepository("postgresql://unused", connect=fake)

    with pytest.raises(FlywheelReadError, match="flywheel query failed"):
        repository.fetch_usage()
