# HR Recruiting Intelligence Production Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace user-triggered HR Bot panorama runs with a durable background code-and-model production pipeline, while making the HR workbench a read-only consumer of the latest valid, sourced, downloadable intelligence.

**Architecture:** Preserve the proven v79 source, snapshot, insight, export, and retrieval contracts, then add a v80 production batch and atomic publication boundary that does not depend on a conversation. A backend producer collects each company channel independently, persists normalized evidence, analyzes successful evidence through a configured model adapter, and atomically publishes a version. The business API and React workspace only expose published reads; position tasks retrieve published intelligence without live crawling.

**Tech Stack:** Python 3.12, FastAPI, psycopg/PostgreSQL, httpx, pytest/respx, React 19, TypeScript, Vitest, existing PDF/XLSX exporters.

## Global Constraints

- HR 业务页面不出现“立即更新”。
- HR 业务页面不提供“添加关注公司”、来源维护、运行任务或重试采集入口。
- 普通 HR 对话和岗位任务不得启动实时招聘情报抓取。
- 长耗时生产任务不得占用 HR Bot 会话执行槽位。
- 新版本只有通过质量门禁后才能原子发布；失败时保留上一有效版本。
- 事实、AI 推断、未知项、来源 URL、观察时间和覆盖状态必须可区分、可追溯。
- 每个发布版本同时保存不可变原始公开响应、完整标准化岗位明细和独立 AI 分析；AI 分析不得覆盖原数据。
- 原始证据归档只写入 `/data/agent-platform/hr-intelligence/evidence/`，不得写入 release、`/tmp` 或根盘持久目录。
- 不新增北森、OA、猎聘或 BOSS 企业账号集成。
- 不提交或删除 `backend/.venv` 以及当前工作区其他用户拥有的未跟踪文件。

## File Structure

- `backend/control_migrations/080_hr_panorama_publication.sql`: 独立生产批次、来源尝试和当前发布指针。
- `backend/app/hr/panorama_models.py`: v80 批次、尝试、发布信息及命令对象。
- `backend/app/hr/panorama_repository.py`: v80 持久化、领取、恢复、原子发布和当前报告读取。
- `backend/app/hr/panorama_evidence.py`: 内容寻址的原始响应归档、凭据清除、哈希校验和读取边界。
- `backend/app/hr/panorama_collection.py`: 按来源 URL 采集、解析和标准化的纯代码边界。
- `backend/app/hr/panorama_analysis.py`: 分层模型分析接口、严格结果校验和事实引用检查。
- `backend/app/hr/panorama_producer.py`: 有限并发、失败隔离、幂等和发布门禁。
- `backend/app/hr/panorama_cli.py`: 定时任务和管理员使用的后台 CLI；不暴露到 HR 页面。
- `backend/app/hr/panorama_routes.py`: 仅保留业务读取与下载路由，增加当前发布版本读取。
- `backend/app/hr/panorama_service.py`: 只读业务服务和内部生产服务边界分离。
- `backend/app/hr/panorama_context.py`: 只从已发布版本检索岗位相关情报。
- `backend/app/main.py`: 移除 HR Bot panorama projector 循环，装配只读服务。
- `webui/src/hrPanoramaTypes.ts`: 删除运行和变更输入类型，增加发布覆盖信息。
- `webui/src/hrPanoramaApi.ts`: 删除 POST/轮询方法，增加 `currentReport()`。
- `webui/src/workspaces/hr/HrPanoramaWorkspace.tsx`: 改为打开即读的结果工作台。
- `webui/src/workspaces/hr/HrPanoramaReport.tsx`: 显示发布时间、来源覆盖和最后有效版本状态。
- `backend/tests/test_hr_panorama_publication_migration.py`: v80 数据库边界测试。
- `backend/tests/test_hr_panorama_producer.py`: 生产管线单元测试。
- `backend/tests/test_hr_panorama_evidence.py`: 原始证据归档、去敏、不可变和哈希校验测试。
- `backend/tests/test_hr_panorama_publication_database.py`: 持久化和原子发布集成测试。
- `backend/tests/test_hr_panorama_api.py`: 只读 API 契约测试。
- `backend/tests/test_hr_panorama_context.py`: 仅检索发布版本测试。
- `webui/src/hrPanoramaApi.test.ts`: 只读客户端测试。
- `webui/src/workspaces/hr/HrPanoramaWorkspace.test.tsx`: 无运行控件、降级和直接展示测试。
- `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`: 端到端业务验收改写。

