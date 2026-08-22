# Agent Brain Minimum Use Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `https://agent.orbbec.com.cn/` a real Agent 大脑 usage entry that can answer directly or visibly dispatch one authorized professional Agent through the existing local Execution Relay, while moving every management view under `/admin`.

**Architecture:** The cloud Platform owns authenticated Missions, encrypted messages, authorization, a durable single-Agent state machine, and the browser event stream. A dedicated local `agent-brain-bot` performs planning/direct answering and final synthesis; the existing outbound Execution Worker executes it and the seven professional MetaBots without opening local inbound ports. The first release supports text-only Missions and one professional child task; multi-Agent graphs, attachments, and external system writes remain disabled.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, PostgreSQL 17, psycopg 3, AES-256-GCM, React 18, TypeScript, Vite, SSE, MetaBot Core Chat, Claude Opus 5, pytest, Vitest, LaunchAgent, Docker Compose, Nginx.

## Global Constraints

- `/` is the authenticated Agent 大脑 usage page; it must never render the management Overview.
- Existing management pages move to `/admin`, `/admin/overview`, `/admin/agents`, `/admin/sessions`, `/admin/review`, `/admin/activity`, and `/admin/operations`.
- `/agents` is the authorized professional Agent usage directory, not the management fleet page.
- Every Mission is server-generated, owned by `internal_user_id`, encrypted at rest, and readable only by its owner or an audited management role.
- Agent use authorization is backend-enforced and default-deny; browser-supplied Agent IDs never create authority.
- The Agent 大脑 receives only capability cards for Agents the current user may use.
- One Mission may dispatch at most one professional Agent in this release.
- `agent-brain-bot` is a dedicated local MetaBot; do not reuse Feishu Default, Test Bot, Codex Assistant, or a professional Agent.
- The local worker initiates all cloud communication; do not expose ports 9100–9110 or create SSH/reverse tunnels from cloud to local.
- Execution failure is explicit; do not switch Agent, model, provider, or host as fallback.
- The UI displays only persisted safe events, not model chain-of-thought, system prompts, secrets, or raw debug payloads.
- Text input is limited to 32 KiB UTF-8; attachments and external side effects are rejected in this release.
- Existing FAE external service and `https://fae.orbbec.com.cn/` remain unchanged.

---

## File Structure

New Platform modules:

```text
backend/app/agent_brain/
├── __init__.py          package marker
├── models.py            public Mission and capability models
├── protocol.py          strict planner/synthesis response parsing
├── repository.py        encrypted Mission/task/event persistence
├── authorization.py     user/department/all-member Agent-use decisions
├── orchestrator.py      durable single-Agent Mission state machine
└── routes.py            authenticated catalog, Mission, cancel and SSE APIs

webui/src/
├── brainApi.ts          typed Mission API and SSE client
├── brainTypes.ts        browser-safe Mission/event/catalog types
├── pages/BrainPage.tsx  default input and Mission timeline
├── pages/MissionPage.tsx persisted Mission replay
└── components/mission/  task, progress, failure and delivery components
```

Companion MetaBot configuration lives in `/Users/neo/Developer/work/Orbbec-Agent-Team/bots/agent-brain/`; no model client is added to the cloud Platform.

---

### Task 1: Align the real MetaBot callback contract with the Execution Worker

**Files:**
- Modify: `backend/app/execution_relay/worker.py`
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/tests/test_execution_worker_runtime.py`
- Modify: `backend/tests/test_metabot_relay_client.py`

**Interfaces:**
- Consumes: the deployed MetaBot Core Chat callback `{runId, seq, type, createdAt, bridge, payload}` where `type` is `state|question|file|log|complete|error`.
- Produces: canonical `RelayEvent(run_id, seq, event_type, created_at, payload)` with `event_type=agent.<type>`; `complete` terminalizes as `completed`, `error` as `failed`.

- [ ] **Step 1: Write failing contract tests** using the exact camelCase Core Chat callback body, including `bridge`, and assert `accept_callback()` returns `ACCEPTED`, stores `agent.complete`, and marks the local run `completed`. Add the equivalent `error` case and assert `failed`. Keep tests proving unknown fields, invalid timestamps, sequence gaps, and mismatched `runId` are rejected.
- [ ] **Step 2: Run `cd backend && .venv/bin/pytest tests/test_execution_worker_runtime.py tests/test_metabot_relay_client.py -q`** and verify RED because `_StrictCallbackEvent` currently expects snake_case and rejects `bridge`.
- [ ] **Step 3: Implement the strict wire model and normalization**:

```python
class _CoreChatBridge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    botName: str = Field(min_length=1, max_length=128)
    executionChatId: str = Field(min_length=1, max_length=256)

