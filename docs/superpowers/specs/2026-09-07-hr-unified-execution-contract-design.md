# HR 统一执行契约设计：AI-Agent-Platform 与 MetaBot 端到端

**日期：** 2026-09-07
**状态：** v0.3 复审修订通过，2026-09-07 Owner 指令“开始”；进入阶段 0 / P01 契约与兼容测试，不切流量、不发布、不查询未授权生产数据。其余阶段尚未实施。
**本轮已确认：** 未绑定飞书用户仅引导绑定，不建立访客执行体系；已有业务用户切换前完成身份映射核对。
**覆盖仓库：** `AI-Agent-Platform`（云端平台 + 本地 Relay Worker）、`metabot-dev`（MetaBot 运行时）、`Orbbec-Agent-Team`（Bot 配置、运行契约、部署脚本）
**取代关系：** 本文经最终评审通过后取代 `2026-09-07-hr-runtime-refactor-design.md` 作为 HR 运行底座的最高设计约束；该草案及 `plans/2026-09-07-hr-result-pipeline-refactor.md` 保留为历史与待裁决材料，不再作为施工依据。与 `2026-09-06-hr-local-intelligence-factory-design.md`、`2026-09-06-hr-agent-markdown-intelligence-design.md` 不冲突的约束继续有效。

---

## 0. 评审前提

本文区分四类陈述，并在正文中标注：

- **[业务]** 业务要求，来自需求文档与 P0 设计；
- **[现状]** 当前代码事实，附 `文件:行` 引用，引用基于 `AI-Agent-Platform` 工作树 `.worktrees/hr-position-core-availability`（HEAD `e55806e`）与 `metabot-dev` HEAD `fe5ad87`、`Orbbec-Agent-Team` HEAD `a6c028a`；
- **[设计]** 目标契约；
- **[待决]** 需要 Owner 决定的问题。

现有局部补丁（工作树上的 Task 1 到 Task 3）只作为待裁决材料，第 10 节逐项给出保留意见，不构成本设计必须继承的前提。

---

## 1. 现状事实：两条入口、三段传输

### 1.1 拓扑 [现状]

```text
网页浏览器
  → 云端平台 API（conversation_routes）
  → conversation_turns + missions（同事务）
  → API 进程内 leader 循环 advance_pending（main.py:323）
  → execution_jobs（云端）
  → 本地 Relay Worker 签名领取（execution_relay/routes.py /lease）
  → loopback POST /api/core-chat/runs（metabot_client.py:330）
  → MetaBot hr-bot 执行
  → MetaBot 回调 Worker 本地 HTTP（worker.py:1187）
  → Worker outbox 上传云端 /events、/terminal
  → leader 循环投影为 assistant 消息（conversation_projection.py:627）

飞书用户
  → 飞书长连接 WSClient（metabot-dev src/index.ts:153）
  → event-handler 归一化并 fire-and-forget（event-handler.ts:343）
  → handler 返回后 SDK 协议 ACK（本地 node-sdk 1.64.0）
  → MessageBridge 内存队列（message-bridge.ts:254）
  → 同一个 hr-bot 进程执行
  → 飞书卡片 create/patch（message-sender.ts:52-85）
  → Flywheel 事后只读副本进入平台（cloud_replica，SSH 单向）
```

两条入口只在 MetaBot 进程这一点汇合，在 Turn、Attempt、Result、Delivery 任何一层都不汇合。平台对飞书会话的全部认知是脱敏、假名化、一年后过期的加密副本（`cloud_replica/exporter.py:199-203`），无 turn/mission 关联，无身份关联。

### 1.2 网页入口 [现状]

| 事实 | 引用 |
|---|---|
| 提交强制 `Idempotency-Key`，`(conversation_id, client_request_id)` 唯一，单活跃 Turn 部分唯一索引 | `conversation_routes.py:315-325`；`036_agent_brain_conversations.sql:78,95-97` |
| 直接模式无规划模型调用；但长会话压缩 summary 阶段在模式分支前，可能触发一次 agent-brain-bot 调用 | `orchestrator.py:990`；`orchestrator.py:926-988` |
| 执行状态机在 API 进程 advisory-lock leader 循环内推进，缺少独立的逐任务推进租约；不等于整个 Relay 没有回收机制 | `main.py:308-338`；`orchestrator.py:572-595`；`repository.py:1341-1389` |
| SSE GET 每秒每流做两处 `for update` 加写库 | `conversation_routes.py:629-640` |
| 消息列表默认最旧 100 条升序，前端不分页，按列表扫描 `assistant_message_id` 判断终态 | `conversation_routes.py:960`；`conversation_repository.py:2045`；`conversationApi.ts:749`；`ConversationPage.tsx:152-162` |
| 每轮给 MetaBot 的 `conversationId` 是新生成的 `mission_id`，`executionChatId`/`taskSessionId` 随之每轮唯一；平台真实 `conversation_id` 从未传出 | `orchestrator.py:828,823-825`；`conversation_repository.py:818` |
| `userId` 固定为字面量 `"platform-user"`；协作模式下 `requesterSubject` 不传 | `metabot_client.py:276,316-320` |
| 运行期 300 秒无事件即判 `interrupted`，心跳只续租约不刷新 `updated_at` | `execution_relay/repository.py:631-642,879-884` |
| Worker 重启恢复不通知 MetaBot 取消；`leased/dispatching` 直接标 `interrupted` | `worker.py:566-607` |
| 取消在 relay job 已终态时静默不写任何东西 | `repository.py:1301-1334` |

### 1.3 飞书入口 [现状]

| 事实 | 引用（metabot-dev） |
|---|---|
| handler 未 await 持久接收且 catch 吞错；SDK 在 handler 返回后 ACK，因此早于可靠落库完成 | `event-handler.ts:343`；`src/index.ts:135-139` |
| 未发现业务级持久入站去重；不把 SDK 分帧缓存当作消息账本 | `event-handler.ts:328-370`；`src/index.ts:135-139` |
| 会话键仅 `chatId`，群聊所有用户共享一个会话；`union_id` 不参与路由 | `session-manager.ts:92`；`event-handler.ts:436` |
| 入站队列纯内存，容量 5，关停时清空；在途 turn 卡片不会被收尾 | `message-bridge.ts:254,1829-1856,4529-4569` |
| 初始卡片发送失败则整条消息被丢弃，无重试 | `message-bridge.ts:2568-2576` |
| 最终卡片 3 次重试后降级纯文本再放弃；活动与飞轮记录在投递失败后照常写 `completed` | `final-delivery.ts:10-52`；`message-bridge.ts:3122-3138` |
| 文字卡片的飞书 `message_id` 不持久化；只有附件回执落盘且仅在归档开启时 | `message-bridge.ts:2728`；`output-handler.ts:180-196` |
| 忙检查有竞态：`runningTasks.has` 到 `set` 之间跨越多个 await | `message-bridge.ts:1813` vs `:2733` |
| 旧 Session/上下文溢出恢复会用原 prompt 在新会话重放一次，有 `toolEffect` 门控（read_only 或 local_idempotent） | `session-corruption-recovery.ts:15-28`；`message-bridge.ts:3052-3079` |
| Flywheel 队列为有界内存缓冲，满或关闭即丢；`turn_id` 每轮随机，与飞书 `message_id` 无关联 | `src/flywheel/queue.ts:29-37`；`message-bridge.ts:2638` |
| 平台侧无飞书 `open_id`/`union_id` 到 `internal_user_id` 的映射；唯一桥接是钉钉目录按唯一姓名匹配 | `control_migrations/001_identity_security.sql:32-42`；`migrations/003_dingtalk_directory.sql:22-60` |
| 飞书附件无法成为平台附件：`attachment_id` 在脱敏层丢弃，`platform_attachments` 要求 owner | `cloud_replica/sanitize.py:509-527`；`064_conversation_attachments.sql:7-18` |

