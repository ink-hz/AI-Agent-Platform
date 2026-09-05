# HR Core Usability and Task Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the HR conversation and official-position workspace usable first, then make every recruiting task consume and produce the correct versioned context.

**Architecture:** Extend the existing React conversation and position components instead of creating a second workspace. Extend the existing HR task envelope, task record, structured-output projection, and candidate context rather than adding a new task engine. Preserve the immutable version and attachment-grant boundaries already enforced by the backend.

**Tech Stack:** React 19, TypeScript 5.6, Vitest/jsdom, FastAPI, Python 3.12, PostgreSQL/psycopg, pytest.

## Global Constraints

- Conversation is the primary work surface and must remain mounted while drawers open or close.
- Session materials and position details are closed by default and overlay from the right without narrowing chat.
- HR home textarea target height is 320–420px; position conversation textarea target height is 160–220px.
- Enter sends and Shift+Enter inserts a newline.
- Official facts, confirmed internal context, AI drafts, human confirmation, and unknowns remain separate.
- Only explicitly selected ready attachments receive task read grants.
- Candidate interview questions must use the latest valid match analysis for the same PositionContextVersion.
- No ATS workflow, external recruiting-account integration, new seat model, Office/FAE/VOC/Marketing changes, shared Nginx changes, or unrelated file changes.
- Every implementation task follows red-green-refactor TDD and ends with a focused commit.

---

## File Map

- `webui/src/pages/ConversationPage.tsx`: own session-material drawer state while preserving the mounted conversation.
- `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`: request drawer presentation for HR conversations and expose the new-conversation material entry.
- `webui/src/workspaces/hr/HrPositionWorkspace.tsx`: coordinate mutually exclusive session-material and position-detail drawers.
- `webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx`: render official position facts before internal context.
- `webui/src/workspaces/hr/HrOfficialPositionPanel.tsx`: focused official-version current/history UI.
- `webui/src/hrR12Api.ts` and `webui/src/hrR12Types.ts`: parse official-version and task-reference contracts.
- `webui/src/styles.css`: HR composer, drawer, scroll, contrast, and responsive behavior.
- `backend/app/hr/task_context.py`: create task-aware manifest entries and deterministic prompt context.
- `backend/app/hr/task_service.py`: task intent and prerequisite selection.
- `backend/app/hr/structured_output.py`: strict structured contracts for position task outputs.
- `backend/app/hr/task_result_projection.py`: project structured position modules instead of raw full-answer text.
- `backend/app/hr/candidate_context.py`: add the latest same-context match analysis to interview-plan input.
- `backend/app/hr/panorama_context.py`: allow task intent to request relevant Panorama evidence.
- Existing focused test files beside each component remain the primary regression suite.

### Task 1: Full-width conversation and overlay session materials

**Files:**
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/workspaces/direct/DirectAgentWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/pages/ConversationPage.test.tsx`
- Test: `webui/src/workspaces/hr/HrPositionWorkspace.test.tsx`
- Test: `webui/src/pages/HrWorkspace.acceptance.test.tsx`

**Interfaces:**
- Consumes: existing `SessionMaterialsDrawer` and `ConversationThread` attachment state.
- Produces: `materialsPresentation: "sidebar" | "drawer" | "hidden"`, `materialsOpen?: boolean`, and `onMaterialsOpenChange?(open: boolean): void` without remounting `ConversationPage`; non-HR callers retain their existing sidebar behavior.

- [ ] **Step 1: Write failing drawer and composer tests**

```tsx
expect(screen.getByRole("button", { name: "会话材料" })).toHaveAttribute("aria-expanded", "false");
await user.click(screen.getByRole("button", { name: "会话材料" }));
expect(screen.getByRole("dialog", { name: "会话材料" })).toBeVisible();
expect(screen.getByLabelText("继续对话")).toHaveValue("尚未发送的岗位补充");
expect(container.querySelector(".conversation-workspace-grid")).toBeNull();
```

- [ ] **Step 2: Run focused tests and confirm red**

Run: `cd webui && npm test -- src/pages/ConversationPage.test.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx src/pages/HrWorkspace.acceptance.test.tsx`

Expected: FAIL because drawer presentation and the `会话材料` opener do not exist.

- [ ] **Step 3: Implement controlled overlay presentation**

```tsx
export type MaterialsPresentation = "sidebar" | "drawer" | "hidden";

