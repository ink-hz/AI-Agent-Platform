# Agent 大脑业务确认与独立专业 Agent 接入设计

**日期：** 2026-08-27

**状态：** 待书面评审

**基线：** AI-Agent-Platform `origin/master@40d2c9d`；AI-ADMIN-Agent `e95e274`；AI-FAE-Agent `origin/master@7302821`

**范围：** Agent Brain V2 的能力版本修复、任务状态扩展、用户业务确认、VOC/FAE/行政接入、统一身份和协作室增量；统一附件为并行项目

## 1. 文档定位

本设计不重建 Agent Brain。以下能力已经由迁移 `041`、`045`、`046` 和当前运行时提供：

- 持久化 Brain Loop、Step、Tool Call、Agent Task 和授权快照；
- `list_agents`、`delegate_task`、`await_agent_events`、`send_agent_message`、
  `stop_agent_task`、`request_user_input`、`submit_answer` 七个工具；
- 非阻塞派发、独立事件等待、任务事件 `(task_id, seq)` 幂等；
- 子会话、后续消息、取消投递、等待订阅、用户介入；
- Worker 租约、崩溃恢复和一个 Loop 一个 active Step；
- 并行任务 4、单轮任务 8、单任务默认 300 秒、Turn 活动预算 900 秒；
- 现有多 Agent 协作室的团队、协作记录和交付成果视图。

本设计是下列既有设计的增量，并在冲突处取代其旧假设：

- `2026-08-24-cloud-agent-brain-durable-loop-design.md`
- `2026-08-25-agent-brain-live-multi-agent-workroom-design.md`
- `2026-08-24-agent-platform-unified-agent-entry-design.md`

本次处理五个产品缺口，其中先修复一条阻断所有真实委派的 P0：

1. 能力版本在派发处被硬编码为 `1`；
2. 任务缺少 `dispatched`、`waiting_input`、`waiting_confirmation`；
3. 不可逆业务操作没有与大脑物理分离的用户确认机制；
4. FAE、VOC、行政尚不能同时作为专业工作区和 Brain 可调度 Agent；
5. 图片、文档和 Agent 输出仍没有成为所有企业 Agent 可直接复用的 Platform 底座。

此外，Platform 还没有把企业员工直接打开 FAE 工作区的钉钉身份贯通写成统一合同。
本设计同时补齐浏览器工作区 SSO 与服务端任务身份，两条链最终都映射到同一个
`internal_user_id`。

## 2. 核心架构边界

### 2.1 代码与系统所有权

Platform、FAE、行政、VOC、MetaBot 及其部署代码全部由 Orbbec 团队掌控，团队有权修改、
测试、发布和回滚任一组成部分。本设计中的“上游”“下游”“独立 Agent”只表示运行时
职责、数据方向和故障域，不表示第三方黑盒、代码不可修改或控制权不足。

因此跨服务协议优先在所有相关仓库共同落地，而不是让 Platform 为历史差异长期维护猜测式
兼容层。服务边界仍然保留，是为了独立部署、最小权限、故障隔离和可恢复性。

### 2.2 职责边界

```text
大脑拥有任务和调度权
用户拥有不可逆业务操作的授权权
专业 Agent 拥有领域执行权
Platform 拥有身份、授权记录、状态和审计事实
```

由此导出以下硬约束：

- 大脑可以选择 Agent、并发派发、追问、停止和综合结果；
- 大脑不能确认、拒绝或伪造用户对业务写操作的授权；
- 确认卡片只能由 Platform 持久化 Action 记录渲染；
- 专业 Agent 不能信任 Prompt、浏览器字段或模型文字中的“用户已同意”；
- 专业 Agent 只执行短时凭证中明确授权的能力 Scope；
- 写操作的最终参数必须与用户确认时看到的参数完全一致；
- 无任何静默换 Agent、换模型、降级到一次性聊天或模拟进度的路径；
- 钉钉只作为企业身份源；下游不复制钉钉登录逻辑，也不保存钉钉原始身份标识。

## 3. 已核实的现状

### 3.1 P0：真实专业 Agent 当前无法被 V2 Loop 委派

`backend/app/agent_brain/loop_runtime.py` 当前在创建任务前调用：

```python
authorize_task(owner_id, agent_id, 1)
```

HR 和五个 Marketing Agent 的 Catalog `capability_version` 均为 `2`。因此 V2
持久 Loop 对真实 Agent 的委派恒定得到 `capability_changed`，不会创建
`agent_task`。测试 Fake Registry 又反向断言版本必须为 `1`，把缺陷固化进了测试。

该问题必须作为阶段 0 单独修复和发布，不进入迁移 `049`。

