# 工程知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将线上 AI 工程笔记升级为精选五领域的工程知识库，保留 14 篇原文并新增 15 篇经确认的制造、系统与 SDK 架构文稿。

**Architecture:** 保留现有 FastAPI 文件白名单目录、两层分类和 React 阅读器，不增建内容平台。旧 URL 通过文章 frontmatter 的显式 legacyPaths 查找同一篇规范文章；API 返回规范分类，阅读器用 replace 导航更新地址。正文保留既有长文和目录，标签表达文档类型，来源与状态在正文注明。

**Tech Stack:** Python/Pydantic/FastAPI、Markdown/YAML、React/TypeScript/Vitest、Mermaid、现有 Docker Compose scoped-release。

## Global Constraints

- 页面名称为“工程知识库”，保留 `/ai-notes` 和 `/api/v1/ai-notes` 路由。
- 五个分类按顺序为 `ai-and-agents`（AI 与 Agent）、`sdk-architecture`（SDK 系统架构）、`manufacturing-processes`（制造业务与流程）、`enterprise-systems`（企业系统与集成）、`intelligent-manufacturing`（智能制造架构）。
- 仅收录 `/Users/neo/Developer/work/知识库梳理/本轮收录范围.json` 确认的 15 篇新增文稿，加原有 14 篇，共 29 篇；每篇只有一个主分类。不导入 FAE 文库、SDK 场景卡或其他被排除文章。
- 原有 14 篇文章保留正文和文章 slug，全部旧分类/文章 URL 均可读取同一篇文章。未知路径保持 404，草稿不可经别名读取。
- 制造文稿和 SDK 稿的源文件只读；只在平台副本修正文中已审阅问题、来源链接、格式和必要状态说明。长篇手册、完整方案、总体架构各自保留全文及章目录。
- SDK 为架构导读，不增加具体痛点/排障场景；保留静态研究基线、八仓边界和未做设备运行验收的限定。
- 所有公开文章正文不得有本机绝对路径或断开的本地 Markdown/JSON 链接；指向已收录文章用规范站内路径，其余配套来源用真实仓库固定提交链接或明确的可复核来源标识。
- 所有正式文章作者为“苍渊”、motto 为“博观而约取，厚积而薄发。”；原文章日期保持，新收录日期为 `2026-11-11`。材料查阅日期和实现历史快照单独保留，不冒充收录日现状。
- Mermaid 保持语义、关系和解释，每图唯一 accTitle/accDescr、语义配色、大分组白底；正文无 H1 或原生 HTML。
- 遵守 AGENTS.md；原始 checkout 的未跟踪文件不动；不变更无关功能、数据库或共享 Nginx。开发侧做接口/组件验证，浏览器验收由用户负责。
- 从审查并合入主线的精确提交发布；先核对实际 API/UI 基线，仅更新本任务代码/内容与静态资源，保留原 Compose 有序链、配置和其他容器身份。部署及回滚证据单独记录。

## File boundaries

- `backend/app/ai_notes/models.py`：可选迁移别名元数据，API 摘要/文章形状保持原样。
- `backend/app/ai_notes/repository.py`：加载时建立并验证别名索引，不使用请求路径访问文件系统。
- `webui/src/pages/AiNotesPage.tsx`、`components/ai-notes/AiNotesTree.tsx`：规范路径选择、分类/搜索与名称。
- `backend/app/ai_notes/content/`：五分类 29 篇正式文章。
- `docs/knowledge-library/2026-11-11-source-manifest.json`：本批新增文章来源、原始哈希、规范路径、改动和基线记录。
- `docs/standards/ai-engineering-notes.md`、`backend/app/ai_notes/README.md`：新版准入、别名和验证说明。
- `docs/operations/2026-11-11-engineering-knowledge-release.md`：实际测试、主线/线上版本、接口与资源完整性、回滚信息。

### Task 1: 兼容迁移与工程知识库入口

**Files:**
- Modify: `backend/app/ai_notes/models.py`, `backend/app/ai_notes/repository.py`, `backend/app/ai_notes/README.md`
- Modify: `webui/src/pages/AiNotesPage.tsx`, `webui/src/components/ai-notes/AiNotesTree.tsx`, `webui/src/documentTitle.ts`, `webui/src/pages/BrainPage.tsx`, `webui/src/platform/PlatformSidebar.tsx`, `webui/src/panorama/PanoramaCanvas.tsx`, `webui/src/App.tsx`
- Test: `backend/tests/test_ai_notes_repository.py`, `backend/tests/test_ai_notes_api.py`, `webui/src/pages/AiNotesPage.test.tsx`, `webui/src/components/ai-notes/AiNotesTree.test.tsx` and affected label tests.

**Interfaces:**
- Consumes: `AiNotesRepository.load(root: Path, *, today: date)`, `.article(category_slug, article_slug)`, existing API contract and frontend client.
- Produces: optional article YAML `legacyPaths: ["foundations/agent-engineering-learning-map"]` (no leading slash); API article stays existing exact keys and returns canonical `category_slug`/`slug`; new URLs remain `/ai-notes/{category}/{article}`.

