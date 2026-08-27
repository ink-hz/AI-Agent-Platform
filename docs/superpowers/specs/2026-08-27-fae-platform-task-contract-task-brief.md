# AI FAE Agent 任务书：接入 Agent Brain 持久任务协议

**日期：** 2026-08-27

**状态：** 待 FAE 专属会话评审

**目标仓库：** `/Users/neo/Developer/work/AI-FAE-Agent`

**核对基线：** `origin/master@7302821`

**上游设计：** AI-Agent-Platform `2026-08-27-agent-brain-actions-and-external-adapters-design.md`、
`2026-08-27-http-task-contract-v1.md`、
`2026-08-27-platform-agent-attachment-substrate-design.md`

## 1. 背景

FAE 继续是独立、对外的专业 Agent。`fae.orbbec.com.cn`、公网 `/chat`、客户 Session、
知识库、模型、能力编排和 Review Center 不迁入 Platform。

本任务只增加一条面向 Agent Platform 的内部持久任务入口，使 Agent Brain 可以创建
FAE 任务、读取真实进展、追加消息和取消。不能用 Platform 长挂现有 `/chat` SSE 进行
包装，因为现有 Session 是内存态、SSE 断线不支持持久游标重放。

同时增加企业员工从 Agent Platform 直接进入 FAE 的钉钉单点身份，并让企业任务直接
复用 Platform 统一附件底座。上述增量不改变 FAE 面向外部客户的公开身份和上传模式。

FAE 与 Platform 的代码、部署和协议都由 Orbbec 团队掌控。本任务不是对不可修改的外部
服务做包装；应直接在 FAE 仓库实现规范 Task Facade 和持久任务能力。

## 2. 必须遵守的 FAE 架构原则

- 新任务最终仍进入现有 capability orchestration：context → planner → capability
  evidence → coverage → synthesis → outcome → trace；
- 不增加按样本硬编码或单桶路由；
- 不用静默 fallback 掩盖协议、检索或模型失败；
- `out_of_scope` 继续保守；
- 答案和来源分离；
- 质量验收由 Codex 或人工 FAE 完成，不由回答模型自评；
- 任何工作流变化同步维护 `AI_FAE_Agent_工作流设计.md` 和 `docs/DESIGN_INDEX.md`。

## 3. 边界

### 3.1 本次实现

- PostgreSQL 持久任务、任务消息、事件和幂等记录；
- 快速创建后台任务；
- 事件游标重放；
- 在同一 FAE 子会话追加消息；
- 取消意图和明确取消结果；
- Platform 短时签名身份和 Scope；
- Platform 一次性 Agent Launch 授权码交换和企业 Session Binding；
- Platform Task-scoped Attachment Grant，支持图片和文档；
- 能力/健康探测；
- 最大任务时长 600 秒；
- 规范化 FAE 最终结果、来源和限制。

### 3.2 本次不实现

- 不改 FAE 公网登录或客户身份；
- 不把 Platform Cookie 或钉钉身份传给 FAE；
- 不支持业务写 Action，`supports_actions=false`；
- 不把 Platform Conversation 全量历史传给 FAE；
- 不重启或切换生产，直到 Dev 合同和质量验收完成。

## 4. 内部 API

所有端点只允许内网/回环访问，并验证 Platform 短时凭证：

```text
POST /internal/platform/v1/tasks
POST /internal/platform/v1/tasks/{task_id}/messages
GET  /internal/platform/v1/tasks/{task_id}
GET  /internal/platform/v1/tasks/{task_id}/events?after={seq}&limit={n}&wait_seconds=0
POST /internal/platform/v1/tasks/{task_id}/cancel
GET  /internal/platform/v1/capabilities
GET  /internal/platform/v1/health
```

