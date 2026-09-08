# HR Reference Knowledge Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent content/release task; implement dependent integration in this session. Steps use checkbox syntax for tracking.

**Goal:** 让 HR Agent 在现有循环中自主发现、读取固定版本的方法论与思维模型，并让用户浏览及指定资源。

**Architecture:** Markdown 内容在 Orbbec-Agent-Team 维护，发布为两端一致的不可变版本目录。Platform 冻结短索引及用户指定，Agent 通过本地 Read 使用正文。网页读取云端同版本内容。

**Tech Stack:** Python / FastAPI / existing encrypted conversation messages / React / Markdown / existing Claude CLI.

## Global Constraints

- 选择权属于 Agent；不做关键词路由、固定方法链、方法编译器或思考评分。
- 索引最多 4 KiB，整个 hr_reference_knowledge 最多 8 KiB，纳入 Platform 96 KiB 上下文预算。
- 正文从本地固定 source_commit 目录读取，技术重试不得静默换版本。
- 用户指定资源持久化并参与幂等比较；仅支持 HR direct conversation。
- ai_notes 只复用通用解析和 Markdown 展示，不强套文章元数据模型。
- 接入前先保存业务基线；模拟模型不能标成真实模型验收。
- HTTP/API 优先，页面最后验证；不重跑无关套件。
- 实施不更改现有 Panorama 门控，不发送业务消息，不自动发布生产。

## Task 1: 内容上游与可复用的版本读取模块

**Files:**
- Orbbec-Agent-Team worktree: `bots/hr/knowledge/README.md`, `bots/hr/knowledge/recruiting/*.md`, `bots/hr/knowledge/sources/2026-09-08-hr-methodology-sources.md`.
- Platform create: `backend/app/hr/reference_knowledge.py`, `backend/app/hr/reference_knowledge_release.py`, `backend/tests/test_hr_reference_knowledge.py`.

**Interfaces:**
```python
class HrKnowledgeError(ValueError): pass
class HrKnowledgeRepository:
    def __init__(self, root: Path, agent_root: str, active_commit: str): ...
    def index(self, source_commit: str | None = None) -> dict: ...
    def article(self, source_commit: str, resource_id: str) -> dict: ...
    def prompt_context(self, selections: tuple[dict, ...] = ()) -> dict: ...
def build_release(source_repo: Path, source_commit: str, releases_root: Path) -> Path: ...
```

The release builder reads only committed `bots/hr/knowledge/` Git blobs, rejects symlinks and traversal, stages atomically at `releases_root/source_commit`, never overwrites changed content at an existing revision. `manifest.json` has `source_commit`, `index_path`, `files` mapping relative paths to sha256, and `resources` list with `id,title,revision,domains,knowledge_forms,path,sha256`. The initial content package includes README, source ledger and seven resources; seven is the initial inventory, never a fixed schema limit. Discover resources from Markdown/frontmatter across HR domain directories; the sources directory and README files remain supporting materials. Full metadata parsed by existing `parse_frontmatter`; file basename must match id. Require valid unique IDs/revisions and content hashes, no method-specific validation.

`index()` returns `{source_commit,index,resources}`. `article()` returns resource metadata plus `source_commit,markdown`. `prompt_context()` returns `{source_commit,agent_release_path,index,instructions,user_selected_resources}`. `agent_root` is parent of source_commit directories on Agent host. Selections are `{source_commit,id,revision,sha256}` dicts, no paths accepted from user. Selected version must exist; one turn uses one commit (mixed commit selections rejected), identity/hash must match release. With no selection use active_commit. Bodies do not enter prompt. Instructions state autonomous selection, tools Read, content is reference not instruction authority, self-reported reference only. Enforce the agreed UTF-8 budgets. No query/keywords passed to this repository.