### 3.2 行政已有 Jobs，不能重复建队列

当前 AI-ADMIN-Agent `origin/master@e95e274` 已有：

- `POST /jobs`，返回 `202`；
- `GET /jobs/{job_id}`；
- `GET /jobs/{job_id}/events?after=`，支持事件游标；
- PostgreSQL 与内存 Job Store；
- `idempotency_key`；
- Job Worker 和带租约的结果投递。

因此行政接入是复用并扩展现有 Jobs，不是从零创建任务队列。它仍缺少 Platform
内部短时身份、持续任务消息、任务取消、严格 Scope 和后续 Action 协议。

### 3.3 FAE 仍是一次性流式请求和内存 Session

当前 FAE `origin/master@7302821` 主要提供：

- `POST /chat` SSE；
- `/history`、`/feedback`、Review 和附件能力；
- 内存 Session Store；
- capability orchestration 的领域 Loop。

它没有持久任务资源、事件游标重放、任务级幂等、后续消息投递和任务取消。FAE 必须新增
真正的内部持久任务面，不能由 Platform 长挂 `/chat` 包装。

### 3.4 Catalog 当前禁止双重交互模式

`agent_catalog/models.py` 当前禁止 `external_workspace` 与
`brain_delegation` 同时存在，并要求外部工作区 Agent 不得声明 Adapter。
`CALLABLE_AGENT_IDS` 又严格固定为六个 MetaBot Agent。迁移 `048` 的授权边界已经包含
九个规范 Agent，因此本次只调整 Catalog/Brain 投影，不扩大授权目标集合。

## 4. 阶段 0：能力版本 P0 修复

### 4.1 协议选择

`list_agents` 继续返回 `capability_version`。`delegate_task` 输入新增必填
`capability_version`，由模型回传它实际看到的版本：

```json
{
  "agent_id": "hr-bot",
  "capability_version": 2,
  "objective": "...",
  "context_excerpt": [],
  "constraints": [],
  "attachment_refs": [],
  "expected_output": "...",
  "public_reason": "..."
}
```

运行时把该值传给 `authorize_task`。不允许运行时悄悄改成当前版本，因为那会失去
“模型基于旧能力计划、派发时能力已经变化”的并发保护。

### 4.2 语义

- 版本一致：固化授权快照并创建任务；
- 版本不一致：不创建任务，返回 `capability_changed`，Tool Result 同时携带当前版本和
  `must_call_list_agents=true`；
- 大脑必须重新 `list_agents` 后再派发；
- 同一 Loop 对同一 Agent 连续两次版本不一致后，Runtime 返回
  `capability_version_unstable` 并停止该派发意图，不再让模型无界重试消耗 Step；
- 已创建任务不因后续 Catalog 升级被杀掉；
- 真实授权变为 deny 时，仍以 `authorization_changed` 终止相关 Loop。

### 4.3 发布边界

该修复包含 Tool Schema、系统提示词和 Prompt Hash 更新，会使首次 Prompt Cache
失效。它必须独立提交、独立发布，并用真实 Catalog + Runtime Registry 证明版本 `2`
能够创建 HR 任务；不得只用 Reference Adapter 验证。

## 5. 数据模型：迁移 049 与 050

迁移编号 `049`、`050` 已核对未被本地或远端引用占用。实施前仍需再次确认主线。为降低
回滚和权限审计风险，拆分为：

- `049`：Task/Loop/Turn 状态、任务有效时钟、事件词表、等待订阅和唤醒约束；
- `050`：Action 表、Action Execution Delivery、确认/拒绝/过期/取代函数和列级 Grant。

### 5.1 Agent Task 状态

`agent_tasks.status` 扩展为：

```text
queued
  -> dispatched
  -> running
       -> waiting_input
       -> waiting_confirmation
       -> completed | failed | cancelled | timed_out | unavailable
```

终态不可逆。每次合法状态变化必须伴随真实 `agent_task_event`。重复 `(task_id, seq)`
只能幂等重放同一内容，不得改变既有事件。

任务进入 `waiting_input` 或 `waiting_confirmation` 时暂停自己的有效执行时钟；恢复时按
已消耗时间重算 `effective_deadline_at`。

迁移 `049` 同时增加受约束的 `terminal_reason_code`。事件缺口等上游协议错误把 Task
终结为 `status=failed, terminal_reason_code=protocol_violation`；不能把
`protocol_violation` 混进 Status 枚举。

