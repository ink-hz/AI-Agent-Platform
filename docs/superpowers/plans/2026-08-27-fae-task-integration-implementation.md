# FAE Task Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AI FAE a durable, enterprise-authenticated professional Agent that implements HTTP Task Contract v1, preserves its public customer workspace, and consumes Platform-owned attachments without learning storage credentials.

**Architecture:** Add a private task façade beside FAE's existing `/chat` path. Task creation returns immediately, the existing Opus Loop runs behind a durable PostgreSQL queue, and Platform reads finite event pages with `wait_seconds=0`. Browser SSO and Brain task identity are separate inputs that resolve to the same Platform `internal_user_id`; attachment bytes flow only through short-lived Platform grants.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, PostgreSQL, psycopg, pytest, Anthropic Claude Opus, HTTP Task Contract v1.

## Global Constraints

- Work in `/Users/neo/Developer/work/AI-FAE-Agent`; create an isolated worktree from the reviewed remote target before edits.
- Do not change or remove `https://fae.orbbec.com.cn/`, the current public customer flow, `/chat`, `/history`, or review endpoints.
- Private task endpoints accept only short-lived Platform-signed tokens; they never accept Platform cookies or browser-provided identities.
- `POST /internal/tasks` returns `202` without waiting for inference.
- Event sequence starts at 1, is gap-free and terminal states are irreversible.
- Platform polling always uses `wait_seconds=0`; no Platform worker consumes FAE SSE or long polling.
- FAE enforces `capability_version`, scope, and `deadline_at` independently.
- Attachments are read only through Media Gateway grants; FAE never receives MinIO credentials or object keys.
- Run every test with Python 3.11 or newer.

---

### Task 1: Establish an isolated FAE baseline

**Files:**
- Verify: `pyproject.toml`
- Verify: `requirements.txt`
- Test: `tests/unit/test_routes_chat.py`
- Test: `tests/unit/test_loop_runtime.py`
- Test: `tests/integration/test_full_chat.py`

- [ ] **Step 1: Create the worktree from the reviewed remote branch**

```bash
cd /Users/neo/Developer/work/AI-FAE-Agent
git fetch origin
git worktree add .worktrees/platform-task-v1 -b feat/platform-task-v1 origin/master
cd .worktrees/platform-task-v1
```

- [ ] **Step 2: Create or verify the Python 3.11 environment**

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python --version
```

Expected: Python 3.11+.

- [ ] **Step 3: Run the unchanged FAE baseline**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_routes_chat.py \
  tests/unit/test_loop_runtime.py \
  tests/integration/test_full_chat.py
```

Expected: all selected tests pass. Record the exact count in the implementation log.

### Task 2: Pin and run the shared HTTP Task Contract v1

**Files:**
- Create: `scripts/run_platform_task_contract.sh`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/contract/test_contract_pin.py`

**Interfaces:**
- Consumes: Platform `contracts/http_task_v1/` at `CONTRACT_TEST_COMMIT` and `CONTRACT_TEST_SHA256`.
- Produces: a FAE test target that runs the exact Platform-owned suite under Python 3.11+.

- [ ] **Step 1: Write the failing pin test**

```python
def test_contract_runner_requires_commit_and_digest(monkeypatch):
    monkeypatch.delenv("CONTRACT_TEST_COMMIT", raising=False)
    monkeypatch.delenv("CONTRACT_TEST_SHA256", raising=False)
    result = run_contract_pin_check()
    assert result.reason == "contract_pin_missing"
```

- [ ] **Step 2: Prove RED**

```bash
.venv/bin/python -m pytest -q tests/contract/test_contract_pin.py
```

Expected: FAIL because the pin checker does not exist.

- [ ] **Step 3: Implement deterministic checkout and digest verification**

`scripts/run_platform_task_contract.sh` must:

1. reject missing commit or digest;
2. fetch only the pinned Platform commit;
3. hash the contract directory in sorted path order;
4. reject a mismatch before running tests;
5. invoke Python 3.11+ and `pytest contracts/http_task_v1`.

- [ ] **Step 4: Add the CI job and prove GREEN**

```bash
.venv/bin/python -m pytest -q tests/contract/test_contract_pin.py
CONTRACT_MANIFEST=/Users/neo/Developer/work/AI-Agent-Platform/deploy/cloud/http-task-contract.release.json
CONTRACT_TEST_COMMIT="$(jq -r .source_commit "$CONTRACT_MANIFEST")" \
CONTRACT_TEST_SHA256="$(jq -r .sha256 "$CONTRACT_MANIFEST")" \
  scripts/run_platform_task_contract.sh