不得增加 `confirm_action`，FAE 本批没有业务写能力。

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
  "idempotency_key": "...",
  "deadline_at": "...",
  "authorized_scopes": ["fae.answer"]
}
```

服务必须先持久化任务和幂等指纹，再返回 `202`：

```json
{
  "downstream_task_id": "opaque",
  "status": "queued",
  "next_event_seq": 1,
  "duplicate": false
}
```

禁止让请求线程运行 FAE Loop。Platform 投递租约只有 45 秒，长驻 HTTP 会导致租约过期
和重复创建。

## 6. 持久化模型

使用 FAE 自己的 PostgreSQL，新增独立内部任务表，不复用内存 Session 作为事实源：

```text
platform_tasks
platform_task_messages
platform_task_events
platform_task_idempotency
```

核心约束：

- `platform_task_id` 唯一；
- 创建 `idempotency_key` 唯一且绑定 payload hash；
- `(task_id, message_seq)` 唯一；
- `(task_id, event_seq)` 唯一；
- 事件 seq 必须严格连续；
- 终态不可逆；
- 相同幂等键不同 payload 返回 `idempotency_conflict`；
- 下游任务 ID 不能决定 Platform 状态。

内存 Session 可以作为执行缓存，但进程重启后必须从持久消息重建完成任务所需上下文。

## 7. 状态和事件

任务状态：

```text
queued -> running -> waiting_input -> running
       -> completed | failed | cancelled | timed_out
```

事件至少包括：

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

所有公开事件必须是 FAE 运行时真实产生的事实。不得把历史答案倒推成进度，不得用定时器
生成“正在分析”等模拟消息。

线上事件严格遵循 HTTP Task Contract v1。FAE 内部的 `started/progress/sources/timed_out`
必须在内部 Task Facade 分别规范化为 `work_update/work_update/artifact/timeout`；创建
`accepted` 只由 `202` 回执表达。阻塞型问题使用 `input_required`，普通非阻塞提问使用
`message`。Platform Adapter 不负责猜测或翻译 FAE 私有事件。

## 8. 后续消息

`POST /messages` 按 `message_seq + idempotency_key` 幂等。消息进入同一 FAE 任务会话的
context/planner/synthesis，不新建无关联 Session。若当前执行不可安全中断：

- 持久化消息；
- 当前执行到稳定边界后消费；
- 发出明确 `message_queued`/`message_consumed` 事件；
- 不得假装消息已经影响正在生成的答案。

## 9. 取消

取消请求先持久化 `cancel_requested`，再由 Worker 在安全边界停止。重复取消幂等。

- 尚未开始：直接 cancelled；
- 正在检索/规划：在下一个可取消边界停止；
- Provider 请求无法取消：明确 `cancel_pending`，丢弃未授权后的输出投影，最终转 cancelled；
- 已终态：返回既有终态，不改写结果。

## 10. 身份与安全

只接受 Platform 签发的 audience-bound 短时凭证，至少校验：issuer、audience、kid、
expiry、agent_id=`ai-fae-agent`、task_id 和 `fae.answer` Scope。

- 不信任请求体中的 user_id、角色或部门；
- 不记录 Token、内部用户 ID、完整 Prompt 或附件名；
- 任务正文和消息加密存储；
- 错误响应不透出 SQL、文件路径、密钥或内部 Provider 信息；
- 内部路由不得由 FAE 公网 Nginx 暴露；
- `/health` 公网行为保持不变，内部 capabilities/health 只对签名请求开放。

### 10.1 企业员工工作区 SSO

员工从 Platform 专业 Agent 目录进入 FAE 时：

```text
Platform 登录 + Agent 授权
  -> 60 秒单次 Agent Launch code
  -> 浏览器跳转 fae.orbbec.com.cn 企业 SSO 回调
  -> FAE 服务端向 Platform back-channel exchange
  -> internal_user_id + identity_binding_id
  -> FAE 创建 HttpOnly/Secure/SameSite=Lax 企业 Session
