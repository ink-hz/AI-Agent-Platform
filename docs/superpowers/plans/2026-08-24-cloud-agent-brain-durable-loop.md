# Cloud Agent Brain Durable Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Mac-routed V1 planner with a cloud-resident, crash-recoverable Opus 5.0 Brain Loop that can directly answer, delegate to multiple authorized professional Agents, wait for user or Agent results, and submit one durable final answer inside the existing continuous Conversation UI.

**Architecture:** `agent_platform_control.platform_control` remains the only Conversation system of record. A new `platform_brain` schema stores Loop, Step, Tool Call, Agent Task, Task Event, Delivery, authorization snapshot, and disposable checkpoint state; a non-public `platform-brain` worker reconstructs model messages from those records and uses one configured `BrainModelAdapter`. Professional execution is isolated behind explicit Adapters; MetaBot is only the `metabot_local` Adapter, while FAE remains untouched until a separately approved FAE integration batch.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, PostgreSQL 17, psycopg 3, httpx 0.28, AES-256-GCM content envelopes, Anthropic Messages-compatible API, Claude Opus 5 with the `opus_1m` profile and adaptive thinking, React 19, TypeScript, Vite, SSE, pytest, Vitest, Docker Compose.

## Global Constraints

- Conversation SoR stays in database `agent_platform_control`, schema `platform_control`; never write Platform Conversations to MetaBot SQLite or `platform_replica`.
- V2 execution state lives in schema `platform_brain` in the same database and uses a separate `platform_brain_worker` database role.
- A Conversation permits one non-terminal Turn across `accepted/running/waiting_agents/waiting_user/completing`.
- A Turn has at most one Brain Loop; a retry creates a new Turn linked by `retry_of_turn_id`.
- The production Brain model is exactly the release-manifest model `claude-opus-5`, context profile `opus_1m`, adaptive thinking with `display=omitted`, initially frozen at high effort after a `medium/high/xhigh` Dev sweep, `max_output_tokens=65536`, and one configured Provider.
- No runtime Provider, model, Agent, or V1 Brain fallback is allowed.
- Provider requests use streaming, omit `temperature`, `top_p`, `top_k`, and `fallbacks`, and map HTTP-200 `stop_reason=refusal` directly to `provider_refused` without protocol retry.
- Model-visible tools are exactly `list_agents`, `delegate_task`, `request_user_input`, and `submit_answer`; `cancel_task` is not exposed in this release.
- Only `submit_answer` creates a normal final Assistant Message. Free text outside tool-use is discarded.
- Defaults are `max_brain_steps=12`, `max_agent_tasks=8`, `max_parallel_tasks=4`, active execution budget `900s`, waiting-user limit `86400s`, single-task limit `300s`, task-result limit `65536` bytes, and answer limit `65536` UTF-8 bytes.
- `waiting_user` pauses active execution time. `running`, `waiting_agents`, and `completing` consume it.
- A batch of `delegate_task` calls resumes the model only after every accepted task settles; rejected calls still receive paired tool results.
- `brain_steps`, `brain_tool_calls`, `agent_tasks`, and `agent_task_events` are recovery truth. Checkpoints are deletable caches.
- Provider-required Assistant content blocks are encrypted and retained byte-for-byte for recovery, never projected to UI/SSE/flywheel/logs/child Agents, and erased seven days after Loop terminalization. With `display=omitted`, no readable thinking text is returned; release 1 exposes no raw-response export path.
- The four tool schemas remain byte-stable for an entire Turn; forced submission changes only `tool_choice`.
- The versioned Brain system prompt is a release artifact whose SHA-256 is frozen into the model manifest and Step telemetry; runtime prompt editing is forbidden.
- Every tool contains a bounded non-empty `public_reason`; UI never derives a reason from thinking text.
- Every brain-mode Turn accepted after V2 cutover writes zero rows to `missions`, `mission_tasks`, and `mission_runs`; pre-cutover history and the separately retained Direct-Agent V1 path are excluded from that counter.
- FAE code, database, container, domain, configuration, startup time, and restart count remain unchanged throughout this plan.
- Implementation uses an isolated worktree and TDD: failing focused test, minimal implementation, green focused test, commit.

## Release Boundary

This plan delivers the production Brain runtime, the Reference Adapter, the existing HR/Marketing MetaBot Agents through `metabot_local`, the Conversation API/UI, and the atomic V2 cutover. It does not add the FAE internal API or register `fae_http`; that requires a separate FAE-owned plan because the current batch is explicitly forbidden from modifying or restarting FAE.

## File Structure

```text
backend/app/agent_brain/
├── loop_models.py          immutable Loop/Step/Task records and enums
├── tool_protocol.py        four tool schemas, validation, deterministic IDs
├── model_adapter.py        provider-neutral Brain request/response contract
├── anthropic_adapter.py    one Anthropic Messages-compatible implementation
├── provider_probe.py       real-provider capability and cache-TTL probe
├── prompt.py               immutable system-prompt loader and SHA verifier
├── prompts/
│   └── brain_v1.md         versioned delegation, scope, and output discipline
├── runtime_registry.py     authorized capability + health + Adapter snapshots
├── loop_repository.py      leases, append-only state, reconstruction, projection
├── loop_runtime.py         one durable state-machine transition per pass
├── worker_runtime.py       non-public Brain/Adapter/reaper process entrypoint
├── conversation_service.py V2 Turn creation/resume/stop/retry transaction boundary
└── adapters/
    ├── base.py             Adapter protocol and normalized result contract
    ├── reference.py        deterministic Dev/preview acceptance Adapter
    └── metabot_local.py    V2 Task to existing Execution Relay bridge

backend/tests/
├── test_agent_brain_v2_migration.py
├── test_agent_brain_tool_protocol.py
├── test_agent_brain_loop_repository.py
├── test_agent_brain_model_adapter.py
├── test_agent_brain_provider_probe.py
├── test_agent_brain_prompt.py
├── test_agent_brain_runtime_registry.py
├── test_agent_brain_loop_runtime.py
├── test_agent_brain_v2_conversation_api.py
├── test_agent_brain_v2_recovery.py
├── test_agent_brain_metabot_adapter.py
└── test_agent_brain_v2_acceptance.py

webui/src/components/conversation/
├── ExecutionCard.tsx       public Brain/Agent timeline only
└── UserInputRequest.tsx    in-Turn answer form for waiting_user
```

Keep `backend/app/agent_brain/orchestrator.py`, `repository.py`, `protocol.py`, and legacy Mission routes unchanged except for explicit V1 wiring/cutover guards. They remain history-compatible V1 code, not shared V2 state-machine code.

---

### Task 1: Add the isolated `platform_brain` schema and worker role

**Files:**
- Create: `backend/control_migrations/039_agent_brain_durable_loop.sql`
- Create: `backend/tests/test_agent_brain_v2_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`
- Modify: `backend/tests/test_control_plane_dsn_roles.py`
- Modify: `backend/app/control_plane/dsn.py`
- Modify: `deploy/cloud/bootstrap-control-db.sh`
- Modify: `backend/tests/test_agent_brain_deployment.py`

**Interfaces:**
- Produces: DSN purpose `brain` mapped to `platform_brain_worker` and `platform_brain_worker_preview`.
- Produces: `platform_brain.authorization_snapshots`, `brain_loops`, `brain_steps`, `brain_tool_calls`, `agent_tasks`, `agent_task_events`, `adapter_deliveries`, and `brain_checkpoints`.
- Produces: expanded V2 Turn states and `conversation_turns.retry_of_turn_id`.

- [ ] **Step 1: Write the failing role and schema tests**

```python
@pytest.mark.postgres
def test_v2_schema_enforces_durable_loop_invariants(control_database):
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        tables = {row[0] for row in connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema='platform_brain'"
        )}
        assert {
            "authorization_snapshots", "brain_loops", "brain_steps",
            "brain_tool_calls", "agent_tasks", "agent_task_events",
            "adapter_deliveries", "brain_checkpoints",
        }.issubset(tables)
        indexes = "\n".join(row[0] for row in connection.execute(
            "select indexdef from pg_indexes where schemaname='platform_brain'"
        ))
        assert "one_active_brain_step" in indexes
        assert "one_active_adapter_delivery" in indexes
        assert "brain_tool_call_id" in indexes

def test_brain_dsn_requires_exact_worker_role():
    assert validate_control_dsn(
        "postgresql://platform_brain_worker@127.0.0.1/agent_platform_control",
        purpose="brain",
    ).environment == "production"
    with pytest.raises(ValueError, match="exact control brain DSN required"):
        validate_control_dsn(
            "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
            purpose="brain",
        )
```

Also assert the Turn CHECK and `one_active_conversation_turn` index cover all five non-terminal states; `turn_id` is unique in `brain_loops`; `(task_id,seq)`, `(step_id,tool_index)`, `(step_id,provider_tool_call_id)`, and delivery idempotency are unique; no online role has `DELETE`; the Brain role cannot read provider identities, web sessions, directory PII, or audit metadata.

