"""Browser-only binding and fixed recovery for external SNS authentication."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Literal

from fastapi import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .partner_provider import EXTERNAL_DINGTALK_STATE_PREFIX

_COOKIE_PREFIX = '__Host-fae-dt-nonce-'
_COOKIE_NAME = re.compile(re.escape(_COOKIE_PREFIX) + r'[0-9a-f]{64}\Z')
_STATE = re.compile(re.escape(EXTERNAL_DINGTALK_STATE_PREFIX) + r'[A-Za-z0-9_-]{32,128}\Z')
_NONCE = re.compile(r'[A-Za-z0-9_-]{32,128}\Z')
PENDING_LIMIT = 3
COOKIE_TTL_SECONDS = 300
AUTH_HEADERS = {
    'Cache-Control': 'no-store', 'Pragma': 'no-cache',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}
_LOG = logging.getLogger(__name__)
RejectionReason = Literal[
    'invalid_state_shape', 'invalid_callback_shape', 'missing_cookie',
    'invalid_cookie_shape', 'storage_rejected', 'pending_limit',
]


def nonce_cookie_name(state: str) -> str | None:
    if not isinstance(state, str) or _STATE.fullmatch(state) is None:
        return None
    return _COOKIE_PREFIX + hashlib.sha256(state.encode('ascii')).hexdigest()


def _cookie_values(request: Request) -> list[tuple[str, str]]:
    # Request.cookies collapses duplicates; retain them so ambiguous binding
    # fails closed instead of picking one client-controlled value.
    return [
        (name.strip(), value.strip())
        for header in request.headers.getlist('cookie')
        for part in header.split(';')
        if '=' in part
        for name, value in [part.split('=', 1)]
    ]


def pending_cookie_count(request: Request) -> int:
    return len({name for name, _ in _cookie_values(request) if _COOKIE_NAME.fullmatch(name)})


def browser_nonce(request: Request, cookie_name: str) -> tuple[str | None, RejectionReason | None]:
    values = [value for name, value in _cookie_values(request) if name == cookie_name]
    if not values:
        return None, 'missing_cookie'
    if len(values) != 1 or _NONCE.fullmatch(values[0]) is None:
        return None, 'invalid_cookie_shape'
    return values[0], None


def _accepts_html(request: Request) -> bool:
    for entry in request.headers.get('accept', '').split(','):
        media_type, *parameters = entry.split(';')
        if media_type.strip().lower() != 'text/html':
            continue
        quality = 1.0
        try:
            for parameter in parameters:
                key, separator, value = parameter.strip().partition('=')
                if key.lower() == 'q' and separator:
                    quality = float(value)
        except ValueError:
            continue
        if 0 < quality <= 1:
            return True
    return False


def rejected_response(request: Request, *, code: str, status_code: int, reason: RejectionReason) -> Response:
    # Never pass callback data, provider payloads, exceptions or identity to logs.
    _LOG.warning('external_dingtalk_auth_rejected reason=%s', reason)
    if _accepts_html(request) and status_code in {401, 429}:
        message = (
            '待完成的登录页面已达上限。请先完成已有页面的登录，或等待五分钟后重试。'
            if status_code == 429 else
            '此次登录验证已失效或无法确认。请在打开登录页面的同一浏览器中重新扫码登录。'
        )
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>外部用户登录未完成</title></head><body><main>'
            '<h1>外部用户登录未完成</h1><p>' + message + '</p>'
            '<p><a href="https://fae.orbbec.com.cn/partner/login">重新打开登录页面</a></p>'
            '<p><a href="https://fae.orbbec.com.cn/">返回 FAE</a></p>'
            '</main></body></html>', status_code=status_code, headers=AUTH_HEADERS,
        )
    return JSONResponse({'detail': {'code': code}}, status_code=status_code, headers=AUTH_HEADERS)