const showMaterialsDrawer = Boolean(attachmentLimits && materialsPresentation === "drawer" && materialsOpen);
return <div className="conversation-workspace-content">
  {conversationContent}
  {attachmentLimits && materialsPresentation === "drawer" && <button
    aria-expanded={showMaterialsDrawer}
    className="session-materials-opener"
    onClick={() => onMaterialsOpenChange?.(!showMaterialsDrawer)}
    type="button"
  >会话材料</button>}
  {showMaterialsDrawer && <SessionMaterialsOverlay onClose={() => onMaterialsOpenChange?.(false)}>
    <SessionMaterialsDrawer {...drawerProps} />
  </SessionMaterialsOverlay>}
</div>;
```

Keep `ConversationPage` mounted and coordinate the two position drawers in `HrPositionWorkspace` with one state:

```tsx
type OpenDrawer = "materials" | "position" | null;
const [openDrawer, setOpenDrawer] = useState<OpenDrawer>(null);
```

- [ ] **Step 4: Apply the confirmed sizing and scroll CSS**

```css
.agent-use-workspace[data-agent-id="hr-bot"] .agent-direct-composer textarea {
  min-height: clamp(320px, 38vh, 420px);
}
.hr-position-chat-surface .agent-use-workspace[data-agent-id="hr-bot"] .conversation-composer textarea {
  min-height: clamp(160px, 21vh, 220px);
}
.session-materials-overlay { position: absolute; inset: 0; z-index: 39; }
.session-materials-overlay .session-materials-drawer {
  position: absolute; inset: 0 0 0 auto; width: min(520px, calc(100% - 32px)); overflow-y: auto;
}
```

- [ ] **Step 5: Run focused tests and build**

Run: `cd webui && npm test -- src/pages/ConversationPage.test.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx src/pages/HrWorkspace.acceptance.test.tsx && npm run build`

Expected: all selected tests PASS and TypeScript/Vite build succeeds.

- [ ] **Step 6: Commit the self-contained UI fix**

```bash
git add webui/src/pages/ConversationPage.tsx webui/src/workspaces/direct/DirectAgentWorkspace.tsx webui/src/workspaces/hr/HrPositionWorkspace.tsx webui/src/styles.css webui/src/pages/ConversationPage.test.tsx webui/src/workspaces/hr/HrPositionWorkspace.test.tsx webui/src/pages/HrWorkspace.acceptance.test.tsx
git commit -m "fix(hr): restore full-width conversation workspace"
```

### Task 2: Complete official-position current and history views

**Files:**
- Create: `webui/src/workspaces/hr/HrOfficialPositionPanel.tsx`
- Create: `webui/src/workspaces/hr/HrOfficialPositionPanel.test.tsx`
- Modify: `backend/app/hr/position_intelligence_routes.py`
- Modify: `backend/app/hr/position_intelligence_service.py`
- Test: `backend/tests/test_hr_position_intelligence_api.py`
- Modify: `webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionDetailsDrawer.test.tsx`
- Modify: `webui/src/hrR12Api.ts`
- Modify: `webui/src/hrR12Api.test.ts`
- Modify: `webui/src/hrR12Types.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `GET /api/hr/positions/{position_id}/official-versions` and version-detail endpoint.
- Produces: `HrOfficialPositionVersion`, `HrR12Api.officialVersions()`, `HrR12Api.officialVersion()`, authenticated Markdown export, and `HrOfficialPositionPanel`.

- [ ] **Step 1: Add failing API contract tests**