Assert `platform_control.worker_heartbeats.worker_name` additionally accepts exactly `agent-brain-step`, `agent-brain-adapter`, and `agent-brain-reaper`, and that only the Brain worker role can insert/update those three rows.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_brain_v2_migration.py tests/test_control_plane_dsn_roles.py tests/test_control_plane_migration.py -q
```

Expected: FAIL because migration 039, the Brain roles, and DSN purpose do not exist.

- [ ] **Step 3: Implement migration 039 and bootstrap credentials**

Use these exact status sets:

```sql
-- brain_loops.status
'queued','running','waiting_agents','waiting_user','completing',
'completed','failed','cancelled','interrupted'

-- brain_steps.status
'queued','leased','requesting_model','waiting_tool_results','completed','failed'

-- brain_tool_calls.status
'accepted','waiting_result','result_ready','consumed','failed'

-- agent_tasks.status
'queued','running','completed','failed','cancelled','timed_out','unavailable'

-- adapter_deliveries.status
'queued','leased','dispatched','completed','failed','expired'
```

Store protected JSON as `bytea` plus positive key version. Store safe counters and timestamps as typed columns. Add `active_budget_ms`, `active_elapsed_ms`, `active_started_at`, `active_deadline_at`, `waiting_user_expires_at`, `protocol_retry_count`, `fallback_used`, `fallback_kind`, `reason_code`, `row_version`, and terminal-shape CHECKs to `brain_loops`. Add nullable self-FK `retry_of_turn_id` to `conversation_turns`, replace its status CHECK, and replace its partial unique index.

Replace the `conversation_events.event_type` CHECK so it retains every V1 event and additionally allows the exact V2 public names in Task 12. Define checkpoints as `(loop_id,through_step_seq)` primary key plus `source_hash`, encrypted checkpoint bytes/key version, `created_at`, and `expires_at`; no code path may require a checkpoint row to exist.

Replace the `worker_heartbeats.worker_name` CHECK with the old directory worker name plus the three exact Brain names; grant the Brain role column-safe insert/update only for its worker rows through a `SECURITY DEFINER` heartbeat function rather than granting it directory-worker privileges.

Create `platform_brain_worker{,_preview}` in `bootstrap-control-db.sh`, owner-only password/DSN files `brain-worker-password` and `brain-worker-database-url` with preview equivalents, and purpose mapping:

```python
_PURPOSE_ROLES = {
    "app": "platform_control_app",
    "audit": "platform_audit_append",
    "migrator": "platform_control_migrator",
    "maintenance": "platform_control_maintenance",
    "directory": "platform_directory_worker",
    "stream": "platform_stream_ingest",
    "brain": "platform_brain_worker",
}
```

Grant the API app role only the schema usage and insert/select privileges needed by `ConversationCommandService`; grant the Brain worker no access to raw DingTalk identity columns. Do not grant `DELETE` to either role.

- [ ] **Step 4: Run schema tests and inspect privileges**

Run the command from Step 2. Expected: all PASS. Then run:

```bash
cd ..
bash -n deploy/cloud/bootstrap-control-db.sh
git diff --check
```

Expected: shell syntax and whitespace checks PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/039_agent_brain_durable_loop.sql \
  backend/tests/test_agent_brain_v2_migration.py \
  backend/tests/test_control_plane_migration.py \
  backend/tests/test_control_plane_dsn_roles.py \
  backend/app/control_plane/dsn.py \
  deploy/cloud/bootstrap-control-db.sh \
  backend/tests/test_agent_brain_deployment.py
git commit -m "feat(brain): add durable loop schema"
```

### Task 2: Define the four-tool protocol and immutable runtime records

**Files:**
- Create: `backend/app/agent_brain/loop_models.py`
- Create: `backend/app/agent_brain/tool_protocol.py`
- Create: `backend/tests/test_agent_brain_tool_protocol.py`

**Interfaces:**
- Produces: `BrainToolBatch`, `ListAgentsCall`, `DelegateTaskCall`, `RequestUserInputCall`, `SubmitAnswerCall`, and `NormalizedTaskResult`.
- Produces: `parse_tool_batch(content_blocks, limits) -> BrainToolBatch` and `stable_runtime_id(loop_id, step_seq, tool_index, kind) -> UUID`.
- Produces: `ProtocolViolation(code)` with stable codes only.

- [ ] **Step 1: Write failing strict-protocol tests**

```python
def test_delegate_batch_accepts_first_four_and_pairs_every_call():
    batch = parse_tool_batch(
        [_delegate_block(index) for index in range(6)],
        ToolLimits(max_parallel_tasks=4),
    )
    assert [call.accepted for call in batch.calls] == [True] * 4 + [False] * 2
    assert [call.result_status for call in batch.calls[-2:]] == [
        "rejected_over_parallel_limit", "rejected_over_parallel_limit"
    ]

def test_zero_tool_use_is_a_protocol_violation():
    with pytest.raises(ProtocolViolation) as error:
        parse_tool_batch([{"type": "text", "text": "hidden draft"}], ToolLimits())
    assert error.value.code == "zero_tool_use"
```

Add cases for unknown tools, duplicate Provider IDs, invalid UTF-8 sizes, empty/oversize `public_reason`, forbidden mixed tools, task/attachment ownership, exact `outcome` enum, answer byte limit, and deterministic UUID stability. Assert no `CancelTaskCall` or `cancel_task` schema exists.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_tool_protocol.py -q`

Expected: collection FAIL because the two modules do not exist.

- [ ] **Step 3: Implement the typed protocol**

Use discriminated Pydantic models with `extra="forbid"`, strict validation, tuple/list limits, and this public dispatcher shape:

```python
ToolCall = ListAgentsCall | DelegateTaskCall | RequestUserInputCall | SubmitAnswerCall

@dataclass(frozen=True)
class BrainToolBatch:
    kind: Literal["list_agents", "delegate_tasks", "request_user_input", "submit_answer"]
    calls: tuple[ParsedToolCall, ...]

def stable_runtime_id(loop_id: UUID, step_seq: int, tool_index: int, kind: str) -> UUID:
    if step_seq < 1 or tool_index < 0 or kind not in {"tool_call", "task", "delivery"}:
        raise ValueError("runtime identity invalid")
    return uuid5(loop_id, f"{step_seq}:{tool_index}:{kind}")
```

Model-visible schemas contain exactly the four approved tool names. `public_reason` is required on every tool and limited to 512 UTF-8 bytes. Never retain non-tool text as an answer or public event.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest tests/test_agent_brain_tool_protocol.py -q
cd .. && git diff --check
git add backend/app/agent_brain/loop_models.py \
  backend/app/agent_brain/tool_protocol.py \
  backend/tests/test_agent_brain_tool_protocol.py
git commit -m "feat(brain): define durable tool protocol"
```

Expected: tests PASS and `cancel_task` remains absent from model schemas.

### Task 3: Implement the durable repository, leases, and deterministic reconstruction

**Files:**
- Create: `backend/app/agent_brain/loop_repository.py`
- Create: `backend/tests/test_agent_brain_loop_repository.py`
- Modify: `backend/app/agent_brain/conversation_models.py`

**Interfaces:**
- Consumes: records and stable IDs from Task 2; existing `ContentCodec`.
- Produces: `BrainLoopRepository.create_loop`, `lease_step`, `commit_model_step`, `append_task_event`, `settle_batch`, `resume_user_input`, `request_cancel`, `expire_leases`, `reconstruct_messages`, and `erase_expired_model_responses`.
- Produces: at most one committed state transition per repository transaction.

- [ ] **Step 1: Write failing PostgreSQL repository tests**

```python
@pytest.mark.postgres
def test_replayed_tool_call_creates_one_task(loop_repository, seeded_loop):
    response = scripted_delegate_response(provider_id="toolu_1")
    first = loop_repository.commit_model_step(seeded_loop.loop_id, response)
    second = loop_repository.commit_model_step(seeded_loop.loop_id, response)
    assert first.task_ids == second.task_ids
    assert loop_repository.task_count(seeded_loop.loop_id) == 1

@pytest.mark.postgres
def test_task_event_same_seq_is_idempotent_but_conflict_fails(loop_repository, task):
    assert loop_repository.append_task_event(task.task_id, 1, _event("running")) is True
    assert loop_repository.append_task_event(task.task_id, 1, _event("running")) is False
    with pytest.raises(BrainRepositoryConflict):
        loop_repository.append_task_event(task.task_id, 1, _event("completed"))
```

Add tests for active Step lease exclusion, expired lease reclaim with incremented attempt, stale `row_version`, terminal event guard, complete batch single wake-up, checkpoint deletion followed by identical message reconstruction, thinking ciphertext never appearing in record repr/errors, and seven-day response erasure preserving normalized calls/results/usage.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_loop_repository.py -q`

Expected: FAIL because `BrainLoopRepository` is absent.

- [ ] **Step 3: Implement repository transactions**

Lease the next eligible row with `FOR UPDATE SKIP LOCKED` ordered by creation time and stable ID; compare `row_version` on every mutable root row. `commit_model_step()` must insert the encrypted raw Provider response, normalized calls, accepted tasks, rejected paired results, and public Step events in one transaction. `append_task_event()` must call a migration-owned function so external event ingestion cannot directly mutate task status.

Reconstruction returns Provider messages in this order:

```python
def reconstruct_messages(self, loop_id: UUID) -> tuple[dict[str, object], ...]:
    # conversation prefix, then each stored assistant content block,
    # then exactly one user tool_result block per tool_use in original order
    rows = self._reconstruction_rows(loop_id)
    return provider_messages_from_rows(rows, content_codec=self._content_codec)