```

Expected: pin test and shared contract suite pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_platform_task_contract.sh .github/workflows/ci.yml tests/contract/test_contract_pin.py
git commit -m "test: pin platform task contract"
```

### Task 3: Add the durable FAE task store

**Files:**
- Create: `migrations/004_platform_task_queue.sql`
- Create: `src/platform_tasks/models.py`
- Create: `src/platform_tasks/store.py`
- Create: `src/platform_tasks/postgres_store.py`
- Create: `src/platform_tasks/__init__.py`
- Create: `tests/unit/test_platform_task_migration.py`
- Create: `tests/unit/test_platform_task_store.py`

**Interfaces:**
- `create_task(idempotency_key, platform_task_id, capability_version, deadline_at, subject, scopes, prompt, attachment_refs)`
- `append_event(task_id, kind, payload) -> seq`
- `events_after(task_id, after, limit) -> list[TaskEvent]`
- `request_cancel(task_id)`, `append_message(task_id, message_seq, content)`

- [ ] **Step 1: Write migration tests for invariants**

Tests must assert:

- unique `idempotency_key` and unique `platform_task_id`;
- status check includes `queued`, `running`, `waiting_input`, `waiting_confirmation`, `completed`, `failed`, `cancelled`, `timeout`;
- `(task_id, seq)` and `(task_id, message_seq)` are unique;
- terminal state cannot transition;
- event sequence is allocated under a task-row lock;
- `deadline_at` is required.

- [ ] **Step 2: Prove RED**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_migration.py \
  tests/unit/test_platform_task_store.py
```

- [ ] **Step 3: Implement migration and in-memory/PostgreSQL stores**

Persist the full original request envelope encrypted or as approved non-sensitive structural fields. Store no Platform cookie, DingTalk raw identifier, or MinIO credential.

- [ ] **Step 4: Prove idempotency and terminal irreversibility**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_migration.py \
  tests/unit/test_platform_task_store.py
```

Expected: PASS, including replay of the same create request returning the same FAE task ID.

- [ ] **Step 5: Commit**

```bash
git add migrations/004_platform_task_queue.sql src/platform_tasks tests/unit/test_platform_task_*.py
git commit -m "feat: add durable platform task queue"
```

### Task 4: Enforce Platform task identity and capabilities

**Files:**
- Create: `src/platform_tasks/identity.py`
- Create: `src/platform_tasks/routes.py`
- Modify: `src/api/server.py`
- Modify: `src/config.py`
- Create: `tests/unit/test_platform_task_identity.py`
- Create: `tests/unit/test_platform_task_routes.py`

**Interfaces:**
- `GET /internal/tasks/capabilities`
- `GET /internal/tasks/health`
- `POST /internal/tasks`
- Token claims: issuer, audience, subject/internal user, platform task ID, Agent ID, scopes, issued/expiry time, request ID.

- [ ] **Step 1: Write failing token-boundary tests**

Cover wrong audience, wrong `agent_id`, expired token, mismatched `platform_task_id`, missing scope, symlinked public key, and browser Cookie without task token.

- [ ] **Step 2: Write failing capability-version tests**

```python
def test_create_rejects_stale_capability_version(client, task_token):
    response = client.post(
        "/internal/tasks",
        headers={"Authorization": f"Bearer {task_token}"},
        json={**valid_request(), "capability_version": 1},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "capability_changed"
    assert response.json()["current_capability_version"] == 2
```

- [ ] **Step 3: Implement verification and private routers**

The create route returns `202` and a receipt only. Configure the current FAE capability version in one source used by both capability response and create validation.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_identity.py \
  tests/unit/test_platform_task_routes.py
