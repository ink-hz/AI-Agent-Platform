# Agent Brain Continuous Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-message/one-Mission experience with encrypted, owner-scoped, resumable continuous conversations while retaining Mission, ChildRun, Trace, Evidence, and admin observability behind each turn.

**Architecture:** Add first-class `Conversation`, `ConversationMessage`, and `ConversationTurn` control-plane records. Starting or continuing a conversation atomically creates a user message, a turn, and its linked Mission; the existing orchestrator executes that Mission and durably projects the result into an assistant message. The browser stays on `/conversations/{id}` and renders persisted Mission events as an expandable collaboration card.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, PostgreSQL 17, psycopg 3, AES-256-GCM, React 18, TypeScript, Vite, SSE, pytest, Vitest, Docker Compose, Nginx.

## Global Constraints

- `/` is an immediately usable continuous-conversation Agent 大脑 page.
- One Conversation is one history item; only “新对话” creates another Conversation.
- One Conversation contains multiple Messages and Turns; one Turn owns at most one Mission.
- Sending a message never navigates to `/missions/{mission_id}`.
- Conversation, first user Message, first Turn, and first Mission are created atomically.
- IDs are server UUIDs except the client UUID `Idempotency-Key`.
- Content uses the existing purpose-bound, versioned content keyring.
- The browser cannot assert user, role, model, Adapter, or authorization scope.
- Direct-Agent authorization is rechecked on every Turn.
- One Conversation permits one non-terminal Turn; overlap returns `409`.
- Input remains text-only and limited to 32 KiB UTF-8.
- Context is the confirmed summary plus recent Messages and never another Conversation implicitly.
- Failure is explicit; no silent Agent/model/provider/host fallback.
- Existing Mission deep links remain valid but leave ordinary navigation.
- Existing FAE service, domain, container, accounts, and customer UI remain unchanged.

## File Structure

```text
backend/app/agent_brain/
├── conversation_models.py
├── conversation_repository.py
├── conversation_context.py
├── conversation_routes.py
└── conversation_projection.py

webui/src/
├── conversationTypes.ts
├── conversationApi.ts
├── pages/ConversationPage.tsx
├── pages/ConversationsPage.tsx
└── components/conversation/
    ├── ConversationComposer.tsx
    ├── ConversationMessages.tsx
    └── ExecutionCard.tsx
```

Keep `repository.py`, `orchestrator.py`, and `routes.py` responsible for Mission execution and compatibility. Keep `MissionPage.tsx` for deep-link compatibility and diagnostics.

---

### Task 1: Add the Conversation control-plane schema

**Files:**
- Create: `backend/control_migrations/036_agent_brain_conversations.sql`
- Create: `backend/tests/test_agent_brain_conversation_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Produces: `conversations`, `conversation_messages`, `conversation_turns`, `conversation_events`.
- Produces: nullable Conversation/Turn/trigger-message links on `missions`.

- [ ] **Step 1: Write the failing schema tests**

```python
def test_conversation_schema_separates_history_from_mission_state(control_db):
    migrate_control(control_db)
    assert columns(control_db, "platform_control", "conversations") >= {
        "conversation_id", "owner_internal_user_id", "mode", "direct_agent_id",
        "title", "status", "summary_ciphertext", "summary_key_version",
        "summary_through_seq", "created_at", "updated_at", "archived_at",
    }
    assert unique_columns(control_db, "platform_control", "conversation_messages") >= {
        ("conversation_id", "seq"), ("conversation_id", "message_id")
    }
    assert foreign_key(control_db, "platform_control.missions", "conversation_id")
    assert not plaintext_columns(control_db, "platform_control", "conversation_messages")
```

Also prove all status/role check constraints, the one-active-Turn partial unique index, production/preview ownership, no `PUBLIC` grants, and no online-role `DELETE` grant.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_migration.py tests/test_control_plane_migration.py -q
```

Expected: FAIL because migration 036 is absent.

- [ ] **Step 3: Implement migration 036**

Use the owner-role selection pattern from migration 035 and these core definitions:

```sql
create table platform_control.conversations (
  conversation_id uuid primary key,
  owner_internal_user_id uuid not null references platform_control.internal_users(internal_user_id),
  mode text not null check (mode in ('brain','direct_agent')),
  direct_agent_id text check (direct_agent_id is null or direct_agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  title text not null check (char_length(title) between 1 and 160),
  status text not null default 'active' check (status in ('active','archived')),
  summary_ciphertext bytea,
  summary_key_version integer,
  summary_through_seq integer not null default 0 check (summary_through_seq >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived_at timestamptz,
  check ((mode='brain' and direct_agent_id is null) or (mode='direct_agent' and direct_agent_id is not null)),
  check ((summary_ciphertext is null and summary_key_version is null) or
         (octet_length(summary_ciphertext) between 29 and 1048576 and summary_key_version > 0)),
  check ((status='active' and archived_at is null) or (status='archived' and archived_at is not null))
);

create table platform_control.conversation_messages (
  message_id uuid primary key,
  conversation_id uuid not null references platform_control.conversations(conversation_id),
  seq integer not null check (seq > 0),
  role text not null check (role in ('user','assistant','system')),
  content_ciphertext bytea not null check (octet_length(content_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null check (encryption_key_version > 0),
  turn_id uuid,
  mission_id uuid references platform_control.missions(mission_id),
  delivery_status text not null check (delivery_status in ('accepted','streaming','completed','failed')),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (conversation_id,seq), unique (conversation_id,message_id)
);

create table platform_control.conversation_turns (
  turn_id uuid primary key,
  conversation_id uuid not null references platform_control.conversations(conversation_id),
  user_message_id uuid not null,
  assistant_message_id uuid,
  client_request_id uuid not null,
  mission_id uuid references platform_control.missions(mission_id),
  status text not null check (status in ('accepted','running','completed','failed','cancelled','interrupted')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (conversation_id,client_request_id), unique (conversation_id,turn_id),
  foreign key (conversation_id,user_message_id)
    references platform_control.conversation_messages(conversation_id,message_id)
);

create unique index one_active_conversation_turn
  on platform_control.conversation_turns(conversation_id)
  where status in ('accepted','running');
```

Add deferred Message↔Turn FKs, `conversation_events` with `(conversation_id,seq)`, and nullable `conversation_id`, `turn_id`, `triggering_message_id` FKs on `missions`. Revoke all from `PUBLIC`; grant selected app role only `SELECT,INSERT,UPDATE`.

- [ ] **Step 4: Run focused tests and commit**

```bash
cd backend && .venv/bin/pytest tests/test_agent_brain_conversation_migration.py tests/test_control_plane_migration.py -q
cd ..
git add backend/control_migrations/036_agent_brain_conversations.sql backend/tests/test_agent_brain_conversation_migration.py backend/tests/test_control_plane_migration.py
git commit -m "feat(brain): add continuous conversation schema"
```

### Task 2: Implement encrypted records and atomic Turn creation

**Files:**
- Create: `backend/app/agent_brain/conversation_models.py`
- Create: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/repository.py`
- Create: `backend/tests/test_agent_brain_conversation_repository.py`
- Modify: `backend/tests/test_agent_brain_repository.py`

**Interfaces:**
- Produces immutable `ConversationRecord`, `ConversationMessageRecord`, `ConversationTurnRecord`, and `ConversationCreateResult`.
- Produces `start`, `append_turn`, `conversation_for_owner`, `messages_after`, `list_for_owner`, `archive`, `request_cancel`.
- Consumes `MissionRepository.insert_for_conversation(cursor, ...)` inside the caller transaction.

- [ ] **Step 1: Write failing atomicity and isolation tests**

```python
def test_start_is_atomic_and_ciphertext_only(repository, db, owner):
    result = repository.start(owner, uuid4(), "帮我找视觉算法候选人", mode="brain", direct_agent_id=None)
    assert result.created is True
    assert result.turn.mission_id is not None
    assert db.scalar("select count(*) from platform_control.conversations") == 1
    assert db.scalar("select count(*) from platform_control.conversation_messages") == 1
    assert db.scalar("select count(*) from platform_control.conversation_turns") == 1
    assert db.scalar("select count(*) from platform_control.missions") == 1
    assert b"视觉算法" not in db.dump_relation("platform_control.conversation_messages")
```

Cover idempotent replay, same-key/different-text conflict, owner isolation, archived Conversation, active-Turn conflict, direct Agent mismatch, monotonic sequences, title truncation, and rollback when Mission insertion fails.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_repository.py tests/test_agent_brain_repository.py -q
```

