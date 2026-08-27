# AI 行政 Agent 任务书：接入 Agent Brain 持久任务协议

**日期：** 2026-08-27

**状态：** 待行政专属会话评审

**目标仓库：** `/Users/neo/Developer/work/AI-ADMIN-Agent`

**核对基线：** `origin/master@e95e274`

**上游设计：** AI-Agent-Platform `2026-08-27-agent-brain-actions-and-external-adapters-design.md`、
`2026-08-27-http-task-contract-v1.md`、
`2026-08-27-platform-agent-attachment-substrate-design.md`

## 1. 事实基线

当前行政仓库已经有持久化 Jobs，不要重复创建一套队列：

```text
POST /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/events?after=
PostgresJobStore / InMemoryJobStore
JobWorker / ConcurrentJobWorker
idempotency_key
Job Delivery lease
```

本任务应扩展既有 Jobs，补齐 Platform 内部身份、持续消息、取消和强制 Scope。`/office/*`
门户、行政问答、班车、住宿和反馈功能保持正常使用。

行政浏览器身份和 Brain 服务端任务身份必须都由 Platform 底座提供，并关联同一个
`internal_user_id`；两条链路不能混用 Cookie 或 Token。Agent Conversation 附件统一
使用 Platform Attachment，不再为行政 Agent 另造一套附件存储。

行政与 Platform 的代码、部署和协议都由 Orbbec 团队掌控。本任务直接修改行政仓库的
Jobs、内部 Facade 和领域 Gateway，不把行政当作不可修改的第三方服务。

## 2. 分两批交付

### 2.1 第一批：只读 Agent

仅允许：

```text
service_catalog.read
shuttle.read
lodging.read
feedback.own.read
```

第一批不实现 Action 执行端点，不实现 `ActionCapableAdapter`，Catalog
`supports_actions=false`。能力不存在优于“存在但约定不调用”。

### 2.2 第二批：写操作

只有 Platform VOC 确认机制通过不确认、确认、拒绝、过期、Digest 变化、重复确认和
崩溃恢复验收后，才允许设计并开放：

- 班车预订/取消；
- 住宿申请/修改/取消；
- 反馈提交或其他产生外部状态的操作。

第二批必须 bump `capability_version`，不能在第一批版本下静默增加写能力。

## 3. 内部 API

在既有 Jobs 之上增加签名的内部规范入口：

```text
POST /internal/platform/v1/tasks
POST /internal/platform/v1/tasks/{task_id}/messages
GET  /internal/platform/v1/tasks/{task_id}
GET  /internal/platform/v1/tasks/{task_id}/events?after={seq}&limit={n}&wait_seconds=0
POST /internal/platform/v1/tasks/{task_id}/cancel
GET  /internal/platform/v1/capabilities
GET  /internal/platform/v1/health
```

这些端点可以内部调用既有 Job Store，但不能把现有通用 `/jobs` 直接暴露给 Platform
并依赖浏览器 Cookie。现有 `/jobs` 的兼容行为不得被破坏。

第一批不得存在 `/actions/{id}/execute`。

## 4. 复用与增量

复用：

- Job ID、状态、结果和事件 Store；
- 创建幂等键；
- Event `after` 游标；
- Job Worker 和并发 Worker；
- Postgres 持久化；
- 既有业务 Store 的 idempotency_key。

新增：

- `platform_task_id` 唯一映射；
- Platform payload 指纹；
- `authorized_scopes` 的不可变任务快照；
- 任务消息和 message sequence；
- `cancel_requested` 与取消终态；
- Platform Token 的 issuer/audience/kid 校验；
- 规范化能力、健康和错误码；
- read-only Tool/Service Gateway。

内部 Task Facade 必须把既有 Job Event 规范化为 HTTP Task Contract v1，并用立即返回的
有限 JSON 页面暴露。现有 `/jobs/{id}/events` 可以继续为其他消费者提供 SSE/长轮询，
但 Platform 内部端点的 `wait_seconds` 必须为 0，不能阻塞 Brain Worker Tick。

## 5. 创建任务

请求至少包含：

```json
{
  "platform_task_id": "uuid",
  "conversation_ref": "opaque",
  "turn_ref": "opaque",
  "objective": "...",
  "context_excerpt": ["..."],
  "constraints": ["..."],
  "attachment_refs": ["uuid"],
  "expected_output": "...",
  "capability_version": 2,
  "idempotency_key": "...",
  "deadline_at": "...",
  "authorized_scopes": ["service_catalog.read"]
}
```

内部入口把该请求规范化为现有 Admin Job，持久化映射后快速返回 `202`。不得在 Platform
45 秒投递租约内等待行政模型完成。

