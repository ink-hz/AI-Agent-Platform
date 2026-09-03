# HR Agent Web Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 HR Agent 网页版升级为可上传和复用材料、可靠接收和下载生成文件、展示联网来源、恢复长任务并闭环收集反馈的通用专业 Agent 会话工作台。

**Architecture:** Agent Platform 是 Conversation、附件、版本、引用、反馈和审计的唯一事实源；浏览器只通过 Platform API 上传和下载，MetaBot 只通过绑定 `task_id + attachment_id + agent_id` 的短期 Grant 读写文件。现有 Flywheel 管理附件票据保持兼容，新建的 Conversation Attachment 路径使用 Control DB 与私有对象存储，并通过 `core_chat_collaboration_v4` 与 MetaBot 交换结构化输入、公开回答、引用和结果文件。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、PostgreSQL 17、psycopg 3、boto3/S3、ClamAV、Pillow、Poppler、React 19、TypeScript、Vite、Vitest、Node.js/MetaBot、pytest、Docker Compose、Nginx。

## Global Constraints

- 本计划实现并替代 `docs/superpowers/plans/2026-08-27-agent-attachment-substrate-implementation.md`；旧计划中的迁移号和“任务可在文件仍处理中时完成”的语义不再适用。
- 先合并或重新基于 `feat/agent-workspace-route-separation` 与 `feat/fae-independent-access` 的最终主线；`063_fae_workbench_access.sql` 已被现有工作占用，本计划固定使用 `064_conversation_attachments.sql`。若主线在开工前已占用 064，整份计划机械改号后再写测试，绝不改写已执行迁移。
- 保留 `/api/attachments/{attachment_id}/ticket` 与 `/api/attachments/content/{ticket}` 的 Flywheel 管理路径及其只读 `AttachmentStore`；Conversation 附件使用独立的 repository、writer 和 `/api/v1/attachments/*` 路径。
- 一个网页 Session 等于一个 Platform Conversation；MetaBot Session、Mission、Run、Task 只作下游执行和治理对象。
- Platform Attachment Service 是输入和输出文件的唯一事实源。浏览器和 MetaBot 均不得获得 MinIO 凭据、Object Key、永久 URL或服务器本地路径。
- 所有对象键为随机值；原始文件名、对象引用、Grant 元数据和反馈正文按现有 `ContentCodec` 规则加密或最小化保存。普通日志不得记录正文、原始文件名、Bearer Grant、对象键、候选人资料或完整反馈。
- 只有 `ready` 对象可以预览、下载或签发输入 Grant。校验和扫描 fail closed；Agent 输出与用户输入走同一套验证、扫描和派生流程。
- 单文件 50 MB；单消息最多 5 个、合计 50 MB；单 Conversation 用户输入最多 50 个、合计 500 MB。输出另设每个 Task 最多 20 个、合计 250 MB；输入与输出及其版本默认保留 365 天。
- 消息提交是原子的：所选附件必须全部属于当前用户、绑定当前 Conversation、状态为 `ready`，且符合 Agent Catalog 能力；同一 `client_request_id` 的重放必须同时匹配文本和附件选择。
- 每轮只把 `active_attachment_ids` 对应的材料发给 Agent；上传进 Session 不等于自动永久注入后续上下文。
- `completed` 只能在公开回答、引用和所有声明交付的结果文件均已持久化且结果状态确定后出现；失败版本不得替换最新成功版本。
- 点踩进入 `pending_triage`，不自动创建工程 Issue。补充内容最多 1,000 个 Unicode code point，后端是最终校验边界。
- HR 历史、新标签页、复制链接和刷新必须使用规范 Agent 作用域路由；前端 click handler 不是作用域安全边界。
- P0 不提供普通同事分享、Office 在线编辑、高保真 Office 预览、飞书完成通知、全文搜索或会话整体导出。
- 任何跨仓库提交都保持单一目的；先提交 Platform 协议和测试，再提交 MetaBot v4 支持，最后启用 HR Catalog 能力。

### Production disk and release discipline

- 根盘 `/` 约 100 GB，只承载系统、当前应用 release 与最近两个可回滚 release；历史 release 只能归档到 `/data/archive/<application>/releases/`，并按“最多 10 个或 30 天，取更严格者”清理。
- release 只包含代码和构建产物；禁止包含 `data/`、`uploads/`、`logs/`、`index/`、`answer_reviews/`、knowledge 数据副本、数据库文件、`.venv/`、`node_modules/`、模型缓存或其他持久/持续增长内容。
- 所有持续增长的数据必须位于 `/data/<application>/`，包括 ClickHouse、PostgreSQL、Langfuse、附件、业务索引、分析结果、数据库备份和长期日志；release 目录不得承载持久数据。
- 每次部署只可使用 `/data/staging/<application>/<deployment_id>/`；成功、失败和信号退出都必须由 `trap` 精确清理本次 deployment ID。不得长期使用 `/tmp`，不得留下 tarball、`.part` 或半成品，也不得以宽泛路径或 glob 清理。
- 每个服务只保留当前镜像和最近两个回滚镜像；只删除已经过服务归属、保留集合和容器引用核验的更旧镜像。禁止无目标的 `docker system prune -a`，不得影响其他应用镜像。
- 发布前必须执行 `df -B1 / /data`。根盘可用空间低于 25 GB、计算 staging 与镜像后预计低于 20 GB、或预计发布后根盘使用率超过 75% 时禁止发布；发布净增长超过 1 GB 必须逐项解释，异常增长必须先停止并报告。
- 共享服务器严格按应用边界变更：AI ADMIN 不得覆盖 Platform Nginx Server Block；Platform 不得修改 `/office/`；FAE、VOC、HR、Marketing 不得改写其他应用目录；共享 Nginx 变更必须持有 Platform 发布锁并完成全量验收；不得重启无关服务。
- 发布报告必须记录部署前后 `df`、新增文件/目录大小、当前版本、两个本地回滚版本、归档/删除历史版本、staging 清空证据、当前/回滚 Docker 镜像、业务页面 HTTP 验收，以及是否修改其他应用或共享 Nginx。

---

