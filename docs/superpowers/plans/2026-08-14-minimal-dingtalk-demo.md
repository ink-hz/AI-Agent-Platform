# Minimal DingTalk Demo Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an isolated real DingTalk QR-login preview for one to three approved Orbbec members without changing the existing Basic-Auth Platform root or FAE.

**Architecture:** Build from the last reviewed identity baseline (`92a1d40`) in a separate demo worktree. A root-operated bootstrap command resolves a small stable-userid allowlist through DingTalk and creates the only active preview directory generation; a dedicated preview API and loopback proxy use the existing separate preview control database. Nginx exposes only `/_preview/dingtalk-r1/`, while existing root and FAE routes remain byte-for-byte unchanged.

**Tech Stack:** Python 3.11, FastAPI, psycopg/PostgreSQL 17, httpx, Docker Compose, Nginx, DingTalk OAuth/OpenAPI, Bash deployment scripts.

## Global Constraints

- Base implementation on reviewed commit `92a1d401da0a12e18e5e0d71f3f14e438e6c2703`; do not include the unfinished Task 8 directory reconciler.
- Publish only `https://agent.orbbec.com.cn/_preview/dingtalk-r1/`.
- Preserve existing `https://agent.orbbec.com.cn/` Basic Auth and `https://fae.orbbec.com.cn/` behavior.
- Use the preview control database, preview roles, preview Cookie names/path and dedicated preview keys.
- QR flow must request exact `openid corpid`; in-client login is not part of the demo.
- Only stable DingTalk `userid` values from a root-owned `0600` input file may be bootstrapped; names never select identity.
- No full-directory schedule, Stream consumer, department grants, production cutover, FAE restart or existing Platform root restart.
- No secret, provider identifier, mobile, email, login code, token or Cookie may appear in git, command output, normal logs or URLs.
- Every deploy action has a read-only preflight and an exact preview-only rollback.

---

### Task 1: Add the preview allowlist bootstrap boundary

