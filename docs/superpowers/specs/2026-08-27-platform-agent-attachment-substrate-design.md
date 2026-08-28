# Platform 统一 Agent 附件与图片底座设计

**日期：** 2026-08-27

**状态：** 待书面评审

**适用系统：** Agent Platform、FAE、VOC、行政、HR、Marketing、MetaBot Local

## 1. 项目定位

附件底座是独立于 Agent Brain Action 确认机制的并行项目。它不阻塞迁移 049/050/051 和 VOC
确认链路；必须在 FAE 以“支持图片/文档”的能力正式开放给 Brain 前完成。

所有相关仓库、对象存储配置、Worker 和 Agent 代码都由 Orbbec 团队掌控，可以统一修改。
因此本设计选择统一 Platform Attachment Service，不接受“各 Agent 已有附件所以只能做
转发”的前提。

统一的是：存储、身份、所有权、生命周期、授权、审计和传输协议。各专业 Agent 仍保留
自己的图片理解、OCR、文档解析和领域推理能力。

## 2. 目标与非目标

目标：

- 用户在任一 Platform Conversation 上传一次图片或文档；
- 同一附件可授权给多个专业 Agent，不重复上传；
- Agent 输出的报告、表格、图片和文档统一回到 Platform；
- 所有访问绑定 `internal_user_id`、Conversation、Message/Turn、Task 和 Agent；
- 附件默认保留一年，支持紧急擦除和完整审计；
- FAE 成为第一条图片、PDF、文档全能力消费链路。

非目标：

- 不把 FAE 面向外部客户的附件强制迁入企业 Platform；
- 不把行政住宿证明、反馈凭证等领域业务附件的事实源迁出行政系统；
- 不向 Agent 暴露 MinIO 凭据、Object Key 或长期预签名 URL；
- 不在首版支持任意远程 URL 抓取、压缩包递归解包或可执行文件；
- 不把附件正文或文件名写入普通访问日志。

## 3. 架构

```text
Browser / Agent output
        |
        v
Platform Attachment API
  - identity / ownership / CSRF
  - metadata / SHA-256 / MIME
  - upload state / retention / erasure
        |
        +--> Platform-managed MinIO
        |
        +--> Scan & Derivative Worker
        |      - ClamAV
        |      - image metadata / safe thumbnail
        |      - PDF preview / text extraction / OCR
        |
        +--> Task-scoped Media Gateway
                 |
                 +--> FAE / VOC / 行政
                 +--> MetaBot Local HR / Marketing
```

Platform 是企业 Agent 附件事实源。MinIO 只是 Blob Store，不能作为权限数据库；Agent
不能根据 Object Key 直接访问对象。

## 4. 数据模型

使用独立 `platform_attachments` schema，不放入只读 `platform_replica`，也不塞进
Action 迁移 049/050/051。迁移编号在 049/050/051 合并后按主线下一个可用编号确定。

```text
attachments
attachment_uploads
attachment_bindings
attachment_derivatives
attachment_access_grants
attachment_access_events
attachment_erasure_jobs
```

### 4.1 attachments

至少保存：

```text
attachment_id uuid
owner_internal_user_id uuid
state uploading | validating | scanning | ready | quarantined | rejected | deleted
size_bytes bigint
sha256 bytea(32)
detected_mime_type text
original_name_ciphertext bytea
original_name_key_version integer
object_ref_ciphertext bytea
object_ref_key_version integer
retention_until timestamptz
created_at / ready_at / deleted_at
```

Object Key 是随机不可猜值，不含用户名、花名、文件名、Conversation ID 或钉钉标识。

### 4.2 bindings

附件必须显式绑定到一个业务对象：

```text
conversation_message
conversation_turn
agent_task_input
agent_task_output
domain_reference
```

绑定记录保存对象类型、对象 ID、用途、创建者和时间。读取时始终重新验证绑定对象的所有权
或 Platform Owner 的审计式跨用户权限。

### 4.3 grants

Agent 读取 Grant 绑定：

```text
attachment_id
agent_task_id
agent_id
audience
purpose
max_reads
max_bytes
expires_at
revoked_at
```

唯一有效范围是 `(task_id, attachment_id, agent_id)`。任务终止、授权撤销、用户停用、
附件删除或 Grant 过期时立即拒绝。

### 4.4 erasure jobs

`attachment_erasure_jobs` 至少包含：

