# HR Position Spine R1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the trusted Position spine that imports official and historical jobs, binds HR conversations and reusable content to exactly one Position, and exposes the position-first HR workspace.

**Architecture:** Add a dedicated `platform_hr` control-database domain with immutable Position IDs, reviewable PositionDrafts, audited conversation/content bindings, and idempotent import commands. Keep Conversation, Attachment, and Artifact as their existing authorities; the HR domain stores references and enforces ownership rather than copying messages or file bytes.

**Tech Stack:** PostgreSQL control migrations, Python 3.11, psycopg 3, FastAPI, Pydantic v2, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- HR Agent is an AI recruiting intelligence layer, not an ATS and not a Beisen replacement.
- Do not add Beisen, OA, Liepin, BOSS, recruiting-stage, interview-scheduling, Offer, onboarding, auto-contact, auto-reject, or auto-hire fields or UI.
- Do not create placeholders, mocks, grey buttons, or speculative external-system adapters.
- Reuse existing Conversation, Turn, Attachment, Artifact, feedback, recovery, HR MetaBot, and `hr-jd-sync` capabilities.
- Do not replay, rewrite, migrate, or fabricate historical HR Turns.
- A Position has an immutable UUID; title and complete J number are never Platform primary keys.
- One Position may own many Conversations; one Conversation may bind to at most one Position.
- User uploads remain Conversation materials until the user explicitly promotes them to Position materials.
- Existing untracked files, especially `backend/.venv`, must not be added, deleted, stashed, or cleaned.

---

## File Map

- `backend/control_migrations/066_hr_position_spine.sql`: schema, tables, constraints, grants, and mutation functions.
- `backend/app/hr/models.py`: immutable domain records and strict command validation.
- `backend/app/hr/repository.py`: psycopg persistence and projection queries.
- `backend/app/hr/service.py`: idempotency, confirmation, merge, correction, and import orchestration.
- `backend/app/hr/routes.py`: owner-scoped HTTP API under `/api/hr`.
- `backend/app/hr/importers.py`: official snapshot validation and deterministic historical discovery.
- `backend/app/hr/context.py`: position scope lookup used by the conversation runtime.
- `webui/src/hrApi.ts`, `webui/src/hrTypes.ts`: strict browser contract and transport.
- `webui/src/workspaces/hr/HrPositionIndex.tsx`: unified position and draft entry page.
- `webui/src/workspaces/hr/HrPositionWorkspace.tsx`: position-scoped conversation shell.
- Existing Conversation and Attachment files receive only narrow integration hooks.

### Task 1: PostgreSQL HR Position Spine

**Files:**
- Create: `backend/control_migrations/066_hr_position_spine.sql`
- Create: `backend/tests/test_hr_position_migration.py`

**Interfaces:**
- Produces tables `platform_hr.positions`, `position_drafts`, `position_conversations`, `position_materials`, `position_artifacts`, `position_import_evidence`.
- Produces SQL functions `platform_hr.create_position_v66`, `confirm_position_draft_v66`, `bind_conversation_v66`, `promote_material_v66`, and `link_artifact_v66`.

- [ ] **Step 1: Write failing migration-contract tests**

Assert exact UUID primary keys, owner-scoped unique constraints, draft states, single-conversation binding, source evidence, cross-schema foreign keys, no ATS/external-system columns, least-privilege grants, and revoked PUBLIC access.

```python
def test_hr_position_migration_has_single_conversation_binding() -> None:
    sql = MIGRATION.read_text().lower()
    assert "conversation_id uuid primary key" in sql
    assert "references platform_control.conversations(conversation_id)" in sql
    assert "unique (position_id,owner_internal_user_id)" in sql
```

- [ ] **Step 2: Run the contract tests and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_migration.py`

Expected: failure because migration 065 does not exist.

- [ ] **Step 3: Implement migration 065**

Use `uuid` keys, `timestamptz`, exact enum checks, `row_version bigint`, ownership composite foreign keys, and security-definer functions that validate `session_user`, owner, source type, idempotency UUID, and current row version. Revoke every table/function from PUBLIC and grant only the existing app/brain migration roles needed by each call path.

- [ ] **Step 4: Run migration and control-plane tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_migration.py tests/test_control_plane_migration.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/066_hr_position_spine.sql backend/tests/test_hr_position_migration.py
git commit -m "feat(hr): add position spine schema"
```

### Task 2: Domain Models and Validation

**Files:**
- Create: `backend/app/hr/__init__.py`
- Create: `backend/app/hr/models.py`
- Create: `backend/tests/test_hr_position_models.py`

