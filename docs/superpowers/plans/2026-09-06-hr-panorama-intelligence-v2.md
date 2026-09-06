# HR Panorama Intelligence V2 Implementation Plan

> **已废弃：** 本计划不得继续执行。招聘情报的新唯一事实源是
> `docs/superpowers/specs/2026-09-06-hr-local-intelligence-factory-design.md`；本文仅保留历史审计记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the first published HR panorama baseline into a sourced, maintainable, six-layer recruiting-intelligence product covering the 12 confirmed companies and supplying relevant evidence to HR position tasks.

**Architecture:** Keep the existing background producer, immutable evidence, snapshot, insight, publication, export, and read-only workbench boundaries. Add operator-only source reconciliation, bounded adapters for the actual public recruiting systems, a deterministic taxonomy/metrics compiler stored inside the existing versioned `direction_clusters` JSON, and task-aware retrieval of the already-published facts. The model remains responsible for evidence-grounded conclusions; code remains authoritative for counts, classification, deduplication, and coverage.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL/PLpgSQL, httpx, pytest, React 19, TypeScript, Vitest, existing PDF/XLSX exporters.

## Global Constraints

- Do all implementation and verification locally; deploy only once after the complete V2 passes all gates.
- HR business pages remain read-only consumers and contain no run, retry, add-source, or “立即更新” control.
- Preserve immutable raw evidence and normalized jobs separately from AI analysis.
- A successful empty source is “checked, no current jobs found”; it is not a collection failure.
- Missing coverage never means that a company has no recruiting activity.
- Do not modify Platform Nginx, other bots, or unrelated applications.
- Do not submit or delete `backend/.venv` or user-owned untracked files.

---

### Task 1: Reconcile the controlled source catalog safely — COMPLETE

**Files:**
- Create: `backend/control_migrations/084_hr_panorama_source_reconciliation.sql`
- Modify: `backend/app/hr/panorama_repository.py`
- Modify: `backend/app/hr/panorama_cli.py`
- Modify: `backend/app/hr/panorama_source_catalog.v1.json`
- Test: `backend/tests/test_hr_panorama_migration.py`
- Test: `backend/tests/test_hr_panorama_database.py`
- Test: `backend/tests/test_hr_panorama_cli.py`

**Interfaces:**
- Produces: `platform_hr.reconcile_talent_source_v84(owner_id, source_id, company_key, canonical_name, aliases, approved_urls, active)`.
- Produces: `PanoramaRepository.reconcile_source(CreateTalentSource) -> TalentSource`.
- Consumes: the existing stable company UUID derived from `owner_id + company_key`.

- [ ] **Step 1: Write failing migration, repository, and CLI tests**

```python
def test_catalog_reconciliation_updates_urls_without_replacing_source_identity():
    original = repository.create_source(source(approved_urls=("https://company.example/jobs",)))
    updated = repository.reconcile_source(source(
        source_id=original.source_id,
        approved_urls=("https://company.example/jobs", "https://ats.example/social/company/1"),
    ))
    assert updated.source_id == original.source_id
    assert updated.approved_urls[-1] == "https://ats.example/social/company/1"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_migration.py tests/test_hr_panorama_database.py tests/test_hr_panorama_cli.py`

Expected: FAIL because migration 084 and `reconcile_source` do not exist.

- [ ] **Step 3: Implement the operator-only reconciliation boundary**

The SQL function must require the application role, lock `(owner_id, company_key)`, reject a different `source_id`, validate all existing v79 name/URL bounds, update only catalog fields, and return the same row. `seed_sources` must call `reconcile_source` for an existing catalog key and `create_source` only for a missing key.

- [ ] **Step 4: Update the 12-company catalog with verified current recruiting portals**

Add the current RoboSense Moka social/campus portals, Revopoint social/campus result pages, Scantech official DingTalk/Moka portals, and Huawei official current/special recruiting pages. Remove non-recruiting marketing pages and challenge pages from the job-source set.