- [ ] **Step 1: Add meaningful failing repository/API and reader tests.** Use existing fixture helpers. Test old path resolves to canonical article, canonical and alias collide -> fail load, malformed/duplicate alias -> fail load, draft alias -> 404, unknown old path -> 404. Reader test returns article with canonical category differing from requested and asserts `onNavigate('/ai-notes/ai-and-agents/example', {replace:true})` plus canonical tree selected/expanded. Search tests cover category, article title, tags and description; do not add tests for every label.

```python
# Core alias contract, adapt to fixture helpers already in the test file.
canonical = repository.article("ai-and-agents", "example")
assert repository.article("foundations", "example") == canonical
assert repository.article("foundations", "missing") is None
```

- [ ] **Step 2: Run focused tests before implementation and record expected failures.**

```sh
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_repository.py tests/test_ai_notes_api.py -q
cd ../webui
npm test -- src/pages/AiNotesPage.test.tsx src/components/ai-notes/AiNotesTree.test.tsx
```

- [ ] **Step 3: Implement minimal alias loading and canonical navigation.** Add `legacy_paths: tuple[str, ...] = Field(default=(), alias="legacyPaths")` to ArticleFrontmatter only. Validate each alias with full safe category/article slug grammar and uniqueness. Build aliases after collecting canonical article identities; reject alias collision with any canonical path or other alias (including drafts); draft identities participate in collision validation but never resolve publicly. Repository constructor may accept optional alias dict for fixture compatibility, but preserve all old constructor call sites. Request lookup validates grammar then exact canonical or alias dict only. Frontend replaces the route when API returns a differing canonical identity; selected category follows resolved identity. Keep request cancellation/stale-result protection and existing missing/unavailable behavior. Avoid type contract changes.

```ts
if (loaded.category_slug !== selectedPath.categorySlug || loaded.slug !== selectedPath.articleSlug) {
  onNavigate(`/ai-notes/${loaded.category_slug}/${loaded.slug}`, { replace: true });
}
```

Rename user-visible entry titles to 工程知识库 throughout the listed navigation/app surfaces, leave internal API identifiers unchanged. In tree hide raw `.md` filenames, keep article title and useful description; search title/description/tags/category plus filename compatibility. Do not add extra nested category UI.

- [ ] **Step 4: Document aliases and run focused suites plus build.** Update relevant existing tests to new entry text only where they cover behavior. Do not alter production corpus yet. Run all `backend/tests/test_ai_notes_*.py`, relevant notes/navigation/documentTitle frontend tests, `npm run build`; report pre-existing unrelated failures separately.
- [ ] **Step 5: Self-review and commit.** Commit only this task's files; full report must include red/green evidence, command/results and concerns. Controller obtains task review before content work.

### Task 2: 五分类精编内容迁移

**Files:**
- Move/create: `backend/app/ai_notes/content/{01-ai-and-agents,02-sdk-architecture,03-manufacturing-processes,04-enterprise-systems,05-intelligent-manufacturing}/`
- Create: `docs/knowledge-library/2026-11-11-source-manifest.json`
- Modify: `docs/standards/ai-engineering-notes.md`, `backend/app/ai_notes/README.md`
- Test: `backend/tests/test_ai_notes_production_content.py`, `backend/tests/test_ai_notes_eight_article_batch.py`, `backend/tests/test_ai_notes_authoring_assets.py`, affected `webui/src/components/ai-notes/*.test.*` content discovery paths.

**Interfaces:**
- Consumes: Task 1 `legacyPaths` contract, existing metadata/reading/diagram validation. Source manifest: `/Users/neo/Developer/work/知识库梳理/本轮收录范围.json`, SDK draft and provenance JSON beside it.
- Produces: exact five-category 29-article catalog below, safe canonical links, unchanged API schema.

**Canonical catalog and order:**

| Category | Article slugs in reading order |
|---|---|
| ai-and-agents | original 14 slugs in original category/file order, numbered 01..14 |
| sdk-architecture | orbbec-sdk-system-architecture |
| manufacturing-processes | manufacturing-system-handbook, manufacturing-process, mrp-worked-example, master-data-and-engineering-change, quality-lean-and-test-data, assessment-and-roadmap, metrics-and-business-acceptance, comparison-and-transfer |
| enterprise-systems | system-landscape, process-and-system-boundaries, integration-and-digital-thread |
| intelligent-manufacturing | oem-ai-manufacturing-platform, manufacturing-platform-architecture, ai-in-manufacturing |

- [ ] **Step 1: Add a failing catalog/compatibility test.** Update frozen publication date in production tests to release date where loading the whole corpus, without changing asserted historic publication dates. Assert exact new category order/article slug sequences and all 14 old URLs resolve to canonical AI articles. Assert added article bodies contain no local absolute path and every local publication link targets an existing canonical article/valid heading; no raw JSON/md relative reference survives. These are content integrity gates, avoid tests that parrot every editorial sentence.

```python
assert [category.slug for category in index.categories] == [
    "ai-and-agents", "sdk-architecture", "manufacturing-processes",
    "enterprise-systems", "intelligent-manufacturing",
]
assert sum(len(category.articles) for category in index.categories) == 29
```

