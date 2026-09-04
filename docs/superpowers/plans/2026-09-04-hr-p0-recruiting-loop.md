# HR P0 Recruiting Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one durable HR flow from a natural-language recruiting conversation through confirmed Position, JD/JR, resume parsing, candidate match analysis, and a downloadable candidate-specific interview-question PDF.

**Architecture:** Extend the existing HR Conversation, PositionDraft, PositionContextVersion, Candidate, PositionCandidate, Task, Attachment, Artifact, and Flywheel foundations. Human-readable Agent Markdown carries a bounded hidden canonical envelope; projectors validate that envelope into versioned HR objects while the visible answer remains normal Markdown. Confirmation is the only transition into formal Position or Candidate data.

**Tech Stack:** Python 3, FastAPI, PostgreSQL/PLpgSQL, psycopg 3, Pydantic/dataclasses, React 19, TypeScript, Vitest, pytest, existing Agent Brain/MetaBot and Attachment/Artifact services.

## Global Constraints

- Base implementation on commit `e2f701b` or its descendant.
- Preserve the existing wide, chat-first HR workspace and the composer attachment placement.
- Do not reimplement Conversation, Attachment, Artifact, Position, Candidate, Task, Flywheel, or authorization foundations.
- AI output remains a draft until the owner explicitly confirms it.
- Mock resumes are test fixtures only and must never appear as a product feature.
- Candidate analysis must distinguish evidence, gaps, risks, and unknowns; absence of evidence is not evidence of absence.
- Candidate interview questions must be downloadable as a PDF and bound to the exact PositionContextVersion, Candidate, and CandidateDocument versions.
- Do not add ATS workflow, candidate stage transitions, scheduling, Offer, onboarding, automated contact, interview transcription, or candidate comparison.
- Use idempotency keys for every mutation and owner scope for every read and write.
- Never log raw resume content, candidate facts, hidden envelopes, signed download tickets, or object-store paths.
- Preserve user-owned untracked files and `backend/.venv`; do not add or delete them.

---

### Task 1: Freeze and parse the HR structured-output envelope

**Files:**
- Create: `backend/app/hr/structured_output.py`
- Create: `backend/tests/test_hr_structured_output.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/tests/test_agent_brain_conversation_context.py`
- Modify: `backend/tests/test_agent_brain_orchestrator.py`

**Interfaces:**
- Produces: `encode_hr_envelope(kind: str, payload: Mapping[str, object]) -> str`.
- Produces: `extract_hr_envelope(markdown: str, expected_kind: str) -> HrStructuredEnvelope | None`.
- Produces: `HR_WORKFLOW_CONTRACT_V1`, injected only into direct `hr-bot` task context.
- Envelope syntax: `<!-- platform-hr-v1:<unpadded-base64url-canonical-json> -->`.

- [ ] **Step 1: Write failing parser tests**

```python
def test_position_package_round_trips_without_changing_visible_markdown():
    payload = {
        "title": "高级结构工程师",
        "modules": {
            "mission": {"text": "负责喷嘴与挤出系统"},
            "jd": {"text": "负责喷嘴与挤出系统结构设计。"},
            "jr": {"text": "具备五年以上精密结构量产经验。"},
        },
    }
    suffix = encode_hr_envelope("position_package", payload)
    parsed = extract_hr_envelope(f"岗位方案如下。\n\n{suffix}", "position_package")
    assert parsed is not None
    assert parsed.payload == payload
    assert parsed.visible_markdown == "岗位方案如下。"
```

Also reject padding, duplicate envelopes, unknown keys, decoded payloads over 512 KiB, invalid UTF-8, a kind mismatch, and Position packages missing exactly `mission`, `jd`, or `jr`.

- [ ] **Step 2: Run the parser test and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_structured_output.py -q`  
Expected: FAIL because `app.hr.structured_output` does not exist.

- [ ] **Step 3: Implement the bounded canonical envelope**

```python
@dataclass(frozen=True, slots=True)
class HrStructuredEnvelope:
    kind: str
    payload: Mapping[str, object]
    visible_markdown: str