```

For terminal Loops older than seven days, replace `model_response_ciphertext` with null and set `response_erased_at`; retain tool calls, tool results, usage, stop reason, and public events.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_loop_repository.py \
  tests/test_agent_brain_conversation_repository.py -q
cd .. && git diff --check
git add backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/conversation_models.py \
  backend/tests/test_agent_brain_loop_repository.py
git commit -m "feat(brain): persist recoverable loop state"
```

### Task 4: Add the single-provider Brain model Adapter and real capability probe

**Files:**
- Create: `backend/app/agent_brain/model_adapter.py`
- Create: `backend/app/agent_brain/anthropic_adapter.py`
- Create: `backend/app/agent_brain/provider_probe.py`
- Create: `backend/tests/test_agent_brain_model_adapter.py`
- Create: `backend/tests/test_agent_brain_provider_probe.py`
- Create: `deploy/cloud/brain-model.release.json`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/requirements.cloud.txt`

**Interfaces:**
- Produces: `BrainModelAdapter.complete(request: BrainModelRequest) -> BrainModelResponse` and `ProviderRefused(category: str | None)`.
- Produces: `BrainRequestBuilder.build(loop, messages, step_seq, *, system_prompt: str, tool_choice=None, budget_notice=None) -> BrainModelRequest`.
- Produces: `AnthropicMessagesAdapter` using an injected `httpx.Client` for tests and aggregating a streamed response into one validated `BrainModelResponse`.
- Produces: `python -m app.agent_brain.provider_probe --manifest PATH --system-prompt PATH --evidence-out PATH`.

- [ ] **Step 1: Write failing Adapter and probe tests**

```python
def test_adapter_sends_adaptive_thinking_cache_and_tools(respx_mock, adapter):
    route = respx_mock.post("https://gateway.example/v1/messages").mock(
        return_value=_streaming_tool_response()
    )
    response = adapter.complete(_request())
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert body["output_config"]["effort"] == "high"
    assert body["max_tokens"] == 65536
    assert body["stream"] is True
    assert {"temperature", "top_p", "top_k", "fallbacks"}.isdisjoint(body)
    assert response.usage.cache_read_input_tokens == 1200

def test_refusal_is_not_retried_or_parsed_as_zero_tool_use(adapter, provider):
    provider.respond_once(stop_reason="refusal", stop_details={"category": "cyber"})
    with pytest.raises(ProviderRefused) as error:
        adapter.complete(_request())
    assert error.value.category == "cyber"
    assert provider.request_count == 1

def test_provider_probe_fails_when_forced_tool_choice_is_not_honored(fake_provider):
    with pytest.raises(ProviderCapabilityError, match="forced_tool_choice"):
        run_probe(_manifest(), fake_provider.with_free_text_response())
```

Also prove: only the pre-first-event 429/5xx/connect failure retries; no retry after any stream event; Provider/model switch is not attempted; API key and raw response never enter exceptions/logs; omitted-thinking blocks and signatures round-trip byte-for-byte but are not in public projections; readable thinking under `display=omitted` fails the probe; truncated output is explicit; tools remain byte-identical when forced `tool_choice` changes; render/cache order is tools, system, then messages; the four-breakpoint and 20-block rules are respected; and 1-hour/5-minute cache TTL, mid-conversation system messages, 1M context, and `medium/high/xhigh` effort calls are proven by evidence.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_model_adapter.py tests/test_agent_brain_provider_probe.py tests/test_config.py -q`

Expected: FAIL because Adapter/config/probe files are absent.

- [ ] **Step 3: Implement the Adapter and immutable manifest**

The release manifest contains no secret:

```json
{
  "config_version": "brain-opus5-v1",
  "provider_kind": "anthropic_compatible",
  "model_id": "claude-opus-5",
  "context_profile": "opus_1m",
  "context_window": 1000000,
  "thinking_type": "adaptive",
  "thinking_display": "omitted",
  "thinking_effort": "high",
  "max_output_tokens": 65536,
  "max_answer_bytes": 65536,
  "prompt_cache_enabled": true,
  "stable_cache_ttl": "1h",
  "rolling_cache_ttl": "5m",
  "system_prompt_sha256": "10b5e0f3d32b419d5e742238f75c94ea7187a62bf1ed22e10b811b5a6b79aba0"
}
```

Read the API key only from `PLATFORM_BRAIN_PROVIDER_API_KEY_FILE`; read the base URL and manifest path from validated configuration. The provider-neutral request and Opus 5 Adapter expose no `temperature`, `top_p`, or `top_k` property. Never send `fallbacks`.

The probe requires an explicit `--system-prompt PATH`; focused tests use a stable synthetic prompt and a test manifest containing that fixture's digest, so Task 4 remains independently testable. Task 5 supplies the production artifact and startup integrity check. The probe performs real `list_agents`, forced `submit_answer`, adaptive-thinking tool-use, streamed 65536-token configuration, cache-create/cache-read, long-prefix, mid-conversation system-message, refusal, and `medium/high/xhigh` effort calls. Its evidence JSON includes manifest and system-prompt SHA-256, Provider request IDs, supported flags, both cache TTLs, cache pricing assumptions, usage, timestamps, effort-quality fixtures, and sanitized errors; deployment rejects missing or mismatched evidence. A failure of mid-conversation system messages, omitted-thinking semantics, 1-hour TTL, forced tool choice, or streaming blocks release rather than selecting a runtime fallback.

Respect the Provider's actual render order: four stable tool schemas, stable top-level system instruction, then messages. Put the capability snapshot/version and budget notice in mid-conversation system messages. Allocate at most four cache breakpoints: final tool schema (1h), final top-level system block (1h), capability snapshot or a required intermediate anchor (5m), and latest appended content block (5m). Put every 1h breakpoint before every 5m breakpoint, rely on the 20-block lookback where possible, and preserve returned Assistant content blocks byte-for-byte in the encrypted response envelope. Keep tools unchanged for forced submission; changing `tool_choice` is the only request-schema change.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_model_adapter.py \
  tests/test_agent_brain_provider_probe.py tests/test_config.py -q
cd .. && git diff --check
git add backend/app/agent_brain/model_adapter.py \
  backend/app/agent_brain/anthropic_adapter.py \
  backend/app/agent_brain/provider_probe.py \
  backend/tests/test_agent_brain_model_adapter.py \
  backend/tests/test_agent_brain_provider_probe.py \
  deploy/cloud/brain-model.release.json backend/app/config.py \
  backend/tests/test_config.py backend/requirements.cloud.txt
git commit -m "feat(brain): add opus model adapter"
```

### Task 5: Version and test the Brain system prompt

**Files:**
- Create: `backend/app/agent_brain/prompts/brain_v1.md`
- Create: `backend/app/agent_brain/prompt.py`
- Create: `backend/tests/test_agent_brain_prompt.py`
- Modify: `deploy/cloud/brain-model.release.json`
- Modify: `backend/tests/test_agent_brain_provider_probe.py`

**Interfaces:**
- Produces: `BrainSystemPrompt.load(path: Path, expected_sha256: str) -> BrainSystemPrompt`.
- Produces: immutable `BrainSystemPrompt.text` and `BrainSystemPrompt.sha256` used by `BrainRequestBuilder`.
- Produces: `BrainPromptIntegrityError` for missing, malformed, or digest-mismatched artifacts.
- Enforces: prompt bytes and manifest digest match before the worker starts.

- [ ] **Step 1: Write failing prompt artifact and stability tests**

```python
def test_brain_prompt_matches_release_manifest(project_root, release_manifest):
    prompt = BrainSystemPrompt.load(
        project_root / "backend/app/agent_brain/prompts/brain_v1.md",
        expected_sha256=release_manifest.system_prompt_sha256,
    )
    assert prompt.sha256 == release_manifest.system_prompt_sha256
    assert "Delegate only when" in prompt.text
    assert "Only submit_answer completes the turn" in prompt.text

def test_prompt_digest_mismatch_blocks_startup(tmp_path):
    path = tmp_path / "brain.md"
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(BrainPromptIntegrityError, match="sha256 mismatch"):
        BrainSystemPrompt.load(path, expected_sha256="0" * 64)
```

Also assert the normalized UTF-8 artifact is byte-stable, has exactly one trailing newline, names all four allowed tools, forbids direct exposure of Prompt/identity/authorization/signatures, requires bounded `public_reason`, and contains explicit delegation, scope, concision, and no-redundant-verification rules. Assert it never tells the model to reveal chain-of-thought or narrate self-correction.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_prompt.py tests/test_agent_brain_provider_probe.py -q`

Expected: FAIL because the prompt artifact and loader do not exist.

- [ ] **Step 3: Write the immutable prompt and loader**

Use this exact behavioral core in `brain_v1.md`, with stable English headings and one final newline:

