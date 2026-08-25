# Professional Agent User Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn HR and Marketing direct-Agent conversations into a clean employee-facing chat product while keeping execution diagnostics in Management Center and enforcing a typed public-answer boundary across MetaBot and Platform.

**Architecture:** MetaBot Core Chat gains a result-mode-aware terminal contract that separates protected execution output from `publicAnswerMarkdown`. Platform carries the result mode through the durable relay, accepts public content only on public-delivery phases, and exposes a sanitized member conversation projection. The React workspace consumes Catalog metadata, renders domain-local navigation and business progress, and adds owner-scoped history and feedback actions.

**Tech Stack:** TypeScript, Node.js, Vitest, Python 3.12, FastAPI, Pydantic, PostgreSQL, React 19, Vite, pytest, Nginx/Docker production release scripts.

## Global Constraints

- Member pages never render Mission/Run/Task/Trace/Evidence identifiers, raw transport status, raw exception text, or links labelled `诊断详情`.
- A direct-Agent successful Turn renders the answer and no raw execution-event card.
- Brain conversations may render only the business-level `查看协作过程` projection.
- HR pages never render Marketing switching controls; Marketing pages render exactly Prospecting, Inbound, Voice, Intelligence, and GTM.
- Browser code never uses an Agent ID as a visible label fallback.
- Only `result.publicAnswerMarkdown` may become a public assistant message for a public-delivery relay run; `responseText` is never a fallback.
- The public answer is valid UTF-8, non-empty, at most the existing visible-result byte limit, and free of explicitly defined bridge-protocol markers.
- Feedback comments are optional and at most 1,000 UTF-8 bytes; stored comments are encrypted with the existing `ContentCodec`.
- Conversation titles are trimmed and contain 1–160 characters.
- Existing ownership, CSRF, idempotency, authorization, retention, encryption, and no-store boundaries remain enforced on the backend.
- Strict Platform public-answer consumption is enabled only after the local MetaBot worker reports Core Chat result contract v2.

---

## File map

### MetaBot repository: `/Users/neo/Developer/work/metabot-dev`

- `src/api/routes/core-chat-routes.ts`: result-mode request parsing, public-output instruction, and v2 terminal callback.
- `src/bridge/message-bridge.ts`: typed `ApiTaskResult.publicAnswerMarkdown` producer input.
- `tests/core-chat-routes.test.ts`: callback contract and final-answer-only regression tests.
- `tests/message-bridge.test.ts`: terminal SDK-result extraction tests.

### Agent Platform repository

- `backend/app/execution_relay/models.py`: durable `result_mode` relay field.
- `backend/app/execution_relay/metabot_client.py`: forward result mode to Core Chat.
- `backend/app/agent_brain/orchestrator.py`: choose public/internal phases and fail closed on malformed public completions.
- `backend/app/agent_brain/conversation_projection.py`: member-safe event payload projection.
- `backend/app/agent_brain/conversation_routes.py`: member DTOs, rename/archive/restore, feedback body, and status-scoped history.
- `backend/app/agent_brain/conversation_repository.py`: owner-scoped conversation lifecycle and encrypted feedback details.
- `backend/app/agent_brain/conversation_models.py`: structured feedback records.
- `backend/app/agent_catalog/models.py`, `backend/app/agent_catalog/catalog.yaml`: public persona subtitles and canonical task starters.
- `backend/control_migrations/044_conversation_feedback_detail.sql`: structured reason and encrypted comment fields.
- `webui/src/components/conversation/PublicProgress.tsx`: direct/Brain member progress projection.
- `webui/src/components/conversation/ConversationMessages.tsx`: structured feedback UI.
- `webui/src/components/conversation/ConversationSidebar.tsx`: concise rows and history actions.
- `webui/src/pages/AgentUsePage.tsx`: domain-local switching and task starters.
- `webui/src/pages/ConversationPage.tsx`: Agent header, continuous chat, member-safe progress.
- `webui/src/conversationApi.ts`, `webui/src/conversationTypes.ts`: strict member contracts and lifecycle APIs.
- `webui/src/styles.css`: responsive workspace, interactive cards, feedback and history controls.
- Existing adjacent `*.test.tsx`, `*.test.ts`, and `backend/tests/test_*.py`: TDD coverage.

---

### Task 1: MetaBot Core Chat public-result contract v2

**Files:**
- Modify: `/Users/neo/Developer/work/metabot-dev/src/api/routes/core-chat-routes.ts`
- Modify: `/Users/neo/Developer/work/metabot-dev/src/bridge/message-bridge.ts`
- Test: `/Users/neo/Developer/work/metabot-dev/tests/core-chat-routes.test.ts`
- Test: `/Users/neo/Developer/work/metabot-dev/tests/message-bridge.test.ts`

