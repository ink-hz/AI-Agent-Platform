# Agent Platform 实施任务书：业务确认与九 Agent 调度

**日期：** 2026-08-27

**状态：** 待评审

**仓库：** `/Users/neo/Developer/work/AI-Agent-Platform`

**设计依据：** `2026-08-27-agent-brain-actions-and-external-adapters-design.md`

## 1. 目标

在不重建现有 041/045/046 Durable Loop 的前提下：

1. 先修复 capability version 硬编码导致真实 Agent 无法委派的 P0；
2. 用迁移 049 增加任务等待状态和用户 Action 确认；
3. 允许 FAE、VOC、行政同时具有专业工作区和 Brain 调度能力；
4. 提供 FAE、行政共用的 HTTP Adapter 基座；
5. 先跑通 VOC 确认，再接 FAE、行政只读，最后开放行政写操作；
6. 只对现有协作室增加确认卡和新状态，不另造任务管理页面。
7. 由 Platform 统一提供钉钉身份、专业工作区 SSO、服务端任务身份和 Agent 附件底座，
   所有内部使用都归属于同一个 `internal_user_id`。

## 2. 不得改动

- 不修改 FAE 公网入口、生产容器或客户身份；
- 不修改行政 `/office/*` 路径和服务门户产品形态；
- Brain/Task Adapter 不把 Platform Cookie 或钉钉原始身份传给上游；`/office/*`
  浏览器身份只按既有受控 Cookie + 回环 Subject 接口处理；
- 不新增 Task Group 表；
- 不重写已有七个 Brain 工具；
- 不用现有 `/chat` 长请求包装成伪任务；
- 不给大脑确认业务操作的工具；
- 不把 `max_steps` 未经实测直接从 12 提到 24。

## 3. 阶段 0：P0 独立修复

### 3.1 实现

- `DelegateTaskCall` 增加必填 `capability_version: int > 0`；
- Tool Schema 和 Brain Prompt 要求从 `list_agents` 结果回传版本；
- `loop_runtime.py` 不再传常量 `1`；
- Runtime Registry 继续执行 optimistic capability check；
- 版本不一致返回 `capability_changed`，不创建 Task；
- 更新 Prompt SHA 和 Release Manifest。

### 3.2 测试

- 使用真实 `load_capability_cards()` 和 Runtime Registry，不使用只接受版本 1 的 Fake；
- HR 版本 2 的 delegate 能创建 Task；
- 版本 1 对 HR 被拒绝；
- `list_agents -> delegate_task` 版本传递完整；
- 重启后重放相同 Tool Call 不重复创建 Task。

### 3.3 提交和发布

该阶段必须形成独立 Commit、Release 和验收记录。不能和迁移 049 混合，便于快速回滚和
定位 Prompt Cache 首次失效成本。

## 4. 迁移 049

### 4.1 Schema

- 扩展 `agent_tasks.status`；
- 为 Task 增加暂停/恢复所需已消耗执行时间；
- 为 Loop 增加 `waiting_confirmation` 和 `intervention_expires_at`；
- 扩展 Conversation Turn 的状态 Check 和单活跃索引；
- 新增 `agent_task_actions`；
- 增加 Action Execution Delivery 的稳定身份和幂等约束；
- 扩展 Event/Wake 白名单；
- 增加确认、拒绝、过期、取代和 Action 执行的公开事件类型；
- 重新审计 Worker/App 的列级 Grant。

### 4.2 数据库函数

至少提供：

- `propose_agent_task_action_v49`：Worker 身份，幂等创建或取代 Action；
- `confirm_agent_task_action_v49`：App 身份，校验 Owner/Digest/Expiry 并创建执行投递；
- `reject_agent_task_action_v49`：App 身份，原子拒绝并唤醒；
- `expire_agent_task_actions_v49`：Worker/Reaper 身份，批量过期并唤醒；
- `supersede_pending_actions_for_intervention_v49`：普通用户消息恢复原 Turn；
- `append_agent_task_event_v49`：包含新增事件和任务状态映射。

函数必须 `SECURITY DEFINER`、固定 `search_path`、撤销 public 权限，并精确验证生产与
Preview caller role。

## 5. Runtime 与状态推导

- 状态推导集中在一个领域函数，不在 Route、Worker、Repository 多处复制；
- 只有无可执行 Step 且全部非终态 Task 等用户时，Loop 才暂停；
- 有其他可运行 Task 时保持活动计时；
- Task 自己的等待时间不计入 Task 有效执行时长；
- Action 超时唤醒大脑部分交付，不走整轮 `user_input_timeout`；
- 普通用户消息使 pending Action `superseded` 并恢复同一个 Turn；
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

## 7. 通用 HTTP Adapter

实现共用基座：

```text
HttpTaskAdapter
SignedTaskTokenIssuer
TaskEventCursorSynchronizer
TaskCapabilityProbe
```