- [ ] **Step 5: Run focused tests GREEN and commit**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_migration.py tests/test_hr_panorama_database.py tests/test_hr_panorama_cli.py`

Commit: `feat(hr): reconcile panorama source catalog`

### Task 2: Collect real public recruiting systems and distinguish empty coverage — COMPLETE

**Files:**
- Modify: `backend/app/hr/panorama_collection.py`
- Modify: `backend/app/hr/panorama_producer.py`
- Test: `backend/tests/test_hr_panorama_collection.py`
- Test: `backend/tests/test_hr_panorama_producer.py`

**Interfaces:**
- Produces: bounded Moka public-job collection with complete `limit/offset` validation.
- Produces: static HTML parsers for the verified ELEGOO cards and Revopoint job tables.
- Produces: JSON-string-wrapped HTML re-parsing.
- Produces: persisted coverage state based on successful attempts, including zero-job success.

- [ ] **Step 1: Add minimal public-shape fixtures and failing parser tests**

```python
def test_parses_revopoint_job_table_with_track_location_and_public_key():
    jobs = parse_public_jobs(REVPOINT_TABLE.encode(), "text/html", revopoint_target())
    assert [(job.public_job_key, job.title, job.location) for job in jobs] == [
        ("122", "【27秋招】产品资料文案策划", "西安")
    ]

@pytest.mark.asyncio
async def test_moka_adapter_fetches_complete_public_job_feed():
    result = await collector.collect(moka_target())
    assert len(result.jobs) == 195
    assert result.jobs[0].location == "广东·深圳市·南山区"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_collection.py tests/test_hr_panorama_producer.py`

Expected: FAIL on static HTML, Moka, wrapped HTML, or zero-job persisted coverage.

- [ ] **Step 3: Implement only the verified adapters**

Moka endpoints are derived from an approved portal path and use only `https://api.mokahr.com/api-platform/v1/jobs/{org}` with `mode`, `siteId`, `limit`, and `offset`. Validate response `code`, `total`, page completeness, maximum 10,000 jobs, and public HTTPS destinations. Static HTML parsers accept only their exact card/table signatures and ignore marketing headings or commented legacy jobs.

- [ ] **Step 4: Make empty success durable and auditable**

`_coverage_from_persisted` must use the latest successful attempt rather than the presence of snapshots to decide success. It must emit `job_count=0` without `error_code` for a checked empty channel.

- [ ] **Step 5: Run focused tests GREEN and commit**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_collection.py tests/test_hr_panorama_producer.py`

Commit: `feat(hr): collect verified recruiting portals`

### Task 3: Compile deterministic six-layer intelligence and grounded model conclusions — COMPLETE

**Files:**
- Create: `backend/app/hr/panorama_dimensions.py`
- Create: `backend/tests/test_hr_panorama_dimensions.py`
- Modify: `backend/app/hr/panorama_analysis.py`
- Modify: `backend/app/hr/panorama_export.py`
- Test: `backend/tests/test_hr_panorama_analysis.py`
- Test: `backend/tests/test_hr_panorama_api.py`

**Interfaces:**
- Produces: `compile_panorama_dimensions(snapshots) -> Mapping[str, object]`.
- Produces: `direction_clusters` with legacy numeric direction counts plus a `_v2` object containing scope, company matrix, recruitment track, job family, seniority, geography, skill, and evidence-sample layers.
- Consumes: only immutable `PublicJobSnapshot` values from one production batch.

- [ ] **Step 1: Write failing exact-count and false-positive tests**

```python
def test_dimensions_deduplicate_jobs_and_classify_six_layers():
    dimensions = compile_panorama_dimensions((social_job, duplicate_job, campus_job))
    assert dimensions["scope"]["unique_job_count"] == 2
    assert dimensions["tracks"] == {"social": 1, "campus": 1, "intern": 0, "unknown": 0}
    assert dimensions["seniority"]["senior"] == 1

