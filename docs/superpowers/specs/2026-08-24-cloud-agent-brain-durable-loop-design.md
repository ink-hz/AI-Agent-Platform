# 云端 Agent 大脑持久化 Loop 架构设计

**状态：** 已完成架构评审，待 TDD 实施

**日期：** 2026-08-24

**范围：** AI-Agent-Platform 的 Agent 大脑执行架构

**不在本设计内：** 修改 FAE 业务实现、迁移 MetaBot 到云端、钉钉业务工具扩展、低代码 Agent 平台

## 1. 决策摘要

Agent 大脑采用“云端持久化顶层 Agent Loop + 多 Adapter 专业 Agent 网络”。

Agent 大脑不是固定路由器，也不是本地 MetaBot 的别名。它是在云端直接运行
Opus 5.0 的模型驱动 Loop：读取持续会话与可用 Agent 能力，决定直接交付、向用户
追问、调用一个或多个专业 Agent、检查结果和补派任务，并且只能通过
`submit_answer` 完成本轮交付。

Platform 负责身份、授权、会话、持久化、任务投递、事件、附件、审计与恢复；
Brain Runtime 负责决策；专业 Agent 负责领域任务。MetaBot 只作为
`metabot_local` Legacy Adapter，Mac 离线不得影响 Agent 大脑直接回答、平台历史与
管理功能，也不得影响其他 Adapter。

本设计选择持久化、事件驱动的异步 Loop，不采用同步长请求，也不继续扩展现有
`planning -> professional -> synthesis` 固定流水线。

### 1.1 与现有统一工作区设计的关系

`2026-08-24-agent-brain-unified-workspace-design.md` 继续定义持续对话、左侧历史与
前端交互；该文档中“沿用现有执行协议”的假设被本设计取代。UI 工作区不再绑定
V1 Mission 流水线，而只消费稳定的 Conversation、Turn 与公开事件协议。

## 2. 已核实的现状

### 2.1 Platform 已有会话主数据

2026-08-24 对当前 `origin/master` 与生产数据库的核验结果如下：

- 仓库包含 `backend/control_migrations/029_agent_brain_mvp.sql`、
  `036_agent_brain_conversations.sql` 与 `038_agent_brain_summary_phase.sql`。
- 生产 `agent_platform_control` 数据库已经存在：
  - `platform_control.conversations`
  - `platform_control.conversation_messages`
  - `platform_control.conversation_turns`
  - `platform_control.conversation_events`
  - `platform_control.missions`
  - `platform_control.mission_tasks`
  - `platform_control.mission_runs`
- 同日生产快照为 3 个 Conversation、6 条 Message、3 个 Turn、3 个 Mission、
  3 个 Mission Run；现有 Turn 均已处于终态。

`agent_platform` 数据库只有 `platform_replica` 等只读复制数据；会话表位于独立的
`agent_platform_control` 数据库。不能因在 Replica 数据库中查不到这些表，就把
当前架构误判成“Platform 尚无会话模型”。

### 2.2 当前执行链路不是 Agent Loop

现有实现存在以下结构性限制：

- Brain 协议只允许 `direct` 或选择恰好一个 Agent。
- `one_mission_child_task` 唯一索引强制一个 Mission 只有一个专业任务。
- `mission_runs.phase` 被固定为 `summary/planning/professional/synthesis/direct`。
- `summary/planning/synthesis` 强制使用 `agent-brain-bot`。
- `agent-brain-bot` 在 MetaBot runtime contract 中指向本地 Mac 的 9110 端口。
- 云端 compose 没有独立模型运行时。

因此当前实际链路是：

```text
Platform 固定状态机
  -> 本地 agent-brain-bot 做 planning
  -> 最多一个本地专业 Agent
  -> 本地 agent-brain-bot 做 synthesis
```

它具备持久化 Mission 与事件，但决策拓扑由代码写死，且 Brain 推理依赖本地 Mac。

### 2.3 MetaBot SQLite 的真实定位

`metabot-dev/packages/server/src/chat/chat-store.ts` 维护本地 SQLite：

- `chat_conversations`
- `chat_participants`
- `chat_messages`
- `chat_runs`
- `chat_run_events`
- `chat_files`

其中 `chat_runs` 的状态与 `chat_run_events(run_id, seq)` 的幂等、终态守卫是可参考的
运行惯例，但该 SQLite 不是 Platform Agent 大脑会话的 system of record。

## 3. System of Record 决策

### 3.1 唯一会话主数据

Platform Conversation 的唯一 system of record 是：

```text
PostgreSQL database: agent_platform_control
schema:              platform_control
tables:              conversations / conversation_messages /
                     conversation_turns / conversation_events
```

现有表继续作为会话主数据，不迁移到 `platform_replica`，也不与 MetaBot SQLite
双写。

### 3.2 新 Loop 数据的归属

在同一个 `agent_platform_control` 数据库中新建 `platform_brain` schema，保存
Brain Loop、Step、Tool Call、Agent Task 与 Adapter Delivery。这样可以：

- 通过外键关联现有 `platform_control.conversation_turns`；
- 保留一个数据库事务内的状态推进与事件投影；
- 将 Brain Worker 的写权限与身份、授权表隔离；
- 不破坏 `platform_replica` 的只读复制语义；
- 不搬迁已上线的会话数据。

`platform_brain` 是执行域隔离，不是新的会话主库。

### 3.3 MetaBot 历史策略

- MetaBot SQLite 继续作为本地 Adapter 私有执行状态。
- 新 Platform 会话与消息不得写入 MetaBot SQLite。
- MetaBot 不得反向决定 Platform Conversation ID、Turn ID 或访问权限。
- 首版不迁移 MetaBot SQLite 历史，不做双写。
- 若未来确需在 Platform 展示旧 MetaBot 会话，只允许一次性导入只读历史归档，
  必须记录 `source_system=metabot_sqlite`、原始外部 ID、导入批次与校验摘要；归档
  不得继续对话，也不得冒充 Platform 原生 Conversation。