**Interfaces:**
- Produces: `CoreChatResultMode = "internal" | "public_markdown"`.
- Produces: `ApiTaskResult.publicAnswerMarkdown?: string`.
- Produces: terminal result `{ contractVersion: "core_chat_result_v2", outputText, publicAnswerMarkdown? }`.
- Produces: capability declaration `contracts.coreChatResult = "core_chat_result_v2"`.

- [ ] **Step 1: Write failing request and callback tests**

```ts
it("emits a typed public answer only for public_markdown runs", async () => {
  const request = validRun({ resultMode: "public_markdown" });
  bridge.executeApiTask.mockResolvedValue({ success: true, responseText: "公开回答" });
  await runAndDrain(request);
  expect(lastCallback().payload.result).toMatchObject({
    contractVersion: "core_chat_result_v2",
    outputText: "公开回答",
    publicAnswerMarkdown: "公开回答",
  });
});

it("does not mark internal run output as a public answer", async () => {
  const request = validRun({ resultMode: "internal" });
  bridge.executeApiTask.mockResolvedValue({ success: true, responseText: "{\"kind\":\"delegate\"}" });
  await runAndDrain(request);
  expect(lastCallback().payload.result.publicAnswerMarkdown).toBeUndefined();
});
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- --run tests/core-chat-routes.test.ts tests/message-bridge.test.ts`

Expected: FAIL because `resultMode`, `contractVersion`, `outputText`, and `publicAnswerMarkdown` are absent.

- [ ] **Step 3: Add the typed producer and public-output instruction**

```ts
type CoreChatResultMode = "internal" | "public_markdown";

export interface ApiTaskResult {
  success: boolean;
  responseText: string;
  publicAnswerMarkdown?: string;
  sessionId?: string;
  costUsd?: number;
  durationMs?: number;
  error?: string;
}

function publicTaskPrompt(prompt: string): string {
  return `${prompt}\n\n<platform-public-answer-contract>\nReturn only the final user-facing Markdown answer. Do not narrate tool selection, skill selection, planning, hidden reasoning, or internal protocol decisions.\n</platform-public-answer-contract>`;
}
```

Parse `resultMode` with strict values, use `publicTaskPrompt()` only for `public_markdown`, and emit:

```ts
function apiTaskResultPayload(result: ApiTaskResult, resultMode: CoreChatResultMode): Record<string, unknown> {
  const selectedPublicAnswer = resultMode === "public_markdown" && result.success
    ? publicAnswer(result.publicAnswerMarkdown ?? result.responseText)
    : undefined;
  return {
    contractVersion: "core_chat_result_v2",
    success: result.success,
    outputText: result.responseText,
    ...(selectedPublicAnswer ? { publicAnswerMarkdown: selectedPublicAnswer } : {}),
    sessionId: result.sessionId,
    costUsd: result.costUsd,
    durationMs: result.durationMs,
    error: result.error,
  };
}
```

- [ ] **Step 4: Add known protocol-marker rejection at the producer boundary**

```ts
const FORBIDDEN_PUBLIC_PREFIXES = Object.freeze([
  "Using jd-registry?",
  "Tool selection:",
  "Internal plan:",
]);

function publicAnswer(text: string): string | undefined {
  const value = text.trim();
  if (!value || FORBIDDEN_PUBLIC_PREFIXES.some((prefix) => value.startsWith(prefix))) return undefined;
  return value;
}
```

A successful `public_markdown` run with no valid public answer emits `type: "error"` and stable error `public_answer_contract_invalid`; it never copies the invalid value into a public field.

Add this stable capability to `buildCoreChatCapabilities()` and its existing capability test:

```ts
contracts: {
  coreChatResult: "core_chat_result_v2",
}
```

- [ ] **Step 5: Run MetaBot tests, typecheck, and commit**

Run: `npm test -- --run tests/core-chat-routes.test.ts tests/message-bridge.test.ts && npm run build:bridge`

Expected: focused tests PASS and TypeScript build exits 0.

```bash
git add src/api/routes/core-chat-routes.ts src/bridge/message-bridge.ts tests/core-chat-routes.test.ts tests/message-bridge.test.ts
git commit -m "feat: add typed public core chat result"
```

---

### Task 2: Carry result mode through Platform relay and consume public answers strictly