class _StrictCallbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    runId: UUID
    seq: int = Field(gt=0)
    type: Literal["state", "question", "file", "log", "complete", "error"]
    createdAt: AwareDatetime
    bridge: _CoreChatBridge
    payload: dict[str, object]

def _relay_event(value: _StrictCallbackEvent) -> RelayEvent:
    return RelayEvent(
        run_id=value.runId,
        seq=value.seq,
        event_type=f"agent.{value.type}",
        created_at=value.createdAt,
        payload=value.payload,
    )
```

Update `_terminal_status()` so `agent.complete -> completed` and `agent.error -> failed`; never infer success from free text.
- [ ] **Step 4: Run the focused tests and `git diff --check`**, expecting all PASS.
- [ ] **Step 5: Commit** with `git commit -m "fix(relay): accept real MetaBot callback events"`.

### Task 2: Add Agent-use grants and encrypted Mission storage

**Files:**
- Create: `backend/control_migrations/028_agent_brain_mvp.sql`
- Modify: `backend/tests/test_control_plane_migration.py`
- Create: `backend/tests/test_agent_brain_migration.py`

**Interfaces:**
- Produces: `agent_use_grants`, `missions`, `mission_messages`, `mission_tasks`, `mission_runs`, and `mission_events` in `platform_control`.
- Produces: `has_agent_use_scope_v28(user_id, agent_id)` and least-privilege app grants.

- [ ] **Step 1: Write failing PostgreSQL migration tests** requiring UUID server IDs, FK ownership to `internal_users`, encrypted `bytea` content plus positive `encryption_key_version`, `(mission_id, seq)` uniqueness, one active child task per Mission, constrained statuses, no plaintext prompt/response columns, and no application-role `DELETE` grants.
- [ ] **Step 2: Add authorization tests** for active direct-user, department, ancestor-department, and all-member grants; also prove inactive users, revoked grants, missing grants, excluded Agents, and stale fabricated department IDs are denied.
- [ ] **Step 3: Run `cd backend && .venv/bin/pytest tests/test_control_plane_migration.py tests/test_agent_brain_migration.py -q`** and verify RED.
- [ ] **Step 4: Implement migration 028** with these state constraints:

```sql
-- missions.mode: brain | direct_agent
-- missions.status: planning | delegated | synthesizing | completed |
--                  partially_completed | failed | cancelled | interrupted
-- mission_tasks.status: queued | running | completed | failed | cancelled | interrupted
-- mission_runs.phase: planning | professional | synthesis | direct
-- mission_events.event_type: mission.started | brain.responding | plan.created |
--   task.dispatched | agent.accepted | agent.progress | agent.result |
--   task.reviewed | synthesis.started | mission.completed | mission.failed |
--   mission.cancelled | mission.interrupted
```

`agent_use_grants.target_kind` is exactly `user|department|all_members`; check constraints require only the matching target column. Use the existing active directory generation and department closure tables inside `has_agent_use_scope_v28`. Revoke all privileges from `PUBLIC`, grant the app role only required `SELECT/INSERT/UPDATE`, and expose audited maintenance functions for grant/revoke rather than direct application writes.
- [ ] **Step 5: Run the focused tests and verify GREEN**, then inspect grants with `information_schema.role_table_grants`.
- [ ] **Step 6: Commit** with `git commit -m "feat(brain): add grants and Mission schema"`.

### Task 3: Implement capability cards and Agent-use authorization

**Files:**
- Create: `backend/app/agent_brain/__init__.py`
- Create: `backend/app/agent_brain/models.py`
- Create: `backend/app/agent_brain/authorization.py`
- Create: `backend/app/agent_brain/capabilities.yaml`
- Create: `backend/tests/test_agent_use_authorization.py`
- Create: `backend/tests/test_agent_capabilities.py`

**Interfaces:**
- Produces: `AgentCapabilityCard` and `AgentUseAuthorization.permitted_agents(auth) -> tuple[AgentCapabilityCard, ...]`.
- Consumes: `has_agent_use_scope_v28`, the business catalog, and the exact seven callable Agent IDs.

- [ ] **Step 1: Write failing tests** that require only `hr-bot`, `fae-bot`, `marketing-prospecting-bot`, `marketing-inbound-bot`, `marketing-voice-bot`, `marketing-intelligence-bot`, and `marketing-gtm-bot`; explicitly reject `feishu-default`, `test-bot`, `codex-assistant`, `ai-fae-agent`, and `ai-admin-agent`.
- [ ] **Step 2: Add tests** proving capability cards contain `mission`, `capabilities`, `exclusions`, `required_inputs`, `example_tasks`, `max_duration_seconds`, and `capability_version`, while excluding Prompt, model, port, credentials, and adapter URL.
- [ ] **Step 3: Run `cd backend && .venv/bin/pytest tests/test_agent_use_authorization.py tests/test_agent_capabilities.py -q`** and verify RED.
- [ ] **Step 4: Implement immutable Pydantic models and YAML loading**. Validate duplicate IDs, unknown IDs, missing exclusions, duration outside `1..300`, and non-positive versions at startup. Evaluate every card through `has_agent_use_scope_v28`; do not cache the final user decision.
- [ ] **Step 5: Run focused tests and commit** with `git commit -m "feat(brain): add authorized capability catalog"`.

### Task 4: Implement encrypted Mission repository and strict Brain protocol

**Files:**
- Create: `backend/app/agent_brain/protocol.py`
- Create: `backend/app/agent_brain/repository.py`
- Create: `backend/tests/test_agent_brain_protocol.py`
- Create: `backend/tests/test_agent_brain_repository.py`

**Interfaces:**
- Produces: `BrainDecision(kind, answer, agent_id, objective, rationale_summary)` where `kind` is `direct|delegate`.
- Produces: transactional `create_mission`, `append_event`, `create_run`, `complete_run`, `mission_for_owner`, and `events_after` repository methods.

- [ ] **Step 1: Write parser tests** for a fenced or unfenced final JSON object with the exact schema below. Reject extra keys, multiple JSON objects, an unauthorized `agent_id`, missing delegate objective, direct decisions without an answer, outputs over 64 KiB, and content following the JSON object.

```json
{
  "kind": "delegate",
  "answer": null,
  "agent_id": "hr-bot",
  "objective": "根据给定 JD 定义候选人能力组合与搜索方向",
  "rationale_summary": "该任务需要招聘与人才定位能力"
}
```

- [ ] **Step 2: Write repository tests** for ciphertext-only storage, purpose-bound AES-GCM subjects, monotonic event sequence under concurrent writers, idempotent client request IDs, owner isolation, terminal immutability, and atomic run/event transitions.
- [ ] **Step 3: Run `cd backend && .venv/bin/pytest tests/test_agent_brain_protocol.py tests/test_agent_brain_repository.py -q`** and verify RED.
- [ ] **Step 4: Implement the strict parser** with `json.JSONDecoder().raw_decode`, Pydantic `extra="forbid"`, and an explicit `allowed_agent_ids` argument. Never repair malformed model output or silently convert it to a direct answer.
- [ ] **Step 5: Implement `MissionRepository`** using the existing `ContentCodec`; bind every ciphertext subject to its row ID and Mission ID. Use `SELECT ... FOR UPDATE` for state transitions and generate event sequence numbers inside the same transaction.
- [ ] **Step 6: Run focused tests and commit** with `git commit -m "feat(brain): persist encrypted Missions"`.

### Task 5: Implement the durable single-Agent orchestrator

**Files:**
- Create: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/execution_relay/repository.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_agent_brain_orchestrator.py`
- Modify: `backend/tests/test_execution_relay_repository.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `ExecutionRelayRepository.enqueue`, canonical relay events, capability cards, and Mission repository transitions.
- Produces: `MissionOrchestrator.advance_pending(limit: int) -> int` and a background loop with a single PostgreSQL advisory-lock leader.

- [ ] **Step 1: Write failing state-machine tests** for these exact paths:

```text
planning -> direct decision -> completed
planning -> delegate -> professional completed -> synthesis -> completed
planning -> malformed decision -> failed(protocol_invalid)
professional failed -> partially_completed with explicit failure
worker offline / timeout -> interrupted
cancel before lease -> cancelled
process restart between terminal upload and advancement -> resumes once
```

Assert every UI-visible transition appends a Mission event first and every relay `run_id` is linked to exactly one Mission phase.
- [ ] **Step 2: Add repository readers** `job_state(run_id)` and `events(run_id)` that decrypt only for the orchestrator, preserve sequence, and expose no worker or API route for arbitrary reads.
- [ ] **Step 3: Build prompts with bounded sections**: role instruction, output JSON schema, authorized capability cards, the user request, and—for synthesis only—the professional result. Escape section boundaries and cap the total prompt at 96 KiB. The planner may choose only `direct` or one authorized Agent.
- [ ] **Step 4: Implement advancement with compare-and-set transitions**. One loop scans non-terminal Missions, claims rows with `FOR UPDATE SKIP LOCKED`, advances at most 50 per pass, sleeps one second when idle, and survives per-Mission failures. Planning and synthesis run on `agent-brain-bot`; professional execution runs on the chosen real Agent.
- [ ] **Step 5: Wire startup and shutdown in `create_app()`** only when identity production mode, relay, and Agent Brain are all enabled. Fail startup if Mission schema or capability configuration is unavailable; never expose a decorative Brain page backed by a disabled service.
- [ ] **Step 6: Run `cd backend && .venv/bin/pytest tests/test_agent_brain_orchestrator.py tests/test_execution_relay_repository.py tests/test_main.py -q`** and commit with `git commit -m "feat(brain): orchestrate one real Agent"`.

### Task 6: Add authenticated Agent catalog, Mission, cancellation and SSE APIs

**Files:**
- Create: `backend/app/agent_brain/routes.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_agent_brain_api.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Produces:

```text
GET  /api/v1/catalog/agents
POST /api/v1/brain/missions
GET  /api/v1/brain/missions/{mission_id}
GET  /api/v1/brain/missions/{mission_id}/events?after={seq}
POST /api/v1/brain/missions/{mission_id}/cancel
POST /api/v1/agents/{agent_id}/missions
```

- [ ] **Step 1: Write failing API tests** for valid DingTalk member, owner, unauthenticated, CSRF failure, wrong Origin, missing Agent grant, another user's Mission, invalid UUID, oversized text, unsupported attachment fields, duplicate idempotency key, cancellation, `after` replay, heartbeat frames, and `Cache-Control: no-store`.
- [ ] **Step 2: Add authorization matrix tests** proving authenticated members may use only self-owned Brain routes and granted Agent routes; management status alone does not bypass Agent-use authorization when starting a user Mission. Cross-user management reads remain under `/admin` APIs and audited separately.
- [ ] **Step 3: Run `cd backend && .venv/bin/pytest tests/test_agent_brain_api.py tests/test_dingtalk_auth_api.py -q`** and verify RED.
- [ ] **Step 4: Implement routes** with server UUIDs, `Idempotency-Key` UUID validation, 32 KiB UTF-8 limit, Pydantic `extra="forbid"`, status codes `201/200/401/403/409/413/422/503`, and no internal error text. SSE emits `id: <seq>`, `event: mission`, one-line safe JSON, 15-second comments, and closes on terminal state.
- [ ] **Step 5: Add the exact route templates to backend authorization**; do not add broad `/api/v1/*` or path-prefix allowances. Keep the Execution Worker namespace on its independent device-auth boundary.
- [ ] **Step 6: Run focused tests and commit** with `git commit -m "feat(brain): expose owned Mission APIs"`.

