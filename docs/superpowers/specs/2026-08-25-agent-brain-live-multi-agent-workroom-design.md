# Agent 大脑真实多 Agent 协作室设计

**日期：** 2026-08-25
**状态：** 书面设计已于 2026-08-25 批准，等待 TDD 实施
**范围：** AI-Agent-Platform 的 Agent 大脑运行协议、专业 Agent 子会话与员工端产品形态

## 1. 决策摘要

Agent 大脑不再只表现为“聊天框背后调用一次专业 Agent”，而要成为一个真实、持续、
可恢复的多 Agent 负责人：自主理解用户原始需求，拆解工作，创建一个或多个专业
Agent 子会话，持续读取阶段进展，向专业 Agent 追问或追加要求，必要时调整分工或停止
任务，最后统一整合和交付。

员工端采用“对话即任务室”的产品形态：用户始终停留在 Agent 大脑持续对话中；复杂
任务自动展开真实协作室，简单问题直接回答。协作室展示 Agent 大脑与专业 Agent 当时
真实产生的 thinking summary、任务消息、公开工作日志、成果和失败，不播放模拟动画，
不根据耗时伪造过程。

首版采用中心协调拓扑：专业 Agent 不直接互相发消息，所有跨 Agent 协作都经过 Agent
大脑。用户无需审批内部拆解、Agent 选择和执行计划；Agent 大脑自主运行。只有业务意图
存在关键歧义，或未来要执行不可逆的外部写操作时，才询问用户。

## 2. 目标与非目标

### 2.1 目标

1. 用户能够直观看到一个真实工作的智能团队，而不是黑盒路由器。
2. 每个专业 Agent 拥有真实、持久化、可继续通信的子任务会话。
3. Agent 大脑能够在子 Agent 任务结束前接收阶段进展并作出新决策。
4. Agent 大脑能够给运行中的子 Agent 发送追问、补充上下文、返工和停止指令。
5. 所有公开过程都能追溯到真实 Provider block、Tool Call、任务消息或 Agent 事件。
6. Worker 或 Adapter 崩溃后能够恢复，不重复创建任务、消息或最终答案。
7. 用户仍然只需要输入原始需求，不需要学习多 Agent 编排。

### 2.2 非目标

- 不做低代码流程编排器。
- 不要求用户选择 Agent、模型、工具或任务拓扑。
- 不允许专业 Agent 之间形成无控制的点对点消息网络。
- 不展示任何 Provider 都不会返回的原始 chain of thought。
- 不用头像动画、计时器文案或预设阶段冒充真实协作。
- 不强迫简单问题调用多个 Agent。
- 本项目不修改 AI ADMIN、`/office/*`、FAE 应用代码或 FAE 公网入口。

## 3. 产品原则

### 3.1 入口就是使用

根页面继续是 Agent 大脑的持续对话。左侧是 Conversation 历史，中间是用户与 Agent
大脑的主对话。用户输入后不跳转到任务管理页，不要求创建项目，也不先填写编排表单。

### 3.2 复杂任务自动形成协作室

一个 Turn 发生真实委派后，在该 Turn 内展开协作室；没有委派时不显示空的团队界面。
协作室属于这一个 Turn，不是永久挂在页面上的 Agent 名单。

协作室提供三个视图：

- **团队：** 谁在做什么、当前状态、用时与阻塞。
- **协作记录：** Agent 大脑与各专业 Agent 的真实消息和公开工作日志。
- **交付成果：** 报告、附件、证据、链接与每位 Agent 的贡献。

执行期间默认展开；Turn 完成后折叠为简洁的团队贡献记录，用户可以重新展开。点击某个
Agent 在原页面展开它的完整子会话，不离开主 Conversation。

### 3.3 用户观察团队，不管理团队

Agent 大脑自主决定直接回答、委派对象、并行度、补派、返工和停止。用户可以随时通过
主输入框改变目标、补充信息或要求停止；这条消息先进入 Agent 大脑，由 Agent 大脑决定
如何影响在飞任务。

普通的 Agent 选择和技术计划不得要求用户批准。用户只在以下情况被打断：

- 缺少无法从上下文推断的关键业务选择；
- 要执行不可逆或对外生效的写操作，需要业务授权。

## 4. “真实透传”的定义

### 4.1 可公开的真实信息

产品可以展示：