**Interfaces:**
- Produces `PositionRecord`, `PositionDraftRecord`, `PositionSummary`, `PositionDetail`.
- Produces commands `CreateManualPosition`, `ProposePositionDraft`, `ConfirmPositionDraft`, `BindPositionConversation`, `PromotePositionMaterial`.

- [ ] **Step 1: Write failing model tests**

Cover trimmed non-empty titles, `official_site/manual` sources, `draft/active/archived` internal state, official status separation, full `^J[0-9]{4,12}$` identifiers, bounded evidence, exact draft states, and rejection of booleans as row versions.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_models.py`

- [ ] **Step 3: Implement frozen dataclasses and Pydantic request models**

Keep public serialization explicit; do not expose encrypted database columns or internal SQL errors.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_models.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr backend/tests/test_hr_position_models.py
git commit -m "feat(hr): define position domain contracts"
```

### Task 3: Owner-Scoped Repository

**Files:**
- Create: `backend/app/hr/repository.py`
- Create: `backend/tests/test_hr_position_repository.py`

**Interfaces:**
- Produces `HrPositionRepository.list_positions(owner_id, query, source, internal_status, cursor, limit)`.
- Produces `position_for_owner`, `list_drafts`, `propose_draft`, `confirm_draft`, `bind_conversation`, `promote_material`, `link_artifact`.
- Raises `HrNotFound`, `HrConflict`, `HrUnavailable`, never raw `psycopg.Error`.

- [ ] **Step 1: Write failing repository tests against the live control fixture**

Prove owner isolation, cursor stability, idempotent draft proposal, atomic draft confirmation, exact one-position conversation binding, audited correction, material ownership checks, and artifact source preservation.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_repository.py`

- [ ] **Step 3: Implement focused repository methods**

Use `dict_row`, 3-second connect timeout, 10-second statement timeout, explicit transaction boundaries, and stored mutation functions from Task 1.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_repository.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/repository.py backend/tests/test_hr_position_repository.py
git commit -m "feat(hr): persist owner scoped positions"
```

### Task 4: Position Commands and Draft Lifecycle

**Files:**
- Create: `backend/app/hr/service.py`
- Create: `backend/tests/test_hr_position_service.py`

**Interfaces:**
- Produces `HrPositionService.create_manual`, `propose_draft`, `confirm_draft`, `merge_draft`, `dismiss_draft`, `bind_conversation`, `correct_conversation_binding`.

- [ ] **Step 1: Write failing service tests**

Test replay-safe request IDs, double confirmation returning the same Position, merge requiring a visible target Position, dismissal preserving evidence, and stale `row_version` producing a conflict projection.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_service.py`

- [ ] **Step 3: Implement orchestration without database bypasses**

The service validates commands and delegates every write to repository transactions. It never edits Conversation messages or attachment bytes.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_service.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/service.py backend/tests/test_hr_position_service.py
git commit -m "feat(hr): manage position draft lifecycle"
```

### Task 5: Official Projection and Historical Discovery

**Files:**
- Create: `backend/app/hr/importers.py`
- Create: `backend/tests/test_hr_position_importers.py`
- Create: `backend/app/hr/import_cli.py`
- Create: `backend/tests/test_hr_position_import_cli.py`

**Interfaces:**
- Produces `OfficialJobSnapshot.parse(bytes)`, `project_official_jobs(snapshot, repository, request_id)`.
- Produces `discover_historical_positions(conversations, rule_version)` returning evidence-backed exact matches and drafts.

- [ ] **Step 1: Write failing importer tests**

Use sanitized fixtures to prove complete J-number validation, duplicate snapshot replay, title changes without new Positions, missing jobs preserving last state, exact J auto-linking, ambiguous titles becoming drafts, and multi-position Conversations remaining unbound.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_importers.py tests/test_hr_position_import_cli.py`

- [ ] **Step 3: Implement strict import envelopes and deterministic discovery**

Accept only the published `hr-jd-sync` registry fields. Historical discovery reads HR Conversation title/message projections, records message sequence evidence, and never invokes or replays an Agent Turn.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_importers.py tests/test_hr_position_import_cli.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/importers.py backend/app/hr/import_cli.py backend/tests/test_hr_position_importers.py backend/tests/test_hr_position_import_cli.py
git commit -m "feat(hr): import existing position sources"
```

### Task 6: HR HTTP API and Authorization

