# Agent Platform 统一使用入口与专业 Agent 接入设计

日期：2026-08-24

状态：已根据首轮 Claude 评审修订，待复审

涉及系统：Agent Platform、AI ADMIN、AI FAE、MetaBot 本地专业 Agent、共享 Nginx 入口

## 1. 结论摘要

Agent Platform 的产品入口必须从“管理和观测平台”升级为“员工直接使用企业 Agent 的统一入口”。平台包含一个默认的 Agent 大脑和八个专业 Agent 入口：一个 HR、五个 Marketing、一个 AI 行政 Agent、一个 AI FAE Agent。

已经确认的核心决定如下：

1. 登录后的根页面是可持续对话的 Agent 大脑，不是管理看板，也不跳转行政系统。
2. 专业 Agent 统一使用 `/agents` 与 `/agents/{agent_id}`；不新增 `/workbench/*` 第二套路由。
3. Catalog 是 Platform 常驻底座，必须脱离 `PLATFORM_AGENT_BRAIN_ENABLED` 开关。
4. HR 和五个 Marketing Agent 继续运行在本地 MetaBot，通过 Execution Relay 接收 Platform 拥有的会话任务；该链路不经过飞书协议层。
5. AI ADMIN 继续使用同一钉钉企业身份，通过同域 `/office/*` 和既有 Platform Session 回环校验接入。
6. AI FAE 保持独立域名和外部客户能力不变；第一期只作为专业入口，不被 Agent 大脑调度。
7. Agent Platform、Platform-owned 专业会话、AI ADMIN 和管理中心共用稳定的 `internal_user_id` 归属 Session、附件、反馈与审计；姓名只用于展示。面向外部客户的 FAE 保持独立身份边界。
8. `agent.orbbec.com.cn` 只能有一个 HTTPS 应用 Server Block；HTTP 跳转块可以保留。两者均由 Platform 管理，AI ADMIN 不再声明同域名 Server Block。
9. Agent 大脑 V1 会话代码已经随当前生产镜像发布但开关关闭；尚未上线的是 041/042 所代表的 V2 durable loop、relay job kind 及其生产开关。

## 2. 背景与问题

现有 Platform 已具备钉钉登录、Agent Registry、Session 回放、附件、Feedback、Review、Trace、Evidence、数据飞轮和运行观测，但产品入口仍存在四类断裂：

- 根路径在 Brain 关闭时由后端重定向 `/admin`，导致员工进入管理页面，且当前 `/admin` 又与 AI ADMIN 路由冲突；
- `/api/v1/catalog/agents`、`/agents` 和 `/agents/{agent_id}` 被 Brain 总开关间接控制，导致专业入口不能独立上线；
- Agent 清单分散在 Registry、Fleet Catalog、Brain capabilities 和运行合约中，数量、命名和用途不一致；
- HR/Marketing、AI ADMIN 与 AI FAE 的接入形态不同，但平台没有用一个清晰协议同时表达“直接对话、可被大脑调度、外部工作区”三种能力。

本设计解决统一入口、身份、目录、路由与接入边界。V2 durable loop 的模型协议、持久化、租约、预算和 Provider 细节继续以《Cloud Agent Brain Durable Loop Design》为准，本设计不重复发明该运行时。

## 3. 目标与非目标

### 3.1 目标

- 员工一次钉钉登录后直接使用 Agent 大脑和已授权专业 Agent；
- 顶部导航以使用为主，管理能力退居管理中心；
- 八个专业 Agent 在同一个 Catalog 中有稳定身份、能力说明、入口模式和授权策略；
- Brain 关闭或尚未发布时，专业 Agent 目录与外部工作区入口仍然可用；
- 本地 MetaBot 离线只影响六个本地专业 Agent，不影响账号、目录、Admin、FAE 和管理中心；
- 一个企业用户在 Platform、AI ADMIN 和 Platform-owned 专业会话中保持同一个 `internal_user_id`；
- `/admin/*` 永久归 Platform，`/office/*` 永久归 AI ADMIN；
- 对历史 Session、现有 FAE、现有 Admin 数据和本地 MetaBot 保持兼容，不做破坏性迁移。

### 3.2 非目标

第一期不做：

- 低代码 Agent 创建、Prompt 在线编辑、模型或工具自由选择；
- `/workbench/hr`、`/workbench/marketing` 等第二套路由；
- 复刻飞书 Webhook、飞书 open_id 或飞书消息事件；
- 把 MetaBot 整体迁移到云端；
- 让 AI ADMIN 或 AI FAE 直接参与 Brain V2 调度；
- 改造 FAE 的外部客户账号体系；
- iframe 嵌入 Admin 或 FAE；
- 运行时静默切换模型、Agent 或执行节点。

## 4. 已核实的现状

### 4.1 生产和分支

- 当前生产 release 为 `cac9369ca2255d3707b589a78262079fe6c67138`；
- 生产已包含 `backend/app/agent_brain/`、036 Conversation、037 Feedback 和 038 Summary Phase；
- 生产 `PLATFORM_AGENT_BRAIN_ENABLED=0`，因此 V1 会话链路未开放；
- 041 durable loop 和 042 relay job kind 当前位于功能工作树，尚未进入生产；
- 后续合并与发布必须以 `origin/master` 为基准，不以落后的本地 `master` 为基准。

### 4.2 根路径和 Catalog Gate

`routes_auth.py` 当前行为是：已登录且 Brain 关闭时，`GET /` 返回 `302 /admin`。

