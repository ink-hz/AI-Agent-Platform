# Agent Brain Platform Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Platform the durable top-level Brain for nine professional Agents, with correct capability versioning, one event-delivery waterline, user-owned Action confirmation, task-local failure isolation, and a shared HTTP contract runner.

**Architecture:** Extend the existing 041/045/046 durable loop rather than replacing it. Migration 049 owns same-batch Task dependencies; migration 050 owns Task/Loop/Wait/event mechanics; migration 051 owns Action authorization and execution. Model-step persistence never competes on event cursors: Wait settlement runs as an idempotent short transaction after commit, after event append, and from the Reaper.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, psycopg 3, PostgreSQL, pytest, Anthropic Messages API, React 18, TypeScript, Vitest.

## Global Constraints

- Run backend tests with `backend/.venv/bin/python -m pytest`; interpreter must be Python `>=3.11`.
- Keep migrations 041/045/046 readable as history; add 049 and 050 instead of editing applied migrations.
- Migration 050 removes `brain_wait_subscriptions.cursors`; `brain_task_event_cursors.delivered_seq` is the sole waterline.
- `submit_answer` with a Pending Action returns a normal rejected Tool Result and never increments `protocol_retry_count`.
- forced + pending transitions to `waiting_confirmation`; it never calls the Provider and never uses `forced_submission_failed`.
- 40001 retries occur only around the short settlement transaction: three retries with 10/25/50ms full-jitter limits.
- Unknown/gapped events fail only that Task with `terminal_reason_code=protocol_violation`; they do not fabricate an upstream event.
- Web/API roles receive no table-level Action update rights; commands use audited `SECURITY DEFINER` functions.
- Keep production `max_steps=12`; 24 is the schema/config hard ceiling until evaluation passes.

---

### Task 1: Establish the Python 3.11 TDD baseline

**Files:**
- Verify: `backend/requirements.txt`
- Verify: `backend/pytest.ini`
- Test: `backend/tests/test_agent_brain_loop_runtime.py`
- Test: `backend/tests/test_agent_brain_live_repository.py`
- Test: `backend/tests/test_agent_brain_runtime_registry.py`

**Interfaces:**
- Consumes: existing repository checkout.
- Produces: reproducible Python 3.11 test command used by every later task.

- [ ] **Step 1: Create the isolated virtual environment if absent**

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
```

- [ ] **Step 2: Verify interpreter and baseline tests**

```bash
backend/.venv/bin/python --version
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_runtime_registry.py
```

Expected: Python 3.11+ and `20 passed` or more; no collection error involving `enum.StrEnum`.

- [ ] **Step 3: Record the baseline without committing the virtual environment**

```bash
git status --short
git check-ignore backend/.venv
```

Expected: `backend/.venv` is ignored and no tracked file changed.

### Task 2: Fix capability-version delegation as an independent P0

**Files:**
- Modify: `backend/app/agent_brain/tool_protocol.py`
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/prompts/brain_v1.md`
- Modify: `deploy/cloud/brain-model.release.json`
- Test: `backend/tests/test_agent_brain_tool_protocol.py`
- Test: `backend/tests/test_agent_brain_loop_runtime.py`
- Test: `backend/tests/test_agent_brain_runtime_registry.py`

**Interfaces:**
- Consumes: `RuntimeAgentRegistry.authorize_task(user_id, agent_id, expected_capability_version)`.
- Produces: `DelegateTaskCall.capability_version: int`; `capability_changed` Tool Result with current version.

- [ ] **Step 1: Write failing protocol and real-registry tests**

```python
def test_delegate_requires_capability_version() -> None:
    block = _delegate_block()
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch([block], ToolLimits())
    assert error.value.code == "invalid_tool_input"


def test_real_hr_version_two_can_authorize(real_registry, owner_id) -> None:
    decision = real_registry.authorize_task(owner_id, "hr-bot", 2)
    assert decision.allowed is True
    assert decision.capability_version == 2
```

- [ ] **Step 2: Run RED tests**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_tool_protocol.py \
  backend/tests/test_agent_brain_runtime_registry.py
