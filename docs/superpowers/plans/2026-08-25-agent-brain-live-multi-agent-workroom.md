# Agent 大脑真实多 Agent 协作室实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有“整批委派后等待终态”的 Agent Brain V2 升级为云端持久化、事件驱动、可追问和可停止的真实多 Agent 协作系统，并在持续对话页展示可追溯的 Provider thinking summary、子 Agent 消息、工作日志与交付成果。

**Architecture:** `agent_platform_control.platform_control` 继续保存 Conversation，`platform_brain` 继续保存执行事实；迁移 `045` 在现有 Durable Loop 上增加子会话、消息、等待订阅、思考摘要和用户介入。Brain Loop 使用非阻塞委派、显式事件等待、后续消息和停止工具；HR 与五个 Marketing Agent 经 Execution Relay 调用本地 MetaBot 持久 Core Chat 会话。前端只消费公开事件投影，绝不根据时间或状态伪造 Agent 发言。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、PostgreSQL 17、psycopg 3、httpx、AES-256-GCM、Anthropic Messages API、Claude Opus 5、TypeScript 5、React 19、Vite、SSE、MetaBot Core Chat、Claude Agent SDK、pytest、Vitest、Docker Compose。

## Global Constraints

- Conversation SoR 固定在 `agent_platform_control.platform_control`；MetaBot SQLite 仅是本地 Agent 私有执行状态。
- Brain 执行事实固定在 `platform_brain`；V2 不得写 `missions`、`mission_tasks` 或 `mission_runs`。
- 一个 Conversation 同时只有一个非终态 Turn；一个 Loop 同时只有一个 active Step。
- 生产 Brain 固定 `claude-opus-5`、`max_output_tokens=65536`、单 Provider，无运行时模型、Provider 或 Agent fallback。
- Brain 请求固定 `thinking={"type":"adaptive","display":"summarized"}`；只展示 Provider 摘要，不宣称或展示原始 chain of thought。
- HR 与五个 Marketing Agent 必须通过 Provider 原生 thinking summary 探测；禁止用工作日志、终稿或第二次模型调用生成替代摘要。
- 工具固定为 `list_agents`、`delegate_task`、`await_agent_events`、`send_agent_message`、`stop_agent_task`、`request_user_input`、`submit_answer`。
- 同一 Step 只允许多个 `delegate_task` 或多个 `send_agent_message`；其他工具独占 Step；派发、消息和等待不得混在同一步。
- `delegate_task` 非阻塞；任务事件按 `(task_id, seq)` 幂等；一次事件最多唤醒一个等待订阅。
- 默认预算：并行任务 4、总任务 8、每任务后续消息 4、决策 Step 24、单任务活跃 600 秒、Turn 活跃 1800 秒、等待用户 24 小时。
- 运行中用户消息进入 `brain_user_interventions` 并唤醒 Brain，不得直接写子会话。
- 每个 UI 项必须能反查真实 `event_id`、`task_id`、`seq`、来源和时间；禁止定时阶段文案、耗时猜测和事后倒推。
- thinking summary、子会话消息和工作日志加密保存；thinking 不进入数据飞轮、全文搜索或普通运营导出。
- 专业 Agent 不获得 Platform Cookie、钉钉原始 ID、角色、其他 Agent 地址或完整 Conversation。
- FAE、`fae.orbbec.com.cn`、原 IP、AI ADMIN 和 `/office/*` 在本计划中保持不变。
- 三个仓库均在独立 worktree 实施；每项任务先红测、再最小实现、再绿测、最后提交。

```text
max_parallel_tasks = 4
max_agent_tasks = 8
max_followup_messages_per_task = 4
max_brain_decision_steps = 24
max_single_task_active_seconds = 600
max_turn_active_seconds = 1800
max_waiting_user_duration = 86400
thinking.display = summarized
```

## Repository Boundaries

```text
/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations
  Platform 数据、Brain Loop、Execution Relay、公开事件、WebUI、云端发布

/Users/neo/Developer/work/metabot-dev
  Core Chat v3、Claude SDK 事件抽取、持久子会话、后续消息与停止

/Users/neo/Developer/work/Orbbec-Agent-Team
  HR/Marketing Bot 运行契约、本地部署验收与真实业务场景
```

实施开始时分别从三个仓库的 `origin/master` 创建专用 worktree；不得在含用户改动的工作树中实施。

---

### Task 1: Add collaboration persistence and database invariants

**Files:**
- Create: `backend/control_migrations/045_agent_brain_live_collaboration.sql`
- Create: `backend/tests/test_agent_brain_live_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Produces five tables: `agent_task_sessions`, `agent_task_messages`, `brain_thinking_summaries`, `brain_wait_subscriptions`, `brain_user_interventions`.
- Extends `adapter_deliveries` with `delivery_kind` and `source_message_seq`.
- Extends Tool and Conversation Event CHECK constraints.

- [ ] **Step 1: Write the failing migration test**

```python
REQUIRED = {
    "agent_task_sessions", "agent_task_messages",
    "brain_thinking_summaries", "brain_wait_subscriptions",
    "brain_user_interventions",
}

@pytest.mark.postgres
def test_live_collaboration_schema(control_database):
    env = control_database["environments"]["production"]
    with psycopg.connect(env["admin"]) as connection:
        tables = {row[0] for row in connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema='platform_brain'"
        )}
        assert REQUIRED.issubset(tables)
        indexes = "\n".join(row[0] for row in connection.execute(
            "select indexdef from pg_indexes where schemaname='platform_brain'"
        ))
        assert "one_task_session" in indexes
        assert "one_active_wait_subscription" in indexes
        assert "(task_id, seq)" in indexes
        assert "(step_id, block_index)" in indexes
```

Also test duplicate task messages, duplicate thinking delta sequence, a second active wait for one Loop, cross-Loop wait targets, duplicate delivery identities, allowed public events, and absence of `DELETE` grants.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_migration.py tests/test_control_plane_migration.py -q`

Expected: FAIL because migration `045` and the five tables do not exist.

- [ ] **Step 3: Implement migration 045**

Use these exact core shapes:

```sql
create table platform_brain.agent_task_sessions (
  task_id uuid primary key references platform_brain.agent_tasks(task_id),
  child_session_id text not null unique check (char_length(child_session_id) between 16 and 256),
  adapter_kind text not null check (adapter_kind ~ '^[a-z][a-z0-9_]{0,63}$'),
  adapter_session_ref_ciphertext bytea,
  adapter_session_ref_key_version integer,
  status text not null check (status in ('active','completed','failed','cancelled')),
  capability_snapshot jsonb not null check (jsonb_typeof(capability_snapshot)='object'),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  check ((adapter_session_ref_ciphertext is null) = (adapter_session_ref_key_version is null)),
  check ((status='active') = (terminal_at is null))
);
create unique index one_task_session on platform_brain.agent_task_sessions(task_id);

create table platform_brain.agent_task_messages (
  task_id uuid not null references platform_brain.agent_tasks(task_id),
  seq integer not null check (seq > 0),
  sender text not null check (sender in ('brain','agent')),
  message_kind text not null check (message_kind in ('initial','followup','question','reply','result')),
  content_ciphertext bytea not null,
  content_key_version integer not null check (content_key_version > 0),
  content_sha256 bytea not null check (octet_length(content_sha256)=32),
  provider_run_ref text check (provider_run_ref is null or char_length(provider_run_ref) between 1 and 256),
  created_at timestamptz not null,
  primary key (task_id, seq)
);

create table platform_brain.brain_thinking_summaries (
  step_id uuid not null references platform_brain.brain_steps(step_id),
  block_index integer not null check (block_index >= 0),
  last_delta_seq integer not null default 0 check (last_delta_seq >= 0),
  summary_ciphertext bytea not null,
  summary_key_version integer not null check (summary_key_version > 0),
  source text not null check (source='provider'),
  provider_run_ref text not null check (char_length(provider_run_ref) between 1 and 256),
  status text not null check (status in ('streaming','completed','interrupted')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (step_id, block_index)
);
```

`brain_wait_subscriptions` stores `wait_id`, unique Tool Call, Loop, `task_ids uuid[]`, `wake_on text[]`, per-task cursor JSON, status, and triggering `(task_id,event_seq)`. `brain_user_interventions` stores unique Conversation `message_id`, Loop, encrypted content, `pending/consumed/rejected`, and consuming Step. Delivery identity is unique on `(task_id, delivery_kind, source_message_seq)`; only `queued` deliveries are leaseable.

Add tool names `await_agent_events`, `send_agent_message`, `stop_agent_task` and public events `brain.thinking_summary`, `brain.waiting_agents`, `brain.user_intervention`, `brain.agent_message_sent`, `brain.agent_stop_requested`, `agent.thinking_summary`, `agent.message`, `agent.work_update`, `agent.artifact`, `agent.question`, `agent.cancelled`, `agent.task_recovered`.

- [ ] **Step 4: Verify GREEN and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_migration.py tests/test_control_plane_migration.py -q && cd .. && git diff --check`

Expected: PASS.

Commit: `git add backend/control_migrations/045_agent_brain_live_collaboration.sql backend/tests/test_agent_brain_live_migration.py backend/tests/test_control_plane_migration.py && git commit -m "feat(brain): add live collaboration schema"`

### Task 2: Implement typed collaboration records and repository transactions

**Files:**
- Create: `backend/app/agent_brain/collaboration_models.py`
- Create: `backend/app/agent_brain/collaboration_repository.py`
- Create: `backend/tests/test_agent_brain_live_repository.py`

**Interfaces:**
- Produces `AgentTaskMessageInput`, `AgentTaskPublicEventInput`, `BrainThinkingDelta`, `WaitSubscriptionSpec`, and `UserInterventionRecord`.
- Produces repository methods `create_task_session`, `append_task_message`, `append_task_event_and_wake`, `create_wait_subscription`, `append_thinking_delta`, and `claim_intervention`.

- [ ] **Step 1: Write failing transaction tests**

```python
@pytest.mark.postgres
def test_event_wakes_one_subscription_once(repository, seeded_live_task):
    loop_id, task_id = seeded_live_task
    wait = repository.create_wait_subscription(WaitSubscriptionSpec(
        tool_call_id=uuid4(), loop_id=loop_id, task_ids=(task_id,),
        wake_on=("finding", "result"), cursors={task_id: 0},
    ))
    event = AgentTaskPublicEventInput(
        task_id=task_id, seq=1, event_type="finding",
        payload={"summary": "发现跨公司能力组合"}, created_at=NOW,
    )
    first = repository.append_task_event_and_wake(event)
    replay = repository.append_task_event_and_wake(event)
    assert first.woken_wait_id == wait.wait_id
    assert replay.replayed is True
    assert replay.woken_wait_id is None
```

Add encrypted round-trip, conflicting replay hash, monotonic messages, four-follow-up limit, foreign task rejection, ordered thinking deltas, interrupted summary, intervention claiming, and crash-after-event-before-Step tests.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_repository.py -q`

Expected: collection FAIL because collaboration modules do not exist.

- [ ] **Step 3: Implement immutable models and encryption subjects**

```python
@dataclass(frozen=True, slots=True)
class WaitSubscriptionSpec:
    tool_call_id: UUID
    loop_id: UUID
    task_ids: tuple[UUID, ...]
    wake_on: tuple[Literal["question", "finding", "result", "failed", "timeout"], ...]
    cursors: Mapping[UUID, int]

@dataclass(frozen=True, slots=True)
class BrainThinkingDelta:
    step_id: UUID
    block_index: int
    delta_seq: int
    text: str
    provider_run_ref: str
```

Use subjects `brain-task:{task_id}:message:{seq}`, `brain-step:{step_id}:thinking:{block_index}`, and `brain-intervention:{intervention_id}`. Reject empty/oversize text, unknown event kinds and duplicates before opening a transaction.

- [ ] **Step 4: Implement atomic event wake**

In one serializable transaction: lock Task; insert-or-compare event hash; select oldest matching active subscription `FOR UPDATE SKIP LOCKED`; mark it triggered; write Tool Result with all events newer than stored cursors; complete waiting Step; queue exactly one new Step; move Loop to `running`. Replays return the existing event without another wake.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_repository.py -q`

Commit: `git add backend/app/agent_brain/collaboration_models.py backend/app/agent_brain/collaboration_repository.py backend/tests/test_agent_brain_live_repository.py && git commit -m "feat(brain): persist child collaboration events"`

### Task 3: Upgrade the Brain tool protocol

**Files:**
- Modify: `backend/app/agent_brain/tool_protocol.py`
- Modify: `backend/app/agent_brain/prompts/brain_v1.md`
- Modify: `deploy/cloud/brain-model.release.json`
- Modify: `backend/tests/test_agent_brain_tool_protocol.py`
- Modify: `backend/tests/test_agent_brain_prompt.py`

**Interfaces:**
- Produces `AwaitAgentEventsCall`, `SendAgentMessageCall`, `StopAgentTaskCall` and new homogeneous batch kinds.
- Changes delegate result to immediate `{status, task_id, child_session_id}`.

- [ ] **Step 1: Write failing protocol tests**

```python
def test_collaboration_tools_enforce_step_homogeneity():
    assert parse_tool_batch([_send("t1"), _send("t2")], _limits()).kind == "agent_messages"
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch([_delegate(1), _await([TASK_ID])], _limits())
    assert error.value.code == "mixed_tool_batch"