```text
erasure_job_id uuid primary key
attachment_id uuid not null
requested_by_internal_user_id uuid not null
reason_ciphertext / reason_key_version / reason_sha256
status queued | running | completed | partial | failed
attempt_count integer
original_deleted_at / derivatives_deleted_at / exports_deleted_at
downstream_cleanup_status jsonb
last_error_code text
created_at / started_at / completed_at / updated_at
unique (attachment_id) where status in (queued, running)
```

`partial` 是正式终态：表示 Platform 原件与可控副本已删除，但至少一个下游临时副本无法
确认清理。重试可以创建新 Attempt，但不能把历史 partial 审计改写成 completed。

## 5. 上传与验证状态机

```text
uploading
  -> validating
  -> scanning
  -> ready
  -> quarantined | rejected | deleted
```

状态不是展示用模拟进度，每一步都有真实处理事实：

- `uploading`：字节仍在写入；
- `validating`：服务端重新计算大小、SHA-256、MIME、magic-byte，并完成解码器安全检查；
- `scanning`：文件已提交给 ClamAV `clamd`，等待固定病毒库版本的真实扫描结果；
- `ready`：验证与扫描均成功；
- `quarantined`：ClamAV 命中或结果可疑；
- `rejected`：类型、大小、格式、解码或策略失败；
- `deleted`：保留到期或紧急擦除完成。

扫描引擎首版固定为 ClamAV 1.x `clamd`，病毒库由 `freshclam` 更新并记录版本。ClamAV
不可用、超时或定义库过期时 fail closed，附件不得进入 `ready`。不能把 MIME/magic-byte
检查描述成“病毒扫描”。

派生物必须在原件扫描为 clean 后生成。解析 Worker 使用无网络、只读输入、临时目录、
CPU/内存/时长限制；禁止宏执行、脚本执行、外链加载和压缩包递归解包。

`ready` 是唯一允许字节离开隔离区的状态。`uploading`、`validating`、`scanning`、
`quarantined`、`rejected`、`deleted` 的对象一律不能经 Media Gateway、浏览器 Preview、
Derivative API 或 Agent Output 下载；内部接口返回稳定 `attachment_not_ready`/`gone`，
不得为了“先预览”绕过状态。该检查在元数据授权服务和实际流式打开对象前各执行一次，
不能只靠前端隐藏。

## 6. 文件限制

Platform 首版硬上限：

```text
单文件 50 MB
单消息最多 10 个
单消息合计 100 MB
```

Catalog 可以降低，不能提高。首版允许：

- 常见图片：JPEG、PNG、WebP；
- PDF；
- 明确允许的纯文本和 Office 文档格式。

首版拒绝：可执行文件、脚本、磁盘镜像、带密码文件、未知二进制、压缩包、嵌套容器和
服务器无法安全解码的图片。SVG 默认作为下载附件处理，不在同源页面内联渲染。

## 7. API 语义

浏览器 API：

```text
create_upload
upload_bytes_or_parts
complete_upload
bind_to_message_or_turn
get_status
preview_or_download
emergency_erase
```

内部 Agent API：

```text
issue_task_grant
stream_media_with_grant
register_agent_output
bind_output_to_task_and_message
```

浏览器写请求使用 Platform Session + CSRF。内部 Agent 请求使用 audience/task/scope-bound
Task Token。完成上传时服务端重新计算所有完整性字段，不能相信浏览器提供的 MIME、大小
或 Hash。

接口禁止调用者指定 Object Key、MinIO Endpoint 或任意远程 URL。下载响应使用安全的
`Content-Disposition`、`nosniff` 和精确 Content Type；HTML、SVG、Office 等主动内容不
在 Platform 同源 Origin 内联执行。

## 8. Catalog 能力

Catalog 不使用单个模糊的 `supports_attachments`，至少声明：

```text
accepted_attachment_mime_types
max_attachment_count
max_attachment_bytes_each
max_attachment_bytes_total
supports_image_vision
supports_document_text
supports_attachment_output
```

Brain 派发前校验能力；不兼容时返回 `attachment_unsupported`，明确指出哪个附件和哪条
能力不匹配。不得只把文件名发给模型，也不得静默忽略附件。

## 9. 各 Agent 接入

### 9.1 FAE

企业 Task 使用 Platform Grant 流式读取。图片进入 FAE 现有视觉路径，PDF/文档进入现有
附件解析、OCR 和证据路径。FAE 输出文件通过 Output Attachment API 回写 Platform。

FAE 面向外部客户的原上传与存储保持独立。企业 Task 不把 Platform 文件复制到公共客户
存储长期保存；确需本地临时文件时使用任务临时目录并在结束后删除。