## 4. 目标架构

```text
Web / DingTalk
      |
      v
Agent Platform API
  identity / authorization / conversation / attachment / audit
      |
      | create Turn + Brain Loop
      v
PostgreSQL: agent_platform_control
  platform_control  <---->  platform_brain
      |                         |
      | public event projection | durable step/task state
      v                         v
SSE / UI                 platform-brain worker
                               |
                               | BrainModelAdapter
                               v
                         Opus 5.0 provider
                               |
                               | tool decisions
                               v
                         Agent Adapter Registry
                  +------------+------------+
                  |            |            |
                  v            v            v
             fae_http     remote_http   metabot_local
                  |            |            |
                  v            v            v
               FAE Agent   Future Agent  Local MetaBot Agents
```

### 4.1 Platform API

负责：

- 钉钉身份、Web Session 与 CSRF；
- Agent 使用授权；
- Conversation、Message、Turn 创建与所有权校验；
- 附件写入与任务范围授权；
- 用户停止、继续和反馈；
- SSE 读取；
- 将公开 Brain 事件投影给用户。

API 不调用模型，不决定 Agent，不执行补派，不同步等待专业 Agent。

### 4.2 Platform Brain Worker

`platform-brain` 是独立、非公网服务：

- 从 PostgreSQL 领取可运行 Loop Step；
- 通过 `BrainModelAdapter` 调用唯一配置的 Opus 5.0 后端；
- 校验工具协议；
- 事务性写入 Step 与 Tool Call；
- 创建 Agent Task；
- 在等待 Agent 时释放 Worker；
- 在整批任务 settle 或用户补充输入后恢复；
- 执行预算、超时、取消与协议失败规则；
- 通过 `submit_answer` 写入正式 Assistant Message。

它不使用 Claude Code PTY，不连接本地 `agent-brain-bot`。

### 4.3 Agent Adapter

Adapter 只负责协议转换与可靠投递，不拥有顶层决策：

- `metabot_local`：调用本地 MetaBot 下的 HR、Marketing 等 Legacy Agent；
- `fae_http`：通过独立、签名的内部 Agent API 调用 FAE；
- `remote_http`：未来云端专业 Agent 的标准 HTTP/SSE 协议；
- 后续 Adapter 必须显式注册，不允许运行时猜测或隐式降级。

### 4.4 专业 Agent

专业 Agent：

- 只执行已委派的领域目标；
- 不接管用户顶层 Conversation；
- 不验证钉钉身份；
- 不获得 Platform Cookie、角色或原始 provider identity；
- 只得到任务所需的最小上下文与附件句柄；
- 返回统一的结构化结果、证据、限制与附件。

## 5. 顶层持久化 Loop

### 5.1 与 FAE Loop 的同构关系

FAE Loop 的工具是事实检索、文档、SDK 与终稿提交；Agent 大脑 Loop 的工具是
Agent 发现、任务委派、用户追问与终稿提交。共同控制原则为：

- 模型决定下一步工具；
- 工具结果回填模型后继续推理；
- 模型自由文本不直接交付；
- 只有 `submit_answer` 能产生正常模型终稿；若强制提交也失败，只允许第 12.2 节
  定义的、明确标注失败来源的平台执行摘要终止本轮；
- 预算与失败不隐藏；
- 工具调用、结果、来源、耗时和 outcome 可审计；
- 原始 thinking 不进入公开产品面；Provider 协议要求保留的 content block 仅按
  第 13.1 与第 15 节的加密、短期、审计边界处理。

顶层 Loop 比 FAE Loop 多一个关键要求：专业 Agent 是分钟级异步工具，因此 Loop
必须可以持久化暂停和恢复，不能依赖一个持续占用的 HTTP 或模型连接。

### 5.2 Turn 与 Loop 的关系

- 一个 Conversation 是持续用户会话。
- 一个正常用户输入创建一个 Turn。
- 一个 Turn 最多创建一个 Brain Loop。
- 一个 Brain Loop 可以包含多个 Brain Step 与多个 Agent Task。
- 失败 Turn 的“重试”创建新的 Turn，并通过 nullable `retry_of_turn_id` 自外键关联
  原 Turn；不得绕过 `turn_id UNIQUE` 在原 Turn 内创建第二个 Brain Loop。
- 直接使用专业 Agent 的 Conversation 保留 `direct_agent` 模式，可以绕过 Brain
  Loop；Agent 大脑仍是默认入口。

### 5.3 Loop 状态

```text
queued
  -> running
  -> waiting_agents
  -> running
  -> waiting_user
  -> running
  -> completing
  -> completed
```

终态为：

```text
completed / failed / cancelled / interrupted
```

`budget_exhausted`、`max_duration`、`provider_failed`、`authorization_changed`
是 outcome/reason code，不额外制造互相重叠的状态。

Platform 现有 API 使用 `cancelled`；MetaBot SQLite 使用 `canceled`。V2 保持
Platform 的 `cancelled` 词汇，`metabot_local` Adapter 负责双向映射，不在公共
协议中混用两种拼写。

### 5.4 同一 Conversation 的并发 Turn

首版每个 Conversation 只允许一个非终态 Turn。数据库 partial unique index 的
条件必须覆盖：

```text
accepted / running / waiting_agents / waiting_user / completing
```

V2 migration 必须同步扩展 `platform_control.conversation_turns.status` 的 CHECK
约束，并替换当前只覆盖 `accepted/running` 的 `one_active_conversation_turn`
partial unique index；不能只在 `platform_brain.brain_loops` 中增加状态。

行为：

- 当前 Turn 为 `running` 或 `waiting_agents` 时，新建 Turn 返回稳定的 `409
  turn_in_progress`；前端保留草稿，用户可以等待或停止当前 Turn。
