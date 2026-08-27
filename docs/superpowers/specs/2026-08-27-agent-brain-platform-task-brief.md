# Agent Platform 实施任务书：业务确认与九 Agent 调度

**日期：** 2026-08-27

**状态：** 待评审

**仓库：** `/Users/neo/Developer/work/AI-Agent-Platform`

**设计依据：** `2026-08-27-agent-brain-actions-and-external-adapters-design.md`、
`2026-08-27-http-task-contract-v1.md`

## 1. 目标

在不重建现有 041/045/046 Durable Loop 的前提下：

1. 先修复 capability version 硬编码导致真实 Agent 无法委派的 P0；
2. 用迁移 049 增加状态、事件和唤醒，用迁移 050 增加用户 Action 确认；
3. 允许 FAE、VOC、行政同时具有专业工作区和 Brain 调度能力；
4. 提供 FAE、行政共用的 HTTP Adapter 基座；
5. 先跑通 VOC 确认，再接 FAE、行政只读，最后开放行政写操作；
6. 只对现有协作室增加确认卡和新状态，不另造任务管理页面。
7. 由 Platform 统一提供钉钉身份、专业工作区 SSO 和服务端任务身份，所有内部使用都
   归属于同一个 `internal_user_id`；附件底座按独立并行设计交付。

Platform、FAE、行政、VOC 和 MetaBot 代码及部署都由 Orbbec 掌控。跨仓协议是独立部署
边界，不是第三方黑盒兼容层；需要一致行为时应同步修改相关仓库。

## 2. 不得改动

- 不修改 FAE 公网入口、生产容器或客户身份；
- 不修改行政 `/office/*` 路径和服务门户产品形态；
- Brain/Task Adapter 不把 Platform Cookie 或钉钉原始身份传给上游；`/office/*`
  浏览器身份只按既有受控 Cookie + 回环 Subject 接口处理；
- 不新增 Task Group 表；
- 不重写已有七个 Brain 工具；
- 不用现有 `/chat` 长请求包装成伪任务；
- 不给大脑确认业务操作的工具；
- `max_steps` 硬上限固定为 24，但首个生产 Manifest 未经实测不从 12 提升。

## 3. 阶段 0：P0 独立修复

### 3.1 实现

- `DelegateTaskCall` 增加必填 `capability_version: int > 0`；
- Tool Schema 和 Brain Prompt 要求从 `list_agents` 结果回传版本；
- `loop_runtime.py` 不再传常量 `1`；
- Runtime Registry 继续执行 optimistic capability check；
- 版本不一致返回 `capability_changed`、当前版本和 `must_call_list_agents=true`，不创建
  Task；同一 Loop/Agent 连续两次不一致后返回 `capability_version_unstable` 并停止该
  派发意图；
- 更新 Prompt SHA 和 Release Manifest。

### 3.2 测试

- 使用真实 `load_capability_cards()` 和 Runtime Registry，不使用只接受版本 1 的 Fake；
- HR 版本 2 的 delegate 能创建 Task；
- 版本 1 对 HR 被拒绝；
- `list_agents -> delegate_task` 版本传递完整；
- 重启后重放相同 Tool Call 不重复创建 Task。

### 3.3 提交和发布

该阶段必须形成独立 Commit、Release 和验收记录。不能和迁移 049/050 混合，便于快速回滚和
定位 Prompt Cache 首次失效成本。

## 4. 迁移 049 与 050

### 4.1 迁移 049：状态、事件与唤醒

- 扩展 `agent_tasks.status`；
- 增加 `dispatched_at`，并以 `mark_adapter_delivery_dispatched_v49` 替换会把 Task 直接
  写成 running 的 v45 函数；
- 为 Task 增加暂停/恢复所需已消耗执行时间；
- 为 Task 增加受约束 `terminal_reason_code`，协议错误落为
  `failed/protocol_violation`，不扩张 Status 枚举；
- 为 Loop 增加 `waiting_confirmation` 和 `intervention_expires_at`；
- 扩展 Conversation Turn 的状态 Check 和单活跃索引；
- 扩展 Event/Wake 白名单；
- 新增 `(loop_id, task_id)` durable delivered cursor；删除 Wait 创建时以
  `max(agent_task_events.seq)` 初始化游标的逻辑；