1. Opus 5 Provider 返回的 `thinking.display=summarized` 内容；
2. 专业 Agent 自身 Provider 返回的真实 thinking summary；
3. Agent 大脑真实发出的 Tool Call 及其受限的公开目的；
4. Agent 大脑发给专业 Agent 的真实任务消息和后续消息；
5. 专业 Agent 在执行当时主动产生的公开工作日志；
6. 专业 Agent 返回的阶段结果、最终结果、证据和附件；
7. Platform 的事实状态，例如消息已投递、Agent 离线、任务超时或等待输入。

所有公开项必须具有稳定的 `event_id`、`task_id`、`seq`、来源、时间和类型。前端只能
消费公开事件投影，不能自己推断 Agent 在做什么。

### 4.2 thinking 边界

Agent 大脑生产请求改为：

```json
{
  "thinking": {
    "type": "adaptive",
    "display": "summarized"
  }
}
```

`summarized` 是 Provider 返回的真实思考摘要，不是 Platform 生成的解释，也不是原始
chain of thought。任何 `display` 设置都不会返回原始 chain of thought。thinking
summary 以流式增量进入当前 Step 的公开事件；原始签名 block 仍按 Provider 协议原样
加密保存和回传。

公开 thinking summary：

- 只对 Conversation 所有者和具备跨用户审计权限的平台所有者可见；
- 加密保存，保留期跟随 Conversation；
- 不传给专业 Agent，不进入数据飞轮、搜索索引或普通运营导出；
- 不替代 Tool Call、证据或正式答案；
- 简单请求若 Provider 没有产生 thinking block，界面不显示空占位。

Provider 原始响应和签名继续使用现有短期、加密、受审计的运行数据边界。

首批 HR 和 Marketing Agent 同样必须提供 Provider 原生 thinking summary，并规范化为
`agent.thinking_summary` 事件。该事件必须带 `source=provider` 和不可伪造的 Adapter
运行引用，不能由工作日志、最终答案或 Platform 状态反向生成。当前 Claude Code 传输
如果不能返回 Provider thinking summary，则阶段 2 必须先升级传输协议；在此之前该
Agent 只能明确显示“暂不提供思考摘要”，不能用 `work_update` 冒充 thinking。首批正式
发布门槛要求 HR 和五个 Marketing Agent 全部通过 thinking summary 能力探测。

### 4.3 专业 Agent 公开工作日志

专业 Agent 协议新增结构化 `work_update`：

```json
{
  "task_id": "uuid",
  "child_session_id": "opaque-id",
  "seq": 7,
  "kind": "finding",
  "summary": "已识别三种跨公司经历组合路径",
  "evidence_refs": ["resume:experience:2", "resume:experience:3"],
  "artifact_refs": [],
  "created_at": "2026-08-25T12:00:00Z"
}
```

`kind` 只允许：

```text
plan / progress / finding / question / blocker / decision / artifact / result
```

Adapter 不支持公开工作日志时，Platform 只能显示事实状态“任务运行中，尚无阶段更新”，
不能补写一段看似来自 Agent 的内容。

### 4.4 禁止 Mock

以下行为进入自动化治理测试：

- 用定时器依次播放“正在分析”“深入思考”“正在整理”；
- 根据任务运行时长猜测工作阶段；
- Turn 完成后倒推并伪造 Agent 对话；
- 把数据库状态翻译成第一人称 Agent 发言；
- 展示没有对应 Tool Call、消息或任务事件的 Agent 卡片；
- 不支持 thinking summary 的 Agent 冒充支持。

## 5. 目标运行架构

```text
用户 / 钉钉
     |
     v
Platform Conversation API
     |
     v
Agent 大脑持久化 Loop（Opus 5）
     |
     +-- 创建/继续 HR Agent 子会话
     +-- 创建/继续 Marketing Agent 子会话
     +-- 创建/继续 FAE Agent 子会话（后续独立接入批次）
     |
     v
Agent 大脑接收真实进展、追问、调整、停止并统一交付
```

通信拓扑固定为中心协调：

```text
HR Agent ---------+
Marketing Agent --+--> Agent 大脑 <--> 用户
FAE Agent --------+
```

专业 Agent 不知道其他 Agent 的地址、Cookie、用户钉钉身份或完整 Conversation。
需要协同时，它向 Agent 大脑提出问题或返回证据，由 Agent 大脑决定是否转交给另一个
Agent。

## 6. 从阻塞批次升级为持续管理协议

### 6.1 当前限制

