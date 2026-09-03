# Platform Sync and Failed-Turn Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore ADMIN observability synchronization, fail deployment on unapplied observability migrations, and display explicit failed turns accurately.

**Architecture:** A repository-owned psql migration command applies an explicitly selected numbered observability migration under a session advisory lock and records its SHA-256 checksum. It never bulk-replays un-ledgered historical SQL. The Session UI derives a safe answer presentation from existing outcome/Trace fields, preserving genuine legacy missing-answer records.

**Tech Stack:** Python, PostgreSQL/psql, Bash, FastAPI/Pydantic, React/TypeScript, Vitest, Pytest.

## Global Constraints

- Apply existing migration `011_admin_session_subject_links.sql`; do not create an ad-hoc table.
- Missing required relations abort before a Platform process/image switch.
- Never expose provider payloads, credentials, private paths, or restricted evidence.
- Do not change shared Nginx, `/office/`, MetaBot, FAE, or other application directories.
- Production staging is `/data/staging/ai-agent-platform/<deployment_id>/` with exact-path trap cleanup and the approved disk/image/retention gates.

---

### Task 1: Add a checksummed observability migration runner

**Files:**
- Create: `deploy/migrate-observability`
- Create: `backend/tests/test_observability_migration_runner.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: owner DSN file and one explicit `backend/migrations/[0-9][0-9][0-9]_*.sql` argument; this release invokes it for migration 011 only.
- Produces: `platform_sync.schema_migrations(version, sha256, applied_at)`, advisory-locked application, and non-zero exit on drift/failure.

- [ ] **Step 1: Write failing runner contract tests**

Assert the script uses `set -Eeuo pipefail`, validates an absolute regular mode-0600 non-symlink DSN file, acquires a PostgreSQL advisory lock, computes SHA-256, rejects checksum drift, invokes `psql -v ON_ERROR_STOP=1`, and verifies migration 011 plus `to_regclass`. Assert no DSN is printed and no migration failure is downgraded.

- [ ] **Step 2: Run RED**

Run `backend/.venv/bin/python -m pytest -q backend/tests/test_observability_migration_runner.py`; expect missing script.

- [ ] **Step 3: Implement the runner**

Use a private temp directory from `mktemp -d` with an EXIT trap. Generate one psql control script that acquires a session advisory lock, creates the ledger, compares any existing digest, includes the selected idempotent migration with `\ir`, records its digest, verifies the required relation, and unlocks. A failure or interrupted run is safely rerunnable because migration 011 uses `create table if not exists` and exact grants. The final gate uses psql conditionals:

```sql
select (to_regclass('platform_identity.session_subject_links') is not null)::int
  as required_relation_ok \gset
\if :required_relation_ok
\else
  \quit 3
