# Platform Static Asset Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress and cache fingerprinted Agent Platform assets without changing authentication, HTML, API, or VOC behavior.

**Architecture:** A dedicated Nginx `/assets/` location proxies to the existing manifest-protected platform asset endpoint. The formal template and DingTalk production transaction emit the same location so fresh installs and later cutovers preserve the optimization.

**Tech Stack:** Nginx, Python 3.11, pytest, Docker-based cloud deployment.

## Global Constraints

- Keep the backend manifest allowlist as the only asset authorization boundary.
- Keep HTML, API, authentication, and non-fingerprinted responses on `no-store`.
- Do not add a CDN or frontend route splitting in this change.
- Preserve all existing security headers and AI FAE routes.

---

### Task 1: Fingerprinted asset proxy policy

**Files:**
- Modify: `backend/tests/test_agent_brain_deployment.py`
- Modify: `backend/tests/test_dingtalk_nginx_transaction.py`
- Modify: `deploy/cloud/agent-domain.nginx.conf`
- Modify: `deploy/cloud/dingtalk_nginx_transaction.py`

**Interfaces:**
- Consumes: backend `GET /assets/{filename}` manifest allowlist on `127.0.0.1:8080`.
- Produces: Nginx `location ^~ /assets/` with gzip and immutable caching.

- [ ] **Step 1: Write failing configuration tests**

Add assertions that both the formal template and the transaction output contain a platform asset block before `location /`, proxy to `127.0.0.1:8080`, hide upstream cache/cookie headers, enable gzip for JavaScript and CSS, and emit `public, max-age=31536000, immutable` without `proxy_buffering off`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_brain_deployment.py::test_formal_nginx_keeps_platform_root_and_proxies_office_safely tests/test_dingtalk_nginx_transaction.py::test_transaction_changes_only_platform_root_and_server_shared_auth -q
```

Expected: both tests fail because `location ^~ /assets/` is absent.

- [ ] **Step 3: Add the minimal Nginx asset location**

Use this policy in both production render paths:

```nginx
location ^~ /assets/ {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Forwarded "";
    proxy_set_header Authorization "";
    proxy_hide_header Cache-Control;
    proxy_hide_header Set-Cookie;
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/css application/javascript application/json image/svg+xml font/woff font/woff2;
    add_header Cache-Control "public, max-age=31536000, immutable";
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
}
```

- [ ] **Step 4: Run focused and deployment tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_brain_deployment.py tests/test_dingtalk_nginx_transaction.py tests/test_dingtalk_production_deployment.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_agent_brain_deployment.py backend/tests/test_dingtalk_nginx_transaction.py deploy/cloud/agent-domain.nginx.conf deploy/cloud/dingtalk_nginx_transaction.py
git commit -m "perf(web): compress and cache platform assets"
```

### Task 2: Verify and publish

**Files:**
- No product file changes.

**Interfaces:**
- Consumes: committed release on `origin/master` and the existing controlled cloud deployment configuration.
- Produces: active Platform release and validated Nginx asset policy on `47.106.112.69`.

- [ ] **Step 1: Run full verification**

Run backend tests from `backend`, run `npm run build` from `webui`, and require zero failures.

- [ ] **Step 2: Review and push**

Require no Critical or Important review findings, then push `master`.

- [ ] **Step 3: Deploy from a clean detached worktree**

Run `deploy/cloud/deploy.sh` with the existing mode-0600 cloud deployment configuration, then atomically render and install the committed formal Nginx template from the active manifest-bound release with backup, `nginx -t`, reload, and rollback on any failed probe.

- [ ] **Step 4: Verify production behavior**

Require a compressed `GET /assets/index-*.js` response with `Content-Encoding: gzip`, immutable caching without `no-store`, a second conditional request returning `304`, matching production and `origin/master` SHAs, and healthy Platform/VOC containers.