def test_soc_does_not_match_social_and_unproven_trend_stays_baseline():
    dimensions = compile_panorama_dimensions((soc_engineer,))
    assert dimensions["tracks"]["unknown"] == 1
    assert dimensions["trend"]["state"] == "baseline_only"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_dimensions.py tests/test_hr_panorama_analysis.py`

Expected: FAIL because the compiler does not exist.

- [ ] **Step 3: Implement deterministic classification and enrichment**

Classify recruitment track, multi-label technical directions, job family, seniority, education, geography, and explicit technology terms from title/duty/requirement/source URL. Counts and matrices must include bounded sample snapshot IDs and must not claim HC, budget, resource spend, or month-over-month trend from one batch. Enrich only the final compiled report after the model response has passed its existing fact/inference binding checks.

- [ ] **Step 4: Export all report layers**

The XLSX `AI分析` sheet includes V2 matrices and model conclusions; `原始岗位` keeps every normalized job; `来源覆盖` distinguishes success-empty from failure; `证据索引` keeps URLs, hashes, and observed times. PDF and Markdown include the same baseline/trend caveat and company/source coverage.

- [ ] **Step 5: Run focused tests GREEN and commit**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_dimensions.py tests/test_hr_panorama_analysis.py tests/test_hr_panorama_api.py`

Commit: `feat(hr): compile six-layer recruiting intelligence`

### Task 4: Make the workbench and position tasks use V2 directly — COMPLETE

**Files:**
- Modify: `webui/src/hrPanoramaTypes.ts`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.tsx`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`
- Modify: `backend/app/hr/panorama_context.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Test: `backend/tests/test_hr_panorama_context.py`
- Test: `backend/tests/test_agent_brain_conversation_context.py`
- Test: `backend/tests/test_hr_position_task_adapter.py`

**Interfaces:**
- Consumes: `_v2` dimensions from the immutable published insight.
- Produces: a read-first report with company comparison, social/campus, directions, job family, seniority, locations, skills, model inferences, unknowns, raw jobs, and source evidence.
- Produces: position-aware ranking for `jd`, `jr`, `talent_profile`, `sourcing_strategy`, and `position_interview_plan` without starting collection.

- [ ] **Step 1: Write failing UI and context tests**

```tsx
expect(screen.getByRole("heading", { name: "公司 × 技术方向" })).toBeVisible();
expect(screen.getByRole("heading", { name: "资历与能力结构" })).toBeVisible();
expect(screen.getByText("基线版本：尚不能判断月度变化")).toBeVisible();
```

```python
def test_jd_and_interview_tasks_receive_only_position_relevant_published_facts():
    fragment = provider.for_turn(
        OWNER, POSITION, "生成面试方案", TURN,
        task_kind="position_interview_plan",
        position_context={"title": "高级结构工程师", "location": "深圳"},
    )
    assert all("结构" in fact["text"] or "深圳" in fact["text"] for fact in fragment.facts)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py tests/test_hr_position_task_adapter.py`

Run: `cd webui && npm test -- src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx`

Expected: FAIL because the V2 matrices and position context argument are absent.

- [ ] **Step 3: Implement the read-first V2 report**

Keep filters and downloads but lead with business conclusions and exact matrices. Show every company even when uncovered, display “本版未覆盖” for failures, and show “已检查，本次未发现公开岗位” only for successful empty sources. Never expose run IDs, retries, Trace, or producer controls.

- [ ] **Step 4: Implement position-aware retrieval**

Parse the existing immutable HR task prompt context in `ConversationContextBuilder`, pass only title/category/location/JD/JR keywords to `PanoramaContextProvider`, rank facts by named company then position terms then task intent, and retain the existing 32 KiB and exact-turn replay boundaries. Missing intelligence remains non-blocking.

- [ ] **Step 5: Run all focused suites GREEN and commit**

Run: `cd backend && ../backend/.venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_agent_brain_conversation_context.py tests/test_hr_position_task_adapter.py`

Run: `cd webui && npm test -- src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx && npm run build`

Commit: `feat(hr): use panorama v2 in recruiting work`

## Final Local Verification and Single-Deploy Gate

- Run the complete backend suite: `cd backend && ../backend/.venv/bin/pytest -q`.
- Run the complete frontend suite and production build: `cd webui && npm test -- --run && npm run build`.
- Run `git diff --check` and verify no user-owned untracked files are staged.
- Generate and inspect a local V2 report from the preserved real snapshot corpus; verify all 12 companies appear, every count maps to deduplicated snapshot IDs, every inference maps to facts, and PDF/XLSX/Markdown exports open.
- Request code review, resolve findings, then merge once and deploy once under the production disk/release discipline.