- 首版不做隐式排队，避免用户无法判断哪条消息仍未执行。
- 当前 Turn 为 `waiting_user` 时，用户回复通过当前 Turn 的 resume 端点写成同一
  Turn 的补充 User Message，并恢复原 Loop，不新建第二个 Turn。
- 两个 Brain Worker 不得同时推进同一 Conversation 的非终态 Turn。

### 5.5 waiting_user 的时钟语义

`max_turn_duration` 是活跃执行预算，不是从 Turn 创建开始连续流逝的 wall-clock
deadline。`running`、`waiting_agents` 与 `completing` 消耗活跃执行预算；
`waiting_user` 暂停该预算。

进入 `waiting_user` 时：

- 累计本段 `active_elapsed_ms`；
- 清空当前 `active_started_at` 与 `active_deadline_at`；
- 保存剩余执行预算；
- 设置独立的 `waiting_user_expires_at`，首版默认为 24 小时。

用户在 24 小时内回复时，运行时以“当前时间 + 剩余执行预算”重新计算
`active_deadline_at`。等待人类的时间不计入 900 秒。超过 24 小时未回复时，Loop
以 `user_input_timeout` 失败并释放 Conversation 的单活跃 Turn 约束；用户随后可
创建新 Turn。用户也可以主动停止 waiting_user Turn。

## 6. 模型工具协议

### 6.1 工具集合

首版工具为：

```text
list_agents
delegate_task
request_user_input
submit_answer
```

首版不向模型暴露 `cancel_task`。在“同批任务全部 settle 后才恢复模型”的协议下，
模型没有合法的活任务取消窗口。`agent_tasks.cancel_requested` 与 Adapter 的取消
能力继续保留，只供用户主动停止和平台安全终止使用。模型主动取消的长期协议见
第 21.1 节。

并行委派通过同一 Assistant 响应内的多个 `delegate_task` tool-use block 实现，
每个 tool-use 唯一标识一个委派意图。这样满足 Messages API 对 tool-use 与
tool-result 一一对应的约束。每个被接受的 `delegate_task` tool-use 恰好映射一个
Agent Task；被运行时限额拒绝的 tool-use 不创建 Task，但仍得到一个配对的终态
tool-result。

### 6.2 Tool Call 组合规则

- `list_agents` 必须单独调用。
- 一个 Step 可以包含一个或多个 `delegate_task`，但不能混入其他工具。
- `request_user_input` 必须单独调用。
- `submit_answer` 必须单独调用。
- 未知工具、重复 tool-call ID、混合了禁止组合的调用、参数不合法或零 tool-use，
  均为协议错误。

如果一个 Step 返回超过 `max_parallel_tasks` 个 `delegate_task`，运行时按
`tool_index` 接受前 N 个并创建 Task；其余 Tool Call 不浪费整个 Step，而是立即
得到：

```json
{
  "status": "rejected_over_parallel_limit",
  "limit": 4
}
```

模型仍在已接受的 Task 全部 settle 后一次性收到该 Step 的全部 tool-result。

模型返回纯自由文本或零 tool-use 时，运行时丢弃该自由文本并进行最多一次协议
纠正。纠正后仍无合法 tool-use，则 Loop 以 `protocol_violation` 失败，并走第
12.2 节的平台执行摘要；reason code 使用
`protocol_violation_after_retry`，不得冒充 `forced_submission_failed`。

### 6.3 public_reason

每个工具参数都必须包含非空、长度受限的 `public_reason`。它是用户可见、可审计
的行动理由，例如“需要 HR Agent 核对候选人的跨阶段能力组合”。

运行时只展示 `public_reason`，绝不从模型 thinking、自由文本或隐藏推理中提取
所谓“决策摘要”。模型返回的非工具自由文本不进入用户事件，也不作为可恢复状态
的权威内容；协议失败只记录稳定错误码与必要的加密诊断元数据。

### 6.4 list_agents

返回当前用户已获授权的 Agent，并合并：

- capability card 与 capability version；
- exclusions、输入输出与附件能力；
- Adapter kind；
- `healthy/degraded/offline/unknown` 可用性；
- 健康采样时间与 freshness；
- 典型耗时 `p50/p95`，样本不足时显式为 unknown；
- 最大任务时长；
- 是否支持取消、流式、附件和幂等。

数据来自现有 fleet/registry、capability catalog、授权与 remote health。静态能力与
实时健康必须在服务端合并，不让模型根据过期 YAML 猜测可用性。

可用性是决策输入，不是授权。`delegate_task` 在创建任务前仍要重新鉴权、校验
capability version 与当前健康；离线目标应快速返回 `unavailable`，不进入无意义
的长租约。

### 6.5 delegate_task

请求的规范形状：

```json
{
  "agent_id": "hr-bot",
  "objective": "判断候选人的能力组合与岗位匹配度",
  "context_excerpt": ["岗位要求英文、视觉技术、硬件产品经验"],
  "constraints": ["不联系候选人", "只使用获授权资料"],
  "attachment_refs": [],
  "expected_output": "判断、证据、风险和面试验证问题",
  "public_reason": "需要 HR Agent 进行专业人才判断"
}
```

Platform 补充而不信任模型提供：

- `task_id`
- `loop_id`
- `brain_tool_call_id`
- `authorization_snapshot_id`
- `capability_version`
- `adapter_kind`
- `effective_deadline_at`
- `idempotency_key`
- `trace_id`

Agent 结果统一为：

```json
{
  "status": "completed",
  "summary": "...",
  "deliverables": [],
  "evidence": [],
  "limitations": [],
  "attachment_refs": []
}
```

所有字符串、列表、附件数和总字节数都有服务端上限。Adapter 的原始响应先进入
私有事件，再由规范化层生成模型可见结果。

### 6.6 request_user_input