\endif
```

Do not interpolate or echo the DSN into that generated file.

- [ ] **Step 4: Run GREEN and commit**

Run the focused test, `bash -n deploy/migrate-observability`, and `git diff --check`. Commit with `fix(sync): gate observability migrations`.

### Task 2: Prove migration idempotency and sync continuity

**Files:**
- Modify: `backend/tests/test_observability_migration.py`
- Modify: `backend/tests/test_sync_importer.py`
- Modify if needed: `deploy/sync-remote-agents`

**Interfaces:**
- Produces: executable proof that migration 011 can run twice, grants only CRUD on the subject-link table to `platform_sync_writer`, and ADMIN import succeeds afterward.

- [ ] **Step 1: Write failing PostgreSQL integration tests**

Against the isolated database fixture, apply migrations through the runner twice, assert the ledger has one version-011 row, `to_regclass` is non-null, public has no privilege, sync writer has SELECT/INSERT/UPDATE/DELETE but not TRUNCATE/REFERENCES/TRIGGER, and an ADMIN bundle with a verified session subject imports successfully.

- [ ] **Step 2: Run RED**

Run the two focused test files; expect the runner/ledger assertions to fail before implementation.

- [ ] **Step 3: Add a sync preflight**

Before `app.sync_remote.cli`, have the wrapper perform a read-only required-relation check using the existing private sync DSN file. It must return a stable `schema_preflight_failed` status without credentials, export content, or SQL text if the relation is absent.

- [ ] **Step 4: Run GREEN and commit**

Run the focused sync/migration tests plus `backend/tests/test_sync_launchagent.py`. Commit with `test(sync): enforce ADMIN schema continuity`.

### Task 3: Render explicit failed turns

**Files:**
- Create: `webui/src/turnAnswerPresentation.ts`
- Create: `webui/src/turnAnswerPresentation.test.ts`
- Modify: `webui/src/components/TurnCard.tsx`
- Modify: `webui/src/messagePresentation.test.tsx`

**Interfaces:**
- Consumes: `Pick<TurnDetail, 'answer'|'outcome'|'trace_key'|'details'>`.
- Produces: `turnAnswerPresentation(turn)` returning `{kind:'answer', content}` or `{kind:'failed', label, classification}` or `{kind:'missing', label}`.

- [ ] **Step 1: Write failing pure presentation tests**

```ts
expect(turnAnswerPresentation({ answer: '', outcome: 'failed', trace_key: 'trace-1', details: {} }))
  .toEqual({ kind: 'failed', label: '本轮执行失败', classification: null });
expect(turnAnswerPresentation({ answer: '', outcome: null, trace_key: null, details: {} }).kind)
  .toBe('missing');
expect(turnAnswerPresentation({ answer: '完成', outcome: 'failed', trace_key: 'trace-1', details: {} }).kind)
  .toBe('answer');
```

Allowlist public classifications such as `timeout`, `provider_unavailable`, `cancelled`, and `execution_error`; ignore all other detail keys/values.

- [ ] **Step 2: Run RED**

```bash
cd webui && npm test -- turnAnswerPresentation.test.ts messagePresentation.test.tsx
```

Expected: module missing and failed outcome still renders `未记录 Agent 回答`.

- [ ] **Step 3: Implement and render the three states**

Treat non-empty answer as authoritative. Treat lowercased outcomes `failed`, `error`, `cancelled`, `interrupted`, `timed_out`, `timeout`, or a failed Trace marker from allowlisted details as failed. Render `本轮执行失败` plus optional safe classification and existing Trace action; otherwise retain `未记录 Agent 回答`.

- [ ] **Step 4: Run GREEN, build, and commit**

Run all Web UI tests and `npm run build`. Commit with `fix(sessions): distinguish failed agent turns`.

### Task 4: Restore local sync and deploy Platform safely

- [ ] **Step 1: Run full verification**

```bash
backend/.venv/bin/python -m pytest -q backend/tests
cd webui && npm test && npm run build
git diff --check
```

- [ ] **Step 2: Apply migrations before service switch**

Acquire the Platform publication lock. Record `df -B1 / /data`, current release/image and two rollbacks. Run `deploy/migrate-observability` with the owner DSN file, then run ADMIN import and verify latest `platform_sync.runs` is `succeeded` with non-zero sessions/turns; verify latest FAE sync remains succeeded.

- [ ] **Step 3: Publish only Platform**

Stage at `/data/staging/ai-agent-platform/<deployment_id>/`, install exact cleanup trap, build a code/artifact-only release, and switch Platform containers only. Do not modify Nginx or `/office/`. Retain current plus two rollback releases/images and archive/delete only individually validated older Platform targets.

- [ ] **Step 4: Complete local/cloud/UI acceptance**

Verify local and cloud successful HR turns have non-empty answers, cloud generation advances, one-year expiry remains, historical failures render `本轮执行失败`, authenticated Session details show downloads/answers, the five-minute cloud job has no stale queue and last exit zero, and no unrelated service restarted.

- [ ] **Step 5: Produce the mandatory release report**

Include before/after `df`, added sizes, current/two rollback releases, archived/deleted releases, empty staging proof, current/two rollback images, business HTTP results, and an explicit statement about Nginx/other-app changes.