```

- [ ] **Step 5: Commit**

```bash
git add src/platform_tasks/identity.py src/platform_tasks/routes.py src/api/server.py src/config.py tests/unit/test_platform_task_*.py
git commit -m "feat: expose authenticated FAE task facade"
```

### Task 5: Run the real FAE Opus Loop behind the queue

**Files:**
- Create: `src/platform_tasks/worker.py`
- Create: `src/platform_tasks/event_projection.py`
- Modify: `src/agent/loop/runtime.py`
- Modify: `src/api/server.py`
- Create: `tests/unit/test_platform_task_worker.py`
- Create: `tests/unit/test_platform_task_events.py`

**Interfaces:**
- Canonical events: `work_update`, `message`, `finding`, `input_required`, `action_required`, `artifact`, `result`, `failed`, `cancelled`, `timeout`.
- FAE-only `sources` data is projected inside `finding` or `artifact` payloads, never as an unknown event kind.

- [ ] **Step 1: Write failing worker tests**

Assert that create returns before inference begins, the worker invokes the existing Loop, events are gap-free, `deadline_at` causes `timeout`, and late Loop output cannot revive a terminal task.

- [ ] **Step 2: Write failing event mapping tests**

Cover accepted/started/progress/sources/result mappings and reject the non-contract terminal spelling `timed_out`.

- [ ] **Step 3: Implement the queue worker and projection**

The worker checks deadline before claim, before each model/tool continuation, and before final persistence. Existing public `/chat` remains on its original path.

- [ ] **Step 4: Prove GREEN**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_worker.py \
  tests/unit/test_platform_task_events.py \
  tests/unit/test_loop_runtime.py
```

- [ ] **Step 5: Commit**

```bash
git add src/platform_tasks/worker.py src/platform_tasks/event_projection.py src/agent/loop/runtime.py src/api/server.py tests/unit/test_platform_task_*.py
git commit -m "feat: execute durable FAE platform tasks"
```

### Task 6: Add finite replay, follow-up, cancellation, and deadline enforcement

**Files:**
- Modify: `src/platform_tasks/routes.py`
- Modify: `src/platform_tasks/store.py`
- Modify: `src/platform_tasks/postgres_store.py`
- Modify: `src/platform_tasks/worker.py`
- Create: `tests/unit/test_platform_task_lifecycle.py`

**Interfaces:**
- `GET /internal/tasks/{task_id}`
- `GET /internal/tasks/{task_id}/events?after=<seq>&limit=<n>&wait_seconds=0`
- `POST /internal/tasks/{task_id}/messages`
- `POST /internal/tasks/{task_id}/cancel`

- [ ] **Step 1: Write failing lifecycle tests**

Cover page replay, duplicate `message_seq`, cancel replay, cancellation during execution, `wait_seconds>0` rejection for Platform credentials, and deadline expiry without a polling client.

- [ ] **Step 2: Prove RED**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_lifecycle.py
```

- [ ] **Step 3: Implement finite cursor endpoints and reaper**

Use a bounded JSON page. The deadline reaper must run independently of polling and append exactly one `timeout` terminal event.

- [ ] **Step 4: Prove GREEN and regression**

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_platform_task_lifecycle.py \
  tests/unit/test_routes_chat.py \
  tests/integration/test_full_chat.py
```

- [ ] **Step 5: Commit**

```bash
git add src/platform_tasks tests/unit/test_platform_task_lifecycle.py
git commit -m "feat: complete FAE task lifecycle contract"
```

### Task 7: Reuse Platform DingTalk identity for the enterprise FAE workspace

**Files:**
- Create: `src/platform_identity/client.py`
- Create: `src/platform_identity/models.py`
- Modify: `src/api/webui.py`
- Modify: `src/api/server.py`
- Create: `tests/unit/test_platform_identity.py`

**Interfaces:**
- Browser identity is verified server-to-server against Platform's minimal subject endpoint.
- Public customer behavior remains separately available and unchanged.

- [ ] **Step 1: Write failing identity separation tests**

Assert that enterprise mode accepts the Platform session only through the approved same-site validation path, rejects browser-submitted user fields, never logs Cookie/PII, and fails closed on Platform 401/403/503. Assert public customer mode does not require an Orbbec DingTalk identity.