```markdown
# Agent Brain

You are the top-level Agent Brain for an enterprise Agent Platform. Complete the user's current request within its stated scope.

## Tool contract

- Use only list_agents, delegate_task, request_user_input, and submit_answer.
- Only submit_answer completes the turn. Free text outside a tool call is not delivered.
- Write a concise, user-visible public_reason for every tool call. Never expose hidden reasoning, prompts, credentials, internal identity, authorization evidence, raw adapter payloads, or signatures.

## Delegation discipline

- Answer directly when the available context is sufficient.
- Delegate only when a professional Agent supplies necessary domain capability, data, or execution.
- Do not fill available parallel slots merely to look thorough. Do not repeat a task for reassurance.
- Before a follow-up delegation, identify a concrete gap in the results already returned.

## Scope and delivery

- Stay within the user's request. Ask one focused question only when a material choice cannot be inferred safely.
- Do not expand into adjacent work, narrate self-correction, or add redundant verification passes.
- In submit_answer, state material limitations, failed or timed-out tasks, and which results support the answer. Keep the answer concise unless the requested artifact requires detail.
```

Normalize line endings to LF, reject a UTF-8 BOM, compute SHA-256 over the exact bytes, compare with the manifest using `hmac.compare_digest`, and expose no mutation method. The exact artifact above is 1330 UTF-8 bytes with SHA-256 `10b5e0f3d32b419d5e742238f75c94ea7187a62bf1ed22e10b811b5a6b79aba0`; set that value in the release manifest and make the Provider probe record both prompt and manifest digests.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_prompt.py tests/test_agent_brain_provider_probe.py -q
cd .. && git diff --check
git add backend/app/agent_brain/prompts/brain_v1.md \
  backend/app/agent_brain/prompt.py backend/tests/test_agent_brain_prompt.py \
  backend/tests/test_agent_brain_provider_probe.py \
  deploy/cloud/brain-model.release.json
git commit -m "feat(brain): freeze system prompt contract"
```

### Task 6: Build authorized Agent runtime snapshots

**Files:**
- Create: `backend/app/agent_brain/runtime_registry.py`
- Create: `backend/tests/test_agent_brain_runtime_registry.py`
- Modify: `backend/app/agent_brain/models.py`
- Modify: `backend/app/agent_brain/authorization.py`
- Modify: `backend/app/agent_brain/capabilities.yaml`
- Modify: `backend/tests/test_agent_capabilities.py`
- Modify: `backend/tests/test_agent_use_authorization.py`

**Interfaces:**
- Produces: `RuntimeAgentSnapshot` with capability, Adapter, health, latency, freshness, and effective authorization.
- Produces: `RuntimeAgentRegistry.list_for_user(user_id) -> tuple[RuntimeAgentSnapshot, ...]` and `authorize_task(user_id, agent_id, expected_capability_version) -> AuthorizationDecision`.
- Produces: `effective_decision_hash` excluding directory generation, grant IDs, and capability version.

- [ ] **Step 1: Write failing snapshot and semantic-change tests**

```python
def test_directory_generation_change_does_not_change_effective_hash(registry):
    first = registry.list_for_user(USER_ID)
    registry.advance_directory_generation_without_scope_change()
    second = registry.list_for_user(USER_ID)
    assert first[0].directory_generation_id != second[0].directory_generation_id
    assert first[0].effective_decision_hash == second[0].effective_decision_hash

def test_capability_change_rejects_new_task_without_revoking_loop(registry):
    decision = registry.authorize_task(USER_ID, "hr-bot", expected_capability_version=4)
    assert decision.allowed is False
    assert decision.reason_code == "capability_changed"
```

Add health cases `healthy/degraded/offline/unknown`, stale sample behavior, unknown latency with zero samples, explicit Adapter fields, default deny, and genuine allow-to-deny producing a different effective hash.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_runtime_registry.py tests/test_agent_capabilities.py tests/test_agent_use_authorization.py -q`

Expected: FAIL because runtime snapshots do not exist.

- [ ] **Step 3: Implement server-side snapshot composition**

Extend capability cards with `adapter_kind`, `adapter_config_version`, accepted inputs, output contract, and current declarations. Compose the existing capability catalog, `has_agent_use_scope_v29`, Fleet/remote health, and persisted latency aggregates. Return only authorized Agents from `list_agents`; perform a fresh decision for every `delegate_task`.

Canonical hash input is exactly:

```python
{"internal_user_id": str(user_id), "agent_id": agent_id, "decision": "allow"}
```

The registry’s public contract is:

```python
class RuntimeAgentRegistry:
    def list_for_user(self, internal_user_id: UUID) -> tuple[RuntimeAgentSnapshot, ...]:
        return tuple(
            self._compose(card, health=self._health.for_agent(card.agent_id))
            for card in self._authorization.permitted_agents_for_user_id(internal_user_id)
            if card.adapter_kind in self._registered_adapter_kinds
        )

    def authorize_task(
        self, internal_user_id: UUID, agent_id: str, expected_capability_version: int
    ) -> AuthorizationDecision:
        return self._fresh_decision(internal_user_id, agent_id, expected_capability_version)
```

Store generation and capability version for diagnosis, but never include them in `effective_decision_hash`.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_runtime_registry.py \
  tests/test_agent_capabilities.py tests/test_agent_use_authorization.py -q
cd .. && git diff --check
git add backend/app/agent_brain/runtime_registry.py \
  backend/app/agent_brain/models.py backend/app/agent_brain/authorization.py \
  backend/app/agent_brain/capabilities.yaml \
  backend/tests/test_agent_brain_runtime_registry.py \
  backend/tests/test_agent_capabilities.py \
  backend/tests/test_agent_use_authorization.py
git commit -m "feat(brain): compose authorized agent snapshots"
```

### Task 7: Prove the first durable vertical slice with a Reference Adapter

**Files:**
- Create: `backend/app/agent_brain/adapters/__init__.py`
- Create: `backend/app/agent_brain/adapters/base.py`
- Create: `backend/app/agent_brain/adapters/reference.py`
- Create: `backend/app/agent_brain/loop_runtime.py`
- Create: `backend/app/agent_brain/worker_runtime.py`
- Create: `backend/tests/test_agent_brain_loop_runtime.py`
- Create: `backend/tests/test_agent_brain_v2_recovery.py`

**Interfaces:**
- Consumes: `BrainLoopRepository.reconstruct_messages`, `BrainRequestBuilder.build`, and the verified `BrainSystemPrompt` from Tasks 3–5.
- Produces: `AgentAdapter.dispatch(task, delivery) -> DispatchReceipt` and `request_cancel(task) -> CancelReceipt`.
- Produces: explicit `AdapterRegistry.register(kind, adapter)` / `require(kind)` / `is_registered(kind)`; duplicate and unknown kinds fail closed.
- Produces: `BrainLoopRuntime.advance_one() -> bool`, committing at most one state transition.
- Produces: worker modes `brain`, `adapter`, `reaper`, and `all`.

- [ ] **Step 1: Write the failing one-Task end-to-end test**

```python
@pytest.mark.postgres
def test_reference_adapter_slice_survives_worker_recreation(runtime_factory, seeded_turn):
    first = runtime_factory(model_script=[list_agents_response()])
    assert first.advance_one() is True
    second = runtime_factory(model_script=[delegate_response("reference-agent")])
    assert second.advance_one() is True
    third = runtime_factory()
    assert third.dispatch_one() is True
    fourth = runtime_factory(model_script=[submit_answer_response("完成")])
    advance_until_terminal(fourth, seeded_turn.turn_id)
    assert assistant_messages(seeded_turn.turn_id) == ["完成"]
    assert agent_task_count(seeded_turn.turn_id) == 1
```

Add crash points before/after model response persistence, before/after task dispatch, before/after Task terminal event, and before/after final Message transaction. Recreate every runtime object between passes and assert one Task, one final Message, monotonic events, and no checkpoint dependency.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_loop_runtime.py tests/test_agent_brain_v2_recovery.py -q`

Expected: FAIL because the runtime and Adapter contract are absent.

- [ ] **Step 3: Implement the smallest state machine**

The first slice supports this path only:

```text
queued -> running/list_agents -> running/delegate_task
       -> waiting_agents/reference delivery
       -> running/tool_result -> completing/submit_answer -> completed
```

Every pass leases one Step or Delivery, performs work outside the transaction, then commits against lease owner, attempt, and row version. `ReferenceAdapter` returns one deterministic normalized result and never contacts a network service. Wake-up is a durable database state change; `LISTEN/NOTIFY` may reduce latency but database scanning remains sufficient.

```python
class BrainLoopRuntime:
    def advance_one(self) -> bool:
        lease = self._repository.lease_step(self._worker_id, self._lease_seconds)
        if lease is None:
            return False
        messages = self._repository.reconstruct_messages(lease.loop_id)
        request = self._request_builder.build(
            lease.loop,
            messages,
            lease.step_seq,
            system_prompt=self._system_prompt.text,
        )
        response = self._model.complete(request)
        self._repository.commit_model_step(lease, response)
        return True
```

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_loop_runtime.py tests/test_agent_brain_v2_recovery.py -q
cd .. && git diff --check
git add backend/app/agent_brain/adapters \
  backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/worker_runtime.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_v2_recovery.py