该工具提交用户可见问题并将 Loop 置为 `waiting_user`。后续回复作为同一 Turn 的
补充 Message 写入 append-only 会话记录。恢复时，运行时必须为原来的
`request_user_input` tool-use 生成且只生成一个配对 tool-result：

```json
{
  "status": "answered",
  "user_message_id": "uuid",
  "answer": "用户本次补充内容"
}
```

模型看到的是符合 Messages API 的 tool-result；用户时间线仍显示正常的 User
Message。重复提交通过 message ID 与 tool-call 唯一约束幂等，不能为同一个
`request_user_input` 生成两个结果。

### 6.7 submit_answer

`submit_answer` 至少包含：

- `answer_markdown`
- `outcome`: `resolved/partially_completed/safe_abstained`
- `used_task_ids`
- `attachment_refs`
- `public_reason`

服务端验证 task 与附件均属于当前 Loop、用户仍有权访问、Markdown 与总字节数
合法，然后在一个事务中写入 Assistant Message、完成 Turn/Loop 并投影 SSE 事件。

## 7. waiting_agents 与两条时间线

### 7.1 模型唤醒边界

一条 Assistant 消息中如果包含 N 个 `delegate_task` tool-use block，下一次模型
调用前必须为 N 个 block 一次性补齐 N 个 tool-result。因此同一批任务必须全部
settle 后才恢复模型：

```text
assistant: delegate_task A + delegate_task B
  -> A completed
  -> B still running
  -> B completed / failed / timeout / unavailable
user/tool: result A + result B
  -> resume model
```

超时、取消、离线和授权变化都必须形成对应的终态 tool-result；不得因为没有正常
正文而遗漏一个结果。想要模型提前纠偏，模型必须主动使用更小的委派批次。

### 7.2 用户事件时间线与模型时间线

用户可以实时看到每个 Agent 的独立状态：

```text
HR Agent 已完成
Marketing Agent 仍在运行
```

但此时模型尚未恢复，也没有“实时观察到 HR 结果”。前端执行过程必须清晰区分：

- Adapter/任务事实事件；
- Brain 已恢复并作出的后续决策。

不得用 UI 文案暗示模型在批次 settle 前已阅读部分结果。

### 7.3 唤醒机制

- Agent Task 终态写入 PostgreSQL 是权威唤醒条件。
- `LISTEN/NOTIFY` 或进程内信号只能作为低延迟提示，丢失后由数据库扫描恢复。
- 当一个 waiting Step 关联的所有 Tool Call 均有终态结果时，事务性创建下一 Step。
- Worker 重启后从相同 settle 条件恢复，不依赖内存回调。

## 8. Conversation 与附件上下文

### 8.1 首版上下文策略

首版不增加会话检索工具。Brain Context Builder 直接注入当前 Conversation 的完整
可用消息，直到配置化的硬 token/byte 上限。

超过上限时：

- 使用确定性的最近消息窗口；
- 在模型上下文插入明确标记，说明更早的 N 条消息因上限未注入；
- 在用户可见执行信息中标记 `context_truncated=true`；
- 不静默摘要、不假装拥有完整上下文；
- 不把现有 summary 当作唯一恢复来源。

后续如增加检索或摘要，必须另行设计和评测。

### 8.2 附件

首版不增加 `read_attachment` 模型工具。Platform 在进入 Brain 前提供：

- 附件 ID、文件名、MIME、大小和处理状态；
- 受限长度的已解析文本/OCR；
- Provider 支持时的受控图片内容块；
- 因类型、解析失败或上下文上限无法读取时的显式 omission marker。

Brain 只能从已提供的附件中选择 `attachment_refs` 委派给专业 Agent。下游收到的
是短时、task-scoped 句柄，不是 MinIO 凭据或可复用公网 URL。

## 9. 持久化模型与数据库不变式

### 9.1 权威事件与 checkpoint

`brain_steps`、`brain_tool_calls`、`agent_tasks` 与 `agent_task_events` 是 append-only
或受严格状态机约束的恢复真相。Brain messages 数组必须能由这些记录确定性重建。
该重建保证覆盖所有非终态 Loop 及终态后 7 天诊断窗口；Thinking 原始 block 到期
擦除后，仍保留规范化 Tool Call、Tool Result、公开消息、usage 与状态转换用于历史
审计，但不承诺重放已经终态的 Provider 原始请求。

`brain_checkpoints` 只是加速缓存：

- 可删除、可重建；
- 不得成为唯一恢复来源；
- 必须记录 `(loop_id, through_step_seq, source_hash)`；
- 与 Step 租约和 row version 校验，不能让旧 Worker 覆盖新状态。

### 9.2 platform_brain.brain_loops

核心字段：

```text
loop_id PK
conversation_id
turn_id UNIQUE FK -> platform_control.conversation_turns
status
outcome / reason_code
model_config_snapshot
max_steps / max_tasks / max_duration_seconds
step_count / task_count
active_budget_ms / active_elapsed_ms / active_started_at / active_deadline_at
waiting_user_expires_at
cancel_requested
row_version
created_at / updated_at / terminal_at
```

状态与 `terminal_at` 必须有 CHECK 约束。`turn_id` 唯一保证一个 Turn 最多一个 Loop。
`waiting_user` 必须满足 `active_started_at/active_deadline_at` 为空且
`waiting_user_expires_at` 非空；其他活跃状态必须有可计算的剩余执行预算。

### 9.3 platform_brain.brain_steps

核心字段：

```text
step_id PK
loop_id FK
step_seq
status: queued/leased/requesting_model/waiting_tool_results/completed/failed
lease_worker_id / lease_expires_at / attempt
input_prefix_hash
model_request_id
model_response_ciphertext
response_retention_until
usage / cache_usage
created_at / updated_at / terminal_at
UNIQUE(loop_id, step_seq)
```

