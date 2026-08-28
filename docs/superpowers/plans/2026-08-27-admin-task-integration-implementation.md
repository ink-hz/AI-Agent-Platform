# AI ADMIN Task Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose AI ADMIN as a durable professional Agent under HTTP Task Contract v1, reuse the existing job queue, preserve `/office/`, and release read-only administration capabilities before any confirmed write operation.

**Architecture:** Build a private Platform task façade over `src/jobs/` instead of creating another queue. A mapping layer binds Platform task IDs to Admin job IDs and projects existing Admin events into the canonical contract. Platform task tokens and `/office/` browser sessions are distinct authentication paths that resolve to the same `internal_user_id`. Write operations remain absent from the first capability manifest; a later release adds Action proposal/execution with user confirmation and exact digest enforcement.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL, psycopg, pytest, existing Admin job worker, HTTP Task Contract v1.

## Global Constraints

- Work in `/Users/neo/Developer/work/AI-ADMIN-Agent`; preserve all user files and create an isolated worktree before editing.
- Keep `/office/`, `/office/?view=services`, current service portal, shuttle, lodging, feedback, and DingTalk bot behavior unchanged.
- Reuse `admin_jobs`, `admin_job_events`, and the existing job worker; do not create a competing scheduler.
- First release is read-only: service lookup, shuttle lookup, lodging information, and the caller's feedback/status. Reservation, application, mutation, or submit methods are not registered.
- Platform task endpoints use short-lived signed task tokens and never accept a Platform browser Cookie.
- `/office/` continues to use Platform enterprise identity; no second account system is added.
- Event reads for Platform use finite JSON with `wait_seconds=0`; existing SSE behavior for existing consumers may remain.
- Upstream independently enforces capability version, scope, deadline, idempotency, and terminal irreversibility.
- Run CI and the shared contract suite with Python 3.11+.

---

### Task 1: Establish an isolated Admin baseline

**Files:**
- Verify: `requirements.txt`
- Verify: `pyproject.toml`
- Test: `tests/unit/test_job_api.py`
- Test: `tests/unit/test_job_queue_migration.py`
- Test: `tests/unit/test_platform_identity.py`
- Test: `tests/unit/test_service_portal_auth.py`

- [ ] **Step 1: Preserve the current dirty checkout and create a clean worktree**

```bash
cd /Users/neo/Developer/work/AI-ADMIN-Agent
git fetch origin
git status --short
git worktree add .worktrees/platform-task-v1 -b feat/platform-task-v1 origin/master
cd .worktrees/platform-task-v1
```

- [ ] **Step 2: Verify Python 3.11 and install dependencies**

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python --version
```

- [ ] **Step 3: Run the queue and identity baseline**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_job_api.py \
  tests/unit/test_job_queue_migration.py \
  tests/unit/test_platform_identity.py \
  tests/unit/test_service_portal_auth.py
```

Expected: all selected tests pass; record exact count.

### Task 2: Pin the shared contract runner

**Files:**
- Create: `scripts/run_platform_task_contract.sh`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/unit/test_platform_task_contract_pin.py`

- [ ] **Step 1: Write the failing pin test**

Assert that missing `CONTRACT_TEST_COMMIT`, missing `CONTRACT_TEST_SHA256`, a digest mismatch, or Python below 3.11 fails before any contract test runs.

- [ ] **Step 2: Prove RED**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_contract_pin.py
```

- [ ] **Step 3: Implement sorted-directory hashing and pinned checkout**

Use the same shell runner interface as FAE. Do not copy the contract tests into this repository.

- [ ] **Step 4: Prove GREEN**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_contract_pin.py
CONTRACT_MANIFEST=/Users/neo/Developer/work/AI-Agent-Platform/deploy/cloud/http-task-contract.release.json
CONTRACT_TEST_COMMIT="$(jq -r .source_commit "$CONTRACT_MANIFEST")" \
CONTRACT_TEST_SHA256="$(jq -r .sha256 "$CONTRACT_MANIFEST")" \
  scripts/run_platform_task_contract.sh
