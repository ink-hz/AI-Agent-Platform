# HR Agent R1.2 Business-Usable Release Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the position-intelligence, candidate-intelligence, and workbench-resource plans into one business-usable HR R1.2 release.

**Architecture:** Three subsystem plans develop against frozen boundaries in separate worktrees. Position intelligence owns migration 069 and the immutable HR context envelope; candidate intelligence owns migration 070 and consumes the confirmed position context identity; workbench/resources consumes both APIs and does not infer business scope client-side. A final integration task mounts routers, updates the migration ceiling, runs cross-domain acceptance, and produces release evidence. Migration numbers were advanced after master assigned 067 to the access-history subject index and 068 to the authentication rollback window.

**Tech Stack:** PostgreSQL migrations, Python 3.11, FastAPI, psycopg 3, Pydantic, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- R1.2 is one business release; do not publish a half-complete subsystem.
- Do not build ATS stages, scheduling, Offer, onboarding, auto-contact, auto-rejection, or external recruiting-system adapters.
- Existing Conversation, Turn, long-task recovery, Attachment, ArtifactVersion, feedback, and HR MetaBot relay remain authoritative.
- Every write uses an idempotency key; confirmations also use a baseline identity and optimistic row version.
- Cross-owner, cross-position, and cross-candidate resources are returned as not found.
- AI may create drafts and analysis versions; only a human-authenticated action creates confirmed context or HumanFeedback.
- Candidate documents reuse Attachment Service bytes, one-year retention, erasure, preview, and download boundaries.
- Preserve `backend/.venv`; never stage or delete it.
- Do not modify shared Nginx, `/office/`, FAE, VOC, Marketing, administrative bots, or unrelated services.
- Production publish and production data apply require separate authorization and the existing disk/release gate.

---

## Frozen Ownership Map

| Domain | Owned files | Forbidden overlap |
|---|---|---|
| Position intelligence | migration `069_*`; `hr/position_intelligence_*.py`; conversation-context/orchestrator extension | `main.py`; candidate modules; React workspace |
| Candidate intelligence | migration `070_*`; `hr/candidate_*.py` | position migration/modules; React workspace |
| Workbench/resources | `hr/resource_*.py`; HR React components, types, and API clients | migrations 069/070; Agent orchestrator |
| Final integration | `main.py`, router composition, migration ceiling, acceptance/release docs | domain behavior already reviewed in subsystem commits |

Each subsystem adds its migration contract assertions to a domain-specific test file. Only final integration changes the shared contiguous-version ceiling in `backend/tests/test_control_plane_migration.py`.

## Frozen Cross-Subsystem Interfaces

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

class CandidateEnvelopeProvider(Protocol):
    def for_task(
        self,
        owner_id: UUID,
        position_id: UUID,
        candidate_id: UUID | None,
        position_candidate_id: UUID | None,
    ) -> CandidateEnvelopeFragment: ...