def extract_hr_envelope(markdown: str, expected_kind: str) -> HrStructuredEnvelope | None:
    """Return None when no envelope exists; reject malformed or ambiguous envelopes."""
```

Canonical JSON must have exactly `schema_version`, `kind`, and `payload`; `schema_version` equals `1`. Validate `position_package`, `candidate_match`, and `candidate_interview_plan` with separate exact-key validators.

- [ ] **Step 4: Inject the contract only into HR Agent calls**

Extend `ConversationContext` with `hr_workflow_contract: str | None`. In `ConversationContextBuilder._load`, set it to `HR_WORKFLOW_CONTRACT_V1` only when `mode == "direct_agent"` and `direct_agent_id == "hr-bot"`. In the orchestrator objective document add:

```python
if user_request.hr_workflow_contract is not None:
    sections["hr_workflow_contract"] = user_request.hr_workflow_contract
```

The contract instructs the Agent to emit a `position_package` envelope only after it has supplied a complete, human-readable岗位需求/JD/JR answer; ordinary questions and clarification turns contain no envelope.

- [ ] **Step 5: Run focused and regression tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_structured_output.py tests/test_agent_brain_conversation_context.py tests/test_agent_brain_orchestrator.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/hr/structured_output.py backend/app/agent_brain/conversation_context.py backend/app/agent_brain/orchestrator.py backend/tests/test_hr_structured_output.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_agent_brain_orchestrator.py
git commit -m "feat(hr): define structured recruiting outputs"
```

### Task 2: Persist versioned Position packages and confirm them atomically

**Files:**
- Create: `backend/control_migrations/076_hr_position_packages.sql`
- Create: `backend/tests/test_hr_position_package_migration.py`
- Create: `backend/tests/test_hr_position_package_database.py`
- Modify: `backend/app/hr/models.py`
- Modify: `backend/app/hr/repository.py`
- Modify: `backend/app/hr/service.py`
- Modify: `backend/tests/test_hr_position_models.py`
- Modify: `backend/tests/test_hr_position_repository.py`
- Modify: `backend/tests/test_hr_position_service.py`

**Interfaces:**
- Produces: immutable `platform_hr.position_draft_versions`.
- Produces: `create_position_draft_version_v76` and `confirm_position_package_v76` app-role-only functions.
- Produces: `PositionDraftVersion` and `CreatePositionDraftVersion`.
- Produces: `HrPositionService.confirm_package -> ConfirmedPositionPackage`.

- [ ] **Step 1: Write static and real-database migration tests**

Require columns for owner, draft, version number, title, `modules`, source conversation/turn/message, Agent/model provenance, row version, and timestamps. Require one version per `owner + draft + source_assistant_message_id`, exact module keys, foreign keys, app-only execution, and deterministic replay.

```python
assert {
    "position_draft_versions",
    "create_position_draft_version_v76",
    "confirm_position_package_v76",
} <= migration_objects(sql)
```

- [ ] **Step 2: Run migration tests and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_package_migration.py tests/test_hr_position_package_database.py -q`  
Expected: FAIL because migration 076 is absent.

- [ ] **Step 3: Implement migration 076**

`confirm_position_package_v76` must lock the PositionDraft, require the selected draft version and expected row version, create one active manual Position, create confirmed PositionContextVersion v1 from the selected `mission/jd/jr` modules, bind the existing Conversation with `draft_confirmed`, mark the draft confirmed, and return all three identifiers in one transaction. Replay with the same client request returns the same result; a different payload raises a conflict.

- [ ] **Step 4: Add domain models and repository/service methods**

```python
@dataclass(frozen=True, slots=True)
class ConfirmedPositionPackage:
    position: PositionRecord
    context: PositionContextVersion
    conversation_id: UUID

def confirm_package(
    self, owner_id: UUID, draft_id: UUID, draft_version_id: UUID,
    request_id: UUID, *, expected_row_version: int,
) -> ConfirmedPositionPackage:
    return self._repository.confirm_package(
        owner_id, draft_id, draft_version_id, request_id,
        expected_row_version=expected_row_version,
    )