```

FAE 不接收 Platform Cookie。授权码不写访问日志、使用后立即失效。FAE 只保存
`internal_user_id` 和 opaque binding，不保存钉钉原始 ID。敏感企业请求校验 binding，
缓存最多60秒。Platform 注销、停用、离职或撤销 FAE 授权后 binding 必须失效。

企业 Session 与公开客户 Session 使用明确不同的 authentication mode；公共客户入口不
自动跳钉钉，也不能冒充企业身份。企业 FAE Session、Feedback 和 Trace 必须能映射回
Platform internal_user_id。

### 10.2 Platform 附件与图片

附件服务本身由独立的
`2026-08-27-platform-agent-attachment-substrate-design.md` 实施，不塞进 FAE 持久任务
迁移。FAE 负责消费统一合同，不再创建第二套企业附件事实源。

企业 Task 中的 `attachment_refs` 指向 Platform 附件，不是 FAE 本地附件 ID。FAE 使用
任务 Token 访问 Platform 内部 Media Gateway：

- Grant 绑定 task_id、agent_id、attachment_id、用途和到期时间；
- FAE 不获得 MinIO 凭据、Object Key、本地路径或长期 URL；
- 下载前校验 MIME、大小、SHA-256 和 Catalog 能力；
- 图片进入现有 vision/图片证据路径；
- PDF和文档进入现有附件解析、OCR/文本证据路径；
- 同一附件无需复制即可被多个获授权 Agent 分别读取；
- FAE 生成的报告或文件先上传 Platform Output Attachment API，再在 Result 返回
  `attachment_refs`；
- 任务终止、授权撤销、Grant 过期或附件擦除后不得继续读取。

现有 FAE 公共客户上传继续使用 FAE 自身存储。企业 Task 不得把 Platform 附件复制到
公共客户存储后长期保留。

## 11. 结果合同

最终 Result 至少包含：

```text
outcome
answer_markdown
sources
planned_capabilities
capability_coverage
risk_notes
fallback_used
fallback_reason
trace_ref
duration_ms
```

结果必须保留 FAE 的结构化来源和 outcome，不把知识文件路径拼进正文。Fallback 必须显式
并在质量验收中按失败处理，除非预期是拒绝或安全弃答。

## 12. 时间与可靠性

- FAE 最大活动时长 600 秒；
- 创建任务端点应在持久化后快速返回，不等待模型；
- Platform 内部 Event GET 支持 `after + limit + wait_seconds=0`，立即返回有限 JSON 页面；
- FAE 可以为其他消费者保留 SSE/长轮询，但 Platform Adapter 不得使用；
- Worker 使用自己的执行租约并续租，不能借用 Platform 45 秒投递租约；
- Worker 崩溃后任务可重新领取；
- 幂等和事件序列保证恢复不重复结果。

## 13. 测试

- 创建和幂等冲突；
- Worker 在持久化前后崩溃；
- 事件游标断线、重连、重复请求和序号缺口；
- `wait_seconds=0` 无事件时立即返回，`timed_out` 事件被合同测试拒绝；
- 多轮 context 真正进入 planner/retrieval/synthesis；
- 后续消息在执行边界前后到达；
- 取消在 queued、running、terminal 三种状态；
- Token 过期、错误 audience、错误 Scope 和签名轮换；
- Agent Launch 码重放、错误 state/return path、Binding 撤销和公开客户身份隔离；
- 图片、PDF和文档的 Task Grant、SHA-256、过期、撤销和跨 Task 越权；
- 公网 `/chat`、历史、附件、Review 无回归；
- Catalog/spec/selection/SDK/troubleshooting/context 的针对性评测；
- Codex 或人工 FAE 独立质量复审。

## 14. 验收和交付

交付报告必须包含：

- 实际迁移、代码和 Commit；
- 内部 API Schema；
- HTTP Task Contract v1 黑盒测试报告；
- 企业工作区 SSO、Binding 和统一 internal_user_id 证据；
- Platform 图片/文档读取与输出附件回传证据；
- 事件和状态表；
- 幂等/崩溃恢复证据；
- 600 秒边界测试；
- 真实多轮追问和取消案例；
- 独立答案质量结论；
- FAE 公网入口、容器和客户体验不变证据；
- 回滚路径和未解决问题。

未完成持久化、事件重放、追问或取消中的任一项时，不得把 FAE 标记为 Brain
`brain_delegation` 可用。