```

Also test multiple sends, single await/stop, foreign task rejection, follow-up size 16 KiB, owned nonterminal stop, and exact wake kinds.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_tool_protocol.py tests/test_agent_brain_prompt.py -q`

Expected: FAIL because three tools are absent.

- [ ] **Step 3: Implement exact models and batch rules**

```python
class AwaitAgentEventsCall(_StrictToolCall):
    task_ids: tuple[UUID, ...]
    wake_on: tuple[Literal["question", "finding", "result", "failed", "timeout"], ...]

class SendAgentMessageCall(_StrictToolCall):
    task_id: UUID
    message: str

class StopAgentTaskCall(_StrictToolCall):
    task_id: UUID
    reason: str
```

Keep seven tool schemas byte-stable. Update the prompt: simple requests answer directly; delegate only for material specialist value; dispatch then explicitly await; follow up only from a concrete event; never manufacture progress; only ask users for business ambiguity or irreversible authorization.

- [ ] **Step 4: Freeze prompt hash, verify GREEN and commit**

Compute the exact digest with `python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("app/agent_brain/prompts/brain_v1.md").read_bytes()).hexdigest())'` from `backend/`; replace only `system_prompt_sha256` in the manifest with that output. Run the Step 2 command.

Commit: `git add backend/app/agent_brain/tool_protocol.py backend/app/agent_brain/prompts/brain_v1.md deploy/cloud/brain-model.release.json backend/tests/test_agent_brain_tool_protocol.py backend/tests/test_agent_brain_prompt.py && git commit -m "feat(brain): add live collaboration tools"`

### Task 4: Define persistent-session Adapter capabilities

**Files:**
- Modify: `backend/app/agent_brain/adapters/base.py`
- Modify: `backend/app/agent_brain/adapters/reference.py`
- Modify: `backend/app/agent_brain/runtime_registry.py`
- Modify: `backend/app/agent_brain/models.py`
- Modify: `backend/app/agent_catalog/models.py`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Create: `backend/tests/test_agent_brain_live_adapter.py`
- Modify: `backend/tests/test_agent_brain_runtime_registry.py`

**Interfaces:**
- Produces `AdapterCapabilities`, `ChildSessionReceipt`, `MessageDeliveryReceipt`, `StopDeliveryReceipt`, `AdapterEvent`.
- Produces Adapter methods `start_session`, `send_message`, `read_events`, `request_stop`.

- [ ] **Step 1: Write failing Adapter tests**

```python
def test_reference_adapter_supports_live_session():
    adapter = ReferenceAdapter()
    opened = adapter.start_session(_task(), _delivery())
    adapter.send_message(opened.child_session_id, _message(), _delivery(2))
    assert [event.kind for event in adapter.read_events(opened.child_session_id, after=0)] == [
        "thinking_summary", "work_update", "message", "result",
    ]
    assert adapter.capabilities.supports_thinking_summary is True
```

Add a Registry test proving an authorized Agent with missing Adapter remains visible as `unavailable`.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_adapter.py tests/test_agent_brain_runtime_registry.py -q`

- [ ] **Step 3: Replace one-shot Adapter methods**

```python
@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    supports_persistent_session: bool
    supports_followup_message: bool
    supports_progress_events: bool
    supports_thinking_summary: bool
    supports_cancel: bool
    supports_attachments: bool
    typical_latency_seconds: int

class AgentAdapter(ABC):
    capabilities: AdapterCapabilities
    @abstractmethod
    def start_session(self, task: AdapterTask, delivery: AdapterDelivery) -> ChildSessionReceipt: ...
    @abstractmethod
    def send_message(self, child_session_id: str, message: AdapterMessage, delivery: AdapterDelivery) -> MessageDeliveryReceipt: ...
    @abstractmethod
    def read_events(self, child_session_id: str, *, after: int) -> tuple[AdapterEvent, ...]: ...
    @abstractmethod
    def request_stop(self, child_session_id: str, reason: str, delivery: AdapterDelivery) -> StopDeliveryReceipt: ...
```

Use these receipt/event shapes so later tasks do not invent a second contract:

```python
@dataclass(frozen=True, slots=True)
class ChildSessionReceipt:
    accepted: bool
    child_session_id: str
    external_run_id: UUID | None

@dataclass(frozen=True, slots=True)
class MessageDeliveryReceipt:
    accepted: bool
    external_run_id: UUID | None

@dataclass(frozen=True, slots=True)
class StopDeliveryReceipt:
    accepted: bool
    supported: bool

@dataclass(frozen=True, slots=True)
class AdapterEvent:
    seq: int
    kind: Literal["thinking_summary", "work_update", "message", "artifact", "question", "result", "error"]
    source: Literal["provider", "agent", "adapter"]
    source_ref: str
    created_at: datetime
    payload: dict[str, object]
```

Extend Catalog/Runtime snapshots with all seven capability fields. Reference Adapter derives stable IDs from `task_id` and deduplicates deliveries by idempotency key.

- [ ] **Step 4: Verify GREEN and commit**

Run the Step 2 command.

Commit: `git add backend/app/agent_brain/adapters backend/app/agent_brain/runtime_registry.py backend/app/agent_brain/models.py backend/app/agent_catalog/models.py backend/app/agent_catalog/catalog.yaml backend/tests/test_agent_brain_live_adapter.py backend/tests/test_agent_brain_runtime_registry.py && git commit -m "feat(brain): define persistent agent sessions"`

### Task 5: Implement event-driven Loop dispatch, wait, follow-up and stop

**Files:**
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/worker_runtime.py`
- Create: `backend/tests/test_agent_brain_live_loop.py`
- Modify: `backend/tests/test_agent_brain_v2_recovery.py`

**Interfaces:**
- Consumes Tasks 2–4.
- Produces one durable transition per `advance_one`, `dispatch_one`, `reconcile_one`, `reap_one`.

- [ ] **Step 1: Write failing vertical-slice tests**

```python
@pytest.mark.postgres
def test_progress_wakes_brain_before_task_terminal(runtime, repository):
    runtime.model.script(_delegate_response("hr-bot"), _await_response(["finding"]))
    assert runtime.advance_one() is True
    assert repository.queued_step_count(LOOP_ID) == 1
    assert runtime.advance_one() is True
    assert repository.loop_status(LOOP_ID) == "waiting_agents"
    assert runtime.dispatch_one() is True
    assert runtime.reconcile_one() is True
    assert repository.queued_step_count(LOOP_ID) == 1
```

Add parallel tasks, follow-up, supported/unsupported stop, a user stop that targets every in-flight Task, event coalescing, user intervention, budgets, revocation and crashes before/after delivery, wake, follow-up and final transaction.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_loop.py tests/test_agent_brain_v2_recovery.py -q`

Expected: FAIL because delegate still waits for terminal batch.

- [ ] **Step 3: Implement nonblocking handlers**

```python
def dispatch_result(task_id: UUID, child_session_id: str) -> dict[str, object]:
    return {"status": "dispatched", "task_id": str(task_id), "child_session_id": child_session_id}