### Task 1: 固定主线前置条件与 Conversation Attachment 数据模型

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/control_migrations/064_conversation_attachments.sql`
- Create: `backend/tests/test_conversation_attachment_migration.py`
- Modify: `backend/tests/test_control_plane_migration.py`

**Interfaces:**
- Schema: `platform_attachments`
- Tables: `attachments`, `uploads`, `bindings`, `artifacts`, `artifact_versions`, `derivatives`, `task_grants`, `access_events`, `processing_jobs`, `erasure_jobs`, `message_citations`, `conversation_read_state`
- Alter: `platform_control.conversation_feedback` adds expanded reason check and `triage_status`

- [ ] **Step 1: 建隔离工作树并证明迁移号可用**

```bash
git status --short
git worktree add .worktrees/hr-agent-web-workspace -b feat/hr-agent-web-workspace master
cd .worktrees/hr-agent-web-workspace
test ! -e backend/control_migrations/064_conversation_attachments.sql
test -e backend/control_migrations/063_fae_workbench_access.sql
```

Expected: 064 不存在，063 已来自前置主线；若第二条失败，先完成前置分支集成，不在本分支复制 063。

- [ ] **Step 2: 写失败的迁移测试**

测试必须断言：

- 所有表、外键、唯一键和索引存在；
- `attachments.state` 仅允许 `uploading/validating/scanning/ready/quarantined/rejected/deleted`；
- `bindings.kind` 覆盖 `conversation_material/message_input/turn_input/task_input/task_output/message_output`；
- `artifact_versions` 的 `(artifact_id, version_no)` 唯一，且 current 由 ready 成功版本查询得出，不保存可漂移布尔值；
- Grant 仅保存 token SHA-256，限制 task、attachment、agent、scope、expiry、reads 和 bytes；
- 输入/输出、版本、派生物默认 `retained_until = created_at + interval '365 days'`；
- expanded feedback reason 和 `pending_triage/triaged/dismissed` 状态可写；
- `platform_control_app`、`platform_brain_worker`、`platform_control_maintenance`、`platform_audit_append` 只有完成各自职责所需权限。

- [ ] **Step 3: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_conversation_attachment_migration.py
```

Expected: FAIL，缺少 migration 064 和目标对象。

- [ ] **Step 4: 实现 migration 064**

原始文件名使用 `original_name_ciphertext/original_name_key_version`；对象引用使用 `object_ref_ciphertext/object_ref_key_version`；每个对象保存 `detected_mime/size_bytes/sha256/retained_until/state/state_reason`。加入 SECURITY DEFINER 函数完成 upload finalize、processing claim/result、Grant issue/consume/revoke、artifact version bind、access audit、read-state upsert 和 erasure claim/result，函数内校验 `current_user`。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_migration.py \
  backend/tests/test_control_plane_migration.py
git add backend/control_migrations/064_conversation_attachments.sql \
  backend/tests/test_conversation_attachment_migration.py \
  backend/tests/test_control_plane_migration.py
git commit -m "feat(attachments): add conversation attachment schema"
```

Expected: PASS；提交只包含 migration 与 migration 测试。

---

### Task 2: 实现私有对象写入、上传生命周期与容量配额

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/attachments/conversation_models.py`
- Create: `backend/app/attachments/conversation_repository.py`
- Create: `backend/app/attachments/object_writer.py`
- Create: `backend/app/attachments/upload_service.py`
- Create: `backend/tests/test_conversation_attachment_repository.py`
- Create: `backend/tests/test_attachment_upload_service.py`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/test_config.py`

**Interfaces:**

```python
class ConversationAttachmentRepository:
    def create_upload(self, owner_id: UUID, conversation_id: UUID | None,
                      original_name: str, declared_mime: str,
                      declared_size: int) -> UploadRecord: ...
    def complete_upload(self, owner_id: UUID, upload_id: UUID,
                        actual_size: int, sha256: bytes) -> AttachmentRecord: ...
    def list_conversation_assets(self, owner_id: UUID,
                                 conversation_id: UUID) -> ConversationAssets: ...

class AttachmentObjectWriter:
    def put_stream(self, object_ref: str, body: BinaryIO,
                   expected_size: int) -> ObjectReceipt: ...
    def delete(self, object_ref: str) -> None: ...

class AttachmentUploadService:
    def begin(self, owner_id: UUID, request: BeginUpload) -> UploadRecord: ...
    def write(self, owner_id: UUID, upload_id: UUID, body: BinaryIO,
              content_length: int) -> UploadRecord: ...
    def complete(self, owner_id: UUID, upload_id: UUID) -> AttachmentRecord: ...
```

- [ ] **Step 1: 写 repository 与 service 的失败测试**

覆盖随机对象键、密文字段、50 MB 流式边界、声明/实际大小不一致、重复 complete、错误 owner、24 小时孤儿 upload、单 Session 50 个/500 MB 配额、部分写失败的对象清理和幂等重试。保留现有 `backend/tests/test_attachment_service.py` 中“旧 `AttachmentStore` 无写接口”的断言。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_repository.py \
  backend/tests/test_attachment_upload_service.py \
  backend/tests/test_attachment_service.py
```

Expected: FAIL，新模块不存在。

- [ ] **Step 3: 实现独立写路径和配置**

新增 Control DB DSN、S3 endpoint/bucket/access-key-file/secret-key-file、上传 TTL、输入/输出配额配置；密钥只从 0600 文件读取。`AttachmentObjectWriter` 流式计算 digest，不把完整文件缓存在内存，不修改旧 `AttachmentStore`。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_repository.py \
  backend/tests/test_attachment_upload_service.py \
  backend/tests/test_attachment_service.py \
  backend/tests/test_config.py
git add backend/app/attachments/conversation_models.py \
  backend/app/attachments/conversation_repository.py \
  backend/app/attachments/object_writer.py \
  backend/app/attachments/upload_service.py backend/app/config.py \
  backend/tests/test_conversation_attachment_repository.py \
  backend/tests/test_attachment_upload_service.py backend/tests/test_config.py
git commit -m "feat(attachments): add private conversation uploads"
```

---

### Task 3: 校验、恶意文件扫描与安全预览派生

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/attachments/validation.py`
- Create: `backend/app/attachments/scanner.py`
- Create: `backend/app/attachments/derivatives.py`
- Create: `backend/app/attachments/worker.py`
- Create: `backend/app/attachments/worker_runtime.py`
- Create: `backend/tests/fixtures/conversation_attachments/`
- Create: `backend/tests/test_attachment_validation.py`
- Create: `backend/tests/test_attachment_scanner.py`
- Create: `backend/tests/test_attachment_derivatives.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/requirements.cloud.txt`
- Modify: `backend/tests/test_requirements.py`

**Interfaces:**

```python
class AttachmentProcessor:
    async def process_next(self) -> bool: ...

class MalwareScanner(Protocol):
    def scan_stream(self, chunks: Iterable[bytes], *, size: int) -> ScanResult: ...

class DerivativeBuilder:
    def build(self, source: OpenedObject, detected_mime: str) -> tuple[Derivative, ...]: ...
```

- [ ] **Step 1: 建安全 fixture 并写失败测试**

fixture 包含 PNG、JPEG、PDF、TXT、DOCX、XLSX、PPTX、扩展名/MIME 不匹配、截断文件、ZIP bomb 元数据、带密码 Office、SVG/HTML、脚本、EICAR。测试断言声明 MIME 不可信；扫描前不执行解析；扫描不可用时对象不进入 `ready`；图片缩略图和 PDF 首屏由隔离 worker 生成；Office P0 只返回元数据与下载能力。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_validation.py \
  backend/tests/test_attachment_scanner.py \
  backend/tests/test_attachment_derivatives.py
