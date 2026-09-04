# HR P0 Panorama Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent HR Panorama Analysis function that tracks explicitly followed companies, versions their public recruitment facts, produces evidence-backed AI analysis, and lets Position conversations retrieve relevant results on demand.

**Architecture:** A new `platform_hr` domain owns followed companies, collection runs, public-job snapshots, versioned insights, and Position-turn retrieval records. Each analysis run uses an ordinary durable `hr-bot` Conversation so existing streaming, retry, web research, citations, attachment, and Flywheel behavior remain authoritative. A strict hidden envelope projects the completed report into formal Panorama data; Position context retrieval supplies only relevant, latest, owner-scoped insight excerpts.

**Tech Stack:** Python 3, FastAPI, PostgreSQL/PLpgSQL, psycopg 3, React 19, TypeScript, Agent Brain/MetaBot web research, Vitest, pytest.

## Global Constraints

- Base implementation on the completed HR recruiting-loop plan and migrations through 077.
- Panorama Analysis is an independent first-level HR function, not a Position subpage.
- Only companies explicitly added by the user are followed; mentioning a company in chat does not create a watch.
- P0 tracks publicly accessible recruitment pages only; do not log in to enterprise recruiting accounts and do not build an unbounded web crawler.
- Facts, AI inferences, and unknowns are separate fields with source URL and observed time.
- Network failure retains the last valid snapshot and never means that a company stopped hiring.
- Position conversations retrieve Panorama results on demand and never copy or automatically confirm them into JD/JR.
- All reads and writes are owner-scoped and idempotent; URLs may be returned, credentials and internal storage locations may not.
- Preserve the main HR chat when navigating to and from Panorama Analysis.

---

### Task 1: Create the Panorama data and migration contract

**Files:**
- Create: `backend/control_migrations/078_hr_panorama_intelligence.sql`
- Create: `backend/tests/test_hr_panorama_migration.py`
- Create: `backend/tests/test_hr_panorama_database.py`

**Interfaces:**
- Produces: `talent_sources`, `panorama_runs`, `public_job_snapshots`, `talent_insight_versions`, and `position_insight_retrievals`.
- Produces app-only idempotent create/list/transition functions suffixed `_v78`.

- [ ] **Step 1: Write failing static and database tests**

Require:

```text
talent_sources: company identity, canonical name, aliases, approved public URLs, active flag
panorama_runs: selected sources, Conversation, state, error code, idempotency and timestamps
public_job_snapshots: source, public job key, title, location, duty/requirement excerpts, URL, observed time, SHA-256, status
talent_insight_versions: selected sources/snapshots, facts, inferences, unknowns, direction clusters, summary, source Conversation/Turn, Agent/model provenance
position_insight_retrievals: Position, Turn, insight versions, query hash, created time
```

Assert exact-owner foreign keys, bounded JSON, URL scheme limited to HTTPS, append-only insight versions, current-snapshot uniqueness, and app-only functions.

- [ ] **Step 2: Run tests RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_migration.py tests/test_hr_panorama_database.py -q`  
Expected: FAIL because migration 078 is absent.

- [ ] **Step 3: Implement migration 078**

Use content hash plus public job key for snapshot deduplication. A collection run may be partially completed. Status transitions are `queued → running → completed|partially_completed|failed`; a failed source stores a bounded reason code but does not delete prior snapshots.

- [ ] **Step 4: Run database tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_migration.py tests/test_hr_panorama_database.py -q`  
Expected: PASS in preview and production migration roles.

- [ ] **Step 5: Commit**

```bash
git add backend/control_migrations/078_hr_panorama_intelligence.sql backend/tests/test_hr_panorama_migration.py backend/tests/test_hr_panorama_database.py
git commit -m "feat(hr): add panorama intelligence schema"
```

### Task 2: Implement Panorama models, repository, and service boundaries

**Files:**
- Create: `backend/app/hr/panorama_models.py`
- Create: `backend/app/hr/panorama_repository.py`
- Create: `backend/app/hr/panorama_service.py`
- Create: `backend/tests/test_hr_panorama_models.py`
- Create: `backend/tests/test_hr_panorama_repository.py`
- Create: `backend/tests/test_hr_panorama_service.py`

