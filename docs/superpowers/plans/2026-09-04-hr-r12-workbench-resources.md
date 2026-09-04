# HR R1.2 Workbench and Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give HR one responsive workspace for context, candidates, exact position materials, downloadable artifacts, quick tasks, and recoverable batch work.

**Architecture:** Resource APIs project existing Attachment/Artifact relations without copying bytes. React consumes typed position/candidate/resource clients and renders four sections inside the existing HR position route; mobile uses drawers rather than compressed desktop columns.

**Tech Stack:** FastAPI, Python 3.11, React 19, TypeScript, Vitest, Testing Library, existing Attachment download tickets.

## Global Constraints

- Never infer position scope from counts or the current conversation; APIs return exact resource rows.
- User attachments require explicit position promotion and per-turn selection.
- Generated artifacts retain source Turn, version, preview, and download behavior.
- No mock data, empty future-integration panels, ATS navigation, internal Trace, or storage locator.
- Preserve `backend/.venv`; do not touch unrelated applications or Nginx.

---

### Task 1: Add exact position material and artifact APIs

**Files:**
- Create: `backend/app/hr/resource_models.py`
- Create: `backend/app/hr/resource_service.py`
- Create: `backend/app/hr/resource_routes.py`
- Create: `backend/tests/test_hr_position_resources.py`
- Create: `backend/tests/test_hr_position_resource_api.py`

**Interfaces:**
- Produces: `PositionMaterialItem`, `PositionArtifactItem`, `build_hr_resource_router(service, require_hr_access)`

- [ ] **Step 1: Write RED tests for exact rows and download delegation**

```python
def test_position_resources_return_exact_metadata_not_only_ids(service):
    resources = service.for_position(OWNER, POSITION)
    assert resources.materials[0].attachment_id == MATERIAL
    assert resources.artifacts[0].source_turn_id == TURN
    assert not hasattr(resources.artifacts[0], "immutable_locator")

def test_cross_position_resource_is_not_visible(client):
    assert client.get(f"/api/hr/positions/{OTHER}/resources/{MATERIAL}").status_code == 404
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_resources.py tests/test_hr_position_resource_api.py`

Expected: FAIL because resource projections are absent.

- [ ] **Step 3: Implement projections over existing bindings**

Return filename, media type, state, size, created time, source conversation/turn, artifact version, preview capability, and download capability. Delegate preview/download to existing ticket services; never return storage paths.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_position_resources.py tests/test_hr_position_resource_api.py
git add app/hr/resource_models.py app/hr/resource_service.py app/hr/resource_routes.py tests/test_hr_position_resources.py tests/test_hr_position_resource_api.py
git commit -m "feat(hr): expose exact position resources"
```

Expected: PASS.

### Task 2: Add audited historical resource backfill

**Files:**
- Create: `backend/app/hr/resource_backfill.py`
- Create: `backend/tests/test_hr_resource_backfill.py`

**Interfaces:**
- Produces: `discover_resource_bindings`, `apply_resource_bindings`, CLI dry-run/apply counts

- [ ] **Step 1: Write RED tests for deterministic exact and ambiguous cases**

```python
def test_backfill_links_only_resources_from_exactly_bound_conversations():
    result = discover_resource_bindings(CONVERSATIONS, POSITION_BINDINGS)
    assert result.exact_material_ids == (MATERIAL,)
    assert result.ambiguous_attachment_ids == (AMBIGUOUS,)
    assert result.exact_artifact_ids == (ARTIFACT,)
```

- [ ] **Step 2: Run RED**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_resource_backfill.py`

Expected: FAIL because discovery and CLI counts are absent.

- [ ] **Step 3: Implement replay-safe backfill**

Only exact single-position conversation bindings may auto-link resources. Multi-position or unbound conversations remain ambiguous and are reported by ID/count without copying files or changing Turns. Return a typed discovery summary for the final integration CLI; do not edit that shared CLI in this branch.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd backend
./.venv/bin/python -m pytest -q tests/test_hr_resource_backfill.py
git add app/hr/resource_backfill.py tests/test_hr_resource_backfill.py
git commit -m "feat(hr): backfill historical position resources"
```

Expected: PASS.

### Task 3: Add typed R1.2 clients and route sections

**Files:**
- Create: `webui/src/hrR12Types.ts`
- Create: `webui/src/hrR12Api.ts`
- Create: `webui/src/hrR12Api.test.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`

**Interfaces:**
- Produces: `HrPositionSection`, `HrTaskKind`, `HrR12Api`, routes for `chat/context/candidates/artifacts`

- [ ] **Step 1: Write RED type/API/route tests**

```ts
it("round-trips every HR position section", () => {
  for (const section of ["chat", "context", "candidates", "artifacts"] as const) {
    expect(parseRoute(routePath({ name: "hr-position-section", positionId, section }))).toEqual(
      { name: "hr-position-section", positionId, section },
    );
  }
});
```

- [ ] **Step 2: Run RED**

Run: `cd webui && npm test -- --run src/hrR12Api.test.ts src/router.test.ts`

Expected: FAIL because types, client, and route are absent.

- [ ] **Step 3: Implement bounded clients and canonical routes**

```ts
export type HrTaskKind = "jd" | "jr" | "talent_profile" | "sourcing_strategy"
  | "position_interview_plan" | "candidate_match" | "candidate_interview_plan"
  | "candidate_comparison";