**Files:**
- Create: `backend/control_migrations/019_demo_preview_bootstrap.sql`
- Create: `backend/app/control_plane/demo_bootstrap.py`
- Create: `backend/tests/test_demo_preview_bootstrap.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Command: `python -m app.control_plane.demo_bootstrap --userid-file /run/secrets/demo-userids`
- Input file: one stable DingTalk `userid` per line, one to three unique lines, regular non-symlink file, owned by the process user, mode `0400` or `0600`.
- SQL functions, executable only by `platform_directory_worker_preview`:
  - `platform_control.begin_demo_directory_generation(uuid, integer, bytea) -> void`
  - existing `platform_control.stage_verified_directory_member(...) -> void`
  - existing `platform_control.promote_verified_directory_generation(uuid) -> void`
- Output: `DEMO_DIRECTORY_READY generation=<uuid> members=<count>`; no provider identifiers.

- [ ] **Step 1: Write failing bootstrap tests** for file ownership/mode/symlink, one-to-three stable IDs, duplicate/blank/oversized values, wrong corporation, inactive/missing member, provider error redaction, exact corporate-plus-union protected facts, preview-only DSN roles, partial-member failure leaving the prior active generation unchanged, and idempotent rerun producing one complete active generation.
- [ ] **Step 2: Run RED tests** with `cd backend && .venv/bin/python -m pytest tests/test_demo_preview_bootstrap.py tests/test_control_plane_migration.py -q`; expected result is import/migration failure because the command and migration do not exist.
- [ ] **Step 3: Add migration 019** with a fixed `search_path`, explicit NULL/type/range checks, `PUBLIC` revoke, production/cross-role revoke, and preview-directory-worker-only execute. `begin_demo_directory_generation` inserts a staging generation with zero departments, the declared member count and a 32-byte digest; it never accepts raw provider IDs and refuses when a non-demo staging generation or another active preview bootstrap is in progress.
- [ ] **Step 4: Implement the command** using existing `DingTalkClient.get_member`, `IdentityResolver.corporate_provider_id`, and `ProviderIdentityCodec.seal`. Resolve all members before opening a database transaction; require the configured corporation, active employee state, non-empty unionid and exact preview key policy. Compute a deterministic digest over protected corporate/union versions, HMACs, ciphertext, active flag and display name; begin, stage and promote through worker-only functions.
- [ ] **Step 5: Prove failure isolation** by forcing provider failure on member two, database failure during staging and promotion ambiguity. Provider/staging failures leave the previous active generation unchanged; a promotion whose commit result is unknown is reconciled by reading the authoritative active generation and otherwise returns `demo_promotion_indeterminate` without marking it failed.
- [ ] **Step 6: Run GREEN and compatibility tests**: bootstrap tests, Task 5 identity, Task 6 auth, Task 7 rate/proxy and full backend suite; run `compileall`, `git diff --check`, no-Keychain and secret scans.
- [ ] **Step 7: Commit** as `feat(demo): bootstrap an allowlisted DingTalk preview`.

### Task 2: Package isolated preview services and secrets

**Files:**
- Create: `deploy/cloud/compose.demo-preview.yaml`
- Create: `deploy/cloud/bootstrap-demo-preview-secrets.sh`
- Modify: `deploy/cloud/Dockerfile`
- Create: `backend/tests/test_demo_preview_deployment.py`

**Interfaces:**
- Preview API service: `platform-api-demo-preview`, internal address `172.30.0.5`, no host port.
- Preview loopback service: `platform-loopback-demo-preview`, internal address `172.30.0.6`, host binding `127.0.0.1:8081` only.
- Secret volume: `orbbec-agent-platform-demo-preview-secrets`.
- Required host files under `/opt/orbbec-agent-platform/private/demo-preview/`, all root-owned `0600`: DingTalk AppKey/AgentId/CorpId values, AppSecret, preview app/directory/migrator DSNs, provider HMAC/encryption keyrings, rate-limit keyring and `demo-userids`.

- [ ] **Step 1: Write failing deployment-contract tests** proving only port `127.0.0.1:8081` is added, preview uses `PLATFORM_IDENTITY_MODE=preview`, exact route prefix/Cookie names, `PLATFORM_DINGTALK_LOGIN_FLOW=qr`, exact trusted proxy `/32`, no production DSN/key file, no Stream/directory schedule service, read-only filesystem, dropped capabilities and health checks with normalized proxy headers.
- [ ] **Step 2: Run RED tests** with `cd backend && .venv/bin/python -m pytest tests/test_demo_preview_deployment.py tests/test_cloud_deployment.py tests/test_cloud_loopback_proxy.py -q`; expected result is missing overlay/bootstrap files.
- [ ] **Step 3: Implement the Compose overlay** without replacing existing services. Reuse the immutable Platform image; mount only the demo preview secret volume; set preview identity variables; use `--no-proxy-headers`; bind only loopback port 8081; keep root API/loopback containers untouched.
- [ ] **Step 4: Implement secret staging** as an idempotent root-only script. Validate exact file owner/mode/type and keyring purpose, reject overlapping provider/rate keys and reject any production/preview DSN mismatch. Copy into the Docker volume as UID 10001 mode `0400`; never print contents.
- [ ] **Step 5: Add an image command smoke test** that migrates the preview database with the preview migrator DSN, runs the demo bootstrap with the preview directory-worker DSN, starts API/loopback, and verifies `/api/health` returns only `{"status":"ok"}`.
- [ ] **Step 6: Run GREEN, Compose static parsing, Docker build when available, full deployment regression, `bash -n`, `compileall`, diff/no-Keychain/secret scans.**
- [ ] **Step 7: Commit** as `feat(demo): package the isolated DingTalk preview`.

### Task 3: Add the preview-only Nginx route and rollback

**Files:**
- Create: `deploy/cloud/demo-preview.nginx.conf`
- Create: `deploy/cloud/install-demo-preview.sh`
- Create: `deploy/cloud/rollback-demo-preview.sh`
- Modify: `deploy/cloud/agent-domain.nginx.conf`
- Create: `backend/tests/test_demo_preview_nginx.py`

**Interfaces:**
- Nginx route prefix: `/_preview/dingtalk-r1/` proxied unchanged to `http://127.0.0.1:8081`.
- Root server-level Basic Auth remains unchanged; preview location uses `auth_basic off` and relies on the exact application public allowlist plus authentication middleware.
- Rollback removes only the demo include, reloads Nginx after `nginx -t`, and stops only demo preview services.