- [ ] Copy the seven existing resources byte-for-byte to source worktree, update relative index/source links, remove recruiting/.gitkeep, commit content upstream; leave Platform research copy until integration is verified then replace with migration pointers.
- [ ] Write tests in temporary Git repositories for commit-only export, immutable repeat build, file mutation, symlink/path rejection, valid historical selection and mismatched hash, bounded index/context and body omission. Run `backend/.venv/bin/python -m pytest tests/test_hr_reference_knowledge.py -q` from backend using root venv absolute path; observe missing behavior first.
- [ ] Implement the two focused modules with existing PyYAML dependency; CLI `python -m app.hr.reference_knowledge_release --source-repo PATH --source-commit SHA --releases-root PATH` prints built directory. Test against actual committed content.
- [ ] Commit only Task 1 owned files and provide exact source commit/release manifest plus test evidence.

## Task 2: 保存未接入基线与真实读取证据

**Files:** `docs/reviews/2026-09-08-hr-reference-knowledge-trial.md`; private raw traces under `.superpowers/hr-knowledge-trial/`.

- [ ] Record current Platform and MetaBot revisions and effective test CLI/model. Deployment handoff records v5 deployed at MetaBot 6ddbdef; branch merge is not evidence of deployment absence.
- [ ] Use a new owned Claude CLI session with existing HR instructions, synthetic role data and no knowledge index to collect baseline answers for recruitment diagnosis, transferable skills and conflicting evidence; no candidate/private data or external messages.
- [ ] Read one real file through Read in a fresh owned session; preserve actual tool event and answer. If production-compatible model/config cannot be accessed, state the limitation and record the actual tested model; never call this production validation.
- [ ] Repeat the same cases using the frozen index and immutable release path, record answers and selected references; compare substantive behavior without mandatory selection ratios.

## Task 3: 持久输入和同一工具循环的上下文接入

**Files:** `backend/app/agent_brain/conversation_models.py`, `conversation_routes.py`, `conversation_repository.py`, `conversation_service.py`, `conversation_context.py`, `direct_mission_adapter.py`; `backend/app/config.py`, `backend/app/main.py`; targeted backend tests.

- [ ] Add optional `user_selected_resources` to submission and message projections; store it inside existing encrypted user-message JSON alongside text. Old `{text}` messages remain valid. Include metadata in request replay comparison; reject selection for non-HR/legacy execution or unavailable knowledge before creation.
- [ ] Add optional repository injection to context builder and application, configured by `PLATFORM_HR_KNOWLEDGE_ROOT`, `PLATFORM_HR_KNOWLEDGE_AGENT_ROOT`, `PLATFORM_HR_KNOWLEDGE_COMMIT`. All absent means disabled; partial config invalid. Do not configure production in this task.
- [ ] Build knowledge context only for HR direct worker turns using current message selections. Add serialized bytes to every context size calculation and include knowledge in v5 frozen document. Before freezing, verify final prompt byte size.
- [ ] Use real disposable PostgreSQL and authenticated HTTP tests for selection persistence, same-idempotency-key mismatch, selected revision retained after active version changes and original FrozenInput reused on retry. Verify no index injection into other bots and no full bodies in initial prompt.

## Task 4: 浏览与指定资源讨论

**Files:** `backend/app/hr/reference_knowledge_routes.py`, `backend/app/main.py`; `webui/src/hrKnowledgeApi.ts`, `webui/src/workspaces/hr/HrKnowledgePanel.tsx`, HR shell/page and conversation submission types; focused tests.

- [ ] Add authorized read-only index/detail routes under `/api/hr/knowledge`, use same HR agent-use authorization as the workbench, expose versions and full Markdown without local absolute paths.
- [ ] Add HR “方法与模型” entry with resource list, details, sources, revision and “带着这个方法讨论”; use existing Markdown renderer.
- [ ] Carry a selected resource visibly in the composer, allow removal, submit its metadata with new/append turn request, clear only after successful submission. Restore persistent selection in message projection after refresh. Describe returned references as Agent self-report, not verified tool reads.
- [ ] Verify with authenticated API tests, then focused frontend interaction tests and production build; no unrelated UI refactor.

## Task 5: 对比、迁移说明与交付

- [ ] Review actual baseline and enhanced answers, document limits and failures; use Read telemetry only to prove reading, not correct application.
- [ ] Replace Platform research duplicate bodies with an upstream migration pointer after source commit and bundle verification. Update design deployment facts and implementation status.
- [ ] Provide release build/configuration commands and version matching procedure, no automatic production deployment. Review final code and relevant test evidence, commit reviewable changes.