- [ ] **Step 3: Add exact record types and subjects**

```python
@dataclass(frozen=True)
class ConversationMessageRecord:
    message_id: UUID
    conversation_id: UUID
    seq: int
    role: Literal["user", "assistant", "system"]
    content: str
    turn_id: UUID | None
    mission_id: UUID | None
    delivery_status: Literal["accepted", "streaming", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None

def message_subject(conversation_id: UUID, message_id: UUID) -> str:
    return f"conversation:{conversation_id}:message:{message_id}:content"
```

Define Conversation and Turn records with every Task 1 column; public methods return records, not untyped dictionaries.

- [ ] **Step 4: Implement atomic start/append**

Allocate Conversation, Message, Turn, and Mission UUIDs before opening one transaction. Insert all four objects with deferred FKs, encrypt user content using `message_subject`, call `insert_for_conversation`, link the Turn, and commit once. `append_turn` locks the owned active Conversation and rejects an existing non-terminal Turn. Unique-key conflicts return the existing object only when owner, text digest, mode, and Agent match.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/agent_brain/conversation_models.py backend/app/agent_brain/conversation_repository.py backend/app/agent_brain/repository.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_agent_brain_repository.py
git commit -m "feat(brain): persist encrypted conversation turns"
```

### Task 3: Build bounded context and terminal projection

**Files:**
- Create: `backend/app/agent_brain/conversation_context.py`
- Create: `backend/app/agent_brain/conversation_projection.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/agent_brain/repository.py`
- Create: `backend/tests/test_agent_brain_conversation_context.py`
- Create: `backend/tests/test_agent_brain_conversation_projection.py`
- Modify: `backend/tests/test_agent_brain_orchestrator.py`

**Interfaces:**
- Produces `ConversationContextBuilder.build(conversation_id, turn_id) -> ConversationContext`.
- Produces idempotent `ConversationProjection.project_terminal(mission_id) -> bool`.

- [ ] **Step 1: Write failing multi-turn and projection tests**

```python
def test_second_turn_contains_first_exchange_and_current_request(builder, seeded):
    context = builder.build(seeded.conversation_id, seeded.second_turn_id)
    assert [item.role for item in context.messages] == ["user", "assistant", "user"]
    assert context.messages[-1].content == "继续，给我搜索式"
    assert context.estimated_utf8_bytes <= 96 * 1024

def test_terminal_projects_exactly_one_assistant_message(projector, terminal):
    assert projector.project_terminal(terminal.mission_id) is True
    assert projector.project_terminal(terminal.mission_id) is False
    assert terminal.turn().status == "completed"
    assert terminal.messages()[-1].content == terminal.final_delivery
```

Cover another Conversation exclusion, summary boundaries, failed/cancelled/interrupted results, crash recovery, and release of the Conversation for the next Turn.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_context.py tests/test_agent_brain_conversation_projection.py tests/test_agent_brain_orchestrator.py -q
```

- [ ] **Step 3: Implement deterministic context**

```python
@dataclass(frozen=True)
class ContextMessage:
    role: Literal["user", "assistant", "system"]
    content: str

@dataclass(frozen=True)
class ConversationContext:
    summary: str | None
    messages: tuple[ContextMessage, ...]
    estimated_utf8_bytes: int

MAX_CONTEXT_BYTES = 96 * 1024
```

Use confirmed summary first and then messages with `seq > summary_through_seq`; always retain the current user message. Before summary support lands, reject over-budget context explicitly.

- [ ] **Step 4: Integrate projection with orchestration**

