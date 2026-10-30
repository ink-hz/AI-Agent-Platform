"""Trusted observations only; no SSH, container discovery or business calls."""
import json
import os
import stat
import re
from datetime import datetime, timedelta
from pathlib import Path
from pydantic import AwareDatetime, Field

from .models import Model, RuntimeEvidence, Deployment, HealthObservation

HOST_IDS = ('hr-bot', 'ai-admin-agent')


class _HostRow(Model):
    application_id: str
    state: str
    observed_at: AwareDatetime
    error: str | None
    version: str | None


class _HostFile(Model):
    schema_version: int
    generated_at: AwareDatetime
    observations: list[_HostRow] = Field(max_length=2)


class HostObservationReader:
    def __init__(self, path=Path('/run/platform-runtime/snapshot.json'), *, ttl_seconds=90):
        self.path = Path(path)
        self.ttl = timedelta(seconds=ttl_seconds)

    def read(self, now: datetime) -> dict[str, HealthObservation]:
        def unknown(error, observed_at=None):
            return HealthObservation(runtime=RuntimeEvidence(method='host_loopback',
                checked_at=now, observed_at=observed_at, error=error,
                stale=error == 'observation_stale'))
        try:
            # Open cannot wait for a FIFO writer; nofollow rejects a replaced link.
            # Validate the opened object, not a racy pre-open path.is_file check.
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16_384:
                    raise ValueError
                raw = os.read(descriptor, 16_385)
            finally:
                os.close(descriptor)
            if len(raw) > 16_384:
                raise ValueError
            document = _HostFile.model_validate(json.loads(raw))
            ids = [row.application_id for row in document.observations]
            if document.schema_version != 1 or len(set(ids)) != len(ids) or set(ids) - set(HOST_IDS):
                raise ValueError
            for row in document.observations:
                if row.state not in {'healthy', 'degraded', 'offline', 'unknown'}:
                    raise ValueError
                if row.version is not None and not re.fullmatch('[0-9a-f]{40}', row.version):
                    raise ValueError
                if row.error is not None and not re.fullmatch('[a-z][a-z0-9_]{0,79}', row.error):
                    raise ValueError
        except FileNotFoundError:
            return {key: unknown('observation_missing') for key in HOST_IDS}
        except (OSError, ValueError, TypeError):
            return {key: unknown('observation_invalid') for key in HOST_IDS}
        result = {key: unknown('observation_missing') for key in HOST_IDS}
        for row in document.observations:
            if row.observed_at > now or document.generated_at > now:
                result[row.application_id] = unknown('observation_future', row.observed_at)
            elif now - row.observed_at > self.ttl or now - document.generated_at > self.ttl:
                result[row.application_id] = unknown('observation_stale', row.observed_at)
            else:
                result[row.application_id] = HealthObservation(
                    runtime=RuntimeEvidence(coverage='observed', state=row.state,
                        observed_at=row.observed_at, checked_at=now, method='host_loopback', error=row.error),
                    deployment=Deployment(version=row.version, observed_at=row.observed_at if row.version else None,
                        evidence='ready_release_sha' if row.version else None))
        return result


class HealthReader:
    """Per-process single-flight observations, independent timestamps and failures."""
    def __init__(self, *, urls=None, voc_client=None, host=None, transport=None, ttl_seconds=30):
        import asyncio
        self.urls = urls or {}
        self.voc_client = voc_client
        self.host = host or HostObservationReader()
        self.transport = transport
        self.ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._expires = 0
        self._cached = {}
        from urllib.parse import urlsplit
        for key, url in self.urls.items():
            parsed = urlsplit(url)
            if key not in {'ai-fae-agent','ai-daq-fae-agent'} or parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/health':
                raise ValueError('trusted_health_url_invalid')

    async def read(self, now):
        import asyncio
        from time import monotonic
        async with self._lock:
            if monotonic() < self._expires:
                # File freshness is checked on every read, including the 90s boundary.
                values = dict(self._cached)
                values.update(await asyncio.to_thread(self.host.read,now))
                return values
            async def probe(key):
                runtime = RuntimeEvidence(coverage='not_configured', checked_at=now,
                    method='safe_http', error='health_not_configured')
                if key not in self.urls and not (key == 'voc' and self.voc_client):
                    return key, HealthObservation(runtime=runtime)
                try:
                    async with asyncio.timeout(3):
                        if key == 'voc':
                            response = await self.voc_client.health()
                            status = response.status_code
                            raw = response.body
                        else:
                            import httpx
                            async with httpx.AsyncClient(timeout=2,follow_redirects=False,
                                    transport=self.transport,trust_env=False) as client:
                                async with client.stream('GET',self.urls[key]) as response:
                                    status = response.status_code
                                    raw = bytearray()
                                    async for chunk in response.aiter_bytes():
                                        raw.extend(chunk)
                                        if len(raw) > 16_384:
                                            raise ValueError('health_payload_limit')
                        if status == 200:
                            body = json.loads(raw)
                            healthy = isinstance(body,dict) and (body.get('status') in {'ok','healthy','ready'} or body.get('ready') is True)
                            runtime = RuntimeEvidence(coverage='observed',state='healthy' if healthy else 'degraded',
                                observed_at=now,checked_at=now,method='safe_http',error=None if healthy else 'health_not_ready')
                        else:
                            runtime = RuntimeEvidence(coverage='observed' if status >= 500 else 'not_observed',state='degraded' if status >= 500 else 'unknown',
                                observed_at=now,checked_at=now,method='safe_http',error='health_http_error')
                except Exception:
                    runtime = RuntimeEvidence(coverage='not_observed',state='unknown',observed_at=None,
                        checked_at=now,method='safe_http',error='health_unavailable')
                return key, HealthObservation(runtime=runtime)
            values = dict(await asyncio.gather(*(probe(key) for key in ('ai-fae-agent','ai-daq-fae-agent','voc'))))
            values.update(await asyncio.to_thread(self.host.read,now))
            self._cached, self._expires = values, monotonic()+self.ttl
            return values
