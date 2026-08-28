# Orbbec HTTP Task Contract v1

**日期：** 2026-08-27

**状态：** v1 基线已冻结；2026-08-28 测试夹具、Token Broker 与严格响应模型修订待确认

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

### 3.1 严格响应模型

所有 v1 JSON 请求与响应都拒绝未知字段。布尔值不得作为整数通过校验；时间使用带时区的
RFC 3339 UTC 字符串；UUID 使用带连字符的小写字符串。

`GET /internal/platform/v1/capabilities` 返回：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "agent_id": "ai-fae-agent",
  "capability_version": 2,
  "supports_actions": false,
  "max_duration_seconds": 600,
  "supported_scopes": ["fae.answer"],
  "supported_event_kinds": ["artifact", "cancelled", "failed", "finding", "input_required", "message", "result", "thinking_summary", "timeout", "work_update"]
}
```

`supported_scopes` 与 `supported_event_kinds` 是去重后的字典序数组；事件只能来自 §6
规范词表。`supports_actions=true` 时必须包含 `action_required`。

`GET /internal/platform/v1/health` 只返回可用于调度的最小状态，不暴露依赖正文：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "status": "healthy",
  "capability_version": 2
}
```

`status` 只允许 `healthy | degraded | unavailable`。

`GET /internal/platform/v1/tasks/{task_id}` 返回：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "downstream_task_id": "opaque",
  "platform_task_id": "uuid",
  "status": "running",
  "cancel_requested": false,
  "next_event_seq": 3,
  "terminal": false,
  "created_at": "2026-08-27T10:00:00Z",
  "updated_at": "2026-08-27T10:00:05Z"
}
```

任务 `status` 只允许：

```text
queued | running | waiting_input | waiting_confirmation |
completed | failed | cancelled | timed_out
```

`terminal=true` 当且仅当状态属于 `completed | failed | cancelled | timed_out`。
`next_event_seq` 始终等于当前最大事件序号加一，新任务为 `1`。

### 3.2 消息、取消与 Action Execute 回执

消息请求固定为：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "message_seq": 1,
  "content": "补充现场日志",
  "attachment_refs": [],
  "idempotency_key": "opaque"
}
```

成功返回 HTTP `202`：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "downstream_task_id": "opaque",
  "message_seq": 1,
  "status": "accepted",
  "duplicate": false
}
```

`accepted` 只表示消息已经持久化，不表示已经影响当前推理。

取消请求固定为：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "idempotency_key": "opaque"
}
```

成功返回 HTTP `200` 或 `202`：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "downstream_task_id": "opaque",
  "cancel_request_id": "opaque",
  "status": "cancel_requested",
  "duplicate": false
}
```

`status` 可以是 `cancel_requested | cancelled` 或任务既有终态。重复取消必须返回相同
`cancel_request_id`；`duplicate=true`，但 `status` 可以从 `cancel_requested` 前进到
`cancelled`，不能倒退。

Action Execute 成功返回 HTTP `200` 或 `202`：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "action_id": "uuid",
  "execution_id": "opaque",
  "status": "queued",
  "duplicate": false
}
```

`status` 只允许 `queued | running | completed | failed`。相同幂等键重复执行必须返回相同
`execution_id`，`duplicate=true`；当前执行状态可以单向前进。该回执不携带业务参数。

## 4. 创建、消息与取消

创建任务固定包含：

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
  "capability_version": 2,
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

`capability_version` 必须等于上游 `GET /internal/platform/v1/capabilities` 当前公开的
版本。Platform 在发请求前先做本地授权与版本校验，上游在持久化任务前再次校验；版本
不一致返回 `409 capability_changed`，并携带 `current_capability_version` 和
`must_refresh_capabilities=true`，不得创建任务。这是同一份能力快照的对称校验，不是
把 Platform 的授权判断转移给上游。

`deadline_at` 是上游必须执行的 UTC 硬截止时间，不是提示字段：