**Files:**
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/app/execution_relay/metabot_client.py`
- Modify: `backend/app/execution_relay/worker.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/agent_brain/adapters/metabot_local.py`
- Test: `backend/tests/test_metabot_relay_client.py`
- Test: `backend/tests/test_execution_worker_runtime.py`
- Test: `backend/tests/test_agent_brain_orchestrator.py`

**Interfaces:**
- Consumes: MetaBot `resultMode` and `core_chat_result_v2`.
- Produces: `RelayResultMode = Literal["internal", "public_markdown"]` and `RelayJobPayload.result_mode`.
- Produces: `_terminal_text(events, status, *, require_public)`.

- [ ] **Step 1: Write failing relay and orchestration tests**

```python
def test_metabot_client_forwards_public_result_mode():
    payload = _payload().model_copy(update={"result_mode": "public_markdown"})
    client.start_run(payload, "http://127.0.0.1:9001/callbacks/run/token")
    assert posted_json()["resultMode"] == "public_markdown"

def test_direct_agent_refuses_legacy_response_text(brain_database, orchestrator):
    mission, run = completed_direct_run(
        {"result": {"success": True, "responseText": "Using jd-registry? No — internal"}}
    )
    assert orchestrator.advance(mission.mission_id) is True
    assert latest_mission(mission.mission_id).status == "failed"
    assert latest_terminal_reason(mission.mission_id) == "public_answer_contract_invalid"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q backend/tests/test_metabot_relay_client.py backend/tests/test_execution_worker_runtime.py backend/tests/test_agent_brain_orchestrator.py`

Expected: FAIL because the relay model and strict terminal reader do not exist.

- [ ] **Step 3: Add result mode and phase mapping**

```python
RelayResultMode = Literal["internal", "public_markdown"]

class RelayJobPayload(BaseModel):
    run_id: UUID
    conversation_id: UUID
    trigger_message_id: UUID
    agent_id: str
    prompt: str
    max_turns: int = Field(ge=1, le=24)
    job_kind: RelayJobKind = "legacy_brain"
    result_mode: RelayResultMode = "internal"
    requester_subject: RequesterSubject | None = Field(default=None, repr=False)
```

Use `public_markdown` for legacy phases `direct` and `synthesis`; use `internal` for `planning`, `professional`, and durable-loop `metabot_local` tasks. Forward it as `resultMode` in `MetaBotClient.start_run()`.

- [ ] **Step 4: Enforce terminal public-answer shape**

```python
class PublicAnswerContractError(ExecutionRelayError):
    pass

_PUBLIC_PROTOCOL_PREFIXES = (
    "Using jd-registry?",
    "Tool selection:",
    "Internal plan:",
)

def _validated_terminal_text(text: str, *, public: bool) -> str:
    selected = text.strip()
    if not selected or len(selected.encode("utf-8")) > MAX_RELAY_RESULT_BYTES:
        raise PublicAnswerContractError() if public else ExecutionRelayError(
            "execution relay unavailable"
        )
    if public and selected.startswith(_PUBLIC_PROTOCOL_PREFIXES):
        raise PublicAnswerContractError()
    return selected

def _terminal_text(
    events: tuple[RelayEvent, ...], status: str, *, require_public: bool = False
) -> str:
    expected = "agent.complete" if status == "completed" else "agent.error"
    if not events or events[-1].event_type != expected:
        raise ExecutionRelayError("execution relay unavailable")
    result = events[-1].payload.get("result")
    if not isinstance(result, dict):
        raise ExecutionRelayError("execution relay unavailable")
    key = "publicAnswerMarkdown" if require_public else "outputText"
    if result.get("contractVersion") != "core_chat_result_v2":
        raise PublicAnswerContractError() if require_public else ExecutionRelayError("execution relay unavailable")
    text = result.get(key)
    if not isinstance(text, str) or not text.strip():
        raise PublicAnswerContractError() if require_public else ExecutionRelayError("execution relay unavailable")
    return _validated_terminal_text(text, public=require_public)
```

Catch `PublicAnswerContractError` in direct/synthesis advancement and terminalize with the safe member message `专业 Agent 暂未生成可交付的回答，请重试本轮。` and reason code `public_answer_contract_invalid`.

- [ ] **Step 5: Run relay/orchestrator tests and commit**

Run: `pytest -q backend/tests/test_metabot_relay_client.py backend/tests/test_execution_worker_runtime.py backend/tests/test_agent_brain_orchestrator.py backend/tests/test_agent_brain_metabot_adapter.py`

Expected: all selected tests PASS.

```bash
git add backend/app/execution_relay backend/app/agent_brain/orchestrator.py backend/app/agent_brain/adapters/metabot_local.py backend/tests/test_metabot_relay_client.py backend/tests/test_execution_worker_runtime.py backend/tests/test_agent_brain_orchestrator.py
git commit -m "feat: enforce public answer relay contract"
```

---

### Task 3: Sanitize the member conversation API projection

**Files:**
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/main.py`
- Modify: `webui/src/conversationTypes.ts`
- Modify: `webui/src/conversationApi.ts`
- Test: `backend/tests/test_agent_brain_conversation_api.py`
- Test: `backend/tests/test_agent_brain_conversation_projection.py`
- Test: `webui/src/conversationApi.test.ts`