`dispatched` 是真实持久状态，不是 UI 标签。迁移 `049` 必须用
`mark_adapter_delivery_dispatched_v49` 替换 v45：初始 Delivery 从 `leased` 转
`dispatched` 时，Task 只从 `queued` 转 `dispatched` 并写 `dispatched_at`；只有收到首条
真实 `work_update` 或终态事件时才写 `running/started_at`。Repository 不得继续调用会把
Task 直接写成 `running` 的 v45 函数。

### 5.2 不新增 Task Group

一次并行派发批次由同一个 `step_id` 唯一标识。同一 Step 内 Tool Call 已有
`(step_id, tool_index)` 稳定顺序，`agent_tasks.brain_tool_call_id` 唯一。前端可以
把同一 `step_id` 投影为“本批任务”，数据库不新增 `task_group_id` 或任务组表。

### 5.3 Action 表

迁移 `050` 新增 `platform_brain.agent_task_actions`，至少包含：

```text
action_id uuid primary key
task_id uuid not null
action_seq integer not null
action_kind text not null
summary_ciphertext bytea not null
summary_key_version integer not null
summary_sha256 bytea not null check octet_length = 32
impact_ciphertext bytea not null
impact_key_version integer not null
impact_sha256 bytea not null check octet_length = 32
parameters_ciphertext bytea not null
parameters_key_version integer not null
action_digest bytea not null check octet_length = 32
status pending | confirmed | rejected | expired | superseded
expires_at timestamptz not null
confirmed_by_internal_user_id uuid
confirmed_at timestamptz
execution_timeout_seconds integer not null
execution_status not_started | queued | running | completed | failed
execution_deadline_at timestamptz
created_at / updated_at / terminal_at
unique (task_id, action_seq)
index (task_id, action_digest)
```

`action_id` 使用稳定派生：`uuid5(task_id, "action:" + action_seq)`。同一 propose
重放不得生成第二条 Action。Summary、Impact 和 Parameters 与既有 Brain 内容纪律一致，
只允许加密正文 + Key Version + SHA-256，不保留明文二选一。

Digest 原文严格采用 HTTP Task Contract v1 的四字段对象：
`platform_task_id + action_seq + action_kind + parameters`。Summary/Impact 不参与 Digest；
线上为 64 字符 lowercase hex，入库解码为 `bytea(32)`。Platform 与上游共用合同测试里的
Canonical JSON Fixture，禁止各仓库自行解释“对整个 Payload 做 Hash”。

Action 不冗余保存 `agent_id`、`conversation_id` 或 `turn_id`。权威绑定路径固定为：

```text
agent_task_actions.task_id
  -> agent_tasks.agent_id / brain_tool_call_id
  -> brain_tool_calls.step_id
  -> brain_steps.loop_id
  -> brain_loops.turn_id
  -> conversation_turns.conversation_id / owner
```

确认、读取和审计都沿该路径校验；不得按 Action 表中的缓存身份绕开权威 Join。

### 5.4 Action 状态不变量

- 只有 `pending` 可以确认、拒绝、过期或被取代；
- `confirmed`、`rejected`、`expired`、`superseded` 均不可反向变回 pending；
- 同一任务的新参数 proposal 必须把旧 pending Action 转为 `superseded`；
- Loop 在 Action 尚 pending 时终止，所有 pending Action 转 `expired` 并请求停止任务；
- 正常 `submit_answer` 在存在 pending Action 时不得终止 Loop；Runtime 返回
  `pending_action_requires_resolution` 并要求先等待 Action 决议。只有显式停止、取消、
  不可恢复失败等非正常终止路径才执行上一条清理；
- 重复确认返回同一执行记录，不产生第二次上游写操作；
- Action 参数和确认身份全部绑定 Conversation Owner，不允许首版代确认。

### 5.5 权限

Web/API 角色不得获得 Action 表级 Update。确认和拒绝只能调用 `SECURITY DEFINER`
函数。函数按迁移 `046` 的模式校验：

- 精确数据库和 `session_user`；
- Conversation/Turn/Task/Action 归属；
- 调用者是 Conversation Owner；
- Action 仍 pending 且未过期；
- `action_digest` 与请求匹配；
- Loop 未进入终态；
- Action Capability 和执行 Scope 有效。

迁移必须复核 `041` 的按列 Grant；新增列不能因为旧表 Grant 被遗漏或意外放大权限。

## 6. Loop 状态和预算推导

### 6.1 暂停条件

只有同时满足以下条件，Loop 才进入 `waiting_confirmation` 并暂停活动预算：

1. 没有 queued、leased、requesting_model 或其他可执行 Step；
2. 没有仍可自行产出事件的 dispatched/running Task；
3. 所有非终态 Task 都处于 `waiting_input` 或 `waiting_confirmation`；
4. 已写入明确的介入到期时间，Reaper 能最终处理。