def stop_result(task_id: UUID, accepted: bool) -> dict[str, object]:
    return {"status": "stop_requested" if accepted else "cancel_unsupported", "task_id": str(task_id)}
```

Delegate transaction creates Task, session placeholder, initial message and delivery, writes immediate Tool Result and queues the next Step. Await creates a subscription and releases Worker. Send creates one message/delivery per call. Stop creates cancellation intent/delivery. Never invoke Adapter inside a DB transaction.

- [ ] **Step 4: Implement wake/recovery/budgets**

Append every Adapter event before projection. Matching event triggers at most one subscription and Step. `waiting_agents` consumes active duration; `waiting_user` pauses. Reaper emits factual timeout/recovery events and interrupts incomplete thinking without inventing Agent text.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_loop.py tests/test_agent_brain_loop_runtime.py tests/test_agent_brain_v2_recovery.py -q`

Commit: `git add backend/app/agent_brain/loop_repository.py backend/app/agent_brain/loop_runtime.py backend/app/agent_brain/worker_runtime.py backend/tests/test_agent_brain_live_loop.py backend/tests/test_agent_brain_v2_recovery.py && git commit -m "feat(brain): run event-driven agent collaboration"`

### Task 6: Stream real Opus 5 summarized thinking

**Files:**
- Modify: `backend/app/agent_brain/model_adapter.py`
- Modify: `backend/app/agent_brain/anthropic_adapter.py`
- Modify: `backend/app/agent_brain/provider_probe.py`
- Modify: `deploy/cloud/brain-model.release.json`
- Modify: `backend/tests/test_agent_brain_model_adapter.py`
- Modify: `backend/tests/test_agent_brain_provider_probe.py`
- Create: `backend/tests/test_agent_brain_thinking_stream.py`

**Interfaces:**
- Produces `ThinkingDelta(block_index, delta_seq, text, provider_run_ref)`.
- Changes `BrainModelAdapter.complete(request, *, on_thinking_delta=None)`.

- [ ] **Step 1: Write failing stream tests**

```python
def test_adapter_streams_provider_summary_before_tool_commit():
    seen = []
    response = adapter.complete(_request(), on_thinking_delta=seen.append)
    assert "".join(delta.text for delta in seen) == "需要先拆分任务。"
    assert all(delta.provider_run_ref == response.provider_request_id for delta in seen)

def test_interrupted_summary_creates_no_tool_call(repository):
    with pytest.raises(ProviderInterrupted):
        runtime.advance_one()
    assert repository.thinking_status(STEP_ID) == "interrupted"
    assert repository.tool_call_count(STEP_ID) == 0
```

Also assert `display=summarized`, no sampling/fallback fields, stable seven-tool bytes, and direct `provider_refused` handling.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_model_adapter.py tests/test_agent_brain_provider_probe.py tests/test_agent_brain_thinking_stream.py -q`

- [ ] **Step 3: Add callback and manifest support**

```python
@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    block_index: int
    delta_seq: int
    text: str
    provider_run_ref: str
```

Capture `message_start.message.id` before deltas; accept only `thinking_delta`; number per block; persist via callback; aggregate signed blocks byte-for-byte for replay. Manifest parser requires `Literal["summarized"]`; release JSON uses `summarized`.

The runtime callback converts `model_adapter.ThinkingDelta` into `collaboration_models.BrainThinkingDelta` without renaming fields, then calls `CollaborationRepository.append_thinking_delta`; the Provider type and persistence type remain deliberately separate boundary objects.

- [ ] **Step 4: Verify tests and real Provider probe**

Run the Step 2 command, then `cd backend && .venv/bin/python -m app.agent_brain.provider_probe --manifest ../deploy/cloud/brain-model.release.json`.

Expected: tests PASS; probe reports `summarized_thinking=true`, forced tool choice and `claude-opus-5`, without content or credential.

- [ ] **Step 5: Commit**

Commit: `git add backend/app/agent_brain/model_adapter.py backend/app/agent_brain/anthropic_adapter.py backend/app/agent_brain/provider_probe.py deploy/cloud/brain-model.release.json backend/tests/test_agent_brain_model_adapter.py backend/tests/test_agent_brain_provider_probe.py backend/tests/test_agent_brain_thinking_stream.py && git commit -m "feat(brain): stream provider thinking summaries"`

### Task 7: Project safe events and accept user interventions

**Files:**
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_models.py`
- Create: `backend/tests/test_agent_brain_live_projection.py`
- Create: `backend/tests/test_agent_brain_user_intervention.py`

**Interfaces:**
- Produces safe public keys `task_id`, `child_session_id`, `source`, `source_ref`, `agent_id`, `kind`, `summary`, `evidence_refs`, `artifact_refs`, `status`, `created_at`.
- Changes active Brain Turn POST to durable intervention acceptance.

- [ ] **Step 1: Write failing tests**

```python
def test_thinking_projection_removes_provider_internals():
    public = ConversationProjection.project(PrivateBrainEvent(
        "agent.thinking_summary",
        {"task_id": str(TASK_ID), "agent_id": "hr-bot", "source": "provider",
         "source_ref": "run_opaque", "summary": "需要验证能力组合。",
         "signature": "secret", "raw_response": "secret"},
    ))
    assert public.payload["summary"] == "需要验证能力组合。"
    assert "signature" not in public.payload and "raw_response" not in public.payload

def test_running_turn_accepts_intervention(client, auth):
    response = client.post(f"/api/v1/conversations/{CONVERSATION_ID}/messages",
        json={"text": "只看深圳，排除管理岗"}, **auth)
    assert response.status_code == 202
    assert response.json()["intervention"]["status"] == "pending"
```

Also test owner visibility, audited cross-user reads, SSE order, export/search exclusion, foreign child-session rejection and request-ID replay.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_projection.py tests/test_agent_brain_user_intervention.py -q`

- [ ] **Step 3: Implement projection and intervention**

Thinking requires `source=provider` plus source reference. Agent message requires persisted message sequence. Work update kind is exactly `plan/progress/finding/question/blocker/decision/artifact/result`. Parsing failure projects a Platform fact with stable `public_event_unavailable`, never private payload. Active Brain message append inserts intervention, emits `brain.user_intervention`, and queues one Step if waiting.

- [ ] **Step 4: Add child-session read endpoint**

Add owner-checked `GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/tasks/{task_id}` returning messages/events. Do not add a direct child-session mutation route.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_projection.py tests/test_agent_brain_user_intervention.py tests/test_agent_brain_conversation_projection.py -q`