- Wait 创建与事件 Append 复用同一个 Serializable 结算函数：已有未交付合格事件时立即
  生成 Tool Result，不留下 active Wait；
- 按 HTTP Task Contract v1 统一 `timeout`、`input_required`、`action_required`；
- 替换 `append_agent_task_event_v45` 为 v49，包含新状态映射；
- 增加单 Task 协议隔离函数，序号缺口/乱序/未知事件不得让 CheckViolation 逃逸并拖垮
  整个 Worker；
- 重新审计 Worker/App 的列级 Grant。

### 4.2 迁移 050：Action 与执行

- 新增 `agent_task_actions`，Summary/Impact/Parameters 全部按 Ciphertext + Key Version +
  SHA-256 保存；
- 不冗余 agent/conversation/turn，权威归属沿 task -> tool call -> step -> loop -> turn；
- 增加 `(task_id, action_digest)` 索引；
- 增加 Action Execution Delivery 的稳定身份和幂等约束；
- 增加确认、拒绝、过期、取代和 Action 执行公开投影；
- 重新审计 Worker/App 的列级 Grant。

### 4.3 数据库函数

至少提供：

- `propose_agent_task_action_v50`：Worker 身份，幂等创建或取代 Action；
- `confirm_agent_task_action_v50`：App 身份，校验 Owner/Digest/Expiry 并创建执行投递；
- `reject_agent_task_action_v50`：App 身份，原子拒绝并唤醒；
- `expire_agent_task_actions_v50`：Worker/Reaper 身份，批量过期并唤醒；
- `supersede_agent_task_action_v50`：只取代被明确修改的 Action；
- `append_agent_task_event_v49`：包含新增事件和任务状态映射。
- `mark_adapter_delivery_dispatched_v49`：Delivery dispatched 时只把 queued Task 转为
  dispatched；首条真实进度/终态才写 running/started_at；
- `create_or_settle_wait_subscription_v49`：锁 durable cursor，若事件已先到达则立即结算，
  否则创建 active Wait；
- `fail_agent_task_protocol_v49`：仅隔离一个 Task/Session/Delivery 并写 Agent 健康事实，
  不伪造上游缺失序号；有 Wait 时直接写 Platform-origin Tool Result，无 Wait 时由下一次
  Wait 创建读取 Task 控制面终态并立即结算。

函数必须 `SECURITY DEFINER`、固定 `search_path`、撤销 public 权限，并精确验证生产与
Preview caller role。

## 5. Runtime 与状态推导

- 状态推导集中在一个领域函数，不在 Route、Worker、Repository 多处复制；
- 只有无可执行 Step 且全部非终态 Task 等用户时，Loop 才暂停；
- 有其他可运行 Task 时保持活动计时；
- Task 自己的等待时间不计入 Task 有效执行时长；
- Action 超时唤醒大脑部分交付，不走整轮 `user_input_timeout`；
- 无关普通消息不改变 pending Action；只有确认卡“修改”、绑定 action_id 的介入或上游
  新参数 Proposal 才 supersede 对应 Action，并先向用户提示；
- 存在 Owner Pending Action 时，`submit_answer` 返回
  `pending_action_requires_resolution`，Loop 保持非终态，Action 不失效；
- 终止 Loop 时清理 pending Action 并请求停止对应 Task。

## 6. Action 服务

新增独立服务层，Route 不直接更新表：

```text
ActionProjectionService
ActionCommandService
ActionExpiryWorker
ActionExecutionDispatcher
```

确认卡投影只读持久化 Action；模型 Markdown 不进入参数或授权路径。确认 API 使用
Platform Session + CSRF，拒绝非 Owner、错误 Digest、过期和终态请求。

`action_required` Proposal 的字段、Canonical JSON、Digest 和过期语义严格使用 HTTP
Task Contract v1。Digest 只覆盖
`platform_task_id + action_seq + action_kind + parameters`；Summary/Impact 不参与。
Execute 只发送 `action_id + action_digest + idempotency_key`，不得把 Platform 保存的
Parameters 回传覆盖上游持久副本。