当前 `delegate_task` 在同一 Step 可以并行创建多个任务，但模型必须等整批任务终态后
才恢复。模型看不到中途进展，无法合法地给活任务追问，也无法主动取消仍在运行的任务。
这只能证明“并行调用”，不能形成 Devin 式持续管理。

### 6.2 新工具协议

Brain 工具集升级为：

```text
list_agents
delegate_task
await_agent_events
send_agent_message
stop_agent_task
request_user_input
submit_answer
```

一个 Brain Step 可以包含多个同类 `delegate_task`，或多个同类
`send_agent_message`；其他工具必须独占 Step。一个 Step 不得同时派发、发后续消息和
等待。运行时一次性回填该 Step 内全部 Tool Result，避免生成不合法的 Messages API
序列。

`delegate_task` 改为非阻塞派发：任务事务性创建后立即返回
`{status: "dispatched", task_id, child_session_id}`。同一 Assistant 响应内多个
Tool Call 仍一次性回填全部即时派发结果，满足 Messages API 的 Tool Result 约束。

`await_agent_events` 是显式暂停点，不做轮询。它声明要等待的任务和唤醒条件：

```json
{
  "task_ids": ["uuid-1", "uuid-2"],
  "wake_on": ["question", "finding", "result", "failed", "timeout"],
  "public_reason": "等待专业 Agent 的阶段结果后继续整合"
}
```

Loop 进入 `waiting_agents` 并释放 Worker。数据库收到匹配事件后唤醒新的 Brain Step，
把自上次游标后的公开事件作为 Tool Result 回填。进展事件可以在任务终态前唤醒大脑。
所有原始事件仍逐条保存；运行时可以按任务和事件类型合并一次模型唤醒，防止高频进展
造成重复推理，但不能丢失用户可见事件。

`send_agent_message` 向既有 `child_session_id` 发送追问、补充要求或纠偏，不新建任务。
`stop_agent_task` 对仍在运行的任务写取消意图并调用 Adapter；不支持取消的 Adapter
返回明确 `cancel_unsupported`。这两个工具必须绑定当前 Loop 所有的 Task，禁止跨
Conversation 操作。

### 6.3 子 Agent 会话

一个 `agent_task` 对应一个持久化专业 Agent 子会话，包含：

- 父 Conversation、Turn、Loop 和用户授权快照；
- 专业 Agent、Adapter、能力版本和最小上下文；
- 初始任务消息；
- 大脑后续消息与 Agent 回复；
- 工作日志、证据、附件和终态；
- 独立的消息序号、事件游标和 Adapter 投递幂等键。

任务完成后子会话保持只读；若大脑需要同一专业 Agent 做新的独立工作，创建新任务，
不得把两个目标混入一个无法审计的子会话。

### 6.4 Adapter 能力声明

Catalog/Adapter 必须声明：

```text
supports_persistent_session
supports_followup_message
supports_progress_events
supports_thinking_summary
supports_cancel
supports_attachments
typical_latency_seconds
```

Agent 大脑在派发前看到真实能力和健康状态。缺失能力不导致 Agent 从 Catalog 消失，
而是以明确 unavailable 或 capability_unsupported 返回。不得静默改派其他 Agent。

## 7. 数据模型增量

沿用 `platform_brain` 作为执行事实源，增加或扩展：

- `agent_task_sessions`：任务与 Adapter 子会话映射；
- `agent_task_messages`：大脑和专业 Agent 的真实消息，`(task_id, seq)` 唯一；
- `agent_task_events`：继续保存状态和工作日志，`(task_id, seq)` 唯一；
- `brain_thinking_summaries`：Step 级流式思考摘要及投影状态；
- `brain_wait_subscriptions`：`await_agent_events` 的任务集合、游标和唤醒条件；
- `adapter_deliveries`：初始任务、后续消息和停止请求的独立幂等投递。
- `brain_user_interventions`：运行中 Turn 的用户补充、停止或改向请求及处理结果。

核心不变量：

1. 一个 Task 只有一个规范子会话；
2. 一个 Task 消息序号只能提交一次；
3. 一个 Tool Call 只能创建一个 Task 或一个消息投递；
4. 一个 Loop 同时只有一个 active Step；
5. 一次 Agent 事件最多唤醒一个待处理 Step；
6. 最终 Assistant Message 与 Turn 完成仍在一个数据库事务中；
7. V2 永不写 `missions` 或 `mission_runs`。

## 8. 前端交互