**Interfaces:**
- Produces member DTOs without `mission_id` on messages, turns, events, cancel results, or feedback.
- Produces public event payloads with `agent_name`, never `agent_id` or `selected_agent_id`.

- [ ] **Step 1: Write failing API-projection tests**

```python
def test_member_conversation_projection_omits_diagnostics(client, owned_conversation):
    response = client.get(f"/api/v1/conversations/{owned_conversation}/messages")
    assert response.status_code == 200
    assert all("mission_id" not in item for item in response.json()["items"])

def test_member_event_uses_catalog_label_not_agent_id(client, brain_event):
    event = read_first_sse_event(client, brain_event.conversation_id)
    assert event["payload"]["agent_name"] == "HR Agent"
    assert "agent_id" not in event["payload"]
    assert "mission_id" not in event
```

- [ ] **Step 2: Run API and Web parser tests and verify RED**

Run: `pytest -q backend/tests/test_agent_brain_conversation_api.py backend/tests/test_agent_brain_conversation_projection.py && npm --prefix webui test -- --run src/conversationApi.test.ts`

Expected: FAIL because diagnostic identifiers remain in member DTOs.

- [ ] **Step 3: Add one member-safe serializer boundary**

Pass the canonical Catalog repository to `build_conversation_router()` and map internal Agent IDs to display names server-side. Update `_message_payload`, `_turn_payload`, `_event_payload`, `_feedback_payload`, and cancel response to omit Mission identifiers. Keep internal records and Management Center routes unchanged.

```python
def _member_event_payload(record, display_name_for_agent) -> dict[str, object]:
    payload = ConversationProjection.public_payload(record.event_type, record.payload)
    agent_id = payload.pop("agent_id", None) or payload.pop("selected_agent_id", None)
    if isinstance(agent_id, str):
        payload["agent_name"] = display_name_for_agent(agent_id) or "专业 Agent"
    return {
        "event_id": str(record.event_id),
        "conversation_id": str(record.conversation_id),
        "seq": record.seq,
        "turn_id": str(record.turn_id) if record.turn_id else None,
        "event_type": record.event_type,
        "payload": payload,
        "created_at": record.created_at.isoformat(),
    }
```

- [ ] **Step 4: Tighten TypeScript exact-key parsing**

Remove `mission_id` from `ConversationMessage`, `ConversationTurn`, `ConversationEvent`, `ConversationCancelResult`, and `ConversationFeedback`, and from each exact-key set. Add a negative test proving extra diagnostic fields are rejected.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest -q backend/tests/test_agent_brain_conversation_api.py backend/tests/test_agent_brain_conversation_projection.py && npm --prefix webui test -- --run src/conversationApi.test.ts`

Expected: all selected tests PASS.

```bash
git add backend/app/agent_brain/conversation_projection.py backend/app/agent_brain/conversation_routes.py backend/app/main.py webui/src/conversationTypes.ts webui/src/conversationApi.ts backend/tests/test_agent_brain_conversation_api.py backend/tests/test_agent_brain_conversation_projection.py webui/src/conversationApi.test.ts
git commit -m "fix: hide diagnostics from member conversations"
```

---

### Task 4: Add canonical persona subtitles and domain-aware Agent navigation

**Files:**
- Modify: `backend/app/agent_catalog/models.py`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Modify: `backend/tests/test_agent_catalog.py`
- Modify: `webui/src/brainTypes.ts`
- Modify: `webui/src/brainApi.ts`
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`

**Interfaces:**
- Produces: `AgentCatalogCard.persona_subtitle: str | None`.
- Consumes: Catalog `domain_group`, `display_name`, and `example_tasks` only; no hardcoded visible Agent IDs.

- [ ] **Step 1: Write failing Catalog and HR/Marketing navigation tests**

```tsx
it("does not render Marketing controls on the HR workspace", async () => {
  await renderAgent("hr-bot", [hrCard, ...fiveMarketingCards]);
  expect(container.querySelector("nav[aria-label='Marketing Agent 切换']")).toBeNull();
  expect(container.textContent).toContain("Hannah · 技术人才搜寻与招聘协作");
});

it("renders exactly five Marketing choices on Marketing pages", async () => {
  await renderAgent("marketing-gtm-bot", [hrCard, ...fiveMarketingCards]);
  expect(container.querySelectorAll("nav[aria-label='Marketing Agent 切换'] a")).toHaveLength(5);
});
```

- [ ] **Step 2: Run Catalog and AgentUse tests and verify RED**

Run: `pytest -q backend/tests/test_agent_catalog.py && npm --prefix webui test -- --run src/pages/AgentUsePage.test.tsx`