- 已经过期的创建请求返回 `deadline_expired`，不得排队；
- 到点后不得开始新的模型调用、工具调用或业务写操作；
- 正在运行的可取消工作应请求取消，并恰好一次地产生规范终态事件 `timeout`；
- Task 已超时后到达的结果只能记入隔离诊断，不能反向改变终态或触发业务写入；
- Action 确认后的独立执行窗口由签名 Task Token 中的
  `action_execution_deadline_at` 约束，不改变本节 Task Deadline，也不把业务参数放回
  Execute 请求。

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
  "contract_version": "orbbec-http-task/v1",
  "downstream_task_id": "opaque",
  "events": [],
  "next_after": 12,
  "terminal": false
}
```

`next_after` 是本页最后一个已返回序号；无新事件时等于请求 `after`。上游可以保留面向
其他消费者的 SSE 或长轮询端点，但 Platform 内部合同端点不得阻塞等待。未来若拆出独立
Reconcile Worker，必须另写设计和独立心跳，不能悄悄改变 v1 语义。

`events` 中每个对象严格包含 `seq`、`kind`、`created_at` 和 `payload`；`payload` 必须是
JSON 对象。事件页拒绝未知顶层字段，事件对象拒绝未知字段；具体 `payload` 由事件种类对应
Schema 约束。

事件序号从 1 开始严格连续。相同 `(task_id, seq)` 只允许幂等重放完全相同的事件。
Platform 在写库前验证整页事件从 `after + 1` 开始且页内连续。发现缺口、乱序或同序不同
内容时，不把异常抛出到整个 Worker Tick：只把该 Task 终结为
`status=failed, reason_code=protocol_violation`、把对应 Agent 健康投影标为异常，并继续
处理其他 Agent。
原始缺口游标不得前移；Platform 生成的任务终结事实使用独立的控制面记录，不伪造一个
来自上游的缺失序号事件。

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
| `message_queued` | `work_update` | `payload.phase="message_queued"`；后续消息已持久化但尚未消费 |
| `message_consumed` | `work_update` | `payload.phase="message_consumed"`；后续消息已进入任务上下文 |
| `cancel_pending` | `work_update` | `payload.phase="cancel_pending"`；已持久化取消请求、正等待安全边界 |
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
- Digest 原文固定且只包含以下四个字段：

  ```text
  action_digest = sha256(canonical_json({
    "platform_task_id": <UUID lowercase string>,
    "action_seq": <positive integer>,
    "action_kind": <string>,
    "parameters": <JSON object>
  }))
  ```

- Canonical JSON 固定采用 RFC 8785 JSON Canonicalization Scheme (JCS) 的 UTF-8 字节；
  等价实现必须按 Unicode 码点排序对象键、无多余空白、保留数组顺序、不 ASCII-escape
  普通 Unicode，并拒绝 NaN、Infinity 和非法 Unicode surrogate；UUID 使用带连字符的
  小写字符串；
- 线上 `action_digest` 是 64 个字符的 lowercase hex；Platform 入库前解码为
  `bytea(32)`；
- `summary` 和 `impact` 是加密保存的展示投影，明确不进入 Digest；修改展示文案不能改变
  已冻结的业务操作；
- 上游计算 Digest 后持久化参数与 Digest，再发事件；
- Platform 重新计算并校验 Digest，随后加密保存参数、摘要和影响；
- Platform 的公开确认卡只由持久 Action 投影生成；
- 相同 Action 重放必须字节和 Digest 一致，不一致返回 `action_conflict`；
- `execution_timeout_seconds` 不能超过 Capability 固定上限。

## 8. Action Execute

确认后 Platform 调用：

```json
{
  "contract_version": "orbbec-http-task/v1",
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

`action_required` 到达时，Platform 必须无条件、原子地持久化 Action 和 Task 状态；active
wait subscription 只是唤醒通道，永远不是 Proposal 落库或状态转换的前置条件。

为消除“事件先到、Wait 后建”的游标竞态，Platform 为每个 Task 保存唯一权威
`delivered_seq`，它只在事件已经进入持久 Tool Result 后前移。

模型 Step 的提交事务只持久化 Wait，不锁 Event Cursor，也不在该付费事务内做结算。提交
成功后，由独立、幂等、短事务 `settle_if_undelivered(loop_id)` 检查
`seq > delivered_seq`；Event Append 成功后也调用同一结算函数，Reaper 每轮再兜底扫描
active Wait：

- 已存在符合 `wake_on` 的事件时，短事务立即把自上次 `delivered_seq` 后的全部事件写入
  同一个 Tool Result、推进游标、终结 Wait 并创建下一 Step；
- 不存在时保留 active Wait；后续 Event Append 或 Reaper 再结算；
- Event Append、模型提交后的主动调用和 Reaper 使用相同锁顺序和结算函数，重复调用或
  并发执行不能产生两次唤醒；
- `40001 serialization_failure` 最多重试三次，使用 10/25/50ms full-jitter 上限；耗尽后
  保留 active Wait 并报告局部指标，由下一 Reaper Tick 继续，不重新调用模型。

`brain_wait_subscriptions.cursors` 在迁移 049 中删除。订阅不保存第二份可写水位；所有
唤醒判定、Tool Result 排空和重放都只读 `brain_task_event_cursors.delivered_seq`。

确认、拒绝、过期或明确修改 Action 时：

1. 有 active Wait 时，原子终结它，把 Action 事实写入 Tool Result，推进 delivered cursor；
2. 没有 active Wait 时，仍持久化 Action 决议和执行投递；后续 Wait 由上述“创建即检查”
   路径立即取得该事实，不允许丢弃或造孤儿 Proposal；
3. Loop 已处于 `waiting_confirmation` 且没有活跃 Step 时，数据库函数原子创建恢复 Step；
4. 确认路径同时创建 Action Execution Delivery；
5. 下一 Brain Step 可以等待真实执行结果，不能把“已确认”展示为“已执行”。

已终结的 Wait 不复用，也不允许确认后依靠定时扫描碰运气唤醒。

## 10. 普通消息与 Pending Action

普通消息不自动废弃全部 Pending Action。规则如下：

- 用户从确认卡选择“修改”或消息带 Platform 生成的 `action_id` 介入上下文时，只
  supersede 对应 Action；
- 上游对同一 Action 提出新参数时，旧 Action superseded；
- 与 Action 无关的普通消息可以在同一 Turn 继续处理，原确认卡保持 pending；
- 前端在任何 supersede 操作前明确显示“原确认将失效”；
- 大脑文字不能自行把 Action 标记 superseded。

只要当前 Turn 仍有归属于 Conversation Owner 的 `pending` Action，`submit_answer` 就不
终结 Loop。Runtime 为该 Tool Call 返回普通 Tool Result：

```json
{
  "status": "rejected",
  "reason": "pending_action_requires_resolution",
  "required_next_action": "await_agent_events"
}
```

该拒绝不计入 `protocol_retry_count`，也不走 `ProtocolViolation`。大脑必须等待确认、拒绝、
过期或明确修改后的权威事件；Action 解决后才允许提交最终答案。

若任一强制收束条件（任务数、Step 数或活动 Deadline）已经成立且仍有 Pending Action，
Runtime 不调用模型、不强制 `submit_answer`，而是把 Loop 转为 `waiting_confirmation` 并
暂停 Brain 活动时钟。Task 自己的有效时钟继续运行，事件继续持久化。Action 决议后恢复；
此时强制收束仍成立但 Pending 已解除，Runtime 再强制 `submit_answer`，自然完成 Turn。
显式停止/取消整个 Turn 仍可终止并过期 Action。

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
deadline_expired
attachment_unsupported
upstream_unavailable
```

错误返回不包含 Token、Cookie、数据库正文、文件路径、对象键或 Provider 敏感信息。

### 11.1 错误信封与 HTTP 状态

所有合同错误使用同一个严格信封：

```json
{
  "contract_version": "orbbec-http-task/v1",
  "error": {
    "code": "scope_denied",
    "message": "task scope is not authorized",
    "details": {}
  }
}
```

`message` 是稳定、安全、非敏感的展示文本；客户端不得依赖它分支。`details` 默认空对象，
只允许以下例外字段：

- `capability_changed`：`current_capability_version`、`must_refresh_capabilities=true`；
- 序号冲突：对应的 `expected_sequence`；
- 不支持的合同版本：`supported_contract_versions`。

HTTP 状态固定为：

| 状态 | 错误 |
|---:|---|
| 401 | Token 缺失、签名错误、过期、错误 Audience、未知或已退休 `kid` |
| 403 | `scope_denied`、Token 与路径 Task 绑定不一致 |
| 404 | `task_not_found` |
| 409 | 所有幂等/序号/Action 冲突、`capability_changed`、`deadline_expired`、`task_terminal` |
| 422 | 请求 JSON/字段违反 v1 Schema 或 `contract_version_unsupported` |
| 503 | `upstream_unavailable` |

认证类响应不能说明具体是签名、Audience、Task、`kid` 还是人员状态错误；精确原因只进入
受控审计。

## 12. 合同测试

唯一测试源位于 AI-Agent-Platform 仓库 `contracts/http_task_v1/`，包含 JSON Schema、
固定请求/响应样例和只依赖 HTTP 的 pytest 黑盒驱动器。FAE 与行政 CI 不复制测试：它们按
`CONTRACT_TEST_COMMIT` 检出 Platform 仓库的固定 Commit，启动本仓服务后运行同一入口；
Release Manifest 同时记录 Commit 和测试目录 SHA-256。Commit 不存在、Hash 不匹配或测试
未运行均视为合同未通过。

测试驱动器要求 Python `>=3.11`。Platform、FAE 和行政 CI 必须显式选择 3.11 或更高版本，
不得使用操作系统默认 Python；本仓本地命令统一使用 `backend/.venv/bin/python -m pytest`。

同一套测试至少覆盖：

- 创建快速返回和幂等冲突；
- Capability Version 双向校验与过期版本拒绝；
- `deadline_at` 到期后停止执行、产生 `timeout`，且不发生迟到业务写入；
- `wait_seconds=0` 立即返回，无事件时不阻塞；
- 规范事件词表和所有旧事件映射；
- `timeout` 终态，`timed_out` 被拒绝；
- `input_required` 与普通 `message` 的状态差异；
- Action propose、Digest 重算、execute 不带参数；
- 重复 execute 不重复业务写入；
- 错误 Scope、Audience、Task 绑定和 Token 轮换；
- 断线后从 `after` 精确重放，无重复、无缺口；
- 事件序号缺口只隔离单 Task/Agent，其他 Agent 的派发、取消和心跳继续；
- 事件先于首次 Wait 到达时，Wait 创建立即结算而不是把事件当成已读；
- Pending Action 存在时 `submit_answer` 被拒绝且确认卡仍有效；
- Pending Action 下重复 `submit_answer` 不消耗协议重试；forced + pending 转等待，Action
  决议后再强制收束成功；
- 终态不可逆。

只有合同测试通过并记录具体 Commit 后，Platform 才能为该 Agent 打开
`brain_delegation`。

### 12.1 两个测试 Profile

唯一测试源仍位于 `contracts/http_task_v1/`，但分为两个明确 Profile：

1. `upstream_http`：FAE、行政和 Platform 的 HTTP Reference Adapter 都运行；只通过本节
   正式 HTTP 端点检查上游 Facade 合同。
2. `platform_integration`：只在 Agent Platform 运行，覆盖事件缺口后的 Task/Agent 隔离、
   Event-before-Wait、Pending Action、协议重试预算和 forced + pending。这些是 Platform
   Brain Runtime 语义，不要求 FAE/行政暴露测试控制端点。

FAE/行政 CI 运行 `upstream_http`；Platform 同时运行两个 Profile。两者共享同一份 Schema、
事件词表、固定样例和 Digest 实现，不能复制或重写合同。

事件缺口的责任边界固定为：

- FAE/行政内部生产者若制造缺口，Facade 在本服务内终结该任务并用下一个连续序号输出
  `failed`，`payload.reason_code="protocol_violation"`；不得把缺口页面暴露给 Platform。
- `platform_integration` 使用 Reference Fault Adapter 注入原始非法 HTTP 页面，验证
  Platform 只隔离该 Task/Agent，其他 Agent 的派发、取消和心跳继续。

### 12.2 测试执行器与依赖注入边界

`upstream_http` 使用固定 Fixture Scenario，但生产代码不得识别 Fixture 名称。下游 CI
必须通过依赖注入启动真实 Task Facade、真实持久 Store 和一个仅测试进程存在的
`ContractExecutionBackend`。只有该测试 Backend 解释固定 `turn_ref`/objective 并产生确定性
内部事件；生产 Loop、编排器和领域 Gateway 不包含 `contract:*` 分支。

固定场景至少包括：创建幂等、异步事件分页、后续消息、异步取消、运行中超时、
`input_required`、普通 `message`、全部旧事件规范化、Action 提案/执行/冲突/过期、内部事件
缺口、迟到结果和隔离后的健康任务。

`upstream_http` 必须以稳定 Case ID 实现以下最小矩阵，不能按上游仓库删减：

| 类别 | 必须覆盖的 Case |
|---|---|
| Schema | 未知字段、错误类型、布尔冒充整数、非法 UUID/时间、错误合同版本均在持久化前拒绝 |
| 认证 | 缺 Token、过期 Token、错误 Audience、退休 `kid` 返回 401；错误 Scope、Task 绑定返回 403 |
| 创建 | 首次 202；同键同 Payload 返回同任务且 `duplicate=true`；同键异 Payload 返回 409 |
| 能力与截止 | 过期 Capability、已过 Deadline 均拒绝；修正后同 Task/幂等键创建为 `duplicate=false` |
| 消息 | 序号连续、同键重放、同序冲突、终态后拒绝；queued/consumed 映射为规范 `work_update` |
| 事件 | 非阻塞分页、精确重放、内部事件规范化、连续序号、终态后稳定且无迟到结果 |
| 取消 | queued 与 running 两条路径、重复取消同一 `cancel_request_id`、终态不可逆 |
| Action | Proposal/Digest、Execute 不带参数、重复 Execute 同 `execution_id` 且业务效果计数仍为 1 |

固定 Case ID、请求、预期 HTTP 状态和错误码进入同一份测试清单；测试选择只能按 Profile，
不能通过环境变量逐项跳过安全或生命周期 Case。

执行器不得假定创建后立即有事件。它只循环调用有限 JSON 页：

- 每次都发送 `wait_seconds=0`；
- 只有完整校验一页后才推进 `after`；
- 在 Case Deadline 内等待目标事实；
- 达到终态后至少执行三次非阻塞读取，且观察窗口不短于 500ms；
- 终态后任何新事件、状态倒退、第二个 Action 执行事实或迟到业务结果均失败。

Fixture 不调用真实模型或外部业务系统，因此普通 Case Deadline 固定为 15 秒，异步取消与
硬截止 Case 固定为 30 秒；单个 `upstream_http` Profile 的进程级 Deadline 为 180 秒。
启动目标服务的部署等待由 CI 在 Runner 外单独控制。Runner 和 pytest 配置不得再设置
905 秒或其他覆盖全部 Case 的长超时。

Action Fixture 的规范 `result` 事件必须带稳定 `execution_id` 和
`fixture_business_effect_count=1`。这是测试 Backend 的可观测证明，不是生产业务 Payload 的
通用必填字段。重复 Execute 后仍只能存在一个该执行事实。

### 12.3 Task Token Broker

单个静态 Token 无法覆盖一个执行器创建的多个动态 `platform_task_id`。下游 CI 必须提供
本地 Token Broker 可执行文件；它不是 HTTP 服务，也不进入生产镜像。Runner 以无 Shell 的
子进程调用绝对路径，向 stdin 写一条 JSON、从 stdout 读取一条 JSON：

```json
{
  "profile": "valid",
  "agent_id": "ai-fae-agent",
  "platform_task_id": "uuid",
  "capability_version": 2,
  "authorized_scopes": ["fae.answer"],
  "task_deadline_at": "2026-08-27T10:15:00Z",
  "action_execution_deadline_at": null
}
```

输出严格为：

```json
{"token": "opaque"}
```

`profile` 只允许：

```text
valid | expired | wrong_scope | wrong_audience | wrong_task_binding | retired_kid
```

Broker 使用固定测试人员，但为每个请求的动态 Task ID 单独签名。Runner 不读取密钥、不解析或
记录 Token，也不把 Token 放入 pytest 参数、错误文本或报告。`retired_kid` 由测试 Keyring
签发一个已从服务验证集合移除的旧 `kid`；`expired` 使用当前 `kid` 但把 `expires_at` 固定为
当前时间之前；`valid` 使用当前 `kid`，从而分别验证时效与真实轮换行为。

错误 Scope、Audience、Task Binding、退休 Key、过期 Deadline 和过期 Capability 的请求
被拒绝后，Runner 使用同一个 `platform_task_id` 与幂等键、修正后的有效凭证/Payload 再次
创建，并要求 `duplicate=false`，以证明拒绝发生在任务和幂等键持久化之前。

### 12.4 Schema、样例与 Manifest

`contracts/http_task_v1/` 必须提交：

- 本文全部请求、响应、事件与错误信封的 JSON Schema；
- 每个 Schema 至少一个固定有效样例；
- 认证、冲突、终态与非法事件的固定失败样例；
- `upstream_http` Runner 和 `platform_integration` 共用的模型/Digest 模块。

Release Manifest 不信任工作树。生成器必须从传入的 Git Commit 读取
`contracts/http_task_v1/`（例如 `git archive`），按相对路径字典序计算路径敏感 Digest；
Commit 不存在或工作树合同目录与该 Commit 不一致时拒绝生成。Manifest 测试不得因文件缺失
而跳过，并必须从归档独立复算 Hash。

生成物排除规则只允许精确目录/后缀：`__pycache__/`、`.pytest_cache/`、`*.pyc`、
`*.pyo`、`*.egg-info/`、根目录 `build/` 与 `dist/`。其他名为 `build` 的源码目录不得被
通配排除。