### 1.4 MetaBot core-chat 契约 [现状]

| 事实 | 引用（metabot-dev） |
|---|---|
| v3/v4 以 `(taskSessionId, messageSeq, sha256)` 日志去重，重放返回 `replayed` 不执行；legacy 模式不幂等，完成后重发同 `runId` 会再执行 | `core-chat-session-store.ts:39-72,116-132`；`core-chat-routes.ts:796-845` |
| 回调事件无 outbox：3 次固定 250ms 重试后静默丢弃；终态回调失败时 `drain()` 先抛出，兜底 `error` 事件永不发出 | `core-chat-routes.ts:31-32,287-338,716-745` |
| 重启后在途 run 无恢复、不标 `interrupted`、日志仍显示 `active`；平台重发同命令得到 `replayed` | 全库 grep；`core-chat-session-store.ts:269-278` |
| 取消无专用终态事件，以 `error: Task was stopped` 表达；下载/上传阶段取消返回 `canceled` 但 run 继续并可能再发 `result` | `core-chat-routes.ts:957-1032`；`message-bridge.ts:3956-3958` |
| v4 `citations` 恒空、`recovery` 恒 null；`result` 事件旁带内部 `state` 字段 | `core-chat-routes.ts:912-925,716-725` |
| 飞书 turn 与 core-chat run 共用同一 bridge、执行器池（20 槽、LRU 淘汰无在途保护）、熔断与预算；网关 turn 租约默认容量 1 且实例级 | `bot-registry.ts:57-68`；`executor-registry.ts:38,218-227`；`gateway-turn-lease.ts:173-226` |
| 输出文件必须在 `result` 之前完成上传；无 `outputWriteGrant` 时产文件直接失败 | `core-chat-routes.ts:655-680,709-716` |

### 1.5 已经正确、应作为共同前提的部分 [现状]

- 平台 Turn 提交幂等、`execution_jobs.run_id` 唯一、Worker 本地 outbox 与云端 `/events` 的 seq 连续校验（`worker_store.py:353-437`；`repository.py:408-451`）。
- HR 业务成果登记是独立、幂等、带租约的账本：`claim_hr_task_result_projection_v77` 与 `position_package_projection`，`projection_request_id` 确定性生成并作为下游 `client_request_id`（`071_hr_task_result_projection.sql:279-306`）。
- 岗位上下文每轮冻结并按 canonical hash 重放（`platform_hr.position_task_records`，`hr/task_context.py:416-420`）。
- MetaBot 重放前检查工具副作用（`tool-effect.ts:43-55`），并在拒绝重放时明确告知用户。
- MetaBot 外部可靠性控制面设计（Orbbec-Agent-Team `docs/2026-07-14-metabot-core-reliability-design.md`）禁止该控制面重放真实用户 turn；不能据此推导 MetaBot 执行器内部重试已被禁止或已共用预算。

---

## 2. 评审裁决与状态权威 [设计]

### 2.1 本轮裁决

| 编号 | 原稿阻断问题 | 修订决定 |
|---|---|---|
| R01 | 飞书 message_id 不是 UUID；空话题键可能重复 | 保留原始 ID，固定 UUIDv5 映射；单聊话题键用空串而非 NULL |
| R02 | 执行尝试与领取代次混用；cancel_requested 不在状态枚举 | attempt_no 与 lease_epoch 分离；取消是请求字段，不能释放活跃占位 |
| R03 | 稳定会话键不兼容 v4 direct、hash、失败 session | 新增协商的 v5，分开逻辑会话、业务命令、run；旧 v4 不改变语义 |
| R04 | result 发送失败再入 error，会产生双终态 | 唯一业务终态与 outbox 原子提交；网络失败只重投 |
| R05 | 204 又要求 JSON；gap 停发无法恢复；取消丢 outbox | v5 使用 JSON 连续游标回执、补发与保留取消终态 |
| R06 | 租约过期不能证明旧 Claude 已停；两层重放扩大预算 | 先核验旧执行停止；首轮迁移不自动重跑已派发任务，仅恢复原结果 |
| R07 | 文字仍被 MetaBot 上传阻塞 | 文字终态与冻结成果意图先入账，文件上传是独立持久作业 |
| R08 | 快照版本不存在；失败无 answer 永久轮询 | 新增 snapshot_version；一致只读事务；按 outcome 判终态 |
| R09 | 去 Mission 漏掉附件、071/077、088、摘要依赖 | 本轮保留数据兼容层，移走执行裁决；物理去表另案 |
| R10 | 飞书 Bot 全切与只接单聊矛盾；回滚会遗弃交付 | 以 HR 单聊路由范围和 epoch 切换，双向冻结并排空；群聊仍属旧路径 |
| R11 | 无绑定主体不能靠提示词实现访客隔离 | Owner 已确认：只引导绑定；不运行模型、不代建 owner |
| R12 | 轻量情报降级缺 analysis；候选人比较混入底座改造 | 按冻结 Bundle 补读有界摘要；候选人比较另列业务任务，不阻塞运行迁移 |

复审追加修订不改变 R01–R12，也不增加第 19 项任务：

| 编号 | 冻结前缺口 | v0.3 落点 |
|---|---|---|
| F01 | run 外的上行没有本地鉴权入口 | 3.1.1、3.4.1；P01/P07/M01/O02 |
| F02 | 附件和入站请求挤占执行 Relay 配额 | 3.1.2；P07/P08/O02 |
| F03 | deferred 没有唤醒、恢复与排队提示 | 3.2.1；P07/M01/O03 |
| F04 | P04 网页 fixture 错建推送 Delivery | 2.2 不变；P04 分别验证飞书 1 条、网页 0 条 |

### 2.2 权威维护者

| 对象 | 权威维护者及存储 | 单一事实 |
|---|---|---|
| Turn | 平台 Turn 服务；conversation_turns，关联冻结清单 | 接受的业务输入、来源、上下文版本、最终业务状态 |
| Attempt | 独立云端调度 Worker；turn_attempts | 执行尝试、当前持有者、执行结果裁决 |
| Result | 平台结果投影器；conversation_messages + platform_attachments | 文字、成果版本、绑定与引用 |
| Delivery | 投递服务；turn_deliveries + MetaBot 渠道回执 | 推送渠道是否收到结果；不等于人已阅读 |
| Position | 现有 HR 业务域；platform_hr 表 | 已确认岗位、候选人关系与版本 |