class HrTaskContextProvider(Protocol):
    def build_for_turn(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> HrPositionContextEnvelope: ...
```

The position subsystem supplies `HrTaskContextProvider`; the candidate subsystem supplies `CandidateEnvelopeProvider`.
The final composition injects the candidate provider into the position provider. The conversation builder only calls the HR provider when `direct_agent_id == "hr-bot"` and the conversation has a verified Position binding.

Frontend route names are frozen as:

```ts
type HrPositionSection = "chat" | "context" | "candidates" | "artifacts";

type HrTaskKind =
  | "jd"
  | "jr"
  | "talent_profile"
  | "sourcing_strategy"
  | "position_interview_plan"
  | "candidate_match"
  | "candidate_interview_plan"
  | "candidate_comparison";
```

## Dependency Order

```text
Position migration/model/repository ─┐
                                      ├─ Agent context integration ─┐
Candidate migration/model/repository ┘                             │
                                                                    ├─ R1.2 acceptance
Resource APIs ───────────────────────── Workbench UI ───────────────┘
```

Position, candidate, and resource/UI implementation starts in parallel. Agent context composition waits only for the two provider interfaces, while the React workbench uses typed fake clients until the APIs merge.

### Task 1: Create isolated subsystem worktrees

**Files:**
- No product files modified

**Interfaces:**
- Consumes: local `feat/hr-usable-workbench-r12` at the approved-plan commit
- Produces: three non-overlapping feature branches and worktrees

- [ ] **Step 1: Verify the integration worktree is clean**

Run: `git status --short --branch`

Expected: branch `feat/hr-usable-workbench-r12` with only the three approved plan files and no product-code modifications.

- [ ] **Step 2: Create the position worktree**

Run: `git worktree add .worktrees/hr-r12-position -b feat/hr-r12-position feat/hr-usable-workbench-r12`

Expected: a new worktree on `feat/hr-r12-position`.

- [ ] **Step 3: Create the candidate worktree**

Run: `git worktree add .worktrees/hr-r12-candidate -b feat/hr-r12-candidate feat/hr-usable-workbench-r12`

Expected: a new worktree on `feat/hr-r12-candidate`.

- [ ] **Step 4: Create the workbench worktree**

Run: `git worktree add .worktrees/hr-r12-workbench -b feat/hr-r12-workbench feat/hr-usable-workbench-r12`

Expected: a new worktree on `feat/hr-r12-workbench`.

### Task 2: Execute and review subsystem plans

**Files:**
- Read: `docs/superpowers/plans/2026-09-04-hr-r12-position-context.md`
- Read: `docs/superpowers/plans/2026-09-04-hr-r12-candidate-intelligence.md`
- Read: `docs/superpowers/plans/2026-09-04-hr-r12-workbench-resources.md`

**Interfaces:**
- Consumes: frozen interfaces in this plan
- Produces: reviewed subsystem heads with focused test evidence

- [ ] **Step 1: Dispatch one implementation worker per subsystem**

Each worker must use `test-driven-development`, record RED before implementation, commit only owned files, preserve `.venv`, and return focused test output plus the final commit SHA.

- [ ] **Step 2: Run specification review on each subsystem**

Expected: every requirement in its plan is either implemented or explicitly rejected before merge; no Critical or Important issue remains.

- [ ] **Step 3: Run code-quality review on each subsystem**

Expected: owner isolation, idempotency replay, exact version pinning, attachment authorization, error recovery, and no ATS fields are verified from code and tests.

### Task 3: Merge domain branches and compose application wiring

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/hr/routes.py`
- Modify: `backend/app/hr/import_cli.py`
- Modify: `backend/tests/test_main.py`
- Modify: `backend/tests/test_control_plane_migration.py`
- Modify: `backend/tests/test_hr_position_import_cli.py`
- Modify: `webui/src/App.tsx`
- Test: `backend/tests/test_hr_r12_integration.py`
- Test: `webui/src/workspaces/hr/HrR12.acceptance.test.tsx`

**Interfaces:**
- Consumes: `build_position_intelligence_router`, `build_candidate_router`, `build_hr_resource_router`, `HrTaskContextProvider`, and the frozen frontend route/task types
- Produces: one mounted R1.2 application

- [ ] **Step 1: Write failing composition tests**

Add tests proving the application mounts all three routers only when the writable Control Plane dependencies exist, migration max is 70, HR position turns receive one pinned envelope, and `/hr/positions/:id/{context,candidates,artifacts}` routes restore after refresh.

- [ ] **Step 2: Run the composition tests to record RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_main.py tests/test_control_plane_migration.py tests/test_hr_r12_integration.py`

Expected: FAIL because the new routers and migration ceiling are not composed.

Run: `cd webui && npm test -- --run src/router.test.ts src/workspaces/hr/HrR12.acceptance.test.tsx`

Expected: FAIL because the R1.2 route sections are not mounted.

- [ ] **Step 3: Merge position, candidate, and workbench branches without squashing**

Run from the integration worktree:

```bash
git merge --no-ff feat/hr-r12-position
git merge --no-ff feat/hr-r12-candidate
git merge --no-ff feat/hr-r12-workbench
```

Expected: only planned router/wiring conflicts; resolve by retaining every domain router and using migration order 069 then 070 after master migrations 067 and 068.

- [ ] **Step 4: Implement the composition layer**

Mount the domain routers in `main.py`, compose `CandidateEnvelopeProvider` into `HrTaskContextProvider`, pass that provider into `ConversationContextBuilder`, and make `hr/routes.py` expose one stable HR router tree. Connect `discover_resource_bindings` and `apply_resource_bindings` to the import CLI dry-run/apply summary. Render the four route sections already added by the workbench branch.

- [ ] **Step 5: Run composition tests to GREEN**

Run the two commands from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit integration wiring**

```bash
git add backend/app/main.py backend/app/hr/routes.py backend/app/hr/import_cli.py backend/tests/test_main.py backend/tests/test_control_plane_migration.py backend/tests/test_hr_position_import_cli.py backend/tests/test_hr_r12_integration.py webui/src/App.tsx webui/src/workspaces/hr/HrR12.acceptance.test.tsx
git commit -m "feat(hr): integrate business-usable R1.2"
```

### Task 4: Run business acceptance and hardening

**Files:**
- Modify: `backend/app/hr/position_intelligence_service.py`
- Modify: `backend/app/hr/candidate_service.py`
- Modify: `backend/app/hr/resource_service.py`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionContextPanel.tsx`
- Modify: `webui/src/workspaces/hr/HrCandidateWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionResourcesPanel.tsx`
- Modify: `webui/src/hrR12Api.ts`
- Test: `backend/tests/test_hr_r12_acceptance.py`
- Test: `webui/src/workspaces/hr/HrR12.acceptance.test.tsx`

**Interfaces:**
- Consumes: composed R1.2 application
- Produces: the exact approved end-to-end business workflow

- [ ] **Step 1: Add backend acceptance for the approved workflow**

The test must seed one official position, three resume attachments, one parse failure, two confirmed candidates, two match analyses, one HumanFeedback correction, one comparison, one context update, and one cross-position attack. It must assert immutable input versions, per-file failure isolation, successful retry, old-version preservation, and not-found isolation.

- [ ] **Step 2: Add frontend acceptance for the approved workflow**

The test must navigate chat → context → candidates → artifacts, select materials, launch a quick task, restore an in-flight task after remount, retry one failed resume, confirm two candidates, open downloadable artifacts, append correction text, and compare the two candidates.

- [ ] **Step 3: Record RED, implement only demonstrated gaps, and reach GREEN**

Run:

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_r12_acceptance.py
cd ../webui
npm test -- --run src/workspaces/hr/HrR12.acceptance.test.tsx
```

Expected after fixes: PASS with no xfail or skipped R1.2 scenario.

- [ ] **Step 4: Run security and recovery regression sets**

Run:

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_*.py tests/test_conversation_attachment_binding.py tests/test_agent_brain_conversation_context.py tests/test_execution_relay_*.py tests/test_web_session_security.py
```

Expected: PASS.

- [ ] **Step 5: Commit acceptance hardening**

```bash
git add backend/tests/test_hr_r12_acceptance.py webui/src/workspaces/hr/HrR12.acceptance.test.tsx
git add backend/app/hr/position_intelligence_service.py backend/app/hr/candidate_service.py backend/app/hr/resource_service.py
git add webui/src/workspaces/hr/HrPositionWorkspace.tsx webui/src/workspaces/hr/HrPositionContextPanel.tsx webui/src/workspaces/hr/HrCandidateWorkspace.tsx webui/src/workspaces/hr/HrPositionResourcesPanel.tsx webui/src/hrR12Api.ts
git commit -m "test(hr): prove the R1.2 business workflow"
```

### Task 5: Verify, review, and prepare release evidence

**Files:**
- Create: `docs/operations/2026-09-04-hr-r12-release.md`
- Modify: `deploy/cloud/acceptance.sh`
- Test: `backend/tests/test_cloud_deployment.py`

**Interfaces:**
- Consumes: final integrated R1.2 head
- Produces: release-ready code and an unexecuted production runbook

- [ ] **Step 1: Add deployment acceptance assertions**

Require migration 070, `platform_hr` readiness, `/api/hr` authenticated health, HR deep-link shell, one Position read, and attachment download-ticket readiness. Do not modify Nginx or unrelated service checks.

- [ ] **Step 2: Run deployment tests to RED then GREEN**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_cloud_deployment.py`

Expected after the scoped acceptance change: PASS.

- [ ] **Step 3: Run final verification from the exact commit**

```bash
cd backend
./.venv/bin/python -m pytest -q
./.venv/bin/python -m compileall -q app
./.venv/bin/ruff check --select I app/hr tests/test_hr_*.py
cd ../webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Expected: all suites pass; only documented existing warnings remain.

- [ ] **Step 4: Request independent final review**

The review must report Critical, Important, and Minor findings, confirm the approved end-to-end workflow, and verify that no ATS or external integration surface was added. Resolve all Critical and Important findings before merge.

- [ ] **Step 5: Write the release record**

Record exact test counts, migration order, dry-run commands for official jobs/history/materials/artifacts, disk gates, staging cleanup, current plus two rollback releases, HTTP acceptance, and the explicit statement that production apply was not run without separate authorization.

- [ ] **Step 6: Commit release evidence**

```bash
git add deploy/cloud/acceptance.sh backend/tests/test_cloud_deployment.py docs/operations/2026-09-04-hr-r12-release.md
git commit -m "docs(hr): prepare R1.2 release evidence"
```