如果还有任何可运行任务或可执行 Step，Loop 保持 `running`/`waiting_agents`，活动预算
继续计时。禁止冻结活动预算后继续调用模型。

### 6.2 介入到期字段

迁移 `049` 新增含义明确的 `intervention_expires_at`。`waiting_user_expires_at` 保留旧
路径兼容；新确认路径不继续扩展含义不准确的旧字段。后续迁移可以在所有等待路径统一后
移除旧列，本次不做破坏性删除。

### 6.3 Conversation 单活跃 Turn

`waiting_confirmation` 仍计入 `one_active_conversation_turn`。

- 用户点击确认或拒绝：恢复原 Turn；
- 用户发送普通文本：作为原 Turn 的用户介入；
- 与 Action 无关的普通文本不会自动废弃 pending Action；
- 只有用户从确认卡明确选择“修改”、消息携带 Platform 绑定的 `action_id` 介入上下文，
  或上游对同一 Action 提出新参数时，才 supersede 对应 Action；
- 前端执行 supersede 前必须明确提示“原确认将失效”；
- 用户需要完全独立的工作时，新建 Conversation，或先停止当前 Turn。

因此不允许同一 Conversation 出现两个非终态 Turn，也不会让确认卡期间的主输入框失效。

### 6.4 Action 超时

默认有效期为 2 小时，具体 Action Capability 可以缩短，硬上限 24 小时。超时后：

1. Action 原子转 `expired`；
2. 对应等待 Tool Result 标记为“未获用户授权”；
3. 创建下一 Brain Step；
4. 大脑使用已有结果交付部分答案，并明确未执行的操作；
5. 不使用当前 `expire_waiting_users -> fail_with_platform_summary` 的整轮失败路径。

模型响应当前只在 Loop 终止后且达到 7 天保留期时擦除；waiting_confirmation 是非终态，
不会被该 Reaper 提前擦除。Checkpoint 只允许作为性能缓存，恢复正确性必须来自持久化
Step、Tool Result、Task、Event 和 Action。

### 6.5 确认后的独立执行窗口

用户确认成功后，Platform 原子创建 Action Execution Delivery，并给它完整的
`execution_timeout_seconds`。该窗口不取 Brain 剩余活动预算的最小值。

- 接受确认后绝不能因为 Brain 只剩几秒而静默丢弃；
- Action 执行结果必须持久化并直接投影给用户；
- 大脑仍有预算时被结果事件唤醒并综合；
- 大脑预算已经耗尽时，Platform 先显示权威执行回执，再以明确的预算结束状态关闭 Turn；
- `execution_timeout_seconds` 来自固定 Action Capability，不由模型自由填写，且不超过
  对应 Agent 的 `max_duration_seconds`。

### 6.6 确认后的 Wait 处理

Action Proposal 和任务状态转换无条件落库，active Wait 只负责唤醒，绝不是前置条件。
迁移 `049` 新增 `(loop_id, task_id)` 的 durable delivered cursor；游标只在事件已经写入
持久 Tool Result 后前移，不能在创建 Wait 时取事件表 `max(seq)`。

创建 Wait 时必须在同一 Serializable 事务内锁游标并检查已存在的合格事件。若事件已先
到达，立即结算 Tool Result、推进游标、完成 Step 并创建下一 Step，不留下 active Wait；
没有合格事件才创建订阅。Event Append 与 Wait Create 复用同一个结算函数和锁顺序。

确认、拒绝、过期或明确修改时：有 active Wait 就原子终结并唤醒；没有 active Wait 也
必须持久化决议和执行投递，后续 Wait 通过“创建即检查”取得事实。Loop 已暂停且无 active
Step 时，控制函数原子创建恢复 Step。已终结的旧 Wait 不复用；下一 Step 等待真实
`result/failed/timeout`，不能把“已确认”展示为“已执行”。

## 7. 事件与唤醒协议

`PUBLIC_EVENT_KINDS` 和 `await_agent_events.wake_on` 增加：

```text
input_required
action_required
```

必须同步修改：

- 迁移 `045` 的数组 cardinality 和允许值；
- `append_agent_task_event_v45` 的事件白名单、状态映射和终态守卫；
- `tool_protocol.py` 的 Literal 与精确集合校验；
- `collaboration_models.py` 的 `WAIT_WAKE_KINDS`；
- `collaboration_models.py` 的 `PUBLIC_EVENT_KINDS`。

唤醒继续满足：真实事件驱动、每个 Loop 最多一个 active wait、游标单调、重复事件幂等、
终态事件不再唤醒。