- API、Relay、MetaBot 不能分别裁决同一 Attempt；受认证传输只提交证据，由平台事务裁决。
- 文字完成、文件处理中、飞书投递失败可以并存。
- 网页采用拉取语义，Result 可查询即 available，不伪造 delivered/read 回执；不为网页创建推送 Delivery。
- conversation_result_deliveries 仅是成果绑定恢复记录，不是 Result 内容权威，也不是飞书发送账本。
- 普通咨询不要求 Position；既有 freeform 与岗位路径保留。冻结 hash 验证一致性，不替代“所需 JD/JR 是否真正入上下文”的验收。
- 本轮只迁 HR direct；其他 Bot 与 Brain 的执行模式、模型和共享配置保持不变。

## 3. 统一入口、身份与持久接收

### 3.1 沿用现有传输边界 [设计]

~~~text
MetaBot 飞书持久 inbox
  → 受认证 loopback 的 Relay Worker 入站代理
  → Worker 主动发起现有签名 HTTPS：resolve_channel / turn_intake
  → 云端平台解析身份、建立会话、提交 Turn

平台持久 channel_delivery
  → Worker 签名领取
  → 受认证 loopback MetaBot 渠道发送
  → Worker 签名上行 delivery_receipt
~~~

平台不直接入网访问 MetaBot，不向公网暴露 loopback。turn_intake 是上行幂等命令，不是等待平台执行的模型 job；channel_delivery 是独立投递 job。两者复用签名、重放保护与 Worker 信任边界，但不滥用 execution_jobs 的模型状态机。

签名证明 Worker 身份，不证明请求中的用户拥有权限。云端只按已核对的 tenant/app/bot、渠道主体映射确定 owner，拒绝客户端提供的任意 internal_user_id。接入允许范围仅 HR 实例；凭据不进入日志、事件或提示词。

#### 3.1.1 run 外的本地代理鉴权 [现状 / 设计]

[现状] `backend/app/execution_relay/worker.py:1139-1145` 仅接收 `/callbacks/{run_id}/{token}`；部署的 callback 端口为 9120（`deploy/local-execution-worker/execution-worker.ecosystem.config.cjs:32`）。该 token 不能用于还没有 run 的 intake，也不能用于执行结束后的渠道回执。

[设计] 在现有 127.0.0.1 callback listener 增加独立路由模块，不增加公网入口。固定前缀 `/hr-bridge/v1`，仅允许 POST 的 `resolve-channel`、`turn-intake`、`attachment-create`、`attachment-chunk`、`attachment-complete`、`delivery-receipts` 六个子路径。三个附件操作使用已解析的会话、既有上传 grant 和受限 attachment/upload 身份，不接受任意 URL、文件路径或 owner；receipt 只核对既存 operation/内容 hash/归属，不能自建 Delivery。

- 使用独立 HR 机器凭据文件，`Authorization: Bearer`，不复用 MetaBot API secret、Worker 云端私钥或每 run callback token。文件仅 `agentops` 所有、0600，直接父目录 0700；绝对路径、普通文件、拒绝符号链接，复用现有安全文件读取纪律。它约束的是受信任运行账号内的调用范围，不宣称能隔离同 UID 的其他进程。
- 凭据映射固定 `hr-bot` 与合同中的 HR appId；云端 Worker 授权范围进一步核对 tenant/app/bot。请求正文不能扩大这个范围。云端仍按既有签名、nonce、时间窗和身份绑定校验，不因 loopback 鉴权成功跳过用户授权。
- 仅绑定 127.0.0.1，并验证实际 socket peer 为 loopback，不信任转发头；不启用浏览器 CORS。缺失/错误凭据返回 401，越权操作返回 403，非法路径/方法拒绝。鉴权失败不调用云端、不下载文件、不发送消息。
- JSON 请求体上限 1MiB；附件 chunk 使用 raw bytes、单块上限 1MiB，并沿用现有总大小、MIME、hash 和上传授权限制。凭据不能出现在 URL、错误正文或日志。body 大小有界不等于可以任意分配内存并发。
- 本地重试复用入站键、上传块偏移/hash、operation_id 等业务幂等身份：同键同内容返回既有结果，同键异内容 409。云端 HTTP 每次重签新 nonce，不把网络重试当新业务输入。轮换凭据后未决 inbox/outbox 换凭据重传，业务身份不变；不新增本地签名协议。

具体合同字段见 3.4.1。P07 交付 listener/校验器，M01 交付持久 ingress 客户端，P08/M06 复用同一回执入口；只部署 M01 而缺 P07 入口时不得切换飞书流量。

#### 3.1.2 配额和调度隔离 [现状 / 设计]

[现状] `execution_relay/routes.py:71-99,150-166` 是每 API 进程、每 Worker 身份共享的 120 次/60 秒桶，并非跨进程全局限额。

[设计] 沿用现有限流器实现机制，按服务端确定的操作类别分桶，不能让客户端通过 header 自选桶，也不能在新桶前再套原共享 120 桶：

| 桶 | 操作 | 首版默认预算（每 Worker、每 API 进程） |
|---|---|---|
| execution | 既有 lease/heartbeat/events/terminal/stop 等执行 Relay 路由 | 保留 120 次/60 秒，不被新流量扣减 |
| channel_ingress | resolve-channel、turn-intake、绑定命令 | 60 次/60 秒 |
| channel_delivery | Delivery 领取、receipt/核对 | 120 次/60 秒 |
| attachment_ingress | 附件创建、分块、完成及该上传状态查询 | 120 次/60 秒，仍受附件域既有字节/用户配额约束 |

以上新增数值是设计初始配置，不是生产吞吐测量结论。429 返回 Retry-After；消费者持久记录下一次尝试时间，按该时间加抖动恢复，不清空输入、不回退本地执行。loopback 和云端均按上述类别保护入口；执行调度/HTTP 连接至少保留 2 个槽，渠道并发最多 2、附件上传最多 1，不能共用一个被附件占满的串行队列。

P07 验证耗尽入站/附件桶不会扣减 execution；P08 验证 receipts 在上述压力下仍可补账，并与实际执行 heartbeats/events 并行。沿用 process-local 限流意味着多 API 进程预算相加：O02 必须记录进程数、有效预算、连接槽和合成负载结果；不把它描述成全局限额，也不为本轮引入 Redis。若压力测试仍让执行心跳/终态饥饿，禁止切换，而不是盲目整体调高配额。

### 3.2 身份与入站去重 [设计]

- 原始渠道身份：(provider, tenant_id, app_id, bot_id, message_id)，message_id 为 text，保留独立唯一索引。
- client_request_id 使用 UUIDv5：namespace=UUID namespace URL；name 为 UTF-8 的 JSON 数组 ["hr-feishu-intake-v1", tenant_id, app_id, bot_id, message_id]，无空格、保留 Unicode、不拼接含歧义分隔符。固定测试向量须在 Python/TypeScript 两端一致。
- 同键同业务内容重投返回同一 turn_id；同键不同 canonical 输入 hash 返回 conflict，不覆盖旧请求。hash 排除重试时间、签名、短期 token，包含正文、原序附件身份与 SHA、业务主体和会话。
- channel_conversations 唯一键为 (tenant_id, app_id, bot_id, chat_id, thread_key)；单聊 thread_key=""，字段 NOT NULL，并发只能创建一个 owner 固定的 Conversation。
- 网页仍使用 UUID Idempotency-Key。绑定码消息独立识别为 identity 操作，不产生业务 Turn。
- 飞书已有 owner 的活跃 Turn 未结束时，后续消息保存在有序 inbox，返回 deferred；按本地持久 ingest_seq 排队。迟到事件保留渠道原时间，不假称跨断线严格全局时序。不得因“忙”丢消息或创建第二个活跃 Turn。
- 平台成功提交 Turn、用户消息、冻结清单引用、Attempt(queued)、初始可见事件必须原子完成。附件与情报不得在数据库事务内做网络请求。

