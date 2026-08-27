# Orbbec HTTP Task Contract v1

**日期：** 2026-08-27

**状态：** 待 Platform、FAE、行政三方书面评审

**适用仓库：** AI-Agent-Platform、AI-FAE-Agent、AI-ADMIN-Agent

## 1. 所有权与定位

Platform、FAE、行政、VOC、MetaBot 及其部署代码都由 Orbbec 团队掌控，可以协调修改、
测试和发布。本协议里的“上游 Agent”“下游服务”只描述一次调用的数据方向，不表示外部
供应商、不可修改的黑盒或较弱的控制权。

之所以仍使用版本化 HTTP 合同，是因为这些服务独立部署、独立持久化并具有不同故障域。
合同用于保证跨仓变更可测试、可灰度、可回滚，而不是迁就不能修改的系统。

本协议是以下三份任务书共同引用的唯一线上合同：

- Agent Platform 实施任务书；
- AI FAE Agent 接入任务书；
- AI 行政 Agent 接入任务书。

各任务书不得重新定义事件名称、Action Payload、执行参数或事件等待语义。

## 2. 版本规则

- 规范版本固定为 `orbbec-http-task/v1`；
- 请求使用 `X-Orbbec-Task-Contract: orbbec-http-task/v1`；
- 不兼容变更发布 v2，不能在 v1 下改变字段含义；
- `capability_version` 表示单个 Agent 能力版本，不等于合同版本；
- Platform 只在 Capability Probe 和合同测试通过后把对应 Agent 标记为可调度。

## 3. 统一端点

```text
POST /internal/platform/v1/tasks
POST /internal/platform/v1/tasks/{task_id}/messages
GET  /internal/platform/v1/tasks/{task_id}
GET  /internal/platform/v1/tasks/{task_id}/events?after={seq}&limit={n}&wait_seconds=0
POST /internal/platform/v1/tasks/{task_id}/cancel
GET  /internal/platform/v1/capabilities
GET  /internal/platform/v1/health
```

声明 `supports_actions=true` 的 Agent 额外实现：

```text
POST /internal/platform/v1/tasks/{task_id}/actions/{action_id}/execute
```

这些端点都验证 Platform 签发的短时 Task Token，不依赖浏览器 Cookie，也不信任请求体中
自称的用户、部门、角色或管理员标记。

## 4. 创建、消息与取消

创建任务至少包含：

```json
{
  "contract_version": "orbbec-http-task/v1",
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
  "authorized_scopes": ["..."]
}
```