**Interfaces:**
- Produces: `TalentSource`, `PanoramaRun`, `PublicJobSnapshot`, `TalentInsightVersion`, and `PanoramaReport`.
- Produces: `PanoramaService.add_company/list_companies/start_run/report/run_status`.
- Produces: `PanoramaService.relevant_insights(owner_id, query, position_id, limit=5)`.

- [ ] **Step 1: Write failing domain tests**

```python
source = service.add_company(
    owner_id=OWNER,
    request_id=REQUEST,
    canonical_name="联合光电",
    aliases=("Union Optech",),
    approved_urls=("https://www.union-optech.com/jobs",),
)
assert source.source_kind == "company"
replayed = service.add_company(
    owner_id=OWNER,
    request_id=REQUEST,
    canonical_name="联合光电",
    aliases=("Union Optech",),
    approved_urls=("https://www.union-optech.com/jobs",),
)
assert replayed.source_id == source.source_id
```

Reject ambiguous empty names, HTTP URLs, more than 20 URLs, cross-owner records, arbitrary source kinds, and retrieval without a Position owned by the caller.

- [ ] **Step 2: Run RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_models.py tests/test_hr_panorama_repository.py tests/test_hr_panorama_service.py -q`  
Expected: FAIL because the Panorama modules are absent.

- [ ] **Step 3: Implement narrow immutable models and repository methods**

```python
class PanoramaService:
    def relevant_insights(
        self, owner_id: UUID, query: str, position_id: UUID, *, limit: int = 5,
    ) -> tuple[TalentInsightVersion, ...]:
        return self._repository.relevant_insights(
            owner_id, query, position_id, limit=limit,
        )
```

Ranking uses company-name/alias match, direction keywords, Position title/category, freshness, then stable ID. Do not add embeddings in P0.

- [ ] **Step 4: Run tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_models.py tests/test_hr_panorama_repository.py tests/test_hr_panorama_service.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/panorama_models.py backend/app/hr/panorama_repository.py backend/app/hr/panorama_service.py backend/tests/test_hr_panorama_models.py backend/tests/test_hr_panorama_repository.py backend/tests/test_hr_panorama_service.py
git commit -m "feat(hr): add panorama intelligence domain"
```

### Task 3: Expose owner-scoped Panorama APIs

**Files:**
- Create: `backend/app/hr/panorama_routes.py`
- Create: `backend/tests/test_hr_panorama_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `GET/POST /api/hr/panorama/sources`.
- Produces: `POST /api/hr/panorama/runs`, `GET /api/hr/panorama/runs/{run_id}`.
- Produces: `GET /api/hr/panorama/reports` and `GET /api/hr/panorama/reports/{insight_version_id}`.

- [ ] **Step 1: Write failing route tests**

```python
created = client.post(
    "/api/hr/panorama/sources",
    headers={"Idempotency-Key": str(request_id), "X-CSRF-Token": "csrf"},
    json={"canonical_name": "联合光电", "aliases": [], "approved_urls": ["https://example.com/jobs"]},
)
assert created.status_code == 200
```

Require the same HR Agent authorization used by Position/Candidate APIs, 503 for stale read-only mutations, 404 for another owner's IDs, strict page limits, and `Cache-Control: no-store`.

- [ ] **Step 2: Run RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_api.py -q`  
Expected: FAIL because the router is absent.

- [ ] **Step 3: Implement router and `create_app` wiring**

Use the existing `require_hr_access` dependency; do not introduce a separate Panorama permission universe.

- [ ] **Step 4: Run tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_api.py tests/test_hr_r12_integration.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/panorama_routes.py backend/app/main.py backend/tests/test_hr_panorama_api.py backend/tests/test_hr_r12_integration.py
git commit -m "feat(hr): expose panorama intelligence API"
```

### Task 4: Run public recruitment research through durable HR conversations

**Files:**
- Create: `backend/app/hr/panorama_runtime.py`
- Create: `backend/tests/test_hr_panorama_runtime.py`
- Modify: `backend/app/hr/structured_output.py`
- Modify: `backend/app/hr/panorama_service.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `PanoramaRunCoordinator.submit(run_id) -> conversation_id`.
- Produces: `PanoramaResultProjector.reconcile_one() -> bool`.
- Consumes: existing `ConversationCommandService`, `hr-bot`, MetaBot web research, citations, and `panorama_report` envelope.

- [ ] **Step 1: Write failing runtime tests**

