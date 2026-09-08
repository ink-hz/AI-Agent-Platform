# HR 公司情报消费侧 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 以真实已发布内容实现公司列表、连续详情、岗位分页、依据展开与显式带入对话，替换混合报告入口。

**Architecture:** 保留 bundle 导入与既有授权；为公司增加独立消费读模型，不通过有损 `_project`。当前目录取得不可变 bundle 身份，后续读取携带该身份，保证阅读过程中不串包。专题保留明确不可用说明，待 M1/M2/M3 解除。

**Tech Stack:** FastAPI、psycopg/PostgreSQL、React、TypeScript、pytest、Vitest。

## Global Constraints

- 公司／专题是两个根维度，各自形成完整阅读空间，不能杂糅成一个总页面。
- 默认显示最新情报，取消版本选择、版本历史和“第几版”展示。
- 采集、分析生产和情报重新生成不在本次消费侧实施范围内。
- 不使用名字关键词、输入证据全集、引用样本或数组顺序推导专题关系。
- 公司身份使用 company_key；矩阵使用真实 schema 3；缺失统计不等于零。
- 引用保留 bundle、unit、类型、局部 ID；官网文档依据不能因不是岗位而丢弃。
- 选择不自动发送；保留草稿与附件；失败保留选择；成功仅清理本次已提交选择；重试保持相同输入。
- 接口优先，页面最后验收。真实 bundle、实际导入、真实身份授权与持久化证据分别记录，不能以模拟数据代替。
- 不修改保留的方法论分支；不部署；不发送业务消息；不调用模型重新分析。

## Shared API contract

所有新路由复用 `/api/hr/panorama` 的授权、私有缓存与错误策略；旧报告 API 兼容保留，新的产品入口不调用它们。

```text
GET /api/hr/panorama/companies
  204 if no publication
  {bundle_id, generated_at, items: CompanySummary[], topics: {state: "blocked"}}
GET /api/hr/panorama/companies/{company_key}?bundle_id=UUID
  {bundle_id, generated_at, company: CompanySummary,
   units: CompanyUnit[], metrics: CompanyMetrics|null}
GET /api/hr/panorama/companies/{company_key}/jobs?bundle_id=UUID&offset=0&limit=25&location=...&status=...
  {bundle_id, company_key, items: Job[], total, offset, limit}
```

`bundle_id` 查询可省略，表示当前包；来自目录的访问必须传入。普通公司深链首次读取当前包；404 表示当前情报不包含该公司，不回退旧包。

```typescript
type CompanySummary = {
  company_key: string; canonical_name: string; aliases: string[];
  summary: string | null;
  coverage: { state: string; observed_at: string | null; job_count: number | null;
    limitations: string[]; document_limitations: string[] } | null;
};
type CompanyUnit = {
  unit_id: string; kind: "company"; scope_key: string;
  response: {
    summary: string; confidence: string;
    facts: {fact_id: string; text: string; evidence_sha256: string; source_url: string; observed_at: string}[];
    inferences: {inference_id: string; text: string; claim_type?: string; basis_fact_ids: string[]}[];
    recommendations: {recommendation_id: string; text: string; target_tasks?: string[]; basis_fact_ids: string[]}[];
    alternatives: {alternative_id: string; text: string; challenged_inference_ids: string[]; basis_fact_ids: string[]}[];
    unknowns: string[];
  };
};
```

`CompanyMetrics` 直接保留已验证 schema 3 中该 company_key 的 `job_count, directions, secondary_directions, locations, tracks, seniority, job_families, skills, sample_snapshot_ids`；计数关系可能重叠，不能相加当总人数。`Job` 保留产物字段（job_id/company_key/title/location/status/duty_excerpt/requirement_excerpt/source_url/observed_at 等），不在浏览器推导招聘类型。没有可靠逐岗位分面字段时不提供该筛选；统计仍可展示。

## Task 1: Company read model and APIs