服务先持久化任务和 Payload 指纹，再快速返回：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "downstream_task_id": "opaque",
  "status": "queued",
  "next_event_seq": 1,
  "duplicate": false
}
```

HTTP 状态为 `202`。相同幂等键与相同 Payload 返回相同任务；相同幂等键与不同 Payload
返回 `409 idempotency_conflict`。

后续消息按 `(task_id, message_seq, idempotency_key)` 幂等。取消先持久化
`cancel_requested`，重复取消返回相同结果；终态不可反向改变。

## 5. 非阻塞事件游标

Platform Adapter 的 Worker Tick 是单线程顺序编排。为了不阻塞其他 Agent 的派发、取消
和心跳，Platform 调用事件端点时必须满足：

```text
wait_seconds = 0
1 <= limit <= 100
响应为有限 JSON 页面，不使用 SSE
```

响应格式：

```json
{
  "events": [],
  "next_after": 12,
  "terminal": false
}
```

`next_after` 是本页最后一个已返回序号；无新事件时等于请求 `after`。上游可以保留面向
其他消费者的 SSE 或长轮询端点，但 Platform 内部合同端点不得阻塞等待。未来若拆出独立
Reconcile Worker，必须另写设计和独立心跳，不能悄悄改变 v1 语义。

事件序号从 1 开始严格连续。相同 `(task_id, seq)` 只允许幂等重放完全相同的事件。

## 6. 规范事件词表

HTTP Task Contract v1 在线只允许以下 `kind`：

| kind | 是否终态 | Platform 状态影响 | 语义 |
|---|---:|---|---|
| `thinking_summary` | 否 | 无 | Provider 返回的可展示思考摘要；不是原始思维链 |
| `message` | 否 | 无 | 面向用户的普通消息，不表示任务阻塞 |
| `work_update` | 否 | queued/dispatched 可转 running | 真实执行进度或阶段变化 |
| `artifact` | 否 | 无 | 来源集合、文件、表格或其他结构化产物 |
| `input_required` | 否 | 转 `waiting_input` | 任务必须取得用户输入后才能继续 |
| `action_required` | 否 | 转 `waiting_confirmation` | 上游已持久化一个不可逆操作提案 |
| `finding` | 否 | 无 | 可供大脑提前使用的中间发现 |
| `result` | 是 | 转 `completed` | 最终成功或部分完成结果 |
| `failed` | 是 | 转 `failed` | 明确失败 |
| `timeout` | 是 | 转 `timed_out` | 超时；线上禁止使用 `timed_out` 作为事件名 |
| `cancelled` | 是 | 转 `cancelled` | 取消完成 |

`accepted`、`started`、`progress`、`sources`、`done`、`succeeded`、`timed_out` 和
`question` 不是 v1 在线事件名。各 Agent 的内部 Facade 在返回事件前完成规范化：

| 既有内部事件 | v1 输出 | 规则 |
|---|---|---|
| `accepted` | 无事件 | 创建任务的 `202` 回执已表达接受；Platform 任务转 `dispatched` |
| `started` | `work_update` | `payload.phase="started"` |
| `progress` | `work_update` | 保留真实阶段、百分比或摘要；不得按时间伪造 |
| `sources` | `artifact` | `payload.artifact_type="sources"` |
| `done` / `succeeded` | `result` | 只在上游真实终态时产生 |
| `timed_out` | `timeout` | 必须在上游 Facade 改名 |
| 阻塞型 `question` | `input_required` | 必须带稳定 `input_request_id` |
| 非阻塞型 `question` | `message` | 不改变任务状态 |

映射职责属于 FAE/行政各自的内部 Task Facade。Platform Adapter 不猜测未知事件；遇到非
规范事件返回 `protocol_violation`，保持原始游标不前移并报告 Agent 不健康。

`question` 保留为 MetaBot 旧投影的兼容事件，但新 HTTP Agent 不得发送。`input_required`
是唯一会把 HTTP Task 转为等待用户输入的事件。

## 7. Action Proposal

Action 由专业 Agent 提出，不由大脑或前端从文本推断。上游必须先持久化 Canonical
Parameters，再发出 `action_required`：

```json
{
  "seq": 17,
  "kind": "action_required",
  "created_at": "2026-08-27T10:00:00Z",
  "payload": {
    "action_id": "uuid",
    "action_seq": 1,
    "action_kind": "voc.submit",
    "summary": "提交本次 VOC 草稿",
    "impact": "将生成正式业务记录",
    "parameters": {},
    "action_digest": "lowercase-hex-sha256",
    "expires_at": "2026-08-27T12:00:00Z",
    "execution_timeout_seconds": 120
  }
}
```

硬约束：

- `action_id = uuid5(platform_task_id, "action:" + action_seq)`；
- 上游持久化的 Canonical JSON 是最终执行参数唯一事实源；
- Canonical JSON 使用 UTF-8、键排序、固定分隔符，禁止 NaN/Infinity；
- 上游计算 Digest 后持久化参数与 Digest，再发事件；
- Platform 重新计算并校验 Digest，随后加密保存参数、摘要和影响；
- Platform 的公开确认卡只由持久 Action 投影生成；
- 相同 Action 重放必须字节和 Digest 一致，不一致返回 `action_conflict`；
- `execution_timeout_seconds` 不能超过 Capability 固定上限。

## 8. Action Execute

确认后 Platform 调用：

```json
{
  "action_id": "uuid",
  "action_digest": "lowercase-hex-sha256",
  "idempotency_key": "..."
}
```

执行请求不得携带 `parameters`。上游必须读取 propose 时持久化的参数，复算 Digest，并与
请求 Digest 比较。参数不存在、Digest 不一致、Action 过期或世界状态已变化时明确失败；
不得让 Platform 参数覆盖持久副本，也不得自动更换班次、房间、客户或其他目标。

相同幂等键重复执行返回相同业务结果，不产生第二次写入。写操作同时使用领域 Store 的
业务幂等键，不能只在 HTTP 层去重。

## 9. Action 等待与唤醒

进入 `waiting_confirmation` 前必须存在一个覆盖相关 Task 的 active wait subscription。
确认、拒绝、过期或明确修改 Action 时，Platform 采用迁移 046 的用户介入模式：

1. 原子终结当前 wait subscription；
2. 把 Action 事实写入等待 Tool Result；
3. 完成当前 Step 并创建下一 Step；
4. 恢复 Loop 活动时钟；
5. 确认路径同时创建 Action Execution Delivery；
6. 下一 Brain Step 可以等待真实执行结果，不能把“已确认”展示为“已执行”。

不复用旧 active wait，也不允许确认后依靠定时扫描碰运气唤醒。

## 10. 普通消息与 Pending Action

普通消息不自动废弃全部 Pending Action。规则如下：

- 用户从确认卡选择“修改”或消息带 Platform 生成的 `action_id` 介入上下文时，只
  supersede 对应 Action；
- 上游对同一 Action 提出新参数时，旧 Action superseded；
- 与 Action 无关的普通消息可以在同一 Turn 继续处理，原确认卡保持 pending；
- 前端在任何 supersede 操作前明确显示“原确认将失效”；
- 大脑文字不能自行把 Action 标记 superseded。

## 11. 错误码

至少统一：

```text
contract_version_unsupported
protocol_violation
idempotency_conflict
scope_denied
task_not_found
task_terminal
message_sequence_conflict
event_sequence_conflict
action_conflict
action_digest_mismatch
action_expired
capability_changed
attachment_unsupported
upstream_unavailable
```

错误返回不包含 Token、Cookie、数据库正文、文件路径、对象键或 Provider 敏感信息。

## 12. 合同测试

Platform 提供同一份黑盒 Contract Test Suite，FAE 和行政都必须运行。至少覆盖：

- 创建快速返回和幂等冲突；
- `wait_seconds=0` 立即返回，无事件时不阻塞；
- 规范事件词表和所有旧事件映射；
- `timeout` 终态，`timed_out` 被拒绝；
- `input_required` 与普通 `message` 的状态差异；
- Action propose、Digest 重算、execute 不带参数；
- 重复 execute 不重复业务写入；
- 错误 Scope、Audience、Task 绑定和 Token 轮换；
- 断线后从 `after` 精确重放，无重复、无缺口；
- 终态不可逆。

只有合同测试通过并记录具体 Commit 后，Platform 才能为该 Agent 打开
`brain_delegation`。