```ts
expect(await api.officialVersions(POSITION_ID)).toEqual([expect.objectContaining({
  officialVersionId: OFFICIAL_VERSION_ID,
  duty: "负责数采设备质量策划",
  requirement: "熟悉 DQE 方法",
})]);
```

- [ ] **Step 2: Run API test and confirm red**

Run: `cd webui && npm test -- src/hrR12Api.test.ts`

Expected: FAIL because `officialVersions` is not defined.

- [ ] **Step 3: Add exact type and parsers**

```ts
export interface HrOfficialPositionVersion {
  officialVersionId: string; positionId: string; officialJobId: string;
  title: string; department: string | null; locations: string[];
  category: string | null; subcategory: string | null; headcount: number | null;
  degree: string | null; employmentType: string | null; salary: string | null;
  duty: string | null; requirement: string | null; officialStatus: string;
  sourceVersion: string; sourceChangedAt: string; sourceSnapshotAt: string;
  firstObservedAt: string; lastObservedAt: string;
}
```

Parse every field explicitly and reject malformed payloads; do not cast an unchecked object.

- [ ] **Step 4: Write failing panel behavior tests**

```tsx
expect(await screen.findByRole("heading", { name: "官网岗位原文" })).toBeVisible();
expect(screen.getByText("负责数采设备质量策划")).toBeVisible();
expect(screen.getByText("熟悉 DQE 方法")).toBeVisible();
expect(screen.getByText("官网未公开")).toBeVisible();
expect(screen.getByText("尚未形成内部岗位理解")).toBeVisible();
```

- [ ] **Step 5: Implement current/history rendering and accurate empty states**

`HrOfficialPositionPanel` loads the list once, selects the current ID from `HrPositionDetail`, renders duty and requirement first, and exposes history selection. Keep `HrPositionContextPanel` below an explicit `内部岗位理解` heading. Add an owner-scoped export endpoint that returns a sanitized Markdown attachment for the selected immutable version; use its authenticated response for download and never expose a storage locator.

- [ ] **Step 6: Run panel, API, drawer, and build verification**

Run: `cd backend && pytest -q tests/test_hr_position_intelligence_api.py && cd ../webui && npm test -- src/hrR12Api.test.ts src/workspaces/hr/HrOfficialPositionPanel.test.tsx src/workspaces/hr/HrPositionDetailsDrawer.test.tsx && npm run build`

Expected: selected tests PASS and build succeeds.

- [ ] **Step 7: Commit official position usability**

```bash
git add backend/app/hr/position_intelligence_routes.py backend/app/hr/position_intelligence_service.py backend/tests/test_hr_position_intelligence_api.py webui/src/hrR12Api.ts webui/src/hrR12Api.test.ts webui/src/hrR12Types.ts webui/src/workspaces/hr/HrOfficialPositionPanel.tsx webui/src/workspaces/hr/HrOfficialPositionPanel.test.tsx webui/src/workspaces/hr/HrPositionDetailsDrawer.tsx webui/src/workspaces/hr/HrPositionDetailsDrawer.test.tsx webui/src/styles.css
git commit -m "feat(hr): show complete official position versions"
```

### Task 3: Task-aware context manifest and Panorama retrieval