#### 3.2.1 deferred 的唯一消费者与用户反馈 [设计]

- 本地 `IngressWorker` 是 inbox 转发者。`deferred` 返回 `blocking_turn_id`、`retry_after_seconds=5`；本地事务保存状态、阻塞引用、`next_attempt_at`。不新建云端 Turn，也不标成功转发；同幂等键核对时先返回已经接受的 Turn，再判断其他活跃 Turn，避免 ACK 丢失后被误排队。
- 每个 `(tenant, app, bot, chat, thread_key)` 按持久 ingest_seq 只领取最早未解决项；forwarding 有独立短租约，网络中不持有行锁。同 chat 前项接受结果未知或附件未解决时后项不能越过；其他 chat 通过 SKIP LOCKED 继续，避免全局队首阻塞。当前 Turn 终态但旧执行仍 reconciling 时继续返回 deferred，不绕过执行排他。
- 本地持久事务落入站项即唤醒消费者；loop 每 1 秒检查有界 due 队列（最多 20 个 chat，实际网络并发仍受 3.1.2 限制）。deferred 每 5 秒加 0–1 秒抖动按原键再提交；收到同会话终态线索时可提前唤醒，但只是提示，平台仍裁决是否空闲。无需新增“唤醒 job”或依赖通知必达。
- 进程启动立即扫描未决记录，之后每 30 秒补扫过期 forwarding 租约和漏掉的唤醒。上行超时保持结果未知，重启也先按原键核对，不重新编号消息。429 优先遵守 Retry-After；网络故障指数退避 1–30 秒，成功后恢复 5 秒 deferred 周期。不得对所有历史 inbox 每秒发网络请求。
- 首次 deferred 事务写一条幂等 `queued_notice` 意图，正常通道下 5 秒内尝试提示“已收到，HR Agent 正在处理上一条消息，随后处理这条。”只在第一次排队/实际状态改变时更新，不每次轮询发卡片。复用该输入的接收卡片：已有 message_id 才 patch；初始 create 回执未知先按 5.3 核对，不盲发新卡。初始 ACK、queued、final 的更新串行且 final 优先，迟到 queued 不覆盖最终答案。
- 排队提示发送失败与业务输入分别重试；卡片通道本身不可用时不能承诺用户已经看见，保留发送证据并告警。最终完成/失败都会使下一条重新接受检查；reconciling 不安全放行。低频“仍在处理”卡片更新不在本轮 18 项中，不能用心跳冒充有效工作进展。

### 3.3 身份绑定 [已确认 / 设计]

Owner 在本轮确认：未绑定用户只收到绑定引导，不进入访客执行，不伪造 owner，不访问业务附件或本地 HR 工具。

未绑定业务输入在绑定引导意图可靠入账后记为 `handled_without_turn`；不占用业务Turn，也不在以后绑定成功时自动重跑旧输入。绑定码走独立身份操作，不被活跃业务Turn阻塞；身份处理完成后同样显式结束该inbox项，避免未绑定首条消息永久堵住同chat队列。

优先核对已有可验证映射；缺失时使用网页登录态发起的一次性绑定码。码只存 hash，10 分钟过期、一次消费、单主体唯一绑定，错误尝试限流；绑定主体必须来自经过 SDK 验证的渠道事件，并由 Worker 签名传输。不得按姓名相同自动授权。

subject 使用 tenant/app/开发商作用域与 union_id（有值时）；可按明确 app 范围保存 open_id 别名，不能把不同 app 的 open_id 混用。关闭 Flywheel 后身份提取与绑定仍然有效。

切换前完成现有 HR 业务用户映射核对；这是避免退化的迁移检查，不增加“18 个授权员”之类业务权限概念。超出映射范围的新用户仅收到绑定引导。

### 3.4 ACK、入站卡片与附件 [设计]

MetaBot 使用现有本地 PostgreSQL 实例的新隔离 schema 保存 inbox/outbox，连接和数据目录按 HR 实例运行合同配置；不在 Release 写 SQLite，不新建数据库服务。选择理由是已有 PostgreSQL 运维，避免再维护第二种持久恢复机制。云端与本地是两个故障域，不假设跨库事务。

#### 3.4.1 本地持久配置合同 [现状 / 设计]

[现状] Team `deploy/metabot.runtime-contract.json:25-51` 未允许本地运行账本连接变量；`scripts/reliability/generate-ecosystem.mjs:11-49` 显式生成环境，仅改 allowedEnvironment 不会注入值。Flywheel 测试 `metabot-dev/tests/flywheel-integration.test.ts:8-12,77-81` 使用专用测试连接并可停止一次性 PG，不是可直接复用生产连接的许可。

[设计] schemaVersion=2 保持兼容，仅 `bots[name=hr-bot].instance.executionRuntime` 新增以下可选对象；启用 HR 新路径时四项均必填，其他 Bot 无此对象且不注入对应变量：

~~~json
{
  "databaseUrlFile": "/Users/agentops/AgentRuntime/instances/hr-bot/private/runtime-postgres-url",
  "databaseSchema": "hr_runtime",
  "relayBridgeUrl": "http://127.0.0.1:9120/hr-bridge/v1",
  "relayBridgeCredentialFile": "/Users/agentops/AgentRuntime/instances/hr-bot/private/relay-bridge-token"
}
~~~

| 合同字段 | 生成的 HR 环境变量 |
|---|---|
| databaseUrlFile | METABOT_RUNTIME_DATABASE_URL_FILE |
| databaseSchema | METABOT_RUNTIME_DATABASE_SCHEMA |
| relayBridgeUrl | METABOT_RELAY_BRIDGE_URL |
| relayBridgeCredentialFile | METABOT_RELAY_BRIDGE_CREDENTIAL_FILE |

allowedEnvironment 只增加这四个名称；PM2 和合同均只保存文件路径，不保存 DSN/密码/明文 token。MetaBot 新 loader 校验绝对路径、owner、0700/0600、无 symlink，读取 DSN 后仅允许经核对的本地 PostgreSQL socket/loopback 连接，使用专用最小权限角色限定 `hr_runtime`，不得采用 Flywheel ingest 角色或有权访问所有 schema 的超级用户。迁移角色和运行角色分离，运行进程不自行建库。

Worker 通过已有 `PLATFORM_METABOT_RUNTIME_CONTRACT` 读取同一个 HR bridge URL/凭据引用，不引入第二份可漂移配置；不读取 MetaBot 的数据库凭据。URL 的 host/port/prefix 必须与本地 listener 和当前 callback 端口匹配。校验失败则新路径报告 `hr_runtime_configuration_invalid` 并阻止切换，不切换到内存存储或原生模型兜底。