Commit: `git add backend/app/agent_brain/conversation_projection.py backend/app/agent_brain/conversation_repository.py backend/app/agent_brain/conversation_routes.py backend/app/agent_brain/conversation_models.py backend/tests/test_agent_brain_live_projection.py backend/tests/test_agent_brain_user_intervention.py && git commit -m "feat(brain): expose real collaboration events"`

### Task 8: Add MetaBot Core Chat v3 persistent task sessions

**Repository:** `/Users/neo/Developer/work/metabot-dev`

**Files:**
- Create: `src/api/routes/core-chat-contract.ts`
- Create: `src/api/routes/core-chat-session-store.ts`
- Modify: `src/api/routes/core-chat-routes.ts`
- Create: `tests/core-chat-session-store.test.ts`
- Modify: `tests/core-chat-routes.test.ts`

**Interfaces:**
- Produces `core_chat_collaboration_v3`.
- Accepts `taskSessionId`, `messageKind`, `messageSeq`, `parentRunId`, `eventCallbackUrl`.
- Emits `thinking_summary`, `work_update`, `agent_message`, `artifact`, `question`, `result`, `error`.

- [ ] **Step 1: Write failing v3 session tests**

```typescript
it('continues one task session with an idempotent follow-up', () => {
  const initial = parseCoreChatCommand(initialCommand({ taskSessionId: 'task-session-1', messageSeq: 1 }));
  const followup = parseCoreChatCommand(followupCommand({
    taskSessionId: 'task-session-1', messageSeq: 2, parentRunId: 'run-1',
  }));
  expect(initial.command?.messageKind).toBe('initial');
  expect(followup.command?.messageKind).toBe('followup');
  expect(store.accept(followup.command!)).toEqual(store.accept(followup.command!));
});
```

Also test wrong Bot, sequence gap, conflicting duplicate, follow-up before initial, follow-up after terminal, stop, journal reload and absence of requester identity in the journal.

- [ ] **Step 2: Run and verify RED**

Run: `npx vitest run tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts`

Expected: FAIL because v3 types/store are absent.

- [ ] **Step 3: Implement strict contract and journal**

```typescript
export type CoreChatMessageKind = 'initial' | 'followup';
export type CoreChatCollaborationEventType =
  | 'thinking_summary' | 'work_update' | 'agent_message'
  | 'artifact' | 'question' | 'result' | 'error';

export interface CoreChatCommandV3 {
  contractVersion: 'core_chat_collaboration_v3';
  runId: string;
  taskSessionId: string;
  targetBot: string;
  messageKind: CoreChatMessageKind;
  messageSeq: number;
  parentRunId?: string;
  prompt: string;
  eventCallbackUrl: string;
}
```

Write an owner-only JSONL journal under the existing Bot state directory using atomic append and fsync. Store only opaque task session ID, Bot ID, execution chat ID, sequence, run ID, status and SHA-256. Reuse the same `executionChatId` for follow-ups. Never store Platform identity or callback authorization.

- [ ] **Step 4: Route initial, follow-up and stop**

Keep `/api/core-chat/runs` compatible with v2; require v3 fields when contract is v3. Follow-up calls `executeApiTask` with stored chat ID. Stop resolves the active run and calls `stopChatTask`. Capabilities include `"collaboration":"core_chat_collaboration_v3"` alongside v2.

- [ ] **Step 5: Verify GREEN and commit**

Run: `npx vitest run tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts && npm run build:bridge`

Commit: `git add src/api/routes/core-chat-contract.ts src/api/routes/core-chat-session-store.ts src/api/routes/core-chat-routes.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts && git commit -m "feat(core-chat): add persistent collaboration sessions"`

### Task 9: Extract genuine Claude Provider thinking and work events

**Repository:** `/Users/neo/Developer/work/metabot-dev`

**Files:**
- Create: `src/engines/claude/provider-public-events.ts`
- Modify: `src/engines/claude/stream-processor.ts`
- Modify: `src/engines/claude/executor.ts`
- Modify: `src/api/routes/core-chat-routes.ts`
- Create: `tests/provider-public-events.test.ts`
- Modify: `tests/stream-processor.test.ts`
- Modify: `tests/core-chat-routes.test.ts`

**Interfaces:**
- Produces `ProviderThinkingSummaryEvent` only from top-level SDK `thinking_delta`.
- Produces structured work updates only from actual SDK/Agent events.
- Produces capability `thinkingSummary: "provider" | "unsupported"`.

- [ ] **Step 1: Write failing provenance tests**

```typescript
it('emits thinking only from provider thinking_delta', () => {
  const projector = new ProviderPublicEventProjector();
  expect(projector.accept(streamThinkingDelta('真实摘要'))).toMatchObject({
    type: 'thinking_summary', source: 'provider', text: '真实摘要',
  });
  expect(projector.accept({
    type: 'assistant', message: { content: [{ type: 'text', text: '分析中' }] },
  } as any)).toBeNull();
});
```

Also test top-level only, delta ordering, interrupted block, no terminal-answer backfill, no terminal-decoration parsing, and Provider run reference.

- [ ] **Step 2: Run and verify RED**

Run: `npx vitest run tests/provider-public-events.test.ts tests/stream-processor.test.ts tests/core-chat-routes.test.ts`

Expected: FAIL because `StreamProcessor` ignores thinking deltas.

- [ ] **Step 3: Implement provenance-preserving events**

```typescript
export interface ProviderThinkingSummaryEvent {
  type: 'thinking_summary';
  source: 'provider';
  providerRunRef: string;
  blockIndex: number;
  deltaSeq: number;
  text: string;
}
```

Accept only top-level `stream_event` thinking deltas with known Provider message/session reference. Work updates may originate only from actual `system.task_progress`, task notification, explicit question, artifact callback or result callback. Normalize kind to `plan/progress/finding/question/blocker/decision/artifact/result`. Never use `CardState.status`, elapsed time or final answer to create updates.

- [ ] **Step 4: Add the real transport capability probe**

Extend the existing Opus probe to execute one bounded request through the same Claude Code/Agent SDK path and require a Provider thinking delta. Its only output is:

```json
{"contract":"core_chat_collaboration_v3","thinkingSummary":"provider","agents":6}
```

If the installed transport lacks `thinking_delta`, update the pinned Claude Agent SDK/Claude Code transport until the probe passes. Do not parse terminal decoration or call another model.

- [ ] **Step 5: Verify GREEN and commit**

Run: `npx vitest run tests/provider-public-events.test.ts tests/stream-processor.test.ts tests/core-chat-routes.test.ts && npm run build:bridge`

Commit implementation files and dependency lockfiles only when the real capability probe required a pinned upgrade: `git add src/engines/claude/provider-public-events.ts src/engines/claude/stream-processor.ts src/engines/claude/executor.ts src/api/routes/core-chat-routes.ts tests/provider-public-events.test.ts tests/stream-processor.test.ts tests/core-chat-routes.test.ts package.json package-lock.json && git commit -m "feat(core-chat): expose provider thinking summaries"`