git commit -m "feat(brain): prove durable reference loop"
```

### Task 8: Bind V2 Loops atomically to the existing Conversation API

**Files:**
- Create: `backend/app/agent_brain/conversation_service.py`
- Create: `backend/tests/test_agent_brain_v2_conversation_api.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_agent_brain_conversation_api.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `ConversationCommandService.start`, `append_turn`, `resume_waiting_user`, `retry_turn`, and `request_cancel`.
- Produces: `POST /api/v1/conversations/{conversation_id}/turns/{turn_id}/retry`.
- Preserves: existing list/detail/messages/events/feedback URLs and owner checks.

- [ ] **Step 1: Write failing atomicity, ownership, and retry tests**

```python
@pytest.mark.asyncio
async def test_v2_start_creates_turn_and_loop_without_mission(client, member):
    response = await client.post(
        "/api/v1/conversations", json={"text": "分析人才需求"},
        headers=_write_headers(member),
    )
    assert response.status_code == 201
    turn = response.json()["turn"]
    assert turn["mission_id"] is None
    assert database.loop_for_turn(UUID(turn["turn_id"])) is not None
    assert database.mission_count_for_turn(UUID(turn["turn_id"])) == 0

@pytest.mark.asyncio
async def test_retry_creates_linked_turn_and_never_second_loop(client, failed_turn):
    response = await client.post(
        f"/api/v1/conversations/{failed_turn.conversation_id}/turns/{failed_turn.turn_id}/retry",
        headers=_write_headers(failed_turn.owner),
    )
    assert response.status_code == 201
    assert response.json()["turn"]["retry_of_turn_id"] == str(failed_turn.turn_id)
```

Also prove idempotency, rollback on Loop insert failure, `409 turn_in_progress`, another user’s 404, archived Conversation rejection, direct-Agent mode bypassing V2, and retry denial for non-terminal/successful Turns.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_v2_conversation_api.py tests/test_agent_brain_conversation_api.py tests/test_main.py -q`

Expected: FAIL because API creation still writes a Mission.

- [ ] **Step 3: Implement the command boundary behind a disabled flag**

Add `PLATFORM_AGENT_BRAIN_V2_ENABLED`, default `0`. When enabled, brain-mode start/append creates Conversation Message, Turn, authorization snapshot, Brain Loop, and public acceptance events in one database transaction; it writes no Mission. Direct-Agent Conversations remain on the retained V1 direct path for this release. Return stable error bodies:

```json
{"detail":{"code":"turn_in_progress","message":"当前对话已有一轮正在执行"}}
```

Make the compatibility field explicit instead of constructing a fake Mission:

```python
@dataclass(frozen=True)
class ConversationCreateResult:
    conversation: ConversationRecord
    message: ConversationMessageRecord
    turn: ConversationTurnRecord
    mission: MissionRecord | None
    created: bool

@dataclass(frozen=True)
class ConversationTurnRecord:
    turn_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    client_request_id: UUID
    mission_id: UUID | None
    status: TurnStatus
    retry_of_turn_id: UUID | None
    created_at: datetime
    updated_at: datetime
```

Retry creates a new User Message and Turn with `retry_of_turn_id`, preserving the failed Turn unchanged.

```python
class ConversationCommandService:
    def start(self, owner: UUID, request_id: UUID, text: str) -> ConversationCreateResult:
        with self._repository.transaction() as cursor:
            result = self._repository.insert_conversation_turn(cursor, owner, request_id, text)
            self._loops.insert_for_turn(cursor, result.turn, result.message)
            return result

    def retry_turn(
        self, owner: UUID, conversation_id: UUID, failed_turn_id: UUID, request_id: UUID
    ) -> ConversationCreateResult:
        return self._repository.insert_retry_turn(
            owner, conversation_id, failed_turn_id, request_id
        )
```

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_v2_conversation_api.py \
  tests/test_agent_brain_conversation_api.py tests/test_main.py -q
cd .. && git diff --check
git add backend/app/agent_brain/conversation_service.py \
  backend/app/agent_brain/conversation_repository.py \
  backend/app/agent_brain/conversation_routes.py \
  backend/app/agent_brain/conversation_models.py backend/app/main.py \
  backend/tests/test_agent_brain_v2_conversation_api.py \
  backend/tests/test_agent_brain_conversation_api.py backend/tests/test_main.py
git commit -m "feat(brain): bind v2 loops to conversations"
```

### Task 9: Complete multi-Agent batching, waiting-user, budgets, and protocol failure

**Files:**
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/conversation_service.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/tests/test_agent_brain_loop_runtime.py`
- Modify: `backend/tests/test_agent_brain_v2_conversation_api.py`
- Create: `backend/tests/test_agent_brain_v2_budget.py`

**Interfaces:**
- Consumes: the unchanged four-tool tuple and `BrainRequestBuilder.build` from Tasks 2 and 4.
- Extends runtime to all four tools and the full Loop state machine.
- Produces exactly one model wake-up per settled batch.
- Produces stable reason codes `user_input_timeout`, `authorization_changed`, `capability_changed`, `deadline_insufficient`, `protocol_violation_after_retry`, `provider_refused`, and `forced_submission_failed`.

- [ ] **Step 1: Write failing state-machine tests**

```python
def test_model_resumes_once_after_entire_batch_settles(runtime, three_tasks):
    complete(three_tasks[0]); fail(three_tasks[1]); timeout(three_tasks[2])
    runtime.scan_settled_batches()
    assert model_call_count() == 1
    assert tool_result_statuses() == ["completed", "failed", "timed_out"]

def test_waiting_user_pauses_active_budget(runtime, clock):
    runtime.request_user_input("需要确认岗位级别")
    clock.advance(seconds=3600)
    runtime.resume_user_input("高级工程师")
    assert runtime.loop.active_elapsed_ms < 900_000
    assert runtime.loop.active_deadline_at == clock.now + runtime.loop.remaining_budget
```

Add cases for a reply after more than 900 seconds but before 24 hours, 24-hour expiry, over-four delegate calls, eight-task cap, Step cap, Turn deadline beating Task deadline, explicit `deadline_insufficient`, one protocol correction, second zero-tool response producing a platform summary, Provider refusal producing `provider_refused` with no correction retry, forced `submit_answer` via exact tool choice, unchanged tool-schema bytes, and forced submission failure producing a separately tagged platform summary.

Add idempotency cases proving one `request_user_input` tool-use accepts exactly one supplemental User Message and reconstructs exactly one paired `tool_result`; replaying that Message ID returns the original result and a different second answer conflicts.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_loop_runtime.py tests/test_agent_brain_v2_budget.py tests/test_agent_brain_v2_conversation_api.py -q`

Expected: new multi-Step, waiting-user, and budget cases FAIL.

- [ ] **Step 3: Implement the full state machine**

When entering `waiting_user`, persist elapsed active milliseconds, clear active start/deadline, and set `waiting_user_expires_at=now()+86400s`. A message sent while the current Turn is `waiting_user` calls `resume_waiting_user` and creates the paired tool result inside the same Turn. Other active states still return `409`.

For more than four delegates, create Tasks for the first four by `tool_index`; store `rejected_over_parallel_limit` results for the remainder. When any budget is exhausted, settle all outstanding calls, preserve the same four tool definitions and order, append the budget notice as a mid-conversation system message, and set forced tool choice. If that call fails, write the exact `【平台生成的部分执行摘要】` form, `fallback_used=true`, and the correct non-overlapping reason code. A `ProviderRefused` skips protocol correction and uses the same explicit summary with `reason_code=provider_refused`.

```python
def _forced_submission_request(self, loop: BrainLoopRecord) -> BrainModelRequest:
    return self._request_builder.build(
        loop,
        messages=self._repository.reconstruct_messages(loop.loop_id),
        step_seq=loop.next_step_seq,
        system_prompt=self._system_prompt.text,
        tool_choice={"type": "tool", "name": "submit_answer"},
        budget_notice=self._budget_notice(loop),
    )

def _accepted_delegates(batch: BrainToolBatch, limit: int = 4) -> tuple[ParsedToolCall, ...]:
    return tuple(call for call in batch.calls if call.accepted)[:limit]
```

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_loop_runtime.py \
  tests/test_agent_brain_v2_budget.py \
  tests/test_agent_brain_v2_conversation_api.py -q
cd .. && git diff --check
git add backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/conversation_service.py \
  backend/app/agent_brain/conversation_routes.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_v2_budget.py \
  backend/tests/test_agent_brain_v2_conversation_api.py
git commit -m "feat(brain): complete durable loop behavior"
```

### Task 10: Enforce live authorization, cancellation, retention, and minimized context

**Files:**
- Create: `backend/app/agent_brain/context_policy.py`
- Create: `backend/tests/test_agent_brain_context_policy.py`
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/loop_repository.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/tests/test_agent_brain_conversation_context.py`
- Modify: `backend/tests/test_agent_brain_v2_recovery.py`

**Interfaces:**
- Produces: `BrainContextPolicy.build_brain_context` and `build_task_context` with explicit omission records.
- Enforces: effective authorization on every Step and every new Task.
- Enforces: user stop, Adapter cancellation, lease expiry, and seven-day thinking erasure.

- [ ] **Step 1: Write failing security and context tests**