Require the submitted prompt to include only explicitly approved companies/URLs, an as-of timestamp, bounded retry instructions, and the exact output contract. Require ordered projection of jobs and analysis; a source-level `SEARCH_UNAVAILABLE` produces `partially_completed` when another source succeeds.

```python
report = projector.project(completed_answer)
assert report.direction_clusters["结构"] == 4
assert report.facts[0]["source_url"].startswith("https://")
assert report.inferences[0]["basis_fact_ids"]
```

- [ ] **Step 2: Run RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_runtime.py -q`  
Expected: FAIL because runtime coordination is absent.

- [ ] **Step 3: Extend the structured envelope validator**

`panorama_report` requires exact top-level keys `companies`, `jobs`, `facts`, `direction_clusters`, `inferences`, `unknowns`, and `summary`. Each job requires company, public key, title, location, duty/requirement excerpts, source URL, observed time, and content hash. Each inference references one or more fact IDs.

- [ ] **Step 4: Implement coordinator/projector and wire one loop**

The Conversation remains visible from the report as its execution history. Replaying the same run uses the same Conversation and projection IDs. Failed/malformed output is isolated to the run and does not corrupt previous report versions.

- [ ] **Step 5: Run focused tests GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_runtime.py tests/test_hr_structured_output.py tests/test_hr_panorama_service.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/hr/panorama_runtime.py backend/app/hr/structured_output.py backend/app/hr/panorama_service.py backend/app/main.py backend/tests/test_hr_panorama_runtime.py
git commit -m "feat(hr): run durable panorama research"
```

### Task 5: Retrieve relevant Panorama results inside Position conversations

**Files:**
- Create: `backend/app/hr/panorama_context.py`
- Create: `backend/tests/test_hr_panorama_context.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/hr/task_context.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `PanoramaContextProvider.for_turn(owner_id, position_id, query, turn_id) -> PanoramaContextFragment | None`.
- Adds a bounded `hr_panorama_context` objective section with insight version IDs, as-of time, facts, inferences, unknowns, and HTTPS citations.

- [ ] **Step 1: Write failing context tests**

```python
fragment = provider.for_turn(OWNER, POSITION, "参考联合光电修订这个岗位的 JR", TURN)
assert fragment.insight_version_ids == (LATEST_UNION_OPTECH_INSIGHT,)
assert fragment.facts
assert fragment.inferences
assert fragment.source_urls == ("https://example.com/jobs/1",)
```

Assert unrelated questions return `None`, stale-but-last-valid data includes an explicit age warning, cross-owner/cross-Position access fails closed, and no more than five insight versions or 32 KiB enter one Turn.

- [ ] **Step 2: Run RED**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py -q`  
Expected: FAIL because the provider is absent.

- [ ] **Step 3: Implement deterministic on-demand retrieval**

Retrieval triggers on a named followed company or explicit language such as `竞品`, `招聘情报`, `全景分析`, `外部岗位`, or `参考关注公司`. Persist the retrieved insight IDs and query hash in `position_insight_retrievals` for the exact Turn.

- [ ] **Step 4: Compose context without auto-writing Position data**

```python
if panorama_fragment is not None:
    sections["hr_panorama_context"] = panorama_fragment.as_prompt_document()
```

The prompt states that facts may be cited, inferences must be labelled, and any JD/JR change remains a draft until confirmation.

- [ ] **Step 5: Run context and task regressions GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py tests/test_agent_brain_orchestrator.py tests/test_hr_task_context.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/hr/panorama_context.py backend/app/agent_brain/conversation_context.py backend/app/agent_brain/orchestrator.py backend/app/hr/task_context.py backend/app/main.py backend/tests/test_hr_panorama_context.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_agent_brain_orchestrator.py backend/tests/test_hr_task_context.py
git commit -m "feat(hr): retrieve panorama insights for positions"
```

### Task 6: Build the independent Panorama Analysis page

**Files:**
- Create: `webui/src/hrPanoramaTypes.ts`
- Create: `webui/src/hrPanoramaApi.ts`
- Create: `webui/src/hrPanoramaApi.test.ts`
- Create: `webui/src/workspaces/hr/HrPanoramaWorkspace.tsx`
- Create: `webui/src/workspaces/hr/HrPanoramaWorkspace.test.tsx`
- Create: `webui/src/workspaces/hr/HrPanoramaReport.tsx`
- Create: `webui/src/workspaces/hr/HrPanoramaReport.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspaceShell.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspaceShell.test.tsx`
- Modify: `webui/src/workspaces/hr/HrWorkspacePage.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces routes `/hr/panorama` and `/hr/panorama/reports/:insightVersionId`.
- Adds first-level navigation label `全景分析`.
- Consumes the Panorama APIs from Task 3.