```

Expected: FAIL because `DelegateTaskCall` has no capability version and the runtime still assumes `1`.

- [ ] **Step 3: Add the strict tool field and propagate it**

```python
class DelegateTaskCall(_StrictToolCall):
    agent_id: str
    capability_version: int
    objective: str
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str

    @field_validator("capability_version")
    @classmethod
    def _capability_version_is_positive(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("Capability version invalid")
        return value
```

In `loop_runtime.py`, call:

```python
decision = self._runtime_registry.authorize_task(
    owner_id,
    parsed.call.agent_id,
    parsed.call.capability_version,
)
```

When rejected for version mismatch, persist this normal Tool Result and no Task:

```python
{
    "status": "rejected",
    "reason": "capability_changed",
    "current_capability_version": decision.capability_version,
    "must_call_list_agents": True,
}
```

- [ ] **Step 4: Update the system prompt and release hash**

Add the exact instruction to `brain_v1.md`:

```text
delegate_task.capability_version 必须原样使用最近一次 list_agents 返回的版本；
收到 capability_changed 后先重新 list_agents，同一 Agent 连续两次变化后停止派发。
```

Recalculate `system_prompt_sha256` using the repository’s existing prompt-hash test/helper and update `brain-model.release.json`.

- [ ] **Step 5: Run GREEN tests and full P0 regression**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_tool_protocol.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_runtime_registry.py \
  backend/tests/test_agent_brain_prompt.py
```

Expected: PASS; HR version 2 creates a Task, version 1 returns `capability_changed`, two mismatches return `capability_version_unstable`.

- [ ] **Step 6: Commit P0 separately**

```bash
git add backend/app/agent_brain/tool_protocol.py \
  backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/prompts/brain_v1.md \
  deploy/cloud/brain-model.release.json \
  backend/tests/test_agent_brain_tool_protocol.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_runtime_registry.py \
  backend/tests/test_agent_brain_prompt.py
git commit -m "fix: propagate agent capability versions"
```

### Task 3: Publish the single-source HTTP Task Contract test runner

**Files:**
- Create: `contracts/http_task_v1/pyproject.toml`
- Create: `contracts/http_task_v1/orbbec_task_contract/__init__.py`
- Create: `contracts/http_task_v1/orbbec_task_contract/models.py`
- Create: `contracts/http_task_v1/orbbec_task_contract/runner.py`
- Create: `contracts/http_task_v1/tests/test_contract_driver.py`
- Create: `contracts/http_task_v1/fixtures/action_digest.json`
- Create: `scripts/hash_http_task_contract.py`
- Create: `deploy/cloud/http-task-contract.release.json`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_http_task_contract_asset.py`

**Interfaces:**
- Consumes: an HTTP base URL and a Task Token supplied by the target repository’s test fixture.
- Produces: CLI `python -m orbbec_task_contract.runner --base-url ...`; frozen JCS digest fixture.

- [ ] **Step 1: Write failing asset and digest tests**

```python
def test_contract_runner_requires_python_311() -> None:
    project = tomllib.loads(PROJECT.read_text("utf-8"))
    assert project["project"]["requires-python"] == ">=3.11"


def test_action_digest_fixture_is_stable() -> None:
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    assert action_digest(fixture["input"]) == fixture["lowercase_hex"]
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_http_task_contract_asset.py
```

Expected: FAIL because `contracts/http_task_v1` does not exist.

- [ ] **Step 3: Implement the contract models and JCS digest API**

```python
from hashlib import sha256
from uuid import UUID

import jcs
from pydantic import BaseModel, ConfigDict, PositiveInt


class ActionDigestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platform_task_id: UUID
    action_seq: PositiveInt
    action_kind: str
    parameters: dict[str, object]


def action_digest(value: ActionDigestInput) -> str:
    document = {
        "platform_task_id": str(value.platform_task_id),
        "action_seq": value.action_seq,
        "action_kind": value.action_kind,
        "parameters": value.parameters,
    }
    return sha256(jcs.canonicalize(document)).hexdigest()
```

Set `requires-python = ">=3.11"` and expose black-box create/events/cancel/deadline/action test cases through the runner.

- [ ] **Step 4: Add the fixed cross-repository fixture**

```json
{
  "input": {
    "platform_task_id": "0d8f0764-91be-4af5-b4d8-e79d58ab3b07",
    "action_seq": 1,
    "action_kind": "voc.submit",
    "parameters": {"title": "机器人客户反馈", "priority": 2}
  },
  "lowercase_hex": "0f7a671c2f01c4c4e9ce5ec974fd4bf7706290765319bfcee1071d28c8578486"
}
```

Set the contract package dependency to `jcs==0.2.1`; add the same pinned dependency to
`backend/requirements.txt`. Independently verify the frozen literal with the canonical UTF-8 byte string
from the design before accepting the test.

- [ ] **Step 5: Run GREEN**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_http_task_contract_asset.py \
  contracts/http_task_v1/tests/test_contract_driver.py
```

Expected: PASS; runner refuses Python 3.10 and verifies finite `wait_seconds=0` pages.

- [ ] **Step 6: Commit the contract runner**

```bash
git add contracts/http_task_v1 scripts/hash_http_task_contract.py \
  backend/requirements.txt backend/tests/test_http_task_contract_asset.py
git commit -m "feat: publish HTTP task contract v1 tests"
```

- [ ] **Step 7: Freeze the released contract commit and directory digest**

`scripts/hash_http_task_contract.py` hashes regular files in lexicographic relative-path order and
writes a manifest with exactly `contract_version`, `source_commit`, `sha256`, and `requires_python`.

```bash
backend/.venv/bin/python scripts/hash_http_task_contract.py \
  --source contracts/http_task_v1 \
  --source-commit "$(git rev-parse HEAD)" \
  --output deploy/cloud/http-task-contract.release.json
backend/.venv/bin/python -m pytest -q backend/tests/test_http_task_contract_asset.py
git add deploy/cloud/http-task-contract.release.json
git commit -m "chore: freeze HTTP task contract release"
```

#### Task 3A: Replace permissive documents with frozen strict models and schemas

The first Task 3 implementation (`3bd9e34`, `b5c06cf`) is a rejected baseline. The approved
2026-08-28 amendment in `2026-08-27-http-task-contract-v1.md` reopens Task 3. Do not preserve a
behavior merely because the baseline test already passes.

**Files:**
- Modify: `contracts/http_task_v1/orbbec_task_contract/models.py`
- Create: `contracts/http_task_v1/schema/http-task-contract-v1.schema.json`
- Create: `contracts/http_task_v1/fixtures/valid_examples.json`
- Create: `contracts/http_task_v1/fixtures/error_examples.json`
- Modify: `contracts/http_task_v1/tests/test_contract_driver.py`
- Modify: `backend/tests/test_http_task_contract_asset.py`

**Interfaces:**
- Produces strict Pydantic models for capabilities, health, create/message/cancel/action receipts,
  task state, finite event pages and the common error envelope.
- Produces one JSON Schema bundle whose `$defs` names match those model names.

- [ ] **Step 1: Write RED tests for every strict response and committed schema asset**

Add parameterized tests that accept each object from `valid_examples.json`, reject an added
`unknown` key, reject `true` in every integer field, and verify the event page includes
`contract_version` and `downstream_task_id`. Add an asset test that requires the schema and both
example files and rejects missing `$defs` for any request/response named in Contract §3–§11.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py \
  backend/tests/test_http_task_contract_asset.py
```

Expected: FAIL because the baseline models are permissive/incomplete and the three frozen assets
do not exist.

- [ ] **Step 3: Implement the minimal strict models and schema/examples**

Use one frozen base model:

```python
class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
```

Define the exact fields and literals from Contract §3–§11. Generate the committed JSON Schema from
the model bundle, then freeze the valid and error examples as source assets. Domain event payloads
remain JSON objects, but the event envelope itself rejects unknown keys.

- [ ] **Step 4: Run GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py \
  backend/tests/test_http_task_contract_asset.py
git add contracts/http_task_v1 backend/tests/test_http_task_contract_asset.py
git commit -m "feat: freeze HTTP task contract schemas"
```

#### Task 3B: Add the local per-task Token Broker boundary

**Files:**
- Create: `contracts/http_task_v1/orbbec_task_contract/token_broker.py`
- Modify: `contracts/http_task_v1/orbbec_task_contract/models.py`
- Modify: `contracts/http_task_v1/orbbec_task_contract/runner.py`
- Modify: `contracts/http_task_v1/tests/test_contract_driver.py`

**Interfaces:**
- Produces `TaskTokenBroker.issue(request: TokenBrokerRequest) -> str`.
- Runner consumes an absolute executable path and profiles `valid`, `expired`, `wrong_scope`,
  `wrong_audience`, `wrong_task_binding`, and `retired_kid`.

- [ ] **Step 1: Write RED broker tests**

Tests must prove: the broker is invoked without a shell; receives one JSON line with the dynamic
`platform_task_id`; rejects a relative executable path, nonzero exit, extra output fields and an
empty token; and no exception/report contains the returned token.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py -k token_broker
```

Expected: FAIL because `TaskTokenBroker` does not exist and `ContractRunner` still requires one
static token.

- [ ] **Step 3: Implement the minimal broker and move authorization to request time**

Use `subprocess.run([absolute_path], input=json_line, text=True, capture_output=True,
timeout=5, check=False, shell=False)`. The runner asks the broker for a token after allocating each
dynamic Task ID; it never stores a token in `ContractReport`, pytest parameters or error text.

- [ ] **Step 4: Run GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py -k 'token_broker or authorization'
git add contracts/http_task_v1/orbbec_task_contract \
  contracts/http_task_v1/tests/test_contract_driver.py
git commit -m "feat: issue per-task contract tokens"
```

#### Task 3C: Make the upstream runner asynchronous, bounded and lifecycle-complete

**Files:**
- Create: `contracts/http_task_v1/orbbec_task_contract/cases.py`
- Modify: `contracts/http_task_v1/orbbec_task_contract/runner.py`
- Modify: `contracts/http_task_v1/tests/test_contract_driver.py`

**Interfaces:**
- Produces Profile `upstream_http` with the stable Case Matrix from Contract §12.2.
- Consumes a target process that dependency-injects `ContractExecutionBackend`; production code must
  not recognize fixture names or `contract:*` strings.

- [ ] **Step 1: Write RED lifecycle/security tests against an asynchronous test target**

Replace the immediate `ContractTarget` with a test-only backend plus a real in-memory Facade/Store.
Tests first assert the runner fails the baseline on: missing/expired/wrong token profiles, rejection
followed by a valid `duplicate=false` create, delayed event polling, strict message/cancel receipts,
three post-terminal reads over at least 500ms, one Action `execution_id`, and
`fixture_business_effect_count == 1` after duplicate Execute.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q contracts/http_task_v1/tests/test_contract_driver.py
```

Expected: FAIL on the first newly asserted frozen behavior; specifically the baseline's static token,
immediate-event assumption, permissive receipts or 905-second timeout.

- [ ] **Step 3: Implement the minimal Profile runner**

Keep request timeouts finite and uniform. Poll only finite JSON pages with `wait_seconds=0`, advancing
`after` only after full-page validation. Use 15-second ordinary Case deadlines, 30 seconds for async
cancel/deadline, and a 180-second Profile deadline. Action Execute returns a quick receipt; no HTTP
operation receives a special 905-second timeout. Reject invalid security/version/deadline attempts
before persistence, then retry the same Task/idempotency key with valid input and require
`duplicate=false`.

- [ ] **Step 4: Run GREEN and commit**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py \
  backend/tests/test_http_task_contract_asset.py
git add contracts/http_task_v1 backend/tests/test_http_task_contract_asset.py
git commit -m "feat: enforce HTTP task contract lifecycle"
```

#### Task 3D: Hash the committed source and freeze the corrected release

**Files:**
- Modify: `scripts/hash_http_task_contract.py`
- Modify: `backend/tests/test_http_task_contract_asset.py`
- Modify: `deploy/cloud/http-task-contract.release.json`

**Interfaces:**
- `archive_sha256(repository: Path, source_commit: str) -> str` hashes
  `contracts/http_task_v1/` from `git archive`.
- `write_manifest(...)` refuses an absent commit or a working contract tree different from that
  commit.

- [ ] **Step 1: Write RED archive and dirty-tree tests**

Create a temporary Git repository in the test, commit a contract tree, alter the working tree, and
assert manifest generation fails. Assert nested source directories named `build` remain in the hash,
while only root `build/`, root `dist/`, caches, bytecode and `*.egg-info/` are excluded. Remove the
existing test's conditional skip when the release manifest is absent.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_http_task_contract_asset.py
```

Expected: FAIL because the baseline hashes the working directory, accepts dirty source and excludes
every nested directory named `build`.

- [ ] **Step 3: Implement archive hashing, commit source, then regenerate manifest**

First commit the hash implementation and tests. Then run the generator with that full commit SHA; it
must independently compare archive and worktree digests before writing the release manifest.

- [ ] **Step 4: Run full Task 3 verification and freeze commit**

```bash
backend/.venv/bin/python -m pytest -q \
  contracts/http_task_v1/tests/test_contract_driver.py \
  backend/tests/test_http_task_contract_asset.py
backend/.venv/bin/python scripts/hash_http_task_contract.py \
  --repository . \
  --source-commit "$(git rev-parse HEAD)" \
  --output deploy/cloud/http-task-contract.release.json
git add deploy/cloud/http-task-contract.release.json
git commit -m "chore: freeze corrected HTTP task contract release"
```

### Task 4: Add migration 050 state, cursor, and Wait schema

**Files:**
- Create: `backend/control_migrations/050_agent_brain_task_wait_state.sql`
- Create: `backend/tests/test_agent_brain_task_wait_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`
- Modify: `backend/app/agent_brain/loop_models.py`

**Interfaces:**
- Produces: `AgentTaskStatus.DISPATCHED`, `WAITING_INPUT`, `WAITING_CONFIRMATION`; `BrainLoopStatus.WAITING_CONFIRMATION`; one cursor row per Task.

- [ ] **Step 1: Write failing schema tests**

```python
@pytest.mark.postgres
def test_v50_has_one_delivery_waterline(control_database) -> None:
    with psycopg.connect(control_database["environments"]["production"]["admin"]) as db:
        columns = _columns(db, "platform_brain", "brain_wait_subscriptions")
        assert "cursors" not in columns
        cursor_columns = _columns(db, "platform_brain", "brain_task_event_cursors")
        assert {"task_id", "loop_id", "delivered_seq"}.issubset(cursor_columns)
        assert _primary_key(db, "platform_brain", "brain_task_event_cursors") == ("task_id",)
```

Add tests for `dispatched_at`, `terminal_reason_code`, `intervention_expires_at`, status checks, exact grants, and environment-scoped functions.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_agent_brain_task_wait_migration.py
```

Expected: FAIL because migration 050 is missing.

- [ ] **Step 3: Write migration 050**

The migration must:

```sql
alter table platform_brain.brain_wait_subscriptions drop column cursors;

create table platform_brain.brain_task_event_cursors (
  task_id uuid primary key references platform_brain.agent_tasks(task_id),
  loop_id uuid not null references platform_brain.brain_loops(loop_id),
  delivered_seq integer not null default 0 check (delivered_seq >= 0),
  updated_at timestamptz not null default clock_timestamp()
);
create index brain_task_event_cursors_loop
  on platform_brain.brain_task_event_cursors(loop_id, task_id);
```

Also replace status constraints, add Task clocks/reason columns, extend Turn active statuses, update wake/event allowlists, and grant only the exact required columns/functions to production and preview roles.

- [ ] **Step 4: Add Python enums and records**

```python
class BrainLoopStatus(StrEnum):
    WAITING_CONFIRMATION = "waiting_confirmation"


class AgentTaskStatus(StrEnum):
    DISPATCHED = "dispatched"
    WAITING_INPUT = "waiting_input"
    WAITING_CONFIRMATION = "waiting_confirmation"
```

Extend `AgentTaskRecord` with `dispatched_at`, `active_elapsed_ms`, and `terminal_reason_code`.

- [ ] **Step 5: Run migration GREEN tests**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_task_wait_migration.py \
  backend/tests/test_agent_brain_live_migration.py \
  backend/tests/test_agent_brain_migration.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/050_agent_brain_task_wait_state.sql \
  backend/tests/test_agent_brain_task_wait_migration.py \
  backend/tests/test_control_plane_migration.py \
  backend/app/agent_brain/loop_models.py
git commit -m "feat: add durable task wait state"
```

### Task 5: Implement post-commit Wait settlement and 40001 recovery

**Files:**
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/collaboration_repository.py`
- Modify: `backend/app/agent_brain/collaboration_models.py`
- Test: `backend/tests/test_agent_brain_live_repository.py`
- Test: `backend/tests/test_agent_brain_v2_recovery.py`

**Interfaces:**
- Produces: `create_wait_subscription_v50(...)`; `settle_if_undelivered(loop_id: UUID) -> WaitSettlementResult`.

- [ ] **Step 1: Write the event-before-Wait and one-waterline tests**

```python
def test_event_before_wait_is_delivered_once(seeded_live_task) -> None:
    repository, loop_repository, loop_id, task_id, _ = seeded_live_task
    repository.append_task_event_and_wake(_finding(task_id, seq=1))
    _commit_await_step(loop_repository, loop_id, task_id)
    result = repository.settle_if_undelivered(loop_id)
    assert result.settled is True
    assert [event.seq for event in result.events] == [1]
    assert repository.settle_if_undelivered(loop_id).settled is False
```

Add a test proving `commit_model_step` succeeds and records the Provider response even when the injected settler raises `SerializationFailure` afterward.

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_v2_recovery.py
```

- [ ] **Step 3: Split Wait creation from settlement**

`commit_model_step` inserts the Wait with only `task_ids`, `wake_on`, and `status=active`; after that transaction returns, runtime calls:

```python
@dataclass(frozen=True, slots=True)
class WaitSettlementResult:
    settled: bool
    source: Literal["post_commit", "event_append", "reaper"]
    events: tuple[AgentTaskPublicEvent, ...]
    serialization_retries: int
```

Implement repository retry:

```python
def settle_if_undelivered(self, loop_id: UUID, *, source: str) -> WaitSettlementResult:
    limits = (0.010, 0.025, 0.050)
    for attempt in range(4):
        try:
            return self._settle_once(loop_id, source=source, attempt=attempt)
        except psycopg.errors.SerializationFailure:
            if attempt == 3:
                return WaitSettlementResult(False, source, (), 3)
            self._sleep(self._random.uniform(0.0, limits[attempt]))
    raise AssertionError("unreachable")
```

`_settle_once` locks cursor rows ordered by `task_id`, selects all events after `delivered_seq`, checks `wake_on`, writes one Tool Result, advances each cursor to the highest included seq, and completes the Wait/Step exactly once.

- [ ] **Step 4: Wire all three liveness sources**

- Post-commit: call with `source="post_commit"` after the model-step transaction returns.
- Event append: call with `source="event_append"` after the event transaction commits.
- Reaper: scan active Wait loop IDs and call with `source="reaper"`.

No source may re-enter the Provider or create a second Step.

- [ ] **Step 5: Run GREEN and race tests repeatedly**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_v2_recovery.py
for run in 1 2 3 4 5; do
  backend/.venv/bin/python -m pytest -q \
    backend/tests/test_agent_brain_live_repository.py::test_event_before_wait_is_delivered_once
done
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/collaboration_repository.py \
  backend/app/agent_brain/collaboration_models.py \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_v2_recovery.py
git commit -m "feat: settle agent waits after model commit"
```

### Task 6: Implement dispatched/waiting Task transitions and local protocol isolation

**Files:**
- Modify: `backend/control_migrations/050_agent_brain_task_wait_state.sql`
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/worker_runtime.py`
- Test: `backend/tests/test_agent_brain_live_repository.py`
- Test: `backend/tests/test_agent_brain_live_adapter.py`
- Test: `backend/tests/test_control_plane_worker_runtime.py`

**Interfaces:**
- Produces: `mark_adapter_delivery_dispatched_v50`; `fail_agent_task_protocol_v50`.

- [ ] **Step 1: Write failing state and isolation tests**

```python
def test_dispatch_ack_does_not_claim_execution_started(seeded_live_task) -> None:
    repository, loop_repository, _, task_id, _ = seeded_live_task
    lease = loop_repository.lease_delivery("adapter", lease_seconds=45)
    loop_repository.mark_delivery_dispatched(lease)
    task = loop_repository.task(task_id)
    assert task.status == AgentTaskStatus.DISPATCHED
    assert task.started_at is None


def test_event_gap_fails_only_one_task(two_agent_tasks, worker_runtime) -> None:
    broken, healthy = two_agent_tasks
    worker_runtime.reconcile_event(_finding(broken, seq=2))
    assert worker_runtime.task(broken).terminal_reason_code == "protocol_violation"
    assert worker_runtime.dispatch_task(healthy).accepted is True
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_live_adapter.py \
  backend/tests/test_control_plane_worker_runtime.py
```

- [ ] **Step 3: Replace the v45 dispatch function**

```sql
update platform_brain.agent_tasks set
  status='dispatched', dispatched_at=coalesce(dispatched_at, clock_timestamp()),
  updated_at=clock_timestamp(), row_version=row_version+1
where task_id=selected_task_id and status='queued';
```

`append_agent_task_event_v50` moves `queued/dispatched` to `running` only for a real `work_update`; terminal events may set `started_at` and `terminal_at` together.

- [ ] **Step 4: Add task-local protocol failure**

`fail_agent_task_protocol_v50` sets:

```sql
status='failed',
terminal_reason_code='protocol_violation',
terminal_at=clock_timestamp()
```

It terminates the task session and delivery, records the Agent health fact, and settles an active Wait with a Platform-origin Tool Result. It does not insert a fabricated `agent_task_event`.

- [ ] **Step 5: Isolate worker phases**

Refactor `worker_runtime.tick` into three guarded calls:

```python
changed += _run_phase("agent-brain-step", brain_tick)
changed += _run_phase("agent-brain-adapter", adapter_tick)
changed += _run_phase("agent-brain-reaper", reaper_tick)
```

`AgentEventProtocolError` is caught inside `adapter_tick`; only shared database/connectivity failures mark that phase degraded.

- [ ] **Step 6: Run GREEN**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_live_adapter.py \
  backend/tests/test_control_plane_worker_runtime.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/control_migrations/050_agent_brain_task_wait_state.sql \
  backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/worker_runtime.py \
  backend/tests/test_agent_brain_live_repository.py \
  backend/tests/test_agent_brain_live_adapter.py \
  backend/tests/test_control_plane_worker_runtime.py
git commit -m "feat: isolate professional agent task failures"
```

### Task 7: Add migration 051 and the Action domain service

**Files:**
- Create: `backend/control_migrations/051_agent_brain_actions.sql`
- Create: `backend/app/agent_brain/action_models.py`
- Create: `backend/app/agent_brain/action_service.py`
- Create: `backend/tests/test_agent_brain_action_migration.py`
- Create: `backend/tests/test_agent_brain_action_service.py`
- Modify: `backend/app/agent_brain/loop_repository.py`

**Interfaces:**
- Produces: `AgentTaskAction`; `ActionCommandService.confirm/reject/supersede`; six SECURITY DEFINER functions.

- [ ] **Step 1: Write failing migration and digest tests**

```python
def test_digest_excludes_summary_and_impact() -> None:
    first = action_digest(_proposal(summary="A", impact="B"))
    second = action_digest(_proposal(summary="changed", impact="changed"))
    assert first == second


@pytest.mark.postgres
def test_web_role_cannot_update_action_table(action_database) -> None:
    with psycopg.connect(action_database["app"]) as db, pytest.raises(psycopg.Error):
        db.execute("update platform_brain.agent_task_actions set status='confirmed'")
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_action_migration.py \
  backend/tests/test_agent_brain_action_service.py
```

- [ ] **Step 3: Create strict Action models**

```python
class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    action_id: UUID
    action_seq: PositiveInt
    action_kind: str
    summary: str
    impact: str
    parameters: dict[str, object]
    action_digest: str
    expires_at: datetime
    execution_timeout_seconds: int = Field(gt=0, le=900)
```

Reuse `contracts/http_task_v1` JCS implementation; store summary, impact, and parameters as ciphertext/key-version/hash triplets.

- [ ] **Step 4: Implement migration 051 functions**

Create `propose_agent_task_action_v51`, `confirm_agent_task_action_v51`, `reject_agent_task_action_v51`, `expire_agent_task_actions_v51`, `supersede_agent_task_action_v51`, and `resume_action_resolution_v51`. Each function validates `session_user`, owner Join path, pending state, digest, expiry, and loop state.

- [ ] **Step 5: Implement service methods**

```python
class ActionCommandService:
    def confirm(self, owner_id: UUID, action_id: UUID, digest_hex: str) -> ActionProjection: ...
    def reject(self, owner_id: UUID, action_id: UUID) -> ActionProjection: ...
    def supersede(self, owner_id: UUID, action_id: UUID) -> ActionProjection: ...
```

Routes never update tables directly.

- [ ] **Step 6: Run GREEN and security tests**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_action_migration.py \
  backend/tests/test_agent_brain_action_service.py \
  backend/tests/test_agent_brain_live_security.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/control_migrations/051_agent_brain_actions.sql \
  backend/app/agent_brain/action_models.py \
  backend/app/agent_brain/action_service.py \
  backend/app/agent_brain/loop_repository.py \
  backend/tests/test_agent_brain_action_migration.py \
  backend/tests/test_agent_brain_action_service.py
git commit -m "feat: add owner-confirmed agent actions"
```

### Task 8: Make pending Actions safe under submit and forced budgets

**Files:**
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/loop_models.py`
- Test: `backend/tests/test_agent_brain_loop_runtime.py`
- Test: `backend/tests/test_agent_brain_v2_budget.py`
- Test: `backend/tests/test_agent_brain_action_service.py`

**Interfaces:**
- Produces: `pending_action_ids(loop_id)`; rejected submit Tool Result; forced-pending pause/resume.

- [ ] **Step 1: Write both P1 regression tests**

```python
def test_pending_action_rejects_submit_without_protocol_retry(action_loop) -> None:
    runtime, repository, loop_id = action_loop.with_model(_submit_response())
    runtime.advance_one()
    assert repository.loop(loop_id).protocol_retry_count == 0
    assert repository.action(loop_id).status == "pending"
    assert repository.last_tool_result(loop_id)["reason"] == "pending_action_requires_resolution"


@pytest.mark.parametrize("forced_by", ["task_count", "step_count", "deadline"])
def test_forced_pending_waits_then_submits(action_loop, forced_by) -> None:
    runtime, repository, loop_id = action_loop.forced_by(forced_by)
    assert runtime.advance_one() is True
    assert repository.loop(loop_id).status == BrainLoopStatus.WAITING_CONFIRMATION
    assert runtime.model.calls == 0
    repository.confirm_pending_action(loop_id)
    assert runtime.advance_one() is True
    assert runtime.model.requests[-1].tool_choice["name"] == "submit_answer"
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_v2_budget.py
```

- [ ] **Step 3: Add the pre-Provider forced guard**

Before building the Provider request:

```python
pending_actions = self._repository.pending_action_ids(lease.loop_id)
if forced and pending_actions:
    self._repository.pause_for_pending_actions(
        lease.loop_id,
        intervention_expires_at=self._repository.earliest_action_expiry(lease.loop_id),
    )
    return True
```

Task clocks and event reconciliation continue; Brain active time is paused.

- [ ] **Step 4: Return a normal rejected submit result**

When `batch.kind == "submit_answer"` and `pending_actions` is non-empty, add:

```python
ImmediateToolResult(
    tool_index=0,
    result={
        "status": "rejected",
        "reason": "pending_action_requires_resolution",
        "required_next_action": "await_agent_events",
    },
)
```

Do not call `record_protocol_retry`; do not finish the Loop; do not expire the Action.

- [ ] **Step 5: Run GREEN**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_v2_budget.py \
  backend/tests/test_agent_brain_action_service.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/loop_models.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_v2_budget.py \
  backend/tests/test_agent_brain_action_service.py
git commit -m "fix: preserve pending actions at brain limits"
```

### Task 9: Add Action HTTP APIs and VOC as the first real Action Adapter

**Files:**
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Create: `backend/app/agent_brain/adapters/voc.py`
- Modify: `backend/app/voc_extension/client.py`
- Modify: `backend/app/voc_extension/routes.py`
- Test: `backend/tests/test_agent_brain_conversation_api.py`
- Create: `backend/tests/test_agent_brain_voc_action.py`

**Interfaces:**
- Produces: `POST /api/v1/conversations/{conversation_id}/actions/{action_id}/confirm|reject`; VOC proposal/execute path.

- [x] **Step 1: Write failing owner/CSRF and exactly-once tests**

```python
def test_non_owner_cannot_confirm(client, other_owner_action) -> None:
    response = client.post(other_owner_action.confirm_url, headers=_csrf_headers())
    assert response.status_code == 403


def test_voc_duplicate_confirmation_submits_once(voc_action_client) -> None:
    first = voc_action_client.confirm()
    second = voc_action_client.confirm()
    assert first.execution_id == second.execution_id
    assert voc_action_client.submit_count == 1
```

- [x] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_conversation_api.py \
  backend/tests/test_agent_brain_voc_action.py
```

- [x] **Step 3: Add authenticated routes and projections**

Routes pass only verified `request.state.identity.internal_user_id`, `action_id`, and digest into `ActionCommandService`; responses expose status, summary, impact, expiry, confirmed time, and digest prefix, never parameters ciphertext.

- [x] **Step 4: Implement VOC proposal and execution**

The VOC Adapter persists the Draft, creates `ActionProposal(action_kind="voc.submit", ...)`, and emits `action_required`. Confirmation calls existing VOC submit with an idempotency key derived from `action_id`; repeated calls return the same record.

- [x] **Step 5: Test six outcomes and three crash points**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_agent_brain_voc_action.py
```

Expected cases: confirmed, rejected, expired, digest mismatch, duplicate confirm, superseded; process restart after propose, before confirm, and after confirm.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_brain/conversation_routes.py \
  backend/app/agent_brain/conversation_projection.py \
  backend/app/agent_brain/adapters/voc.py \
  backend/app/voc_extension/client.py \
  backend/app/voc_extension/routes.py \
  backend/tests/test_agent_brain_conversation_api.py \
  backend/tests/test_agent_brain_voc_action.py
git commit -m "feat: confirm VOC actions through agent brain"
```

### Task 10: Build the shared HTTP Adapter and signed Task identity

**Files:**
- Create: `backend/app/agent_brain/adapters/http_task.py`
- Create: `backend/app/agent_brain/task_identity.py`
- Modify: `backend/app/agent_brain/adapters/base.py`
- Modify: `backend/app/agent_brain/worker_runtime.py`
- Test: `backend/tests/test_agent_brain_http_adapter.py`
- Test: `backend/tests/test_agent_brain_task_identity.py`

**Interfaces:**
- Produces: `HttpTaskAdapter`; `SignedTaskTokenIssuer.issue(...)`; finite event-page validation.

- [ ] **Step 1: Write failing token, deadline, and nonblocking tests**

```python
def test_http_adapter_always_uses_nonblocking_events(fake_http) -> None:
    adapter = HttpTaskAdapter(fake_http.client, base_url=fake_http.url, token_issuer=fake_http.issuer)
    adapter.read_events("remote-1", after=7)
    assert fake_http.last_query == {"after": "7", "limit": "100", "wait_seconds": "0"}


def test_task_token_binds_deadline_and_capability(issuer) -> None:
    claims = issuer.verify(issuer.issue(_task_identity(capability_version=2)))
    assert claims["capability_version"] == 2
    assert claims["task_deadline_at"] == "2026-08-27T10:15:00Z"
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_http_adapter.py \
  backend/tests/test_agent_brain_task_identity.py
```

- [ ] **Step 3: Implement strict request/response models**

`CreateTaskRequest` includes contract version, Platform Task/Conversation/Turn refs, objective, context, constraints, attachments, expected output, capability version, idempotency key, UTC deadline, and scopes. Reject extra fields.

`TaskEventPage` enforces `next_after`, finite `events`, exact sequence continuity, canonical event kinds, and terminal irreversibility.

- [ ] **Step 4: Implement signed identity**

Generalize the VOC signer with `kid`, issuer, audience, task, internal user, scopes, capability version, task deadline, optional action execution deadline, 0600 key checks, and no symlink following.

- [ ] **Step 5: Register adapters without enabling Catalog delegation**

Register `fae_http` and `admin_http` only when their endpoint/key configuration exists. A missing Adapter remains visible as `unavailable`; it is never silently removed.

- [ ] **Step 6: Run GREEN**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_http_adapter.py \
  backend/tests/test_agent_brain_task_identity.py \
  backend/tests/test_agent_brain_live_adapter.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/agent_brain/adapters/http_task.py \
  backend/app/agent_brain/task_identity.py \
  backend/app/agent_brain/adapters/base.py \
  backend/app/agent_brain/worker_runtime.py \
  backend/tests/test_agent_brain_http_adapter.py \
  backend/tests/test_agent_brain_task_identity.py \
  backend/tests/test_agent_brain_live_adapter.py
git commit -m "feat: add authenticated HTTP task adapters"
```

### Task 11: Add Wait telemetry and 12/16/24 evaluation

**Files:**
- Modify: `backend/app/agent_brain/telemetry.py`
- Modify: `backend/app/agent_brain/collaboration_repository.py`
- Modify: `backend/app/agent_brain/routes.py`
- Test: `backend/tests/test_agent_brain_telemetry.py`
- Test: `backend/tests/test_agent_brain_v2_budget.py`

**Interfaces:**
- Produces: settlement source/result counters and immediate-settlement rate.

- [ ] **Step 1: Write failing telemetry tests**

```python
def test_wait_telemetry_separates_source_and_result() -> None:
    telemetry = BrainTelemetry().summarize(_snapshot(
        wait_settlements=(
            ("post_commit", "immediate"),
            ("event_append", "pending"),
            ("reaper", "serialization_retry_exhausted"),
        )
    ))
    assert telemetry.wait_immediate_settlement_rate == pytest.approx(1 / 3)
    assert telemetry.wait_settlement_sources["post_commit"] == 1
```

- [ ] **Step 2: Run RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_agent_brain_telemetry.py
```

- [ ] **Step 3: Add bounded content-free counters**

Extend `BrainTurnSnapshot` and `BrainTurnTelemetry` with integer maps limited to the exact source/result enums, `immediate_settlement_count`, and `immediate_settlement_step_count`. Do not include Agent IDs, prompts, or Action parameters in metric labels.

- [ ] **Step 4: Add the evaluation matrix**

Run fixed scripted scenarios at max steps 12, 16, and 24, reporting token growth, cache hit paths, immediate settlement rate, steps consumed by immediate waits, latency, and answer outcome.

- [ ] **Step 5: Run GREEN**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_telemetry.py \
  backend/tests/test_agent_brain_v2_budget.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/agent_brain/telemetry.py \
  backend/app/agent_brain/collaboration_repository.py \
  backend/app/agent_brain/routes.py \
  backend/tests/test_agent_brain_telemetry.py \
  backend/tests/test_agent_brain_v2_budget.py
git commit -m "feat: measure agent wait settlement cost"
```

### Task 12: Render real Action cards in the existing workroom

**Files:**
- Create: `webui/src/components/conversation/ActionCard.tsx`
- Create: `webui/src/components/conversation/ActionCard.test.tsx`
- Modify: `webui/src/workroomTypes.ts`
- Modify: `webui/src/workroomProjection.ts`
- Modify: `webui/src/components/conversation/MultiAgentWorkroom.tsx`
- Modify: `webui/src/conversationApi.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: confirmed/rejected/expired/superseded/executing/completed/failed Action projection and mutations.

- [ ] **Step 1: Write failing rendering and mutation tests**

```tsx
it("renders a pending action from the server projection", async () => {
  render(<ActionCard action={pendingAction} onConfirm={confirm} onReject={reject} />);
  expect(screen.getByText("提交本次 VOC 草稿")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "确认执行" }));
  expect(confirm).toHaveBeenCalledWith(pendingAction.actionId, pendingAction.actionDigest);
});
```

Add tests that Markdown containing fake Action syntax creates no card and that supersede shows an explicit invalidation warning.

- [ ] **Step 2: Run RED**

```bash
cd webui && npm test -- --run src/components/conversation/ActionCard.test.tsx
```

- [ ] **Step 3: Add strict types and API methods**

```typescript
export interface WorkroomAction {
  actionId: string;
  taskId: string;
  status: "pending" | "confirmed" | "rejected" | "expired" | "superseded";
  executionStatus: "not_started" | "queued" | "running" | "completed" | "failed";
  summary: string;
  impact: string;
  actionDigest: string;
  expiresAt: string;
  confirmedAt: string | null;
  confirmedBy: string | null;
}
```

Mutations include the existing CSRF header and same-origin credentials.

- [ ] **Step 4: Render inside the current workroom**

Add an Actions section without creating a new page. Do not display raw parameters, ciphertext, internal UUID owner identity, or model reasoning.

- [ ] **Step 5: Run GREEN and UI regression**

```bash
cd webui && npm test -- --run \
  src/components/conversation/ActionCard.test.tsx \
  src/components/conversation/MultiAgentWorkroom.test.tsx \
  src/pages/BrainWorkspacePage.test.tsx
```

- [ ] **Step 6: Commit**

```bash
git add webui/src/components/conversation/ActionCard.tsx \
  webui/src/components/conversation/ActionCard.test.tsx \
  webui/src/workroomTypes.ts webui/src/workroomProjection.ts \
  webui/src/components/conversation/MultiAgentWorkroom.tsx \
  webui/src/conversationApi.ts webui/src/styles.css
git commit -m "feat: show owner-confirmed actions in workroom"
```

### Task 13: Run Platform release gates without enabling FAE/Admin early

**Files:**
- Modify: `deploy/cloud/accept.sh`
- Modify: `deploy/cloud/brain-model.release.json`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Test: `backend/tests/test_agent_brain_live_acceptance.py`
- Test: `backend/tests/test_agent_brain_v2_acceptance.py`

**Interfaces:**
- Consumes: Tasks 2–12.
- Produces: Platform/VOC release with FAE/Admin still unavailable until their own plans pass.

- [ ] **Step 1: Write failing acceptance assertions**

Assert P0 version 2, migration 049/050/051 presence, no Wait cursors, one cursor waterline, zero V2 mission writes, Pending Action forced recovery, task-local protocol isolation, VOC exactly-once confirmation, and unchanged FAE/Admin public paths.

- [ ] **Step 2: Run RED against the pre-release candidate**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_live_acceptance.py \
  backend/tests/test_agent_brain_v2_acceptance.py
```

- [ ] **Step 3: Update deployment gates and Catalog safely**

Enable `voc` Brain delegation only. Keep `ai-fae-agent` and `ai-admin-agent` visible with `availability=unavailable` until their pinned contract reports exist. Do not modify `/office` or the FAE Nginx server block.

- [ ] **Step 4: Run the complete Platform suite**

```bash
backend/.venv/bin/python -m pytest -q backend/tests
cd webui && npm test -- --run && npm run build
```

Expected: all tests pass, no skipped mandatory contract/security tests, production WebUI build succeeds.

- [ ] **Step 5: Commit the release gates**

```bash
git add deploy/cloud/accept.sh deploy/cloud/brain-model.release.json \
  backend/app/agent_catalog/catalog.yaml \
  backend/tests/test_agent_brain_live_acceptance.py \
  backend/tests/test_agent_brain_v2_acceptance.py
git commit -m "chore: gate durable agent brain release"
```

- [ ] **Step 6: Create the release evidence bundle**

Record migration hashes, contract runner Commit/SHA-256, test logs, prompt hash, image ID, rollback image, VOC Action evidence, and unchanged `/office`/FAE probes. Do not enable FAE/Admin delegation in this release.