**Files:**
- Modify: `backend/app/hr/position_intelligence_models.py`
- Modify: `backend/app/hr/task_context.py`
- Modify: `backend/app/hr/task_service.py`
- Modify: `backend/app/hr/panorama_context.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Create: `backend/control_migrations/080_hr_task_context_manifest.sql`
- Test: `backend/tests/test_hr_task_context.py`
- Test: `backend/tests/test_hr_task_context_recovery.py`
- Test: `backend/tests/test_hr_panorama_context.py`
- Test: `backend/tests/test_agent_brain_conversation_context.py`

**Interfaces:**
- Consumes: current `HrPositionContextEnvelope`, immutable task record, and Panorama insight retrieval.
- Produces: `HrTaskContextReference`, `context_references`, and `PanoramaContextProvider.for_turn(..., task_kind=...)`.

- [ ] **Step 1: Write failing task-selection tests**

```python
assert {item.selected_reason for item in envelope.context_references} >= {
    "official_position_baseline", "confirmed_position_context"
}
assert panorama.for_turn(owner_id, position_id, "生成搜寻策略", turn_id, task_kind="sourcing_strategy") is not None
assert panorama.for_turn(owner_id, position_id, "生成岗位面试方案", turn_id, task_kind="position_interview_plan") is None
```

- [ ] **Step 2: Run the context suites and confirm red**

Run: `cd backend && pytest -q tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py`

Expected: FAIL on missing reference and task-intent APIs.

- [ ] **Step 3: Add immutable reference values to the existing envelope**

```python
@dataclass(frozen=True, slots=True)
class HrTaskContextReference:
    source_type: str
    source_id: UUID
    version_id: UUID | None
    selected_reason: str
    content_sha256: str
```

Serialize references into the canonical envelope hash. Migration `080_hr_task_context_manifest.sql` adds a bounded `context_references jsonb` column plus insert/read/recovery function updates to the existing `position_task_records`; replays compare the complete canonical hash and reuse the recorded manifest.

- [ ] **Step 4: Add task-aware Panorama policy**

```python
_PANORAMA_DEFAULT_TASKS = frozenset({"talent_profile", "sourcing_strategy"})
explicitly_requested = task_kind in _PANORAMA_DEFAULT_TASKS or _has_explicit_trigger(query)
```

Keep the existing source-name filtering, freshness warning, fact/inference/unknown separation, five-insight limit, and 32 KiB bound.

- [ ] **Step 5: Run context tests green**

Run: `cd backend && pytest -q tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py`

Expected: all selected tests PASS, including immutable recovery.

- [ ] **Step 6: Commit the task-aware context contract**

```bash
git add backend/control_migrations/080_hr_task_context_manifest.sql backend/app/hr/position_intelligence_models.py backend/app/hr/task_context.py backend/app/hr/task_service.py backend/app/hr/panorama_context.py backend/app/agent_brain/conversation_context.py backend/tests/test_hr_task_context.py backend/tests/test_hr_task_context_recovery.py backend/tests/test_hr_panorama_context.py backend/tests/test_agent_brain_conversation_context.py
git commit -m "feat(hr): assemble task-aware recruiting context"
```

### Task 4: Structured position-task outputs and dependency reuse

**Files:**
- Modify: `backend/app/hr/structured_output.py`
- Modify: `backend/app/hr/task_service.py`
- Modify: `backend/app/hr/task_result_projection.py`
- Test: `backend/tests/test_hr_structured_output.py`
- Test: `backend/tests/test_hr_task_result_projection.py`
- Test: `backend/tests/test_hr_task_result_projection_database.py`

**Interfaces:**
- Consumes: visible Markdown plus one hidden backward-compatible `platform-hr-v1` envelope for the task kind.
- Produces: validated module payloads for `jd`, `jr`, `talent_profile`, `sourcing_strategy`, and `position_interview_plan`.

- [ ] **Step 1: Write failing strict-envelope tests**

```python
parsed = extract_hr_envelope(answer, "sourcing_strategy")
assert parsed.payload == {
    "target_sources": ["目标公司"],
    "keywords": ["光机结构"],
    "exclusions": ["纯消费电子外观结构"],
    "evidence_refs": ["insight-version-1"],
    "unknowns": ["公开渠道覆盖率未知"],
}
```

- [ ] **Step 2: Run structured-output and projection tests red**

Run: `cd backend && pytest -q tests/test_hr_structured_output.py tests/test_hr_task_result_projection.py tests/test_hr_task_result_projection_database.py`

Expected: FAIL because position tasks currently project raw visible Markdown.

- [ ] **Step 3: Define per-task schemas and prompts**

Define exact-key validators for each task. Every prompt requires human-readable Markdown followed by exactly one matching envelope. Reject missing, duplicate, wrong-kind, oversized, or unknown-key payloads.

```python
_POSITION_TASK_SCHEMAS = {
    "jd": frozenset({"text", "change_summary", "unknowns", "evidence_refs"}),
    "jr": frozenset({"responsibilities", "must_have", "preferred", "trainable", "evaluation_criteria", "unknowns", "evidence_refs"}),
    "talent_profile": frozenset({"dimensions", "priorities", "counter_examples", "unknowns", "evidence_refs"}),
    "sourcing_strategy": frozenset({"target_sources", "keywords", "exclusions", "unknowns", "evidence_refs"}),
    "position_interview_plan": frozenset({"dimensions", "questions", "follow_ups", "evaluation_anchors", "unknowns", "evidence_refs"}),
}
```

- [ ] **Step 4: Project validated payloads and visible Markdown separately**

Store the typed payload in the correct context module and keep `visible_markdown` as the human summary. Preserve official/context/material/Panorama references in the draft provenance.

- [ ] **Step 5: Run projection suites green**

Run: `cd backend && pytest -q tests/test_hr_structured_output.py tests/test_hr_task_result_projection.py tests/test_hr_task_result_projection_database.py`

Expected: all selected tests PASS and malformed outputs fail without creating a draft.

- [ ] **Step 6: Commit structured position task results**

```bash
git add backend/app/hr/structured_output.py backend/app/hr/task_service.py backend/app/hr/task_result_projection.py backend/tests/test_hr_structured_output.py backend/tests/test_hr_task_result_projection.py backend/tests/test_hr_task_result_projection_database.py
git commit -m "feat(hr): structure position task results"
```

### Task 5: Require the latest match analysis for candidate interview plans

**Files:**
- Modify: `backend/app/hr/candidate_context.py`
- Modify: `backend/app/hr/candidate_models.py`
- Modify: `backend/app/hr/task_service.py`
- Modify: `backend/app/hr/task_context.py`
- Create: `backend/control_migrations/081_hr_candidate_match_dependency.sql`
- Test: `backend/tests/test_hr_candidate_context.py`
- Test: `backend/tests/test_hr_task_context.py`
- Test: `backend/tests/test_hr_task_context_recovery.py`

**Interfaces:**
- Consumes: latest valid `CandidateAnalysisVersion(analysis_kind="match")` for the exact PositionCandidate and PositionContextVersion.
- Produces: `match_analysis_version_id` plus `LATEST_MATCH_ANALYSIS_FOR_INTERVIEW` in the immutable candidate fragment.

- [ ] **Step 1: Write failing prerequisite tests**

```python
with pytest.raises(CandidateScopeViolation, match="matching analysis unavailable"):
    provider.for_task(owner_id, position_id, candidate_id, relation_id, task_kind="candidate_interview_plan")