### Task 10: Carry child-session commands through Execution Relay

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations`

**Files:**
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/app/execution_relay/metabot_client.py`
- Modify: `backend/app/execution_relay/worker.py`
- Modify: `backend/app/agent_brain/adapters/metabot_local.py`
- Modify: `backend/tests/test_metabot_relay_client.py`
- Modify: `backend/tests/test_execution_worker_runtime.py`
- Create: `backend/tests/test_agent_brain_metabot_collaboration.py`

**Interfaces:**
- Extends `RelayJobPayload` with collaboration contract, task session, message kind/sequence and parent run.
- Normalizes Core Chat v3 callback without losing source sequence.

- [ ] **Step 1: Write failing relay tests**

```python
def test_followup_reuses_child_session(client, respx_mock):
    payload = _relay_payload(task_session_id="task-session-1",
        message_kind="followup", message_seq=2, parent_run_id=RUN_ID)
    client.start_run(payload, CALLBACK_URL)
    sent = respx_mock.calls.last.request.json()
    assert sent["contractVersion"] == "core_chat_collaboration_v3"
    assert sent["taskSessionId"] == "task-session-1"
    assert sent["messageSeq"] == 2
```

Also test initial/follow-up/stop, callback replay, thinking provenance, work kind validation, result normalization, missing v3 capability, Mac offline and no fallback to local `agent-brain-bot`.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_metabot_relay_client.py tests/test_execution_worker_runtime.py tests/test_agent_brain_metabot_collaboration.py -q`

- [ ] **Step 3: Extend relay envelope and client**

```python
class RelayJobPayload(BaseModel):
    collaboration_contract: Literal["core_chat_collaboration_v3"] | None = None
    task_session_id: str | None = Field(default=None, min_length=16, max_length=256)
    message_kind: Literal["initial", "followup", "stop"] = "initial"
    message_seq: int = Field(default=1, ge=1)
    parent_run_id: UUID | None = None
```

Require v3 fields for `job_kind=metabot_local`. Verify v3 capability before send. Preserve callback `(run_id, seq, type, createdAt, source)` and never convert free text into thinking.

- [ ] **Step 4: Implement MetaBotLocalAdapter operations**

`start_session` enqueues initial job; `send_message` enqueues follow-up on the same child session; `read_events` reconciles events after cursor; `request_stop` enqueues stop. No method targets `agent-brain-bot`.

- [ ] **Step 5: Verify GREEN and commit**

Run the Step 2 command.

Commit: `git add backend/app/execution_relay/models.py backend/app/execution_relay/metabot_client.py backend/app/execution_relay/worker.py backend/app/agent_brain/adapters/metabot_local.py backend/tests/test_metabot_relay_client.py backend/tests/test_execution_worker_runtime.py backend/tests/test_agent_brain_metabot_collaboration.py && git commit -m "feat(brain): relay persistent metabot sessions"`

### Task 11: Freeze HR/Marketing runtime contract and local gates

**Repository:** `/Users/neo/Developer/work/Orbbec-Agent-Team`

**Files:**
- Modify: `deploy/metabot.runtime-contract.json`
- Modify: `tests/test_metabot_runtime_contract.py`
- Modify: `tests/verify-marketing-agent-family-contracts.sh`
- Create: `scripts/probe_metabot_collaboration_v3.sh`
- Create: `tests/test_probe_metabot_collaboration_v3.py`
- Create: `docs/runbooks/agent-brain-live-collaboration.md`

**Interfaces:**
- Freezes exactly six delegatable Bots: HR plus five Marketing.
- Requires v3 and Provider thinking summary for each.

- [ ] **Step 1: Write failing contract tests**

```python
def test_delegatable_bots_have_live_contract(runtime_contract):
    delegated = [bot for bot in runtime_contract["bots"] if bot.get("brainDelegatable")]
    assert [bot["name"] for bot in delegated] == [
        "hr-bot", "marketing-prospecting-bot", "marketing-inbound-bot",
        "marketing-voice-bot", "marketing-intelligence-bot", "marketing-gtm-bot",
    ]
    assert all(bot["collaborationContract"] == "core_chat_collaboration_v3" for bot in delegated)
    assert all(bot["thinkingSummary"] == "provider" for bot in delegated)
```

Also test that local Brain, FAE and ADMIN are not delegatable; ports stay loopback; model is Opus 5; fallback is forbidden.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m pytest tests/test_metabot_runtime_contract.py tests/test_probe_metabot_collaboration_v3.py -q`

- [ ] **Step 3: Add exact contract fields and probe**

Each of six Bot objects receives:

```json
{"brainDelegatable":true,"collaborationContract":"core_chat_collaboration_v3","thinkingSummary":"provider","supportsFollowup":true,"supportsProgressEvents":true,"supportsCancel":true}
```

Probe reads `/Users/agentops/AgentRuntime/private/metabot-api-token`, calls each loopback capability endpoint, sends one bounded real probe, requires Provider thinking plus terminal result, redacts content and prints status only. It must not touch macOS Keychain or browser state.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python3 -m pytest tests/test_metabot_runtime_contract.py tests/test_probe_metabot_collaboration_v3.py -q && bash tests/verify-marketing-agent-family-contracts.sh`

Commit: `git add deploy/metabot.runtime-contract.json tests/test_metabot_runtime_contract.py tests/verify-marketing-agent-family-contracts.sh scripts/probe_metabot_collaboration_v3.sh tests/test_probe_metabot_collaboration_v3.py docs/runbooks/agent-brain-live-collaboration.md && git commit -m "feat(agents): require live collaboration contract"`

### Task 12: Build pure workroom projection and real-only UI

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations`

**Files:**
- Create: `webui/src/workroomTypes.ts`
- Create: `webui/src/workroomProjection.ts`
- Create: `webui/src/workroomProjection.test.ts`
- Create: `webui/src/components/conversation/MultiAgentWorkroom.tsx`
- Create: `webui/src/components/conversation/WorkroomTeamView.tsx`
- Create: `webui/src/components/conversation/WorkroomTimeline.tsx`
- Create: `webui/src/components/conversation/WorkroomDeliverables.tsx`
- Create: `webui/src/components/conversation/WorkroomAgentSession.tsx`
- Create: `webui/src/components/conversation/MultiAgentWorkroom.test.tsx`
- Modify: `webui/src/conversationTypes.ts`

**Interfaces:**
- Produces `projectWorkroom(events) -> WorkroomTurn | null` and three views.

- [ ] **Step 1: Write failing projection tests**

```typescript
it('creates no workroom without a real delegated task', () => {
  expect(projectWorkroom([brainThinkingEvent()])).toBeNull();
});

it('keeps sources distinct', () => {
  expect(projectWorkroom(realEventFixture())?.timeline.map((item) => item.sourceKind)).toEqual([
    'brain_thinking', 'brain_message', 'platform_fact',
    'agent_thinking', 'agent_work', 'agent_message',
  ]);
});
```