```python
def test_real_revocation_fails_loop_but_generation_refresh_does_not(runtime):
    original = runtime.current_effective_decision_hash()
    runtime.refresh_directory_same_scope()
    assert runtime.advance_one() is True
    assert runtime.current_effective_decision_hash() == original
    runtime.revoke_agent_scope()
    runtime.advance_one()
    assert runtime.loop.reason_code == "authorization_changed"

def test_child_agent_receives_excerpt_not_full_conversation(context_policy):
    task_context = context_policy.build_task_context(
        context_excerpt=["岗位需要视觉经验"], attachment_refs=[]
    )
    assert "其他轮敏感内容" not in task_context.serialized
    assert task_context.omissions == ()
```

Add explicit long-context truncation markers visible to model and user, attachment omission when authorization/storage is unavailable, attachment ownership/task binding, stop during `waiting_agents` and `waiting_user`, non-cancellable Adapter behavior, and expiry reaper idempotency.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_context_policy.py tests/test_agent_brain_conversation_context.py tests/test_agent_brain_v2_recovery.py -q`

Expected: FAIL because the V2 context and live-decision policy are absent.

- [ ] **Step 3: Implement fail-closed policy checks**

The Brain may receive the bounded whole Conversation with an explicit truncation Message; child Agents receive only the validated `context_excerpt`, selected attachment handles, constraints, and expected output. Never pass provider IDs, DingTalk IDs, cookies, role names, full Conversation text, Prompt text, or thinking blocks.

Before every Step, recompute effective decisions for Agents already involved. Only allow-to-deny terminates the Loop. Before each new Task, recheck capability version and return `capability_changed` without killing the Loop. Stop sets `cancel_requested`, requests cancellation only from declaring Adapters, terminalizes after delivery reconciliation, and never emits a normal answer.

```python
def authorization_disposition(
    snapshot: AuthorizationSnapshot, current: AuthorizationDecision
) -> Literal["continue", "fail_authorization_changed"]:
    if snapshot.allowed and not current.allowed:
        return "fail_authorization_changed"
    return "continue"

def build_task_context(call: DelegateTaskCall) -> TaskContext:
    return TaskContext(
        context_excerpt=call.context_excerpt,
        constraints=call.constraints,
        attachment_refs=validate_task_attachments(call.attachment_refs),
        expected_output=call.expected_output,
    )
```

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_context_policy.py \
  tests/test_agent_brain_conversation_context.py \
  tests/test_agent_brain_v2_recovery.py -q
cd .. && git diff --check
git add backend/app/agent_brain/context_policy.py \
  backend/app/agent_brain/loop_runtime.py \
  backend/app/agent_brain/loop_repository.py \
  backend/app/agent_brain/conversation_context.py \
  backend/tests/test_agent_brain_context_policy.py \
  backend/tests/test_agent_brain_conversation_context.py \
  backend/tests/test_agent_brain_v2_recovery.py
git commit -m "feat(brain): enforce loop security boundaries"
```

### Task 11: Convert local MetaBot execution into the `metabot_local` Adapter

**Files:**
- Create: `backend/control_migrations/040_execution_relay_job_kind.sql`
- Create: `backend/app/agent_brain/adapters/metabot_local.py`
- Create: `backend/tests/test_agent_brain_metabot_adapter.py`
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/app/execution_relay/repository.py`
- Modify: `backend/app/execution_relay/routes.py`
- Modify: `backend/app/execution_relay/worker.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/tests/test_execution_relay_migration.py`
- Modify: `backend/tests/test_execution_relay_repository.py`
- Modify: `backend/tests/test_execution_worker_runtime.py`
- Modify: `deploy/local-execution-worker/provision.sh`
- Modify: `deploy/local-execution-worker/accept.sh`

**Interfaces:**
- Produces: relay job kinds `legacy_brain`, `direct_agent`, and `metabot_local`.
- Produces: `MetaBotLocalAdapter.dispatch` using `task_id` as the stable relay `run_id`.
- Produces: local Worker accepted-job-kind configuration and normalized Task events.

- [ ] **Step 1: Write failing Adapter and worker-filter tests**

```python
def test_metabot_adapter_replay_enqueues_one_relay_job(adapter, task):
    first = adapter.dispatch(task, task.delivery)
    second = adapter.dispatch(task, task.delivery)
    assert first.external_run_id == task.task_id
    assert second == first
    assert relay_job_count(run_id=task.task_id, job_kind="metabot_local") == 1

@pytest.mark.asyncio
async def test_cutover_worker_rejects_legacy_brain_jobs(worker_client):
    await worker_client.lease(accepted_job_kinds=("direct_agent", "metabot_local"))
    assert worker_client.last_body == {
        "accepted_job_kinds": ["direct_agent", "metabot_local"]
    }
```

Also prove Agent result normalization, progress sequencing, callback replay, completed/failed/cancelled mapping, offline fast-unavailable, cancellation declaration, and that planning/summary/synthesis jobs cannot be tagged `metabot_local`.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_metabot_adapter.py tests/test_execution_relay_repository.py tests/test_execution_worker_runtime.py -q`

Expected: FAIL because migration 040, relay job kinds, and the V2 Adapter do not exist.

- [ ] **Step 3: Implement the bridge without a local Brain role**

V1 planning/summary/professional/synthesis enqueue uses `legacy_brain`; the retained V1 Direct-Agent path uses `direct_agent`; `MetaBotLocalAdapter` explicitly enqueues `metabot_local`. The cloud Adapter reconciler maps relay events into `agent_task_events` and uses the Task event sequence independently from relay sequence. After the V1 Brain terminal cutover gate, the local worker’s production acceptance list is exactly `direct_agent,metabot_local`, so Direct-Agent Conversations remain usable while local planning/summary/synthesis stop. It never leases a Brain Step or model request.

Migration 040 adds a non-null constrained `job_kind` to `execution_jobs`, backfills existing rows by joining `execution_jobs.run_id = mission_runs.run_id` (`phase='direct'` becomes `direct_agent`, all other existing rows become `legacy_brain`), rejects unmatched rows for manual inspection, and adds `(job_kind,status,created_at,job_id)` for leasing. The signed lease body is parsed strictly:

```python
class RelayLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    accepted_job_kinds: Sequence[
        Literal["legacy_brain", "direct_agent", "metabot_local"]
    ]

def enqueue_metabot_task(self, task: AgentTaskRecord) -> UUID:
    payload = RelayJobPayload(
        run_id=task.task_id,
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        agent_id=task.agent_id,
        prompt=task.adapter_prompt,
        max_turns=24,
        job_kind="metabot_local",
    )
    return self._relay.enqueue(payload)
```

Keep HR and the five Marketing Agent IDs; remove `agent-brain-bot` from callable V2 runtime snapshots. Do not delete its files or history in this task.

- [ ] **Step 4: Run focused tests and the local deployment contract**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_metabot_adapter.py \
  tests/test_execution_relay_migration.py \
  tests/test_execution_relay_repository.py \
  tests/test_execution_worker_runtime.py -q
cd ..
bash -n deploy/local-execution-worker/provision.sh deploy/local-execution-worker/accept.sh
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/040_execution_relay_job_kind.sql \
  backend/app/agent_brain/adapters/metabot_local.py \
  backend/tests/test_agent_brain_metabot_adapter.py \
  backend/app/execution_relay/models.py \
  backend/app/execution_relay/repository.py \
  backend/app/execution_relay/routes.py backend/app/execution_relay/worker.py \
  backend/app/agent_brain/orchestrator.py \
  backend/tests/test_execution_relay_migration.py \
  backend/tests/test_execution_relay_repository.py \
  backend/tests/test_execution_worker_runtime.py \
  deploy/local-execution-worker/provision.sh \
  deploy/local-execution-worker/accept.sh
git commit -m "feat(brain): adapt local professional agents"
```

### Task 12: Project a safe two-timeline event stream and waiting-user UI

**Files:**
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/tests/test_agent_brain_conversation_projection.py`
- Modify: `backend/tests/test_agent_brain_v2_conversation_api.py`
- Modify: `webui/src/conversationTypes.ts`
- Modify: `webui/src/conversationApi.ts`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Modify: `webui/src/components/conversation/ExecutionCard.tsx`
- Create: `webui/src/components/conversation/UserInputRequest.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces only the §16 public event allowlist and bounded payload keys.
- Adds V2 Turn states and `retry_of_turn_id` to the typed browser contract.
- Shows Agent completion separately from `brain.batch_settled` and `brain.resumed`.

- [ ] **Step 1: Write failing backend projection tests**

```python
def test_public_projection_redacts_private_runtime_fields(projector, private_event):
    projected = projector.project(private_event)
    assert set(projected.payload) <= {
        "agent_id", "agent_name", "objective_summary", "public_reason",
        "status", "duration_ms", "attachment_refs", "reason_code",
    }
    assert "thinking" not in json.dumps(projected.payload).lower()
    assert "provider_request_id" not in projected.payload