- [ ] **Step 2: Run the new catalog test to confirm failure before migration.**

```sh
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_production_content.py -q
```

- [ ] **Step 3: Migrate existing and selected source articles.** Read selected sources and exact `minor_fixes`/`content_issues` from scope JSON; apply necessary corrections in platform copies only. Verify source hashes first; record observed changes rather than silently using altered originals. Rename source H1 to frontmatter title and shift heading levels only as needed to maintain no-H1, retaining all chapter text, tables and original TOCs with repaired anchors. Add document-type tags (手册/架构/方案/案例/方法 as suitable) to new articles; preserve original14 tags to retain old assertions. Mark overall design target identity and historical Nov9 snapshot; preserve Oct27 research dates and source strength caveats. New frontmatter uses draft true until all gates reviewed, final artifact draft false for approved corpus.

For links, map selected source files to canonical `/ai-notes/...` paths. For unselected manufacturing companion/source files use `https://github.com/ink-hz/AI-Manufacturing-Platform/blob/53958c7cede6707c41b99cba7c636402f3c23fe3/{path}` only if file is committed at that SHA; otherwise use actual source repo identity/verified fixed commit. Do not import unselected articles just to satisfy links. SDK local citations must retain source titles, relative paths/line references and precise baseline via source appendix or verified remote links; no fake public SHA. Keep a provenance manifest linking source hashes and all edits. Existing AI article bodies stay unchanged; their historic links may use legacy routes (which resolve) rather than rewriting them.

The standards become engineering-knowledge scope: five domains, precision over volume, manuals and architecture can remain complete, exactly one primary category, types via tags, sources/status clearly separate. Existing AI-only requirement is superseded. Browser visual acceptance explicitly remains user's responsibility under AGENTS.md; record unperformed acceptance, never claim it done.

- [ ] **Step 4: Validate all publication policies, corpus tests, Markdown links and Mermaid.** Real Mermaid render/DOMPurify tests dynamically discover published content; adjust only obsolete hardcoded content paths, not validation rigor. New diagrams may need unique accTitle/accDescr, explicit styles and accessible text while keeping relationship semantics. Run:

```sh
cd backend
.venv/bin/python -m app.ai_notes.validate
.venv/bin/python -m pytest tests/test_ai_notes_*.py -q
cd ../webui
npm test -- src/components/ai-notes src/pages/AiNotesPage.test.tsx src/aiNotesApi.test.ts src/aiNotesTypes.test.ts
npm run build
```

- [ ] **Step 5: Self-review all selected edits and commit.** Report exact source/canonical mapping, source hash verification, minimal corrections, link/diagram counts, tests and unverified runtime/visual limits. Controller obtains scoped content+quality review and whole-branch review.

### Task 3: 合入主线与安全发布

**Files:**
- Create: `docs/operations/2026-11-11-engineering-knowledge-release.md`
- Use: `deploy/cloud/runbooks/scoped-api-release.md`, `deploy/cloud/scoped-api-release.py` and actual latest migration-free assembler; existing UI static release path from actual deployment audit.

**Interfaces:**
- Consumes: reviewed branch, source provenance, actual production baseline audit `/Users/neo/Developer/work/知识库梳理/知识库发布基线审计.md`.
- Produces: merged/pushed master commit, exact API/UI deployed versions and verified release/rollback evidence. No database changes.

- [ ] **Step 1: Resolve all task/broad-review important findings; run fresh scoped backend and frontend tests and build once.** Include route compatibility, full catalog and real published Mermaid rendering. Check `git diff --check` and `git status`; no unrelated files in commit.
- [ ] **Step 2: Fetch master and merge without overwriting parallel changes.** Recheck shared master worktree clean status, integrate exact reviewed feature commit preserving main history, push master; avoid resets/force. If master moved, assess conflicts and revalidate affected change.
- [ ] **Step 3: Prepare reviewable scoped API and UI candidates from live baseline.** Capture actual immutable image, release SHA, ordered Compose inputs, all protected containers, UI file inventory and symlink. Build API by replacing only reviewed notes code/content atop actual live image. UI candidate uses precise merged source with source-baseline parity checked. Preserve secrets, OS/dependencies and runtime config; no Nginx configuration or reload needed.
- [ ] **Step 4: Execute controlled deployment and verify.** Standard helper plan/inspect/apply for API, atomic existing UI static release switch; do not touch other services. Verify authenticated existing notes HTTP interfaces enumerate five domains/29 articles and all old14 URLs return correct canonical identity, unknown paths404; verify served static resource complete bytes/hashes match build and protected services unchanged. If auth prevents HTTP acceptance, use approved existing production test identity/session or local API process from candidate with equivalent real router/auth; never fabricate a production success claim.
- [ ] **Step 5: Record immutable versions, verification results and rollback.** Keep transaction receipts, old image/Compose chain and previous UI release symlink. Operations record distinguishes API/backend, component/Mermaid, production HTTP/resource checks and user browser acceptance pending. Commit/push evidence documentation; no new deployment needed for doc-only commit.