Catalog 路由当前定义在 `build_agent_brain_router()` 中，而该 Router 只有在 `mission_repository` 存在时挂载；`mission_repository` 又只在 `PLATFORM_AGENT_BRAIN_ENABLED=1` 时创建。因此 Brain 关闭时：

- `GET /api/v1/catalog/agents` 不存在；
- `/agents` 和 `/agents/{agent_id}` 无法正常加载；
- 直接专业 Agent 会话也不可启动。

### 4.3 已有直接对话能力

现有 `/agents/{agent_id}` 已经可以创建 `mode=direct_agent`、绑定 `direct_agent_id` 的 Conversation，并进入统一持续对话页。它天然满足：

- 一个 Conversation 固定绑定一个专业 Agent；
- 用户可在同一 Conversation 中持续追问；
- 选择另一个 Agent 时创建新的 Conversation；
- Session、消息、附件和反馈继续由 Platform 所有。

因此不再新增 Workbench 路由，只补现有页面缺少的目录筛选、Agent 切换和按 Agent 过滤的历史侧栏。

### 4.4 MetaBot 执行语义

现有链路是：

```text
Platform Conversation
  -> Execution Relay job
  -> 本地 Worker 主动领取
  -> 指定 MetaBot Agent 会话内核
  -> Relay events / result
  -> Platform Conversation
```

Relay payload 已包含 `conversation_id` 和 `trigger_message_id`，连续对话由 Platform Conversation 保证。它不经过飞书消息处理器，不伪造飞书用户，也不依赖飞书机器人入口。

本地 Worker 离线时，`metabot_local` Adapter 已明确返回 `unavailable`，不会改派其他 Agent，也不会伪装成功。

## 5. 产品信息架构

### 5.1 一级入口

```text
Agent 大脑       /
专业 Agent      /agents
管理中心         /admin/*       仅 owner/admin 可见
苍渊             /account       点击右上角企业姓名进入
```

“企业账号”不占用一级导航标签。右上角展示 Platform 返回的人工确认姓名，例如“苍渊”，点击进入 `/account`。

### 5.2 根页面

Brain 已启用时，`/` 是持续对话工作区：

- 左侧是当前用户的历史 Conversation，最新在上；
- 中间保持当前 Conversation，不把每轮输入跳转为孤立任务页；
- 用户可新建对话、继续追问、停止当前 Turn；
- 执行过程公开展示任务分派、专业 Agent 状态和结果，但不展示原始思维链。

Brain 未启用时，`/` 仍返回 Platform 应用壳，响应增加稳定的 `X-Platform-Entry-State: brain-preparing`，页面显示明确的“Agent 大脑准备中”，并提供“进入专业 Agent”按钮。不得跳 `/admin`，也不得渲染一个调用未挂载 Brain API 的报错页。

### 5.3 专业 Agent 目录

`/agents` 只展示当前用户获授权的入口，按领域分组：

- HR；
- Marketing；
- 行政服务；
- 技术支持。

Marketing 下显示五个明确能力，不用一个含糊的“Marketing Bot”覆盖。目录支持 `?domain=marketing` 筛选，但不引入另一套 URL 层级。

## 6. Agent 清单与接入模式

平台定义八个专业 Agent；Agent 大脑本身是默认平台能力，不计入这八个专业 Agent。

| Agent ID | 展示名称 | 领域 | 接入模式 | Brain 首期可调度 | 正式入口 |
|---|---|---|---|---:|---|
| `hr-bot` | HR Agent | HR | direct_chat、brain_delegation | 是 | `/agents/hr-bot` |
| `marketing-prospecting-bot` | Marketing Prospecting | Marketing | direct_chat、brain_delegation | 是 | `/agents/marketing-prospecting-bot` |
| `marketing-inbound-bot` | Marketing Inbound | Marketing | direct_chat、brain_delegation | 是 | `/agents/marketing-inbound-bot` |
| `marketing-voice-bot` | Marketing Voice | Marketing | direct_chat、brain_delegation | 是 | `/agents/marketing-voice-bot` |
| `marketing-intelligence-bot` | Marketing Intelligence | Marketing | direct_chat、brain_delegation | 是 | `/agents/marketing-intelligence-bot` |
| `marketing-gtm-bot` | Marketing GTM | Marketing | direct_chat、brain_delegation | 是 | `/agents/marketing-gtm-bot` |
| `ai-admin-agent` | AI 行政 Agent | 行政服务 | external_workspace | 否 | `/office/?view=services` |
| `ai-fae-agent` | AI FAE Agent | 技术支持 | external_workspace | 否 | `https://fae.orbbec.com.cn/` |

遗留 `fae-bot` 不属于新专业目录。它不得因 `fae_http` Adapter 将来被注册而自动进入 Brain 调度。历史数据可保留该 ID，但新 Catalog、Brain allowlist 和直接入口均使用 `ai-fae-agent`。

## 7. 独立 Agent Catalog

### 7.1 单一权威源

新增独立的 Platform Agent Catalog 模块，规范文件建议为：

```text
backend/app/agent_catalog/catalog.yaml
```

现有 `backend/app/agent_brain/capabilities.yaml` 的有效能力描述迁入该文件。Brain、专业目录、授权、运行状态投影和管理视图均消费同一 Catalog，不再各自定义“有哪些 Agent”。

以下系统仍可保留自己的运行数据，但不能成为 Agent 身份和产品清单的权威源：

- Registry：服务发现和工作区端点；
- Fleet Catalog：观测展示兼容层；
- Runtime Contract：某个执行节点当前实际承载哪些实例；
- Replica：生产观测数据副本。

关联规则固定为：