迁移 `049` 新增 `platform_brain.brain_task_event_cursors`（或等价命名），唯一键为
`(loop_id, task_id)`，保存 `delivered_seq`。它是“已交给模型”的权威水位，不是“已经
写库”的水位。现有 `loop_repository.py` 以 `max(seq)` 初始化 Wait Cursor 的逻辑必须
删除，并增加“事件先于首次 await 到达”的数据库与 Repository 集成测试。

所有 HTTP Agent 的在线词表、旧事件映射和 Payload 以
`2026-08-27-http-task-contract-v1.md` 为唯一权威：

- 终态事件统一为 `timeout`，禁止 HTTP Agent 发送 `timed_out`；
- `accepted` 由创建回执表达，不写 Task Event；
- `started/progress` 在各 Agent 的内部 Facade 规范化为 `work_update`；
- `sources` 规范化为 `artifact`；
- 阻塞型旧 `question` 规范化为 `input_required`，非阻塞问题为 `message`；
- Platform Adapter 不猜测未知事件，遇到未知 Kind 明确报 `protocol_violation`。

事件页在入库前必须验证从请求 `after + 1` 开始连续。未知 Kind、缺口、乱序或同序冲突
都是 Task/Agent 局部协议错误：调用 `fail_agent_task_protocol_v49`（名称可保持版本后缀但
语义必须一致）原子写入 Task 的 `failed/protocol_violation` 控制面终态、终结其
Session/Delivery，并更新该 Agent 的持久健康投影；
不能让 PostgreSQL `check_violation` 逃逸到整个 Worker Tick。

协议缺口不能伪造成一个“来自上游的连续 failed 事件”。有 active Wait 时，控制函数直接
用 Platform-origin Tool Result 唤醒；没有 active Wait 时，后续 Wait 创建同时检查 Task
控制面终态并立即返回该失败事实。

Worker Tick 的 Brain Step、Adapter、Reaper 三个阶段分别捕获异常并分别写心跳。一个
Adapter/Task 的协议错误不能跳过其他 Adapter、取消处理或 Reaper，也不能把三个心跳
一起标记 degraded；只有数据库整体不可用等共享基础设施故障才允许升级为全局故障。

## 8. HTTP Adapter 标准合同

FAE 和行政的内部任务面实现同一
`2026-08-27-http-task-contract-v1.md`。所有代码都由 Orbbec 控制，因此规范化在各仓库
的内部 Task Facade 完成；Platform Adapter 只处理同一协议，不维护长期猜测式映射。
VOC 可在 Platform 进程内实现同一 Adapter 接口。

### 8.1 必需能力

```text
POST   /internal/platform/v1/tasks
POST   /internal/platform/v1/tasks/{task_id}/messages
GET    /internal/platform/v1/tasks/{task_id}
GET    /internal/platform/v1/tasks/{task_id}/events?after={seq}&limit={n}&wait_seconds=0
POST   /internal/platform/v1/tasks/{task_id}/cancel
GET    /internal/platform/v1/capabilities
GET    /internal/platform/v1/health
```

Action 能力是可选接口，只对声明 `supports_actions=true` 的 Agent 开放：

```text
POST /internal/platform/v1/tasks/{task_id}/actions/{action_id}/execute
```

首批 FAE 和行政只读 Adapter 不实现 Action 接口。VOC 是第一条 Action 验证链路；行政
写操作在其后独立升级能力版本。

Action Proposal 通过规范 `action_required` 事件进入 Platform，Payload 包含稳定
`action_id/action_seq`、Action Kind、加密前 Summary/Impact、Canonical Parameters、
Digest、Expiry 和固定执行窗口。上游必须先持久化参数和 Digest，再发事件。

Action Execute 请求只包含：

```text
action_id
action_digest
idempotency_key
```

禁止 Platform 回传 Parameters；上游执行时只读取 propose 阶段持久化的唯一参数副本。

### 8.2 快速创建与租约

`POST /tasks` 必须在持久化任务和幂等记录后快速返回 `202`：

```json
{
  "downstream_task_id": "opaque",
  "status": "queued",
  "next_event_seq": 1,
  "duplicate": false
}
```

禁止用 300/600 秒长驻 HTTP 请求承载任务。Platform 的 Adapter Delivery 租约默认只有
45 秒，只覆盖“创建上游任务并保存映射”。任务结果由事件游标同步，不能占用派发租约。

Platform Adapter 读取事件必须传 `wait_seconds=0`，并消费有限 JSON 页面。FAE/行政可以
为其他消费者保留 SSE 或长轮询，但 Brain Worker Tick 禁止进入 30/60 秒阻塞等待，否则
会同时卡住全体 Agent 的派发、取消和心跳。