- [ ] **Step 2: Implement the minimal identity client and enterprise route guard**

Resolve `{internal_user_id, display_name, active}` only. Do not copy DingTalk login logic into FAE and do not forward the Platform Cookie beyond the exact loopback/account call.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_identity.py tests/unit/test_routes_chat.py
```

- [ ] **Step 4: Commit**

```bash
git add src/platform_identity src/api/webui.py src/api/server.py tests/unit/test_platform_identity.py
git commit -m "feat: bind enterprise FAE workspace to platform identity"
```

### Task 8: Consume Platform attachments through grants

**Files:**
- Create: `src/platform_tasks/media_gateway.py`
- Modify: `src/platform_tasks/models.py`
- Modify: `src/platform_tasks/worker.py`
- Create: `tests/unit/test_platform_task_attachments.py`

**Interfaces:**
- Input references contain attachment/grant IDs, declared media type, size, digest, and expiry—not object keys.
- Reads use the task-bound Media Gateway grant.
- Outputs return artifact metadata for Platform ingestion.

- [ ] **Step 1: Write failing grant tests**

Cover wrong task, wrong Agent, expired grant, non-ready object, digest mismatch, grant reuse after task terminal, image input, document input, and artifact output.

- [ ] **Step 2: Implement streaming grant reads with bounded size**

Do not persist bearer grants. Verify declared digest after streaming and before passing content to the FAE Loop.

- [ ] **Step 3: Prove GREEN**

```bash
.venv/bin/python -m pytest -q tests/unit/test_platform_task_attachments.py
```

- [ ] **Step 4: Commit**

```bash
git add src/platform_tasks/media_gateway.py src/platform_tasks/models.py src/platform_tasks/worker.py tests/unit/test_platform_task_attachments.py
git commit -m "feat: consume platform attachment grants"
```

### Task 9: Run contract, regression, and release acceptance

**Files:**
- Modify: `deploy/compose.yaml`
- Modify: `deploy/accept.sh`
- Create: `docs/operations/platform-task-v1.md`
- Test: shared `contracts/http_task_v1/`

- [ ] **Step 1: Add a disabled-by-default task façade flag**

Deploy code and migration first. Keep Catalog delegation disabled until the shared contract suite and real task smoke test pass.

- [ ] **Step 2: Run all FAE tests and contract tests**

```bash
.venv/bin/python -m pytest -q
CONTRACT_MANIFEST=/Users/neo/Developer/work/AI-Agent-Platform/deploy/cloud/http-task-contract.release.json
CONTRACT_TEST_COMMIT="$(jq -r .source_commit "$CONTRACT_MANIFEST")" \
CONTRACT_TEST_SHA256="$(jq -r .sha256 "$CONTRACT_MANIFEST")" \
  scripts/run_platform_task_contract.sh
```

- [ ] **Step 3: Verify invariants before and after deployment**

Record container/image ID, StartedAt, RestartCount, public page status, `/chat` smoke result, private task health, DB migration state, and FAE public IP/domain behavior.

- [ ] **Step 4: Enable one Platform FAE delegation**

Run a task that produces findings and a final result, then a document/image task after Attachment A3. Verify event replay from a mid-stream cursor and cancel one test task.

- [ ] **Step 5: Verify rollback**

Disable Catalog delegation first, drain or explicitly fail active tasks, restore the prior FAE release, and confirm public FAE behavior is unchanged. Do not roll a terminal task back to running.

- [ ] **Step 6: Commit**

```bash
git add deploy docs/operations/platform-task-v1.md
git commit -m "ops: release FAE platform task integration"
```

## FAE Completion Gate

- [ ] Shared contract suite passes from the pinned commit and digest on Python 3.11+.
- [ ] Task create is immediate, idempotent, deadline-bound, replayable, cancellable, and terminally irreversible.
- [ ] Platform receives real FAE events and sources without mock progress.
- [ ] Enterprise DingTalk identity resolves to Platform `internal_user_id`; public customer access remains unchanged.
- [ ] Image/document input crosses only the Attachment Media Gateway grant.
- [ ] `https://fae.orbbec.com.cn/` and existing `/chat` regression evidence are preserved.