**Files:**
- Create: `backend/app/hr/routes.py`
- Create: `backend/tests/test_hr_position_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- `GET /api/hr/positions`
- `GET /api/hr/positions/{position_id}`
- `GET /api/hr/position-drafts`
- `POST /api/hr/position-drafts`
- `POST /api/hr/position-drafts/{draft_id}/confirm|merge|dismiss`
- `POST /api/hr/positions/{position_id}/conversations/{conversation_id}`
- `POST /api/hr/positions/{position_id}/materials/{attachment_id}`
- `DELETE /api/hr/positions/{position_id}/materials/{attachment_id}`

- [ ] **Step 1: Write failing route tests**

Test no-store private headers, CSRF on every mutation, hard-stale read-only rejection, current HR Agent authorization, owner isolation, strict request bodies, 404 concealment, 409 versions, and 503 fail-closed behavior.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_api.py`

- [ ] **Step 3: Implement a router factory and wire dependencies in `create_app`**

The factory receives `HrPositionService` and `AgentUseAuthorization`; it permits only owners currently authorized for `hr-bot`. Do not add a separate HR permission universe.

- [ ] **Step 4: Run route and main tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_api.py tests/test_main.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/routes.py backend/app/main.py backend/tests/test_hr_position_api.py
git commit -m "feat(hr): expose authorized position API"
```

### Task 7: Conversation, Material, and Artifact Integration

**Files:**
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_service.py`
- Modify: `backend/app/attachments/artifact_service.py`
- Create: `backend/app/hr/context.py`
- Create: `backend/tests/test_hr_position_conversation_binding.py`

**Interfaces:**
- Adds optional strict `position_id` and `position_draft_id` fields to HR direct-conversation creation only.
- Produces `HrPositionScope.for_conversation(owner_id, conversation_id)`.
- Artifact registration links successful output to the bound Position without changing artifact authority.

- [ ] **Step 1: Write failing integration tests**

Prove a new existing-position Conversation is not returned successfully until binding exists, draft confirmation binds its originating Conversation, non-HR Agents reject HR scope fields, failed/replayed requests do not duplicate bindings, and output artifacts retain Turn provenance.

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_conversation_binding.py`

- [ ] **Step 3: Implement the narrow scope hook**

Use the Conversation idempotency key as the binding idempotency key. On a transient bind failure, a retry resolves the same Conversation and completes the missing binding; unbound partial results are excluded from the position view.

- [ ] **Step 4: Run and confirm GREEN plus regression tests**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_conversation_binding.py tests/test_agent_brain_conversation_api.py tests/test_conversation_attachment_api.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/context.py backend/app/agent_brain/conversation_routes.py backend/app/agent_brain/conversation_service.py backend/app/attachments/artifact_service.py backend/tests/test_hr_position_conversation_binding.py
git commit -m "feat(hr): bind conversations and results to positions"
```

### Task 8: Browser API Contract

**Files:**
- Create: `webui/src/hrTypes.ts`
- Create: `webui/src/hrApi.ts`
- Create: `webui/src/hrApi.test.ts`

**Interfaces:**
- Produces `HrApi.listPositions`, `position`, `listDrafts`, `proposeDraft`, `confirmDraft`, `mergeDraft`, `dismissDraft`, `bindConversation`, `promoteMaterial`, `removeMaterial`.

- [ ] **Step 1: Write failing transport and parser tests**

Test encoded filters, credentials, AbortSignal, CSRF, UUID/status/source validation, malformed response rejection, and 409 conflict preservation.

- [ ] **Step 2: Run and confirm RED**

Run: `cd webui && npm test -- --run src/hrApi.test.ts`

- [ ] **Step 3: Implement strict types, parsers, and transport**

Reuse `platformPath`; never trust arbitrary response values through unchecked type assertions.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd webui && npm test -- --run src/hrApi.test.ts`

- [ ] **Step 5: Commit**

```bash
git add webui/src/hrTypes.ts webui/src/hrApi.ts webui/src/hrApi.test.ts
git commit -m "feat(hr): add position browser API"
```

### Task 9: Unified Position and Draft Entry Page

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionIndex.tsx`
- Create: `webui/src/workspaces/hr/HrPositionIndex.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/platform/workspaces.ts`

**Interfaces:**
- `/agents/hr-bot` becomes the position index.
- `/agents/hr-bot/positions/{position_id}` opens the position workspace.
- Existing `/agents/hr-bot/conversations/{conversation_id}` remains compatible for unbound historical links.

- [ ] **Step 1: Write failing UI and routing tests**