### 8.3 幂等和事件

- 创建任务按 Platform `agent_task_id + idempotency_key` 幂等，并携带当前
  `capability_version`；上游 Capability Facade 必须对称校验，版本不一致时返回当前版本
  且不创建任务；
- 后续消息按 `task_id + message_seq + idempotency_key` 幂等；
- 取消重复调用返回相同结果；
- 事件 `seq` 从 1 开始、严格单调、支持 `after` 重放；
- 终态不可逆；
- 相同幂等键配不同 payload 返回 `idempotency_conflict`；
- 上游任务 ID 只作为加密映射，不能反向决定 Platform Task 状态。

`deadline_at` 是上游硬截止，不是提示。上游到点后必须停止新模型/工具/业务写操作并
恰好一次发出 `timeout`；Platform 已终结后的迟到结果不得复活 Task。Action 确认后的
独立执行截止通过签名 Task Token 约束，上游同样必须在截止后禁止业务写入。

唯一黑盒合同套件位于 Platform `contracts/http_task_v1/`。FAE/行政 CI 按固定
`CONTRACT_TEST_COMMIT` 检出并运行 HTTP Driver，不 vendor 或复制断言；Release Manifest
记录该 Commit 与目录 SHA-256。

## 9. 统一身份、Scope 与参数一致性

### 9.1 两条身份通道

Platform 是企业身份底座，统一维护：

```text
DingTalk provider identity
        -> Platform internal_user_id
        -> Agent 使用授权
        -> Browser Workspace SSO / Server Task Token
```

浏览器直接使用专业工作区和 Brain 服务端委派是两条不同通道：

- **Workspace SSO：** 用户直接打开 FAE、VOC 或行政专业页面；
- **Task Identity：** Brain/Adapter 代表已认证用户向专业 Agent 创建任务。

两条通道共享同一个 `internal_user_id`、Agent 授权决策、停用状态和审计关联，但不共享
浏览器 Cookie，也不向下游传钉钉原始 ID。

### 9.2 浏览器工作区 SSO

- HR、Marketing、VOC 位于 Platform 同一应用内，直接使用 Platform Session；
- 行政 `/office/*` 与 Platform 同源，继续使用已确认的具名 Platform Session Cookie，
  行政后端只通过 Platform 最小 Subject 接口逐请求取得可信主体；
- FAE 位于 `fae.orbbec.com.cn`，不得共享 `agent.orbbec.com.cn` Cookie。员工从专业
  Agent 目录进入 FAE 时，先访问 Platform Agent Launch 端点，领取一次性授权码，再
  跳转 FAE；FAE 通过服务端 back-channel 交换最小主体并创建自己的安全 Session；
- 一次性码必须绑定 Agent、用户、return path、state/nonce，60 秒内单次使用，数据库只
  保存哈希；
- FAE 企业 Session 保存 Platform `identity_binding_id`，敏感请求通过 back-channel
  校验仍有效，缓存不得超过 60 秒；Platform 登出、停用或移出企业后该绑定失效；
- FAE 面向外部客户的原入口和客户身份保持独立，不强制客户使用钉钉。企业员工入口与
  对外客户入口可以落在同一 FAE 应用，但身份模式必须显式区分并审计。

### 9.3 短时内部任务凭证

泛化现有 VOC Token Signer，给每个上游签发短时、audience-bound、capability-scoped
凭证。凭证至少包含：

```text
issuer
audience
internal_user_id
agent_id
agent_task_id
capability_version
authorized_scopes
task_deadline_at
action_execution_deadline_at (仅 Action Execute Token)
issued_at
expires_at
request_id
kid
```

不传 Platform Cookie、钉钉原始 ID、部门或角色。私钥权限 `0600`，禁止跟随符号链接；
支持 `kid` 和双接受轮换窗口。

### 9.4 Scope 双重强制

Platform Adapter 在发出请求前检查 Scope；上游在每个端点和业务动作处再次检查。Scope
缺失时返回稳定 `scope_denied`，不得按内部默认管理员或 Prompt 意图放行。

行政第一期只允许：

```text
service_catalog.read
shuttle.read
lodging.read
feedback.own.read
```

### 9.5 Action Digest

双方使用 HTTP Task Contract v1 冻结的 RFC 8785 JCS UTF-8 Canonical JSON 和固定跨仓
Fixture。Digest 原文只包含
`platform_task_id + action_seq + action_kind + parameters`，Summary/Impact 明确排除；
线上使用 lowercase hex，Platform 入库使用 `bytea(32)`。专业 Agent 在 propose 前持久化
的 Canonical Parameters 是唯一执行事实源；Platform 保存其加密副本用于确认卡与审计，但 execute
请求不回传 Parameters，只传 Action ID、Digest 和幂等键。上游执行前从自己的持久副本
复算 Digest。世界状态变化导致参数失效时返回业务冲突并重新 propose，不得自动换班次、
房间、客户或其他参数。