Expected: FAIL because subtitle is absent and Marketing controls are unconditional.

- [ ] **Step 3: Add Catalog subtitles and strict parsing**

```python
persona_subtitle: str | None = Field(default=None, min_length=1, max_length=160)
```

Set HR to `Hannah · 技术人才搜寻与招聘协作`; add concise domain personas for the five Marketing cards. Increment each changed card's `capability_version`.

- [ ] **Step 4: Scope the switcher and add task starters**

```tsx
const siblings = catalog.filter((item) =>
  item.domain_group === card.domain_group && item.interaction_modes.includes("direct_chat"));
const showMarketingSwitcher = card.domain_group === "Marketing" && siblings.length === 5;
```

Render up to four `example_tasks` as buttons that call `setText(example)` and do not submit. Render `persona_subtitle` only when present.

- [ ] **Step 5: Run tests and commit**

Run: `pytest -q backend/tests/test_agent_catalog.py backend/tests/test_agent_brain_api.py && npm --prefix webui test -- --run src/pages/AgentUsePage.test.tsx src/brainApi.test.ts`

Expected: all selected tests PASS.

```bash
git add backend/app/agent_catalog backend/tests/test_agent_catalog.py webui/src/brainTypes.ts webui/src/brainApi.ts webui/src/pages/AgentUsePage.tsx webui/src/pages/AgentUsePage.test.tsx
git commit -m "feat: improve professional agent navigation"
```

---

### Task 5: Replace member diagnostics with public progress and clean conversation headers

**Files:**
- Create: `webui/src/components/conversation/PublicProgress.tsx`
- Create: `webui/src/components/conversation/PublicProgress.test.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Retain for Management Center only: `webui/src/components/conversation/ExecutionCard.tsx`

**Interfaces:**
- Produces: `PublicProgress(props: PublicProgressProps): JSX.Element | null`.
- Consumes: public `agent_name`, `objective_summary`, `public_reason`, `status`, and `duration_ms` only.

- [ ] **Step 1: Write failing direct and Brain presentation tests**

```tsx
it("shows no diagnostics after a successful direct Agent turn", async () => {
  await renderDirectConversation();
  expect(container.textContent).not.toContain("执行过程");
  expect(container.textContent).not.toContain("诊断详情");
  expect(container.textContent).not.toContain("accepted");
  expect(container.textContent).not.toContain("completed");
});

it("shows only business collaboration for Brain mode", async () => {
  await renderBrainConversation([delegatedToHr, hrCompleted, brainResumed]);
  expect(container.textContent).toContain("查看协作过程");
  expect(container.textContent).toContain("HR Agent 已完成");
  expect(container.querySelector("a[href*='/missions/']")).toBeNull();
});
```

- [ ] **Step 2: Run ConversationPage/PublicProgress tests and verify RED**

Run: `npm --prefix webui test -- --run src/pages/ConversationPage.test.tsx src/components/conversation/PublicProgress.test.tsx`

Expected: FAIL because `ExecutionCard` still renders raw records and links.

- [ ] **Step 3: Implement the two projections**

```tsx
interface PublicProgressProps {
  mode: ConversationMode;
  events: ConversationEvent[];
  active: boolean;
  assistantLabel: string;
  stopButton: ReactNode;
}

interface PublicBrainStep {
  key: string;
  label: string;
}

function publicBrainSteps(events: ConversationEvent[]): PublicBrainStep[] {
  return events.flatMap((event) => {
    const agent = typeof event.payload.agent_name === "string"
      ? event.payload.agent_name : "专业 Agent";
    const label = {
      "brain.started": "Agent 大脑正在分析需求",
      "agent.task_dispatched": `已交给 ${agent}`,
      "agent.task_progress": `${agent} 正在处理`,
      "agent.task_completed": `${agent} 已完成`,
      "brain.resumed": "Agent 大脑正在整合结果",
    }[event.event_type];
    return label ? [{ key: event.event_id, label }] : [];
  });
}

export function PublicProgress(props: PublicProgressProps) {
  if (props.mode === "direct_agent") {
    return props.active ? <section className="conversation-running" role="status">
      <span>{props.assistantLabel} 正在处理…</span>{props.stopButton}
    </section> : null;
  }
  return <details className="public-collaboration">
    <summary>查看协作过程</summary>
    <ol>{publicBrainSteps(props.events).map((step) => <li key={step.key}>{step.label}</li>)}</ol>
  </details>;
}
```

Only the Brain branch renders the details disclosure. Keep `UserInputRequest` outside diagnostics and render it when the latest public event requests input.

- [ ] **Step 4: Replace the duplicate prompt header**

Render:

```tsx
<header className="conversation-header">
  <h1>{assistantLabel}</h1>
  {personaSubtitle && <p>{personaSubtitle}</p>}