Change planning/direct prompts to consume `ConversationContext`. After a terminal Mission commit, project its final delivery or explicit failure into an assistant/system Message. The loop also scans terminal Missions whose linked Turn is non-terminal so a crash recovers exactly once.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/agent_brain/conversation_context.py backend/app/agent_brain/conversation_projection.py backend/app/agent_brain/orchestrator.py backend/app/agent_brain/repository.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_agent_brain_conversation_projection.py backend/tests/test_agent_brain_orchestrator.py
git commit -m "feat(brain): carry context across conversation turns"
```

### Task 4: Add owned Conversation REST and SSE APIs

**Files:**
- Create: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/routes.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_agent_brain_conversation_api.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Produces the approved Conversation endpoints and owner-scoped SSE stream.
- Consumes secure `AuthContext`, Agent authorization, repository, and SSE limiter.

- [ ] **Step 1: Write failing API tests**

```python
def test_follow_up_reuses_conversation(client, member_headers):
    first = client.post("/api/v1/conversations", json={"text": "定义候选人画像"}, headers={**member_headers, "Idempotency-Key": str(uuid4())})
    conversation_id = first.json()["conversation_id"]
    finish_current_turn(conversation_id)
    second = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "继续，给出 GitHub 搜索式"}, headers={**member_headers, "Idempotency-Key": str(uuid4())})
    assert second.status_code == 201
    assert second.json()["conversation_id"] == conversation_id
```

Cover authentication, CSRF, Origin, other owner, archived state, UTF-8 limit, idempotency, `409`, direct Agent grant/revocation, pagination, cancellation, archive, no-store, SSE replay, and heartbeat.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_api.py tests/test_dingtalk_auth_api.py -q
```

- [ ] **Step 3: Implement strict routes**

```python
class ConversationTextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=32 * 1024)

@router.post("/api/v1/conversations", status_code=201)
async def start_conversation(body, request, response, idempotency_key=Header(...)):
    context = auth_context(request)
    result = await asyncio.to_thread(
        conversations.start, context.internal_user_id,
        parse_idempotency_key(idempotency_key), body.text,
        mode="brain", direct_agent_id=None,
    )
    response.status_code = 201 if result.created else 200
    response.headers.update(NO_STORE)
    return conversation_payload(result)
```

Add list/detail/messages/events/cancel/archive and `POST /api/v1/agents/{agent_id}/conversations`. Never accept an Agent ID in the JSON body. SSE uses `event: conversation` and monotonically increasing Conversation event IDs.

- [ ] **Step 4: Register exact route templates**

Register only the nine approved REST templates; do not add `/api/v1/*` wildcards. Wire the router only when Agent Brain is enabled.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/agent_brain/conversation_routes.py backend/app/agent_brain/routes.py backend/app/control_plane/authorization.py backend/app/control_plane/middleware.py backend/app/main.py backend/tests/test_agent_brain_conversation_api.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(brain): expose continuous conversation APIs"
```

### Task 5: Add typed browser Conversation API and routes

**Files:**
- Create: `webui/src/conversationTypes.ts`
- Create: `webui/src/conversationApi.ts`
- Create: `webui/src/conversationApi.test.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.brain.test.ts`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/documentTitle.ts`

**Interfaces:**
- Produces strict types and `startConversation`, `appendConversationMessage`, `fetchConversation`, `listConversations`, `fetchConversationMessages`, `streamConversationEvents`, `cancelCurrentTurn`, `archiveConversation`.
- Produces `/conversations` and `/conversations/{id}` browser routes.

- [ ] **Step 1: Write failing client and router tests**

```typescript
it("reuses one conversation for a follow-up", async () => {
  const submission = createConversationMessageSubmission("c-1", "继续", "csrf");
  await submission.send();
  expect(fetch).toHaveBeenCalledWith("/api/v1/conversations/c-1/messages", expect.objectContaining({
    method: "POST", body: JSON.stringify({ text: "继续" }),
  }));
});

it("routes conversation history", () => {
  expect(parseRoute("/conversations")).toEqual({ name: "conversations" });
  expect(parseRoute("/conversations/abc")).toEqual({ name: "conversation", conversationId: "abc" });
});
```

