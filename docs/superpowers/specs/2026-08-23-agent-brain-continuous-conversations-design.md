# Agent 大脑持续对话设计

日期：2026-08-23  
状态：产品方向已确认，待实施计划

## 1. 摘要

Agent Platform 登录后的首页必须是可以立即、持续使用的 Agent 大脑，而不是一次性任务提交页。用户发送第一条消息后仍停留在同一段对话中，可以继续追问、补充背景、纠正方向和要求专业 Agent 继续工作。只有用户主动点击“新对话”时，才创建另一条历史记录。

现有 `Mission`、`AgentTask`、`ChildRun`、Trace 和 Evidence 继续作为后台执行与治理模型，但不再直接充当普通用户的对话模型。一次对话可以包含多轮用户消息；每一轮需要执行时，可以在后台创建一个与该轮消息关联的 Mission。用户默认看到干净的对话时间线，需要时再展开真实的任务分发与执行过程。

本设计修正 `2026-08-20-agent-brain-orchestration-design.md` 首个上线切片中“一条用户消息创建一个 Mission”被直接做成用户信息架构的问题。原设计中的持续补充、真实协作、权限、Relay 和治理边界继续有效。

## 2. 当前问题

当前生产行为是：

1. 用户在 `/` 输入一句话；
2. 前端创建一个 Mission；
3. 页面立即跳转到 `/missions/{mission_id}`；
4. Mission 结束后没有继续输入框；
5. 用户只能返回首页重新创建另一个 Mission；
6. 每句话都成为一条“历史任务”。

这把内部执行模型暴露成了产品交互，导致需求澄清、连续追问和基于结果继续工作的体验全部缺失。Mission 页面适合工程观测，不适合作为企业成员的默认使用入口。

## 3. 已确认的产品决策

- `/` 是持续对话首页，输入框立即可用；
- 一段连续交流只形成一条历史对话；
- 发送消息后不跳转到 Mission 页面；
- Agent 大脑的分析、分发、专业 Agent 执行和综合在当前对话内实时展示；
- 用户可以在最终回答后继续追问；
- “历史任务”改为“历史对话”；
- Mission 继续存在，但成为某一轮对话背后的执行记录；
- 管理中心继续按 Mission、ChildRun、Trace 和 Evidence 提供工程视图；
- 专业 Agent 直接入口复用同一套持续对话外壳；
- 第一增量仍为纯文本，不借持续对话顺带扩展附件、多 Agent 并行或外部系统写入。

## 4. 方案比较

### 4.1 推荐：Conversation 为一等对象，Mission 为每轮后台执行

新增稳定的 Conversation、Message 和 Turn 边界。一条 Conversation 包含多轮消息；每个用户 Turn 可以创建零个或一个 Mission，Mission 完成后写回 assistant Message。

优点：产品模型与执行模型清晰分离；真正支持上下文、历史、追问和后续扩展；Mission 的可靠性与治理能力可以完整保留。缺点：需要新增控制面迁移、API 和前端页面。

### 4.2 不采用：把一个 Mission 改造成无限多轮对话

现有 Mission 有明确的 planning、delegated、completed、failed、cancelled 等终态以及取消、重放和审计规则。终态 Mission 再接收新消息会破坏状态机、幂等和运营指标，也会使一次失败影响整段长期对话。

### 4.3 不采用：前端把多个独立 Mission 视觉串联

仅在前端保存父 Mission ID，仍会让后端缺少真正的会话所有权、上下文摘要、统一历史和并发控制。刷新、跨设备、审计和权限校验都会出现双重事实源。

## 5. 用户信息架构

```text
/                         新对话或当前对话入口
/conversations            当前用户的历史对话
/conversations/{id}       持续对话与实时协作过程
/agents                   获授权的专业 Agent 目录
/agents/{agent_id}        以该专业 Agent 开始持续对话

/admin                    管理中心
/admin/missions           Mission 与 ChildRun 工程视图
/admin/...                现有 Review、Trace、Evidence、Operations
```

`/missions/{id}` 暂时保留为兼容和工程详情入口，但从普通用户主导航移除。已有 Mission 深链不失效。

## 6. 首页与对话体验

### 6.1 新对话

用户打开 `/` 时看到简洁的 Agent 大脑对话界面：身份说明、主输入框、少量示例和历史对话入口。第一条消息提交成功后，Platform 服务端创建 Conversation，前端使用 `replaceState` 进入 `/conversations/{id}`，不产生额外浏览器历史跳转。

### 6.2 持续追问

对话页包含：

- 按时间排列的用户与 assistant 消息；
- 固定在底部的输入框；
- 当前轮停止按钮；
- 默认收起、可展开的真实协作卡片；
- 断线、重连、失败和重试状态；
- “新对话”按钮。

一轮执行完成后输入框立即恢复。后续消息自动带上同一 Conversation 的受控上下文。用户不需要理解 Mission、Run 或 Session 才能继续交流。

### 6.3 协作过程

默认时间线只展示易懂状态，例如：

