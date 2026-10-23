"""Development-only personal OAuth/provisioning probe; no Platform or FAE session."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import stat
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from ipaddress import ip_address
from itertools import combinations
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from psycopg.conninfo import conninfo_to_dict
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.execution_relay.content_crypto import ContentCodec

from .auth import AuthSecrets
from .crypto import IdentityKeyring
from .dingtalk_external_provider import DingTalkExternalIdentityProvider
from .dsn import validate_control_dsn
from .partner_identity_crypto import PartnerProviderIdentityCodec
from .partner_models import PartnerIdentityError
from .partner_provider import EXTERNAL_DINGTALK_NONCE_COOKIE, PartnerAuthenticationBroker
from .partner_repository import PartnerRepository
from .partner_service import PartnerService

_CALLBACK = '/api/v1/auth/dingtalk/callback'
_REDIRECT = 'https://agent.orbbec.com.cn' + _CALLBACK
_HEADERS = {
    'Cache-Control': 'no-store',
    'Pragma': 'no-cache',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
}
_ERRORS = frozenset({
    'partner_auth_invalid', 'partner_auth_state_invalid', 'partner_auth_state_replay',
    'partner_auth_state_expired', 'authentication_cancelled', 'provider_identity_inactive',
    'subject_inactive', 'organization_inactive', 'operator_inactive', 'fae_access_denied',
    'partner_identity_conflict', 'required_audit_unavailable', 'partner_identity_unavailable',
})


def _private_text(variable: str) -> str:
    """Open bounded, owner-only files without following symlinks or exposing paths."""
    path = Path(os.environ[variable])
    metadata = path.lstat()
    if (not path.is_absolute() or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600):
        raise ValueError
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError
        value = os.read(descriptor, 65_537)
    finally:
        os.close(descriptor)
    if len(value) > 65_536:
        raise ValueError
    text = value.decode('utf-8').strip()
    if not text or '\n' in text or '\r' in text or '\0' in text:
        raise ValueError
    return text


def _build_service():
    try:
        if (os.environ.get('FAE_PROBE_ENVIRONMENT') != 'development'
                or any(name.startswith('PG') for name in os.environ)):
            raise ValueError
        dsn = _private_text('FAE_PROBE_DATABASE_URL_FILE')
        parsed = validate_control_dsn(dsn, purpose='app')
        if parsed.environment != 'preview':
            raise ValueError
        # Libpq service/options indirection must not override the explicit preview target.
        values = conninfo_to_dict(dsn)
        if (not all(values.get(key) for key in ('host', 'port', 'password'))
                or set(values) - {'host', 'hostaddr', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout'}):
            raise ValueError
        rings = []
        for name, purpose in [
            ('AUTH', 'fae-probe-auth'),
            ('PROVIDER_ENCRYPTION', 'partner-provider-encryption'),
            ('PROVIDER_LOOKUP', 'partner-provider-lookup-hmac'),
            ('CONTENT', 'platform-content-encryption'),
        ]:
            rings.append(IdentityKeyring.from_file(
                os.environ[f'FAE_PROBE_{name}_KEYRING_FILE'],
                expected_purpose=purpose, expected_key_length=32,
            ))
        if any(first.overlaps(second) for first, second in combinations(rings, 2)):
            raise ValueError
        auth, encryption, lookup, content = rings
        codec = PartnerProviderIdentityCodec(encryption, lookup)
        repository = PartnerRepository(dsn, identity_codec=codec)
        service = PartnerService(repository, content_codec=ContentCodec(content))
        return service, AuthSecrets(auth.active_key, key_version=auth.active_version)
    except Exception:
        raise ValueError('fae_probe_configuration_invalid') from None


def _check_database(service: PartnerService) -> None:
    try:
        with service.repository._connection() as connection:
            row = connection.execute('select current_database() as database, current_user as role').fetchone()
            if row != {'database': 'agent_platform_control_preview', 'role': 'platform_control_app_preview'}:
                raise ValueError
            # Require the actual provisioning migration before declaring readiness.
            connection.execute("select 'platform_control.provision_external_dingtalk_v123(uuid,bytea,integer[],bytea[],bytea,integer,bytea,integer,uuid,bytea,integer,bytea,integer,timestamptz)'::regprocedure")
    except Exception:
        raise ValueError('fae_probe_database_unavailable') from None


class _Limits:
    """Single-worker bounded counters; source is the socket peer, never forwarded headers."""
    def __init__(self):
        self.sources = OrderedDict()
        self.inflight = 0

    def admit(self, request: Request) -> bool:
        try:
            source = ip_address(request.client.host).compressed
        except (AttributeError, ValueError):
            return False
        now = time.monotonic()
        for key, (started, _) in list(self.sources.items()):
            if now - started >= 60:
                del self.sources[key]
        if source not in self.sources:
            if len(self.sources) >= 256:
                return False
            self.sources[source] = (now, 0)
        started, count = self.sources[source]
        if count >= 20:
            return False
        self.sources[source] = (started, count + 1)
        return True


def _error(code: str, status: int = 401):
    return JSONResponse({'code': code}, status_code=status)


def _clear_nonce(response):
    response.delete_cookie(EXTERNAL_DINGTALK_NONCE_COOKIE, path='/', secure=True, httponly=True, samesite='lax')
    return response


def _callback_values(request: Request) -> dict[str, str]:
    callback = {}
    if len(request.scope.get('query_string', b'')) > 8192:
        raise ValueError
    for key, value in request.query_params.multi_items():
        if key in callback or key not in {'state', 'code'}:
            raise ValueError
        if not value or len(value) > 4096 or re.fullmatch(r'[\x21-\x7e]+', value) is None:
            raise ValueError
        callback[key] = value
    if (re.fullmatch(r'fae-dt-sns-v1\.[A-Za-z0-9_-]{32,128}', callback.get('state', '')) is None
            or not callback.get('code')):
        raise ValueError
    return callback


def create_app(*, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    service, secrets = _build_service()
    try:
        client_id = _private_text('FAE_PROBE_APP_KEY_FILE')
        client_secret = _private_text('FAE_PROBE_APP_SECRET_FILE')
    except Exception:
        raise ValueError('fae_probe_configuration_invalid') from None
    _check_database(service)
    provider = DingTalkExternalIdentityProvider(
        client_id=client_id, client_secret=client_secret, redirect_uri=_REDIRECT,
        verified_at_for_subject=service.external_verified_at, transport=transport,
    )
    broker = PartnerAuthenticationBroker(provider, service, state_secrets=secrets)
    limits = _Limits()
    # This standalone process must never emit access URLs or HTTP provider debug material.
    for name in ('uvicorn.access', 'httpx', 'httpcore'):
        logging.getLogger(name).disabled = True

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await provider.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.partner_service = service
    app.state.provider = provider

    @app.middleware('http')
    async def boundary(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            response = _error('partner_identity_unavailable', 503)
        if request.url.path == _CALLBACK:
            _clear_nonce(response)
        response.headers.update(_HEADERS)
        return response

    @app.get('/health')
    async def health():
        return {'ready': True}

    @app.get('/_dev/fae-external/start')
    async def start(request: Request):
        if not limits.admit(request):
            return _error('fae_probe_rate_limited', 429)
        try:
            started = broker.begin_auth()
        except PartnerIdentityError as error:
            return _error(error.code if error.code in _ERRORS else 'partner_identity_unavailable', error.status_code)
        response = RedirectResponse(started.authorization_url, status_code=302)
        response.set_cookie(EXTERNAL_DINGTALK_NONCE_COOKIE, started.browser_nonce, max_age=300,
                            path='/', secure=True, httponly=True, samesite='lax')
        return response

    @app.get(_CALLBACK)
    async def callback(request: Request):
        if not limits.admit(request):
            return _error('fae_probe_rate_limited', 429)
        try:
            values = _callback_values(request)
        except ValueError:
            return _error('partner_auth_invalid')
        if limits.inflight >= 2:
            return _error('fae_probe_rate_limited', 429)
        limits.inflight += 1
        try:
            await broker.finish_auth(values, browser_nonce=request.cookies.get(EXTERNAL_DINGTALK_NONCE_COOKIE))
        except PartnerIdentityError as error:
            return _error(error.code if error.code in _ERRORS else 'partner_identity_unavailable', error.status_code)
        finally:
            limits.inflight -= 1
        return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>开发验证</title>'
                            '<h1>身份验证成功</h1><p>开发环境身份与自动开通验证已完成。请关闭此页。</p></html>')

    return app


def safe_report(service: PartnerService) -> dict:
    """Read only opaque subject IDs and aggregate counts; never decrypt identities."""
    if not isinstance(service, PartnerService) or service.repository.environment != 'preview':
        raise ValueError('fae_probe_configuration_invalid')
    _check_database(service)
    try:
        with service.repository._connection() as connection:
            rows = connection.execute("""
                select operator.subject_id, bool_and(identity.revoked_at is null) as identity_active,
                    (select count(*) from platform_control.audit_events event
                     where event.event_type='fae_external_authenticated'
                     and event.target_internal_id=operator.subject_id::text) as login_count
                from platform_control.partner_operators operator
                join platform_control.partner_provider_identities identity
                  on identity.partner_operator_id=operator.partner_operator_id
                where identity.provider_kind='dingtalk_external'
                group by operator.subject_id
                order by operator.subject_id
            """).fetchall()
        subjects = [dict(subject_id=str(row['subject_id']), login_count=row['login_count'],
                         state='active' if row['identity_active'] and service.decide_fae_access(row['subject_id']).allowed else 'inactive')
                    for row in rows]
        return {'subject_count': len(subjects), 'subjects': subjects}
    except Exception:
        raise ValueError('fae_probe_report_unavailable') from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Preview-only external identity probe report')
    parser.add_argument('command', choices=['report'])
    parser.parse_args(argv)
    try:
        service, _ = _build_service()
        print(json.dumps(safe_report(service), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'code': 'fae_probe_report_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