### 8.1 主页面

现有左侧 Conversation 列表保留。中间消息流中的复杂 Turn 包含一个可展开的
`MultiAgentWorkroom`，而不是跳转到管理页面或单独的 Mission 页面。

### 8.2 团队视图

每张 Agent 卡只显示真实数据：角色、任务目标、状态、最后一条工作日志、用时和交付物
数量。卡片状态来自 Task/Adapter 事件，不使用前端计时状态机。

### 8.3 协作记录

按服务器序号显示：

- Agent 大脑 thinking summary；
- 专业 Agent Provider thinking summary；
- 大脑任务派发与后续消息；
- 专业 Agent 的工作日志、问题和结果；
- Platform 明确标注的投递、超时、离线和恢复事实。

来源样式必须区分“大脑思考摘要”“真实 Agent 消息”和“Platform 状态”，不能全部伪装
成聊天气泡。专业 Agent thinking summary 与其公开工作日志也必须使用不同来源标记。

Thinking delta 在 Provider 流式响应到达时按 `(step_id, block_index, delta_seq)` 或
`(task_id, provider_run_id, block_index, delta_seq)` 幂等追加。前端可以实时渲染摘要，
但 Tool Call 和任务状态只有在完整 Provider 响应通过协议校验后才提交。Provider 中断时
已展示的摘要标记为“本次思考中断”，不得据此创建任务或交付答案。

### 8.4 用户介入

主输入框始终可用。用户的新消息进入同一 Conversation：

- 当前 Turn 运行中时，记录为 `user_intervention`，唤醒 Agent 大脑处理；
- Agent 大脑可继续、修改、停止或重建部分任务；
- 用户不直接持有 child session 写权限；即使 UI 上选择某个 Agent，消息也通过 Agent
  大脑转交并留下审计记录。

### 8.5 移动端

移动端主界面仍是对话。协作室使用全宽折叠区，团队、协作记录、交付成果用顶部切换，
不固定显示右栏，不出现横向任务画布。

## 9. 状态、错误与恢复

- **Agent 离线：** 任务立即明确 unavailable；大脑可以部分交付或说明无法完成，不自动
  换 Agent。
- **阶段事件延迟：** 保持真实运行状态，不生成虚假进度；达到超时后产生 Platform
  timeout 事件。
- **Agent 提问：** 唤醒大脑。大脑能回答则发送后续消息；不能回答才询问用户。
- **部分失败：** 成功结果继续可用，失败任务的原因进入 Tool Result 和最终交付。
- **授权撤销：** 当前 Loop 失败关闭，未交付结果不再投影给用户。
- **用户停止：** 停止所有在飞 Task，记录 Adapter 是否确认；最终状态不得冒充成功。
- **Worker 崩溃：** 从 append-only Step、Tool Call、Task、Message 和 Event 重建；租约
  到期后恢复，不重复消息。
- **Provider 拒答：** 显式 `provider_refused`，不重试、不换模型、不伪造 thinking。
- **公开事件解析失败：** 原始执行可继续，公开投影显示“该更新暂不可展示”并记录稳定
  错误码；前端不得自行补写。

## 10. 预算与自主性

首版默认上限：

```text
max_parallel_tasks = 4
max_agent_tasks = 8
max_followup_messages_per_task = 4
max_brain_decision_steps = 24
max_single_task_active_seconds = 600
max_turn_active_seconds = 1800
max_waiting_user_duration = 24h
```

`waiting_user` 不计入 active duration。大脑在预算内自主运行，不向用户展示技术预算，
但预算耗尽必须明确部分交付。预算不能通过隐藏切模型、静默换 Agent 或假装完成来绕过。

## 11. 安全与数据边界

- 子 Agent 只获得完成任务需要的最小上下文和附件句柄。
- 子 Agent 不获得 Platform Cookie、钉钉原始 ID、角色或完整历史。
- 所有 Agent 任务和消息绑定 Conversation 所有者与有效 Agent 使用授权。
- thinking summary、公开工作日志和子 Agent 消息均加密存储。
- 原始 Provider block、Prompt、Adapter payload 和内部 URL 不进入公开事件。
- 平台所有者查看他人协作室继续写审计日志。
- 用户可见的 thinking summary 不进入数据飞轮或通用搜索。
- FAE 作为外部客户 Agent 的独立身份和入口保持不变；未来接入 Brain Adapter 需要单独
  威胁模型与验收批次。

## 12. 测试与发布门槛