Also test duplicate/out-of-order SSE, interrupted thinking, unavailable Agent, artifacts, partial failure, completed collapse and child selection.

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npx vitest run src/workroomProjection.test.ts src/components/conversation/MultiAgentWorkroom.test.tsx`

- [ ] **Step 3: Implement strict view models**

```typescript
export type WorkroomSourceKind =
  | 'brain_thinking' | 'brain_message' | 'agent_thinking'
  | 'agent_work' | 'agent_message' | 'platform_fact';

export interface WorkroomTimelineItem {
  eventId: string;
  taskId: string | null;
  seq: number;
  sourceKind: WorkroomSourceKind;
  sourceLabel: string;
  text: string;
  createdAt: string;
  interrupted: boolean;
}
```

Return `null` until real `agent.task_dispatched`. Never invent items from elapsed time or active state. Unknown events are ignored; malformed allowlisted events show a server-issued Platform error fact.

- [ ] **Step 4: Implement accessible components**

Use buttons for tabs/cards, correct `aria-selected`, ordered timeline, Platform attachment links and read-only inline child session. Active workroom defaults open; completed defaults closed; user choice remains for the Turn.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd webui && npx vitest run src/workroomProjection.test.ts src/components/conversation/MultiAgentWorkroom.test.tsx && npm run build`

Commit: `git add webui/src/workroomTypes.ts webui/src/workroomProjection.ts webui/src/workroomProjection.test.ts webui/src/components/conversation/MultiAgentWorkroom.tsx webui/src/components/conversation/WorkroomTeamView.tsx webui/src/components/conversation/WorkroomTimeline.tsx webui/src/components/conversation/WorkroomDeliverables.tsx webui/src/components/conversation/WorkroomAgentSession.tsx webui/src/components/conversation/MultiAgentWorkroom.test.tsx webui/src/conversationTypes.ts && git commit -m "feat(web): add real multi-agent workroom"`

### Task 13: Keep collaboration inside continuous conversation

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations`

**Files:**
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Modify: `webui/src/components/conversation/PublicProgress.tsx`
- Modify: `webui/src/components/conversation/PublicProgress.test.tsx`
- Modify: `webui/src/components/conversation/ExecutionCard.tsx`
- Modify: `webui/src/conversationApi.ts`
- Modify: `webui/src/styles.css`
- Create: `webui/src/components/conversation/NoMockProgress.test.ts`

**Interfaces:**
- Keeps main composer enabled for Brain intervention.
- Embeds one workroom inside its Turn.

- [ ] **Step 1: Write failing continuous-chat/no-mock tests**

```typescript
it('keeps composer usable while brain waits for agents', async () => {
  renderConversation({ turnStatus: 'waiting_agents', events: realEventFixture() });
  expect(screen.getByRole('textbox')).toBeEnabled();
  await userEvent.type(screen.getByRole('textbox'), '把范围改成深圳');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(client.postIntervention).toHaveBeenCalled();
});

it('contains no timed fake progress vocabulary', () => {
  expect(readConversationSources()).not.toMatch(/setTimeout|setInterval|深入思考|正在整理/);
});
```

Also test mobile, active/completed collapse, child loading, offline reconnect and a simple direct answer without workroom.

- [ ] **Step 2: Run and verify RED**

Run: `cd webui && npx vitest run src/pages/ConversationPage.test.tsx src/components/conversation/PublicProgress.test.tsx src/components/conversation/NoMockProgress.test.ts`

- [ ] **Step 3: Integrate workroom and intervention**

Group events by `turn_id`; render workroom in that Turn. Active Brain composer posts intervention and handles `202`. Direct-Agent keeps active-turn lock. Remove generic analysis/integration text unless supplied by real thinking/message event.

- [ ] **Step 4: Add responsive styles**

Desktop uses inset workroom; mobile uses full-width tabs and bottom-sheet child session. Cards have hover/focus/pressed states but no pulsing avatar or synthetic animation. Reuse Platform Markdown and attachment components.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd webui && npx vitest run src/pages/ConversationPage.test.tsx src/components/conversation/PublicProgress.test.tsx src/components/conversation/NoMockProgress.test.ts && npm test && npm run build`

Commit: `git add webui/src/pages/ConversationPage.tsx webui/src/pages/ConversationPage.test.tsx webui/src/components/conversation/PublicProgress.tsx webui/src/components/conversation/PublicProgress.test.tsx webui/src/components/conversation/ExecutionCard.tsx webui/src/conversationApi.ts webui/src/styles.css webui/src/components/conversation/NoMockProgress.test.ts && git commit -m "feat(web): keep collaboration inside conversation"`

### Task 14: Enforce security, retention, provenance and no fallback

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations`

**Files:**
- Create: `backend/app/agent_brain/retention.py`
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/worker_runtime.py`
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Create: `backend/tests/test_agent_brain_live_security.py`
- Create: `backend/tests/test_agent_brain_live_retention.py`
- Modify: `backend/tests/test_cloud_sanitizer.py`
- Modify: `backend/tests/test_task4_security_boundary.py`

**Interfaces:**
- Enforces owner/audited-owner read, encryption, erasure, export exclusion and genuine provenance.

- [ ] **Step 1: Write failing security tests**

```python
@pytest.mark.postgres
def test_thinking_never_enters_flywheel_or_search(databases):
    seed_live_collaboration(databases)
    run_sync_and_index(databases)
    assert search_all("需要验证岗位能力组合") == []
    assert flywheel_event_count("agent.thinking_summary") == 0

def test_child_agent_receives_no_platform_identity(adapter_capture):
    dispatch_live_task(adapter_capture)
    serialized = json.dumps(adapter_capture.payload, ensure_ascii=False).lower()
    assert "__host-platform_session" not in serialized
    assert "dingtalk" not in serialized
    assert "internal_user_id" not in serialized
```

Also test cross-user 403/audit, 365-day Conversation retention erasure, no raw Provider response/internal URL in SSE, provenance requirements, and that one professional Agent cannot address or message another professional Agent.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_live_security.py tests/test_agent_brain_live_retention.py tests/test_cloud_sanitizer.py tests/test_task4_security_boundary.py -q`

- [ ] **Step 3: Extend retention and deny rules**

`agent_brain.retention` selects only archived Conversations older than 365 days in batches of 100. In one transaction it tombstones child messages, task events and thinking summaries, preserving IDs, timestamps, hashes and terminal state for audit. `worker_runtime` runs the bounded batch from the existing reaper mode. Standard export/search/flywheel serializers reject both thinking event types, ciphertext, signatures, Adapter payload and callback URL.

- [ ] **Step 4: Enforce provenance and no fallback**

Reject thinking without `source=provider` and source reference. Assert no mapping from work update, CardState, terminal answer or elapsed time to thinking. Assert refusal, unavailable and unsupported do not change model, Provider or Agent.

- [ ] **Step 5: Verify GREEN and commit**

Run the Step 2 command.

Commit: `git add backend/app/agent_brain/retention.py backend/app/agent_brain/loop_repository.py backend/app/agent_brain/worker_runtime.py backend/app/agent_brain/conversation_projection.py backend/tests/test_agent_brain_live_security.py backend/tests/test_agent_brain_live_retention.py backend/tests/test_cloud_sanitizer.py backend/tests/test_task4_security_boundary.py && git commit -m "security(brain): enforce collaboration boundaries"`

### Task 15: Add deployment gates and real acceptance

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/agent-brain-conversations`