---

### Task 1: Remove collection controls from the HR workbench

**Files:**
- Modify: `webui/src/hrPanoramaTypes.ts`
- Modify: `webui/src/hrPanoramaApi.ts`
- Modify: `webui/src/hrPanoramaApi.test.ts`
- Modify: `webui/src/workspaces/hr/HrPanoramaWorkspace.tsx`
- Modify: `webui/src/workspaces/hr/HrPanoramaWorkspace.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.tsx`
- Modify: `webui/src/workspaces/hr/HrPanoramaReport.test.tsx`
- Modify: `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`

**Interfaces:**
- Consumes: existing `GET /api/hr/panorama/reports` and `GET /api/hr/panorama/reports/{id}`.
- Produces: `HrPanoramaApi.currentReport(signal?: AbortSignal): Promise<HrPanoramaReport | null>` with no mutation or run methods.

- [ ] **Step 1: Write failing API and workspace tests**

```ts
it("is a read-only panorama client", async () => {
  const api = createHrPanoramaApi("ignored");
  expect("startRun" in api).toBe(false);
  expect("addCompany" in api).toBe(false);
  expect("runStatus" in api).toBe(false);
});

it("opens the latest valid report without collection controls", async () => {
  await renderWorkspace();
  expect(container.textContent).toContain("数据截至");
  expect(container.textContent).not.toContain("立即更新");
  expect(container.textContent).not.toContain("添加关注公司");
  expect(container.textContent).not.toContain("正在收集");
  expect(container.textContent).toContain("AI 分析");
  expect(container.textContent).toContain("原始岗位数据");
});
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `cd webui && npm test -- src/hrPanoramaApi.test.ts src/workspaces/hr/HrPanoramaWorkspace.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx`

Expected: FAIL because mutation methods and collection controls still exist.

- [ ] **Step 3: Replace the client with read-only methods**

```ts
export function createHrPanoramaApi(_csrfToken: string) {
  return {
    async currentReport(signal?: AbortSignal): Promise<HrPanoramaReport | null> {
      const reports = await request("/api/hr/panorama/reports?limit=1", { signal }).then(
        (value) => itemList(value).map(parseHrPanoramaInsight),
      );
      return reports[0] ? request(`/api/hr/panorama/reports/${encodeURIComponent(reports[0].insightVersionId)}`, { signal }).then(parseHrPanoramaReport) : null;
    },
    listReports(signal?: AbortSignal): Promise<HrPanoramaInsight[]> {
      return request("/api/hr/panorama/reports?limit=100", { signal }).then((value) => itemList(value).map(parseHrPanoramaInsight));
    },
    report(insightVersionId: string, signal?: AbortSignal): Promise<HrPanoramaReport> {
      const selected = inputId(insightVersionId);
      return request(`/api/hr/panorama/reports/${encodeURIComponent(selected)}`, { signal }).then(parseHrPanoramaReport);
    },
  };
}
```

Rewrite `HrPanoramaWorkspace` around three states only: loading, latest-valid empty state, and report. Remove retained execution localStorage, POST mutations, timers, source selection, add-company forms, run progress and retry buttons. Keep history navigation, report filters, source evidence, copy and downloads. Present two explicit report layers: “AI 分析” for decisions and “原始岗位数据” for the complete normalized records, with every AI fact linking back to its snapshot and source.

- [ ] **Step 4: Run tests and build**

Run: `cd webui && npm test -- src/hrPanoramaApi.test.ts src/workspaces/hr/HrPanoramaWorkspace.test.tsx src/workspaces/hr/HrPanoramaReport.test.tsx src/workspaces/hr/HrPanorama.acceptance.test.tsx && npm run build`

Expected: all selected tests PASS and TypeScript/Vite build succeeds.

- [ ] **Step 5: Commit**

```bash
git add webui/src/hrPanoramaTypes.ts webui/src/hrPanoramaApi.ts webui/src/hrPanoramaApi.test.ts webui/src/workspaces/hr/HrPanoramaWorkspace.tsx webui/src/workspaces/hr/HrPanoramaWorkspace.test.tsx webui/src/workspaces/hr/HrPanoramaReport.tsx webui/src/workspaces/hr/HrPanoramaReport.test.tsx webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx
git commit -m "refactor(hr): make panorama workbench read only"
```

### Task 2: Add durable production batches and atomic publications

**Files:**
- Create: `backend/control_migrations/080_hr_panorama_publication.sql`
- Create: `backend/tests/test_hr_panorama_publication_migration.py`
- Modify: `backend/app/hr/panorama_models.py`
- Modify: `backend/tests/test_hr_panorama_models.py`
- Modify: `backend/app/hr/panorama_repository.py`
- Create: `backend/tests/test_hr_panorama_publication_database.py`

**Interfaces:**
- Consumes: v79 `talent_sources`, `public_job_snapshots`, `talent_insight_versions`, and immutable report readers.
- Produces: `ProductionBatch`, `SourceCollectionAttempt`, `PublishedPanorama`; repository methods `create_production_batch`, `record_source_attempt`, `publish_report`, `current_publication`.

- [ ] **Step 1: Write failing migration and model tests**

```python
def test_v80_adds_background_publication_boundary() -> None:
    sql = normalized_sql("080_hr_panorama_publication.sql")
    assert "create table platform_hr.panorama_production_batches" in sql
    assert "create table platform_hr.panorama_source_attempts" in sql
    assert "create table platform_hr.panorama_publications" in sql
    assert "create table platform_hr.panorama_current_publications" in sql
    assert "create function platform_hr.publish_panorama_version_v80" in sql
    assert "pg_advisory_xact_lock" in sql