```

- [ ] **Step 5: Commit**

```bash
git add scripts/run_platform_task_contract.sh .github/workflows/ci.yml tests/unit/test_platform_task_contract_pin.py
git commit -m "test: pin platform task contract"
```

### Task 3: Add a Platform-to-Admin job mapping without rebuilding Jobs

**Files:**
- Create: `migrations/016_platform_task_facade.sql`
- Create: `src/platform_tasks/models.py`
- Create: `src/platform_tasks/store.py`
- Create: `src/platform_tasks/postgres_store.py`
- Create: `src/platform_tasks/__init__.py`
- Create: `tests/unit/test_platform_task_migration.py`
- Create: `tests/unit/test_platform_task_store.py`

**Interfaces:**
- One `platform_task_id` maps to one existing `admin_jobs.id`.
- Follow-up messages and task event delivery remain idempotent by Platform sequence.
- Mapping stores Platform subject, scopes, capability version, deadline, and terminal projection.

- [ ] **Step 1: Write migration tests**

Assert unique `platform_task_id`, unique `(platform_task_id, message_seq)`, required `deadline_at`, immutable Admin job binding, terminal irreversibility, and no plaintext task token/Cookie.

- [ ] **Step 2: Prove RED**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_migration.py tests/unit/test_platform_task_store.py
```

- [ ] **Step 3: Implement migration and mapping store**

The mapping may refer to `admin_jobs` by foreign key. Existing queue status remains the execution authority; the façade supplies contract state and normalized event sequence.

- [ ] **Step 4: Prove replay behavior**

Creating the same `idempotency_key` must return the same Platform task and Admin job. A different payload with the same key must return a stable conflict.

- [ ] **Step 5: Commit**

```bash
git add migrations/016_platform_task_facade.sql src/platform_tasks tests/unit/test_platform_task_*.py
git commit -m "feat: map platform tasks to admin jobs"
```

### Task 4: Add the authenticated private task façade

**Files:**
- Create: `src/platform_tasks/identity.py`
- Create: `src/platform_tasks/routes.py`
- Modify: `src/api/routes.py`
- Modify: `src/config.py`
- Create: `tests/unit/test_platform_task_identity.py`
- Create: `tests/unit/test_platform_task_routes.py`

**Interfaces:**
- `GET /internal/platform-tasks/capabilities`
- `GET /internal/platform-tasks/health`
- `POST /internal/platform-tasks`
- `GET /internal/platform-tasks/{task_id}`
- `GET /internal/platform-tasks/{task_id}/events?after=<seq>&limit=<n>&wait_seconds=0`

- [ ] **Step 1: Write failing identity tests**

Cover expired token, wrong audience, wrong Agent ID, task mismatch, scope mismatch, symlinked verification key, Platform Cookie without bearer token, and browser identity fields attempting to override the subject.

- [ ] **Step 2: Write failing immediate-create tests**

Creation must return `202` before the existing worker claims the mapped job and must reject a stale capability version with current version metadata.

- [ ] **Step 3: Implement routes over the existing queue**

The Platform create request enqueues an Admin job using the Platform idempotency key and stores the mapping in the same transaction where possible; otherwise use an explicit compensating failure state, never an untracked job.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_identity.py \
  tests/unit/test_platform_task_routes.py \
  tests/unit/test_job_api.py
```

- [ ] **Step 5: Commit**

```bash
git add src/platform_tasks/identity.py src/platform_tasks/routes.py src/api/routes.py src/config.py tests/unit/test_platform_task_*.py
git commit -m "feat: expose authenticated admin task facade"
```

### Task 5: Normalize existing Job events and enforce deadlines

**Files:**
- Create: `src/platform_tasks/event_projection.py`
- Modify: `src/platform_tasks/routes.py`
- Modify: `src/jobs/worker.py`
- Modify: `src/jobs/store.py`
- Modify: `src/jobs/postgres_store.py`
- Create: `tests/unit/test_platform_task_events.py`
- Create: `tests/unit/test_platform_task_deadline.py`

**Interfaces:**
- Existing event names project to canonical `work_update`, `message`, `finding`, `input_required`, `result`, `failed`, `cancelled`, `timeout`.
- Platform event pages are finite JSON; `wait_seconds=0` is mandatory for task-token callers.

- [ ] **Step 1: Write failing event mapping and gap tests**

Assert a gap or unknown event fails only the mapped task, not the Admin worker. Assert `timed_out` is rejected and contract output uses `timeout`.

- [ ] **Step 2: Write failing deadline tests**

Cover expiry before claim, during work, after underlying result but before Platform projection, and late completion after a terminal timeout.

- [ ] **Step 3: Implement normalization and hard deadline checks**

Check `deadline_at` before an Admin capability call and before committing its result. Reaper expiry must not depend on Platform polling.

- [ ] **Step 4: Prove GREEN**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_events.py \
  tests/unit/test_platform_task_deadline.py \
  tests/unit/test_job_api.py
```