- Registry 通过 `flywheel_agent_id` 关联规范 Catalog，不使用 Registry 自身的短 `id`；现有 Admin 条目必须补 `flywheel_agent_id: ai-admin-agent`；
- Fleet 的 `aliases` 只属于历史数据投影层，例如 `marketing-bot` 归一为 `marketing-prospecting-bot`，别名本身不进入规范 Catalog；
- `unresolved_aliases` 是显式已知但暂不归属的历史 ID，不生成用户卡片，也不作为逐次运行告警；只在治理报告中汇总；
- 其他未声明 ID 才进入治理告警，且不能静默生成第九张用户可见卡片。

Registry 的 `entry_url`、`api_base` 和 `health.url` 继续服务于内部发现与健康检查，不得派生用户可见的 `workspace_url`。因此现有 FAE 的 HTTP IP 地址不会参与 Catalog workspace 白名单校验。Admin 路径迁移后必须同时清除 Registry 的 `<admin-host>` 占位符，固定为宿主回环 `127.0.0.1:8011` 及其真实应用/健康路径；占位地址不得进入发布产物。

### 7.2 Catalog 字段

每个 Agent 至少定义：

```yaml
agent_id: hr-bot
display_name: HR Agent
domain_group: HR
mission: ...
capabilities: [...]
exclusions: [...]
example_tasks: [...]
interaction_modes:
  - direct_chat
  - brain_delegation
workspace_url: null
adapter_kind: metabot_local
adapter_config_version: 1
capability_version: 1
authorization_policy: agent_grant
```

`interaction_modes` 是集合，不是互斥枚举。支持值首期固定为：

- `direct_chat`：由 Platform 创建直接 Conversation；
- `brain_delegation`：可由 Brain 创建 Agent Task；
- `external_workspace`：打开受控外部或同域专业页面。

`adapter_kind` 仅对包含 `direct_chat` 或 `brain_delegation` 的 Agent 必填；纯 `external_workspace` Agent 必须为 `null`。可调度性由 `brain_delegation` 和已注册 Adapter 共同决定；不得通过“Adapter 恰好没注册”实现隐式排除。

包含 `brain_delegation` 但 Adapter 缺失时，Catalog 卡片仍存在，Runtime 状态必须是 `unavailable`，Brain 的 `list_agents` 也要看到明确不可用原因；不得在 `runtime_registry.py` 中 `continue` 后静默消失。纯 external workspace 不要求 Adapter。

`workspace_url` 只允许：

- 同源 `/office/` 白名单路径；
- 明确配置的 `https://fae.orbbec.com.cn/`；
- 不允许任意 URL、协议相对地址、query 注入或运行时用户输入。

### 7.3 与 Brain 开关解耦

Catalog Loader、授权读取和 `GET /api/v1/catalog/agents` 在钉钉身份体系启用时常驻挂载，不依赖：

- `PLATFORM_AGENT_BRAIN_ENABLED`；
- Mission Repository；
- Brain Provider；
- 本地 Worker 是否在线。

Catalog 返回当前用户获授权的卡片及可用性。Brain 关闭时，`direct_chat` 和 `external_workspace` 仍按各自依赖工作；不能工作的模式返回明确状态，不删除整张卡片。

## 8. 统一身份与授权

### 8.1 企业身份原则

钉钉是唯一企业身份源。Platform 创建稳定 `internal_user_id` 并维护钉钉映射。所有业务归属和授权使用 `internal_user_id`，不得使用姓名、手机号、部门名称或浏览器字段猜测。

产品原则是：

> 一个企业账号、一次登录、贯穿所有智能体；Platform 统一验证身份，各专业 Agent 继承可信主体，但不自行建立另一套企业账号。

统一身份不等于所有组件都直接解析浏览器 Cookie。不同接入形态采用最小且明确的身份传递方式。

### 8.2 Platform 原生会话

Agent 大脑、HR 和 Marketing 的 Conversation 由 Platform 保存并绑定：

```text
internal_user_id
conversation_id
mode
direct_agent_id（直接专业 Agent 时）
```

本地 MetaBot 只执行 Platform 创建的任务。Relay job envelope 增加由 Platform 生成的可信主体：

```text
requester_subject.internal_user_id
requester_subject.display_name
```

Worker 将该主体作为请求元数据交给目标 MetaBot，不把它拼进用户 Prompt，也不允许浏览器覆盖。部门等扩展字段只有在目标能力明确需要且获得授权时才单独增加。不得传递钉钉原始标识、浏览器 Cookie、角色快照或完整组织档案。Session 所有权和访问判断始终在 Platform。

该字段采用两阶段发布：先升级本地 Worker 使其识别并验证 `requester_subject`，再允许云端 Relay 生产该字段。当前 `RelayJobPayload` 对额外字段并非 fail-closed，旧 Worker 会忽略新字段，因此 Worker 升级完成前不得宣称主体已贯通到 MetaBot。

### 8.3 AI ADMIN 身份贯通

AI ADMIN 以《AI 行政 Agent `/office` 路径迁移设计》（2026-08-24）为接入合同：

本设计保留该任务书的同域 Cookie 身份贯通，但将其中调用通用 `/api/v1/account` 的部分收窄为专用最小 Subject 端点；AI ADMIN 任务书必须同步修订后再实施。