基座负责：短时签名凭证、快速创建、幂等键、上游任务映射、游标同步、重试退避、健康、
协议错误和明确 unavailable。FAE 与行政只提供配置和事件映射，不复制可靠性代码。

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

## 9. 统一 Agent 附件底座

现有 Attachment 模块只有基于 Flywheel 元数据的预览/下载 Ticket，不是 Agent Conversation
的统一上传事实源。本阶段扩展为 Platform-owned Attachment Service：

- Metadata/ownership 位于 Platform 控制库；
- 原始 Blob 和派生物位于 Platform 管理的 MinIO；
- 浏览器上传、Agent 读取、Agent 输出、预览、下载、保留和审计统一；
- 原始文件默认保留一年，紧急擦除覆盖原件、预览、OCR/文本派生物和导出副本；
- 所有 Agent Conversation 上传组件复用同一 API；
- 上游 Agent 不取得 MinIO 凭据或长期预签名 URL，只使用绑定 Task 的短时读取 Grant；
- Agent 输出附件先上传 Platform，再通过 `attachment_ref` 进入结果。

任务上下文只包含用户明确选择且通过所有权检查的附件引用。Catalog 声明 MIME、数量、
单文件/总大小、图片视觉、文档文本和输出附件能力；能力不匹配显式返回
`attachment_unsupported`，不得静默丢弃。

新增 `platform_attachments` schema：attachments、uploads、bindings、derivatives、
access_grants、access_events。首版硬上限单文件50 MB、单消息10个、总计100 MB；只有通过
类型、大小、SHA-256、magic-byte 和安全扫描并进入 `ready` 的对象可以绑定。

统一接口至少覆盖：创建上传、完成上传、将附件绑定到 Message/Turn、签发 Task Grant、
按 Grant 流式读取、Agent 输出登记、用户预览/下载和紧急擦除。浏览器写接口使用 Platform
Session + CSRF；内部读写接口使用 audience/task/scope-bound Token。接口不得接收任意
Object Key，也不得允许调用方以 URL 指定上游文件。

适配路径必须覆盖全部内部 Agent：

- FAE/行政 HTTP Adapter 使用短时 Media Gateway；
- VOC 同进程仍执行相同 Task 授权；
- MetaBot Local Worker 通过 HTTPS + Task Token 下载到 `0600` 临时目录，任务结束删除；
- Agent 输出使用统一 Output Attachment API 回写 Platform；
- 任何 Adapter 不得获得 MinIO 凭据或长期 URL。

## 10. VOC 第一条确认链路

复用 `app/voc_extension` 的短时 Token 与 Draft/Submit 能力：

1. Brain 派发“整理 VOC”；
2. VOC Adapter 返回结构化 Draft；
3. 正式提交生成 `action_required` 和持久 Action；
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
- 生产 Step 仍为 12，先增加 12/16/24 评测工具和指标。

## 13. 前端

现有协作室增加：

- 新任务状态；
- ActionCard；
- Confirm/Reject 按钮及 CSRF；
- Expired/Superseded/Executing/Completed/Failed 展示；
- 真实确认身份、时间和 Digest 摘要；
- 预算不足、Agent unavailable、Scope denied 的明确提示。

禁止通过前端计时器伪造处理阶段，禁止从回答 Markdown 解析 Action。

## 14. 验收

- P0 真实 HR 委派；
- Reference Adapter 全状态机；
- VOC 六条确认路径；
- FAE 600 秒任务、断线游标恢复、追问、取消；
- 行政只读 Scope 越权拒绝；
- 行政写操作只在后续 capability version 启用；
- Worker 在 propose 后、confirm 前、confirm 后被杀时都不重复执行；
- 九 Agent 不静默消失，Mac 离线明确 unavailable；
- `/office/*` 和 FAE 公网入口不变。
- 钉钉登录一次后，VOC、行政和企业 FAE 入口均解析为同一个 internal_user_id；注销或
  停用后跨域 FAE Binding 不再有效；
- 图片、PDF和普通文档经同一 Platform 附件 ID 被 FAE 与至少另一个 Agent 成功读取，
  未授权 Task、错误 Agent、过期 Grant 和 MIME 不兼容均被拒绝。

## 15. 交付物

- P0 Commit/Release/验收证据；
- 049 Migration、权限矩阵和回滚说明；
- HTTP Task Contract v1 Schema；
- Workspace SSO/Task Identity 合同、威胁模型和身份贯通证据；
- Platform Attachment Contract、MinIO/Metadata 迁移、访问审计和一年保留策略；
- VOC/FAE/行政 Adapter；
- Action 前端与审计证据；
- 九 Agent 并发报告；
- 12/16/24 Step 成本报告；
- 尚未解决问题清单，禁止用 fallback 掩盖。