def test_publication_requires_a_terminal_batch_and_valid_insight() -> None:
    with pytest.raises(ValueError):
        PublishedPanorama(publication_id=uuid4(), insight_version_id=uuid4(), coverage_state="invalid", published_at=NOW)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_publication_migration.py tests/test_hr_panorama_models.py`

Expected: FAIL because v80 objects do not exist.

- [ ] **Step 3: Implement the v80 database contract**

Create immutable production batches keyed by `batch_id`, attempts keyed by `(batch_id, source_id, source_url, attempt_number)`, immutable publications keyed by `publication_id`, and a single current pointer per shared `workspace_key='hr'`. `publish_panorama_version_v80(...)` must lock `workspace_key`, verify batch terminality, verify every fact belongs to a referenced snapshot, insert publication, then upsert the current pointer in one transaction. Grant production functions only to the application role; expose current-publication reads through a stable security-definer function.

The shared published report is workspace-scoped, not viewer-owned. It retains `producer_owner_internal_user_id` for audit while `read_current_panorama_publication_v80()` returns the same authorized HR publication to every HR workspace member.

- [ ] **Step 4: Add strict model and repository methods**

```python
@dataclass(frozen=True, slots=True)
class PublishedPanorama:
    publication_id: UUID
    insight_version_id: UUID
    batch_id: UUID
    coverage_state: Literal["complete", "partial"]
    source_coverage: tuple[Mapping[str, object], ...]
    published_at: datetime

@dataclass(frozen=True, slots=True)
class PublishPanoramaReport:
    publication_id: UUID
    client_request_id: UUID
    batch_id: UUID
    insight_version_id: UUID
    coverage_state: Literal["complete", "partial"]
    source_coverage: tuple[Mapping[str, object], ...]

