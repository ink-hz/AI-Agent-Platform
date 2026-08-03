# Remove macOS Keychain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every AI Agent Platform Keychain dependency with restart-safe, current-user-only local secret files.

**Architecture:** A shared `read_secret_file()` function enforces absolute paths, regular non-symlink files, current-user ownership, `0600`-or-stricter access, bounded size, and non-empty content. Database resolvers use environment variables only as explicit operational overrides and otherwise read their configured file; replay credentials support `env:` and `file:` references only.

**Tech Stack:** Python 3.11, pytest, Pydantic registry models, macOS LaunchAgent, POSIX file permissions.

## Global Constraints

- Do not place credential values in Git, plist, database, logs, API responses, or frontend assets.
- Preserve analyst, review writer, and sync writer as three distinct PostgreSQL identities.
- Any missing or unsafe file fails closed; do not fall back to another role or credential.
- Do not run evaluation or chat traffic against FAE production.
- Remove `/usr/bin/security`, Keychain configuration fields, and `keychain:` replay support completely.
- Preserve unrelated dirty files in the root worktree.

---

### Task 1: Add the strict local secret reader

**Files:**
- Create: `backend/app/local_secrets.py`
- Create: `backend/tests/test_local_secrets.py`

**Interfaces:**
- Produces: `SecretFileUnavailable(RuntimeError)` and `read_secret_file(path: str, *, max_bytes: int = 16384) -> str`.

- [ ] **Step 1: Write failing tests** for a valid `0600` current-user file and rejection of relative paths, symlinks, group/other permissions, empty content, non-regular files, and content over 16 KiB.
- [ ] **Step 2: Run** `backend/.venv/bin/pytest -q backend/tests/test_local_secrets.py`; expect import failure because `app.local_secrets` does not exist.
- [ ] **Step 3: Implement** `read_secret_file()` with `Path.is_absolute()`, `Path.lstat()`, `stat.S_ISREG`, `stat.S_ISLNK`, `st_uid == os.getuid()`, `(stat.S_IMODE(st_mode) & 0o077) == 0`, bounded byte reads, UTF-8 decoding, and `.strip()` non-empty validation. All invalid states raise the same sanitized `SecretFileUnavailable("secret file unavailable")`.
- [ ] **Step 4: Run** `backend/.venv/bin/pytest -q backend/tests/test_local_secrets.py`; expect all tests to pass.
- [ ] **Step 5: Commit** `backend/app/local_secrets.py` and `backend/tests/test_local_secrets.py` as `feat(secrets): add strict local file reader`.