</header>
```

The prompt remains only in `ConversationMessages`. Remove all visible fallback use of `direct_agent_id`.
`AgentUsePage` passes `assistantLabel={card.display_name}` and
`personaSubtitle={card.persona_subtitle}` into `ConversationPage`; the Brain workspace
passes `assistantLabel="Agent 大脑"` and no persona subtitle. `ConversationPage` never
derives either visible value from `direct_agent_id`.

- [ ] **Step 5: Run tests and commit**

Run: `npm --prefix webui test -- --run src/pages/ConversationPage.test.tsx src/components/conversation/PublicProgress.test.tsx`

Expected: all selected tests PASS.

```bash
git add webui/src/components/conversation/PublicProgress.tsx webui/src/components/conversation/PublicProgress.test.tsx webui/src/pages/ConversationPage.tsx webui/src/pages/ConversationPage.test.tsx
git commit -m "feat: add member-safe agent progress"
```

---

### Task 6: Add owner-scoped rename, archive, restore, and status-filtered history

**Files:**
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Test: `backend/tests/test_agent_brain_conversation_repository.py`
- Test: `backend/tests/test_agent_brain_conversation_api.py`
- Modify: `webui/src/conversationApi.ts`
- Modify: `webui/src/components/conversation/ConversationSidebar.tsx`
- Modify: `webui/src/components/conversation/ConversationSidebar.test.tsx`

**Interfaces:**
- Produces: `PATCH /api/v1/conversations/{id}` with `{ title }`.
- Produces: `POST /api/v1/conversations/{id}/restore`.
- Extends: `GET /api/v1/conversations?...&status=active|archived`, cursor-bound to status.

- [ ] **Step 1: Write failing ownership and lifecycle tests**

```python
def test_owner_can_rename_archive_and_restore(repository, owned_conversation):
    renamed = repository.rename(OWNER, owned_conversation, "  新标题  ")
    assert renamed.title == "新标题"
    assert repository.archive(OWNER, owned_conversation).status == "archived"
    assert repository.restore(OWNER, owned_conversation).status == "active"

def test_other_user_cannot_mutate_conversation(repository, owned_conversation):
    with pytest.raises(ConversationRepositoryNotFound):
        repository.rename(OTHER, owned_conversation, "越权")
```

- [ ] **Step 2: Run repository/API/sidebar tests and verify RED**

Run: `pytest -q backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py && npm --prefix webui test -- --run src/components/conversation/ConversationSidebar.test.tsx src/conversationApi.test.ts`

Expected: FAIL because rename, restore, and status-scoped listing are absent.

- [ ] **Step 3: Implement backend lifecycle methods and cursor binding**

```python
class ConversationRenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def normalized_title(cls, value: str) -> str:
        selected = value.strip()
        if not selected:
            raise ValueError("Conversation title required")
        return selected
```

Repository SQL always includes `owner_internal_user_id=%s`. Listing defaults to `status='active'`; archived history is requested explicitly. Cursor payload includes and validates `status` so a cursor cannot cross lists.

- [ ] **Step 4: Implement sidebar actions**

Each row shows title and time only. The selected row exposes an accessible `更多` menu with `重命名` and `归档`. The sidebar footer exposes `已归档`, loads archived rows on demand, and offers `恢复`. No hard-delete action exists.

- [ ] **Step 5: Run tests and commit**

Run: `pytest -q backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py && npm --prefix webui test -- --run src/components/conversation/ConversationSidebar.test.tsx src/conversationApi.test.ts`

Expected: all selected tests PASS.

```bash
git add backend/app/agent_brain/conversation_routes.py backend/app/agent_brain/conversation_repository.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py webui/src/conversationApi.ts webui/src/components/conversation/ConversationSidebar.tsx webui/src/components/conversation/ConversationSidebar.test.tsx webui/src/conversationApi.test.ts
git commit -m "feat: manage professional conversation history"
```

---

### Task 7: Add structured, encrypted improvement feedback

**Files:**
- Create: `backend/control_migrations/044_conversation_feedback_detail.sql`
- Modify: `backend/app/agent_brain/conversation_models.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/review/routes.py`
- Test: `backend/tests/test_agent_brain_conversation_migration.py`
- Test: `backend/tests/test_agent_brain_conversation_repository.py`
- Test: `backend/tests/test_agent_brain_conversation_api.py`
- Modify: `webui/src/conversationTypes.ts`
- Modify: `webui/src/conversationApi.ts`
- Modify: `webui/src/components/conversation/ConversationMessages.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Test: `webui/src/pages/ConversationPage.test.tsx`