必须建立 partial unique index，保证一个 Loop 同时只有一个 active Step。租约形状、
终态时间与 attempt 必须由 CHECK 约束，而不是只靠 Python 判断。

### 9.4 platform_brain.brain_tool_calls

核心字段：

```text
brain_tool_call_id PK
step_id FK
tool_index
provider_tool_call_id
tool_name
arguments_ciphertext
public_reason
status: accepted/waiting_result/result_ready/consumed/failed
result_ciphertext
UNIQUE(step_id, tool_index)
UNIQUE(step_id, provider_tool_call_id)
```

被接受的 `delegate_task` Tool Call 与 `agent_tasks.brain_tool_call_id` 为一对一唯一
关系；`rejected_over_parallel_limit` Tool Call 不创建 Agent Task。

### 9.5 platform_brain.agent_tasks

核心字段：

```text
task_id PK
loop_id FK
brain_tool_call_id UNIQUE FK
agent_id / adapter_kind
capability_version
authorization_snapshot_id
objective/context/constraints ciphertext
status: queued/running/completed/failed/cancelled/timed_out/unavailable
effective_deadline_at
cancel_requested
row_version
created_at / updated_at / started_at / terminal_at
```

一个 Loop 可以有多个 Agent Task；删除现有“一个 Mission 只能有一个任务”的语义，
但不修改旧表的历史约束。

### 9.6 platform_brain.agent_task_events

```text
task_id FK
seq > 0
event_type
payload_ciphertext
created_at / received_at
PRIMARY KEY(task_id, seq)
```

通过安全存储过程实现：

- 同 `(task_id, seq)` 同内容重放返回 already_applied；
- 同 seq 不同内容返回 conflict；
- 终态 Task 拒绝新的业务事件；
- 事件写入与 Task 状态推进位于同一事务；
- 外部 Adapter 不能直接 update Task 主表。

该模式沿用现有 durable directory event 与 execution relay 的“持久化 inbox、幂等
event sequence、终态守卫”惯例，不再创造第三套无关语义。

### 9.7 platform_brain.adapter_deliveries

每次投递尝试一行：

```text
delivery_id PK
task_id FK
adapter_kind
attempt > 0
status: queued/leased/dispatched/completed/failed/expired
lease_worker_id / lease_expires_at
idempotency_key
created_at / updated_at / terminal_at
UNIQUE(task_id, attempt)
UNIQUE(idempotency_key)
```

partial unique index 保证 `(task_id, adapter_kind)` 同时只有一个非终态 Delivery。
任务跨 attempt 使用相同业务 `task_id`，Adapter 必须声明幂等能力；不支持幂等的
Adapter 在已确认 dispatched 后不得自动重试。

## 10. 租约、重放与崩溃恢复

- Brain Worker 领取 Step 时使用 `FOR UPDATE SKIP LOCKED` 与有限租约。
- 只有当前 lease owner、未过期 lease、匹配 row version 才能提交 Step 结果。
- Provider 请求返回后，Tool Call 与 Step 状态必须在同一事务提交。
- Worker 在 Provider 已执行但数据库提交前崩溃时，Step 可能重新调用模型；所有
  有副作用的 Agent Task ID 必须由 `(loop_id, step_seq, tool_index)` 确定性派生或
  通过唯一键稳定映射，因此重放不得创建重复任务。
- Adapter Delivery 使用相同 task idempotency key；是否重试取决于 Adapter 的
  `supports_idempotency` 声明和投递阶段。
- Checkpoint 不参与互斥，租约与数据库唯一约束才是正确性边界。

## 11. 授权变化

### 11.1 授权快照

创建 Agent Task 时保存服务端生成的授权快照，至少包含：

- internal user ID；
- agent ID；
- 命中的 `agent_use_grants` ID 集合或全员/部门授权依据；
- 目录 generation；
- capability version；
- 计算时间；
- `effective_decision_hash`，只覆盖规范化的 user、agent、allow/deny 与有效授权
  scope，不包含目录 generation、capability version 或支持该决定的具体 grant ID。

这里使用的是 Agent 使用授权，不把管理看板的 `observation_grants` 当作调用授权。
两者可以沿用相同的“显式授权依据 + 版本快照”做法，但语义不能混用。

### 11.2 中途撤销

首版采用失败关闭：

- 每次 dispatch、任务结果回填、Loop 恢复和 `submit_answer` 前重新鉴权。
- 只有当前 effective authorization decision 从 allow 变为 deny，才将整个 Loop
  以 `authorization_changed` 显式失败，并向支持取消的 Task 发取消请求。
- 目录 generation 变化但 effective decision 仍为 allow 时，不影响 Loop。
- capability version 变化但授权仍为 allow 时，不终止在飞 Loop，也不丢弃已完成
  结果；后续新建 Task 返回 `capability_changed` tool-result，要求模型重新调用
  `list_agents` 后决定是否继续。
- 尚未回填模型的结果不得进入模型上下文。
- 已在早期 Step 回填的结果无法事后精确摘除，因此不得继续生成正常终稿；加密
  运行记录按既定保留与擦除策略处理。

不得声称可以在模型已经看过结果后“只丢弃该结果并安全继续”。

## 12. 预算、Deadline 与强制收束

首版默认配置：

```text
max_brain_steps        = 12
max_agent_tasks        = 8
max_parallel_tasks     = 4
max_turn_duration      = 900 seconds
max_waiting_user       = 86400 seconds
max_single_task        = 300 seconds
max_task_result_bytes  = 65536
max_output_tokens      = 32768
max_answer_markdown    = 65536 bytes
```

规则：

- Step 计数只在一次合法、包含 tool-use 的模型决策提交后增加；tool-result 的数据库
  回填与 Loop 唤醒不计 Step。
