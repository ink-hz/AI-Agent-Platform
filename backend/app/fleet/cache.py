import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from .models import DataSourceStatus
from .repository import FlywheelReadError, FlywheelRepository, UsageSnapshot


@dataclass(frozen=True)
class CachedUsage:
    snapshot: UsageSnapshot | None
    source: DataSourceStatus


class UsageCache:
    def __init__(
        self,
        repository: FlywheelRepository,
        *,
        ttl_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._expires_at = float("-inf")
        self._value: CachedUsage | None = None

    async def get(self) -> CachedUsage:
        if self._value is not None and self._clock() < self._expires_at:
            return self._value

        async with self._lock:
            if self._value is not None and self._clock() < self._expires_at:
                return self._value
            try:
                snapshot = await asyncio.to_thread(self._repository.fetch_usage)
            except FlywheelReadError:
                previous = self._value.snapshot if self._value else None
                self._value = CachedUsage(
                    snapshot=previous,
                    source=DataSourceStatus(
                        healthy=False,
                        checked_at=(
                            previous.checked_at.isoformat() if previous else None
                        ),
                        stale=previous is not None,
                        error="usage_unavailable",
                    ),
                )
            else:
                self._value = CachedUsage(
                    snapshot=snapshot,
                    source=DataSourceStatus(
                        healthy=True,
                        checked_at=snapshot.checked_at.isoformat(),
                    ),
                )
            self._expires_at = self._clock() + self._ttl_seconds
            return self._value