## 10. 统一 Agent 附件底座

附件是独立并行项目，完整设计见
`2026-08-27-platform-agent-attachment-substrate-design.md`。本设计只冻结与 Brain 的接口：

- Brain 只派发用户明确选择且通过所有权检查的 `attachment_refs`；
- 每个 Agent 在 Catalog 声明 MIME、大小、图片视觉、文档文本和输出附件能力；
- Platform 为 `(task_id, attachment_id, agent_id)` 签发短时 Grant；
- Agent 不获得 MinIO 凭据、Object Key 或长期 URL；
- 能力不匹配返回 `attachment_unsupported`，不得静默忽略；
- FAE 图片/文档能力开放前，附件独立轨必须完成 Task Grant、Media Gateway 和 FAE 验收；
- VOC Action 确认不依赖附件，附件项目不得阻塞 VOC 阶段。

## 11. Catalog 和九 Agent 投影

允许以下交互模式共存：

```yaml
interaction_modes: [external_workspace, brain_delegation]
workspace_url: ...
adapter_id: ...
adapter_kind: ...
```

双模式 Agent 必须同时具有 allowlisted `workspace_url` 和完整 Adapter 声明。外部工作区
仍可直接进入，Brain 也能看到并派发。`CALLABLE_AGENT_IDS` 显式扩展为九个规范 ID：

```text
hr-bot
voc
marketing-prospecting-bot
marketing-inbound-bot
marketing-voice-bot
marketing-intelligence-bot
marketing-gtm-bot
ai-admin-agent
ai-fae-agent
```

缺少 Adapter 或健康异常时 Agent 必须以 `unavailable` 出现，不能静默从 Brain 列表消失。
企业员工看到的 FAE `workspace_url` 指向 Platform Agent Launch 端点，不直接绕过
Platform 身份和 Agent 授权打开公共 FAE URL。

## 12. 时间预算

放宽两处 Pydantic 上限并删除运行时硬编码：

- `agent_catalog/models.py`：`max_duration_seconds <= 900`；
- `agent_brain/models.py`：`max_duration_seconds <= 900`；
- `loop_runtime.py`：读取授权后的能力卡时长。

配置：FAE 600 秒；其他 Agent 默认 300 秒；Turn 活动预算继续 900 秒。普通 Task 的实际
截止时间不超过创建时剩余活动预算。Action 确认后的执行使用第 6.5 节独立窗口。

`list_agents` 继续返回 Agent 典型时长和最大时长，并增加：

```text
remaining_active_seconds
remaining_task_slots
remaining_step_slots
```

委派回执增加 `assigned_timeout_seconds`、`remaining_active_seconds_after_dispatch` 和
`deadline_at`。剩余预算不足时派发前返回 `deadline_insufficient`。

用户已确认 `max_steps=24` 是本版架构硬上限。该决策不等于未经评测立即把生产默认值改为
24：首个 Release Manifest 仍为 12；Dev 必须测量 12/16/24 Step 的最坏 Token、上下文
增长、连续 Step 缓存命中和等待恢复缓存命中。达到成本、延迟和质量门槛后以受审计配置
发布到 24，不再修改 Schema 或协议；任何环境都不得超过 24。

## 13. 前端增量

不新增页面，不重做协作室。现有团队、协作记录和交付成果视图增加：

- `dispatched`、`waiting_input`、`waiting_confirmation` 状态；
- 由 Action 投影渲染的确认卡片；
- 确认、拒绝、过期、被新消息取代和执行结果；
- 确认人、确认时间和 Digest 摘要；
- 九个 Agent 的真实可用性和事件。

卡片必须携带服务端 `action_id + action_digest`。前端不从模型 Markdown 提取参数，不
允许乐观显示“已执行”。Mutation 继续使用 Platform CSRF 和同源校验。

## 14. 分阶段交付

```text
阶段 0  capability_version P0：独立修复、提交、发布、真实 HR 委派
阶段 1  HTTP Task Contract v1 冻结；迁移 049 状态、事件、等待与唤醒
阶段 2  迁移 050 Action、执行投递、权限与恢复；Reference Adapter 全状态机
阶段 3  Platform 统一身份；VOC propose / confirm / reject / expire / submit
阶段 4  FAE：企业 SSO、持久任务、事件重放、追问、取消、600 秒预算
阶段 5  行政只读：复用 Jobs、短时身份、Scope、追问、取消
阶段 6  行政写操作：在 VOC 确认机制验收后开放
阶段 7  九 Agent 并发和 12/16/24 Step 成本评测；按门槛发布 24

附件轨 A0-A6 与阶段 0-3 并行，不阻塞 VOC；A2/A3 完成后才给 FAE 打开图片/文档能力
```