### 12.1 后端协议

- 多个非阻塞 `delegate_task` 得到不同 Task 和子会话；
- 进展事件能在终态前唤醒大脑；
- 大脑能向运行中任务发送后续消息并收到对应回复；
- 停止活任务成功，或明确返回不支持；
- 重复 Tool Call、消息和事件不产生重复数据；
- 崩溃发生在派发、消息投递、事件提交和最终答案事务各边界时均可恢复；
- 专业 Agent 之间无法直接寻址或发消息；
- 授权撤销、Agent 离线、超时和 Provider 拒答不触发静默兜底。

### 12.2 真实性

- 每个 UI Agent 节点都有真实 Task；
- 每条 Agent 发言都有真实 `agent_task_message` 或 `work_update`；
- 每条大脑思考都有 Provider thinking summary block；
- 每条专业 Agent 思考都有 Provider thinking summary block 和 Adapter 运行引用；
- 关闭/缺失 progress 或 thinking 能力时不出现模拟内容；
- 前端源码和测试禁止定时阶段文案；
- 公开事件能反查来源、序号和时间，但不暴露内部密文或 Tool ID。

### 12.3 产品验收

1. 简单自我介绍直接回答，不出现空协作室。
2. 一个跨 HR 与 Marketing 的真实问题创建至少两个并行子会话。
3. 用户能在同一页面看到大脑思考、派发、Agent 阶段发现和最终结果。
4. 大脑根据一个阶段发现向运行中的 Agent 发出真实追问，Agent 再次回复。
5. 点击 Agent 能展开完整子会话，不离开主 Conversation。
6. 用户在执行中补充要求后，大脑能调整在飞任务并留下真实记录。
7. 最终答案明确汇总各 Agent 贡献、失败和附件。
8. 完成后协作室折叠，重新打开仍能回放相同事件顺序。
9. 手机端可以完成提问、观察团队、补充要求和查看最终交付。
10. `/office/*`、FAE 容器、FAE 域名和原 IP 在发布前后保持不变。

### 12.4 独立质量评审

使用真实 HR、Marketing 和跨领域案例，由独立 Codex 或业务专家评审：

- 大脑是否合理拆分而非为了展示强行多 Agent；
- 追问和返工是否真正改善结果；
- 公开 thinking summary 是否有帮助且不过度冗长；
- 工作日志是否真实、有证据、可理解；
- 最终结果是否优于把同一问题直接发给单个专业 Agent。

## 13. 分阶段交付

### 阶段 1：非阻塞协作协议

实现子会话、消息、等待订阅、后续消息、停止任务、幂等与崩溃恢复；先用确定性 Reference
Adapter 验证完整状态机，不改员工端 UI。

### 阶段 2：MetaBot 真实子会话

将 HR 和五个 Marketing Agent 的 `metabot_local` Adapter 从一次性 Core Chat 调用升级
为持久化任务会话，支持 Provider thinking summary、工作日志、后续消息和明确取消能力。
先对 Claude Code 传输做真实 thinking summary 能力探测；拿不到 Provider block 时必须
升级传输合同，不能解析终端装饰文本或生成替代摘要。Mac 离线仍只影响这些本地 Agent。

### 阶段 3：对话即任务室

实现 `MultiAgentWorkroom`、三个视图、Agent 子会话展开、用户介入与移动端体验；所有
数据来自新公开事件协议。

### 阶段 4：真实 thinking summary

生产 Provider 切换为 `display=summarized`，完成缓存、流式、访问控制、保留策略与内容
边界验收。Thinking 配置变化写 release manifest 和审计事件，不做运行时切换。

### 阶段 5：真实案例发布

使用 HR、Marketing 与跨领域任务完成并行、进展唤醒、追问、返工、停止、崩溃恢复和
独立质量评审。通过后才把新协作室作为 Agent 大脑默认体验。

FAE Adapter 不随本阶段捆绑上线，另行验收；AI ADMIN 只保留专业入口，不参与本次 Brain
调度。

## 14. 参考

- Anthropic Thinking：<https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models>
- Anthropic Adaptive Thinking：<https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking>
- Devin Managed Sessions：<https://docs.devin.ai/work-with-devin/advanced-capabilities>
- 现有持久化 Loop 设计：`2026-08-24-cloud-agent-brain-durable-loop-design.md`
- 现有统一工作区设计：`2026-08-24-agent-brain-unified-workspace-design.md`