```

Expected: FAIL，新模块和 fixture 未实现。

- [ ] **Step 3: 实现 fail-closed 状态机**

worker 按 `validating -> scanning -> ready|quarantined|rejected` 推进；重新计算 size/SHA-256、校验 magic 与 Office ZIP 结构、拒绝主动内容和加密文档。ClamAV 使用 INSTREAM 协议和固定超时；病毒库过期、daemon 错误、超时均保留不可读状态并指数退避。图片使用 Pillow 重新编码缩略图，PDF 使用 `pdftoppm` 在无网络、限 CPU/内存/时长子进程生成首屏 PNG。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_validation.py \
  backend/tests/test_attachment_scanner.py \
  backend/tests/test_attachment_derivatives.py \
  backend/tests/test_requirements.py
git add backend/app/attachments/validation.py backend/app/attachments/scanner.py \
  backend/app/attachments/derivatives.py backend/app/attachments/worker.py \
  backend/app/attachments/worker_runtime.py backend/tests/fixtures/conversation_attachments \
  backend/tests/test_attachment_validation.py backend/tests/test_attachment_scanner.py \
  backend/tests/test_attachment_derivatives.py backend/requirements.txt \
  backend/requirements.cloud.txt backend/tests/test_requirements.py
git commit -m "feat(attachments): validate scan and preview files"
```

---

### Task 4: 暴露成员级上传、状态、预览、下载与删除 API

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/attachments/conversation_routes.py`
- Create: `backend/app/attachments/download_service.py`
- Create: `backend/tests/test_conversation_attachment_api.py`
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/app/control_plane/middleware.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main.py`
- Modify: `backend/tests/test_attachment_api.py`

**Interfaces:**

```text
POST   /api/v1/attachments/uploads
PUT    /api/v1/attachments/uploads/{upload_id}/content
POST   /api/v1/attachments/uploads/{upload_id}/complete
DELETE /api/v1/attachments/uploads/{upload_id}
GET    /api/v1/attachments/{attachment_id}
POST   /api/v1/attachments/{attachment_id}/ticket
GET    /api/v1/attachments/content/{ticket}
DELETE /api/v1/attachments/{attachment_id}
GET    /api/v1/conversations/{conversation_id}/attachments
POST   /api/v1/conversations/{conversation_id}/artifacts/download
```

- [ ] **Step 1: 写失败的 API 安全测试**

