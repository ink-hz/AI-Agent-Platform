"""Observation budgets and nonqueued ownership of blocking boundaries.

Cancellation abandons a waiter, never its worker. A stuck synchronous boundary
keeps its slot until it exits; subsequent callers cannot accumulate replacements.
"""
import asyncio
from concurrent.futures import Future
from threading import Lock, Thread
from time import monotonic

REQUEST_SECONDS = 4.0
SOURCE_SECONDS = 3.5


def remaining(deadline):
    seconds = deadline - monotonic()
    if seconds <= 0:
        raise TimeoutError('dashboard_deadline')
    return seconds


async def wait_for(future, deadline):
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    async with asyncio.timeout(remaining(deadline)):
        return await asyncio.shield(wrapped)


class SharedWork:
    """One in-flight synchronous job; only safe global observations belong here."""
    def __init__(self):
        self._lock = Lock()
        self._future = None

    def start(self, function):
        with self._lock:
            if self._future is None or self._future.done():
                future = self._future = Future()
                def run():
                    try:
                        future.set_result(function())
                    except BaseException as error:
                        future.set_exception(error.with_traceback(None))
                Thread(target=run, name='dashboard-observation', daemon=True).start()
            return self._future

    async def read(self, function, deadline):
        remaining(deadline)
        return await wait_for(self.start(function), deadline)


class ActorWork:
    """Fresh authorization per call; bounded admission, no queue or grant cache."""
    def __init__(self, limit=8):
        self.limit = limit
        self._lock = Lock()
        self._active = {}

    async def read(self, key, function, deadline):
        while True:
            remaining(deadline)
            with self._lock:
                previous = self._active.get(key)
                if previous is None:
                    if len(self._active) >= self.limit:
                        raise TimeoutError('dashboard_authorization_busy')
                    future = Future()
                    self._active[key] = future
                    break
            # This waits for admission only. Every admitted call rechecks grants.
            try:
                await wait_for(previous, deadline)
            except Exception:
                remaining(deadline)
        def run():
            try:
                value, error = function(), None
            except BaseException as caught:
                value, error = None, caught
            with self._lock:
                del self._active[key]
                if error is None:
                    future.set_result(value)
                else:
                    future.set_exception(error.with_traceback(None))
        Thread(target=run, name='dashboard-authorization', daemon=True).start()
        return await wait_for(future, deadline)


class DeadlineConnection:
    """Each statement consumes the same refresh budget, including fetch work."""
    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline

    def execute(self, statement, params=None):
        milliseconds = max(1, min(5000, int(remaining(self.deadline)*1000)))
        self.connection.execute(f"SET LOCAL statement_timeout = '{milliseconds}ms'")
        remaining(self.deadline)
        result = self.connection.execute(statement, params) if params is not None else self.connection.execute(statement)
        remaining(self.deadline)
        return result