**Interfaces:**
- Produces reasons: `inaccurate | incomplete | unclear | unresolved | other`.
- Produces request: `{ rating, reason, comment }`, with `reason/comment` null for helpful.
- Produces Management Center feedback detail with decrypted comment only on authorized review route.

- [ ] **Step 1: Write failing migration, repository, and UI tests**

```python
def test_unhelpful_feedback_encrypts_comment(repository, connection):
    result = repository.create_feedback(OWNER, MESSAGE, "unhelpful", "incomplete", "缺少目标公司")
    row = connection.execute(
        "select reason,comment_ciphertext,comment_key_version from platform_control.conversation_feedback where feedback_id=%s",
        (result.feedback.feedback_id,),
    ).fetchone()
    assert row["reason"] == "incomplete"
    assert row["comment_ciphertext"] is not None
    assert b"目标公司" not in bytes(row["comment_ciphertext"])
```

```tsx
it("collects an optional improvement reason before submitting", async () => {
  await click("需改进");
  await click("信息不完整");
  await typeComment("缺少目标公司");
  await click("提交反馈");
  expect(submitFeedback).toHaveBeenCalledWith(
    "message-2", "unhelpful", "incomplete", "缺少目标公司", "csrf", expect.any(AbortSignal),
  );
});
```

- [ ] **Step 2: Run migration/repository/UI tests and verify RED**

Run: `pytest -q backend/tests/test_agent_brain_conversation_migration.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py && npm --prefix webui test -- --run src/pages/ConversationPage.test.tsx src/conversationApi.test.ts`

Expected: FAIL because structured detail fields and UI are absent.

- [ ] **Step 3: Add migration 044**

```sql
alter table platform_control.conversation_feedback
  add column reason text,
  add column comment_ciphertext bytea,
  add column comment_key_version integer;

alter table platform_control.conversation_feedback
  add constraint conversation_feedback_reason_v44 check (
    reason is null or reason in ('inaccurate','incomplete','unclear','unresolved','other')
  ),
  add constraint conversation_feedback_comment_pair_v44 check (
    (comment_ciphertext is null) = (comment_key_version is null)
  );
```

Retain existing grants: app role receives only the existing table operations; no public role gets access.

- [ ] **Step 4: Seal/unseal comments and expose role-appropriate DTOs**

Use `ContentCodec.seal_json(f"conversation-feedback:{feedback_id}:comment", {"text": comment})`. The member response includes rating and reason but omits comment. The owner/viewer review route may decrypt and return comment. Helpful feedback rejects non-null reason/comment; comments over 1,000 UTF-8 bytes return 422 before repository write.

- [ ] **Step 5: Implement the lightweight feedback interaction**

Change the labels to `有帮助` and `需改进`. Helpful submits immediately. Improvement opens five reason chips, optional comment, `提交反馈`, and `取消`. Failed submission retains the unsent selection and retries feedback only.

- [ ] **Step 6: Run tests and commit**

Run: `pytest -q backend/tests/test_agent_brain_conversation_migration.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py backend/tests/test_agent_brain_conversation_summary.py && npm --prefix webui test -- --run src/pages/ConversationPage.test.tsx src/conversationApi.test.ts`

Expected: all selected tests PASS.

```bash
git add backend/control_migrations/044_conversation_feedback_detail.sql backend/app/agent_brain/conversation_models.py backend/app/agent_brain/conversation_repository.py backend/app/agent_brain/conversation_routes.py backend/app/review/routes.py backend/tests/test_agent_brain_conversation_migration.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_conversation_api.py webui/src/conversationTypes.ts webui/src/conversationApi.ts webui/src/components/conversation/ConversationMessages.tsx webui/src/pages/ConversationPage.tsx webui/src/pages/ConversationPage.test.tsx webui/src/conversationApi.test.ts
git commit -m "feat: add structured conversation feedback"
```

---

### Task 8: Finish responsive visual hierarchy and accessibility

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Modify: `webui/src/components/conversation/ConversationSidebar.test.tsx`

**Interfaces:**
- Consumes all preceding components; produces no backend contract.

- [ ] **Step 1: Add failing structural and accessibility assertions**

```tsx
expect(container.querySelector(".agent-task-starters button")).not.toBeNull();
expect(container.querySelector(".conversation-header h1")?.textContent).toBe("HR Agent");
expect(container.querySelector(".conversation-sidebar [aria-current='page']")).not.toBeNull();
expect(container.querySelector("button[aria-label='打开对话操作']")).not.toBeNull();
```

- [ ] **Step 2: Run WebUI tests and verify RED**

Run: `npm --prefix webui test -- --run src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx src/components/conversation/ConversationSidebar.test.tsx`

Expected: FAIL on the new visual-structure/accessibility selectors.