**Files:** Create `backend/app/hr/company_intelligence.py`, `backend/tests/test_hr_company_intelligence.py`; modify `backend/app/hr/panorama_service.py`, `panorama_routes.py`, `panorama_repository.py`; add fixtures under `backend/tests/fixtures/hr_intelligence_company/` if needed.

**Interfaces:** `PanoramaService.companies()`, `company(company_key, *, bundle_id=None)`, `company_jobs(company_key, *, bundle_id=None, offset=0, limit=25, location=None, status=None)` implement shared API contract. Repository paginates/filter rows before returning to application; no `bundle_jobs()` all-company loading on list/detail. Reuse existing security-definer read entrypoint with SQL filtering/pagination if possible; no unnecessary schema migration.

真实 HTTP 首轮测量补充：只缩小浏览器响应不足以消除延迟。目录 6367 B、详情约 3–13 KB，但同机请求仍约 760–800 ms。新增公司专用数据库投影，在 SQL 内选择摘要／公司正文及必要元数据，不将生产请求、输入证据与无关分析搬入应用进程；岗位范围校验也不读取全量分析。复用既有安全读取函数，新增投影测试并复测，不承诺生产时延。

- [x] Add failing tests for independent company summaries, complete IDs/links/non-job facts, correct company_key statistics, missing metrics, empty publication and unknown company.
- [x] Run `python -m pytest tests/test_hr_company_intelligence.py -q`; record expected missing capability failure.
- [x] Implement pure company projection from `source_catalog`, `source_coverage`, accepted company `analysis` and `aggregates`. Keep response arrays and original IDs, never require job evidence matches.
- [x] Add GET handlers after auth and bounded offset/limit/status/location validation. Pinned historical reads use existing approved bundle repository; unpinned reads use current publication only.
- [x] Verify pagination excludes other companies, list/detail never load jobs, forbidden access before repository, 404/204/409/503 semantics and unchanged legacy API tests.
- [x] Produce a real-bundle derived API fixture for the frontend, preserving all company summaries, one full company detail with non-job evidence, another detail with missing matrix, and a job page. Record exact source bundle/manifest and extraction script; no invented fields.
- [x] Commit only backend and fixture changes; write TDD and self-review report under `.superpowers/sdd/company-api-report.md`.

## Task 2: Company reading workspace

**Files:** Create `webui/src/hrCompanyIntelligenceApi.ts`, `webui/src/hrCompanyIntelligenceTypes.ts`, corresponding tests and focused company components under `webui/src/workspaces/hr/`; replace `HrPanoramaWorkspace.tsx` and its tests; adjust `HrWorkspaceShell.tsx` and affected assertions; use dedicated CSS.

**Interfaces:** Shared API contract above; workspace accepts optional `onSelectReference(reference)` callback, supplied by Task 3. Reference shape is defined below and emitted only on explicit click. Preserve `account`, legacy `insightVersionId` props (legacy link now opens latest root). Use `?company=company_key` for refreshable company location without adding a report/version route.

```typescript
type HrIntelligenceReference = {
  key: string; bundleId: string; companyKey: string; companyName: string;
  label: string; generatedAt: string; excerpt: string;
  sourceUrls: string[]; unitId?: string; claimType?: string; localId?: string;
  jobIds?: string[]; filters?: {location?: string; status?: string};
};
```

- [x] Add failing tests for new two-root navigation, neutral company/alias search, independent summary/detail and absence of history/changes requests.
- [x] Implement runtime parser and lightweight list/detail/jobs client with safe source URLs and abort support. Consume fixture produced by Task 1 through parser; do not recreate fictional schema data.
- [x] Render compact company index then continuous detail with complete existing summaries/inferences/recommendations/alternatives/unknowns. Facts and source observations expand next to related claims. Long sections may collapse without arbitrary top-N ranking. Render meaningful stored metrics only, explain missing coverage and overlapping counts.
- [x] Load jobs only when expanded, paginate server-side with location/status scope, preserve body on jobs error, allow retry and discard stale requests after company changes.
- [x] Keep Company / Topic roots; Topic shows concise unavailable explanation, no fictional topics/relations. Replace navigation label with HR 情报. Remove active legacy report/changes/history UI, preserve old route compatibility.
- [x] Emit explicit company/claim/job-range references with immutable identities and bounded excerpt. Opening evidence or changing filters does not select. Readable action disabled with explanation if no host integration.
- [x] Verify component tests, real-derived parser tests and production build. Commit only UI-owned files; report at `.superpowers/sdd/company-ui-report.md`.