Team 的合同校验器、ecosystem 生成器和逐实例运行核对必须同步覆盖；`generate-instance-configs` 不将上述凭据/DSN 混入其他 Bot 的 bots.json。只读预检验证连接、角色权限、schema 版本、secret 文件元数据与配置指纹，不记录密钥值。本地 PG 数据仍在已有持久实例目录；此示例属于本地 macOS agentops 主机，不能照搬为云端 Linux 持久数据目录，云端仍强制 `/data/<application>/`。

M02 随 v5 store 交付新 `src/runtime/local-runtime-config.ts` loader、`local-runtime-store.ts` 和一次性 PG fixture；M01 复用该基础，不再各建一份。配置基础可凭 P01 fixture 先测，不等待飞书切换。Team 生产合同的定向生成、实例预检与应用属于 O02；M02 的执行正确性不依赖飞书在线。

#### 3.4.2 持久接收顺序

最前置 handler 只做基础可信事件校验与 inbox 持久写入。流程：

~~~text
收到有效 HR 单聊事件
→ 本地事务落 inbox + 独立“已收到/绑定引导”发送意图
→ handler 返回，由 SDK ACK
→ 后台解析/下载/查询身份/初始卡片发送/上行，互不阻塞可靠接收
~~~

[现状核验] package-lock 与安装的 node-sdk 都是 1.64.0。WSClient.handleEventData await dispatcher.invoke，异常返回协议 500，正常返回 200；本轮在无网络 SDK 探针中验证了两条分支。问题在业务 event-handler.ts:370 的 catch 吞错及 index.ts:136 的 fire-and-forget。新 handler 必须 await 持久写入，持久失败必须向 SDK 抛出，不在 handler 内无限阻塞重试。SDK 官方说明处理窗口为 3 秒；服务端实际重推仍需专用测试用户验收。

重复事件只 ACK，不重建卡片；初始卡片失败不阻止 turn_intake。初始卡片也用持久发送意图，避免 create 成功后进程退出导致重复卡片。

附件流程先 resolve_channel 得到云端解析的 owner/conversation 与限定上传权限，再经 Worker 代传到现有 user_input 接口，最后提交带平台 attachment_id 的 Turn。下载、解析失败保留原输入，明确提示失败，不静默丢文件而执行不完整任务。使用入站唯一键 + 原附件序号 + SHA 防重复；保留原序、类型和文件名，不能依赖 Flywheel 副本还原附件。

平台不可达时继续保存 inbox，不启用本地模型兜底。30 分钟是建议告警阈值，不是自动删除或无条件重跑阈值；终止等待应先与平台按幂等键核对是否已接受，不能仅因响应超时宣布未提交。业务材料默认一年；终态后可清理中间重复副本，但保留至少一年无正文幂等 tombstone；未解决 inbox/outbox 不按固定 7 天清除。

## 4. Attempt、租约、命令与恢复

### 4.1 不混用三类身份 [设计]

~~~sql
turn_attempts (
  attempt_id uuid primary key,
  turn_id uuid not null,
  attempt_no integer not null check (attempt_no > 0),
  executor_kind text not null, -- legacy_api_v1 / worker_direct；本轮不迁 Brain
  executor_id text,           -- queued 时为 null；领取后为进程实例 UUID
  lease_epoch bigint not null default 0,
  lease_expires_at timestamptz,
  status text not null,       -- queued / running / reconciling / completed / failed / cancelled / interrupted
  cancel_requested_at timestamptz,
  transport_run_id uuid unique,
  result_message_id uuid,
  retry_of_attempt_id uuid,
  reason_code text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  unique (turn_id, attempt_no)
);
-- queued/running/reconciling 始终属于一个活跃 Attempt；取消请求不释放占位。
create unique index one_live_attempt_per_turn
  on turn_attempts(turn_id) where status in ('queued','running','reconciling');
~~~

- attempt_no 只在确实新开一次模型执行时变化；领取、断线补传、投递重试不新建 Attempt。
- 同一 Attempt 每次接管递增 lease_epoch。裁决写入要求当前 attempt_id、executor_kind、executor_id、lease_epoch 全部相等，且租约有效；既拒绝旧代次也拒绝未来代次。
- 网络调用不持有数据库行锁。云端独立 Worker 用短事务 SKIP LOCKED 领取，租约续期、执行派发、结果收口与投递采用独立有界调度，长模型调用不得堵住心跳和其他收口任务。
- 租约失效进入 reconciling，先查询同 run 的持久证据；当前持有者可吸收经过认证的迟到事件，但旧写入者不能越过 fencing 直接改终态。
- Result 入账、Turn 终态、可见事件和后续投递/成果恢复意图同一事务；Delivery 的后续领取有自己的 lease_epoch，不依赖已经结束的执行租约。

### 4.2 取消、终态与重跑 [设计]

| 情形 | 裁决 |
|---|---|
| 派发前取消 | 原子取消 queued Attempt，不派发；允许无 answer 的 cancelled Turn |
| 派发后取消 | 记录 cancel_requested_at，转入停止/核对流程；请求本身不等于执行已停 |
| 完成与取消竞态 | 按持久终态证据裁决：已先提交的 result 保留并返回 too_late；已先提交 cancelled 则拒绝后续 result |
| 租约过期但结果已在 outbox | 新持有者恢复同 Attempt 结果，不重新执行模型 |
| 重启后旧 Claude 仍可能运行 | 进入 reconciling；验证所属执行器启动身份并确认退出，不能仅凭 PID、stop 返回或日志 closed 判断 |
| 不能证明旧执行已停/副作用未知 | 保持受阻并可见地说明原因；同会话不派发后续任务，禁止并行重跑 |
| 已停止且无有效结果 | 原 Attempt interrupted，失败证据保留；用户显式重试产生新 Turn，引用原 Turn |
| 普通网络重传 | 同 command_id/run_id/业务 hash，最多执行一次，只补回执或事件 |
| SSE/快照/投递恢复 | 永不创建 Attempt |

首个迁移版本不启用“租约过期自动重新执行模型”。现有 A+ 旧 Session 启动失败恢复仅在尚无有效输出/副作用、有完整证据时允许一次；重试预算由 Turn 级持久记录统一裁决。平台持有的 v5 执行必须显式获得 replay permit，MetaBot 不能另起一份内存预算；外部可靠性控制面仍不得重放业务消息。

toolEffect 跨尝试累积最强证据，不能在重建 stream processor 后变回 read_only；缺失为 unknown。传输幂等不等于端到端 exactly-once；外部副作用执行状态未知时宁可暂停核对，不自动重跑。

### 4.3 v5 命令契约 [设计]

新增协商版本 core_chat_collaboration_v5；不把取消、执行恢复或新会话语义偷偷塞入 v4。各端先部署双读协议，再对 HR 开启 v5 发送。