- 协议纠正调用单独计 `protocol_retry_count`，最多一次，但仍计 token、耗时和成本。
- `waiting_user` 暂停 900 秒活跃执行预算，并受独立 24 小时等待上限约束。
- Task 的有效 deadline 为任务上限、Agent 上限和 Turn 剩余时间三者最小值。
- 当 Turn deadline 先到时，仍运行的 Task 被标记 `timed_out`，补齐对应 tool-result，
  然后进入强制收束；不能等待“300 秒 × 多轮”自然完成。
- 剩余 Turn 时间低于 Adapter 的最小安全执行窗口时，拒绝创建新 Task，并把
  `deadline_insufficient` 作为工具结果交给模型。

### 12.1 强制 submit_answer

达到 Step、Task 或时间预算后：

1. 终止或超时未完成 Task，并补齐所有等待中的 tool-result。
2. 向模型加入明确预算通知。
3. 下一次请求只暴露 `submit_answer` schema。
4. 设置 `tool_choice={type: tool, name: submit_answer}`。
5. 要求基于已有结果说明已完成内容、缺口与失败项。

### 12.2 二级失败路径

若强制提交时 Provider 仍失败、截断或违反协议，Platform 生成确定性的执行状态
摘要，而不是伪造语义答案：

```text
【平台生成的部分执行摘要】
Agent 大脑未能生成正式终稿。
已完成任务：...
失败或超时任务：...
已保留的附件：...
建议：重试本轮或直接进入对应专业 Agent。
```

该交付必须记录：

```text
outcome=partially_completed
fallback_used=true
fallback_kind=platform_execution_summary
reason_code=forced_submission_failed
```

普通协议纠正后仍为零 tool-use 时使用相同的显式平台摘要结构，但 reason code 为
`protocol_violation_after_retry`。两者都不能交付被丢弃的模型自由文本。

这是显式、可审计的失败交付，不得标记为正常模型答案。

## 13. BrainModelAdapter

### 13.1 生产唯一后端

Adapter 代码可以支持 Anthropic Messages API 和兼容 Gateway，但每个生产 release
只能配置其中一个：

```text
provider_kind:        anthropic_messages 或 anthropic_compatible
model_id:             claude-opus-5
context_profile:      opus_1m
context_window:       1000000
thinking_type:        adaptive
thinking_display:     omitted
thinking_effort:      high
max_output_tokens:    32768
max_answer_bytes:     65536
prompt_cache_enabled: true
prompt_cache_ttl:     1h（真实 Provider 探测通过后）
```

具体 model ID、context profile、adaptive thinking、强制 tool choice、输出上限与
1 小时 cache TTL 必须在 Dev 对真实 Provider 做能力探测后冻结到 release manifest；
不能只凭环境变量名称声称已经启用。任一关键能力不支持都阻塞发布，不得在运行时
悄悄关闭 thinking 或切换 Provider。

Opus 5.0 首版明确启用 adaptive thinking，并使用 `display=omitted`。Anthropic
Messages 工具循环要求与 tool-use 同属一个 Assistant Turn 的 thinking content
block 在返回 tool-result 时完整、不修改地回传；因此 `brain_steps` 必须保存恢复
所需的原始 content block。`max_tokens` 同时覆盖 thinking、tool call 与终稿，故从
8192 提升至 32768，并将 `submit_answer.answer_markdown` 的服务端上限固定为
65536 bytes。真实网关验收必须覆盖高 effort 下的工具调用、强制
`submit_answer`、截断与答案长度。

协议依据：