## Task 3: Explicit selection into HR conversation

**Files:** Create `webui/src/workspaces/hr/hrIntelligenceReference.ts` plus tests; modify `HrWorkspacePage.tsx`, `DirectAgentWorkspace.tsx`, `pages/ConversationPage.tsx` and submission tests only as needed. Do not modify Task 2 components; connect callback at host.

**Interfaces:** Export `HrIntelligenceReference` shape from Task 2 contract. Host owns account-scoped selected references and passes `onSelectReference` to workspace. New and existing conversation composers show removable, inspectable selection. Keep user text independent. Encode bounded reference text and immutable IDs as explicit user-selected reference material within existing durable user message on send, using one shared formatter; do not claim this is server-authoritative metadata. Existing turn idempotency/freezing persists the exact serialized message, without a new pipeline or schema.

- [x] Test explicit select does not send or replace draft, returns to current chat if known otherwise free-chat draft, and survives page navigation/account scoping.
- [x] Test shared reference formatter includes pinned identity, label, excerpt and sources as data, enforces a UTF-8 aggregate budget of 12 KiB, refuses silent truncation, and does not treat materials as execution instructions.
- [x] Add shared optional composer reference props across both new and existing chat paths; calculate send size with the actual formatted text. Pending submission snapshots references alongside text/attachments; failed retry preserves request, successful submission removes only matching submitted reference keys. User edits create a new submission as existing behavior specifies.
- [x] Add visible inspect/remove selection controls and return link to company page; reference changes must not overwrite editor text or attachment state. Read-only/pending controls follow current send policy.
- [x] Verify durable existing HTTP input path can carry formatted reference and preserve retries; distinguish engineering validation from actual Agent/model retrieval. Do not claim ordinary autonomous topic discovery or M1/M2/M3 fixed by this handoff.
- [x] Commit only owned integration files; report at `.superpowers/sdd/company-reference-report.md`.

## Task 4: Integration and acceptance

**Files:** Add focused real-bundle HTTP acceptance script/test under `backend/tools/` or `backend/tests/`; add evidence and limitations under `docs/reviews/2026-09-08-hr-company-intelligence-implementation.md`; update this checklist.

- [x] Use disposable local PostgreSQL and real migrations/roles/import path with sample bundle; exercise new APIs using actual signed-in HR identity and denied identity. Never use production DB or send model/business messages.
- [x] Capture endpoint payload bytes and local request timings as local evidence, not production speed claims; ensure first list excludes jobs/evidence blobs and detail excludes job bulk.
- [x] Run relevant backend/API/repository regression and frontend tests, then build. Audit new API outputs with frontend parser using real response artifacts.
- [x] Perform one final browser pass for company selection, reading, evidence, pagination, return and reference composer states, if browser capability is available. Record any unverified state precisely.
- [x] Task-specific review then final combined review, fix important findings and rerun affected tests; record commit/test evidence and outstanding upstream/runtime limitations. Keep branch for user review, no deployment or merge.

## Progress

- Baseline backend: panorama API/service 18 passed. Frontend dependency install needed in this worktree; no application failure observed before install.
- Plan reviewed against approved design: company implementation is independently scoped; full topic and ordinary Agent discovery remain distinct dependencies and must not be declared complete by selection transport.

- 公司 API、UI、显式选材分项复审通过；真实 HTTP 与浏览器验收通过，证据见 docs/reviews/2026-09-08-hr-company-intelligence-implementation.md。最终联合复审通过，最后运行代码 c279cdb；真实页面重复点击回归及 HTTP 复验通过。