fragment = provider.for_task(owner_id, position_id, candidate_id, relation_id, task_kind="candidate_interview_plan")
assert "LATEST_MATCH_ANALYSIS_FOR_INTERVIEW" in fragment.prompt_context
assert fragment.match_analysis_version_id == latest_match.analysis_version_id
```

- [ ] **Step 2: Run candidate context tests red**

Run: `cd backend && pytest -q tests/test_hr_candidate_context.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py`

Expected: FAIL because the provider does not receive task kind or load analyses.

- [ ] **Step 3: Select and pin the exact match analysis**

Extend the provider repository protocol with a same-relation analysis reader. Filter by `analysis_kind == "match"`, exact context version, and usable document versions; choose the highest `(version_number, created_at, analysis_version_id)` and add its ID and structured result to the fragment.

- [ ] **Step 4: Persist the dependency in canonical hashing**

Migration `081_hr_candidate_match_dependency.sql` adds `match_analysis_version_id` to task requests and task records and updates their insert/read/recovery functions. Add the field to candidate snapshot hashing and replay checks. A recovery must never silently switch to a newer match analysis.

- [ ] **Step 5: Run candidate context tests green**

Run: `cd backend && pytest -q tests/test_hr_candidate_context.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py`

Expected: all selected tests PASS; interview-plan start without a match analysis returns a scoped prerequisite error.

- [ ] **Step 6: Commit candidate dependency reuse**

```bash
git add backend/control_migrations/081_hr_candidate_match_dependency.sql backend/app/hr/candidate_context.py backend/app/hr/candidate_models.py backend/app/hr/task_service.py backend/app/hr/task_context.py backend/tests/test_hr_candidate_context.py backend/tests/test_hr_task_context.py backend/tests/test_hr_task_context_recovery.py
git commit -m "feat(hr): base interview plans on match analysis"
```

### Task 6: Show actual task references in the business UI

**Files:**
- Modify: `backend/app/hr/task_routes.py`
- Modify: `webui/src/hrR12Api.ts`
- Modify: `webui/src/hrR12Types.ts`
- Modify: `webui/src/workspaces/hr/HrPositionContextPanel.tsx`
- Modify: `webui/src/workspaces/hr/HrCandidateWorkspace.tsx`
- Test: `backend/tests/test_hr_position_intelligence_api.py`
- Test: `webui/src/hrR12Api.test.ts`
- Test: `webui/src/workspaces/hr/HrPositionContextPanel.test.tsx`
- Test: `webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx`

**Interfaces:**
- Consumes: persisted task context references.
- Produces: `HrTaskReference[]` and a compact `本次参考` disclosure attached to results.

- [ ] **Step 1: Write failing API and UI tests**

```tsx
await user.click(screen.getByRole("button", { name: "查看本次参考" }));
expect(screen.getByText("官网岗位 v2026-09-05")).toBeVisible();
expect(screen.getByText("人才画像 v2")).toBeVisible();
expect(screen.getByText("禾赛科技招聘情报 · 截至 2026-09-05")).toBeVisible();
```

- [ ] **Step 2: Run API/UI tests red**

Run: `cd backend && pytest -q tests/test_hr_position_intelligence_api.py && cd ../webui && npm test -- src/hrR12Api.test.ts src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx`

Expected: FAIL because task references are not serialized or rendered.

- [ ] **Step 3: Serialize safe display metadata**

Return only type, display label, version, selection reason, freshness, and an authorized resource identifier. Do not return storage locators, encrypted payloads, or internal prompt text.

- [ ] **Step 4: Render a compact disclosure beside each result**

Use one collapsed `本次参考` button. Group official position, confirmed context, selected materials, candidate analysis, and Panorama evidence. Missing optional evidence is not shown; stale evidence shows its age.

- [ ] **Step 5: Run focused tests and build**

Run: `cd backend && pytest -q tests/test_hr_position_intelligence_api.py && cd ../webui && npm test -- src/hrR12Api.test.ts src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx && npm run build`

Expected: all selected tests PASS and build succeeds.

- [ ] **Step 6: Commit task reference visibility**

```bash
git add backend/app/hr/task_routes.py backend/tests/test_hr_position_intelligence_api.py webui/src/hrR12Api.ts webui/src/hrR12Api.test.ts webui/src/hrR12Types.ts webui/src/workspaces/hr/HrPositionContextPanel.tsx webui/src/workspaces/hr/HrPositionContextPanel.test.tsx webui/src/workspaces/hr/HrCandidateWorkspace.tsx webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx
git commit -m "feat(hr): show task evidence references"
```

### Task 7: Prove the recruiting loop with real context reuse

**Files:**
- Modify: `backend/tests/test_hr_p0_combined_acceptance.py`
- Modify: `backend/app/hr/p0_acceptance_cli.py`
- Modify: `webui/src/workspaces/hr/HrP0Combined.acceptance.test.tsx`
- Modify: `webui/src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: one deterministic acceptance report proving the exact context chain.