export type HrPositionSection = "chat" | "context" | "candidates" | "artifacts";
```

All mutations take caller-generated request IDs; APIs preserve abort signals and normalize 404/409/422 into typed user-actionable errors.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd webui
npm test -- --run src/hrR12Api.test.ts src/router.test.ts
git add src/hrR12Types.ts src/hrR12Api.ts src/hrR12Api.test.ts src/router.ts src/router.test.ts
git commit -m "feat(hr): add R1.2 web contracts"
```

Expected: PASS.

### Task 4: Build context, candidate, and resource panels

**Files:**
- Create: `webui/src/workspaces/hr/HrPositionContextPanel.tsx`
- Create: `webui/src/workspaces/hr/HrPositionContextPanel.test.tsx`
- Create: `webui/src/workspaces/hr/HrCandidateWorkspace.tsx`
- Create: `webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx`
- Create: `webui/src/workspaces/hr/HrPositionResourcesPanel.tsx`
- Create: `webui/src/workspaces/hr/HrPositionResourcesPanel.test.tsx`

**Interfaces:**
- Consumes: `HrR12Api`
- Produces: isolated panels for current/draft/history, batch candidates, and exact resources

- [ ] **Step 1: Write RED component tests**

```tsx
it("keeps two successful resume drafts when a sibling fails and retries only that item", async () => {
  render(<HrCandidateWorkspace api={apiWithOneFailedResume} positionId={positionId} />);
  expect(await screen.findAllByText("待确认")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "重试解析" }));
  expect(api.retryDraft).toHaveBeenCalledTimes(1);
});

it("previews and downloads an exact position artifact", async () => {
  render(<HrPositionResourcesPanel api={api} positionId={positionId} />);
  await user.click(await screen.findByRole("button", { name: "下载面试方案.docx" }));
  expect(api.downloadArtifact).toHaveBeenCalledWith(artifactId);
});
```

- [ ] **Step 2: Run RED**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx`

Expected: FAIL because panels are absent.

- [ ] **Step 3: Implement accessible resilient panels**

Context panel supports module confirmation and baseline conflict without losing edits. Candidate panel supports batch item state, retry, confirmation, match, interview, feedback text, and same-version comparison. Resource panel shows exact status, preview, single download, and existing safe batch download.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd webui
npm test -- --run src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx
git add src/workspaces/hr/HrPositionContextPanel.tsx src/workspaces/hr/HrPositionContextPanel.test.tsx src/workspaces/hr/HrCandidateWorkspace.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx src/workspaces/hr/HrPositionResourcesPanel.tsx src/workspaces/hr/HrPositionResourcesPanel.test.tsx
git commit -m "feat(hr): add position intelligence panels"
```

Expected: PASS.

### Task 5: Integrate the business workspace and quick tasks

**Files:**
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPositionWorkspace.test.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`
- Create: `webui/src/workspaces/hr/HrR12.acceptance.test.tsx`

**Interfaces:**
- Consumes: panels and route/task types from Tasks 3–4
- Produces: responsive four-section position workspace

- [ ] **Step 1: Write RED acceptance for navigation, task launch, recovery, download, and feedback**

```tsx
it("completes the position and candidate workflow after remount", async () => {
  const view = render(<HrPositionWorkspace {...fixture} />);
  await user.click(screen.getByRole("button", { name: "生成人才画像" }));
  view.unmount();
  render(<HrPositionWorkspace {...fixtureWithRunningTask} />);
  expect(await screen.findByText("任务仍在执行")).toBeVisible();
  await user.click(screen.getByRole("tab", { name: "候选人" }));
  expect(screen.getByRole("button", { name: "批量上传简历" })).toBeEnabled();
});
```

- [ ] **Step 2: Run RED**

Run: `cd webui && npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx src/styles.test.ts`

Expected: FAIL because sections and quick tasks are not integrated.

- [ ] **Step 3: Implement desktop and mobile composition**

Desktop uses conversation list, central section, and collapsible materials. Mobile uses one column with material/context/history drawers and a persistent composer. Quick tasks send typed task kind plus current position, selected candidate, context version, and selected material IDs.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd webui
npm test -- --run src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx src/styles.test.ts
git add src/workspaces/hr/HrPositionWorkspace.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx src/workspaces/hr/HrR12.acceptance.test.tsx src/styles.css src/styles.test.ts
git commit -m "feat(hr): deliver the R1.2 business workspace"
```

Expected: PASS.

### Task 6: Verify web and resource regressions

**Files:**
- Modify: only Task 1–5 files when a test demonstrates a defect

- [ ] **Step 1: Run focused backend and frontend suites**

Run: `cd backend && ./.venv/bin/python -m pytest -q tests/test_hr_position_resource*.py tests/test_hr_resource_backfill.py tests/test_hr_position_import_cli.py`

Run: `cd webui && npm test -- --run src/hrR12Api.test.ts src/workspaces/hr`

Expected: PASS.

- [ ] **Step 2: Run production build and diff check**

Run: `cd webui && npm run build && cd .. && git diff --check`

Expected: PASS with only the existing Vite chunk-size advisory.
