# Agent Platform Public Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the sanitized, read-only cloud Platform at `https://agent.orbbec.com.cn` behind temporary administrator Basic Auth without exposing port 8080 or changing FAE behavior.

**Architecture:** Keep Platform and PostgreSQL on their existing private networks and put an exact-host Nginx TLS virtual host in front of the loopback proxy. A local non-interactive publisher streams a private password to a fail-closed remote installer, which stores only a salted hash, backs up all touched state, atomically enables the route, and creates a rollback script.

**Tech Stack:** Python 3.11, FastAPI, Pytest, Bash, Nginx 1.24, Certbot 2.9, OpenSSL, Docker Compose, OpenSSH.

## Global Constraints

- `127.0.0.1:8080` remains the only Platform host listener; PostgreSQL and importer have no public port.
- Cloud mode remains read-only and all mutation, attachment-byte, control and source-fallback paths remain disabled.
- Every HTTPS route requires authentication; HTTP serves only ACME and redirects to HTTPS.
- The password and password hash never enter Git, argv, process listings, shell tracing, acceptance output or Keychain.
- Existing FAE domain and legacy IP behavior, container identity, image, start time and health remain unchanged.
- Deployment is fail-closed, non-interactive and creates a root-only backup and rollback script before activation.
- The public entry is temporary administrator access, not employee identity or HR authorization.

---

### Task 1: Configurable cloud authentication status

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/cloud_replica/repository.py`
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/remote-stage.sh`
- Test: `backend/tests/test_cloud_config.py`
- Test: `backend/tests/test_cloud_api.py`
- Test: `backend/tests/test_cloud_repository.py`
- Test: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: `PLATFORM_CLOUD_AUTH_MODE` with allowed values `ssh-tunnel` and `basic-auth`.
- Produces: `Config.cloud_auth_mode` and `/api/deployment.auth` matching the active entry mode.

- [ ] **Step 1: Write failing tests** that set `PLATFORM_CLOUD_AUTH_MODE=basic-auth`, require `load_config().cloud_auth_mode == "basic-auth"`, reject every other value, require repository and fallback deployment payloads to use the configured value, and require Compose to set the default `ssh-tunnel` value.
- [ ] **Step 2: Run the focused tests and verify RED** with `cd backend && .venv/bin/python -m pytest tests/test_cloud_config.py tests/test_cloud_api.py tests/test_cloud_repository.py tests/test_cloud_deployment.py -q`; failures must identify the missing configuration field and hard-coded `ssh-tunnel` values.
- [ ] **Step 3: Implement the minimum configuration flow** by adding `cloud_auth_mode: Literal["ssh-tunnel", "basic-auth"]`, validating it only in cloud mode, passing it to `ReplicaObservabilityRepository`, returning it from the fallback route, setting `PLATFORM_CLOUD_AUTH_MODE: ${PLATFORM_CLOUD_AUTH_MODE:-ssh-tunnel}` in Compose, and preserving or defaulting it in `remote-stage.sh`.
- [ ] **Step 4: Run the focused tests and verify GREEN**, then run `git diff --check`.
- [ ] **Step 5: Commit** with `git commit -m "feat(cloud): report public authentication mode"`.

### Task 2: Fail-closed Agent domain publisher

**Files:**
- Create: `deploy/cloud/agent-domain.nginx.conf`
- Create: `deploy/cloud/install-agent-domain.sh`
- Create: `deploy/cloud/publish-agent-domain.sh`
- Create: `backend/tests/test_agent_domain_deployment.py`
- Modify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Consumes: a mode-0600 config containing `CLOUD_ADMIN_HOST`, `CLOUD_ADMIN_KEY`, `AGENT_DOMAIN`, `AGENT_BASIC_AUTH_USER`, and `AGENT_BASIC_AUTH_PASSWORD_FILE`.
- Produces: authenticated `https://agent.orbbec.com.cn`, `/root/rollback-agent-domain-<UTC>.sh`, a protected Nginx password hash, and stable success `AGENT_DOMAIN_PUBLISH_OK domain=agent.orbbec.com.cn`.

