# HR R1.2 Position Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every HR position task use immutable official facts, a confirmed position-context version, exact materials, and a recoverable task record.

**Architecture:** Migration 067 owns official versions, context versions, module confirmations, and task records. New focused HR modules expose repository/service/router boundaries; `ConversationContextBuilder` composes an immutable HR envelope only for a verified `hr-bot` position conversation.

**Tech Stack:** PostgreSQL, Python 3.11, psycopg 3, FastAPI, Pydantic, pytest.

## Global Constraints

- Preserve owner isolation, idempotency, immutable confirmed versions, and R1.1 Position bindings.
- Website facts never overwrite internal confirmed context.
- AI writes drafts only; confirmation requires an authenticated human request and baseline version.
- Invalid Position, version, material, or conversation scope fails closed.
- Preserve `backend/.venv`; do not touch unrelated applications or Nginx.

---

### Task 1: Add migration 067 and domain models

**Files:**
- Create: `backend/control_migrations/067_hr_position_intelligence.sql`
- Create: `backend/app/hr/position_intelligence_models.py`
- Create: `backend/tests/test_hr_position_intelligence_migration.py`
- Create: `backend/tests/test_hr_position_intelligence_models.py`

**Interfaces:**
- Produces: `OfficialPositionVersion`, `PositionContextVersion`, `PositionTaskRecord`, `CreateContextDraft`, `ConfirmContextModules`

- [ ] **Step 1: Write failing migration and model tests**

```python
def test_confirmed_context_is_immutable_and_one_current_version_per_position():
    assert migration_has_partial_unique_current_index()
    assert migration_revokes_table_writes_from_app_role()

def test_context_draft_requires_exact_baselines():
    command = CreateContextDraft(position_id=POSITION, base_context_version_id=None,
                                 official_version_id=OFFICIAL, modules={"talent_profile": {}},
                                 client_request_id=REQUEST)
    assert command.position_id == POSITION
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_intelligence_migration.py tests/test_hr_position_intelligence_models.py`

Expected: FAIL because migration 067 and models do not exist.

- [ ] **Step 3: Implement schema and bounded models**

Create owner-composite foreign keys and security-definer functions for official projection, draft creation, module confirmation, version reads, and task-record creation. Use the exact immutable envelope identity:

```python
@dataclass(frozen=True, slots=True)
class HrPositionContextEnvelope:
    position_id: UUID
    official_version_id: UUID | None
    context_version_id: UUID | None
    task_kind: str
    material_attachment_ids: tuple[UUID, ...]
    candidate_id: UUID | None
    position_candidate_id: UUID | None
    document_attachment_ids: tuple[UUID, ...]
    human_feedback_ids: tuple[UUID, ...]
    prompt_context: str
    canonical_sha256: str
```

- [ ] **Step 4: Run GREEN and commit**

Run the Step 2 command. Expected: PASS.

```bash
git add backend/control_migrations/067_hr_position_intelligence.sql backend/app/hr/position_intelligence_models.py backend/tests/test_hr_position_intelligence_migration.py backend/tests/test_hr_position_intelligence_models.py
git commit -m "feat(hr): add position intelligence schema"
```

### Task 2: Implement position-intelligence repository and service

**Files:**
- Create: `backend/app/hr/position_intelligence_repository.py`
- Create: `backend/app/hr/position_intelligence_service.py`
- Create: `backend/tests/test_hr_position_intelligence_repository.py`
- Create: `backend/tests/test_hr_position_intelligence_service.py`

**Interfaces:**
- Consumes: migration 067 functions and models from Task 1
- Produces: `PositionIntelligenceRepository`, `PositionIntelligenceService`

- [ ] **Step 1: Write RED tests for replay, conflict, and owner isolation**

```python
def test_context_confirmation_is_replay_stable_and_rejects_a_stale_baseline(repository):
    first = repository.confirm_modules(CONFIRM)
    assert repository.confirm_modules(CONFIRM) == first
    with pytest.raises(PositionContextConflict):
        repository.confirm_modules(replace(CONFIRM, client_request_id=uuid4()))
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_intelligence_repository.py tests/test_hr_position_intelligence_service.py`

Expected: FAIL because repository and service do not exist.

- [ ] **Step 3: Implement typed repository/service boundaries**

```python
class PositionIntelligenceService:
    def current(self, owner_id: UUID, position_id: UUID) -> PositionContextVersion | None: ...
    def create_draft(self, command: CreateContextDraft) -> PositionContextVersion: ...
    def confirm_modules(self, command: ConfirmContextModules) -> PositionContextVersion: ...
    def compare(self, owner_id: UUID, position_id: UUID, left: UUID, right: UUID) -> dict[str, object]: ...
```

- [ ] **Step 4: Run GREEN and commit**

Run the Step 2 command. Expected: PASS.

```bash
git add backend/app/hr/position_intelligence_repository.py backend/app/hr/position_intelligence_service.py backend/tests/test_hr_position_intelligence_repository.py backend/tests/test_hr_position_intelligence_service.py
git commit -m "feat(hr): add position context versions"
```

### Task 3: Persist complete official facts and expose APIs