### Task 7: Make usage the default UI and isolate management under `/admin`

**Files:**
- Create: `webui/src/brainTypes.ts`
- Create: `webui/src/brainApi.ts`
- Create: `webui/src/pages/BrainPage.tsx`
- Create: `webui/src/pages/BrainPage.test.tsx`
- Create: `webui/src/pages/MissionPage.tsx`
- Create: `webui/src/pages/MissionPage.test.tsx`
- Create: `webui/src/components/mission/MissionTimeline.tsx`
- Create: `webui/src/components/mission/MissionTimeline.test.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/cloudMode.test.tsx`
- Create: `webui/src/router.brain.test.ts`

**Interfaces:**
- Produces: `/`, `/missions/{id}`, `/agents`, `/agents/{id}`, and `/admin/...` route families.
- Consumes: Task 6 JSON/SSE contract only; no direct connection to MetaBot.

- [ ] **Step 1: Write router tests** requiring `parseRoute("/").name === "brain"`, all old management pages under `/admin`, and explicit permanent client redirects from legacy `/review`, `/activity`, and management deep links. `/agents` must resolve to the usage directory.
- [ ] **Step 2: Write page tests** requiring an immediately enabled input after account load, submit-once idempotency, visible persisted event cards, Markdown rendering for Agent results with raw HTML disabled, reconnection from the last SSE ID, explicit offline/failure states, stop action, and a link to the persisted Mission URL.
- [ ] **Step 3: Run `cd webui && npm test -- --run`** and verify RED.
- [ ] **Step 4: Implement `brainApi.ts`** with same-origin credentials, CSRF header for writes, a generated UUID idempotency key retained across retries, `EventSource`-equivalent fetch streaming that sends `after`, and an `AbortController` on unmount. Do not treat a disconnected stream as Mission failure; refetch the Mission snapshot before reconnecting.
- [ ] **Step 5: Implement the clean usage shell**. The first viewport contains only “Agent 大脑”, the main textarea, submit control, three real examples, recent Missions, and a quiet professional-Agent link. Render persisted cards for analysis, dispatch, progress, professional result, review, final delivery, and failure. Do not show management metrics on `/`.
- [ ] **Step 6: Move management navigation** to an owner/admin-only “管理中心” menu targeting `/admin`; members see “Agent 大脑 / 专业 Agent / 历史任务 / 企业账号”. Brand click always returns to `/`.
- [ ] **Step 7: Run `npm test -- --run && npm run build && git diff --check`** and commit with `git commit -m "feat(web): make Agent Brain the default entry"`.