class PanoramaRepository:
    def current_publication(self) -> PublishedPanorama | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_current_panorama_publication_v80(%s)",
                    ("hr",),
                ).fetchone()
            return None if row is None else _publication(row)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "current publication")

    def publish_report(self, command: PublishPanoramaReport) -> PublishedPanorama:
        if not isinstance(command, PublishPanoramaReport):
            raise ValueError("panorama publication command required")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.publish_panorama_version_v80(%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        command.publication_id,
                        command.client_request_id,
                        command.batch_id,
                        command.insight_version_id,
                        command.coverage_state,
                        json.dumps(thaw_json(command.source_coverage), ensure_ascii=False),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama publication unavailable")
            return _publication(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "publication")
```

Implement `_publication(row)` with the same strict field mapping style as `_source`, `_run` and `_insight`; convert psycopg constraint failures to `PanoramaConflict` and availability failures to `PanoramaUnavailable` using the repository's existing `_raise` boundary.

- [ ] **Step 5: Verify migration and transaction behavior**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_publication_migration.py tests/test_hr_panorama_models.py tests/test_hr_panorama_publication_database.py`

Expected: PASS, including concurrent publish winner, failed-publish rollback, partial coverage, and last-known-good reads.

- [ ] **Step 6: Commit**

```bash
git add backend/control_migrations/080_hr_panorama_publication.sql backend/app/hr/panorama_models.py backend/app/hr/panorama_repository.py backend/tests/test_hr_panorama_publication_migration.py backend/tests/test_hr_panorama_models.py backend/tests/test_hr_panorama_publication_database.py
git commit -m "feat(hr): add durable panorama publication boundary"
```

### Task 3: Build the isolated code collection pipeline

**Files:**
- Create: `backend/app/hr/panorama_evidence.py`
- Create: `backend/tests/test_hr_panorama_evidence.py`
- Create: `backend/app/hr/panorama_collection.py`
- Create: `backend/tests/test_hr_panorama_collection.py`
- Create: `backend/app/hr/panorama_producer.py`
- Create: `backend/tests/test_hr_panorama_producer.py`

**Interfaces:**
- Consumes: active source catalog and v80 batch/attempt repository methods.
- Produces: `EvidenceArchive.store(EvidencePayload) -> EvidenceRecord`; `PublicSourceCollector.collect(SourceTarget) -> CollectionResult`; `PanoramaProducer.run(batch_id) -> ProductionSummary`.

- [ ] **Step 1: Write failing collector and failure-isolation tests**

```python
@pytest.mark.asyncio
async def test_collects_each_company_channel_independently(respx_mock) -> None:
    respx_mock.get("https://a.example/jobs").mock(return_value=httpx.Response(200, text=JOBS_HTML))
    result = await PublicSourceCollector(httpx.AsyncClient()).collect(target("https://a.example/jobs"))
    assert [job.title for job in result.jobs] == ["高级结构工程师"]
    assert result.content_sha256 == hashlib.sha256(JOBS_HTML.encode()).hexdigest()

@pytest.mark.asyncio
async def test_one_source_failure_does_not_cancel_successful_sources() -> None:
    summary = await producer_with(success("A"), timeout("B")).run(BATCH_ID)
    assert summary.successful_sources == (A_ID,)
    assert summary.failed_sources == {B_ID: "source_timeout"}

def test_evidence_archive_preserves_source_bytes_without_credentials(tmp_path) -> None:
    archive = EvidenceArchive(tmp_path)
    record = archive.store(EvidencePayload(source_url="https://a.example/jobs", mime="text/html", body=JOBS_HTML.encode(), request_headers={"Authorization": "Bearer secret"}))
    assert archive.read(record.sha256) == JOBS_HTML.encode()
    assert "secret" not in record.metadata_json
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_evidence.py tests/test_hr_panorama_collection.py tests/test_hr_panorama_producer.py`

Expected: FAIL because collector and producer modules do not exist.

- [ ] **Step 3: Implement bounded collection**

`EvidenceArchive` stores immutable content-addressed response bodies under `/data/agent-platform/hr-intelligence/evidence/<sha256-prefix>/<sha256>`, writes metadata without request credentials, verifies the hash on read, and treats an existing identical object as success. `PublicSourceCollector` accepts only canonical approved HTTPS URLs, disables redirects outside the approved origin, enforces connect/read/total timeouts and response size limits, parses supported HTML/JSON into `NormalizedPublicJob`, and returns the archive record plus complete normalized jobs. `PanoramaProducer` runs at most four source targets concurrently, retries network errors at most twice with injected backoff, persists every attempt before analysis, and never retries schema/approval errors.

Use `asyncio.TaskGroup` only inside a wrapper that converts each task result to success/failure data; no source exception may cancel siblings. Use deterministic job identity `(source_id, public_job_key, content_sha256)` and skip unchanged payloads.

- [ ] **Step 4: Verify collector safety and idempotency**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_evidence.py tests/test_hr_panorama_collection.py tests/test_hr_panorama_producer.py`

Expected: PASS for HTML, JSON, timeout, size limit, redirect rejection, bounded retry, independent failure, duplicate hash and restart recovery.

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/panorama_evidence.py backend/app/hr/panorama_collection.py backend/app/hr/panorama_producer.py backend/tests/test_hr_panorama_evidence.py backend/tests/test_hr_panorama_collection.py backend/tests/test_hr_panorama_producer.py
git commit -m "feat(hr): collect recruiting intelligence in isolated batches"
```

### Task 4: Add evidence-grounded model analysis and operator CLI

**Files:**
- Create: `backend/app/hr/panorama_analysis.py`
- Create: `backend/tests/test_hr_panorama_analysis.py`
- Create: `backend/app/hr/panorama_cli.py`
- Create: `backend/tests/test_hr_panorama_cli.py`
- Modify: `backend/app/hr/panorama_producer.py`
- Modify: `backend/tests/test_hr_panorama_producer.py`

**Interfaces:**
- Consumes: successful normalized snapshots, configured analysis adapter and v80 publication repository.
- Produces: `PanoramaAnalyzer.analyze_company`, `analyze_topic`, `compile_report`; CLI commands `seed-sources`, `run`, `resume`, `status`.

- [ ] **Step 1: Write failing grounded-analysis tests**

```python
def test_compiler_rejects_an_inference_without_basis_facts() -> None:
    with pytest.raises(PanoramaAnalysisError, match="basis"):
        validate_analysis({"facts": [FACT], "inferences": [{"text": "扩张", "basis_fact_ids": []}], "unknowns": []}, snapshots=(SNAPSHOT,))

def test_compiler_rejects_a_fact_with_an_unknown_source_url() -> None:
    forged = FACT | {"source_url": "https://forged.example/jobs"}
    with pytest.raises(PanoramaAnalysisError, match="evidence"):
        validate_analysis({"facts": [forged], "inferences": [], "unknowns": []}, snapshots=(SNAPSHOT,))
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_analysis.py tests/test_hr_panorama_cli.py tests/test_hr_panorama_producer.py`

Expected: FAIL because analysis and CLI modules do not exist.

- [ ] **Step 3: Implement hierarchical analysis**

Build bounded canonical JSON prompts in three passes: per-company analysis, social/campus topic synthesis, then cross-company report compilation. The analysis must go beyond summarization and derive evidence-grounded technical clusters, hiring-resource concentration, product/route signals, regional/team-shape changes, role additions/removals, cross-company similarities and differences, implications for Orbbec positions, and explicit unknowns. Validate exact keys, UTF-8 byte limits, fact-to-snapshot bindings, inference basis IDs, allowed technical directions and source timestamps after every model response. The adapter is injected and obtains its configured model/version from deployment configuration; model identity is recorded in the publication and is never hardcoded in the workbench. A stronger model may create a new analysis version from unchanged raw evidence without mutating that evidence.

- [ ] **Step 4: Implement the operator-only CLI**

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_batch(args))
    if args.command == "resume":
        return asyncio.run(resume_batch(args))
    if args.command == "status":
        return print_status(args)
    return seed_sources(args)