Proposal 与 Task 状态无条件落库，active Wait 不是前置条件。确认、拒绝、过期和明确修改
时，有 active Wait 才按迁移 046 模式终结并填 Tool Result；无 active Wait 仍持久化决议
和执行投递，后续 Wait 通过“创建即检查未交付事件”立即取得事实。禁止复用旧 Wait 或等
轮询碰运气唤醒。

## 7. 通用 HTTP Adapter

实现共用基座：

```text
HttpTaskAdapter
SignedTaskTokenIssuer
TaskEventCursorSynchronizer
TaskCapabilityProbe
```

基座负责：短时签名凭证、快速创建、幂等键、上游任务映射、游标同步、重试退避、健康、
协议错误和明确 unavailable。FAE 与行政的内部 Task Facade 直接输出 HTTP Task Contract
v1 规范事件；Platform Adapter 不猜测 `accepted/progress/timed_out` 等旧事件。

创建请求携带经 Catalog 校验的 `capability_version` 和硬 `deadline_at`。上游再次校验
版本；Task Token 同时绑定能力版本、Task Deadline，以及确认后的独立
`action_execution_deadline_at`。过期请求不得创建，截止后不得继续业务写操作。

事件读取固定传 `wait_seconds=0` 并消费有限 JSON 页面。一次 Tick 不得进入 SSE/长轮询；
无事件时立即返回，不能阻塞其他 Agent 的派发、取消或 60 秒健康心跳。

事件页先在内存中验证从 `after + 1` 连续，再逐条落库。协议错误只终结当前 Task、标记
当前 Agent 健康异常并继续 Tick。Brain Step、Adapter、Reaper 三个阶段分别 catch、分别
写 Heartbeat；单 Agent 错误不得把三类 Heartbeat 一起标为 degraded。

Action 能力使用可选接口 `ActionCapableAdapter`。行政只读阶段和 FAE Adapter 不实现。

## 8. Platform 统一身份底座

实现两个相互独立但共享 `internal_user_id` 的通道。

### 8.1 Workspace SSO

- 同源 VOC 和 HR/Marketing 继续使用 Platform Session；
- `/office/*` 继续使用现有具名 Cookie + 最小 Subject 接口；
- 新增 FAE Agent Launch：校验登录、在职状态和 Agent 授权后签发60秒单次授权码；
- 新增服务端 Exchange/Introspection：FAE 用受认证 back-channel 交换最小 Subject，并
  校验 `identity_binding_id` 是否仍有效；
- 授权码只存哈希，绑定 user/agent/state/return path，使用后立即作废；
- FAE 专业入口 Catalog URL 指向 Launch 端点，不直接使用公共 URL；
- Platform 登出、停用和移出企业会使 Binding 失效。

### 8.2 Task Identity

- Adapter 每次创建任务签发短时 audience-bound Token；
- Token 只含最小 `internal_user_id`、Agent、Task、Scope、request_id 和有效期；
- 不传 Cookie、钉钉 provider ID、部门或角色；
- 所有跨 Agent Session、Action、Feedback 和审计用同一 internal_user_id 关联。

身份底座必须有后端测试覆盖：错误企业、停用用户、无 Agent 授权、码重放、错误 state、
错误 audience、Binding 失效和 Token 轮换。

## 9. 附件并行项目边界

统一附件底座不属于迁移 049/050，也不阻塞 VOC Action。完整实现按
`2026-08-27-platform-agent-attachment-substrate-design.md` 建立独立分支、迁移、计划和
验收。

本任务只负责 Brain 接口边界：保留 `attachment_refs`、Catalog 能力声明、派发前所有权
检查、能力不匹配的 `attachment_unsupported` 和 Task Grant 调用点。在独立附件轨未通过
验收前，不得声称任何新增 Agent 已支持 Platform 图片/文档；也不得静默丢弃引用。

## 10. VOC 第一条确认链路

复用 `app/voc_extension` 的短时 Token 与 Draft/Submit 能力：