### Task 2: Replace database and replay Keychain readers

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/fleet/database.py`
- Modify: `backend/app/review/database.py`
- Modify: `backend/app/review/credentials.py`
- Modify: `backend/app/sync_remote/cli.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_fleet_database.py`
- Modify: `backend/tests/test_review_database.py`
- Modify: `backend/tests/test_review_replay.py`
- Modify: `backend/tests/test_sync_cli.py`

**Interfaces:**
- Consumes: `read_secret_file(path: str) -> str`.
- Produces config fields `flywheel_database_url_file`, `review_database_url_file`, and `sync_database_url_file` with defaults below `~/Library/Application Support/OrbbecAI-Agent-Platform/secrets/`.

- [ ] **Step 1: Replace Keychain-oriented tests** with RED tests asserting default secret-file paths, environment-variable precedence, file fallback, fail-closed file errors, no analyst fallback for review, `file:` replay resolution, unsafe file rejection, and unsupported `keychain:` rejection.
- [ ] **Step 2: Run** `backend/.venv/bin/pytest -q backend/tests/test_config.py backend/tests/test_fleet_database.py backend/tests/test_review_database.py backend/tests/test_review_replay.py backend/tests/test_sync_cli.py`; expect failures for missing file fields and `file:` support.
- [ ] **Step 3: Modify configuration and resolvers** to remove all Keychain fields/subprocess runners, use the three configured secret files, retain environment variables as explicit first priority, and convert file-reader errors to the existing fail-closed `None`, `CredentialUnavailable`, or `sync_database_unavailable` outcomes.
- [ ] **Step 4: Run** the same five test files; expect all tests to pass.
- [ ] **Step 5: Run** `rg -n "/usr/bin/security|keychain:|_KEYCHAIN_|keychain_service|keychain_account" backend/app backend/tests`; expect no matches.
- [ ] **Step 6: Commit** the Task 2 files as `refactor(secrets): remove keychain readers`.

### Task 3: Update active configuration and operational documentation

**Files:**
- Modify: `registry.yaml`
- Modify: `README.md`
- Modify: `backend/tests/test_registry_models.py`
- Modify: `backend/tests/test_registry_repository.py`
- Modify: `docs/reviews/2026-08-03-feedback-fix-closure-acceptance.md`

**Interfaces:**
- Produces replay reference `file:/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/secrets/ai-fae-dev-replay-token`.

- [ ] **Step 1: Change registry tests** to use `file:` replay references and add a regression assertion that active registry/config/docs contain no Keychain setup command.
- [ ] **Step 2: Run** `backend/.venv/bin/pytest -q backend/tests/test_registry_models.py backend/tests/test_registry_repository.py`; expect failures while fixtures and registry still use `keychain:` or `env:`.
- [ ] **Step 3: Update** `registry.yaml`, README credential instructions, and the acceptance record to describe file-backed persistence and the Keychain retirement. Historical design/plan documents remain historical and are not runtime dependencies.
- [ ] **Step 4: Run** the two registry test files and `rg -n "/usr/bin/security|keychain:|Keychain service|KEYCHAIN_(SERVICE|ACCOUNT)" backend/app backend/tests registry.yaml README.md`; expect tests pass and no matches.
- [ ] **Step 5: Commit** the Task 3 files as `docs(secrets): retire keychain operations`.

### Task 4: Provision, deploy, verify, and delete old entries

**Files:**
- Create outside Git: `~/Library/Application Support/OrbbecAI-Agent-Platform/secrets/{flywheel-analyst-database-url,platform-review-writer-database-url,platform-sync-writer-database-url,ai-fae-dev-replay-token}`
- Runtime only: `~/Library/LaunchAgents/com.orbbec.ai-agent-platform.plist`
- Runtime only: `~/Library/LaunchAgents/com.orbbec.ai-agent-platform-sync.plist`

**Interfaces:**
- Consumes the four current `launchctl getenv` values before they are removed.
- Produces restart-safe Platform and sync services with no Keychain dependency.

- [ ] **Step 1: Run full verification** with `backend/.venv/bin/pytest -q`; expect the complete backend suite to pass.
- [ ] **Step 2: Create the secret directory and files atomically** from the four current launchd variables, set directory mode `0700`, file mode `0600`, and compare each saved value to its source without printing either value.
- [ ] **Step 3: Clear** `PLATFORM_FLYWHEEL_DATABASE_URL`, `PLATFORM_REVIEW_DATABASE_URL`, `PLATFORM_SYNC_DATABASE_URL`, and `AI_FAE_DEV_REPLAY_TOKEN` from the user launchd environment.
- [ ] **Step 4: Restart only** `com.orbbec.ai-agent-platform`; verify its PID changes and `/api/health`, `/review`, `/api/review/overview`, and one Sessions API request return HTTP 200.
- [ ] **Step 5: Start only** `com.orbbec.ai-agent-platform-sync`; verify its run count increments, exit status is 0, and logs contain successful source sync plus review backfill without credentials.
- [ ] **Step 6: Verify replay file resolution** directly through `CredentialResolver` without sending a model request; verify Platform APIs do not expose the reference or token.
- [ ] **Step 7: Delete exactly** the four Keychain services `flywheel-analyst-database-url`, `platform-review-writer-database-url`, `platform-sync-writer-database-url`, and `ai-fae-dev-api` for account `neo`.
- [ ] **Step 8: Restart Platform again** and rerun the four HTTP smokes, direct replay credential resolution, and LaunchAgent state checks; expect no authorization dialog and all checks to pass.
- [ ] **Step 9: Commit any final acceptance-note update**, merge the implementation branch to `master`, push `origin/master`, and report test counts, service states, synchronization result, deleted Keychain entries, and remaining unrelated root-worktree changes.
