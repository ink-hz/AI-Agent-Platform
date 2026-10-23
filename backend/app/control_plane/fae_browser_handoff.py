"""Static browser bridge to the existing authenticated, CSRF-protected launch API."""
from __future__ import annotations

import re

from fastapi.responses import HTMLResponse, Response

_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'none'; "
        "script-src 'self'; connect-src 'self'"
    ),
}

# No request, account, CSRF, or launch-code data is interpolated into this asset.
FAE_LAUNCH_SCRIPT = r"""(() => {
  'use strict';
  const status = document.getElementById('fae-launch-status');
  const retry = document.getElementById('fae-launch-retry');
  const path = window.location.pathname;
  const suffix = '/agents/ai-fae-agent';
  if (!/^\/(?:[A-Za-z0-9_-]+\/)*agents\/ai-fae-agent$/.test(path)) {
    status.textContent = 'FAE 入口地址无效，请从 Agent Platform 重新打开。';
    return;
  }
  const prefix = path.slice(0, -suffix.length);
  let running = false;

  function failed(permission) {
    status.textContent = permission
      ? '没有 FAE 使用权限，请联系管理员后手动重试。'
      : '暂时无法进入 FAE，请手动重试；若仍失败，请联系管理员。';
    retry.hidden = false;
    retry.disabled = false;
    running = false;
  }

  async function launch() {
    if (running) return;
    running = true;
    retry.hidden = true;
    retry.disabled = true;
    status.textContent = '正在打开 AI FAE…';
    try {
      const account = await fetch(prefix + '/api/v1/account', {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error'
      });
      if (account.status === 401) {
        window.location.replace(prefix + '/login?return_path=' + encodeURIComponent(suffix));
        return;
      }
      if (!account.ok) { failed(account.status === 403); return; }
      const identity = await account.json();
      if (!identity || typeof identity.csrf_token !== 'string' ||
          !/^[A-Za-z0-9_-]{1,256}$/.test(identity.csrf_token)) {
        failed(false); return;
      }
      const response = await fetch(prefix + '/api/v1/agents/ai-fae-agent/launch', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: {'X-CSRF-Token': identity.csrf_token}
      });
      if (!response.ok) { failed(response.status === 403); return; }
      const result = await response.json();
      // Compare the original string, not a URL parser's normalized authority/path.
      // The length check also rejects JavaScript $ matching before a final newline.
      const target = result && result.launch_url;
      const match = typeof target === 'string' &&
        /^https:\/\/fae\.orbbec\.com\.cn\/#platform_launch=[A-Za-z0-9_-]{32,256}$/.exec(target);
      if (!match || match[0] !== target) { failed(false); return; }
      window.location.replace(target);
    } catch (_) {
      // A POST may already have succeeded. Only a deliberate click starts anew.
      failed(false);
    }
  }
  retry.addEventListener('click', launch);
  void launch();
})();
"""


def fae_handoff_page(route_prefix: str) -> HTMLResponse:
    prefix = route_prefix.rstrip("/")
    # Prefix is deployment configuration, but unsupported paths still fail closed.
    if prefix and re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", prefix) is None:
        return HTMLResponse(
            "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<title>AI FAE</title><p>FAE 入口配置无效，请联系管理员。</p></html>",
            status_code=503,
            headers=_HEADERS,
        )
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer"><title>打开 AI FAE</title>'
        f'<script src="{prefix}/api/v1/auth/fae-launch.js" defer></script>'
        '</head><body><main><h1>打开 AI FAE</h1>'
        '<p id="fae-launch-status" role="status">正在确认登录状态…</p>'
        '<button id="fae-launch-retry" type="button" hidden>手动重试</button>'
        '<noscript>请启用 JavaScript 后重新打开 AI FAE。</noscript>'
        '</main></body></html>',
        headers=_HEADERS,
    )


def fae_handoff_script() -> Response:
    return Response(FAE_LAUNCH_SCRIPT, media_type="application/javascript", headers=_HEADERS)