- [ ] **Step 1: Write failing router, API, and component tests**

```tsx
expect(parseRoute("/hr/panorama")).toEqual({ name: "hr-panorama" });
expect(screen.getByRole("link", { name: "全景分析" })).toHaveAttribute("href", "/hr/panorama");
await user.click(screen.getByRole("button", { name: "立即更新" }));
expect(await screen.findByText("正在收集公开招聘岗位")).toBeVisible();
```

Assert company list, analysis history, multi-company report, facts/inferences/unknowns separation, source links, observed time, partial failure, last-valid fallback, single-source retry, and no internal error codes.

- [ ] **Step 2: Run tests RED**

Run: `cd webui && npm test -- src/router.test.ts src/hrPanoramaApi.test.ts src/workspaces/hr/HrWorkspaceShell.test.tsx src/workspaces/hr/HrPanoramaWorkspace.test.tsx src/workspaces/hr/HrPanoramaReport.test.tsx`  
Expected: FAIL because the routes and components are absent.

- [ ] **Step 3: Implement strict types/API and the simple master-detail page**

Left side shows followed companies and report history. The main area shows summary, direction clusters, recruitment changes, geography, capabilities, facts, inferences, unknowns, citations, and last-updated time. Advanced task diagnostics remain collapsed.

- [ ] **Step 4: Preserve chat state across navigation**

Use the existing retained HR draft/conversation state; navigating to Panorama and back must not clear an unsent HR message or selected upload. Do not key the chat host by current first-level section.

- [ ] **Step 5: Run focused tests and build GREEN**

Run: `cd webui && npm test -- src/router.test.ts src/hrPanoramaApi.test.ts src/workspaces/hr/HrWorkspaceShell.test.tsx src/workspaces/hr/HrPanoramaWorkspace.test.tsx src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrWorkspacePage.test.tsx && npm run build`  
Expected: tests PASS and production build succeeds.

- [ ] **Step 6: Commit**

```bash
git add webui/src/hrPanoramaTypes.ts webui/src/hrPanoramaApi.ts webui/src/hrPanoramaApi.test.ts webui/src/workspaces/hr/HrPanoramaWorkspace.tsx webui/src/workspaces/hr/HrPanoramaWorkspace.test.tsx webui/src/workspaces/hr/HrPanoramaReport.tsx webui/src/workspaces/hr/HrPanoramaReport.test.tsx webui/src/workspaces/hr/HrWorkspaceShell.tsx webui/src/workspaces/hr/HrWorkspaceShell.test.tsx webui/src/workspaces/hr/HrWorkspacePage.tsx webui/src/App.tsx webui/src/router.ts webui/src/styles.css
git commit -m "feat(hr): add panorama analysis workspace"
```

### Task 7: Prove Panorama collection, analysis, and Position reuse

**Files:**
- Create: `backend/tests/test_hr_p0_panorama_flow.py`
- Create: `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`

**Interfaces:**
- Exercises Tasks 1–6 through public APIs and rendered UI.
- Produces a stable acceptance seam consumed by the combined P0 acceptance plan.

- [ ] **Step 1: Write deterministic backend acceptance**

Add three followed companies. Simulate two successful public-source result sets and one temporary `SEARCH_UNAVAILABLE`; require a partial report, last-valid preservation, retry to completed, direction clustering, citations, and a Position Turn retrieving the latest relevant insight.

- [ ] **Step 2: Write deterministic web acceptance**

Assert the user can add sources, run analysis, inspect evidence behind an inference, return to the preserved Position Conversation, request a JD/JR revision, and see referenced companies, source links, and as-of time.

- [ ] **Step 3: Run RED, make scoped integration corrections, then run GREEN**

Run: `cd backend && .venv/bin/python -m pytest tests/test_hr_p0_panorama_flow.py -q && cd ../webui && npm test -- src/workspaces/hr/HrPanorama.acceptance.test.tsx`  
Expected after corrections: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_hr_p0_panorama_flow.py webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx
git commit -m "test(hr): prove panorama intelligence flow"
```