### Task 8: Add and deploy the dedicated local Agent Brain MetaBot

**Files (Orbbec-Agent-Team repository):**
- Create: `bots/agent-brain/CLAUDE.md`
- Create: `bots/agent-brain/bots.json.template`
- Modify: `deploy/metabot.runtime-contract.json`
- Modify: `scripts/deploy_metabot_api_loopback.sh`
- Modify: `tests/test_metabot_runtime_contract.py`
- Modify: `tests/test_deploy_metabot_api_loopback.py`

**Files (Platform repository):**
- Modify: `backend/app/execution_relay/metabot_client.py`
- Modify: `backend/tests/test_metabot_relay_client.py`
- Modify: `deploy/local-execution-worker/install.sh`
- Modify: `backend/tests/test_execution_worker_deployment.py`

**Interfaces:**
- Produces: loopback-only `agent-brain-bot` on fixed port `9110`, Claude Opus 5, its own state/config/log directories, and worker allowlist membership.

- [ ] **Step 1: Write failing contract tests** requiring exactly one `agent-brain-bot`, port 9110, loopback API host, dedicated workdir/state/config/log paths, Opus 5 compatibility profile, and exclusion from business usage/management counts.
- [ ] **Step 2: Write Prompt policy tests** requiring strict JSON for planning, normal Markdown only for direct/synthesis responses, no direct professional Agent/network calls, no secrets, no hidden reasoning request, and explicit failure on malformed input.
- [ ] **Step 3: Extend the worker's exact approved runtime map** from seven professional IDs to those seven plus `agent-brain-bot`; retain rejection of Test, Default, Codex Assistant, AI ADMIN, and external FAE.
- [ ] **Step 4: Implement the dedicated MetaBot files and deploy-script transaction** using the existing staged release, backup, loopback probe, rollback, and no-Keychain runtime rules. Do not alter the Feishu app mapping because Agent Brain is Platform-only.
- [ ] **Step 5: Run both repositories' focused tests**, then deploy locally and verify `127.0.0.1:9110`, authenticated `/api/health`, one synthetic planner response, and no wildcard listener.
- [ ] **Step 6: Commit Orbbec-Agent-Team** with `git commit -m "feat(metabot): add Agent Brain runtime"` and Platform with `git commit -m "feat(relay): dispatch Agent Brain runs"`.