```

`seed-sources` idempotently loads the approved company catalog, including the initial priority companies and confirmed HR Session companies. `run` and `resume` write only through repository functions; logs contain IDs and sanitized reason codes, never page content or credentials.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_analysis.py tests/test_hr_panorama_cli.py tests/test_hr_panorama_producer.py`

Expected: PASS for evidence validation, per-company calls, partial compilation, atomic publish, CLI idempotency and resume.

- [ ] **Step 6: Commit**

```bash
git add backend/app/hr/panorama_analysis.py backend/app/hr/panorama_cli.py backend/app/hr/panorama_producer.py backend/tests/test_hr_panorama_analysis.py backend/tests/test_hr_panorama_cli.py backend/tests/test_hr_panorama_producer.py
git commit -m "feat(hr): compile and publish sourced recruiting intelligence"
```

### Task 5: Switch the business API and app runtime to published reads

**Files:**
- Modify: `backend/app/hr/panorama_service.py`
- Modify: `backend/app/hr/panorama_routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_hr_panorama_service.py`
- Modify: `backend/tests/test_hr_panorama_api.py`
- Modify: `backend/tests/test_hr_r12_integration.py`
- Modify: `webui/src/hrPanoramaApi.ts`
- Modify: `webui/src/hrPanoramaApi.test.ts`