- [ ] **Step 1: Extend acceptance fixtures with a DQE official version, selected material, Panorama insight, mock resume, match result, and interview PDF**

```python
expected_chain = (
    "official_position_version",
    "confirmed_position_context",
    "selected_material",
    "panorama_insight",
    "candidate_document",
    "candidate_match_analysis",
    "candidate_interview_pdf",
)
```

- [ ] **Step 2: Run the combined suites and confirm missing assertions fail**

Run: `cd backend && pytest -q tests/test_hr_p0_combined_acceptance.py tests/test_hr_p0_acceptance_cli.py && cd ../webui && npm test -- src/workspaces/hr/HrP0Combined.acceptance.test.tsx src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx`

Expected: FAIL until every chain element is exposed and reused.

- [ ] **Step 3: Complete only the integration wiring required by the acceptance chain**

Verify the same Conversation remains mounted, each task records exact input versions, confirmation creates a new context version, candidate match uses that version, and interview plan pins the match analysis and PDF Artifact.

- [ ] **Step 4: Run the complete focused HR suite**

Run: `cd backend && pytest -q tests/test_hr_* tests/test_agent_brain_conversation_context.py && cd ../webui && npm test -- src/hrR12Api.test.ts src/pages/ConversationPage.test.tsx src/pages/HrWorkspace.acceptance.test.tsx src/workspaces/hr`