**Files:**
- Create: `backend/app/hr/position_intelligence_routes.py`
- Modify: `backend/app/hr/importers.py`
- Modify: `backend/app/hr/import_cli.py`
- Create: `backend/tests/test_hr_position_intelligence_api.py`
- Modify: `backend/tests/test_hr_position_importers.py`
- Modify: `backend/tests/test_hr_position_import_cli.py`

**Interfaces:**
- Produces: `build_position_intelligence_router(service, require_hr_access)` and complete official-version import

- [ ] **Step 1: Write RED tests**

```python
def test_official_import_preserves_duty_requirement_and_hash():
    projected = project_official_jobs(SNAPSHOT, REPOSITORY, OWNER, REQUEST)
    assert projected[0].official_version.duty == "Build the system."
    assert projected[0].official_version.requirement == "Test the system."

def test_context_api_returns_current_history_drafts_and_diff(client):
    assert client.get(f"/api/hr/positions/{POSITION}/context").status_code == 200
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_importers.py tests/test_hr_position_import_cli.py tests/test_hr_position_intelligence_api.py`

Expected: FAIL because complete facts and routes are absent.

- [ ] **Step 3: Implement exact APIs and import replay**

Expose `GET context`, `GET context/versions`, `POST context/drafts`, `POST context/drafts/{id}/confirm`, and `GET context/compare`. Keep dry-run free of JD content and secrets.

- [ ] **Step 4: Run GREEN and commit**

Run the Step 2 command. Expected: PASS.

```bash
git add backend/app/hr/position_intelligence_routes.py backend/app/hr/importers.py backend/app/hr/import_cli.py backend/tests/test_hr_position_intelligence_api.py backend/tests/test_hr_position_importers.py backend/tests/test_hr_position_import_cli.py
git commit -m "feat(hr): import and serve complete position facts"
```

### Task 4: Build the immutable HR task envelope

**Files:**
- Create: `backend/app/hr/task_context.py`
- Create: `backend/tests/test_hr_task_context.py`

**Interfaces:**
- Consumes: `PositionIntelligenceService`, R1.1 position/material bindings, `CandidateEnvelopeProvider`
- Produces: `HrTaskContextProvider.build_for_turn(owner_id, conversation_id, turn_id)`

- [ ] **Step 1: Write RED tests for exact inputs and fail-closed behavior**

```python
def test_envelope_pins_position_context_materials_and_candidate_fragment(provider):
    envelope = provider.build_for_turn(OWNER, CONVERSATION, TURN)
    assert envelope.position_id == POSITION
    assert envelope.context_version_id == CONTEXT
    assert envelope.material_attachment_ids == (MATERIAL,)
    assert envelope.canonical_sha256 == canonical_hash(envelope)
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_task_context.py`

Expected: FAIL because the provider does not exist.

- [ ] **Step 3: Implement provider with bounded canonical serialization**

Reject unready, expired, erased, unselected, cross-owner, and cross-position materials. Persist the same envelope identity to `PositionTaskRecord` before relay dispatch.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_task_context.py
git add app/hr/task_context.py tests/test_hr_task_context.py
git commit -m "feat(hr): build immutable position task context"
```

Expected: PASS.

### Task 5: Inject the envelope into Conversation execution and recovery

**Files:**
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/tests/test_agent_brain_conversation_context.py`
- Create: `backend/tests/test_hr_task_context_recovery.py`

**Interfaces:**
- Consumes: `HrTaskContextProvider`
- Produces: relay prompt/context pinned for first execution and recovery

- [ ] **Step 1: Write RED integration tests**

```python
def test_hr_turn_includes_one_position_envelope_and_recovery_reuses_it(orchestrator):
    first = orchestrator.advance_pending(1)
    recovered = orchestrator.advance_pending(1)
    assert first == 1
    assert recovered == 1
    assert relay_payloads[0]["hr_context_sha256"] == relay_payloads[1]["hr_context_sha256"]
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_agent_brain_conversation_context.py tests/test_hr_task_context_recovery.py`

Expected: FAIL because conversation execution has no HR provider.

- [ ] **Step 3: Compose only verified HR direct conversations**

Extend `ConversationContext` with `hr_position_context: HrPositionContextEnvelope | None`; build it only for a Position-bound `direct_agent_id == "hr-bot"` conversation. Store and replay the canonical envelope without rereading a newer context version.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_agent_brain_conversation_context.py tests/test_hr_task_context_recovery.py
git add app/agent_brain/conversation_context.py app/agent_brain/orchestrator.py tests/test_agent_brain_conversation_context.py tests/test_hr_task_context_recovery.py
git commit -m "feat(hr): inject position context into Agent execution"
```

Expected: PASS.

### Task 6: Verify the position subsystem

**Files:**
- Modify: only the files listed in Tasks 1–5 when a demonstrated failure requires it

- [ ] **Step 1: Run focused and regression suites**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_*.py tests/test_hr_task_context*.py tests/test_agent_brain_conversation_context.py`

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run: `cd backend && ./.venv/bin/python -m compileall -q app && ./.venv/bin/ruff check --select I app/hr tests/test_hr_*.py && git diff --check`

Expected: PASS.