**Interfaces:**
- Consumes: `PanoramaRepository.current_publication()` and `report_for_publication(publication_id)`.
- Produces: `GET /api/hr/panorama/current`, version list/detail/source coverage, and existing PDF/XLSX download routes; no business POST routes.

- [ ] **Step 1: Write failing route-boundary tests**

```python
def test_business_router_has_no_panorama_mutations(app) -> None:
    paths = {(route.path, tuple(route.methods or ())) for route in app.routes}
    assert not any(path == "/api/hr/panorama/runs" and "POST" in methods for path, methods in paths)
    assert not any(path == "/api/hr/panorama/sources" and "POST" in methods for path, methods in paths)

def test_current_returns_204_when_no_publication(client) -> None:
    assert client.get("/api/hr/panorama/current").status_code == 204
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_service.py tests/test_hr_panorama_api.py tests/test_hr_r12_integration.py`

Expected: FAIL because POST routes and HR Bot projector wiring still exist.

- [ ] **Step 3: Implement read-only business routes**

Remove `AddCompanyBody`, `StartRunBody`, request id mutation handling, `/sources` POST, `/runs` POST and run-status routes from `build_panorama_router`. Add `/current`; require normal HR read access once and return the shared publication. Keep report history/detail/export read authorization. Split producer commands into objects constructed only by CLI or scheduled process.

In `main.py`, stop creating `PanoramaRunCoordinator`, `PanoramaResultProjector`, and `panorama_projection_loop` for the web process. Construct the repository, read-only service, and published-only context provider.

- [ ] **Step 4: Point the web client directly at `/current`**

```ts
async currentReport(signal?: AbortSignal): Promise<HrPanoramaReport | null> {
  const response = await fetch(platformPath("/api/hr/panorama/current"), { cache: "no-store", credentials: "same-origin", signal, headers: { Accept: "application/json" } });
  if (response.status === 204) return null;
  if (!response.ok) throw new HrPanoramaApiError(response.status);
  return parseHrPanoramaReport(await response.json());
}
```

- [ ] **Step 5: Run backend and frontend checks**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_service.py tests/test_hr_panorama_api.py tests/test_hr_r12_integration.py`

Run: `cd webui && npm test -- src/hrPanoramaApi.test.ts src/workspaces/hr/HrPanoramaWorkspace.test.tsx`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/hr/panorama_service.py backend/app/hr/panorama_routes.py backend/app/main.py backend/tests/test_hr_panorama_service.py backend/tests/test_hr_panorama_api.py backend/tests/test_hr_r12_integration.py webui/src/hrPanoramaApi.ts webui/src/hrPanoramaApi.test.ts
git commit -m "refactor(hr): serve only published panorama intelligence"
```

### Task 6: Make position tasks consume only current published intelligence

**Files:**
- Modify: `backend/app/hr/panorama_context.py`
- Modify: `backend/tests/test_hr_panorama_context.py`
- Modify: `backend/tests/test_hr_task_context.py`
- Modify: `backend/tests/test_hr_position_task_adapter.py`

**Interfaces:**
- Consumes: repository `current_and_relevant_insights(query, source_ids, limit=5)` restricted to published versions.
- Produces: the existing immutable `PanoramaContextFragment` with publication ID, facts, inferences, URLs, timestamps and stale age.

- [ ] **Step 1: Write failing no-live-crawl and publication-scope tests**

```python
def test_position_turn_uses_only_the_current_publication() -> None:
    fragment = provider.for_turn(owner_id=OWNER, position_id=POSITION, conversation_id=CONVERSATION, turn_id=TURN, query="生成结构工程师面试方案")
    assert fragment.publication_id == CURRENT_PUBLICATION_ID
    assert OLD_INSIGHT_ID not in fragment.insight_version_ids

def test_ordinary_turn_never_starts_a_panorama_run() -> None:
    provider.for_turn(owner_id=OWNER, position_id=POSITION, conversation_id=CONVERSATION, turn_id=TURN, query="生成 JR")
    assert repository.started_runs == []
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_hr_task_context.py tests/test_hr_position_task_adapter.py`

Expected: FAIL because retrieval is not publication-scoped.

- [ ] **Step 3: Restrict retrieval to the current publication**

Resolve the current publication first, search only its facts and insights, then apply existing query/source relevance and byte limits. Persist the publication and insight IDs in the retrieval record so replay is deterministic after a newer publication appears. If there is no publication or no matching facts, return no panorama fragment; do not raise a user-visible task failure and do not invoke collection.