创建前必须把 `capability_version` 与当前行政 Capability 比较；不一致返回
`409 capability_changed + current_capability_version + must_refresh_capabilities=true`，
不创建 Job。`deadline_at` 是硬截止：过期请求拒绝入队，到点后停止新的模型/工具/领域
调用并恰好一次写入 `timeout`。即使 Platform 已超时，行政侧也不得继续任何业务写操作。

## 6. 持续消息与取消

### 6.1 后续消息

新增 Job Message Store：`(job_id, seq)` 唯一，按 idempotency key 防重复。Worker 在
安全边界消费消息，并发出 `message_queued`、`message_consumed` 和真实回复事件。

消息必须进入同一行政任务上下文，不能新开无关联 `/chat` Session，也不能声称已影响
一个已经完成的答案。

### 6.2 取消

新增持久 `cancel_requested`。queued Job 可直接取消；running Job 在下一安全边界停止；
终态 Job 重复取消返回原状态。任何已提交的业务写操作不能通过取消假装回滚。

## 7. Scope 是上游硬边界

Platform 声明 Scope 不足以构成安全边界。行政服务必须在调用服务目录、班车、住宿和反馈
领域服务时再次校验任务 Scope。

- 缺 Scope 返回 `scope_denied`；
- Prompt 说“用户同意了”不能扩大 Scope；
- 浏览器字段中的 role、user_id、部门或管理员标记不可信；
- 第一批 Worker 不能调用任何写方法；
- 不允许把 Platform Member 映射成行政管理员；
- 读取“我的”数据必须使用 Token 中的 `internal_user_id`，不能接受请求体覆盖。

建议为 Agent 任务建立独立 `ReadOnlyAdministrativeGateway`，只暴露第一批四类只读查询，
而不是把完整 Store 对象交给模型或编排器。

## 8. 身份和 Token

只接受 Platform 签发的短时、audience-bound Token：

```text
audience = ai-admin-agent
agent_id = ai-admin-agent
agent_task_id
internal_user_id
capability_version
authorized_scopes
task_deadline_at
action_execution_deadline_at (仅第二批 Action Execute Token)
issued_at / expires_at / request_id / kid
```

- 不传 Platform Cookie；浏览器 `/office/*` 的既有同域身份链路保持不变；
- 内部任务 Token 和 `/office` 浏览器 Session 是两条不同调用链；
- Token 不写日志、数据库或错误正文；
- 私钥/公钥文件 `0600`、不跟随符号链接、支持 `kid` 轮换；
- 错误 audience、过期、错误 Agent/Task 绑定全部拒绝。

### 8.1 `/office` 浏览器身份

`/office/*` 与 Platform 同源，继续使用具名 Platform Session Cookie。行政后端只把该
Cookie 用于回环调用 Platform 最小 Subject 端点，取得
`internal_user_id + display_name + active`。行政不得复制、续签、持久化或记录该 Cookie，
也不得使用通用账号端点取得手机号、CSRF Token 或其他多余 PII。

### 8.2 Brain 任务身份

Brain Adapter 不使用浏览器 Cookie，而使用短时 Task Token。无论用户从 `/office`
直接操作还是由 Brain 委派，行政审计、Job 和业务记录都归属于同一个
`internal_user_id`。不得按花名、手机号或浏览器字段猜测合并身份。

### 8.3 Platform 附件

附件服务由独立的 `2026-08-27-platform-agent-attachment-substrate-design.md` 实施。行政
第一批只读任务不依赖附件上线；声明附件能力后，行政只消费统一 Grant/Output 合同。

行政 Agent 的 Conversation 输入和输出使用 Platform Task-scoped Attachment Grant：

- 行政服务不取得 MinIO 凭据或 Object Key；
- 只读取 Token 和 Catalog 允许的 MIME/大小；
- 任务结果附件上传回 Platform，返回 `attachment_refs`；
- 未授权、过期、跨 Task 和已擦除附件明确拒绝；
- 住宿证明、反馈凭证等现有业务附件仍属于对应业务记录。Agent 若需要读取，必须经过
  明确 Scope 和 Platform Attachment 引用/受控复制，不能暴露业务存储路径；
- 第一批只读 Agent 不得借附件机制提交业务表单或触发写操作。

## 9. 事件和结果

事件至少规范化为：

```text
thinking_summary
message
work_update
artifact
input_required
finding
result
failed
cancelled
timeout
```

现有 Job Event Sequence 继续作为上游事实序列。结果至少包含 outcome、answer_markdown、
evidence_refs、limitations、fallback state、trace_ref 和 duration。

不得通过定时器或耗时推断伪造工作进度。没有阶段事件时只报告“运行中，尚无更新”。
现有 Job 的 `accepted/started/progress/done/timed_out` 在行政内部 Facade 按 HTTP Task
Contract v1 映射，Platform Adapter 不做猜测式翻译。阻塞输入只能使用
`input_required`；普通消息不改变任务状态。