Expected: all HR and context tests PASS with no skipped new acceptance assertion.

- [ ] **Step 5: Run repository-level verification**

Run: `cd backend && pytest -q && cd ../webui && npm test && npm run build`

Expected: backend suite, frontend suite, TypeScript, and production build all PASS.

- [ ] **Step 6: Commit the acceptance contract**

```bash
git add backend/tests/test_hr_p0_combined_acceptance.py backend/tests/test_hr_p0_acceptance_cli.py backend/app/hr/p0_acceptance_cli.py webui/src/workspaces/hr/HrP0Combined.acceptance.test.tsx webui/src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx
git commit -m "test(hr): prove context-aware recruiting loop"
```

### Task 8: Production release and authenticated smoke acceptance

**Files:**
- Modify only if required by the existing release process: HR-specific deployment manifest or release script already owned by Platform.
- Do not modify shared Nginx.

**Interfaces:**
- Consumes: a clean, fully verified master commit.
- Produces: one production release with current plus two root-disk rollback versions and a complete release report.

- [ ] **Step 1: Record the release gate before staging**

Run: `df -B1 / /data`

Expected: root free space is at least 25 GB and projected post-stage/post-image free space is at least 20 GB.

- [ ] **Step 2: Stage only code/build output under the deployment ID**

```bash
deployment_id="hr-core-$(date +%Y%m%d%H%M%S)"
staging_dir="/data/staging/ai-agent-platform/${deployment_id}"
```

Use the existing deployment command with a trap that removes exactly `staging_dir`. Exclude data, uploads, logs, indexes, reviews, knowledge copies, databases, `.venv`, `node_modules`, and model caches.

- [ ] **Step 3: Deploy Platform HR without touching other applications**

Retain current plus two rollback releases on root. Archive older releases under `/data/archive/ai-agent-platform/releases/`, retaining at most 10 versions or 30 days, whichever is stricter. Retain current plus two service images and remove only older unreferenced images for this service.

- [ ] **Step 4: Run HTTP and authenticated HR acceptance**

Verify `/hr/`, one DQE position, official duty/requirement history, drawer behavior, one real “介绍一下你自己” reply, one JD/JR task, one selected attachment task, one candidate match, and one interview PDF download. Confirm assistant content and Trace answer are non-empty and management sync does not show “未记录 Agent 回答”.

- [ ] **Step 5: Record the post-release discipline report**

Run: `df -B1 / /data`

Report before/after disk, new sizes, current/two rollback releases, archived/deleted releases, empty staging, current/two rollback images, HTTP acceptance, and an explicit statement that no other application or shared Nginx was modified.