Cover official/manual/draft grouping, search, sync-health freshness, empty/error/retry states, keyboard navigation, confirm/merge/dismiss actions, no Candidate or external-system placeholders, and stable deep links.

- [ ] **Step 2: Run and confirm RED**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionIndex.test.tsx src/router.test.ts`

- [ ] **Step 3: Implement the position-first index**

Use semantic buttons/links, visible focus, minimum 11.5px text, existing calm HR palette, and real API state only.

- [ ] **Step 4: Run and confirm GREEN**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionIndex.test.tsx src/router.test.ts`

- [ ] **Step 5: Commit**

```bash
git add webui/src/workspaces/hr/HrPositionIndex.tsx webui/src/workspaces/hr/HrPositionIndex.test.tsx webui/src/workspaces/hr/HrWorkspacePage.tsx webui/src/router.ts webui/src/platform/workspaces.ts
git commit -m "feat(hr): add unified position entry"
```

### Task 10: Position-Scoped Conversation Workspace

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Create: `webui/src/workspaces/hr/HrPositionWorkspace.test.tsx`
- Modify: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Modify: `webui/src/components/conversation/SessionMaterialsDrawer.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Accepts exact `positionId`, scopes history to that Position, and passes `position_id` on new Conversation creation.
- Adds explicit “设为岗位材料/移出岗位材料” actions while retaining Conversation attachment controls.

- [ ] **Step 1: Write failing workspace tests**

Cover position header facts, only bound Conversation history, default chat focus, three-column responsive layout, explicit material promotion, downloadable artifacts, archived Conversation behavior, hard-stale read-only mode, and no cross-position rendering.

- [ ] **Step 2: Run and confirm RED**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx`

- [ ] **Step 3: Implement the smallest reusable scope extension**

Keep Marketing and other direct Agent behavior unchanged. HR-specific presentation belongs under `workspaces/hr`, not conditional branches scattered through generic components.

- [ ] **Step 4: Run and confirm GREEN plus shared regressions**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx src/styles.test.ts`

- [ ] **Step 5: Commit**

```bash
git add webui/src/workspaces/hr/HrPositionWorkspace.tsx webui/src/workspaces/hr/HrPositionWorkspace.test.tsx webui/src/workspaces/direct/DirectAgentWorkspace.tsx webui/src/components/conversation/SessionMaterialsDrawer.tsx webui/src/styles.css
git commit -m "feat(hr): deliver position scoped conversations"
```

### Task 11: R1.1 Acceptance, Operations, and Full Verification

**Files:**
- Create: `backend/tests/test_hr_position_spine_acceptance.py`
- Create: `webui/src/workspaces/hr/HrPositionSpine.acceptance.test.tsx`
- Create: `docs/operations/2026-09-04-hr-position-spine-r11-release.md`

**Interfaces:**
- Produces executable evidence for every R1.1 acceptance rule and a deployment checklist that does not authorize deployment.

- [ ] **Step 1: Add cross-boundary acceptance tests**

Cover official import replay, historical exact/ambiguous/multi-position cases, draft-to-Position conversion, one-position Conversation binding, explicit material promotion, artifact provenance/download, owner isolation, refresh recovery, and forbidden ATS placeholders.

- [ ] **Step 2: Run all HR-focused verification**

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_hr_position_*.py
cd ../webui && npm test -- --run src/hrApi.test.ts src/workspaces/hr
```

Expected: all pass.

- [ ] **Step 3: Run repository-wide verification**

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../webui && npm test -- --run && npm run build
cd .. && git diff --check
```

Expected: all backend and frontend tests pass, production build succeeds, no whitespace errors.

- [ ] **Step 4: Record exact verification and release boundaries**

The operations note records commit, test counts, migrations, feature flags, rollback scope, historical discovery dry run, and explicitly states that production deployment needs a separate disk/preflight authorization.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_hr_position_spine_acceptance.py webui/src/workspaces/hr/HrPositionSpine.acceptance.test.tsx docs/operations/2026-09-04-hr-position-spine-r11-release.md
git commit -m "test(hr): verify position spine release"
```

## Subsequent Plans

R1.2 and R1.3 are intentionally separate plans because each introduces an independently reviewable subsystem:

- R1.2: versioned PositionContext, confirmation, role-calibration import, Agent context injection, JD/JR/profile artifacts.
- R1.3: TalentSource, public job snapshots, change detection, resumable public research, insights, and periodic meaningful-change summaries.

R1.1 is not marketed or declared as the completed product first phase. Product first phase is complete only after all three plans pass their acceptance suites.
