# Agent 大脑业务确认与外部专业 Agent 接入设计

**日期：** 2026-08-27

**状态：** 待书面评审

**基线：** AI-Agent-Platform `origin/master@40d2c9d`；AI-ADMIN-Agent `e95e274`；AI-FAE-Agent `origin/master@7302821`

**范围：** Agent Brain V2 的能力版本修复、任务状态扩展、用户业务确认、VOC/FAE/行政接入、统一身份、统一附件和协作室增量

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
- 无任何静默换 Agent、换模型、降级到一次性聊天或模拟进度的路径。
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
- 版本不一致：不创建任务，返回 `capability_changed`；
- 大脑必须重新 `list_agents` 后再派发；
- 已创建任务不因后续 Catalog 升级被杀掉；
- 真实授权变为 deny 时，仍以 `authorization_changed` 终止相关 Loop。

### 4.3 发布边界

该修复包含 Tool Schema、系统提示词和 Prompt Hash 更新，会使首次 Prompt Cache
失效。它必须独立提交、独立发布，并用真实 Catalog + Runtime Registry 证明版本 `2`
能够创建 HR 任务；不得只用 Reference Adapter 验证。

## 5. 数据模型：迁移 049

迁移编号 `049` 已核对未被本地或远端引用占用。实施前仍需再次确认主线。

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

### 5.2 不新增 Task Group

一次并行派发批次由同一个 `step_id` 唯一标识。同一 Step 内 Tool Call 已有
`(step_id, tool_index)` 稳定顺序，`agent_tasks.brain_tool_call_id` 唯一。前端可以
把同一 `step_id` 投影为“本批任务”，数据库不新增 `task_group_id` 或任务组表。

### 5.3 Action 表

新增 `platform_brain.agent_task_actions`，至少包含：

```text
action_id uuid primary key
task_id uuid not null
action_seq integer not null
action_kind text not null
summary text or encrypted projection
impact text or encrypted projection
parameters_ciphertext bytea not null
parameters_key_version integer not null
action_digest bytea not null check octet_length = 32
status pending | confirmed | rejected | expired | superseded
expires_at timestamptz not null
owner_internal_user_id uuid not null
confirmed_by_internal_user_id uuid
confirmed_at timestamptz
execution_timeout_seconds integer not null
execution_status not_started | queued | running | completed | failed
execution_deadline_at timestamptz
created_at / updated_at / terminal_at
unique (task_id, action_seq)
```

`action_id` 使用稳定派生：`uuid5(task_id, "action:" + action_seq)`。同一 propose
重放不得生成第二条 Action。

### 5.4 Action 状态不变量

- 只有 `pending` 可以确认、拒绝、过期或被取代；
- `confirmed`、`rejected`、`expired`、`superseded` 均不可反向变回 pending；
- 同一任务的新参数 proposal 必须把旧 pending Action 转为 `superseded`；
- Loop 在 Action 尚 pending 时终止，所有 pending Action 转 `expired` 并请求停止任务；
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
- 普通文本会把该 Turn 内所有 pending Action 转为 `superseded`，再由大脑处理新要求；
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

## 8. HTTP Adapter 标准合同

FAE 和行政的内部任务面采用同一语义合同，但不要求共享代码仓库。VOC 可在 Platform
进程内实现同一 Adapter 接口。

### 8.1 必需能力

```text
POST   /internal/platform/v1/tasks
POST   /internal/platform/v1/tasks/{task_id}/messages
GET    /internal/platform/v1/tasks/{task_id}
GET    /internal/platform/v1/tasks/{task_id}/events?after={seq}&limit={n}
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

### 8.3 幂等和事件

- 创建任务按 Platform `agent_task_id + idempotency_key` 幂等；
- 后续消息按 `task_id + message_seq + idempotency_key` 幂等；
- 取消重复调用返回相同结果；
- 事件 `seq` 从 1 开始、严格单调、支持 `after` 重放；
- 终态不可逆；
- 相同幂等键配不同 payload 返回 `idempotency_conflict`；
- 上游任务 ID 只作为加密映射，不能反向决定 Platform Task 状态。

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
authorized_scopes
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

双方使用同一 Canonical JSON：UTF-8、键排序、固定分隔符、禁止 NaN/Infinity、禁止
隐式浮点格式变化，再计算 SHA-256。Platform 保存的 Digest 是确认卡权威；上游执行前
独立复算。世界状态变化导致参数失效时返回业务冲突并重新 propose，不得自动换班次、
房间、客户或其他参数。

## 10. 统一 Agent 附件底座

### 10.1 Platform 是企业 Agent 附件事实源

所有企业内部 Agent Conversation 的输入图片、文档和输出附件统一由 Platform 管理：

```text
Browser / Agent output
  -> Platform Attachment API
  -> ownership + metadata + sha256 + classification
  -> Platform-managed MinIO
  -> task-scoped access grant
  -> FAE / HR / Marketing / VOC / 行政