```

Validate exact owner scope and never fall back to the raw initial request as the final title or JD/JR.

- [ ] **Step 5: Run domain/database tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_package_migration.py tests/test_hr_position_package_database.py tests/test_hr_position_models.py tests/test_hr_position_repository.py tests/test_hr_position_service.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/076_hr_position_packages.sql backend/app/hr/models.py backend/app/hr/repository.py backend/app/hr/service.py backend/tests/test_hr_position_package_migration.py backend/tests/test_hr_position_package_database.py backend/tests/test_hr_position_models.py backend/tests/test_hr_position_repository.py backend/tests/test_hr_position_service.py
git commit -m "feat(hr): persist and confirm position packages"
```

### Task 3: Project completed recruiting conversations into PositionDraft versions

**Files:**
- Create: `backend/app/hr/position_package_projection.py`
- Create: `backend/tests/test_hr_position_package_projection.py`
- Modify: `backend/control_migrations/076_hr_position_packages.sql`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_hr_r12_integration.py`

**Interfaces:**
- Produces: `PositionPackageProjectionRepository.claim/complete/fail/release`.
- Produces: `PositionPackageProjector.reconcile_one() -> bool`.
- Consumes: `extract_hr_envelope(markdown, "position_package")` and `HrPositionService.create_draft_version`.

- [ ] **Step 1: Write failing projector tests**

Cover a free HR Conversation with a complete envelope, an explicit PositionDraft-scoped Conversation, clarification turns without envelopes, duplicate result replay, malformed envelope failure isolation, cross-owner rejection, and processing continuation after one bad result.

```python
assert projector.reconcile_one() is True
version = positions.latest_draft_version(OWNER, DRAFT)
assert set(version.modules) == {"mission", "jd", "jr"}
assert version.source_assistant_message_id == ASSISTANT_MESSAGE
```

- [ ] **Step 2: Run and verify RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_package_projection.py -q`  
Expected: FAIL because the projector is absent.

- [ ] **Step 3: Add a leased projection ledger to migration 076**

The claim function selects completed, non-empty, direct `hr-bot` assistant messages. If the Conversation already belongs to a proposed PositionDraft, use it; otherwise create a deterministic `new_conversation` PositionDraft keyed by Conversation. A message with no envelope is marked skipped, not failed. Malformed envelopes are terminal for that message only.

- [ ] **Step 4: Implement and wire the projector**

```python
async def position_package_projection_loop(
    projector: PositionPackageProjector, *, idle_seconds: float = 0.5,
) -> None:
    while True:
        changed = await asyncio.to_thread(projector.reconcile_one)
        if not changed:
            await asyncio.sleep(idle_seconds)
```

Wire one loop in `main.py` under the existing HR identity/runtime gates. Do not add a second scheduler process.

- [ ] **Step 5: Run focused integration tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_package_projection.py tests/test_hr_r12_integration.py -q`  
Expected: PASS and `create_app` exposes one projector loop.

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/076_hr_position_packages.sql backend/app/hr/position_package_projection.py backend/app/main.py backend/tests/test_hr_position_package_projection.py backend/tests/test_hr_r12_integration.py
git commit -m "feat(hr): project conversation position packages"
```

### Task 4: Expose Position packages and atomic confirmation through the HR API

**Files:**
- Modify: `backend/app/hr/routes.py`
- Modify: `backend/tests/test_hr_position_api.py`
- Modify: `webui/src/hrTypes.ts`
- Modify: `webui/src/hrApi.ts`
- Modify: `webui/src/hrApi.test.ts`

**Interfaces:**
- Produces: `GET /api/hr/conversations/{conversation_id}/position-package`.
- Produces: `POST /api/hr/position-drafts/{draft_id}/versions/{draft_version_id}/confirm`.
- Produces: `HrPositionPackage` and `HrConfirmedPositionPackage` TypeScript types.

- [ ] **Step 1: Add failing API tests**