- [ ] **Step 1: Write failing static policy tests** that parse the Nginx template and scripts and require exact host routing, ACME before redirect, authentication on all HTTPS paths, loopback upstream, security headers, TLS 1.2/1.3, one-second auth delay, no `set -x`, password only on standard input, strict mode/owner checks, `BatchMode=yes`, pre-change backup, `nginx -t`, Certbot webroot issuance, rollback installation, FAE invariant checks, and no command that restarts or recreates FAE.
- [ ] **Step 2: Run `cd backend && .venv/bin/python -m pytest tests/test_agent_domain_deployment.py -q` and verify RED** because the release assets do not exist.
- [ ] **Step 3: Implement `agent-domain.nginx.conf`** with separate HTTP and HTTPS exact-host blocks, ACME webroot, 308 redirect, Basic Auth, strict headers, disabled proxy buffering/cache, 300-second proxy timeouts, and upstream `127.0.0.1:8080`.
- [ ] **Step 4: Implement `install-agent-domain.sh`** to validate domain/user/password input, capture FAE/Platform/listener facts, back up `/etc/nginx` and Platform environment, generate the password hash without argv exposure, stage HTTP ACME configuration, obtain or reuse a valid hostname certificate, atomically enable HTTPS, set `PLATFORM_CLOUD_AUTH_MODE=basic-auth`, recreate only Platform API and loopback when needed, run authenticated/unauthenticated checks, create a root-only rollback script, and restore prior state on any failure.
- [ ] **Step 5: Implement `publish-agent-domain.sh`** to validate private local inputs, copy reviewed assets with non-interactive SSH, stream the password over standard input, verify public HTTP redirect / HTTPS 401 / authenticated APIs without printing credentials, and emit only the stable success line.
- [ ] **Step 6: Extend the runbook** with credential creation outside Git, publication, verification, rotation through the same installer, temporary-access limits, and rollback.
- [ ] **Step 7: Run the focused policy tests and verify GREEN**, then run `bash -n deploy/cloud/*.sh`, `git diff --check`, and scan changed files for forbidden plaintext or Keychain commands.
- [ ] **Step 8: Commit** with `git commit -m "feat(cloud): publish authenticated agent domain"`.

### Task 3: Full verification, integration and deployment

**Files:**
- Verify: all files changed in Tasks 1 and 2
- Deploy: cloud host `47.106.112.69`

**Interfaces:**
- Consumes: the reviewed feature commit, current cloud Platform, current FAE Nginx reservation blocks, and private local deployment files.
- Produces: merged `master`, deployed authenticated domain entry, external acceptance evidence, and preserved rollback capability.

- [ ] **Step 1: Run the complete local gate** with `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`, `cd webui && npm test -- --run && npm run build`, `bash -n deploy/cloud/*.sh`, and `git diff --check`.
- [ ] **Step 2: Review the complete feature diff** against the design requirements and fix every critical or important issue, then re-run affected tests.
- [ ] **Step 3: Push the feature branch and fast-forward `origin/master`** only after the complete local gate passes; do not touch the dirty primary checkout.
- [ ] **Step 4: Deploy the application release** from a clean checkout at `origin/master`, verify `cloud-replica/read_only`, and preserve the loopback-only listener.
- [ ] **Step 5: Generate a high-entropy temporary password in the existing private cloud-replica directory**, write it mode 0600 without printing it, publish the Agent domain, and retain the plaintext only in that protected local file for administrator retrieval.
- [ ] **Step 6: Run fresh remote and public acceptance checks** for TLS, redirect, 401 rejection, authenticated HTML/assets/APIs, auth mode, loopback listener, Nginx, Certbot, FAE domain, legacy IP, container invariants, backups and rollback script.
- [ ] **Step 7: Report the public URL, the exact protected local password-file path, release commit, verification counts, rollback path, and the separate unresolved data backfill/synchronization gates without exposing the password itself.**