- [ ] **Step 5: Commit**

```bash
git add src/platform_tasks src/jobs tests/unit/test_platform_task_*.py
git commit -m "feat: enforce admin task event contract"
```

### Task 6: Release a code-enforced read-only capability surface

**Files:**
- Create: `src/platform_tasks/read_only_gateway.py`
- Create: `src/platform_tasks/capabilities.py`
- Modify: `src/jobs/worker.py`
- Create: `tests/unit/test_platform_task_read_only.py`

**Interfaces:**
- Allowed: service catalog/read, shuttle schedule/read, lodging policy/read, caller-owned feedback/status read.
- Not implemented: shuttle reservation, lodging application, feedback mutation, admin operation, or generic route invocation.

- [ ] **Step 1: Write the negative capability tests first**

For every current write service, assert there is no registered method and a crafted method name returns `capability_not_available`, not a forwarded request.

- [ ] **Step 2: Implement an explicit read-only dispatch table**

Do not infer read safety from HTTP verb or function name. Each registered handler independently rechecks scope and subject ownership.

- [ ] **Step 3: Run read-only and existing business tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_read_only.py \
  tests/unit/test_service_portal_auth.py \
  tests/unit/test_shuttle_identity.py \
  tests/unit/test_lodging_identity.py
```

- [ ] **Step 4: Commit**

```bash
git add src/platform_tasks/read_only_gateway.py src/platform_tasks/capabilities.py src/jobs/worker.py tests/unit/test_platform_task_read_only.py
git commit -m "feat: expose read-only admin agent capabilities"
```

### Task 7: Prove one enterprise identity across Platform and `/office/`

**Files:**
- Modify: `src/service_portal/platform_identity.py`
- Modify: `src/service_portal/identity_provider.py`
- Modify: `src/api/identity_state.py`
- Create: `tests/unit/test_platform_task_subject.py`

- [ ] **Step 1: Write failing subject-correlation tests**

Verify an `/office/` browser request and a Platform task token for the same employee resolve to the same `internal_user_id`, while using different credentials. Verify name, department, phone, or role from the browser cannot replace the subject.

- [ ] **Step 2: Implement the common subject model**

Keep current `/office/` validation and CSRF behavior. Task requests construct the same domain subject from signed claims, not from the browser Cookie.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_subject.py \
  tests/unit/test_platform_identity.py \
  tests/unit/test_service_portal_auth.py
```

- [ ] **Step 4: Commit**

```bash
git add src/service_portal src/api/identity_state.py tests/unit/test_platform_task_subject.py
git commit -m "refactor: unify admin platform subject identity"
```

### Task 8: Add follow-up and cancellation without exposing write actions

**Files:**
- Modify: `src/platform_tasks/routes.py`
- Modify: `src/platform_tasks/store.py`
- Modify: `src/platform_tasks/postgres_store.py`
- Modify: `src/jobs/worker.py`
- Create: `tests/unit/test_platform_task_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover duplicate follow-up sequence, append after terminal, repeated cancel, cancel while queued/running, and terminal result after cancellation.

- [ ] **Step 2: Implement idempotent message/cancel routes**

Map follow-up to the existing job's persistent session rather than enqueuing an unrelated conversation. Cancellation marks both the façade and underlying job, with terminal precedence.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_lifecycle.py tests/unit/test_job_api.py
```