- [ ] **Step 4: Verify task behavior**

Run: `cd backend && .venv/bin/pytest -q tests/test_hr_panorama_context.py tests/test_hr_task_context.py tests/test_hr_position_task_adapter.py`

Expected: PASS for JD, JR, talent profile, sourcing and interview tasks; missing/stale intelligence remains non-blocking.

- [ ] **Step 5: Commit**

```bash
git add backend/app/hr/panorama_context.py backend/tests/test_hr_panorama_context.py backend/tests/test_hr_task_context.py backend/tests/test_hr_position_task_adapter.py
git commit -m "feat(hr): ground position tasks in published intelligence"
```

### Task 7: Complete acceptance, seed the first publication, and document operations

**Files:**
- Modify: `backend/tests/test_hr_p0_panorama_flow.py`
- Modify: `backend/tests/test_hr_p0_acceptance_cli.py`
- Modify: `backend/app/hr/panorama_export.py`
- Modify: `backend/tests/test_hr_panorama_api.py`
- Modify: `webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx`
- Create: `docs/runbooks/hr-panorama-producer.md`

**Interfaces:**
- Consumes: completed Tasks 1-6.
- Produces: a repeatable P0 acceptance command and operator runbook.

- [ ] **Step 1: Replace the old long-conversation acceptance scenario**

The acceptance fixture must seed a production batch with three independent sources, fail one source, publish the two successful sources as `partial`, and verify the current endpoint, source coverage, separate AI-analysis/raw-job views, exports and position-context retrieval. Assert no `/api/hr/panorama/runs` request and no HR Agent conversation creation occurs. Verify the XLSX workbook contains “原始岗位”, “AI分析”, “来源覆盖” and “证据索引”; verify PDF text contains the analysis and raw-job appendix headings.

- [ ] **Step 2: Write the runbook with exact commands**

```bash
cd /opt/agent-platform/current/backend
.venv/bin/python -m app.hr.panorama_cli seed-sources --catalog /data/agent-platform/hr-intelligence/source-catalog.json
.venv/bin/python -m app.hr.panorama_cli run --trigger schedule
.venv/bin/python -m app.hr.panorama_cli status --current
```

Document data locations under `/data/agent-platform/hr-intelligence/`, sanitized logs, scheduler invocation, resume, quality-gate failure, last-known-good verification and rollback. Do not use `/tmp` or release directories for persistent evidence.

- [ ] **Step 3: Run complete verification**

Run: `cd backend && .venv/bin/pytest -q`

Run: `cd webui && npm test -- --run && npm run build`

Run: `rg -n "立即更新|添加关注公司|/api/hr/panorama/runs|startRun|runStatus" webui/src`

Expected: backend and frontend suites PASS, build succeeds, and the forbidden-pattern search returns no business frontend references.

- [ ] **Step 4: Seed and inspect a local first publication**

Run the CLI against the repository's local integration database and verify: at least one company, one source URL, one public job snapshot, one grounded fact, one published report, and one downloadable PDF/XLSX. Capture IDs and coverage counts in the test output; do not add generated evidence or secrets to git.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_hr_p0_panorama_flow.py backend/tests/test_hr_p0_acceptance_cli.py backend/app/hr/panorama_export.py backend/tests/test_hr_panorama_api.py webui/src/workspaces/hr/HrPanorama.acceptance.test.tsx docs/runbooks/hr-panorama-producer.md
git commit -m "test(hr): verify background panorama delivery"
```

## Final verification and delivery gate

- Run `git diff --check` and confirm the worktree only contains intentional changes plus pre-existing user-owned untracked files.
- Run the focused tests after every task, then the full backend suite, full frontend suite and production build.
- Review the final diff for any POST panorama route, frontend run state, fake company conclusion, direct HR Bot collection, unbounded retry or release-directory persistent storage.
- Before any production deployment, follow the user's disk and release discipline: `df -B1 / /data`, stage only under `/data/staging/<application>/<deployment_id>/`, preserve only current plus two rollback releases/images, and report all required disk, release, image, HTTP and shared-Nginx checks.
- Production deployment is limited to the HR/Platform application components required by this feature. Do not change shared Nginx or unrelated bots/services.