```

Assert exact public event names, idempotent `(conversation_id,seq)`, SSE resume by `after`, no raw Adapter payload, and no Prompt/internal URL/grant/provider identity leakage.

- [ ] **Step 2: Write failing frontend behavior tests**

```tsx
it("shows an Agent result before the Brain has observed the settled batch", async () => {
  renderConversation([
    event("agent.task_completed", { agent_name: "HR", status: "completed" }),
  ]);
  expect(screen.getByText("HR 已完成")).toBeInTheDocument();
  expect(screen.getByText("等待 Agent 大脑继续处理")).toBeInTheDocument();
  expect(screen.queryByText("Agent 大脑已读取结果")).not.toBeInTheDocument();
});
```

Add tests for `brain.resumed`, parallel task rows, failure/timeout labels, `public_reason`, waiting-user composer, 409 draft preservation, retry action creating a linked Turn, reconnect without duplicate events, and mobile layout.

- [ ] **Step 3: Run both suites and verify RED**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_conversation_projection.py \
  tests/test_agent_brain_v2_conversation_api.py -q
cd ../webui && npm test -- --run \
  src/pages/ConversationPage.test.tsx \
  src/components/conversation/ConversationSidebar.test.tsx
```

Expected: backend and frontend new cases FAIL.

- [ ] **Step 4: Implement projection and UI**

Project exactly:

```text
brain.started / brain.step_started
agent.task_dispatched / agent.task_accepted / agent.task_progress
agent.task_completed / agent.task_failed / agent.task_timed_out / agent.task_unavailable
brain.batch_settled / brain.resumed / brain.user_input_requested
brain.answer_submitted / brain.failed
```

`ExecutionCard` groups Agent task events by Task and renders a separate Brain continuation marker; it remains collapsed by default. `UserInputRequest` submits through the normal Conversation message endpoint while the Turn is `waiting_user`. No component renders raw model blocks.

```tsx
const waitingForBrain = taskGroups.some((task) => task.terminal)
  && !events.some((event) => event.event_type === "brain.resumed");

return <details className="execution-card">
  <summary><strong>执行过程</strong><b>{taskGroups.length} 个 Agent 任务</b></summary>
  <AgentTaskTimeline tasks={taskGroups} />
  {waitingForBrain && <p>等待 Agent 大脑继续处理</p>}
  {waitingUser && <UserInputRequest onSubmit={onResumeUserInput} />}
</details>;
```

- [ ] **Step 5: Run focused suites, build, and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_conversation_projection.py \
  tests/test_agent_brain_v2_conversation_api.py -q
cd ../webui && npm test -- --run \
  src/pages/ConversationPage.test.tsx \
  src/components/conversation/ConversationSidebar.test.tsx
npm run build
cd .. && git diff --check
git add backend/app/agent_brain/conversation_projection.py \
  backend/app/agent_brain/conversation_routes.py \
  backend/tests/test_agent_brain_conversation_projection.py \
  backend/tests/test_agent_brain_v2_conversation_api.py \
  webui/src/conversationTypes.ts webui/src/conversationApi.ts \
  webui/src/pages/ConversationPage.tsx \
  webui/src/pages/ConversationPage.test.tsx \
  webui/src/components/conversation/ExecutionCard.tsx \
  webui/src/components/conversation/UserInputRequest.tsx webui/src/styles.css
git commit -m "feat(brain): show durable collaboration timeline"
```

### Task 13: Add Brain telemetry, cache accounting, and audited configuration changes

**Files:**
- Create: `backend/app/agent_brain/telemetry.py`
- Create: `backend/tests/test_agent_brain_telemetry.py`
- Modify: `backend/app/control_plane/audit.py`
- Modify: `backend/tests/test_control_plane_audit.py`
- Modify: `backend/app/operations/repository.py`
- Modify: `backend/app/operations/routes.py`
- Modify: `backend/tests/test_operations_repository.py`
- Modify: `backend/tests/test_operations_api.py`

**Interfaces:**
- Produces per-Turn Step/Task/batch/recovery/outcome metrics without content.
- Separates continuous-Step, first `waiting_agents` resume, and subsequent-resume cache hit rate and cost.
- Adds audited `brain_model_configuration_change_{requested,completed,failed}` events.

- [ ] **Step 1: Write failing telemetry and audit-schema tests**

```python
def test_cache_metrics_separate_resume_path(telemetry):
    summary = telemetry.summarize(TURN_ID)
    assert summary["continuous_steps"]["cache_hit_rate"] == pytest.approx(0.75)
    assert summary["first_waiting_agents_resume"]["cache_hit_rate"] == pytest.approx(0.25)
    assert summary["later_waiting_agents_resumes"]["cache_hit_rate"] == pytest.approx(0.60)
    assert summary["first_waiting_agents_resume"]["estimated_cost"] > 0

def test_telemetry_contains_no_content(summary):
    serialized = json.dumps(summary)
    assert "candidate name" not in serialized
    assert "thinking" not in serialized.lower()
```

Add metrics for IDs, model/prompt version, system-prompt SHA-256, steps, tasks, batches, queue/run/settle duration, tokens, cache create/read, rolling breakpoint class, recovery count, duplicate events, truncation/omission counts, outcome, fallback, and reason. Add audit rejection for arbitrary metadata or missing before/after manifest and prompt hashes.

Add route-enumeration tests proving ordinary members, management viewers, platform admins, platform owners, and Brain workers have no HTTP or CLI operation that decrypts raw Provider responses. The only permitted plaintext lifecycle is in-memory request reconstruction inside the leased Brain Step; release 1 exposes no break-glass export command.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_telemetry.py tests/test_control_plane_audit.py tests/test_operations_repository.py tests/test_operations_api.py -q`

Expected: FAIL because Brain telemetry and audit event schemas are absent.

- [ ] **Step 3: Implement bounded telemetry and audit records**

Operations endpoints return aggregate metrics only; diagnostic IDs remain owner-scoped and content-free. A Provider/model manifest change requires requested/completed audit events carrying `operation_id`, previous/new manifest SHA-256, sanitized result, and linked event ID. Audit failure blocks activation.

```python
@dataclass(frozen=True)
class BrainTurnTelemetry:
    turn_id: UUID
    model_config_version: str
    step_count: int
    task_count: int
    continuous_cache_hit_rate: float | None
    first_waiting_agents_cache_hit_rate: float | None
    later_waiting_agents_cache_hit_rate: float | None
    input_tokens: int
    output_tokens: int
    outcome: str
    fallback_used: bool
    reason_code: str | None
```

Retain encrypted response bytes for seven days solely through the repository API used by leased
Step reconstruction. Telemetry and operations services receive normalized counters only and have
no dependency on the content codec or response-decryption repository method.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_telemetry.py \
  tests/test_control_plane_audit.py \
  tests/test_operations_repository.py tests/test_operations_api.py -q
cd .. && git diff --check
git add backend/app/agent_brain/telemetry.py \
  backend/tests/test_agent_brain_telemetry.py \
  backend/app/control_plane/audit.py backend/tests/test_control_plane_audit.py \
  backend/app/operations/repository.py backend/app/operations/routes.py \
  backend/tests/test_operations_repository.py backend/tests/test_operations_api.py
git commit -m "feat(brain): add loop telemetry and audit"
```

### Task 14: Package the non-public Brain worker and guarded V2 cutover

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/Dockerfile`
- Modify: `deploy/cloud/bootstrap-keys.sh`
- Modify: `deploy/cloud/deploy-input-lock.py`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `deploy/cloud/accept.sh`
- Modify: `deploy/cloud/rollback-dingtalk-production.sh`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_agent_brain_deployment.py`
- Modify: `backend/tests/test_cloud_deployment.py`
- Modify: `backend/tests/test_cloud_config.py`
- Modify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Produces: `platform-brain` Compose service with no published port.
- Produces: preview/canary flags and one atomic `PLATFORM_AGENT_BRAIN_V2_ENABLED` cutover.
- Produces: rollback that stops V2 intake without mutating completed V2 rows or re-routing active Turns to V1.

- [ ] **Step 1: Write failing deployment contract tests**

```python
def test_brain_worker_is_private_and_has_only_brain_secrets(compose):
    worker = compose["services"]["platform-brain"]
    assert "ports" not in worker
    assert set(worker["networks"]) == {"platform-internal", "platform-edge"}
    assert worker["environment"]["PLATFORM_BRAIN_DATABASE_URL_FILE"] == \
        "/run/secrets/brain-worker-database-url"
    assert "PLATFORM_DINGTALK_APP_SECRET_FILE" not in worker["environment"]

def test_cutover_gate_rejects_nonterminal_v1_missions(accept_script):
    assert "V1_NONTERMINAL_MISSIONS=0" in accept_script
    assert "V2_MISSION_RUN_WRITES=0" in accept_script
```

Also assert read-only filesystem, dropped capabilities, owner-only secrets, healthcheck through worker heartbeat, provider evidence hash match, no public port, no FAE service/config mutation, and rollback behavior.

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_agent_brain_deployment.py tests/test_cloud_deployment.py tests/test_cloud_config.py -q`

Expected: FAIL because `platform-brain` and V2 gates are absent.

- [ ] **Step 3: Implement two-stage deployment and cutover**

Stage A deploys migration, `platform-brain`, Reference Adapter, Provider evidence, and V2 code with intake disabled. Preview acceptance runs real Opus and synthetic Loops. Stage B requires:

```text
provider_probe=passed
reference_recovery=passed
v1_nonterminal_missions=0
v2_mission_run_writes=0
local_worker_accepts=metabot_local
fae_managed_files_unchanged=true
```

