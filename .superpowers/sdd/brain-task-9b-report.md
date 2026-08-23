# Task 9B report — first-production bootstrap closure

Date: 2026-08-23 (Asia/Shanghai)

## Outcome

The first-production bootstrap gaps identified by the Task 9 read-only audit
are implemented in commits `5709694` (`fix(cloud): close Agent Brain bootstrap
gaps`) and `290f7aa` (`fix(bootstrap): harden first production cleanup`). No
production deployment, local Worker installation, cloud mutation or push was
performed.

The four pre-existing user-owned dirty reports remain untouched and uncommitted:

- `.superpowers/sdd/task-2-report.md`
- `.superpowers/sdd/task-3-report.md`
- `.superpowers/sdd/task-4-report.md`
- `.superpowers/sdd/task-6-report.md`

## Implemented controls

### Local Worker first provision

- `deploy/local-execution-worker/provision.sh` is a Neo-owned coordinator with
  no arguments and fixed commands/paths.
- It uses Neo's PostgreSQL 17 private socket to create a random temporary
  SCRAM login SUPERUSER. The password and owner DSN are sent only on stdin and
  are never printed or included in child process arguments.
- A managed HBA block is inserted before broader host rules. It contains a
  temporary `postgres` bootstrap rule only during the transaction and leaves
  exactly one permanent rule for database `agent_execution_worker`, role
  `agent_execution_worker_runtime`, address `127.0.0.1/32`, auth
  `scram-sha-256`.
- The wrapper validates `pg_hba_file_rules`, reload success and byte-exact HBA
  restoration on failure. HBA publication and rollback are atomic; a backup is
  retained if rollback cannot be proven. Cleanup is armed before role creation,
  so a response-lost-after-commit still drops the temporary role. Its trap asks
  the fixed agentops helper to remove the owner DSN on success and failure.
- `provision-agentops.sh` creates the fixed mode-0700 runtime/private/log tree,
  copies the reviewed Platform tree, creates its venv, and copies the existing
  file-backed MetaBot API token to one fixed mode-0600 Worker secret. It does
  not call Keychain.
- It invokes the existing Worker installer, verifies exact Agent Brain and all
  non-Brain PM2 PID/restart/command snapshots (including unknown PM2 names) are
  unchanged, rejects extra wildcard/IPv6 listeners, and correlates the sole
  IPv4 loopback Brain 9110 and Worker 9120 listener PIDs with PM2 and launchd.
- The complete reviewed Platform tree is checksum-compared on rerun rather than
  trusting a four-file subset. Secret files use exclusive no-follow creation
  and short-write-safe loops.

### First cloud Worker registration

- Migration `033_first_production_bootstrap.sql` adds a maintenance-only
  `ensure_first_execution_worker_v33` boundary.
- The boundary accepts only `agentops-mac-primary`, the exact ordered eight-Agent
  allowlist, an active single key and fixed audited reference. Complete absence
  registers through the existing v28 audited function; an exact replay is a
  no-op; partial, revoked, inactive, extra-key or mismatched state fails closed.
- The fixed UUIDv4 request ID survives a response-lost-after-commit retry.
- `remote-stage.sh` now requires Brain disabled, runs migrations first, invokes
  this bootstrap against the staged public Worker document, and verifies a
  sanitized status/fingerprint before starting the API services.

### Content keyring

- `generate-content-keyring.py` creates one 32-byte standard-base64 key for
  purpose `platform-content-encryption`, active version 1, with no transition
  versions.
- Ancestors are opened component-by-component with directory FDs and no-follow;
  unsafe writable ancestors and symlinks are rejected, and the parent must be
  owned by the caller and mode 0700. Publication uses a fixed
  O_EXCL/O_NOFOLLOW mode-0600 part and exclusive hard-link publication, so it
  cannot overwrite a raced target.
- Existing files are never regenerated and are validated through
  `IdentityKeyring` plus the production `ContentCodec`.
- Output contains only `CREATED`/`VALID` plus SHA-256 public fingerprint.

### Audited acceptance grant

- `python -m app.agent_brain.acceptance_grant` accepts a mode-0600 private JSON
  document containing predeclared `grant_id` and `request_id` UUIDs.
- Actor/member identities may be stable internal UUID strings or exact
  `/api/v1/account` results. Names and provider IDs cannot be supplied as the
  identity selector.
- The maintenance-only v33 database boundary verifies an active owner or
  platform administrator and active member, creates only the exact audited
  `hr-bot` grant, and re-reads the exact grant/audit/unrevoked state before
  verifying HR allowed and `marketing-gtm-bot` denied. Owner grants delegate to
  v29; administrator grants use the equivalent v33 audited idempotency boundary.
- Replaying the same IDs and payload is a no-op; a conflicting replay fails.
  CLI output is one sanitized allow/deny marker without identities.

### Acceptance coordinator ownership

- `deploy/cloud/accept.sh` now requires the Neo account and fixes cloud root
  access to `/Users/neo/.ssh/orbbec_aliyun_ed25519`.
- The acceptance config no longer contains a cloud host or private-key path.
  Worker-local actions remain fixed `sudo -n -u agentops` program/path calls;
  there is no agentops shell command interpolation and no private-key copy.

## TDD and verification evidence

Red was observed first as
`ModuleNotFoundError: app.agent_brain.acceptance_grant` from the new bootstrap
suite. The final focused bootstrap suite reports `42 passed` and covers real
temporary PostgreSQL clusters, function privilege boundaries, exact replay,
mismatch, NULL rejection, response loss, keyring unsafe paths, executable HBA
success/failure cleanup, secret-argv exclusion, PM2 mutation, listener identity,
launchd correlation and config-injection harnesses.

Fresh verification:

- Full backend: `2322 passed, 1 skipped`; exit 0. The one skip is
  repository-defined and expected in this branch.
- Affected deployment, relay, migration and Agent Brain suites: `456 passed`;
  exit 0.
- Web UI: `40` files, `311` tests passed.
- Web UI production build: succeeded (`tsc -b`, Vite; 1347 modules).
- `bash -n deploy/cloud/*.sh deploy/local-execution-worker/*.sh`: passed.
- `git diff --check`: passed.
- New-path embedded-secret scan: passed; no private key, DingTalk credential or
  long API-token literal. Runtime passwords, DSNs, Cookies and keys are never
  printed or included in child process arguments.
- Independent review after all fixes: `0 Critical, 0 Important, 0 Minor`.
- Docker Compose rendering could not be repeated in this workstation process
  because the `docker` CLI is not installed (`command not found`). The compose
  file was not changed by Task 9B; this remains a deployment-host preflight.

## Remaining manual-only inputs and actions

The following are intentionally not fabricated or performed by this change:

1. A real DingTalk member and owner complete fresh logins and provide their two
   private Cookie header files.
2. An operator creates and backs up the content keyring in an approved private
   directory, and creates the private acceptance-grant/config/prompt files.
3. The reviewed commit is merged/pushed before the clean-release deployment
   precondition can pass.
4. The local provision wrapper is run, followed by its real listener/PM2/relay
   canary evidence.
5. Cloud deployment with Brain disabled, Worker heartbeat, grant helper,
   `preflight -> release -> rollback -> restore`, FAE invariance and sanitized
   acceptance evidence are still pending.

No key material, DSN, token, Cookie, DingTalk identifier, prompt or answer is
recorded in this report.