```

现有 Platform Attachment 模块主要是 Flywheel 附件的只读 Ticket/Streaming Proxy，不能
直接作为 Conversation 上传事实源。本次在保留既有预览兼容的同时增加 Platform-owned
Attachment Metadata、上传、Task Binding、内部读取和 Agent Output 能力。

### 10.2 统一能力范围

- 浏览器上传、分片/流式写入、大小和 SHA-256 校验；
- MIME 与 magic-byte 双重校验，拒绝可执行内容和类型伪装；
- 图片尺寸/格式元数据、文档文本提取、PDF 预览和可选 OCR 派生物；
- 原件、派生物、访问审计和一年保留；
- Conversation/Message/Turn 所有权；
- Task 范围内最小授权；
- Agent 生成附件回传；
- Owner 紧急擦除覆盖原件、缩略图、OCR/文本、导出副本和未过期 Task Grant。

Platform 硬上限首版固定为单文件50 MB、单消息最多10个、单消息合计最多100 MB；Catalog
可以为每个 Agent 设置更低上限，不能提高 Platform 硬上限。上传状态为
`pending -> scanning -> ready`，检测失败进入 `quarantined`，擦除或保留到期进入
`deleted`。只有 `ready` 附件可以绑定消息或任务。

控制库新增独立 `platform_attachments` schema：

```text
attachments
attachment_uploads
attachment_bindings
attachment_derivatives
attachment_access_grants
attachment_access_events
```

这组表使用独立于 049 Action 状态机的后续迁移。迁移编号在阶段 0 合并后按主线下一个
可用编号确定，不能把附件数据面塞进 049，也不能放进只读的 `platform_replica`。

Blob Object Key 使用随机不可猜值，不包含用户名、花名、原始文件名、Conversation ID 或
钉钉身份。文件名仅作为加密元数据保存，日志不记录查询参数、文件名和 Object Key。

### 10.3 统一接口边界

附件服务至少提供以下语义，不要求把对象存储协议暴露给浏览器或 Agent：

```text
create_upload -> upload bytes -> complete_upload
bind_to_message / bind_to_turn
issue_task_grant -> stream_media_with_grant
register_agent_output
preview / download
emergency_erase
```

浏览器写接口使用 Platform Session + CSRF；内部 Agent 读取和输出接口使用
audience/task/scope-bound Token。完成上传必须由服务端重新计算字节数、SHA-256、MIME 和
magic-byte，不能相信浏览器声明。任何接口都不得接受调用方指定的 Object Key、MinIO
Endpoint 或任意远程 URL。

### 10.4 Task 访问

Brain 只把用户明确选择、属于该 Conversation 且通过授权检查的 `attachment_refs` 放入
`delegate_task`。Platform 为每个 `(task_id, attachment_id, agent_id)` 签发短时读取
Grant。专业 Agent 通过 Platform 内部 Media Gateway 流式读取，不能获得 MinIO Access
Key、Object Key 或长期预签名 URL。

Grant 必须绑定 Task、Agent、Audience、用途、最大读取次数/字节数和到期时间。每次打开、
完成、范围读取、失败和过期都写审计。任务终止、授权撤销或附件擦除时 Grant 立即失效。

### 10.5 Catalog 附件能力

附件不再用一个模糊的 `supports_attachments` 表达，Catalog 至少声明：

```text
accepted_attachment_mime_types
max_attachment_count
max_attachment_bytes_each
max_attachment_bytes_total
supports_image_vision
supports_document_text
supports_attachment_output
```

所有 Agent 复用同一存储、权限、生命周期和传输底座，但可以声明不同的处理能力。能力不
匹配返回 `attachment_unsupported`；不得静默丢弃文件或只把文件名传给模型。

### 10.6 FAE 与其他 Agent

FAE 企业任务直接消费 Platform Attachment Grant，并把图片送入现有视觉路径、文档送入
现有附件解析路径。FAE 面向外部客户的上传仍由 FAE 自己管理，不强制外部客户使用企业
Platform。

HR、Marketing、VOC 和行政逐个接入相同 Grant/Result Contract。行政门户中的住宿证明、
反馈凭证等领域业务附件仍属于对应业务记录；当它们进入 Agent Conversation 时必须通过
显式授权引用或复制到 Platform Attachment，不允许直接暴露业务存储路径。

MetaBot 本地 Worker 使用同一 HTTPS Media Gateway 和 Task Token 下载附件到权限 `0600`
的任务临时目录，任务结束立即删除。它不得把附件长期写入 MetaBot SQLite 或共享目录。
VOC 在 Platform 进程内也必须经过同一 Task 授权检查，不能因为同进程而绕过所有权。

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

生产 `max_steps` 暂时保持 12。先监控并在 Dev 测量 12/16/24 Step 的最坏 Token、上下文
增长、连续 Step 缓存命中和等待恢复缓存命中，再决定发布值；24 只是候选上限。

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
阶段 1  迁移 049：状态机、Action、确认、唤醒、权限、恢复
阶段 2  Platform 统一身份与附件底座；Reference Adapter 验证全状态机和附件授权
阶段 3  VOC：propose / confirm / reject / expire / submit
阶段 4  FAE：企业 SSO、持久任务、附件/图片、事件重放、追问、取消、600 秒预算
阶段 5  行政只读：复用 Jobs、短时身份、Scope、追问、取消
阶段 6  行政写操作：在 VOC 确认机制验收后开放
阶段 7  九 Agent 并发和 12/16/24 Step 成本评测
```