```text
Agent 大脑正在理解需求
已交给 HR Agent
HR Agent 正在执行
Agent 大脑正在检查并整理结果
```

展开后可以查看任务交付、专业 Agent 可展示结果、Evidence、附件引用、耗时和明确错误。不得展示原始思维链、系统 Prompt、密钥或未脱敏调试载荷。

### 6.4 历史

历史列表以 Conversation 为单位，展示标题、最近一条消息摘要、更新时间、使用模式和当前状态。标题第一期直接由首条用户消息安全截断生成，不额外调用模型。排序按 `updated_at` 倒序。

## 7. 数据模型

### 7.1 Conversation

```text
conversation_id            UUID，服务端生成
owner_internal_user_id     稳定内部用户 ID
mode                       brain | direct_agent
direct_agent_id            direct_agent 时必填
title                      安全截断标题
status                     active | archived
summary_ciphertext         可空，长对话摘要
summary_key_version        可空
summary_through_seq        摘要覆盖到的消息序号
created_at
updated_at
archived_at                可空
```

Conversation 不承担执行状态。某一轮失败不会把整段 Conversation 标成失败。

### 7.2 ConversationMessage

```text
message_id                 UUID，服务端生成
conversation_id
seq                        Conversation 内单调递增
role                       user | assistant | system
content_ciphertext
encryption_key_version
turn_id                    关联轮次
mission_id                 可空，关联后台 Mission
delivery_status            accepted | streaming | completed | failed
created_at
completed_at               可空
```

唯一约束为 `(conversation_id, seq)`。内容继续使用现有版本化内容密钥加密，密文不进入普通日志、审计详情或 URL。

### 7.3 ConversationTurn

```text
turn_id
conversation_id
user_message_id
assistant_message_id       可空，开始生成后建立
client_request_id          UUID，幂等键
mission_id                 可空
status                     accepted | running | completed | failed | cancelled | interrupted
created_at
updated_at
```

唯一约束至少包含 `(conversation_id, client_request_id)`。同一 Conversation 第一增量最多一个非终态 Turn。

### 7.4 Mission 关联

现有 Mission 增加 `conversation_id`、`turn_id` 和 `triggering_message_id`。Mission 仍拥有独立状态、事件、Plan、Task、ChildRun 和 FinalDelivery。直接回答也可以创建 Mission，以保留真实运行和审计；但产品 API 不要求每条系统提示都是 Mission。

### 7.5 现有数据迁移

每个已有用户 Mission 迁移成一个只包含首轮请求和已有最终结果的 Conversation；无法确定最终结果的历史 Mission 仍建立 Conversation，并以明确的中断或失败消息表示。迁移只使用稳定 owner ID，不按姓名猜测归属。

## 8. API

```text
POST /api/v1/conversations
GET  /api/v1/conversations?limit=&before=
GET  /api/v1/conversations/{conversation_id}
GET  /api/v1/conversations/{conversation_id}/messages?after=&limit=
POST /api/v1/conversations/{conversation_id}/messages
GET  /api/v1/conversations/{conversation_id}/events?after={sequence}
POST /api/v1/conversations/{conversation_id}/turns/current/cancel
POST /api/v1/conversations/{conversation_id}/archive
```

创建 Conversation 必须和首条 `text` 消息在同一事务内完成，避免留下空对话；Brain 入口只接受 `text`，direct Agent 使用服务器已验证的路径参数，浏览器不能提交模型、Adapter 地址或权限。新建对话和后续发送消息都要求 `Idempotency-Key` 和 CSRF Token。所有端点都从安全 Cookie 得到 `internal_user_id`，并在数据库查询中校验 Conversation 所有权。

Mission API 和管理投影继续保留。普通用户前端不再通过 `POST /brain/missions` 开始每一条对话。

## 9. 上下文与 Token 边界

每轮由 Platform 构造唯一、可审计的对话上下文：

1. Conversation 的已确认摘要；
2. 摘要之后的最近消息；
3. 当前用户消息；
4. 当前用户获授权的 Agent 能力卡；
5. 当前轮所需的最小附件与 Evidence 引用。

第一增量设置固定上下文预算。未超预算时直接使用完整历史；超出预算后，由 Agent 大脑生成结构化摘要并记录 `summary_through_seq`。摘要生成失败时显式缩短可用上下文并提示用户，不静默读取其他 Conversation、专业 Agent 原生 Session 或管理副本。

每个 Turn 固定最多一次 Brain 规划、一个专业 Agent ChildRun 和一次 Brain 综合，沿用现有时长与输出预算。追问会创建新 Turn，不复用已经终态的 ChildRun。

## 10. 状态、并发与可靠性