- [ ] **Step 3: Implement the clean visual hierarchy**

Use one restrained accent color for the person/Agent identity, visible hover/focus states for all cards and history rows, a maximum readable message width, and a sticky composer that does not cover the latest message. At widths below 760 px, the history sidebar becomes a modal drawer, task starters wrap to one column, and feedback controls remain at least 44 px high.

```css
.conversation-message { max-width: 760px; }
.agent-task-starters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
.agent-task-starters button:hover,
.agent-task-starters button:focus-visible { border-color: var(--accent); transform: translateY(-1px); }
@media (max-width: 760px) {
  .agent-task-starters { grid-template-columns: 1fr; }
  .conversation-feedback button { min-height: 44px; }
}
```

- [ ] **Step 4: Build and commit**

Run: `npm --prefix webui test && npm --prefix webui run build`

Expected: all WebUI tests PASS and production build exits 0.

```bash
git add webui/src/styles.css webui/src/pages/AgentUsePage.test.tsx webui/src/pages/ConversationPage.test.tsx webui/src/components/conversation/ConversationSidebar.test.tsx
git commit -m "style: refine professional agent workspace"
```

---

### Task 9: Full verification, safe rollout, and production acceptance

**Files:**
- Modify when required by new assertions: `deploy/local-execution-worker/accept.sh`
- Modify when required by new assertions: `deploy/cloud/accept.sh`
- Create: `docs/operations/2026-08-25-professional-agent-ux-release.md`

**Interfaces:**
- Consumes MetaBot contract v2 and all Platform changes.
- Produces release evidence and rollback identifiers.

- [ ] **Step 1: Add rollout gates**

The local acceptance script must assert the MetaBot capability response advertises `core_chat_result_v2` before the Platform strict consumer is deployed. Cloud acceptance must assert HR member HTML contains no `诊断详情`, `hr-bot`, `accepted`, or `/missions/` link and that Management Center still exposes authorized diagnostic records.

- [ ] **Step 2: Run complete local verification**

MetaBot:

```bash
npm test
npm run lint
npm run build:bridge
```

Platform:

```bash
pytest -q backend/tests
npm --prefix webui test
npm --prefix webui run build
git diff --check
```

Expected: every command exits 0 with no failed tests.

- [ ] **Step 3: Deploy producer before consumer**

1. Record MetaBot and Platform pre-release commits, worker heartbeat, container ID/Image ID/StartedAt/RestartCount, and current HR smoke result.
2. Deploy MetaBot Core Chat v2 locally and restart only the local execution worker through its existing provisioning script.
3. Run `deploy/local-execution-worker/accept.sh` and prove the v2 contract capability is active.
4. Run one direct HR probe and one internal Brain/professional probe; confirm public mode contains `publicAnswerMarkdown` and internal mode does not.
5. Only then deploy Platform and migration 044 through the existing cloud deployment script.

- [ ] **Step 4: Perform member/operator production acceptance**

Using a real member account:

1. HR: open a new conversation, ask `介绍一下你自己`, continue with a follow-up, and verify no internal preamble or diagnostics.
2. Marketing: switch among exactly five cards and verify switching starts/opens Agent-scoped history rather than rebinding a conversation.
3. Brain: verify `查看协作过程` shows named business delegation without diagnostic IDs.
4. Failure: stop one controlled Turn and verify safe retry language.
5. History: rename, archive, open archived history, and restore.
6. Feedback: submit helpful once; submit improvement with reason/comment on another answer.
7. Management Center: locate the same runs, verify full diagnostics and encrypted feedback comment projection are available only to authorized roles.
8. Repeat critical navigation, composer, and feedback checks on mobile width and DingTalk mobile client.

- [ ] **Step 5: Write release evidence and commit**

The release document records commits, release IDs, test counts, screenshots, pre/post worker and cloud runtime evidence, migration result, acceptance results, and exact rollback commands. It contains no Cookie, token, private candidate information, raw provider payload, or internal prompt.

```bash
git add deploy/local-execution-worker/accept.sh deploy/cloud/accept.sh docs/operations/2026-08-25-professional-agent-ux-release.md
git commit -m "docs: record professional agent ux release"
```

---

## Final release order

```text
MetaBot tests/build
  → deploy local MetaBot Core Chat v2 producer
  → prove worker advertises v2
  → direct public + internal result probes
  → Platform backend/web tests and build
  → Platform migration 044 + application release
  → real member HR/Marketing/Brain acceptance
  → authorized Management Center verification
  → mobile/DingTalk acceptance
```

Rollback Platform first if member delivery fails; the v2 MetaBot producer remains backward-compatible with the old Platform consumer. Roll back MetaBot only after the old Platform consumer is restored. Migration 044 is additive and may remain in place during application rollback.