## 10. 第二批 Action 契约

行政必须先在自己的 PostgreSQL 中持久化 Canonical Parameters、Action ID 和 Digest，再
发出 HTTP Task Contract v1 的 `action_required` 事件。Proposal Payload 全部按公共合同
实现；不能让模型 Markdown 或前端文本产生 Action。

Digest 原文只包含
`platform_task_id + action_seq + action_kind + parameters`，线上为 lowercase hex；
Summary/Impact 不参与。行政必须使用公共 Contract Fixture 验证与 Platform 得到完全相同的
Hash。

第二批才增加：

```text
POST /internal/platform/v1/tasks/{task_id}/actions/{action_id}/execute
```

执行请求只包含 Platform 确认的 Action ID、Digest 和幂等键，不携带 Parameters。行政
服务从 propose 阶段自己的持久化副本读取唯一执行参数：

1. 校验写 Scope；
2. 复算 Canonical JSON SHA-256；
3. Digest 不一致返回 `action_digest_mismatch`；
4. 原样执行上游 propose 时持久化、且用户已确认的参数；
5. 外部状态变化时失败并要求重新 propose；
6. 不自动换班次、住宿、时间、人员或其他业务参数；
7. 重复确认/投递返回同一业务结果，不重复写入。

Action 执行必须复用班车、住宿、反馈 Store 已有的业务 idempotency_key，而不是只在 HTTP
入口去重。

确认后的独立执行截止由签名 Task Token 的 `action_execution_deadline_at` 给出。行政必须
在执行前和提交领域写入前检查；领域写入与幂等结果必须在同一事务内再次校验并原子提交。
截止后恰好一次返回 `timeout`，不能让已超时的预订、申请或提交继续成为孤儿写操作。

## 11. 生产不变项

- `/office/` 行政问答保持可用；
- `/office/?view=services` 行政门户保持可用；
- 班车、住宿、反馈现有员工和管理页面不变；
- 现有 `/jobs` 和结果投递不回归；
- 行政后端继续只监听回环/内部端口；
- 不修改 FAE；
- Platform Adapter 尚未验收前不在 Catalog 打开 brain_delegation。

## 12. 测试

行政仓库不复制合同断言。CI 通过 `CONTRACT_TEST_COMMIT` 检出 AI-Agent-Platform 的
`contracts/http_task_v1/`，校验目录 SHA-256 后对本地 Admin 服务运行同一 HTTP 黑盒
Driver；Commit 与 Hash 写入 Release Manifest。

第一批：

- 复用现有 Job 的创建、重复创建、事件游标和恢复；
- capability_version 当前值成功、旧值明确拒绝且不创建 Job；
- deadline_at 过期拒绝、运行中到点写 timeout，迟到结果不允许调用领域写方法；
- Platform 内部事件端点 `wait_seconds=0` 立即返回，旧事件正确规范化，`timed_out` 被拒绝；
- 后续消息序号与幂等冲突；
- queued/running/terminal 取消；
- Token 过期、错误 audience、错误 Task、错误 Scope；
- `/office` 和 Brain 任务对同一员工解析为同一个 internal_user_id，且 Task API 从未
  接收 Platform Cookie；
- Platform 图片/文档 Grant 的成功读取、过期、撤销、跨 Task 越权和输出附件回传；
- 四类只读查询成功；
- 所有写方法在代码层不可达或返回 scope_denied；
- 用户只能读取自己的反馈、班车和住宿数据；
- `/office`、现有 Jobs、班车、住宿、反馈回归。

第二批：

- 不确认、拒绝、过期时无业务写入；
- Digest 变化和参数变化拒绝；
- Action Proposal 先持久化参数，Execute 请求不含参数；
- 重复 execute 不重复预订/申请/提交；
- 提交后 Worker 崩溃仍能恢复同一结果；
- 外部状态变化返回业务冲突，不自适应参数；
- Platform Brain 预算耗尽不丢失已确认操作结果。

## 13. 交付报告

报告必须包含：

- 实际复用和新增的 Jobs 结构；
- 内部 API 与 Token Schema；
- HTTP Task Contract v1 黑盒测试报告；
- `/office` Subject 身份和 Brain Task 身份的统一 internal_user_id 证据；
- Platform 附件复用和领域业务附件边界证据；
- Scope 到具体业务方法的矩阵；
- 后续消息、取消和断线恢复证据；
- 第一批只读越权测试；
- `/office` 和现有行政业务不变证据；
- 第二批 Action 幂等和 Digest 证据（进入第二批时）；
- Commit、部署、回滚和未解决问题。