### 9.2 VOC

VOC 与 Platform 同进程仍必须调用同一授权服务，不因进程内调用绕过 Task/Owner 检查。
VOC Action 确认首版不依赖附件，因此附件轨不阻塞 VOC 上线。

### 9.3 行政

行政 Agent Conversation 输入和输出使用 Platform Attachment。住宿证明、反馈凭证等
领域附件仍属于行政业务记录；进入 Agent Conversation 时采用明确 Scope 的受控引用或
复制，不暴露行政存储路径。

行政只读 Agent 第一批可以在无附件情况下上线；声明附件能力前必须通过本设计合同测试。

### 9.4 HR / Marketing / MetaBot Local

本地 Worker 通过 HTTPS Media Gateway + Task Token 下载到权限 `0600` 的任务临时目录。
任务结束、取消或超时立即删除；不得写入 MetaBot SQLite、用户目录或共享长期目录。

## 10. 输出附件

Agent 生成附件时：

1. 使用 Output Token 创建上传；
2. 上传字节并完成验证、扫描；
3. Platform 记录生成 Agent、Task、输入来源和 Hash；
4. `ready` 后返回 `attachment_ref`；
5. 最终 `result` 只引用 Attachment ID，不携带本地路径或 Object Key。

输出附件与用户上传采用相同保留、擦除、预览和审计规则。

## 11. 身份、授权与审计

- 所有附件归属于 Platform `internal_user_id`；
- FAE 企业 SSO、行政浏览器身份和 Brain Task Token 最终使用同一内部用户；
- 下游不接收钉钉原始 ID；
- 用户只能读取自己的附件和会话；Platform Owner 跨用户查看必须写审计；
- Grant 签发、打开、读取完成、范围拒绝、过期、撤销和擦除全部写访问事件；
- 普通日志不记录文件名、查询参数、Object Key、正文或 Token。

## 12. 保留与擦除

默认保留一年。紧急擦除必须覆盖：

- 原始对象；
- 缩略图、预览、OCR、文本和其他派生物；
- 未过期 Task Grant；
- 服务端导出副本；
- Agent 临时文件清理请求；
- 缓存与未完成分片上传。

数据库保留墓碑和不可逆审计元数据，但不保留原文件名和内容。任何下游无法确认删除时，
擦除结果必须报告 `partial`，不能记成成功。

## 13. 分阶段交付

```text
A0  Metadata、MinIO、上传完整性、所有权、预览/下载
A1  ClamAV、隔离解析、图片/PDF/文档派生物
A2  Task Grant、Media Gateway、Agent Output
A3  FAE 图片/文档真实接入
A4  VOC 与一个 MetaBot Agent 接入
A5  行政、其余 HR/Marketing 按能力逐个开放
A6  一年保留、紧急擦除与恢复演练
```

该轨与 Brain 阶段 0–3 并行；A2/A3 是 FAE attachment capability 对 Brain 可见的前置，
不是 VOC Action 的前置。

## 14. 验收

- 同一图片只上传一次，FAE 与另一个获授权 Agent 可分别读取；
- 同一 PDF/文档可委派给两个 Agent，无重复对象；
- 未授权 Task、错误 Agent、过期 Grant、擦除后读取全部拒绝；
- MIME 伪装、Hash 不一致、ClamAV 命中和扫描不可用都不能进入 ready；
- 任一非 `ready` 状态经 Media Gateway、Preview、Derivative 或 Output 下载均被后端拒绝；
- FAE 图片问答与文档证据链真实使用 Platform Attachment；
- Agent 输出文件回到 Conversation，可预览/下载；
- MetaBot 本地临时文件在完成、取消、崩溃恢复后清理；
- 一年保留和紧急擦除覆盖所有原件、派生物、分片和导出；
- 下游清理无法确认时 Erasure Job 终态为 `partial`，不得误报 completed；
- FAE 外部客户上传无回归；
- 日志与响应不暴露 Object Key、MinIO 凭据、文件路径或钉钉标识。

## 15. 发布与回滚

- MinIO Bucket 默认私有，不开匿名访问；
- Attachment API 在 Agent 集成前先独立验收；
- 每个 Agent 通过合同测试后逐个 bump `capability_version`；
- 回滚 Agent 能力时不删除已存附件，只撤销新 Task Grant；
- 回滚存储版本前停止新上传并排空扫描/派生队列；
- 擦除墓碑和已完成删除事实不可因回滚复活。