1. 浏览器同域请求 `/office/*` 时携带 `__Host-platform_session`；
2. AI ADMIN 只读取该具名 Cookie；
3. Platform 新增固定回环端点 `GET http://127.0.0.1:8080/api/v1/internal/session/subject`；AI ADMIN 只把具名 Session Cookie 发给该端点逐请求校验；
4. AI ADMIN 不复制、续签、持久化、记录或返回 Cookie；
5. AI ADMIN 不信任浏览器传入的用户 ID、姓名、部门、角色或管理员标记；
6. Platform 返回 401、403、3xx、超时或非法响应时失败关闭；
7. 回环客户端禁止跟随重定向，只接受固定 Schema；
8. Admin 新建聊天、反馈、班车和住宿操作均记录可信 `internal_user_id`；
9. Admin 业务管理员名单绑定 `internal_user_id`，姓名仅展示。

专用 Subject 端点必须满足以下不变量：

- 只接受没有代理转发头的回环请求；公网 Nginx 对同一路径精确返回 404；
- 无论请求额外携带哪些 Cookie，都只返回 `{internal_user_id, display_name, active}`；
- 永不调用完整 `account_snapshot`，不返回 role、departments、gender、real_name、mobile、observation scopes 或 CSRF token；
- 永不设置或刷新 Cookie，不返回 `Set-Cookie`，响应固定 `Cache-Control: no-store`；
- 非活动用户、无效 Session 返回 401，身份后端不可用返回 503；
- AI ADMIN 不得改为调用通用 `/api/v1/account`。

AI ADMIN 的所有非安全方法必须自行执行 CSRF 防护，因为 `/office/*` 不经过 Platform ASGI 中间件，而且 `fae.orbbec.com.cn` 与 `agent.orbbec.com.cn` 在浏览器 SameSite 语义中属于同站。最低要求是严格校验 `Origin=https://agent.orbbec.com.cn`，拒绝缺失或其他 Origin，并在浏览器提供时要求 `Sec-Fetch-Site: same-origin`；Admin 也可以叠加自己的 Session-bound CSRF token。来自 FAE Origin 的 POST 即使携带 Platform Session 也必须返回 403。

这不是新的下游 JWT 协议，也不要求员工二次登录。

### 8.4 FAE 身份边界

FAE 同时面向外部客户，不能强制改成 Platform 钉钉账号体系。第一期从专业 Agent 目录打开 FAE 公开入口，但不把 Platform Cookie、钉钉身份或 Platform 内部角色发送到 `fae.orbbec.com.cn`。

未来若要求“内部员工进入 FAE 时自动识别企业身份”，应单独设计一次性授权码交换，不能扩大 `__Host-platform_session` 的域，也不能把外部客户身份与企业身份混库。本期不实施。

### 8.5 授权策略

- Agent 大脑：所有有效 Platform member 可用；
- HR 和五个 Marketing：沿用按 Agent 的人员、部门或全员授权，默认拒绝；
- AI ADMIN：所有有效企业成员可进入员工服务，Admin 自身业务规则决定班车、住宿、反馈管理权限；
- AI FAE：目录入口可全员展示，FAE 内部/外部业务权限保持其现状；
- Platform 管理中心：仅 `platform_owner`、`platform_admin` 及已经明确设计的只读观察角色访问相应范围。

前端隐藏不构成授权。Catalog、直接会话创建、Conversation 读取、附件和每次 Brain 委派都由后端重新校验。

## 9. 专业 Agent 使用体验

### 9.1 Direct Chat

`direct_chat` Agent 进入 `/agents/{agent_id}`：

- 首屏展示能力、边界和示例；
- 用户输入后创建固定 `direct_agent_id` 的 Conversation；
- 页面进入统一持续对话工作区；
- 左侧历史只显示该 Agent 的 Conversation；
- 用户可以继续追问、上传受支持附件、停止当前 Turn；
- 切换 Agent 时明确创建新的 Conversation，不把旧 Conversation 改绑。

### 9.2 Marketing 切换

Marketing 不新增独立 Workbench。目录支持 Marketing 分组筛选，直接对话页提供同组 Agent 切换器：

```text
找客户 | 入站营销 | 语音触达 | 市场情报 | GTM
```

切换只改变下一条新 Conversation 的目标 Agent。正在进行或已有 Conversation 永远保留原 `direct_agent_id`。

### 9.3 External Workspace

`external_workspace` 卡片不显示 Platform 聊天输入框，而显示清晰的“进入专业工作区”：

- AI 行政 Agent：同页跳转 `/office/?view=services`，沿用 Platform 企业身份；
- AI FAE Agent：新页面打开 `https://fae.orbbec.com.cn/`，明确其面向技术支持与外部客户。

不得使用 iframe。外部工作区返回、不可用或无权限时显示明确结果，不伪造 Platform Conversation。

## 10. Agent 大脑

### 10.1 顶层职责

Agent 大脑是云端持久化顶层 Loop，负责：

- 理解原始需求；
- 决定直接回答、追问或委派；
- 从当前用户获授权且可调度的 Agent 中选择；
- 向一个或多个专业 Agent 提交最小任务上下文；
- 等待并检查真实结果；
- 必要时补派；
- 形成最终可追踪交付。

MetaBot 只是可用 Adapter 之一，不是 Agent 大脑本身。Mac 离线不能让 Agent 大脑、Admin、FAE、账号和管理中心整体离线。

### 10.2 首期调度范围

Brain 首期只能调度：

- `hr-bot`；
- 五个 `marketing-*-bot`。

`ai-admin-agent`、`ai-fae-agent`、遗留 `fae-bot`、`feishu-default`、Test Bot、Codex Assistant 和个人 Workspace 均不得出现在 Brain 的 `list_agents` 可调度集合中。

