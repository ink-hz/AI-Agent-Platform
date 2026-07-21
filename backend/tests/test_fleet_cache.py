from datetime import datetime, timezone

import pytest

from app.fleet.cache import UsageCache
from app.fleet.repository import FlywheelReadError, UsageSnapshot


CHECKED_AT = datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)
SNAPSHOT = UsageSnapshot(records=(), trend=(), checked_at=CHECKED_AT)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


class Repository:
    def __init__(self):
        self.calls = 0
        self.error: Exception | None = None

    def fetch_usage(self):
        self.calls += 1
        if self.error:
            raise self.error
        return SNAPSHOT


@pytest.mark.asyncio
async def test_cache_reuses_data_for_60_seconds():
    repository = Repository()
    clock = Clock()
    cache = UsageCache(repository, ttl_seconds=60, clock=clock)

    first = await cache.get()
    second = await cache.get()

    assert first.source.healthy is True
    assert second.snapshot == first.snapshot
    assert repository.calls == 1


@pytest.mark.asyncio
async def test_cache_preserves_last_success_when_refresh_fails():
    repository = Repository()
    clock = Clock()
    cache = UsageCache(repository, ttl_seconds=60, clock=clock)
    first = await cache.get()
    clock.advance(61)
    repository.error = FlywheelReadError("flywheel query failed")

    stale = await cache.get()

    assert stale.snapshot == first.snapshot
    assert stale.source.healthy is False
    assert stale.source.stale is True
    assert stale.source.error == "usage_unavailable"
    assert stale.source.checked_at == CHECKED_AT.isoformat()


@pytest.mark.asyncio
async def test_first_failure_returns_unavailable_without_fake_snapshot():
    repository = Repository()
    repository.error = FlywheelReadError("flywheel query failed")
    cache = UsageCache(repository, ttl_seconds=60, clock=Clock())

    result = await cache.get()

    assert result.snapshot is None
    assert result.source.healthy is False
    assert result.source.stale is False
    assert result.source.checked_at is None
    assert result.source.error == "usage_unavailable"