```python
response = client.get(f"/api/hr/conversations/{conversation_id}/position-package")
assert response.json()["modules"]["jd"]["text"] == "负责喷嘴与挤出系统结构设计。"

confirmed = client.post(
    f"/api/hr/position-drafts/{draft_id}/versions/{version_id}/confirm",
    headers={"Idempotency-Key": str(request_id), "X-CSRF-Token": "csrf"},
    json={"expected_row_version": 2},
)
assert confirmed.json()["conversation_id"] == str(conversation_id)
```

Require 404 when no package exists, 409 on stale confirmation, 403 cross-owner, and 503 read-only.

- [ ] **Step 2: Run backend and web parser tests RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_api.py -q && cd ../webui && npm test -- src/hrApi.test.ts`  
Expected: FAIL on missing routes/parsers.

- [ ] **Step 3: Implement strict API serialization and TypeScript parsing**

```ts
confirmPositionPackage(
  draftId: string,
  draftVersionId: string,
  expectedRowVersion: number,
  requestId: string,
  signal?: AbortSignal,
): Promise<HrConfirmedPositionPackage>
```

No raw database exceptions, encrypted content, hidden envelope, or artifact locator is returned.

- [ ] **Step 4: Run tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_position_api.py -q && cd ../webui && npm test -- src/hrApi.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/routes.py backend/tests/test_hr_position_api.py webui/src/hrTypes.ts webui/src/hrApi.ts webui/src/hrApi.test.ts
git commit -m "feat(hr): expose conversation position packages"
```

### Task 5: Render the Position package in chat and preserve the thread on confirmation

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionProposalCard.tsx`
- Create: `webui/src/workspaces/hr/HrPositionProposalCard.test.tsx`
- Create: `webui/src/workspaces/hr/HrConversationOutcomePanel.tsx`
- Create: `webui/src/workspaces/hr/HrConversationOutcomePanel.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `HrApi.positionPackage(conversationId)` and `HrApi.confirmPositionPackage`.
- Produces: a chat supplement card with tabs `岗位需求`, `JD`, `JR` and action `确认并加入岗位库`.
- Produces: SPA-only route transition while the same conversation remains mounted and visible.

- [ ] **Step 1: Write failing component tests**

```tsx
expect(await screen.findByRole("heading", { name: "岗位方案" })).toBeVisible();
await user.click(screen.getByRole("button", { name: "确认并加入岗位库" }));
expect(navigate).toHaveBeenCalledWith(`/hr/positions/${positionId}/conversations/${conversationId}`);
expect(screen.getByText("此前对话消息")).toBeVisible();
```

Also assert tabs, copy, download, retry after 409 refresh, no duplicated submit, no document reload, and no card on clarification turns.

- [ ] **Step 2: Run component tests RED**

Run: `cd webui && npm test -- src/workspaces/hr/HrPositionProposalCard.test.tsx src/workspaces/hr/HrConversationOutcomePanel.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx`  
Expected: FAIL because the components do not exist.

- [ ] **Step 3: Implement the card and stable chat host**

Keep one `DirectAgentWorkspace` host keyed by owner and Conversation, not by free-chat versus Position route. After confirmation, update the Position chrome and route around that host; do not clear message, composer, attachment, or stream state.

```tsx
<DirectAgentWorkspace
  key={`hr-chat:${account.internal_user_id}:${conversationId ?? "new"}`}
  threadSupplement={<HrConversationOutcomePanel conversationId={conversationId} />}
  {...sharedChatProps}
/>
```

- [ ] **Step 4: Add scoped styles**

Cards use the existing HR typography, contrast, message width, sticky composer, and FAE-style icon actions. Do not restore permanent multi-column panels or task-state cards.

- [ ] **Step 5: Run focused tests and build GREEN**

Run: `cd webui && npm test -- src/workspaces/hr/HrPositionProposalCard.test.tsx src/workspaces/hr/HrConversationOutcomePanel.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx && npm run build`  
Expected: tests PASS and Vite production build succeeds.

- [ ] **Step 6: Commit**