Brain 从独立 Catalog 读取能力，从授权服务读取当前用户权限，从 Runtime Registry 读取实时可用性。包含 `brain_delegation` 的能力存在但 Adapter 缺失或执行节点离线时，应向模型和用户返回带原因的 `unavailable`，不能由 `runtime_registry.py` 静默 `continue`、删除或自动换 Agent。

### 10.3 V2 开关

区分两个开关语义：

- Catalog 和专业入口是否可用；
- Brain V2 durable loop 是否接管新的 Brain Turn。

不得再用一个 `PLATFORM_AGENT_BRAIN_ENABLED` 同时控制 Catalog、直接会话、根页面和 Brain Runtime。Catalog 在迁移完成后是无开关的常驻基础能力；需要独立控制的只有直接执行和 Brain V2：

```text
PLATFORM_DIRECT_AGENT_ENABLED
PLATFORM_AGENT_BRAIN_V2_ENABLED
```

Brain V2 只在迁移、Provider 探测和 durable worker 验收完成后开启。即使两个执行开关均关闭，账号、Catalog、外部工作区和管理中心仍然可用。

最终合法开关矩阵如下；钉钉生产身份是所有生产组合的前提：

| Relay | Direct Agent | Brain V2 | Brain Model | 合法 | 用途 |
|---:|---:|---:|---:|---|---|
| 0 | 0 | 0 | 0 | 是 | Catalog、Admin、FAE、管理中心 |
| 1 | 0 | 0 | 0 | 是 | Relay/Worker 维护窗口，不接受用户执行 |
| 1 | 1 | 0 | 0 | 是 | HR/Marketing 直接会话，Brain 关闭 |
| 1 | 0 | 1 | 1 | 是 | Brain-only Dev 验证 |
| 1 | 1 | 1 | 1 | 是 | 最终完整生产形态 |
| 0 | 1 | 任意 | 任意 | 否 | Direct Agent 需要 Relay |
| 0 | 任意 | 1 | 1 | 否 | Brain V2 需要 Relay |
| 任意 | 任意 | 1 | 0 | 否 | Brain V2 需要模型运行时 |
| 任意 | 任意 | 0 | 1 | 否 | 模型运行时不得脱离 Brain V2 单独启用 |

现有 `PLATFORM_AGENT_BRAIN_ENABLED` 仅作为 V1 迁移兼容开关保留到 V2 切换完成，不再控制 Catalog 或 Direct Agent。配置校验必须按上表重写并增加参数化测试。

## 11. 路由与 Nginx 所有权

最终浏览器路由固定为：

```text
https://agent.orbbec.com.cn/                  Agent 大脑或准备页
https://agent.orbbec.com.cn/account           企业账号
https://agent.orbbec.com.cn/agents            专业 Agent 目录
https://agent.orbbec.com.cn/agents/{id}       Platform 直接专业 Agent
https://agent.orbbec.com.cn/admin/*           Platform 管理中心
https://agent.orbbec.com.cn/office/*          AI 行政 Agent
https://fae.orbbec.com.cn/*                   AI FAE Agent
http://47.106.112.69/*                        FAE 原 IP HTTP，保持现状
```

`agent.orbbec.com.cn` 只允许一个监听 443 的 HTTPS 应用 Server Block。监听 80 的 HTTP → HTTPS 跳转块可以独立存在。两个块均由 Platform 发布事务管理，AI ADMIN 不得再定义同域名 Server Block。HTTPS 应用块至少显式声明：

```nginx
location = /admin { proxy_pass http://127.0.0.1:8080; }
location ^~ /admin/ { proxy_pass http://127.0.0.1:8080; }
location = /api/v1/internal/session/subject { return 404; }
location = /office { return 308 /office/$is_args$args; }
location ^~ /office/ { proxy_pass http://127.0.0.1:8011; }
location / { proxy_pass http://127.0.0.1:8080; }
```

实际生产配置还必须保留 TLS、ACME、请求体、SSE、可信代理、日志脱敏和精确上传限制。AI ADMIN 后续发布不得再次创建该域名 Server Block，也不得修改 `/admin/*`。

现有 HTTPS Server Block 的 `client_max_body_size 1m` 和安全响应头会继承到 `/office/*`。路径迁移必须明确处理：

- 普通 Platform 与 Admin 请求继续保持 1 MB 上限；
- AI ADMIN 只有其受版本控制路由清单中精确的反馈附件上传端点提高到 12 MB；
- Platform Conversation 附件上线时，只有精确上传端点提高到既有设计规定的单文件 50 MB，不能提高整个 `/api/` 或 `/office/`；
- `/office/chat` 单独保留流式响应所需的 buffering、cache 和超时设置，但不扩大其他方法或路径；
- AI ADMIN 必须先验证 WebUI 在 `script-src 'self'`、`style-src 'self'`、`connect-src 'self'` 下可运行；不得临时加入 `'unsafe-inline'` 或任意 CDN；
- Platform 应用壳还会返回应用级 CSP，浏览器会对 Nginx CSP 和应用 CSP 取交集，验收必须检查实际响应中的全部 CSP header，而不是只看配置文件；
- 如果 `/office/` 需要 location 级 CSP 或其他 `add_header`，必须通过完整安全头 snippet 重新声明 HSTS、nosniff、X-Frame-Options、Referrer-Policy、CSP 和 Permissions-Policy；Cache-Control 按内容类型明确设置，HTML/API/个人数据为 `no-store`，带内容哈希的静态资源才允许 immutable cache。Nginx 的 `add_header` 继承规则不允许只覆盖其中一项而意外丢掉其他安全头。