- [ ] **Step 1: Write failing Nginx tests** for exact prefix matching, no sibling-prefix match, QR callback GET, login/start/in-client/logout POST support, 1 MiB body limit, redacted access logging, 330-second upstream timeout, forced normalized proxy headers, empty `Forwarded`, no authorization forwarding, no FAE/root directive changes and no new public listener.
- [ ] **Step 2: Run RED tests** with `cd backend && .venv/bin/python -m pytest tests/test_demo_preview_nginx.py tests/test_agent_domain_deployment.py -q`; expected result is missing preview configuration/scripts.
- [ ] **Step 3: Implement the isolated include** with security headers, no-store, exact prefix and an explicit redirect from `/_preview/dingtalk-r1` to the trailing-slash URL. Add one fixed `include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;` line immediately before the existing root `location /` in the source template; do not change the root location body. The installer verifies the deployed server block matches the expected template hash before adding that exact include line, backs up the complete file, and installs the separately rendered snippet only after validating a temporary complete Nginx configuration.
- [ ] **Step 4: Implement installation safeguards**: capture hashes of existing enabled Nginx config, FAE/root container IDs/images/start times/restart counts, listeners and current external response codes; install to a temporary path; require `nginx -t`; reload rather than restart; verify all invariants after reload or automatically restore the backup.
- [ ] **Step 5: Implement preview-only rollback** with explicit targets, idempotency, syntax validation and post-rollback checks proving root Basic Auth and FAE are unchanged.
- [ ] **Step 6: Run GREEN tests, shell syntax checks and `git diff --check`; if Nginx is unavailable locally, run static tests and require real `nginx -t` on the target before activation.**
- [ ] **Step 7: Commit** as `feat(demo): expose and rollback the DingTalk preview`.

### Task 4: Deploy, bootstrap and accept the real QR demo

**Files:**
- Create: `deploy/cloud/deploy-demo-preview.sh`
- Create: `deploy/cloud/accept-demo-preview.sh`
- Create: `docs/runbooks/minimal-dingtalk-demo.md`
- Create: `backend/tests/test_demo_preview_release.py`

**Interfaces:**
- Target: `root@47.106.112.69` using the configured SSH key.
- Demo URL: `https://agent.orbbec.com.cn/_preview/dingtalk-r1/`.
- Release command accepts an immutable release SHA and SHA-256 archive digest; it never reads secrets from command-line arguments.
- Rollback command: `/opt/orbbec-agent-platform/current/deploy/cloud/rollback-demo-preview.sh`.

- [ ] **Step 1: Write failing release tests** for immutable archive manifest, dirty-worktree refusal, secret prerequisite refusal, root/FAE preflight invariants, preview database-only migration, bootstrap output parsing, container health, invalid-state/provider-zero-call smoke, unapproved-user denial evidence, exact Cookie flags/path and rollback restoration.
- [ ] **Step 2: Run RED tests** with `cd backend && .venv/bin/python -m pytest tests/test_demo_preview_release.py -q`; expected result is missing deploy/acceptance scripts.
- [ ] **Step 3: Implement deployment** as prepare → verify → activate. Build and upload an immutable archive, create a release directory, build the image, bootstrap preview credentials/database, migrate preview only, run the member bootstrap, start preview services, validate loopback health, install Nginx preview include, then run external smoke tests. Any failure before activation leaves Nginx unchanged; any failure after activation invokes preview-only rollback.
- [ ] **Step 4: Implement automated acceptance** for HTTPS, minimal public health, login page/assets, state replay denial, protected-route 401, root Basic Auth challenge, FAE 200, unchanged public listeners and unchanged existing container identities/restart counts. Print only safe PASS/FAIL labels.
- [ ] **Step 5: Run local verification**: focused demo tests, full backend, frontend tests/build, shell syntax, Compose config, Docker build, secret scan, `git diff --check`, and a clean-worktree assertion.
- [ ] **Step 6: Perform target preflight** without mutation. Require all secret files including one-to-three approved stable userids, sufficient disk, healthy existing containers, available preview port 8081, valid internal/public DNS and trusted TLS. Stop and report the exact missing prerequisite if any check fails.
- [ ] **Step 7: Deploy the preview** only after preflight passes. Record release SHA, image digest, preview container IDs, preview database migration version, Nginx config hash and rollback command in the runbook evidence section.
- [ ] **Step 8: Execute real acceptance**: the approved user opens the demo URL and completes QR login; verify account identity and logout; an unapproved account is denied. If the second account is not available, automated denial plus explicit allowlist database proof is accepted for the demo but recorded.
- [ ] **Step 9: Commit implementation/runbook evidence** as `docs(demo): record DingTalk preview acceptance`. Do not commit secrets, stable provider IDs, raw HTTP payloads or Cookies.

## Final Gate

- [ ] Run an independent review of the complete demo branch from base `92a1d40`.
- [ ] Confirm no Critical or Important findings remain.
- [ ] Re-run preview rollback once, redeploy the same immutable release and repeat the root/FAE invariance checks.
- [ ] Hand the user the demo URL and exact preview shutdown command; explicitly state that Tasks 8–16 remain required for production cutover.