- [ ] **Step 4: Commit**

```bash
git add src/platform_tasks src/jobs/worker.py tests/unit/test_platform_task_lifecycle.py
git commit -m "feat: add admin task follow-up and cancellation"
```

### Task 9: Add confirmed writes as a separate release

**Files:**
- Create: `migrations/017_platform_task_actions.sql`
- Create: `src/platform_tasks/actions.py`
- Modify: `src/platform_tasks/routes.py`
- Modify: `src/platform_tasks/capabilities.py`
- Create: `tests/unit/test_platform_task_actions.py`

**Interfaces:**
- Proposal event carries Action Contract v1 fields.
- Execute accepts only `action_id`, `action_digest`, and `idempotency_key`.
- Digest is recomputed from the Admin-persisted proposal parameters using RFC 8785 JCS.

- [ ] **Step 1: Keep this task disabled until Platform/VOC confirmation acceptance passes**

Record the accepted Platform release and migration 051 evidence in the Admin implementation log.

- [ ] **Step 2: Write failing Action tests**

Cover no confirmation, rejection, expiry, superseded digest, duplicate execution, wrong subject, wrong scope, changed world state, deadline too short, crash before/after domain commit, and late retry.

- [ ] **Step 3: Implement atomic domain execution**

Persist proposal parameters before emitting `action_required`. Execute those exact persisted parameters. If shuttle capacity or lodging state changed, fail explicitly; never adapt parameters after confirmation.

- [ ] **Step 4: Register write capability only after tests pass**

Bump `capability_version` and add the exact write scopes in a separate manifest commit. Keep the read-only manifest available for rollback.

- [ ] **Step 5: Run tests and commit**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_actions.py
git add migrations/017_platform_task_actions.sql src/platform_tasks tests/unit/test_platform_task_actions.py
git commit -m "feat: execute confirmed admin actions exactly once"
```

### Task 10: Contract, `/office/` regression, release, and rollback

**Files:**
- Create: `docs/runbooks/platform-task-v1.md`
- Modify: `.env.example`
- Modify: `scripts/check_formal_beta_release_gate.py`

- [ ] **Step 1: Run the full Admin suite and pinned contract**

```bash
.venv/bin/python -m pytest -q
CONTRACT_MANIFEST=/Users/neo/Developer/work/AI-Agent-Platform/deploy/cloud/http-task-contract.release.json
CONTRACT_TEST_COMMIT="$(jq -r .source_commit "$CONTRACT_MANIFEST")" \
CONTRACT_TEST_SHA256="$(jq -r .sha256 "$CONTRACT_MANIFEST")" \
  scripts/run_platform_task_contract.sh
```

- [ ] **Step 2: Deploy with Platform delegation disabled**

Apply migration 016, deploy code, verify private health, then verify `/office/`, `/office/?view=services`, chat, service portal, shuttle, lodging, feedback, static assets, mobile login, and CSRF.

- [ ] **Step 3: Enable read-only delegation and run a real task**

Verify create receipt, finite event replay, result, cancellation, identity attribution, and no write-capability advertisement.

- [ ] **Step 4: Verify rollback**

Disable Catalog delegation, drain or explicitly fail active Platform tasks, restore the prior application release, and re-run `/office/` acceptance. Do not revert migration data or revive terminal tasks.

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/platform-task-v1.md .env.example scripts/check_formal_beta_release_gate.py
git commit -m "ops: release admin platform task integration"
```

## AI ADMIN Completion Gate

- [ ] Existing durable Jobs are reused; no parallel scheduler exists.
- [ ] Shared contract passes under Python 3.11+ at the pinned commit and digest.
- [ ] First release advertises and executes only code-enforced read operations.
- [ ] Platform and `/office/` resolve the same employee to one `internal_user_id` without sharing task/browser credentials.
- [ ] Event pages are finite, non-blocking, gap-free, deadline-bound, and terminally irreversible.
- [ ] `/office/?view=services` and all current administrative functions pass regression.
- [ ] Confirmed writes, when later enabled, execute exact persisted parameters exactly once.