建议将完整安全头生成到 root-owned Nginx snippet，并由 HTTPS Server 与所有需要覆盖 header 的精确 location 引用，避免 Admin 路径迁移时漏掉安全头。

发布验收通过 `nginx -T` 检查：

- 只有一个监听 443 且承载应用 location 的 `agent.orbbec.com.cn` Server Block；
- 监听 80 的同域名块只能承担 ACME challenge 和 HTTPS 跳转；
- `/admin` 和 `/admin/` 均进入 8080；
- `/office/` 进入 8011；
- `/admin/*` 永不进入 8011；
- FAE Server Block、容器和 IP 访问不变。

`/office/health` 是否返回 404 由 AI ADMIN 应用负责，不是 Platform Nginx 能单独保证的属性；它属于 Admin 发布验收。Platform 只验证该路径没有落入 8080 或暴露其他服务。

## 12. Session、附件和数据归属

- Platform Conversation 的 SoR 是 Platform Postgres；
- 每条 Conversation 必须绑定 `internal_user_id` 和 mode；
- `direct_agent` Conversation 必须绑定且永久保持一个 `direct_agent_id`；
- Admin 自有业务 Session 继续由 Admin 保存，但必须记录 Platform `internal_user_id`，同步到 Platform 时保留这一归属；
- FAE Session 保持 FAE 自有身份与存储边界，第一期不强行合并；
- 普通用户只能读取自己的 Platform Conversation、附件和反馈；
- 管理员跨用户查看继续审计；
- Agent 切换不迁移、不合并、不重写历史 Session；
- 任何历史数据都不得按姓名自动归属。

现有观测/飞轮库只有 `sender_user_id` 和展示名投影，不能证明它等于控制库 `internal_user_id`。因此新增显式可信归属桥：

1. AI ADMIN 新建 Session 时，把 Subject 端点返回的 `internal_user_id` 写入 Admin 自有 Session 行的专用列；
2. Admin → Flywheel/Platform 的受信同步协议增加可空 `owner_internal_user_id`；
3. 观测侧增加 `session_subject_links(source_kind, native_session_id, internal_user_id, verification_method, verified_at)`，只允许受信 importer 写入；
4. 该映射只用于展示归属、审计和管理投影，不作为在线登录或授权库；
5. 历史 Admin Session 默认为未归属，只有源系统证据或人工审计映射才能回填；姓名、手机号和展示名不得作为回填依据；
6. 跨数据库无法建立外键时，导入器校验 UUID 格式并把无法对应当前控制面用户的记录标为 `unresolved`，不得猜测。

## 13. 失败与降级语义

| 场景 | 用户可见行为 |
|---|---|
| Brain 关闭 | 根页面显示准备状态和专业 Agent 入口，不跳管理中心 |
| Catalog 不可用 | 专业目录显式失败并可重试；管理中心和账号仍可用 |
| 本地 Worker 离线 | HR/Marketing 显示“本地专业 Agent 当前离线，任务未派发” |
| 单个 MetaBot 不可用 | 仅该 Agent 不可执行；不得换 Agent |
| Admin 身份校验失败 | 401/403/503 明确失败，不回退匿名或旧 ticket |
| FAE 不可用 | 外部工作区入口明确不可用，不生成虚假 Conversation |
| Brain Provider 不可用 | 当前 Turn 显式失败；不切换模型或本地 Brain |
| 授权被撤销 | 新请求 403；在飞任务按 durable loop 既定授权变化规则终止 |

“能力不可用”和“用户未授权”必须分开表达；前端不得把 403 显示成空目录或普通网络错误。

## 14. 安全边界

- 钉钉 OAuth、Platform Session、CSRF 和授权均由 Platform 后端验证；
- `__Host-platform_session` 保持 `Secure`、`HttpOnly`、`Path=/` 且无 Domain；
- Admin 只把具名 Cookie 发给固定回环 Subject 接口，禁止重定向、日志和持久化；通用 `/api/v1/account` 不再作为服务间身份端点；
- Admin 写请求独立执行严格 Origin/Fetch Metadata CSRF 防护，不能依赖 Platform 中间件或 SameSite；
- MetaBot 不接收 Platform Cookie、钉钉原始 ID 或浏览器角色字段；
- FAE 域名不接收 Platform Cookie；
- 浏览器不能自行指定 `internal_user_id`、有效 Agent 集合、Conversation owner 或管理员角色；
- External workspace URL 来自受版本控制 Catalog 白名单；
- Nginx 覆盖式设置可信代理头，不信任客户端 `X-Forwarded-For`；
- 未认证请求只按 IP 做高阈值粗限流；认证后主要按 `internal_user_id` 和功能限流，避免公司 NAT 出口互相误锁；
- Session、附件、授权、跨用户查看和管理操作继续写审计；
- 不在 SSE、前端、专业 Agent 或数据飞轮展示原始思维链。

## 15. 方案比较与取舍

### 15.1 专业工作台路由

选择：复用 `/agents/{agent_id}`。

拒绝新增 `/workbench/hr` 和 `/workbench/marketing`，因为现有直接 Conversation 已具备固定 Agent、持续追问、Session 归属和统一回放。新路由只会复制页面、历史和授权逻辑。

### 15.2 Catalog 与 Brain

选择：Catalog 独立常驻。

拒绝“先启用 Brain 才能显示专业目录”，因为目录、授权和外部工作区是 Platform 基础能力，不能被模型 Provider、Mission Repository 或 Brain Worker 拖垮。

### 15.3 Admin 身份

选择：延续现有同域 Cookie，并将 AI ADMIN 的服务间校验收窄到固定回环 `/api/v1/internal/session/subject`。

