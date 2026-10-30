"""Host ownership and consumer-clock boundaries; actual local file, no network."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
import threading
from time import monotonic

import httpx
import pytest
from app.runtime_dashboard.health import HealthReader, HostObservationReader

NOW = datetime(2026, 10, 30, 2, tzinfo=UTC)


def host_file(tmp_path, *, observed_at=NOW, generated_at=NOW):
    path = tmp_path/'snapshot.json'
    path.write_text(json.dumps({'schema_version': 1, 'generated_at': generated_at.isoformat(),
        'observations': [{'application_id': key, 'state': 'healthy', 'observed_at': observed_at.isoformat(),
            'error': None, 'version': 'a'*40} for key in ('hr-bot', 'ai-admin-agent')]}))
    return HostObservationReader(path)


class DelayedHost:
    def __init__(self, original):
        self.original = original
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = 0
        self.block = True
    def read(self, now):
        self.calls += 1
        self.entered.set()
        if self.block:
            self.release.wait(2)
        return self.original.read(now)


async def entered(host):
    while not host.entered.is_set():
        await asyncio.sleep(.001)


@pytest.mark.parametrize('later,expected,error', [
    (NOW+timedelta(seconds=90), 'healthy', None),
    (NOW+timedelta(seconds=91), 'unknown', 'observation_stale'),
    (NOW-timedelta(seconds=1), 'unknown', 'observation_future'),
])
@pytest.mark.parametrize('cancel_first', [False, True])
def test_each_live_flight_waiter_rechecks_own_clock(tmp_path, later, expected, error, cancel_first):
    host = DelayedHost(host_file(tmp_path))
    health = HealthReader(host=host)
    async def scenario():
        first = asyncio.create_task(health.read(NOW, deadline=monotonic()+.5))
        await entered(host)
        if cancel_first:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        # Both second/third join the same running health flight, not another read.
        second = asyncio.create_task(health.read(later, deadline=monotonic()+.5))
        third = asyncio.create_task(health.read(NOW, deadline=monotonic()+.5))
        await asyncio.sleep(.01)
        host.release.set()
        actual, current = await asyncio.gather(second, third)
        if not cancel_first:
            assert (await first)['hr-bot'].runtime.state == 'healthy'
        assert host.calls == 1
        for key in ('hr-bot', 'ai-admin-agent'):
            assert actual[key].runtime.state == expected
            assert actual[key].runtime.error == error
            assert actual[key].runtime.checked_at == later
            assert current[key].runtime.state == 'healthy'
            assert current[key].runtime.checked_at == NOW
    try:
        asyncio.run(scenario())
    finally:
        host.release.set()


def test_file_generation_ttl_is_rechecked_independently_of_object_time(tmp_path):
    # Object is only 1s old for the second waiter; the containing file is 91s old.
    host = DelayedHost(host_file(tmp_path, observed_at=NOW+timedelta(seconds=90)))
    health = HealthReader(host=host)
    async def scenario():
        first = asyncio.create_task(health.read(NOW+timedelta(seconds=90), deadline=monotonic()+.5))
        await entered(host)
        second = asyncio.create_task(health.read(NOW+timedelta(seconds=91), deadline=monotonic()+.5))
        await asyncio.sleep(.01)
        host.release.set()
        before, after = await asyncio.gather(first, second)
        assert before['hr-bot'].runtime.state == 'healthy'
        assert after['hr-bot'].runtime.error == 'observation_stale'
        assert after['hr-bot'].runtime.state == 'unknown'
        assert host.calls == 1
    try:
        asyncio.run(scenario())
    finally:
        host.release.set()


def test_expired_worker_cannot_publish_to_later_waiter_or_prior_cache(tmp_path):
    host = DelayedHost(host_file(tmp_path))
    health = HealthReader(host=host)
    async def scenario():
        first = await health.read(NOW, deadline=monotonic()+.03)
        assert first['hr-bot'].runtime.state == 'unknown'
        await health._flight
        second = asyncio.create_task(health.read(NOW, deadline=monotonic()+.3))
        await asyncio.sleep(.01)
        host.release.set()
        after = await second
        assert after['hr-bot'].runtime.state == 'unknown'
        assert after['hr-bot'].runtime.observed_at is None
        assert after['hr-bot'].deployment.version is None
        assert health._prior_host == {}
        assert host.calls == 1
    try:
        asyncio.run(scenario())
    finally:
        host.release.set()


def test_host_timeout_preserves_prior_version_and_watermark_not_healthy_state(tmp_path):
    host = DelayedHost(host_file(tmp_path))
    host.block = False
    remote_calls = []
    def response(request):
        remote_calls.append(request.url.host)
        return httpx.Response(200, json={'status': 'ok'})
    health = HealthReader(host=host, urls={'ai-fae-agent': 'http://fae.local/health'},
        transport=httpx.MockTransport(response))
    async def scenario():
        good = await health.read(NOW)
        assert good['hr-bot'].deployment.version == 'a'*40
        host.block = True
        later = NOW+timedelta(seconds=1)
        failed = await health.read(later, deadline=monotonic()+.03)
        for key in ('hr-bot', 'ai-admin-agent'):
            row = failed[key]
            assert row.runtime.state == 'unknown' and row.runtime.stale
            assert row.runtime.error == 'health_unavailable'
            assert row.runtime.checked_at == later and row.runtime.observed_at == NOW
            assert row.deployment.version == 'a'*40 and row.deployment.observed_at == NOW
        assert failed['ai-fae-agent'].runtime.state == 'healthy'
        assert failed['ai-fae-agent'].runtime.observed_at == NOW
        assert remote_calls == ['fae.local'] and host.calls == 2
        host.release.set()
        await health._flight
        await asyncio.wrap_future(health._host_work._future)
        assert health._prior_host['hr-bot'].runtime.observed_at == NOW
    try:
        asyncio.run(scenario())
    finally:
        host.release.set()


def test_opaque_dict_cannot_claim_host_health_without_generation_evidence(tmp_path):
    original = host_file(tmp_path)
    class OpaqueHost:
        def read(self, now):
            return dict(original.read(now))  # deliberately loses file temporal evidence
    async def scenario():
        health = HealthReader(host=OpaqueHost())
        data = await health.read(NOW)
        assert data['hr-bot'].runtime.state == 'unknown'
        assert data['hr-bot'].runtime.error == 'observation_invalid'
        assert health._prior_host == {}
    asyncio.run(scenario())


def test_file_generation_future_is_rechecked_separately(tmp_path):
    host = DelayedHost(host_file(tmp_path, generated_at=NOW+timedelta(seconds=1)))
    health = HealthReader(host=host)
    async def scenario():
        first = asyncio.create_task(health.read(NOW+timedelta(seconds=1), deadline=monotonic()+.5))
        await entered(host)
        second = asyncio.create_task(health.read(NOW, deadline=monotonic()+.5))
        await asyncio.sleep(.01)
        host.release.set()
        after, before = await asyncio.gather(first, second)
        assert after['hr-bot'].runtime.state == 'healthy'
        assert before['hr-bot'].runtime.state == 'unknown'
        assert before['hr-bot'].runtime.error == 'observation_future'
        assert host.calls == 1
    try:
        asyncio.run(scenario())
    finally:
        host.release.set()