FAE 和行政由各自仓库的专属会话并行实施，但必须先冻结本设计的内部协议版本。Platform
Adapter 不得在上游尚未通过合同测试时声称 Agent 可用。

Workspace SSO 底座随阶段 2 建立；VOC/行政复用现有同源身份，FAE 的跨域一次性授权码
交换随阶段 4 验收。

## 15. 验收门槛

### 15.1 P0

- 真实 Catalog 版本 `2` 的 HR Agent 能创建 Task；
- 旧版本派发返回 `capability_changed` 且不创建 Task；
- Catalog 升级不杀掉既有 Task；
- 授权撤销仍明确终止 Loop。

### 15.2 Action

- 不确认、确认、拒绝、超时、Digest 变化、重复确认六条路径；
- propose 后、confirm 前、confirm 后三个崩溃点；
- 确认后即使 Brain 预算不足，操作仍进入独立执行窗口；
- 新普通消息使 pending Action 变 `superseded` 并恢复原 Turn；
- 非 Owner、过期、错误 Digest、终态 Loop 全部拒绝；
- 上游参数复算失败绝不执行。

### 15.3 Adapter

- 创建请求快速返回，45 秒租约内保存映射；
- 重试不产生第二个上游任务或业务写操作；
- 事件游标断线续传无重复、无缺口；
- 后续消息与取消可幂等重放；
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

### 15.6 统一附件

- 同一 Platform 图片附件能被 FAE 和至少一个其他 Agent 通过各自 Task Grant 读取；
- 同一文档附件无需重复上传即可委派给两个获授权 Agent；
- 未授权 Task、错误 Agent、过期 Grant、附件擦除和 MIME 不兼容全部明确拒绝；
- Agent 输出附件归档回 Platform，并能在 Conversation 中预览/下载；
- 上游日志、事件和结果不暴露 Object Key、本地路径或 MinIO 凭据；
- 一年保留和紧急擦除覆盖所有原件与派生物。

## 16. 发布和回滚

- 阶段 0 独立发布，失败只回滚 Tool Schema/Prompt/Runtime 修复；
- 049 先在 Preview 数据库做全量迁移和 Grant 审计；
- Catalog 双模式只在对应 Adapter 合同通过后逐个开启；
- FAE 公网 `/chat`、`fae.orbbec.com.cn`、原 IP 保持不变；
- 行政 `/office/*`、服务门户和既有 Jobs 保持兼容；
- 回滚前明确终止或排空新 Adapter 在飞任务；
- 已确认 Action 不允许通过回滚变成可重复执行，回滚工具必须读取执行事实并保持终态。

## 17. 配套任务书

- `2026-08-27-agent-brain-platform-task-brief.md`
- `2026-08-27-fae-platform-task-contract-task-brief.md`
- `2026-08-27-admin-platform-task-contract-task-brief.md`