本期拒绝新建下游 JWT、独立登录和 iframe。现有方案已经实现一次企业登录和服务端可信主体；新增协议只扩大迁移范围。

### 15.4 FAE

选择：独立域名、外部工作区入口、暂不参与 Brain 调度。

拒绝把 FAE 迁到 `/fae`、共享 Platform Cookie 或在本期修改其外部客户体系。

## 16. 分阶段实施顺序

### 阶段 0：归还路由所有权

- AI ADMIN 完成 `/admin` → `/office` 基础路径迁移；
- Platform 成为 `agent.orbbec.com.cn` 唯一 HTTPS 应用 Server Block 所有者；
- 显式固定 `/admin/*`、`/office/*` 和 `/` 的上游；
- 保持现有 Admin Cookie 回环身份校验；
- 完整验证 Admin 功能和 FAE 不变性。

### 阶段 1：建立可用基线

- 以 `origin/master` 为基准完成当前工作树合并；
- 验证 039、040、041、042 顺序和全量迁移测试；
- 应用 042 前在生产控制库执行只读孤儿检查：`execution_jobs` 左连接 `mission_runs` 后 `mission_runs.run_id is null` 的数量必须为 0；
- 若孤儿数非 0，立即停止发布，按运行记录逐类形成审计过的分类清单后再修改迁移或数据；不得无证据统一 `coalesce` 为 `legacy_brain`；
- 根路径取消 Brain-disabled → `/admin` 重定向；
- 增加 Brain 关闭准备页；
- 同步修改 `deploy/cloud/accept.sh` 的关闭 Brain 回滚断言：从 `302 + Location: /admin` 改为 `200 + X-Platform-Entry-State: brain-preparing`；
- 完成前后端、迁移、部署和回滚门禁。

### 阶段 2：Catalog 独立

- 新建 Platform Agent Catalog 模块和唯一规范文件；
- 将 Catalog API 与 Agent 授权从 Brain Router 拆出；
- 迁移八个专业 Agent 卡片；
- 明确 `interaction_modes`、workspace URL 和 dispatchability；
- Fleet、Registry 和 Runtime Contract 改为 Catalog 投影或关联数据；
- Registry Admin 条目补 `flywheel_agent_id: ai-admin-agent`，并把 `<admin-host>` 替换为迁移后真实的 `127.0.0.1:8011` 回环端点；
- 对未知、重复、遗留和不可调度 ID 增加治理测试。

### 阶段 2.5：直接会话与可信主体脱离 Brain 开关

- 在生产身份启用且 Direct Agent 或 Brain V2 任一开启时，初始化共享的 control DB、ContentCodec、MissionRepository、ConversationRepository 和 AgentUseAuthorization；不再以旧 `agent_brain_enabled` 作为唯一构造条件；
- `build_conversation_router` 的挂载条件改为 `PLATFORM_DIRECT_AGENT_ENABLED`，不再依赖 `PLATFORM_AGENT_BRAIN_ENABLED`；
- Direct Agent 开启时强制要求 Execution Relay、生产身份、内容密钥和对应数据库迁移就绪；
- 保留 MissionRepository 作为现有 direct Conversation 兼容依赖，但不启动 V1 Brain scheduler；
- 新增最小 Subject 回环端点并将公网同路径固定为 404；
- 先发布识别 `requester_subject` 的本地 Worker，再发布生成该字段的云端 Relay；
- 增加 Admin Session 的 `internal_user_id` 源字段、可信同步字段和观测侧 `session_subject_links`；
- 对合法开关矩阵、Repository 初始化和 Router 可达性增加参数化测试。

### 阶段 3：专业 Agent 入口

- `/agents` 上线 HR、五个 Marketing、Admin、FAE；
- HR/Marketing 复用 `/agents/{id}` 创建直接 Conversation；
- Admin 卡片进入 `/office/?view=services`；
- FAE 卡片进入 `https://fae.orbbec.com.cn/`；
- Brain 仍可保持关闭。

### 阶段 4：直接工作区补齐

- Conversation 左侧栏支持按 `direct_agent_id` 过滤；
- Marketing 增加五能力切换器；
- 切换 Agent 必须新建 Conversation；
- 补附件、失败重试、停止和本地离线提示；
- 删除任何“模拟飞书消息”的错误实现描述。

### 阶段 5：Brain V2 上线

- 完成 041/042、durable worker、Provider probe、预算、缓存和崩溃恢复验收；
- Brain 只获得六个首期可调度 Agent；
- Dev 真实任务评测通过后开启 V2；
- 根页面从准备页切换为持续 Agent 大脑；
- 不迁移 MetaBot 到云端，不改变 Admin 和 FAE 接入方式。

## 17. 测试与验收门槛

### 17.1 Catalog

- Brain 关闭时 `GET /api/v1/catalog/agents` 仍返回 200；
- 规范 Catalog 恰好定义八个专业 Agent；
- 当前用户只看到获授权卡片；
- `fae-bot`、`feishu-default`、Test Bot、Codex Assistant 和个人 Workspace 不出现；
- `ai-admin-agent`、`ai-fae-agent` 不可被 Brain 派发；
- 未注册本地 Adapter 不会静默删除 external workspace 卡片；
- 包含 `brain_delegation` 但缺失 Adapter 的卡片保留并返回 `unavailable`，不得静默消失；
- 纯 external workspace 的 `adapter_kind=null` 可以通过 Schema 校验；
- Fleet aliases 只做投影归一，unresolved aliases 不进入 Catalog 且不制造逐次告警；
- Registry Admin 通过 `flywheel_agent_id=ai-admin-agent` 正确关联；用户 workspace URL 不读取 Registry `entry_url`；
- 未知 Runtime ID 产生治理告警，不自动进入目录。