worker_direct 派发前检查 MetaBot 与 Relay 的 v5/持久终态接收能力。仅宣告 v4 时 Attempt 保持 queued，设置 reason_code=`executor_capability_missing`，快照明确提示“执行服务版本尚未就绪，请管理员完成升级；本轮未执行”，用户可取消；不创建 run、不向模型 POST、不降级 legacy。能力重查 30 秒一次，配置更新可唤醒，连续 5 分钟仍缺失产生运维告警而不自动失败/重跑。能力满足后清除原因并按同一 queued Attempt 派发。O02 原则上应在引流前阻止此状态；P03 仍须测试运行时能力退化，不能只靠发布检查。

- conversation_id：稳定的平台业务会话 UUID；principal_ref：服务器产生的脱敏 owner 引用。
- taskSessionId：稳定逻辑键 platform:{conversation_id}:{agent_id}，不是“每个 run 的生命周期”。
- command_id：一次执行命令的 UUID，与 attempt_id 一一对应；transport run_id 保持一次执行固定。
- turn_seq：业务 Turn 顺序，仅用于展示/审计；command_seq：MetaBot 逻辑会话内已分配命令顺序，独立持久分配。
- 重传相同 command_id 不消耗 command_seq；切换旧会话时新传输 session 从 command_seq=1 开始。新尝试用新 command_id 和显式 retry_of，而不是改旧 seq 的 hash。
- command_hash 包含冻结 prompt/context hash、逻辑身份、附件 SHA/顺序、工具权限 scope；不包含可轮换的 callback URL、短期 token 或租约持有者。回调地址更新必须独立认证，不能借“不进 hash”绕过 loopback 白名单。
- 命令 failed/cancelled/interrupted 只结束该命令，不封死整个逻辑会话。下一 Turn 可在旧执行确认停止后继续。
- v5 Wire 字段沿用 camelCase（commandId/attemptId/attemptNo/leaseEpoch/turnId/turnSeq/commandSeq/principalRef/contextMode）；Python仓储使用snake_case，通过显式alias映射。共享fixture固定两端序列化，不让不同计划自行命名。
- MetaBot 对执行器池中的活跃项禁止LRU驱逐；容量不足时在持久命令队列等待或在尚未接受前返回可重试背压，不能先接受再丢弃。重传同command仍只占一个队列位置，不能让全局网关租约的容量1变成随机执行失败。
- v4 recovery 保留搜索恢复语义；新增 executionRecovery，Wire字段为 evidenceComplete、累计 toolEffect、hasOutput、executorStopped、replayUsed，严格版本校验；执行器停止身份另带经核验的证明引用。
- v5携带platform turnId及run/command关联；Flywheel和Trace保留自身事件/trace身份并记录该关联，不能伪造为同一个ID。观测消费者从持久事件重放，使用固定事件幂等键；易失Flywheel队列不能再是唯一待写副本，观测故障仍不回滚业务Result。
- 首版 context_mode=frozen_prompt：平台冻结 prompt 是上下文唯一来源，逻辑会话稳定不等于盲目复用 Claude 隐式历史。旧 Session 缓存不得额外带入已排除材料。隐式历史复用/delta 输入是后续独立优化，不能同时发送完整历史又叠加旧历史。

### 4.4 运行信号与统计 [设计]

Worker 存活、run 活性、有效进展、总时长/成本是四个不同字段。新增 run_heartbeat（60 秒目标周期）不得伪装成用户可见工作进展；普通 state 不承担兼容性猜测。

阶段 0 只读统计必须按 agent/job_kind/协议/成功失败/事件类型分层，计算启动到首事件、相邻事件、末事件到终态的间隔，单列右删失的在途任务。报告样本量、时间范围与最大值，不把 P95 总耗时当 P95 静默间隔，也不把筛掉超时任务后的分布当完整基线。只输出聚合与脱敏 run 引用，不导出 prompt、简历或凭据。

本轮不改生产 300 秒值。新心跳能力需注入长工具停顿、心跳停但进程活着、Worker 离线三类故障验证；超时/预算数值以观测后的配置评审为切换前置，不能用“收到心跳”无限延长任务。

## 5. Result、文件与渠道投递

### 5.1 文字独立完成与成果恢复 [设计]

v5 在有效文字与冻结成果意图持久化后生成唯一 result；不等待网络上传完成。成果意图包含 task/owner/conversation、文件序号、SHA、MIME、大小与本地持久 spool 引用；不对外暴露本地路径。文件落盘失败只标该成果不可交付，不把已生成的文字改成模型失败。

平台 result 事务建立只允许声明清单的成果恢复意图。已登记上传可沿用现有授权；尚未登记但已在冻结清单的文件使用绑定该 Result/任务/哈希的独立短期成果权限，期限不得超过原授权截止和文字完成后 15 分钟的更早值，不重新开放任意新文件写入。失败/取消、明确撤销、其他 Bot 不享受该例外。

上传、病毒/格式处理（沿用当前内部附件规则，不引入新 ClamAV）、绑定就绪分别跟踪。文件成功上传后解析晚到 12 分钟，仍可在 ready 时绑定；上传 grant 过期不等于已经入库的成果应 abandoned。过期且未上传的意图标 expired，管理侧可在显式新授权后只重驱动文件，不重跑模型、不伪造旧 grant。

晚到 artifact 使用独立成果作业/事件，不在已封闭的执行终态序列后追加第二个结果。成果重试有退避、错误分类和限速；ready 事件加定期补扫避免漏通知，没有“每 5 秒对所有失败成果无限重试”。

文字超过原 8KiB 限制是 P0 用例；新 inline 上限建议 128KiB UTF-8，验证加密存储与回调大小。超过上限的完整结果持久为 Markdown 成果，正文给出明确摘要与完整文件状态，不静默截断或将有效任务误标执行失败。

### 5.2 回调 outbox [设计]

MetaBot 本地“唯一终态裁决 + outbox 事件”同事务提交。有效 result 已入账后，drain/HTTP 失败不得新建 error。未有终态的真实执行异常才能产生 error；cancelled 是专用终态。

v5 回调 ACK 为 HTTP 200 JSON：
~~~json
{"status":"accepted","runId":"uuid","acceptedThrough":12,"expectedSeq":13}
~~~
重复返回 status=duplicate；409 gap 返回 acceptedThrough/expectedSeq，发送方从持久 outbox 补发缺失事件；缺失事件本地也不存在则持久标 protocol_gap，隔离该 run 并告警，不跳游标、不堵其他 run。409 conflict 隔离不盲重试。

旧 v4 的 204 只在 v4 分支兼容；204 不可能同时承载 JSON。run 取消后停止模型执行，但前序已入账事件和取消终态仍须传完。原 Worker 强制终态时删除 outbox 的代码不得用于 v5；保留证据，使用显式 superseded/reconciled 处置。

指数退避加抖动 250ms 起、上限 30 秒，按 run 隔离。未确认终态不按次数删除；不可恢复协议错误停自动发送并保留证据。重启扫描必须先核对/停止所属旧执行器，再决定 interrupted；outbox 已有 result 则只补送该 result。

### 5.3 飞书 Delivery [设计]

turn_deliveries 仅表示渠道推送；channel=feishu。字段包括 delivery_id、turn_id、outcome_message_id、channel_target_ref、content_hash、route_epoch、status、lease_epoch、lease_expires_at、next_attempt_at、receipt_ref、created_at；唯一 (turn_id, channel, target_ref, purpose, part_no)。purpose 区分最终文字、文件与失败通知；大结果可确定性拆分，全部 part 保留顺序与独立幂等键。