```bash
git add webui/src/workspaces/hr/HrPositionProposalCard.tsx webui/src/workspaces/hr/HrPositionProposalCard.test.tsx webui/src/workspaces/hr/HrConversationOutcomePanel.tsx webui/src/workspaces/hr/HrConversationOutcomePanel.test.tsx webui/src/workspaces/hr/HrWorkspacePage.tsx webui/src/workspaces/hr/HrWorkspacePage.test.tsx webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx webui/src/styles.css
git commit -m "feat(hr): confirm position packages from chat"
```

### Task 6: Store structured candidate analysis and bind interview PDFs

**Files:**
- Create: `backend/control_migrations/077_hr_candidate_analysis_artifacts.sql`
- Create: `backend/tests/test_hr_candidate_analysis_artifact_migration.py`
- Modify: `backend/app/hr/candidate_models.py`
- Modify: `backend/app/hr/candidate_repository.py`
- Modify: `backend/app/hr/task_service.py`
- Modify: `backend/app/hr/task_result_projection.py`
- Modify: `backend/tests/test_hr_candidate_models.py`
- Modify: `backend/tests/test_hr_candidate_repository.py`
- Modify: `backend/tests/test_hr_task_result_projection.py`
- Modify: `backend/tests/test_hr_task_result_projection_database.py`

**Interfaces:**
- Adds nullable `source_artifact_version_id` to `CandidateAnalysisVersion`.
- Candidate tasks consume `candidate_match` or `candidate_interview_plan` envelopes.
- `candidate_interview_plan` requires one ready `application/pdf` result artifact.

- [ ] **Step 1: Write failing contract and migration tests**

For candidate match require exact payload keys `summary`, `dimensions`, `evidence`, `gaps`, `risks`, `unknowns`, and `verification_questions`. For interview questions require `title`, `questions`, and each question's `verification_goal`, `candidate_reason`, `question`, `follow_ups`, `strong_evidence`, and `risk_signals`.

```python
assert projected.result["summary"] == "总体匹配"
assert projected.evidence == ({"resume_fact": "负责挤出系统"},)
assert projected.unknowns == ("量产良率经验待验证",)
assert projected.source_artifact_version_id == PDF_VERSION
```

- [ ] **Step 2: Run tests RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_candidate_analysis_artifact_migration.py tests/test_hr_task_result_projection.py tests/test_hr_task_result_projection_database.py -q`  
Expected: FAIL because structured projection and artifact binding are absent.

- [ ] **Step 3: Implement migration and model/repository changes**

Migration 077 adds the artifact-version foreign key and preserves immutability. Repository serialization includes it without exposing locators.

- [ ] **Step 4: Tighten task prompts and projection**

`candidate_match` asks for readable Markdown plus the exact hidden envelope. `candidate_interview_plan` additionally asks the HR Agent to create one PDF named `<岗位>-<候选人>-面试题-v<版本>.pdf` through the existing output-write grant. The projector rejects a completed interview task with a missing, non-ready, wrong-owner, wrong-task, or non-PDF artifact; it never stores `{ "text": ... }` as a completed structured analysis.

- [ ] **Step 5: Run focused tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_candidate_analysis_artifact_migration.py tests/test_hr_candidate_models.py tests/test_hr_candidate_repository.py tests/test_hr_task_result_projection.py tests/test_hr_task_result_projection_database.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/077_hr_candidate_analysis_artifacts.sql backend/app/hr/candidate_models.py backend/app/hr/candidate_repository.py backend/app/hr/task_service.py backend/app/hr/task_result_projection.py backend/tests/test_hr_candidate_analysis_artifact_migration.py backend/tests/test_hr_candidate_models.py backend/tests/test_hr_candidate_repository.py backend/tests/test_hr_task_result_projection.py backend/tests/test_hr_task_result_projection_database.py
git commit -m "feat(hr): project candidate evidence and interview PDFs"
```

### Task 7: Replace raw candidate JSON with usable results and PDF download