- 同一 Conversation 第一增量只允许一个运行中的 Turn；并发提交返回 `409`；
- 输入提交以 `client_request_id` 幂等，网络重试不得创建重复 Turn 或 Mission；
- SSE 按 Conversation Event 单调序号续传，不以浏览器连接状态决定执行；
- 刷新和跨设备打开同一 URL 可以恢复消息及当前执行状态；
- 用户停止当前轮时，取消未完成且支持取消的 ChildRun，保留已产生消息和部分结果；
- Agent、Worker、网络或综合失败写入明确的 assistant/system 消息，输入框随后恢复；
- 首个输出后不静默重跑或切换 Agent；
- Conversation 归档不删除 Mission、审计、Evidence 或附件；
- 用户身份失效、授权撤销和硬过期继续遵循现有后端门禁。

## 11. 前端组件边界

- `ConversationPage`：加载 Conversation、消息和事件，管理 SSE 重连；
- `ConversationComposer`：提交、幂等重试、停止和输入限制；
- `ConversationMessageList`：Markdown 消息与安全附件；
- `ExecutionCard`：将 Mission 事件投影成默认收起的真实协作过程；
- `ConversationHistory`：分页历史与新对话；
- `AgentUsePage`：只选择固定专业 Agent，然后进入相同 Conversation 外壳。

现有 `MissionTimeline` 保留给管理/工程详情；可以抽取纯展示组件供 `ExecutionCard` 复用，但不得让 Conversation 页面依赖 Mission 页路由。

## 12. Feedback、审计与观测

- Feedback 默认绑定 assistant Message 和对应 Turn；
- 路由/专业结果反馈继续关联 Mission、Task 或 ChildRun；
- 普通用户只读取自己的 Conversation；
- 管理角色跨用户读取继续按范围授权并写审计；
- Operations 同时统计 Conversation 数、活跃用户、多轮率、Turn 完成率和 Mission 执行质量；
- 不把 Conversation 内容复制到审计元数据，审计只保存对象 ID、动作、结果和必要原因。

## 13. 兼容与发布

采用增量发布：

1. 先部署新表、Repository 和双写能力，现有 Mission 页面仍可使用；
2. 上线 Conversation API 和持续对话前端；
3. 将 `/` 与专业 Agent 入口切到 Conversation；
4. 将普通用户“历史任务”导航改为“历史对话”；
5. 保留 Mission 管理入口和旧深链；
6. 验证后停止普通用户前端创建裸 Mission，但暂不删除兼容 API。

发布仍使用现有 Agent 大脑开关。关闭时根路径进入管理模式，开启时进入持续对话首页。迁移和回滚不得修改或重启独立 FAE 服务。

## 14. 测试与验收

至少验证：

1. 登录后 `/` 直接显示可输入的持续对话页；
2. 第一条消息创建 Conversation 和 Turn，但页面不跳转到 Mission；
3. 回答完成后可以继续追问，第二轮收到第一轮受控上下文；
4. 两轮交流只出现在一条历史对话中；
5. 点击“新对话”才创建新的 Conversation；
6. 需要专业能力时真实创建 Mission 和 ChildRun，并在当前对话内展示过程与结果；
7. 通用回答、专业 Agent 回答和失败消息都成为可回放的 assistant Message；
8. 刷新、SSE 重连和幂等重试不重复创建 Turn、Mission 或 ChildRun；
9. 同一 Conversation 并发提交被拒绝，当前轮完成后可以继续；
10. 用户可以停止当前轮，并继续发送下一条消息；
11. 用户不能读取、追问、停止或归档他人的 Conversation；
12. direct Agent 对话只能使用当前用户获授权的 Agent；
13. Markdown、Evidence 和执行卡安全渲染，不显示原始思维链或秘密；
14. 现有 Mission、Review、Trace、Evidence、Operations 和管理员入口不回归；
15. FAE 域名、容器、账号边界和外部客户入口保持不变；
16. 手机钉钉与普通浏览器都能恢复同一段历史对话并继续输入。

## 15. 第一实施增量的明确范围

第一增量交付：

- Agent 大脑纯文本持续对话；
- direct Agent 纯文本持续对话；
- Conversation/Message/Turn 数据模型和所有权；
- 每轮最多一个专业 Agent；
- Conversation SSE、断线恢复、停止与幂等；
- 对话历史分页；
- Mission 过程内嵌与管理深链保留；
- 现有 Mission 历史兼容迁移。

第一增量不交付：

- 附件输入与输出扩展；
- 多 Agent 串并行；
- 钉钉文档、消息、审批或其他外部副作用；
- 对话共享、多人协作或公开链接；
- 用户删除历史；
- 语音输入；
- 自动切换模型、Agent 或工具兜底。

## 16. 成功标准

产品成功不再以 Mission 数量作为首要使用指标，而是：

- 用户打开首页到发出第一条消息的时间；
- Conversation 中产生第二轮及以上追问的比例；
- 用户无需返回首页即可完成澄清和交付的比例；
- 路由正确率与专业 Agent Turn 完成率；
- 失败后用户能够理解并继续对话的比例；
- 一段真实工作被完整保存在一条历史对话中的比例。

最终体验是：用户打开 Agent Platform，直接和 Agent 大脑持续交流；Agent 大脑在同一段对话中组织真实专业 Agent 工作，并允许用户基于每次结果继续推进，而不是把每一句话变成一条孤立的历史任务。