Reject missing/extra response fields, invalid statuses, non-monotonic SSE IDs, truncated frames, and another Conversation’s event.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd webui
npm test -- --run src/conversationApi.test.ts src/router.brain.test.ts
```

- [ ] **Step 3: Implement exact contracts and retry identity**

```typescript
export type ConversationMode = "brain" | "direct_agent";
export type TurnStatus = "accepted" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export interface ConversationMessage {
  message_id: string; conversation_id: string; seq: number;
  role: "user" | "assistant" | "system";
  content: string; turn_id: string | null; mission_id: string | null;
  delivery_status: "accepted" | "streaming" | "completed" | "failed";
  created_at: string; completed_at: string | null;
}
```

Generate one `crypto.randomUUID()` per submission object and retain it across retries. Use strict response parsers, existing CSRF/error handling, and the 32 KiB UTF-8 check.

- [ ] **Step 4: Add routes and safe return paths**

Map `/conversations` to history and `/conversations/{id}` to Conversation. Permit only UUID-shaped Conversation return paths in `auth.ts`; malformed values fall back to `/`.

- [ ] **Step 5: Run tests and commit**

```bash
git add webui/src/conversationTypes.ts webui/src/conversationApi.ts webui/src/conversationApi.test.ts webui/src/router.ts webui/src/router.brain.test.ts webui/src/auth.ts webui/src/documentTitle.ts
git commit -m "feat(ui): add typed continuous conversation client"
```

### Task 6: Replace the Mission-first UI with continuous Conversation UI

**Files:**
- Create: `webui/src/pages/ConversationPage.tsx`
- Create: `webui/src/pages/ConversationPage.test.tsx`
- Create: `webui/src/pages/ConversationsPage.tsx`
- Create: `webui/src/pages/ConversationsPage.test.tsx`
- Create: `webui/src/components/conversation/ConversationComposer.tsx`
- Create: `webui/src/components/conversation/ConversationMessages.tsx`
- Create: `webui/src/components/conversation/ExecutionCard.tsx`
- Modify: `webui/src/pages/BrainPage.tsx`
- Modify: `webui/src/pages/BrainPage.test.tsx`
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes Task 5 API.
- Produces persistent composer, Markdown timeline, resumable SSE, execution disclosure, new Conversation, and paginated history.

- [ ] **Step 1: Write failing user-flow tests**

```tsx
it("keeps the composer after an answer and appends a follow-up", async () => {
  render(<ConversationPage conversationId="c-1" account={account} client={client} />);
  await screen.findByText("第一轮结果");
  await userEvent.type(screen.getByLabelText("继续对话"), "继续给出搜索式");
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(client.createMessageSubmission).toHaveBeenCalledWith("c-1", "继续给出搜索式", account.csrf_token);
  expect(screen.getByLabelText("继续对话")).toBeInTheDocument();
});

it("first send opens a Conversation, never a Mission", async () => {
  render(<BrainPage account={account} client={client} />);
  await submit("帮我找候选人");
  expect(window.location.pathname).toBe("/conversations/c-1");
  expect(window.location.pathname).not.toContain("/missions/");
});
```

Add tests for one history item after two Turns, explicit new Conversation, stop, retry identity, reconnect, Markdown, execution disclosure, mobile layout, direct Agent, and hard-stale read-only mode.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd webui
npm test -- --run src/pages/ConversationPage.test.tsx src/pages/ConversationsPage.test.tsx src/pages/BrainPage.test.tsx
```

- [ ] **Step 3: Implement page state and navigation**

On first send:

```typescript
const result = await submission.send(controller.signal);
navigate(`/conversations/${encodeURIComponent(result.conversation_id)}`, { replace: true });
```

`ConversationPage` loads snapshot/messages, resumes SSE after the last sequence, merges by stable IDs, refetches after reconnect, and retains the composer after terminal events. It must not import `createMissionSubmission`.

- [ ] **Step 4: Implement safe execution disclosure and navigation copy**

`ExecutionCard` receives only persisted Conversation events and is collapsed by default. Its panel renders the safe task, Agent, progress, result/Evidence links, and optional Mission diagnostic deep link. Use the existing Markdown renderer and never `dangerouslySetInnerHTML`. Rename “历史任务” to “历史对话”; keep management under `/admin`.

- [ ] **Step 5: Run all frontend tests/build and commit**

```bash
cd webui
npm test -- --run
npm run build
cd ..
git add webui/src
git commit -m "feat(ui): make Agent Brain a continuous conversation"
```

### Task 7: Backfill legacy Missions into one-turn Conversations

**Files:**
- Create: `backend/app/agent_brain/conversation_backfill.py`
- Create: `backend/tests/test_agent_brain_conversation_backfill.py`
- Modify: `deploy/cloud/remote-stage.sh`
- Modify: `backend/tests/test_execution_worker_deployment.py`
- Modify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Produces idempotent `python -m app.agent_brain.conversation_backfill`.
- Consumes maintenance DSN and content keyring; re-encrypts content with Conversation subjects.

- [ ] **Step 1: Write failing backfill tests**