status 为 pending/sending/delivered/failed/receipt_unknown；人工核对用 receipt_unknown + reason_code，不发明未在 schema 的 failed_unknown_receipt。结果写入与 pending Delivery 同事务，失败 Turn 可用 system outcome 消息通知，不能受 result_message_id 非空成功限定。

- 本地 feishu_outbox 先持久发送意图与 payload hash，再调用 API；收到 message_id 后落盘并补上行回执。
- 已知 card_message_id 的 patch 是确定目标的覆盖写；按内容版本串行，不允许迟到进度覆盖终态卡片。
- create 使用 UUID 格式 operation_id；同一发送意图的重试保持内容、目标和 uuid 不变。初始卡片与最终新建卡片必须不同 operation_id。
- 官方 Go SDK 当前 CreateMessageReqBody.Uuid 注释确认 1 小时去重；它不是无限期 exactly-once。长度上限尚未取得可读官方约束，首版使用 36 字符 UUID，受控实测通过才切换。
- create 成功但响应丢失，不能按一个未知 message_id 去 get。只在已验证的去重窗口内重投同 uuid；拿到重复响应/回执后补账；超过窗口或不能确认则 receipt_unknown，禁止自动 create 新消息。
- 有已知 message_id 时才可查询并核对目标、内容版本/hash。人工超时告警建议 10 分钟，不等于改写执行失败。
- 以显式 route_epoch/cohort 标记哪些 Turn 属于新投递路径，不用 created_at 时间戳猜归属。已经接受的 Delivery 在回滚后仍必须收尾，不能简单停止创建而遗弃结果。

## 6. 只读快照与业务上下文

### 6.1 一致快照 [设计]

新增 conversations.snapshot_version bigint，所有对业务可见的 Turn、Result、成果就绪、投递变化都在同一事务递增该版本并写 conversation_events。执行心跳不必逐次成为公共版本。版本分配按同一 conversation 串行；不复用不存在的 conversation.row_version。

~~~text
GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/snapshot
GET /api/v1/conversations/{conversation_id}/turns/current/snapshot
{
  read_version, event_cursor,
  turn: {...} | null,
  attempt: {...} | null,
  outcome: {terminal, kind, reason_code} | null,
  answer: {message_id, role:"assistant", content, completed_at} | null,
  result_enrichment: {status, pending_count, failed_count},
  deliveries: [{channel, status, receipt_ref}],
  context_manifest_ref
}
~~~

所有字段在一个只读 REPEATABLE READ 事务读取，读取 owner、answer、版本和事件游标时不得跨事务。响应上限受 inline 限制，文件正文和情报全文不内嵌。SSE 只读同一公开事件流，after 游标可重放；GET、重连、刷新不得更新执行状态或发模型请求。

成功 completed 必须带已提交的非空 answer；failed/cancelled/interrupted 可以没有 answer，以 outcome 结束执行轮询。合法空会话 turn=null、outcome=null 直接空闲。系统失败提示不伪造 assistant。执行 settled 每个 turn_id 一次；成果与 Delivery pending 可独立刷新，不锁输入、不重跑模型。

新历史接口 GET /messages/page?before_seq=&limit=50：不传 before_seq 取最新页，返回 items 按 seq 升序、next_before_seq、has_more。before 为严格小于，按 keyset 向上翻，不使用 OFFSET。旧 /messages?after= 保持兼容；恢复当前任务只读 snapshot，不遍历全部历史。

前端只保留可中止请求、单个有界订阅/轮询、丢弃旧 read_version、按 message_id 去重；不再拼装多个互不一致的终态来源。必须测 120 条消息、初始页重叠、派发前取消、无结果失败和空会话，不以“换接口自然消失”代替测试。

### 6.2 保留业务能力，阻断无关扩张 [设计]

官网 JD/JR、用户启用材料、候选人范围、情报引用继续按既有冻结清单处理；回归要检查实际发出的 prompt/grants，不只检查表存在。附件 display_name/MIME 复用已有 grant 字段，不额外复制正文。

轻量情报记录只在 Markdown 缺失或校验失败进入结构化降级时缺 analysis；正常 Markdown 路径可用。修复必须按已冻结 bundle_id 读取有界结构化分析/聚合，不改读新的 current bundle，也不把完整大报告重新塞回首屏。找不到可核验证据则明确降级且主对话可用。

candidate_comparison 的 candidate_ids[] 属于现有业务能力补齐，另列有界 TDD 项；不与数据库执行迁移绑成一个大任务。本轮优先保住对话→JD/JR→确认入库→简历分析→面试题/PDF 的既有路径。

## 7. 跨仓库范围、依赖与退出

### 7.1 两种 Worker 不混淆

云端执行协调 Worker 访问平台 PostgreSQL，负责 Attempt/Result/Delivery；本地 Relay Worker 保留现有签名桥与本地 outbox。云端 API 被停止后，云端 Worker 可提交已持久的结果，但新 Relay 上行仍需云端接收 API 恢复。

因此进程验收必须分成：杀 API 时模型继续且本地终态不丢；API 恢复后上传并提交 Result。不得宣称“唯一接收 API 完全停机时，未上传结果仍能立即穿过它入云端”。

### 7.2 发布依赖而非大批量一次上线

| 阶段 | 最小交付 | 前置及允许切换 |
|---|---|---|
| S0 | 审定契约、统计方案、SDK事实、基线与TDD计划 | 文档阶段；不连接生产、不实现 |
| S1a | 平台兼容读取 v5、Attempt/快照及只读投影基础 | 旧入口继续服务；不发送未支持的新协议 |
| S1b | MetaBot v5 outbox/恢复/命令身份/文件拆分；Relay 双协议 | 与 S1a 按冻结 fixture 并行开发；不开 HR v5 流量 |
| S2 | HR 网页灰度切到独立 Worker，替换前端恢复逻辑 | S1a+S1b及真实故障注入通过；无证据不自动重跑 |
| S3 | 身份核对、持久 inbox、上行与 Delivery，HR 单聊切换 | S2 + 飞书受控实测；群聊仍留旧路径，不进平台 owner 数据 |
| S4 | 关闭 HR 已迁移范围的旧领取与读侧写入，完成回滚演练 | 在途与 outbox 对账；其他 Bot/Brain 不变 |

共享 runtime contract 只做兼容检查和 HR 范围配置；不能因检查八个 Bot 就改写八个 Bot。本轮运行当前平台 MetaBotRuntimeMap.from_contract 读取Team合同返回accepted，因此原稿“当前不一致”不成立；该核验不代表生产配置无漂移。

### 7.3 旧路径退出表