- [Anthropic Thinking 文档](https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models)
- [Anthropic Prompt Caching 文档](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

两个 backend 不是运行时 failover。切换必须：

- 通过部署配置变更；
- 生成平台 owner 审计事件；
- 形成新的 release/model config version；
- 重启 Brain Worker；
- 通过真实 tool-use、强制 tool-choice、长上下文与 cache 验收。

请求失败时不得自动换 Provider、模型或 MetaBot。

### 13.2 Prompt caching

消息顺序固定为：

1. 稳定 system instruction；
2. 稳定工具 schema；
3. capability catalog/version；
4. Conversation 上下文；
5. 本轮 append-only Step/Tool Result。

将稳定前缀设置为 Provider 支持的 cache boundary。每个 Step 记录：

- input/output tokens；
- cache creation/read tokens；
- model config version；
- prefix hash；
- Provider request ID；
- duration 与 stop reason。

默认 5 分钟 cache TTL 与最长 300 秒的 `waiting_agents` 正好处于失效边界。首版
优先使用 Provider 的 1 小时 TTL；Step 2 能力探测必须验证真实 Gateway 是否透传
该档位，并把 `cache_ttl`、价格档位与探测证据冻结到 release manifest。若 Gateway
不支持 1 小时 TTL，必须在发布评审中显式接受成本，而不是假设仍会命中。

成本评测必须按完整多 Step Turn 统计，不能用一次调用成本外推。

### 13.3 重试

- 首个响应事件前的 429/5xx/连接失败可以指数退避重试。
- 已收到可提交的模型事件后不自动重试，避免重复工具调用。
- Provider 非流式返回时，在完整响应提交数据库之前不会产生外部副作用。
- 所有重试计入 token、时限和 attempt 遥测。

## 14. Agent Registry 与 Adapter Registry

现有展示 Registry、capability YAML、授权和 remote health 由服务端组合成运行时
Agent Snapshot。创建每个 Loop Step 时读取当前版本，创建 Task 时固定 capability
与 Adapter 版本。

每个可调用 Agent 至少声明：

```text
agent_id / display_name / domain
mission / capabilities / exclusions / example_tasks
accepted_inputs / output_contract
attachment/evidence/stream/cancel/idempotency support
adapter_kind / adapter_config_version
capability_version
health / sampled_at / freshness
latency_p50 / latency_p95 / sample_count
max_duration_seconds
data_classification
```

Registry 的页面展示 URL 与调用端点必须分离；`entry_url` 不能自动成为 Brain 的
调用地址。

## 15. 安全边界

- Brain 只接收 Platform 后端校验过的 internal user context。
- Provider 请求不包含钉钉原始 open ID、手机号、Cookie 或 AppSecret。
- 专业 Agent 获得最小化 `context_excerpt`，不是完整 Conversation。
- Agent Task 携带短时、audience-bound、task-bound 内部凭证。
- 附件句柄绑定 internal user、Agent、Task、操作与过期时间。
- Agent API 必须二次鉴权，不能相信浏览器传入的 user ID 或 agent ID。
- 会话、Tool 参数、结果与公开事件分别加密或白名单投影。
- Provider 协议要求回传的 thinking content block 只以 envelope encryption 保存于
  `brain_steps.model_response_ciphertext`，不投影到 SSE、前端、数据飞轮、日志或
  专业 Agent。
- Thinking block 在 Loop 终态后保留 7 天用于协议恢复审计，随后擦除原始 block；
  规范化 Tool Call、公开事件、usage 和稳定错误码按普通运行记录保留。
- Thinking block 的内部读取只允许 break-glass 诊断，必须填写事由并写审计；普通
  platform owner、reviewer 和业务用户均不可读取。
- Provider/Adapter/模型配置切换、跨用户查看、授权变更和敏感擦除均写审计。

## 16. 公开事件与用户体验

用户执行过程展示行动事实，不展示思维链：

```text
brain.started
brain.step_started
agent.task_dispatched
agent.task_accepted
agent.task_progress
agent.task_completed / failed / timed_out / unavailable
brain.batch_settled
brain.resumed
brain.user_input_requested
brain.answer_submitted
brain.failed
```

每个公开事件使用严格白名单 payload，只允许：Agent 显示名、公开任务摘要、
`public_reason`、状态、耗时、附件引用和稳定错误码。原始 Adapter payload、内部 URL、
Prompt、授权依据与密钥不得进入 SSE。

前端默认折叠执行过程，但必须能展开看到：

- 大脑分派给谁；
- 公开任务目标；
- 当前状态与耗时；
- Agent 的规范化结果；
- 大脑何时真正恢复；
- 最终使用了哪些任务结果。

## 17. 故障语义

| 故障 | 行为 |
|---|---|
| Mac/MetaBot 离线 | `metabot_local` Task 快速返回 unavailable；其他 Adapter 与 Brain 正常 |
| 专业 Agent 超时 | 生成 timed_out tool-result；批次 settle 后由 Brain 决定 |
| 一个批次部分成功 | UI 实时展示；模型等全批 settle 后统一观察 |
| Opus 5.0 不可用 | 当前 Brain Turn 显式失败；“重试”创建关联到原 Turn 的新 Turn，不在原 Turn 重建 Loop；登录、历史、管理和 Direct Agent 不受影响 |
| Brain Worker 重启 | 租约过期后从 append-only 记录重建并恢复 |
| Adapter Worker 重启 | Delivery 租约与 Adapter 幂等声明决定是否重投 |
| 重复 Agent 事件 | `(task_id, seq)` 幂等接受或冲突拒绝 |
| 用户停止 | 标记 Loop cancel_requested，取消可取消任务，完成为 cancelled |
| 有效授权从 allow 变为 deny | 整个 Loop 以 authorization_changed 失败，不继续正常终稿；单纯 generation 变化不触发 |
| 预算耗尽 | 强制 submit_answer；再次失败则显式平台执行摘要 |
| Registry/health 暂不可用 | 不使用过期信息静默派发；list_agents 标记 unknown/stale 或返回明确失败 |

代码不得在上述故障时擅自换 Agent、换 Provider、换模型或回到旧 MetaBot Brain。

## 18. 可观测性与评测

每个 Turn 至少记录：

- conversation/turn/loop/step/task/trace ID；
- 模型配置与 Adapter 版本；
- Step、Task、并行批次数；
- 每次 Agent 选择与公开理由；
- Provider token/cache/耗时；
- 连续 Step cache 命中率；
- `waiting_agents` 恢复后 cache 命中率；
- Task queue/run/settle 耗时；
- outcome、fallback 与 reason code；
- 上下文截断、附件 omission；
- 崩溃恢复与重复事件计数。

评测至少覆盖：

- 直接回答；
- 单 Agent；
- 同步多 Agent 批次；
- 两轮补派；
- 一个成功、一个超时；
- MetaBot 离线；
- Provider 中断；
- Worker 在模型返回前后崩溃；
- Tool Call 重放不重复创建 Task；
- 同 Conversation 并发请求；
- waiting_user 恢复；
- waiting_user 静置超过 900 秒后仍能正常恢复；
- 真实授权撤销导致失败；
- 目录 generation 变化但有效授权不变时 Loop 不失败；
- capability version 变化只拒绝新 Task，不终止在飞 Loop；
- 强制 submit_answer 与二级失败摘要；
- 零 tool-use 的一次纠正与 `protocol_violation_after_retry`；
- 超过并行上限时前 N 个执行、其余返回 `rejected_over_parallel_limit`；
- 长会话显式截断；
- 附件最小化委派；
- 模型不输出原始思维链。

答案质量必须由独立 Codex 或业务专家复审，不能由同一个 Opus 5.0 自评后直接当作
通过。

## 19. 重构与切换顺序

### Step 0：冻结 SoR 与迁移策略

- Conversation SoR 固定为 `agent_platform_control.platform_control`。
- MetaBot SQLite 固定为 Adapter 私有状态。
- 不双写、不迁移当前 Platform 会话。
- 核验旧 V1 Mission 全部终态。

### Step 1：新建 platform_brain V2 持久化模型

- 创建 schema、最小权限角色与迁移。
- 落实 active Step、active Turn、Task Event、Delivery lease、Tool Call 到 Task 唯一
  等数据库约束。
- 完成恢复与冲突测试。

### Step 2：BrainModelAdapter

- 固定 Opus 5.0、context profile、max tokens 和唯一 Provider。
- 验证 adaptive thinking、tool-use、强制 tool-choice、1 小时 Prompt caching、usage
  与错误语义，并将能力与 TTL 探测冻结进 release manifest。
- 配置切换进入审计。

### Step 3：最小端到端纵向切片

```text
1 Conversation
  -> 1 Turn
  -> 1 Brain Step
  -> 1 Agent Task
  -> 1 tool-result
  -> submit_answer
```

只接一个 Dev Fake/Reference Adapter，无并行、无补派。必须证明：

- API/Brain/Adapter 任一处重启后可恢复；
- 不重复创建 Task；
- SSE 可断线续传；
- 只有 submit_answer 产生最终 Message。

### Step 4：完整 durable Brain Loop

- 多 Step；
- 同批多 Agent；
- settle 后恢复；
- 补派、用户主动停止、waiting_user；
- 预算、deadline 与强制收束。

### Step 5：完整协议与过程视图

- Agent 运行时快照；
- public_reason；
- 附件最小化；
- 公开事件白名单；
- 前端 Brain 与 Agent 两条时间线。

### Step 6：MetaBot 降级为 metabot_local Adapter

- 本地 Worker 只能领取 `adapter_kind=metabot_local` 的 Agent Task。
- 删除它领取 planning、summary、synthesis 或 Brain Step 的能力。
- 验证 Mac 离线隔离。

### Step 7：接通 HR / Marketing

- 逐个固定 capability 与输出契约；
- 验证附件、用户停止、超时与幂等声明；
- 不增加代码级兜底路由。

### Step 8：独立 FAE HTTP Adapter

- FAE 保持独立对外入口；
- 增加签名内部调用协议后再注册；
- 不修改或重启 FAE，直到进入明确接入批次。

### Step 9：正式切换新 Turn

- Dev 与 preview 全量通过后，原子切换新 Brain Turn 到 V2。
- 已有 Conversation 可继续，新 Turn 使用 V2 Loop；旧 Turn 原样回放。
- 切换前要求 V1 无非终态 Mission。
- 不做运行时双写与 V1/V2 自动 failover。

### Step 10：旧主链路只读

- 删除 `agent-brain-bot` 作为 planning/summary/synthesis 主链路的运行依赖。
- V2 Brain Loop、Step 与 Task 永远不写入 `missions/mission_runs/mission_tasks`；旧
  CHECK 约束原样保留，只服务 V1 历史回放。
- V1 Mission 与 Run 表、诊断路由按保留期只读。
- MetaBot Agent 与本地历史不删除，只改变其架构地位。

## 20. 验收门槛

架构完成至少满足：

1. 关闭 Mac 后，Agent 大脑仍能直接回答并完成 `submit_answer`。
2. 关闭 Mac 后，只有 `metabot_local` 任务显示 unavailable。
3. 一个 Turn 能并行分派至少两个 Agent，并在整批 settle 后只恢复一次模型。
4. 任意 Worker 在关键事务前后被终止，恢复后不重复 Agent Task 或最终 Message。
5. 同一 Conversation 两次并发提交只有一个非终态 Turn。
6. 同 `(task_id, seq)` 重放幂等，不同内容冲突失败。
7. 达到预算后强制 submit_answer；强制失败时生成带 fallback 标签的平台摘要。
8. waiting_user 静置超过 900 秒后，24 小时窗口内仍可正常回答并完成原 Turn。
9. 真实授权撤销导致 Loop 失败；例行目录同步不误杀，capability 更新只拒绝新 Task。
10. 长会话和附件 omission 对模型与用户都显式可见。
11. UI 能区分“Agent 已完成但 Brain 尚未恢复”和“Brain 已观察结果”。
12. 生产只配置一个 Opus 5.0 Provider，不存在运行时模型/Provider failover。
13. 连续 Step 与 waiting_agents 恢复路径分别报告 cache 命中率和成本。
14. 零 tool-use 重试一次后显式失败，不向用户交付模型自由文本。
15. V2 运行时对旧 `mission_runs` 的写入次数为零。
16. FAE 容器、域名、配置、启动时间和重启次数在非 FAE 接入批次保持不变。

## 21. 明确非目标

- 不做低代码 Agent 创建、Prompt 在线编辑或用户自选模型。
- 不在首版接入钉钉文档、日历等业务工具。
- 不把 MetaBot 迁移到云端。
- 不把 FAE 嵌入 Platform 进程。
- 不做跨 Conversation 自动长期记忆。
- 不做运行时 Provider/模型/Agent 静默兜底。
- 不把 Agent 大脑原始思维链展示给用户或写入数据飞轮。
- 不在首版迁移 MetaBot SQLite 历史。

### 21.1 已知演进：模型主动取消

首版不向模型暴露 `cancel_task`。完成 durable Loop 纵向切片并验证稳定后，可以在
不修改 `agent_tasks` 主 schema 的前提下演进为：

```text
delegate_task -> 立即返回 {status: dispatched, task_id}
await_tasks    -> 对指定 Task 执行阻塞收集
cancel_task    -> 对尚未 settle 的 Task 请求取消
```

该协议允许模型看到部分结果后纠偏和取消无价值任务，但会引入跨 Step 未完成 Task、
显式 await 集合与更复杂的 Messages API 配对关系，必须单独设计、评测和发布，不能
在首版实现中半开放。

## 22. 最终原则

```text
Platform 持有身份、会话与可靠状态；
Agent 大脑持有决策循环；
专业 Agent 持有领域能力；
Adapter 只做可靠连接；
MetaBot 只是一个可失效的本地目的地。
```

只有满足这个边界，系统才是 Agent 大脑，而不是披着对话界面的固定工作流。