### Task 9: Production rollout, real acceptance and rollback

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `deploy/cloud/accept.sh`
- Modify: `deploy/cloud/agent-domain.nginx.conf`
- Modify: `docs/runbooks/cloud-platform.md`
- Create: `backend/tests/test_agent_brain_deployment.py`

**Interfaces:**
- Produces: an opt-in `PLATFORM_AGENT_BRAIN_ENABLED=1` release and a tested rollback that restores the management root without touching FAE or deleting Mission data.

- [ ] **Step 1: Write failing deployment policy tests** requiring mode-0600 file references for control DSN/content keyring, relay and Brain feature flags, Nginx SSE buffering off, `proxy_read_timeout 330s`, no public port except 80/443, no Basic Auth after DingTalk production cutover, and FAE container/config invariance checks.
- [ ] **Step 2: Add an acceptance script gate** that uses a real DingTalk test member and pre-created use grants to verify: `/` renders Agent 大脑; `/admin` is forbidden to member; owner can open `/admin`; HR request creates a real `hr-bot` ChildRun; timeline events equal stored events; final Markdown is rendered; unauthorized Agent fails 403; worker stop yields explicit interruption; restart does not duplicate a ChildRun.
- [ ] **Step 3: Run the full local gate**:

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../webui && npm test -- --run && npm run build
cd .. && bash -n deploy/cloud/*.sh deploy/local-execution-worker/*.sh
git diff --check
```

Expected: all tests PASS, production build succeeds, scripts parse, and no diff errors.
- [ ] **Step 4: Deploy in dependency order**: Platform migrations with Brain disabled; local `agent-brain-bot`; local worker allowlist/key registration; cloud Platform image with Brain disabled; real relay canary; enable Brain; switch `/` UI. Stop immediately on any failed gate and keep the existing management entry active.
- [ ] **Step 5: Run public and local acceptance** from fresh processes, record release SHAs, container IDs/start times, worker key ID, Mission/run IDs, event sequences, listener table, FAE probes, and rollback paths without recording prompts, answers, cookies, DingTalk IDs, or secrets.
- [ ] **Step 6: Exercise rollback** by disabling the Brain feature and restoring the previous UI route; verify `/admin`, existing Sessions/Review/Operations, FAE domain, legacy IP access, and local MetaBots. Do not drop migration 028 or delete Missions.
- [ ] **Step 7: Commit deployment assets** with `git commit -m "feat(cloud): release Agent Brain use entry"`, then merge/push only after the real acceptance record is complete.

---

## Self-Review Results

- **Spec coverage:** The plan covers the approved minimum slice: usage root, management isolation, dedicated Brain, backend grants, one real professional dispatch, visible persisted process, ownership, explicit failure, local MetaBot topology, rollout, and rollback. Multi-Agent, attachments, follow-up plan revisions, and DingTalk business actions remain explicitly outside this release.
- **Placeholder scan:** Every implementation step names its concrete behavior, command, expected result, and owned files; no deferred fill-in markers remain.
- **Type consistency:** `mission_id`, `run_id`, `agent_id`, event `seq`, Brain decision fields, route names, and status values are defined once and used consistently across schema, service, API, UI, and acceptance tasks.
- **Critical precondition:** Task 1 must complete before any live Mission testing because the current worker wire model does not match the real MetaBot Core Chat callback envelope.