| 路径 | 停止条件 | 在途归属 | 兼容与回滚 |
|---|---|---|---|
| HR API V1 领取 | HR conversation.execution_owner=worker_direct；旧谓词必须 JOIN 会话归属 | 旧 Mission 在旧 owner 收尾；新队列只由新 owner 领取 | 双向切换均先暂停接收、确认无活跃 Attempt/Mission与遗留执行，再递增 route_epoch；不能只翻配置 |
| SSE 投影写入 | 独立投影器已覆盖两种 owner，回放验收通过 | 持久游标可继续读 | 回滚只到同样只读的兼容版本，不恢复“GET 推进业务” |
| 前端扫描消息终态 | snapshot 读契约与分页已验收 | 无模型任务迁移 | 回滚保留 snapshot，不能回到已知 100 条失效版本 |
| 固定次数成果绑定 | 新恢复消费者接管已验证 scope 的 pending/failed | 原 Result/task 保持不变 | 重开不等于重新授权，不批量绕过 grant |
| 飞书原生 HR 单聊 | 持久路由记录 (bot, chat_scope, epoch) 标 platform | 切换前 inbox、模型在途、未确认发送逐项对账 | 冻结上行与新执行后排空/明确保留旧 Delivery消费者；不得回滚时重执行旧 inbox |
| HR 群聊 | 本期不迁 | 明确留 metabot 原路径，不转平台 owner | 单聊/群聊路由矩阵排他；不全局关闭 Bot handleMessage |
| legacy/v4 core-chat | 仅 HR 新 owner 发送 v5，旧在途按旧协议结束 | 每条命令固定协议 | 不能把已经接受的 v5 命令降级成 legacy 重发 |
| 每轮临时会话键 | v5 身份/失败后继续/历史切换测试通过 | 旧 session 不直接导入新隐式历史 | 以路由 epoch 新建映射，旧 run 可查询但不重执行 |
| Mission direct 状态权威 | S2 起新 owner 不由旧 orchestrator 裁决 | Mission/task 只作授权/投影兼容记录 | 本轮不物理删 Mission；071/077、064、088、摘要依赖须显式保留并测试 |
| 历史飞书只读副本 | 新 Turn 以源 message_id 显式关联 | 历史失败原样保留 | 不自动重发历史消息；Flywheel/Trace为独立投影，不能阻塞业务 Result |

Mission 兼容层：新 owner 的 Turn 事务可以建立已有 Mission/task 关联以满足 064/071/077 的授权 FK，但唯一推进者是独立 Worker；旧 API 领取必须排除它。076 本身由 Turn/Message 驱动，保留，不强行改成 Attempt claim。物理去 Mission、统一全平台 Brain 与新 task 表是另案，不把它们作为 HR 本轮可用的前置。

## 8. 已确认、建议默认值与待验证

| 项目 | 状态 | 裁决 |
|---|---|---|
| 未绑定用户 | 本轮 Owner 已确认 | 仅引导绑定；已有用户切换前核对 |
| 群聊 | 本期设计建议 | 只迁单聊，群聊保持原路径；不假定平台单 owner 可覆盖群成员 |
| 平台不可达 | 建议默认 | 30分钟告警，不自动清除或本地执行；终止前幂等核对 |
| 无进展超时 | 待观测 | 不改生产值；事件间隔报告与新心跳故障实验后定配置 |
| Mission | 本期设计建议 | 保留数据兼容层，退出 HR 执行权威；物理拆除另案 |
| SDK ACK | 本地已验证 | 1.64.0 handler 返回200、抛错500；云端重推需真实测试 |
| create uuid | 官方源码确认去重窗口 | 1小时；使用36字符UUID，长度/重复响应受控实测未完成 |
| 自动恢复 | 设计约束 | 先恢复原结果；不新增租约过期重跑；A+共享一次持久预算 |
| 长结果/留存 | 设计建议 | inline 128KiB，超限完整Markdown；材料1年，未决outbox不提前清除 |
| 入站鉴权与流量隔离 | v0.3 冻结修订 | HR 专用凭据；按操作分桶；新桶预算须受控负载验证 |
| 排队续送 | v0.3 冻结修订 | 每 chat FIFO、5秒 deferred 重查、重启补扫、一次性排队提示 |
| 本地 PG 配置 | v0.3 冻结修订 | executionRuntime 文件引用与逐实例环境校验；不复用 Flywheel 运行身份 |
| O01 生产统计 | 尚无执行授权范围 | 时间窗、表/列、目标实例需明确后才查询；本轮不连接生产 |

## 9. TDD 与验收边界

实施拆解见同日 hr-unified-execution-tdd 计划索引；各仓库文件路径明确标出现存/新增。计划是待设计评审的测试先行任务，不代表已执行。

每个任务遵守：写失败测试→运行并记录预期失败→最小实现→同用例转绿→相关回归→检查diff→限定文件提交。每个可上线阶段必须另有进程级故障注入；mock 绿色不能替代真实飞书收发、文件下载、Flywheel assistant、非空 Trace 和云端同步验收。

必须覆盖：重复入站、同键异载荷、停止旧执行器失败、过期/未来 epoch 写入、唯一终态、ACK丢失与gap、晚到文件、失败无answer、120条消息、上下文实际注入、API/Worker分别死亡、回滚中在途和投递收尾。

生产数据与SDK API实测尚未执行；只在后续经明确范围核对的阶段做只读统计或专用测试账号验收，不触碰真实业务消息。发布遵守 /data staging、根盘25GB/预计20GB/使用率75%、当前+两个回滚、精确清理和本应用边界，不改共享Nginx或其他Bot。

## 10. 现有补丁与非目标

| 内容 | 保留裁决 |
|---|---|
| cd50c02 Relay 接收契约 | 保留并补 v4/v5 词表分离；不丢身份和seq校验 |
| e36ea65 文字与绑定事务拆分 | 保留机制，按 Result/文件恢复契约重做调度 |
| b2d3422 已登记上传收尾 | 保留其授权边界；v5冻结成果意图另测，不能直接扩大例外 |
| Task 3 前端恢复补丁 | 不再投入旧扫描路径；只迁移正确的不回退、不重POST原则及回归测试 |
| 本分支迁移088 | 当前 master 最新087；088只在本分支，不假设已上线。实施前按届时 master 与已应用清单分配精确编号；未应用才可重写，否则新增后续迁移，不能预占089 |
| 994207f 轻量情报 | 精确修复缺analysis与降级的组合路径，不否定正常Markdown路径 |
| 09-07旧草案/计划 | 历史材料，不再推进；未提交内容保留，不删不改用户其他工作 |

不引入 Kafka、向量库、访客执行、新的审批/ATS流程或全量重建。不开生产情报采集，不升级 Claude 代替恢复设计，不同时改其他应用。本文及TDD计划通过最终评审前，不开展实施和发布。

## 11. 外部与本地核验来源

- SDK锁版本：metabot-dev/package-lock.json:1532，node_modules/@larksuiteoapi/node-sdk/lib/index.js:85764（1.64.0安装副本）；本地无网络探针结果 handler returns→200、throws→500。
- [飞书官方 Node SDK 长连接说明](https://github.com/larksuite/node-sdk)：handler处理窗口、重推与长连接语义；仅作为传输说明，不保证本应用已可靠落库。
- [飞书官方 Go SDK 消息模型](https://github.com/larksuite/oapi-sdk-go/blob/v3_main/service/im/v1/model.go)：CreateMessageReqBody.Uuid 的1小时去重说明。网页文档正文未能被工具读取，不宣称核实了最大长度。