### 17.2 路由和页面

- 已登录且 Brain 关闭访问 `/` 返回准备页，不跳 `/admin`；
- `/agents` 在 Brain 关闭时仍可用；
- `PLATFORM_DIRECT_AGENT_ENABLED=1` 且 Brain V2 关闭时，直接 Conversation Router 仍然可达；
- `/agents/hr-bot` 创建并进入固定 HR Conversation；
- Marketing 切换创建新 Conversation，不改写旧记录；
- 点击“苍渊”进入 `/account`，一级导航无“企业账号”标签；
- `/admin/*` 只进入 Platform；
- `/office/*` 只进入 AI ADMIN；
- `/office` 规范跳转保留 query；
- FAE 域名和原 IP 行为不变。

### 17.3 身份和权限

- Platform、直接 Agent Conversation 和 Admin 操作使用同一 `internal_user_id`；
- 浏览器伪造姓名、部门、角色和 user ID 不能改变主体；
- Admin 只向固定回环账号接口转发具名 Cookie；
- Subject 端点即使收到完整 Cookie 头也永不返回 CSRF token、手机号、真实姓名、部门、角色、Scope 或 `Set-Cookie`；
- Admin 回环 3xx、超时、非法 Schema 均失败关闭；
- 从 `https://fae.orbbec.com.cn` Origin 向 `/office/*` 发起写请求，即使携带有效 Platform Session 也返回 403；缺失 Origin 的浏览器写请求同样拒绝；
- HR/Marketing Worker 收不到浏览器 Cookie和钉钉原始标识；
- Worker 升级后能接收服务端生成的 requester subject，升级前云端不得发送该字段；
- 新 Admin Session 的可信映射可投影到同一 `internal_user_id`，无证据历史 Session 保持 unresolved；
- 普通用户不能读取他人 Conversation；
- 未授权用户直接访问 `/agents/{id}` 或调用 API 均返回 403；
- 管理员跨用户读取写审计。

### 17.4 可靠性

- 本地 Mac 或 Worker 离线不影响登录、Catalog、Admin、FAE 和管理中心；
- 单个 Agent 离线不影响其他 Agent；
- 任务失败不静默切 Agent、模型或执行节点；
- Relay 断线重连不会重复创建业务结果；
- Brain Worker 崩溃后按 durable loop 设计恢复；
- 回滚到不认识 `job_kind` 的旧 Worker/编排器前，必须排空在飞 `metabot_local` 任务，或把它们显式终止为可见失败；不得让旧代码按 `legacy_brain` 接管；
- Nginx、Platform、Admin 均有绝对路径回滚脚本和变更前证据；Platform Nginx 的回滚基线必须是阶段 0 完成后的 `/office` 版本，任何 Nginx 回滚后都要重新验证 `/office/`；
- `/office/health` 返回 404 由 AI ADMIN 验收，Platform 只验证该路径没有进入 8080。

### 17.5 Nginx 与迁移专项

- `nginx -T` 证明只有一个 HTTPS 应用 Server Block，HTTP 块只做 ACME 与跳转；
- `/office/*` 实际响应保留完整安全头，浏览器加载无 CSP 违规；若同时存在 Nginx 与应用 CSP，验收按交集验证；
- 普通请求超过 1 MB 被拒绝；Admin 精确反馈附件端点允许至 12 MB；Platform 精确 Conversation 上传端点上线后允许至 50 MB；其他路径不得继承放大值；
- `/office/chat` 流式完成且不会扩大其他 `/office/*` 的超时和方法；
- 042 前置查询的孤儿数量为 0；非 0 时发布脚本必须在执行迁移前停止；
- Brain-disabled 回滚验收要求根路径 `200` 和 `X-Platform-Entry-State: brain-preparing`，不再接受 `Location: /admin`；
- 回滚旧 Worker 前没有非终态 `metabot_local` job。

### 17.6 发布完成条件

只有以下条件全部满足，才可报告统一入口完成：

```text
PLATFORM_ROOT_IS_USE_ENTRY=true
PLATFORM_CATALOG_INDEPENDENT_FROM_BRAIN=true
PLATFORM_PROFESSIONAL_AGENT_COUNT=8
PLATFORM_BRAIN_DISPATCHABLE_AGENT_COUNT=6
PLATFORM_ADMIN_ROUTE_OWNED=true
AI_ADMIN_OFFICE_PATH_MIGRATION_OK=true
UNIFIED_INTERNAL_USER_ID_VERIFIED=true
FAE_MANAGED_FILES_UNCHANGED=true
NO_SILENT_AGENT_FALLBACK=true
```

## 18. 最终用户体验

员工打开 `https://agent.orbbec.com.cn/` 后，只登录一次钉钉。Brain 已上线时直接进入持续对话；尚未上线时看到清晰准备状态并可进入专业 Agent。

员工可以从 `/agents`：

- 与 HR 或任一 Marketing Agent 持续对话；
- 进入 AI 行政 Agent 办理员工服务，身份不丢失、不二次登录；
- 打开独立的 AI FAE 技术支持页面。

Agent 大脑上线后，它在云端独立完成顶层规划，调用当前用户有权使用且真实在线的六个专业 Agent，并向用户展示任务交付、执行结果和最终综合。MetaBot 是其中一个本地执行方向，不再成为整个平台的单点。