```python
def test_backfill_creates_one_conversation_per_legacy_mission(backfill, legacy):
    first = backfill.run(batch_size=100)
    second = backfill.run(batch_size=100)
    assert first.created == legacy.count
    assert second.created == 0
    assert legacy.conversation_count() == legacy.count
    assert legacy.owner_mismatches() == 0
```

Cover completed, failed, interrupted, corrupt-content quarantine, mixed owners, crash retry, and no name-based ownership. Original Mission rows/ciphertext remain unchanged except additive links.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_backfill.py -q
```

- [ ] **Step 3: Implement deterministic, keyset-batched backfill**

Lock legacy rows with `FOR UPDATE SKIP LOCKED`. Derive Conversation/Message/Turn UUIDv5 values from Mission ID, decrypt the first user message/final delivery, re-encrypt under Conversation subjects, add an explicit terminal message for incomplete Missions, and link the Mission. Print only:

```text
AGENT_BRAIN_CONVERSATION_BACKFILL_OK scanned=<n> created=<n> quarantined=<n>
```

- [ ] **Step 4: Wire deployment and rollback documentation**

Run after migration and before API cutover with Brain disabled. Require exact output and zero quarantine for first release. The additive rollback switches the feature flag off without deleting Conversations and never mutates FAE.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/agent_brain/conversation_backfill.py backend/tests/test_agent_brain_conversation_backfill.py backend/tests/test_execution_worker_deployment.py deploy/cloud/remote-stage.sh docs/runbooks/cloud-platform.md
git commit -m "feat(brain): backfill Mission history into conversations"
```

### Task 8: Bind Feedback and Operations to Conversation Turns

**Files:**
- Modify: `backend/app/feedback/routes.py`
- Modify: `backend/app/operations/routes.py`
- Modify: `backend/app/review/routes.py`
- Modify: `backend/tests/test_feedback_api.py`
- Modify: `backend/tests/test_operations_api.py`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/operationsUi.test.tsx`

**Interfaces:**
- Produces feedback targeting assistant Message/Turn while retaining Mission/Run links.
- Produces Conversation count, multi-turn rate, Turn completion rate, and Mission quality.

- [ ] **Step 1: Write failing feedback/operations tests**

```python
def test_feedback_binds_owned_assistant_message(client, member, conversation):
    response = client.post("/api/feedback", json={
        "target_kind": "conversation_message",
        "target_id": str(conversation.assistant_message_id),
        "rating": "helpful",
    }, headers=member.headers)
    assert response.status_code == 201
    assert response.json()["turn_id"] == str(conversation.turn_id)
```

Prove another owner is denied, content is absent from audit details, and two Turns count as one multi-turn Conversation.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_feedback_api.py tests/test_operations_api.py -q
```

- [ ] **Step 3: Implement scoped projection and UI control**

Resolve assistant Message through owner-scoped Conversation lookup, derive Turn/Mission links server-side, retain Mission feedback compatibility, and count Conversation/Turn/Mission separately. Add per-assistant-message feedback without exposing IDs from another owner.

- [ ] **Step 4: Run backend/frontend tests and commit**

```bash
cd backend && .venv/bin/pytest tests/test_feedback_api.py tests/test_operations_api.py -q
cd ../webui && npm test -- --run src/operationsUi.test.tsx src/pages/ConversationPage.test.tsx
cd ..
git add backend/app/feedback/routes.py backend/app/operations/routes.py backend/app/review/routes.py backend/tests/test_feedback_api.py backend/tests/test_operations_api.py webui/src/pages/ConversationPage.tsx webui/src/operationsUi.test.tsx
git commit -m "feat(brain): observe and review conversation turns"
```

### Task 9: Add long-conversation summary and context budgets

**Files:**
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Create: `backend/tests/test_agent_brain_conversation_summary.py`

**Interfaces:**
- Produces encrypted summary plus `summary_through_seq`.
- Uses only the existing Agent Brain runtime; adds no cloud model client.

- [ ] **Step 1: Write failing summary tests**

```python
def test_summary_compacts_only_completed_turns(summarizer, long_conversation):
    result = summarizer.compact(long_conversation.conversation_id)
    assert result.summary_through_seq == long_conversation.last_completed_assistant_seq
    assert result.summary_through_seq < long_conversation.active_user_seq
    context = long_conversation.context()
    assert context.messages[-1].content == long_conversation.current_request
    assert context.estimated_utf8_bytes <= 96 * 1024
```