**Files:**
- Create: `webui/src/workspaces/hr/HrCandidateAnalysisCard.tsx`
- Create: `webui/src/workspaces/hr/HrCandidateAnalysisCard.test.tsx`
- Modify: `webui/src/hrR12Types.ts`
- Modify: `webui/src/hrR12Api.ts`
- Modify: `webui/src/hrR12Api.test.ts`
- Modify: `webui/src/workspaces/hr/HrCandidateWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: typed match and interview-plan result parsers.
- Produces: evidence/gap/risk/unknown rendering and `下载面试题 PDF`.
- Consumes: existing artifact ticket endpoint through the Position resource API.

- [ ] **Step 1: Write failing UI/API tests**

```tsx
expect(await screen.findByText("匹配证据")).toBeVisible();
expect(screen.getByText("量产良率经验待验证")).toBeVisible();
await user.click(screen.getByRole("button", { name: "下载面试题 PDF" }));
expect(openTicket).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/v1\/attachments\/content\//));
```

Assert no `JSON.stringify` dump, missing artifact shows `PDF 尚未生成，重试本任务`, and expired tickets are requested again rather than cached.

- [ ] **Step 2: Run tests RED**

Run: `cd webui && npm test -- src/hrR12Api.test.ts src/workspaces/hr/HrCandidateAnalysisCard.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx`  
Expected: FAIL on missing typed card and artifact field.

- [ ] **Step 3: Implement strict parsing and result cards**

```ts
export type HrCandidateAnalysisResult = HrCandidateMatchResult | HrCandidateInterviewPlanResult;
export interface HrCandidateAnalysisVersion {
  // existing provenance fields remain
  result: HrCandidateAnalysisResult;
  sourceArtifactVersionId: string | null;
}
```

Show evidence, gaps, risks, unknowns, questions, source Position/Document versions, copy, feedback, retry, and PDF download with business-readable labels.

- [ ] **Step 4: Run focused tests and build GREEN**

Run: `cd webui && npm test -- src/hrR12Api.test.ts src/workspaces/hr/HrCandidateAnalysisCard.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx && npm run build`  
Expected: tests PASS and Vite production build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webui/src/hrR12Types.ts webui/src/hrR12Api.ts webui/src/hrR12Api.test.ts webui/src/workspaces/hr/HrCandidateAnalysisCard.tsx webui/src/workspaces/hr/HrCandidateAnalysisCard.test.tsx webui/src/workspaces/hr/HrCandidateWorkspace.tsx webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx webui/src/styles.css
git commit -m "feat(hr): present candidate analysis and interview PDFs"
```

### Task 8: Prove the complete recruiting loop without Panorama

**Files:**
- Create: `backend/tests/test_hr_p0_recruiting_loop.py`
- Create: `webui/src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx`
- Modify: `backend/tests/test_hr_workspace_acceptance.py`

**Interfaces:**
- Exercises Tasks 1–7 through public APIs and rendered UI.
- Produces a stable acceptance seam consumed by the combined P0 acceptance plan.

- [ ] **Step 1: Write the failing backend acceptance**

Use deterministic Agent results to exercise clarification, Position package projection, atomic confirmation, two successful resume parses, one isolated failed parse and retry, two Candidate confirmations, match analysis, interview plan, and PDF artifact binding.

- [ ] **Step 2: Write the failing web acceptance**

Render the same business sequence and assert the current chat message remains visible through Position confirmation, result cards replace raw JSON, and the PDF ticket is opened.

- [ ] **Step 3: Run RED, make only integration corrections, then run GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_p0_recruiting_loop.py tests/test_hr_workspace_acceptance.py -q && cd ../webui && npm test -- src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx`  
Expected after corrections: PASS.

- [ ] **Step 4: Run the complete HR regression and production build**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_*.py -q && cd ../webui && npm test -- --run && npm run build`  
Expected: all HR backend tests, all web tests, and the production build PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_hr_p0_recruiting_loop.py backend/tests/test_hr_workspace_acceptance.py webui/src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx
git commit -m "test(hr): prove P0 recruiting loop"
```