FAE 和行政由各自仓库的专属会话并行实施，但必须先冻结本设计的内部协议版本。Platform
Adapter 不得在上游尚未通过合同测试时声称 Agent 可用。

Workspace SSO 底座随阶段 3 建立；VOC/行政复用现有同源身份，FAE 的跨域一次性授权码
交换随阶段 4 验收。

## 15. 验收门槛

### 15.1 P0

- 真实 Catalog 版本 `2` 的 HR Agent 能创建 Task；
- 旧版本派发返回 `capability_changed` 且不创建 Task；
- `capability_changed` 回带当前版本与重新 `list_agents` 指令，连续两次后停止该派发意图；
- Catalog 升级不杀掉既有 Task；
- 授权撤销仍明确终止 Loop。

### 15.2 Action

- 不确认、确认、拒绝、超时、Digest 变化、重复确认六条路径；
- propose 后、confirm 前、confirm 后三个崩溃点；
- 确认后即使 Brain 预算不足，操作仍进入独立执行窗口；
- 无关普通消息不误伤 pending Action；只有明确修改对应 Action 时才 supersede，且用户先
  看到失效提示；
- 非 Owner、过期、错误 Digest、终态 Loop 全部拒绝；
- 上游参数复算失败绝不执行。

### 15.3 Adapter

- 创建请求快速返回，45 秒租约内保存映射；
- Adapter 事件读取固定 `wait_seconds=0`，无事件时立即返回且不阻塞其他 Agent 心跳；
- 重试不产生第二个上游任务或业务写操作；
- 事件游标断线续传无重复、无缺口；
- 事件先于首次 Wait 到达时立即结算，不把已落库 finding/action_required 当成已读；
- 上游序号缺口只隔离单 Task/Agent，其他 Adapter 和三类 Worker 心跳继续；
- 后续消息与取消可幂等重放；
- Capability Version 由 Platform 与上游双重校验；Deadline 到点后上游发 `timeout` 且
  不执行迟到业务写；
- Scope 缺失由上游明确拒绝；
- Agent 离线以 `unavailable` 出现，不从列表消失。

### 15.4 九 Agent

系统可靠性验收允许本机 MetaBot 返回明确 `unavailable`；功能验收仍须在 Mac Worker
在线时真实跑通一个 HR、一个 Marketing，并与 FAE、VOC、行政各跑通一个任务。至少一次
真实三 Agent 并行协作。无需让九个 Agent 同时在线成功，但不允许任何 Agent 静默消失。

### 15.5 统一身份

- 同一个员工通过 Brain、VOC、行政和企业 FAE 入口产生的数据都关联同一个
  `internal_user_id`；
- 钉钉登录一次后进入上述企业工作区不再重复登录；
- Platform 注销、停用或撤销 Agent 授权后，FAE 企业 Binding 失效；
- 下游日志、响应和公开事件不出现钉钉原始 ID；
- FAE 外部客户入口不被企业钉钉身份强制覆盖。

### 15.6 统一附件并行轨

附件验收不再作为 VOC Action 的发布门槛。完整门槛由
`2026-08-27-platform-agent-attachment-substrate-design.md` 维护；本项目只验证 Brain 在
附件能力尚未开放时明确返回 `attachment_unsupported`，而不是静默丢弃。

## 16. 发布和回滚

- 阶段 0 独立发布，失败只回滚 Tool Schema/Prompt/Runtime 修复；
- 049、050 分别在 Preview 数据库做全量迁移、回滚和 Grant 审计；
- Catalog 双模式只在对应 Adapter 合同通过后逐个开启；
- FAE 公网 `/chat`、`fae.orbbec.com.cn`、原 IP 保持不变；
- 行政 `/office/*`、服务门户和既有 Jobs 保持兼容；
- 回滚前明确终止或排空新 Adapter 在飞任务；
- 已确认 Action 不允许通过回滚变成可重复执行，回滚工具必须读取执行事实并保持终态。

## 17. 配套任务书

- `2026-08-27-http-task-contract-v1.md`
- `2026-08-27-platform-agent-attachment-substrate-design.md`
- `2026-08-27-agent-brain-platform-task-brief.md`
- `2026-08-27-fae-platform-task-contract-task-brief.md`
- `2026-08-27-admin-platform-task-contract-task-brief.md`