覆盖 session/CSRF/Origin、非 owner、错误 Conversation、未知 ID 非枚举响应、上传进度、Content-Length 不符、非 ready 票据、Range、票据过期/重放、删除后访问、`Content-Disposition` 清洗、`X-Content-Type-Options: nosniff`、HTML/SVG/Office 强制 attachment，以及“全部下载”只包含 ready 且有权的输出版本。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_conversation_attachment_api.py
```

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现路由、短票据和 ZIP 下载**

ticket 最长 300 秒，仅一次或有限 Range 次数；ZIP 使用服务端流式 `zipfile` 临时卷，按 `(artifact_key, version_no, attachment_id)` 生成确定性去重文件名，响应完成后清理。将成员路径加入 `_AUTHENTICATED_SELF_ROUTES`，保留旧 `/api/attachments/*` Owner 路径不变。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_api.py \
  backend/tests/test_attachment_api.py backend/tests/test_main.py
git add backend/app/attachments/conversation_routes.py \
  backend/app/attachments/download_service.py backend/app/control_plane/authorization.py \
  backend/app/control_plane/middleware.py backend/app/main.py \
  backend/tests/test_conversation_attachment_api.py backend/tests/test_main.py
git commit -m "feat(api): expose conversation attachment lifecycle"
```

---

### Task 5: 把附件选择原子绑定到 Conversation Turn

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/agent_brain/conversation_models.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_service.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/conversation_context.py`
- Modify: `backend/app/agent_brain/conversation_projection.py`
- Modify: `backend/tests/test_agent_brain_conversation_repository.py`
- Modify: `backend/tests/test_agent_brain_conversation_api.py`
- Create: `backend/tests/test_conversation_attachment_binding.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ConversationTurnSubmission:
    text: str
    attachment_ids: tuple[UUID, ...]
    active_attachment_ids: tuple[UUID, ...]

class ConversationTextBody(BaseModel):
    text: str = Field(default="", max_length=32768)
    attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=5)
    active_attachment_ids: tuple[UUID, ...] = Field(default=(), max_length=50)
```

- [ ] **Step 1: 写失败的绑定与幂等测试**

断言文本可为空但必须至少有一个新附件；新附件必须在 active 集合；active 可包含当前 Session 的旧 ready 材料；错误 owner、非 ready、已删除、超配额、Agent 不支持附件均使整个消息/Turn 不落库；同 request ID 的文本或附件集合不同返回 conflict；集合顺序规范化后允许安全重放。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_binding.py \
  backend/tests/test_agent_brain_conversation_repository.py \
  backend/tests/test_agent_brain_conversation_api.py
```

Expected: FAIL，当前请求体只接受 `text`。

- [ ] **Step 3: 实现同事务绑定和消息投影**

在 `ConversationRepository.start()`、`append_turn()` 与内部 `_new_*_turn_locked()` 接收 `ConversationTurnSubmission`，使用同一 cursor 调用 attachment repository 的 `bind_turn_locked()`；响应消息包含该消息的 input/output attachments、active IDs、处理覆盖信息和不可用原因。历史文本中提到但无法找到可信对象的文件不生成假记录。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_conversation_attachment_binding.py \
  backend/tests/test_agent_brain_conversation_repository.py \
  backend/tests/test_agent_brain_conversation_api.py
git add backend/app/agent_brain/conversation_models.py \
  backend/app/agent_brain/conversation_routes.py \
  backend/app/agent_brain/conversation_service.py \
  backend/app/agent_brain/conversation_repository.py \
  backend/app/agent_brain/conversation_context.py \
  backend/app/agent_brain/conversation_projection.py \
  backend/tests/test_conversation_attachment_binding.py \
  backend/tests/test_agent_brain_conversation_repository.py \
  backend/tests/test_agent_brain_conversation_api.py
git commit -m "feat(conversations): bind active session materials"
```

---

### Task 6: 建 Task Grant、输出登记、版本和引用服务

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/attachments/grant_service.py`
- Create: `backend/app/attachments/artifact_service.py`
- Create: `backend/app/attachments/citation_service.py`
- Create: `backend/tests/test_attachment_grants.py`
- Create: `backend/tests/test_attachment_artifacts.py`
- Create: `backend/tests/test_conversation_citations.py`
- Modify: `backend/app/attachments/conversation_routes.py`
- Modify: `backend/app/control_plane/middleware.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class TaskAttachmentGrant:
    attachment_id: UUID
    display_name: str
    detected_mime: str
    size_bytes: int
    sha256_hex: str
    download_url: str
    bearer_token: str
    expires_at: datetime

@dataclass(frozen=True)
class OutputWriteGrant:
    task_id: UUID
    agent_id: str
    upload_url: str
    bearer_token: str
    max_files: int
    max_total_bytes: int

@dataclass(frozen=True)
class CitationInput:
    citation_key: str
    title: str
    url: str
    site: str
    retrieved_at: datetime
    supports: tuple[str, ...]
```

```text
GET  /api/v1/execution-worker/attachments/{attachment_id}/content
POST /api/v1/execution-worker/tasks/{task_id}/artifacts
PUT  /api/v1/execution-worker/artifact-uploads/{upload_id}/content
POST /api/v1/execution-worker/artifact-uploads/{upload_id}/complete
```

- [ ] **Step 1: 写失败的双重授权、版本与引用测试**

Grant 签发和读取时都检查 task、agent、attachment state、binding、expiry、read/byte budget、任务终态和撤销。输出测试覆盖错误 task/agent、token 重放、超配额、digest 不符、重复 `artifact_key + producer_version_id` 幂等、失败版本不替换 current、三个 ready 版本均可下载。引用测试拒绝非 HTTP(S)、凭据 URL、控制字符和超长字段，保留规范 URL、retrieval time 和 supports。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_grants.py \
  backend/tests/test_attachment_artifacts.py \
  backend/tests/test_conversation_citations.py
```

Expected: FAIL，新服务不存在。

- [ ] **Step 3: 实现 worker 专用 Bearer 路径**

Bearer 只放 Authorization header，不进 query、响应体日志或 prompt；DB 仅存 digest。输出先进入 `validating`，通过 Task 3 pipeline 后才绑定 `task_output/message_output`。`artifact_key` 来自 producer 稳定 ID，`version_no` 由 Platform 事务分配；current 查询选最新 ready 成功版本。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_grants.py \
  backend/tests/test_attachment_artifacts.py \
  backend/tests/test_conversation_citations.py
git add backend/app/attachments/grant_service.py \
  backend/app/attachments/artifact_service.py \
  backend/app/attachments/citation_service.py \
  backend/app/attachments/conversation_routes.py backend/app/control_plane/middleware.py \
  backend/tests/test_attachment_grants.py backend/tests/test_attachment_artifacts.py \
  backend/tests/test_conversation_citations.py
git commit -m "feat(attachments): grant task inputs and register outputs"
```

---

### Task 7: 定义 Platform ↔ MetaBot `core_chat_collaboration_v4` 契约

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/execution_relay/models.py`
- Modify: `backend/app/execution_relay/metabot_client.py`
- Modify: `backend/app/execution_relay/worker.py`
- Modify: `backend/app/execution_relay/repository.py`
- Modify: `backend/app/agent_brain/adapters/base.py`
- Modify: `backend/app/agent_brain/adapters/metabot_local.py`
- Modify: `deploy/cloud/metabot.runtime-contract.json`
- Modify: `backend/tests/test_execution_relay_api.py`
- Modify: `backend/tests/test_metabot_relay_client.py`
- Modify: `backend/tests/test_agent_brain_metabot_collaboration.py`
- Modify: `backend/tests/test_agent_brain_metabot_adapter.py`
- Create: `backend/tests/test_metabot_collaboration_v4.py`

**Interfaces:**

```python
class RelayJobPayload(BaseModel):
    collaboration_contract: Literal[
        "core_chat_collaboration_v3", "core_chat_collaboration_v4"
    ] | None
    input_attachment_grants: tuple[TaskAttachmentGrantPayload, ...] = ()
    output_write_grant: OutputWriteGrantPayload | None = None

class CollaborationV4Result(BaseModel):
    public_answer_markdown: str
    citations: tuple[CitationPayload, ...] = ()
    artifacts: tuple[RegisteredArtifactPayload, ...] = ()
    completion: Literal["completed", "partially_completed", "failed"]
    recovery: SearchRecoveryPayload | None = None
```

- [ ] **Step 1: 写 v3 兼容和 v4 失败测试**

断言 v3 payload/result 字节语义不变；只有 v4 可带 Grant、引用、登记后的 `attachment_id`；任何绝对本地路径、`bridge-private` artifact 或未登记文件都会使 v4 结果协议失败；relay encrypted payload 包含 secret，但日志/事件投影不包含 bearer。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_metabot_collaboration_v4.py \
  backend/tests/test_execution_relay_api.py \
  backend/tests/test_metabot_relay_client.py \
  backend/tests/test_agent_brain_metabot_collaboration.py \
  backend/tests/test_agent_brain_metabot_adapter.py
```

Expected: FAIL，Literal 仅允许 v3。

- [ ] **Step 3: 实现向后兼容 v4**

命令持久化继续使用 `ContentCodec`。能力握手必须明确声明 `core_chat_collaboration_v4`、input attachments、output artifacts 和 citations；不支持时 HR 附件请求 fail closed，纯文本仍可走 v3。worker callback 只接受 Platform 已登记的附件 ID。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_metabot_collaboration_v4.py \
  backend/tests/test_execution_relay_api.py \
  backend/tests/test_metabot_relay_client.py \
  backend/tests/test_agent_brain_metabot_collaboration.py \
  backend/tests/test_agent_brain_metabot_adapter.py
git add backend/app/execution_relay backend/app/agent_brain/adapters \
  deploy/cloud/metabot.runtime-contract.json backend/tests/test_metabot_collaboration_v4.py \
  backend/tests/test_execution_relay_api.py backend/tests/test_metabot_relay_client.py \
  backend/tests/test_agent_brain_metabot_collaboration.py \
  backend/tests/test_agent_brain_metabot_adapter.py
git commit -m "feat(relay): add metabot collaboration v4"
```

---

### Task 8: 让 MetaBot 安全接收输入并回传生成文件

**Repository:** `/Users/neo/Developer/work/metabot-dev`

**Files:**
- Modify: `src/api/routes/core-chat-contract.ts`
- Modify: `src/api/routes/core-chat-routes.ts`
- Modify: `src/api/routes/core-chat-session-store.ts`
- Modify: `src/bridge/message-bridge.ts`
- Create: `src/api/routes/platform-attachment-transfer.ts`
- Modify: `tests/core-chat-routes.test.ts`
- Modify: `tests/core-chat-session-store.test.ts`
- Create: `tests/platform-attachment-transfer.test.ts`
- Modify: `tests/output-handler.test.ts`

**Interfaces:**

```ts
export interface CoreChatInputAttachmentGrant {
  attachmentId: string;
  displayName: string;
  detectedMime: string;
  sizeBytes: number;
  sha256: string;
  downloadUrl: string;
  bearerToken: string;
  expiresAt: string;
}

export interface ApiTaskInputAttachment {
  attachmentId: string;
  safeName: string;
  detectedMime: string;
  sizeBytes: number;
  sha256: string;
  localPath: string;
}

export interface PlatformArtifactRegistration {
  artifactKey: string;
  producerVersionId: string;
  attachmentId: string;
  displayName: string;
  status: "ready" | "rejected";
}
```

- [ ] **Step 1: 按仓库 `CLAUDE.md` 运行基线并写失败测试**

```bash
cd /Users/neo/Developer/work/metabot-dev
npm test -- --run tests/core-chat-routes.test.ts tests/core-chat-session-store.test.ts
```

然后新增测试：redirect 被拒绝；Bearer 只在同一 Platform origin 使用；下载按 size/hash 校验；临时目录 0700、文件 0600；失败/结束都清理；输出 callback 被 await；输出按流上传且完成验证后才返回 attachment ID；session command digest 忽略短期 token 但包含 attachment ID/hash 和 output grant scope。

- [ ] **Step 2: 运行 RED**

```bash
npm test -- --run \
  tests/platform-attachment-transfer.test.ts \
  tests/core-chat-routes.test.ts \
  tests/core-chat-session-store.test.ts \
  tests/output-handler.test.ts
```

Expected: FAIL，v4/transfer 模块不存在。

- [ ] **Step 3: 实现 bridge-only 文件传输**

`platform-attachment-transfer.ts` 使用 `fetch(..., {redirect: "error"})`、固定 Platform origin、stream pipeline、大小/hash 校验和受控 temp dir。`ApiTaskOptions.onOutputFiles` 改为 `void | Promise<void>` 并 await；把安全本地输入只交给 bridge 执行层，不把 token、URL 或 temp path拼入用户 prompt。保留现有 Feishu archive/output 行为。

- [ ] **Step 4: 运行 GREEN、构建、lint 并提交**

```bash
npm test -- --run \
  tests/platform-attachment-transfer.test.ts \
  tests/core-chat-routes.test.ts \
  tests/core-chat-session-store.test.ts \
  tests/output-handler.test.ts
npm run build:bridge
npm run lint
git add src/api/routes/core-chat-contract.ts src/api/routes/core-chat-routes.ts \
  src/api/routes/core-chat-session-store.ts src/api/routes/platform-attachment-transfer.ts \
  src/bridge/message-bridge.ts tests/core-chat-routes.test.ts \
  tests/core-chat-session-store.test.ts tests/platform-attachment-transfer.test.ts \
  tests/output-handler.test.ts
git commit -m "feat(core-chat): transfer platform attachments"
```

---

### Task 9: 接通 Agent Brain 上下文、结果终态与搜索恢复

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/agent_brain/orchestrator.py`
- Modify: `backend/app/agent_brain/loop_runtime.py`
- Modify: `backend/app/agent_brain/context_policy.py`
- Modify: `backend/app/agent_brain/loop_models.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Create: `backend/app/agent_brain/recovery.py`
- Modify: `backend/tests/test_agent_brain_loop_runtime.py`
- Modify: `backend/tests/test_agent_brain_orchestrator.py`
- Create: `backend/tests/test_agent_brain_attachment_delivery.py`
- Create: `backend/tests/test_agent_brain_search_recovery.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SearchRecoveryState:
    status: Literal["unavailable", "no_results", "partial"]
    attempt_count: int
    last_attempt_at: datetime
    resumable: bool
    coverage_note: str | None
```

```text
POST /api/v1/conversations/{conversation_id}/turns/{turn_id}/resume
```

- [ ] **Step 1: 写失败的端到端编排测试**

断言 direct HR 与 Brain v2 都只为 active IDs 签发 Grant；`loop_runtime.py` 不再硬编码空 refs；Grant 在任务终态撤销；MetaBot 返回的公开回答/引用/artifacts 分开持久化；声明文件但 artifact registration 缺失时 Turn 失败为“结果文件登记失败”；扫描/验证未完成时停留 `completing`；SEARCH_UNAVAILABLE 保存可恢复状态，resume 复用原 Turn 上下文且不重复已登记 artifact；no_results 与 unavailable 不混淆。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_attachment_delivery.py \
  backend/tests/test_agent_brain_search_recovery.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_orchestrator.py
```

Expected: FAIL，attachment refs 仍为空且无 resume 路径。

- [ ] **Step 3: 实现 durable completion barrier 和 resume**

Turn 保持唯一非终态约束；浏览器断开不取消运行。result handler 在一个事务内写回答、引用和 artifact bindings；所有声明输出到达确定状态后才发终态事件。resume 使用新 run/attempt、原 turn lineage 和稳定 artifact idempotency key，不要求重传附件。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_brain_attachment_delivery.py \
  backend/tests/test_agent_brain_search_recovery.py \
  backend/tests/test_agent_brain_loop_runtime.py \
  backend/tests/test_agent_brain_orchestrator.py
git add backend/app/agent_brain backend/tests/test_agent_brain_attachment_delivery.py \
  backend/tests/test_agent_brain_search_recovery.py \
  backend/tests/test_agent_brain_loop_runtime.py backend/tests/test_agent_brain_orchestrator.py
git commit -m "feat(brain): deliver attachments and resumable results"
```

---

### Task 10: 扩展 Catalog 与浏览器 API 类型，不先改 UI

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/agent_catalog/models.py`
- Modify: `backend/app/agent_catalog/catalog.yaml`
- Modify: `backend/tests/test_agent_catalog.py`
- Modify: `webui/src/brainTypes.ts`
- Modify: `webui/src/brainApi.ts`
- Modify: `webui/src/conversationTypes.ts`
- Modify: `webui/src/conversationApi.ts`
- Create: `webui/src/attachmentApi.ts`
- Modify: `webui/src/conversationApi.test.ts`
- Create: `webui/src/attachmentApi.test.ts`

**Interfaces:**

```ts
export type AttachmentState = "uploading" | "validating" | "scanning" |
  "ready" | "quarantined" | "rejected" | "deleted";
export interface ConversationAttachment {
  attachmentId: string;
  conversationId: string | null;
  source: "user" | "agent";
  displayName: string;
  detectedMime: string | null;
  sizeBytes: number;
  sha256: string | null;
  state: AttachmentState;
  stateReason: string | null;
  createdAt: string;
  retainedUntil: string;
  preview: { attachmentId: string; detectedMime: string } | null;
  coverage: { pages: number | null; sheets: number | null;
              slides: number | null; ocrComplete: boolean | null } | null;
}
export interface ConversationCitation {
  citationKey: string;
  title: string;
  url: string;
  site: string;
  retrievedAt: string;
  supports: string[];
}
export interface ArtifactVersion {
  artifactKey: string;
  versionNo: number;
  producerVersionId: string;
  current: boolean;
  status: "processing" | "ready" | "failed";
  attachment: ConversationAttachment | null;
}
export interface TurnSubmission {
  text: string;
  attachmentIds: string[];
  activeAttachmentIds: string[];
}
```

- [ ] **Step 1: 写 strict parser 与请求体失败测试**

覆盖附件/引用/版本/read-state/recovery 的 exact-key 解析，未知 key 拒绝；`startConversation` 和 `appendConversationTurn` 序列化三个提交字段并保留 idempotency key；upload API 报告真实状态；HR card 宣告 input/output attachments 与限制，其他 Agent 默认不变。

- [ ] **Step 2: 运行 RED**

```bash
cd webui
npm test -- --run src/conversationApi.test.ts src/attachmentApi.test.ts
```

Expected: FAIL，新类型和 API 不存在。

- [ ] **Step 3: 实现契约并启用 HR capability version**

Catalog 的 accepted input types 包含图片/PDF/text/Office，output types 包含图片/PDF/Office；HR `supports_attachments_in/out=true` 并提升 capability version。前端从 Catalog 读取限制，不把 HR 数值写进共享组件。

- [ ] **Step 4: 运行 GREEN、构建并提交**

```bash
cd webui
npm test -- --run src/conversationApi.test.ts src/attachmentApi.test.ts src/brainApi.test.ts
npm run build
cd ..
backend/.venv/bin/python -m pytest -q backend/tests/test_agent_catalog.py
git add backend/app/agent_catalog backend/tests/test_agent_catalog.py \
  webui/src/brainTypes.ts webui/src/brainApi.ts webui/src/conversationTypes.ts \
  webui/src/conversationApi.ts webui/src/attachmentApi.ts \
  webui/src/conversationApi.test.ts webui/src/attachmentApi.test.ts
git commit -m "feat(catalog): expose HR file capabilities"
```

---

### Task 11: 构建上传队列、附件卡与 Session 材料抽屉

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/components/conversation/AttachmentUploader.tsx`
- Create: `webui/src/components/conversation/AttachmentUploader.test.tsx`
- Create: `webui/src/components/conversation/AttachmentCard.tsx`
- Create: `webui/src/components/conversation/AttachmentCard.test.tsx`
- Create: `webui/src/components/conversation/SessionMaterialsDrawer.tsx`
- Create: `webui/src/components/conversation/SessionMaterialsDrawer.test.tsx`
- Modify: `webui/src/components/conversation/ConversationComposer.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**

```ts
export interface UploadQueueItem {
  localId: string;
  file: File;
  progress: number;
  state: "queued" | "uploading" | "processing" | "ready" | "failed";
  attachment?: ConversationAttachment;
  error?: string;
}
```

- [ ] **Step 1: 写上传交互失败测试**

用 `File`、`DataTransfer` 和 clipboard fixture 覆盖点击选择、拖放、粘贴图片、多文件、进度、单项失败重试、移除、处理状态、单/消息/Session 配额，以及“文本为空但有 ready 附件可发送”“任一新附件未 ready 时不能发送”。

- [ ] **Step 2: 运行 RED**

```bash
cd webui
npm test -- --run \
  src/components/conversation/AttachmentUploader.test.tsx \
  src/components/conversation/AttachmentCard.test.tsx \
  src/components/conversation/SessionMaterialsDrawer.test.tsx
```

Expected: FAIL，组件不存在。

- [ ] **Step 3: 实现共享组件和明确选择状态**

新上传文件默认加入本轮 active；旧材料沿用上轮选择但在 composer 显示标签；取消 active 不删除；删除必须二次确认并调用 API。抽屉分“本轮启用 / 已上传材料 / 生成结果”，显示容量和到期日，记住展开状态；窄屏为 overlay drawer。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
cd webui
npm test -- --run \
  src/components/conversation/AttachmentUploader.test.tsx \
  src/components/conversation/AttachmentCard.test.tsx \
  src/components/conversation/SessionMaterialsDrawer.test.tsx \
  src/pages/ConversationPage.test.tsx
npm run build
cd ..
git add webui/src/components/conversation/AttachmentUploader.tsx \
  webui/src/components/conversation/AttachmentUploader.test.tsx \
  webui/src/components/conversation/AttachmentCard.tsx \
  webui/src/components/conversation/AttachmentCard.test.tsx \
  webui/src/components/conversation/SessionMaterialsDrawer.tsx \
  webui/src/components/conversation/SessionMaterialsDrawer.test.tsx \
  webui/src/components/conversation/ConversationComposer.tsx \
  webui/src/pages/ConversationPage.tsx webui/src/styles.css
git commit -m "feat(webui): add session materials workspace"
```

---

### Task 12: 展示生成结果、版本、下载、引用、复制与反馈

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `webui/src/clipboard.ts`
- Create: `webui/src/clipboard.test.ts`
- Create: `webui/src/components/conversation/ArtifactVersionList.tsx`
- Create: `webui/src/components/conversation/ArtifactVersionList.test.tsx`
- Create: `webui/src/components/conversation/CitationList.tsx`
- Create: `webui/src/components/conversation/CitationList.test.tsx`
- Create: `webui/src/components/conversation/MessageActions.tsx`
- Create: `webui/src/components/conversation/MessageActions.test.tsx`
- Modify: `webui/src/components/conversation/ConversationMessages.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Feedback reasons add `file_format` and `source_timeliness`.
- Downvote comment limit: 1,000 code points.
- Copy value: rendered user-visible Markdown text only.

- [ ] **Step 1: 写结果与消息操作失败测试**

断言单文件下载按钮明显可见；多结果显示“全部下载”；图片/PDF打开安全 preview，Office 只下载；全部版本可展开且最新 ready 成功版为 current；失败版不替换；引用默认收起且编号对应正文；clipboard API 失败时 textarea fallback；点踩先打开 reason/comment 面板、不立即提交；评论上限前后端一致。

- [ ] **Step 2: 运行 RED**

```bash
cd webui
npm test -- --run \
  src/clipboard.test.ts \
  src/components/conversation/ArtifactVersionList.test.tsx \
  src/components/conversation/CitationList.test.tsx \
  src/components/conversation/MessageActions.test.tsx
```

Expected: FAIL，新组件不存在。

- [ ] **Step 3: 实现消息级交付体验**

使用 Platform ticket API，不复用永久 URL。复制成功显示短暂“已复制”；重新生成保留旧回答和版本，用新 client request ID 及仍可用的原 active attachments 创建 retry。SEARCH_UNAVAILABLE 显示尝试次数、最后时间和“继续重试”，no_results 显示检索范围，partial 显示缺口。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
cd webui
npm test -- --run \
  src/clipboard.test.ts \
  src/components/conversation/ArtifactVersionList.test.tsx \
  src/components/conversation/CitationList.test.tsx \
  src/components/conversation/MessageActions.test.tsx \
  src/pages/ConversationPage.test.tsx
npm run build
cd ..
git add webui/src/clipboard.ts webui/src/clipboard.test.ts \
  webui/src/components/conversation/ArtifactVersionList.tsx \
  webui/src/components/conversation/ArtifactVersionList.test.tsx \
  webui/src/components/conversation/CitationList.tsx \
  webui/src/components/conversation/CitationList.test.tsx \
  webui/src/components/conversation/MessageActions.tsx \
  webui/src/components/conversation/MessageActions.test.tsx \
  webui/src/components/conversation/ConversationMessages.tsx \
  webui/src/pages/ConversationPage.tsx webui/src/styles.css
git commit -m "feat(webui): deliver downloadable HR results"
```

---

### Task 13: 收紧 HR 页面布局、规范路由与离开后的未读状态

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `webui/src/pages/AgentUsePage.tsx`
- Modify: `webui/src/pages/AgentUsePage.test.tsx`
- Modify: `webui/src/pages/ConversationPage.tsx`
- Modify: `webui/src/pages/ConversationPage.test.tsx`
- Modify: `webui/src/components/conversation/ConversationSidebar.tsx`
- Modify: `webui/src/components/conversation/ConversationSidebar.test.tsx`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/styles.css`
- Modify: `backend/app/agent_brain/conversation_routes.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Create: `backend/tests/test_conversation_read_state.py`

**Interfaces:**

```ts
interface ConversationSidebarProps {
  conversationHref: (conversationId: string) => string;
  onOpenConversation: (conversationId: string) => void;
}
```

```text
POST /api/v1/conversations/{conversation_id}/read-state
Body: { last_seen_event_seq: integer }
```

- [ ] **Step 1: 写路由、布局和未读失败测试**

断言 HR anchor 的真实 `href` 为规范 scoped path，复制/新标签/刷新不依赖 onClick；错误 Agent scope 不展示 Conversation；离开后产生 completed/failed/waiting_user 事件会未读，打开并提交最后已见 seq 后清除；处理中状态可见；长回答最后操作区不被 sticky composer 遮挡；空状态不再占用首屏展示大块能力卡。

- [ ] **Step 2: 运行 RED**

```bash
cd webui
npm test -- --run \
  src/components/conversation/ConversationSidebar.test.tsx \
  src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx src/router.test.ts
cd ..
backend/.venv/bin/python -m pytest -q backend/tests/test_conversation_read_state.py
```

Expected: FAIL，sidebar href 仍为通用 Conversation 路径且无 read-state。

- [ ] **Step 3: 实现三栏工作台与持久未读**

左栏历史、中栏对话、右栏材料；采用低噪白底和紧凑 composer。SSE 仅观察任务，refresh 从持久 event seq 恢复。后端以 owner+conversation 校验 read-state；历史列表返回业务状态和 unread，不使用前端时间猜测。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
cd webui
npm test -- --run \
  src/components/conversation/ConversationSidebar.test.tsx \
  src/pages/AgentUsePage.test.tsx src/pages/ConversationPage.test.tsx src/router.test.ts
npm run build
cd ..
backend/.venv/bin/python -m pytest -q backend/tests/test_conversation_read_state.py
git add webui/src/pages/AgentUsePage.tsx webui/src/pages/AgentUsePage.test.tsx \
  webui/src/pages/ConversationPage.tsx webui/src/pages/ConversationPage.test.tsx \
  webui/src/components/conversation/ConversationSidebar.tsx \
  webui/src/components/conversation/ConversationSidebar.test.tsx \
  webui/src/router.ts webui/src/router.test.ts webui/src/styles.css \
  backend/app/agent_brain/conversation_routes.py \
  backend/app/agent_brain/conversation_repository.py \
  backend/tests/test_conversation_read_state.py
git commit -m "feat(hr): polish workspace routing and unread state"
```

---

### Task 14: 建立管理端反馈分诊与 Session 附件投影

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/review/routes.py`
- Modify: `backend/app/agent_brain/conversation_repository.py`
- Modify: `backend/tests/test_review_api.py`
- Create: `backend/tests/test_admin_conversation_attachments.py`
- Create: `webui/src/components/review/ConversationFeedbackInbox.tsx`
- Create: `webui/src/components/review/ConversationFeedbackInbox.test.tsx`
- Modify: `webui/src/components/review/ReviewWorkspace.tsx`
- Modify: `webui/src/api.ts`

**Interfaces:**

```text
GET   /api/review/conversation-feedback?triage_status=pending_triage
PATCH /api/review/conversation-feedback/{feedback_id}
GET   /api/review/conversations/{conversation_id}/attachments
POST  /api/review/attachments/{attachment_id}/ticket
```

- [ ] **Step 1: 写 Owner-only 投影失败测试**

断言普通成员不可访问；Owner 能看到问题、公开回答、rating/reason/comment、Agent、Conversation/Turn、附件元数据、引用、保留期和 triage status；附件 preview/download 每次重新授权并写 access event；删除、隔离、到期和历史缺失只显示不可用原因；PATCH 只允许 `triaged/dismissed` 且审计操作者和时间。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_review_api.py \
  backend/tests/test_admin_conversation_attachments.py
cd webui
npm test -- --run src/components/review/ConversationFeedbackInbox.test.tsx
```

Expected: FAIL，现有 review projection 没有上下文和分诊 UI。

- [ ] **Step 3: 实现独立 Conversation feedback inbox**

不把 pending feedback 强塞进 FAE issue 生命周期；管理端显式操作后才变 triaged，创建工程 Issue 仍为人工选择。复用附件卡的显示层，但管理 ticket 使用 Owner 审计 API。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_review_api.py backend/tests/test_admin_conversation_attachments.py
cd webui
npm test -- --run src/components/review/ConversationFeedbackInbox.test.tsx
npm run build
cd ..
git add backend/app/review/routes.py backend/app/agent_brain/conversation_repository.py \
  backend/tests/test_review_api.py backend/tests/test_admin_conversation_attachments.py \
  webui/src/components/review/ConversationFeedbackInbox.tsx \
  webui/src/components/review/ConversationFeedbackInbox.test.tsx \
  webui/src/components/review/ReviewWorkspace.tsx webui/src/api.ts
git commit -m "feat(review): triage conversation feedback with files"
```

---

### Task 15: 落地一年保留、清理 Worker 与生产安全依赖

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Create: `backend/app/attachments/retention.py`
- Create: `backend/app/attachments/erasure.py`
- Create: `backend/tests/test_attachment_retention.py`
- Create: `backend/tests/test_attachment_erasure.py`
- Modify: `deploy/cloud/Dockerfile`
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/cloud/bootstrap-control-db.sh`
- Modify: `deploy/cloud/deploy.sh`
- Modify: `deploy/cloud/acceptance.sh`
- Modify: `backend/tests/test_cloud_deployment.py`
- Modify: `docs/runbooks/cloud-platform.md`
- Create: `docs/runbooks/conversation-attachments.md`

**Interfaces:**
- Worker command: `python -m app.attachments.worker_runtime all`
- Health command: `python -m app.attachments.worker_runtime healthcheck`
- Maintenance: expire grants, abort 24h orphan uploads, process validation/scan/derivative queue, erase expired/deleted objects, record partial failures for retry.

- [ ] **Step 1: 写保留和删除失败测试**

冻结时钟断言 365 天边界、Conversation archive 不缩短保留、用户删除立即撤销 ticket/Grant、对象删除失败进入可重试 `partial`、审计只保留最小 metadata/hash、派生物和所有版本都被处理、对象存储不得配置早于数据库 retained_until 的生命周期。

- [ ] **Step 2: 运行 RED**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_retention.py backend/tests/test_attachment_erasure.py
```

Expected: FAIL，maintenance 服务不存在。

- [ ] **Step 3: 实现 worker 与部署拓扑**

Docker image 安装固定的 ClamAV client/libmagic/Poppler runtime；Compose 增加私有对象存储、ClamAV 和 `platform-attachments` worker，worker 无 edge 网络，使用独立最小权限 DB/S3 secrets。Platform API 从 cloud replica 的旧 Flywheel attachment 禁用状态中分离，启用新的 Conversation Attachment feature flag；旧管理 attachment flag 保持原义。

- [ ] **Step 4: 加发布和回滚门槛**

`acceptance.sh` 检查：bucket private、scanner fresh、worker healthy、未认证 upload/download 401/403、旧 attachment route 未意外开放、50 MB streaming、到期 Grant 拒绝。runbook 写明 key/secret provisioning、ClamAV 更新、容量、备份恢复、对象/DB 对账、紧急擦除、feature flag 回滚；回滚只停止新上传，不删除已存对象或迁移。

`deploy.sh` 和 `test_cloud_deployment.py` 还必须把上述生产磁盘纪律变成可执行门禁：发布前后解析 `df -B1 / /data`；使用唯一且校验过的 deployment ID 创建 `/data/staging/<application>/<deployment_id>/`；以 `trap` 精确清理该目录；拒绝 release 禁止项；根盘只保留 current + 两个 rollback；更旧 release 移至数据盘归档后按 10 个/30 天收敛；Docker 只清理本服务、无容器引用且不在 current/rollback 集合中的镜像。任何阈值失败或异常净增长都必须在变更服务之前终止。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_attachment_retention.py backend/tests/test_attachment_erasure.py \
  backend/tests/test_config.py backend/tests/test_main.py \
  backend/tests/test_cloud_deployment.py
bash -n deploy/cloud/deploy.sh deploy/cloud/acceptance.sh
docker compose -f deploy/cloud/compose.yaml config >/dev/null
git add backend/app/attachments/retention.py backend/app/attachments/erasure.py \
  backend/tests/test_attachment_retention.py backend/tests/test_attachment_erasure.py \
  deploy/cloud/Dockerfile deploy/cloud/compose.yaml deploy/cloud/bootstrap-control-db.sh \
  deploy/cloud/deploy.sh backend/tests/test_cloud_deployment.py \
  deploy/cloud/acceptance.sh docs/runbooks/cloud-platform.md \
  docs/runbooks/conversation-attachments.md
git commit -m "ops(attachments): enforce retention and safe processing"
```

---

### Task 16: 执行跨仓库回归、真实 HR 样本验收与受控发布

**Repositories:**
- `/Users/neo/Developer/work/AI-Agent-Platform`
- `/Users/neo/Developer/work/metabot-dev`

**Files:**
- Create: `backend/tests/test_hr_workspace_acceptance.py`
- Create: `webui/src/pages/HrWorkspace.acceptance.test.tsx`
- Create: `docs/operations/2026-09-03-hr-agent-web-workspace-release.md`
- Modify: `deploy/cloud/acceptance.sh`

- [ ] **Step 1: 把十个核心场景写成自动化验收**

至少覆盖：五文件一次发送；下一轮只启用两个旧材料；三版 PPT 均可下载且第三版 current；坏 PPT 不显示成功；关页后完成并未读；搜索 unavailable 后 resume；复制和带文字点踩；owner/普通成员/admin 下载权限；HR 规范深链；删除/到期后不可读取。

- [ ] **Step 2: 先运行新增验收并确认缺口**

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_hr_workspace_acceptance.py
cd webui
npm test -- --run src/pages/HrWorkspace.acceptance.test.tsx
```

Expected: 在前 15 个任务未全部集成前 FAIL；全部集成后 PASS。

- [ ] **Step 3: 运行 Platform 全量验证**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
backend/.venv/bin/python -m pytest -q backend/tests
cd webui
npm test
npm run build
cd ..
bash -n deploy/cloud/deploy.sh deploy/cloud/acceptance.sh
docker compose -f deploy/cloud/compose.yaml config >/dev/null
```

Expected: 全部 PASS；无 attachment、Conversation、review、catalog、auth 或 cloud acceptance 回归。

- [ ] **Step 4: 运行 MetaBot 全量验证**

```bash
cd /Users/neo/Developer/work/metabot-dev
npm test
npm run build
npm run lint
npm run format:check
```

Expected: 全部 PASS，Feishu 附件和输出归档测试不回归。

- [ ] **Step 5: 执行脱敏真实样本和故障注入**

在 staging 使用脱敏 JD、访谈 PDF、三张图片及三版 PPT；验证刷新、断网、worker 重启、SSE 重连、ClamAV 离线、对象存储超时、SEARCH_UNAVAILABLE、坏文件输出、ticket 重放、越权 attachment ID 和删除后读取。浏览器检查常规桌面、窄桌面、长回答、多附件、多版本及输入区不遮挡。

- [ ] **Step 6: 写发布证据并提交**

release 文档记录两个仓库 SHA、migration 064、镜像 digest、测试命令和结果、十项场景证据、已知限制、监控指标、回滚命令与附件保留事实；同时记录部署前后 `df`、新增文件和目录大小、当前及两个回滚版本、归档/删除版本、staging 清空证据、当前/回滚镜像、业务页面 HTTP 验收、其他应用/共享 Nginx 是否变更。不放真实候选人内容、文件名、URL token 或对象键。

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git add backend/tests/test_hr_workspace_acceptance.py \
  webui/src/pages/HrWorkspace.acceptance.test.tsx \
  deploy/cloud/acceptance.sh \
  docs/operations/2026-09-03-hr-agent-web-workspace-release.md
git commit -m "test(hr): accept the document workspace"
```

- [ ] **Step 7: 按依赖顺序发布并观察**

先取得 Platform 发布锁并通过磁盘门禁，再部署 MetaBot v4（仍兼容 v3），再部署 Platform schema/services，最后启用 HR Catalog attachment capability。发布后观察上传成功率、扫描时延、声明输出/登记结果一致率、下载成功率、断线恢复率、搜索恢复率和 pending triage 时长；任何越权下载、假成功结果、重复 Turn、对象提前删除、根盘异常增长或跨应用变更立即停止发布/关闭新 attachment feature flag，并保留数据调查。