**Files:**
- Modify: `deploy/cloud/brain-model.release.json`
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `deploy/cloud/accept.sh`
- Modify: `deploy/cloud/rollback-dingtalk-production.sh`
- Modify: `backend/tests/test_agent_brain_deployment.py`
- Create: `backend/tests/test_agent_brain_live_acceptance.py`
- Create: `docs/runbooks/agent-brain-live-collaboration-release.md`

**Interfaces:**
- Produces compatibility-first release across Platform, MetaBot and local Bots.
- Produces content-free release evidence and a recoverable rollback.

- [ ] **Step 1: Write failing deployment/acceptance tests**

```python
@pytest.mark.parametrize("scenario", [
    "simple_direct_answer", "parallel_hr_marketing", "progress_wakeup",
    "agent_followup", "agent_stop", "user_intervention", "partial_failure",
    "adapter_offline", "provider_refusal", "worker_crash_recovery",
    "thinking_stream_interruption", "mobile_replay",
])
def test_live_reference_scenarios(reference_harness, scenario):
    result = reference_harness.run(scenario)
    assert result.invariant_failures == ()
    assert result.mock_events == ()
```

Also test ordinary deploy preserves enabled Brain flags, manifest is summarized, six capabilities pass, rollback rejects active follow-ups, Nginx hash is unchanged, and no command restarts FAE/Admin.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_deployment.py tests/test_agent_brain_live_acceptance.py -q`

- [ ] **Step 3: Implement compatibility-first release**

Enforce this order:

```text
1. Back up control DB and record Platform/Admin/FAE/Nginx evidence.
2. Deploy Core Chat v3 while retaining v2 compatibility.
3. Deploy local worker accepting v3 while old Platform sends v2.
4. Run all six real thinking probes; stop on any unsupported Agent.
5. Apply migration 045 and deploy Platform with UI feature hidden.
6. Run Reference acceptance plus one real HR and Marketing session.
7. Enable collaboration for owner; verify follow-up, stop and recovery.
8. Enable workroom UI for owner; verify desktop and phone.
9. Expand after independent quality review.
```

Stop forcing existing Brain flags to `0` in ordinary deploy. Add `PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED=0`; enabling requires V2 plus exact manifest/capability probes.

- [ ] **Step 4: Implement rollback and invariance checks**

Rollback hides new UI, rejects new collaborative Turns, drains or terminalizes active follow-up deliveries, then rolls back code; migration `045` remains. Compare Nginx SHA, `/office/?view=services`, FAE container ID/ImageID/StartedAt/RestartCount, FAE domain and original IP before/after. Do not modify or restart Admin/FAE.

- [ ] **Step 5: Run all automated gates**

Platform:

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../webui && npm test && npm run build
cd .. && bash -n deploy/cloud/remote-stage.sh deploy/cloud/accept.sh deploy/cloud/rollback-dingtalk-production.sh
git diff --check
```

MetaBot: `npm test && npm run build:bridge`

Orbbec Agent Team: `python3 -m pytest tests/test_metabot_runtime_contract.py tests/test_probe_metabot_collaboration_v3.py -q && bash tests/verify-marketing-agent-family-contracts.sh`

Expected: every command PASS, no invariance difference and no raw content in acceptance output.

- [ ] **Step 6: Execute real product acceptance**

1. “介绍一下你自己” directly answers without workroom.
2. “为英文能力、视觉技术和硬件产品经历组合的人才制定搜索与雇主吸引方案” creates HR plus Marketing sessions.
3. HR emits a real finding before terminal; Brain wakes and sends a real follow-up.
4. User sends “只看深圳，排除管理岗” while running; Brain records and applies it.
5. Stop one task; UI shows confirmed cancellation or `cancel_unsupported`.
6. Restart Brain Worker at a tested crash point; no duplicate task/message/answer.
7. Disconnect Mac; local Agents alone become unavailable and Brain explicitly partially delivers.
8. Reopen on phone; event order/deliverables equal desktop.

Independent Codex or a business expert scores decomposition necessity, follow-up usefulness, process truthfulness, evidence quality and final-answer improvement. Store scores/event IDs, not thinking text.

- [ ] **Step 7: Commit release artifacts**

Commit: `git add deploy/cloud/brain-model.release.json deploy/cloud/compose.yaml deploy/cloud/remote-stage.sh deploy/cloud/accept.sh deploy/cloud/rollback-dingtalk-production.sh backend/tests/test_agent_brain_deployment.py backend/tests/test_agent_brain_live_acceptance.py docs/runbooks/agent-brain-live-collaboration-release.md && git commit -m "release(brain): gate live multi-agent collaboration"`

## Final Integration Sequence

1. Merge/tag MetaBot Core Chat v3 first; retain v2.
2. Merge Orbbec Agent Team contract and deploy six local Bots.
3. Run six real Provider thinking probes; stop on one failure.
4. Merge Platform migration/runtime with collaboration flag off.
5. Apply `045`, deploy API/Brain workers, run Reference acceptance.
6. Enable owner collaboration, run real HR/Marketing acceptance, then deploy WebUI.
7. Verify AI ADMIN、`/office/*`、FAE domain、原 IP 和 Nginx 不变。
8. Expand access only after independent quality review.

## Design Coverage Map

| Design requirement | Tasks |
|---|---|
| Persistent child sessions/messages/events | 1, 2, 4, 8, 10 |
| Nonblocking dispatch and event wake | 3, 5 |
| Follow-up, stop and intervention | 3, 5, 7, 8, 10, 13 |
| Brain Provider summarized thinking | 6, 7, 12 |
| HR/Marketing Provider-native thinking | 8, 9, 10, 11 |
| Real-only logs and no mock UI | 7, 9, 12, 13, 14 |
| Crash recovery and idempotency | 1, 2, 5, 8, 10, 15 |
| Security, ownership and retention | 1, 2, 7, 14 |
| Continuous conversation and mobile | 12, 13, 15 |
| Budgets, failures and no fallback | 3, 5, 6, 14, 15 |
| FAE/Admin/Nginx invariance | 15 |