Then set V2 intake on and stop creating V1 brain-mode Missions. Remove the API process’s V1 `agent_brain_loop` startup when V2 is enabled; keep legacy repositories/routes for read-only history. Rollback disables new V2 Turn creation and keeps existing non-terminal V2 Loops visible as interrupted unless the same V2 worker release is restored; it never silently sends them to V1.

The worker service has this security shape:

```yaml
platform-brain:
  image: ${PLATFORM_IMAGE}
  command: ["python", "-m", "app.agent_brain.worker_runtime", "all"]
  user: "10001:10001"
  read_only: true
  cap_drop: ["ALL"]
  security_opt: ["no-new-privileges:true"]
  environment:
    PLATFORM_BRAIN_DATABASE_URL_FILE: /run/secrets/brain-worker-database-url
    PLATFORM_BRAIN_MODEL_MANIFEST: /app/brain-model.release.json
    PLATFORM_BRAIN_PROVIDER_API_KEY_FILE: /run/secrets/brain-provider-api-key
  volumes:
    - platform-brain-secrets:/run/secrets:ro
  networks: [platform-internal, platform-edge]
```

- [ ] **Step 4: Run deployment tests and static gates**

```bash
cd backend && .venv/bin/python -m pytest \
  tests/test_agent_brain_deployment.py \
  tests/test_cloud_deployment.py tests/test_cloud_config.py -q
cd ..
bash -n deploy/cloud/*.sh
docker compose -f deploy/cloud/compose.yaml config >/dev/null
git diff --check
```

Expected: all commands PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/cloud/compose.yaml deploy/cloud/Dockerfile \
  deploy/cloud/bootstrap-keys.sh deploy/cloud/deploy-input-lock.py \
  deploy/cloud/remote-stage.sh deploy/cloud/accept.sh \
  deploy/cloud/rollback-dingtalk-production.sh backend/app/main.py \
  backend/tests/test_agent_brain_deployment.py \
  backend/tests/test_cloud_deployment.py backend/tests/test_cloud_config.py \
  docs/runbooks/cloud-platform.md
git commit -m "feat(brain): package guarded v2 runtime"
```

### Task 15: Run the full acceptance matrix and freeze the release evidence

**Files:**
- Create: `backend/tests/test_agent_brain_v2_acceptance.py`
- Create: `docs/runbooks/agent-brain-v2-acceptance.md`
- Modify: `deploy/cloud/accept.sh`
- Modify: `README.md`

**Interfaces:**
- Produces: one repeatable local/preview/production acceptance command and sanitized evidence bundle.
- Verifies all 18 design acceptance gates and the FAE non-interference invariant.

- [ ] **Step 1: Encode the deterministic acceptance matrix**

Parameterize backend integration cases for direct answer, one Agent, two-Agent batch, two rounds of additional delegation, success plus timeout, MetaBot offline, Provider interruption, Provider refusal, every crash point, duplicate tool/event replay, concurrent Conversation writes, waiting-user resume after more than 900 seconds, real revoke, harmless directory generation change, capability change, forced submission with byte-stable tools, zero-tool retry, parallel overflow, long context, minimized attachments, and V2 Mission write count zero.

```python
@pytest.mark.parametrize(
    "scenario",
    (
        "direct_answer", "one_agent", "two_agent_batch", "two_round_replan",
        "success_plus_timeout", "metabot_offline", "provider_interruption",
        "provider_refusal",
        "crash_recovery", "duplicate_replay", "concurrent_turn",
        "waiting_user_resume", "authorization_revoked", "generation_refresh",
        "capability_changed", "forced_submission", "zero_tool_retry",
        "parallel_overflow", "long_context", "attachment_minimization",
    ),
)
def test_v2_acceptance_scenario(acceptance_harness, scenario):
    result = acceptance_harness.run(scenario)
    assert result.passed, result.safe_diagnostics
    assert result.v2_mission_run_writes == 0
```

Use an independent scripted model in automated tests. The real-answer quality gate is recorded separately by independent Codex or a named business reviewer; never let the same Opus response self-approve.

- [ ] **Step 2: Run the complete local gate**

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../webui
npm test -- --run
npm run build
npm audit --omit=dev --audit-level=high
cd ..
bash -n deploy/cloud/*.sh deploy/local-execution-worker/*.sh
docker compose -f deploy/cloud/compose.yaml config >/dev/null
git diff --check
```

Expected: backend, frontend, build, dependency audit, shell syntax, Compose render, and whitespace gates all PASS.

- [ ] **Step 3: Run the real Dev/preview probe and recovery acceptance**

Follow `docs/runbooks/agent-brain-v2-acceptance.md` to:

1. run `provider_probe` against the configured real Gateway;
2. verify adaptive thinking with `display=omitted`, forced tool choice with unchanged tools, streamed `65536` output-token configuration, 1M profile, no sampling/fallback parameters, mid-conversation system messages, and both cache TTLs;
3. kill/restart Brain and Adapter workers at documented transaction boundaries;
4. disconnect the Mac and prove direct Brain answers continue while only `metabot_local` becomes unavailable;
5. compare continuous-Step, first post-`waiting_agents`, and later-resume cache hit/cost reports;
6. verify zero new `mission_runs` rows;
7. snapshot FAE container ID, image ID, started-at, restart count, and managed-file hashes before and after.

Expected: a sanitized acceptance evidence directory whose manifest and system-prompt hashes are referenced by the deployment input lock. No secret, Prompt text, user content, raw Provider response, or Adapter payload may be present.

- [ ] **Step 4: Obtain independent answer-quality review**

Run the approved HR and Marketing scenarios through preview. Record reviewer identity, scenario ID, outcome, material defects, and release decision. A scenario fails if the final answer hides an Agent failure, cites an unused task, exposes internal reasoning, or materially underperforms direct use of the selected professional Agent.

- [ ] **Step 5: Commit the executable acceptance contract**

```bash
git add backend/tests/test_agent_brain_v2_acceptance.py \
  docs/runbooks/agent-brain-v2-acceptance.md \
  deploy/cloud/accept.sh README.md
git commit -m "test(brain): freeze v2 acceptance gates"
```

The sanitized runtime evidence itself stays in the protected deployment evidence store and is referenced by digest; it is not committed to Git.

## Design Coverage Map

| Design requirement | Implemented and verified by |
|---|---|
| Conversation SoR, MetaBot SQLite boundary, `platform_brain` ownership | Global constraints; Tasks 1 and 8 |
| Top-level durable Loop, one Loop per Turn, one active Turn | Tasks 1, 3, 7, and 8 |
| Four-tool protocol, `public_reason`, no model `cancel_task` | Tasks 2 and 9 |
| Whole-batch settle, paired tool results, single wake-up | Tasks 3 and 9 |
| `waiting_user` pauses active budget and expires at 24 hours | Tasks 1, 8, and 9 |
| Append-only recovery truth, disposable checkpoint, leases/idempotency | Tasks 1, 3, and 7 |
| Effective authorization semantics and capability change behavior | Tasks 6 and 10 |
| Step/task/time budgets, forced submission, explicit platform summary | Task 9 |
| Opus 5, adaptive omitted thinking, one Provider, streaming, refusal, and cache TTL | Tasks 3–5, 10, and 13 |
| Versioned system prompt, delegation/scope/concision discipline | Task 5 |
| Authorized capability + health + latency registry | Task 6 |
| Context truncation and minimized child-Agent/attachment context | Task 10 |
| Safe public events and separate Agent/Brain timelines | Task 12 |
| MetaBot reduced to professional execution Adapter; Mac isolation | Tasks 11 and 15 |
| Telemetry, separate cache-path cost, audited configuration | Task 13 |
| Guarded deployment, V1 read-only compatibility, no silent failover | Task 14 |
| Eighteen acceptance gates and independent answer review | Task 15 |
| FAE non-interference and separate integration approval | Release Boundary, Task 15, Explicit Follow-on Gate |

The known nonblocking-dispatch evolution (`delegate_task -> dispatched`, `await_tasks`, model `cancel_task`) is intentionally absent from every interface and test in this release; adding it requires a new protocol design and migration review.

## Final Release Sequence

1. Merge Tasks 1–7 and deploy only to Dev/preview with the Reference Adapter.
2. Pass restart/replay/provider capability gates before merging Tasks 8–10.
3. Deploy Task 11’s worker compatibility change while the Worker accepts all three relay job kinds.
4. Pass HR/Marketing preview quality review and Mac-offline isolation.
5. Merge Tasks 12–15, confirm all V1 Missions terminal, and atomically enable V2 intake.
6. Switch the local worker acceptance list to exactly `direct_agent,metabot_local`.
7. Observe one full business day with no duplicate Task, final Message, authorization, or cache-cost anomaly.
8. Keep V1 Mission data and diagnostics read-only; remove no historical data in this release.

## Explicit Follow-on Gate

`fae_http` is not registered by this plan. Its separate plan must start only after the FAE owner supplies and approves a signed, task-bound internal API contract with idempotency, event sequencing, cancellation, attachment, result, health, timeout, and key-rotation semantics. Until then, `list_agents` must either omit FAE from Brain-callable snapshots or mark it unavailable with a stable reason; the existing external FAE entry remains independently usable.