1. Brain 派发“整理 VOC”；
2. VOC Adapter 返回结构化 Draft；
3. VOC 先持久化 Canonical Draft 参数与 Digest，再按 HTTP Task Contract v1 生成
   `action_required`，Platform 校验并持久化 Action；
4. 卡片展示待提交参数；
5. Owner 确认后执行 Submit；
6. 结果事件唤醒 Brain；
7. 重复确认不能重复入库。

必须覆盖拒绝、超时、Draft 修改导致 Digest 变化、确认前/后崩溃。

## 11. Catalog

- validator 允许 `external_workspace + brain_delegation`；
- 双模式要求 workspace URL 与 Adapter 同时完整；
- `CALLABLE_AGENT_IDS` 显式九个；
- 缺 Adapter 或健康异常时保持卡片并显示 unavailable；
- FAE/VOC/行政只有在各自合同验收后才逐个打开 brain_delegation；
- 每次能力改变必须 bump `capability_version`。

## 12. 预算

- 两处模型上限从 300 放宽到 900；
- Runtime 从授权后的能力卡读取时长；
- FAE 设置 600，其他默认 300；
- 普通 Task 截止时间不超过创建时剩余 Turn 活动预算；
- Action 确认后获得固定独立执行窗口；
- list/receipt 投影剩余预算和时长；
- 架构硬上限为 24；首个生产 Manifest 保持 12，增加 12/16/24 评测工具和指标，达到门槛
  后以受审计配置发布 24。

## 13. 前端

现有协作室增加：

- 新任务状态；
- ActionCard；
- Confirm/Reject 按钮及 CSRF；
- Expired/Superseded/Executing/Completed/Failed 展示；
- 真实确认身份、时间和 Digest 摘要；
- 修改 Action 前的明确失效警示，无关普通消息保留 Pending 卡片；
- 预算不足、Agent unavailable、Scope denied 的明确提示。

禁止通过前端计时器伪造处理阶段，禁止从回答 Markdown 解析 Action。

## 14. 验收

- P0 真实 HR 委派；
- capability_changed 返回当前版本并阻止无界重试；
- Reference Adapter 全状态机；
- HTTP Task Contract v1 规范事件、Action Proposal/Execute 和 `wait_seconds=0`；
- Action Digest 四字段 Fixture 在 Platform、FAE、行政得到相同 lowercase hex；
- 事件先于首次 Wait 到达时立即结算，finding/action_required 不丢失；
- queued -> dispatched -> running 三态都有真实持久迁移；
- pending Action 存在时 submit_answer 被拒绝，普通消息不使确认卡失效；
- 单上游事件序号缺口只隔离对应 Task/Agent，其他 Agent 和三个 Worker 阶段继续；
- Task Deadline 到点产生 timeout，迟到执行不能发生业务写入；
- VOC 六条确认路径；
- FAE 600 秒任务、断线游标恢复、追问、取消；
- 行政只读 Scope 越权拒绝；
- 行政写操作只在后续 capability version 启用；
- Worker 在 propose 后、confirm 前、confirm 后被杀时都不重复执行；
- 九 Agent 不静默消失，Mac 离线明确 unavailable；
- `/office/*` 和 FAE 公网入口不变；
- 钉钉登录一次后，VOC、行政和企业 FAE 入口均解析为同一个 internal_user_id；注销或
  停用后跨域 FAE Binding 不再有效；
- 附件底座不阻塞 VOC；附件能力未开放时明确返回 `attachment_unsupported`。

## 15. 交付物

- P0 Commit/Release/验收证据；
- 049/050 Migration、权限矩阵和独立回滚说明；
- `contracts/http_task_v1/` 唯一黑盒套件、固定 Commit/SHA-256 和三个仓库的 CI 证据；
- HTTP Task Contract v1 Schema；
- Workspace SSO/Task Identity 合同、威胁模型和身份贯通证据；
- 独立附件项目的接口占位和依赖说明；附件项目自己的交付物由其设计维护；
- VOC/FAE/行政 Adapter；
- Action 前端与审计证据；
- 九 Agent 并发报告；
- 12/16/24 Step 成本报告；
- 尚未解决问题清单，禁止用 fallback 掩盖。