Reject extra fields, wrong sequence, output over 32 KiB, and unavailable runtime. Failure keeps the previous summary and writes an explicit system message.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/pytest tests/test_agent_brain_conversation_summary.py tests/test_agent_brain_conversation_context.py -q
```

- [ ] **Step 3: Implement strict summary protocol**

Accept exactly:

```json
{"summary":"用户正在招聘视觉算法工程师；已确认候选人需具备英文、视觉技术和硬件产品经验。","through_seq":8}
```

Require `through_seq` to equal a Platform-selected completed assistant sequence. Encrypt with `conversation:{conversation_id}:summary:v{key_version}` and update summary fields atomically.

- [ ] **Step 4: Trigger compaction before over-budget planning**

Enqueue one Brain summary phase before planning. On success continue the same Turn; on failure mark it failed with a user-visible message without dropping the current request or reading unrelated history.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/agent_brain/conversation_context.py backend/app/agent_brain/conversation_repository.py backend/app/agent_brain/orchestrator.py backend/tests/test_agent_brain_conversation_summary.py
git commit -m "feat(brain): bound long conversation context"
```

### Task 10: Full regression, production acceptance, rollback, and evidence

**Files:**
- Modify: `deploy/cloud/accept.sh`
- Modify: `docs/runbooks/cloud-platform.md`
- Create: `docs/reviews/2026-08-23-agent-brain-continuous-conversations-acceptance.md`
- Modify: `backend/tests/test_agent_brain_deployment.py`
- Modify: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Produces fail-closed acceptance for two real Turns in one Conversation, direct Agent, Mission linkage, SSE recovery, authorization, mobile UI, rollback, restore, and FAE invariance.

- [ ] **Step 1: Extend failing acceptance tests**

Require this exact sequence:

```text
member starts one HR-oriented Brain Conversation
first Turn creates one Mission and ChildRun
member posts a follow-up to the same Conversation
follow-up receives prior context and creates a second Turn
history has one Conversation, two user Messages, two assistant Messages
SSE resume creates no duplicate Turn/ChildRun
member cannot access another Conversation or /admin
owner can inspect linked Missions under /admin
unauthorized direct Agent returns 403
rollback sends owner root to /admin without deleting data
restore sends root to Conversation and permits a third Turn
FAE identity/config/mount/start/restart hashes are invariant
```

- [ ] **Step 2: Run all local gates**

```bash
cd backend
.venv/bin/pytest -q
cd ../webui
npm test -- --run
npm run build
cd ..
git diff --check
deploy/cloud/acceptance.sh local
```

Expected: all suites/build pass and `CLOUD_PLATFORM_LOCAL_GATE_OK` is printed.

- [ ] **Step 3: Review, merge latest master, and rerun affected gates**

Use requesting-code-review on the full diff. Resolve every Critical/Important finding. Merge current `origin/master` without rewriting user changes; rerun affected tests and require clean `git diff --check`.

- [ ] **Step 4: Deploy disabled, then run formal release**

Deploy the reviewed image with Brain disabled, run migration/backfill, verify Worker identity/heartbeat and FAE invariance, then run the two-account `accept.sh ... release` gate. Do not complete production by directly editing `platform.env`.

- [ ] **Step 5: Prove rollback and restore**

Run `accept.sh ... rollback`; verify `/` routes owner to `/admin`, Conversation data remains, and FAE is invariant. Run `accept.sh ... restore`; verify `/` is the composer and the same Conversation accepts another Turn exactly once.

- [ ] **Step 6: Write sanitized evidence and commit**

Evidence may contain release SHA, object UUIDs, event sequences/counts, container identity/start time, listeners, and FAE hashes. It must exclude prompts, answers, cookies, DingTalk IDs/names, DSNs, secrets, ciphertext, and reasoning.

```bash
git add deploy/cloud/accept.sh docs/runbooks/cloud-platform.md docs/reviews/2026-08-23-agent-brain-continuous-conversations-acceptance.md backend/tests/test_agent_brain_deployment.py webui/src/cloudMode.test.tsx
git commit -m "test(brain): accept continuous conversations end to end"
```
