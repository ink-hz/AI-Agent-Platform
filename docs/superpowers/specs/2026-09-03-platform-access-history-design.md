# Agent Platform 登录与页面访问记录设计

**日期：** 2026-09-03

**状态：** 产品设计已确认，等待实施计划

**涉及系统：** Agent Platform、AI ADMIN、AI FAE、VOC、HR Agent、Marketing Agent

## 1. 决策

Agent Platform 增加一个统一的访问记录中心，回答两个问题：

1. 哪个企业用户在什么时间成功登录了系统；
2. 登录后，该用户在什么时间进入了哪个业务页面。

记录范围是 `https://agent.orbbec.com.cn` 根域名下所有使用 Platform 钉钉企业身份的页面，包括 Platform 自身页面以及由独立应用承载的 `/office/*`、`/fae/*` 和 `/voc/*`。

访问记录的查看权限仅授予 `platform_owner`。当前唯一的 Platform Owner 是“苍渊”，因此当前只有苍渊能够查看。实现不得按花名字符串硬编码权限，也不得向 `platform_admin`、`management_viewer` 或普通成员开放查询接口。

## 2. 范围

### 2.1 纳入记录

- Agent 大脑与历史 Conversation；
- 专业 Agent 目录；
- `/office/*` 行政门户与行政问答；
- `/fae/*` 内部 FAE Agent 与 FAE 管理工作台；
- `/voc/*` VOC Agent、个人记录与 VOC 管理；
- `/hr/*` HR Agent；
- `/marketing/*` 五个 Marketing Agent；
- `/admin/*` Platform 管理中心；
- 企业账号和 AI 工程笔记；
- 后续新增且使用同一 Platform 钉钉身份的同域工作区。

### 2.2 不纳入记录

- 未完成身份验证的登录页浏览；
- 静态资源、健康检查、API 请求和后台任务；
- 未登录或无权访问页面的尝试；
- 面向外部客户且不使用 Platform 钉钉身份的 `https://fae.orbbec.com.cn/`；
- 按钮点击、滚动、停留时长、输入内容和业务操作明细。

业务操作与敏感读取继续使用现有治理审计机制。访问记录不替代治理审计。

## 3. 产品呈现

新增 Owner 专属页面：

```text
/admin/access
```

管理中心导航仅在当前账号为 `platform_owner` 时显示“访问记录”。直接请求页面或 API 时仍必须由后端重新鉴权；隐藏导航不是授权边界。

页面按时间倒序展示：

```text
苍渊      09-03 10:20:12    登录成功 · 钉钉扫码
苍渊      09-03 10:20:18    Agent 大脑
苍渊      09-03 10:24:03    行政 · 服务门户
西门吹雪  09-03 10:31:42    FAE · 分析报告
```

首版提供四个简单筛选项：

- 时间范围；
- 企业花名，按通讯录规范化后的唯一花名精确匹配；
- 工作区；
- 事件类型：登录或页面访问。

每页显示 50 条并提供上一页、下一页。首屏默认最近 7 天。所有时间由后端生成并以北京时间展示。

## 4. 事件模型

访问记录使用独立表 `platform_control.user_access_events`，不写入高频且语义不同的 `platform_control.audit_events`。

迁移在当前主线合入 HR 的 `064_conversation_attachments.sql` 后使用：

```text
065_user_access_history.sql
```

若实施开始前主线已占用 065，必须先顺延编号并保持迁移连续，不得覆盖已合入迁移。

表至少包含：

```text
access_event_id          uuid primary key
internal_user_id        uuid not null references platform_control.internal_users
session_id              uuid not null, intentionally no foreign key
event_kind              login_succeeded | page_view
login_kind              qr | in_client | null
workspace_key           bounded text | null
page_key                bounded text | null
agent_id                bounded text | null
occurred_at             timestamptz not null, server generated
```

约束：

- `login_succeeded` 必须有 `login_kind`，不得有页面字段；
- `page_view` 必须有 `workspace_key` 和 `page_key`，不得有 `login_kind`；
- 一个 Web Session 只能有一条 `login_succeeded`；
- 页面事件的 `access_event_id` 由浏览器为一次真实导航生成，重试相同 ID 不得重复计数；
- `internal_user_id`、`session_id` 和 `occurred_at` 只能由服务端身份上下文提供，浏览器不得提交；
- `session_id` 只用于把一次登录后的页面轨迹串起来，不保存 Session Token，也不引用 `web_sessions`：现有维护任务会在 Session 绝对过期后删除该行，访问记录必须独立保留 90 天且不能阻止凭据清理；
- 应用角色只能通过限定函数追加和读取自己的写入结果，不能更新或删除历史行；
- 维护角色只能删除超过保留期的行。

数据库以 `(occurred_at desc, access_event_id desc)` 建立查询索引，并为 `internal_user_id`、`workspace_key` 和 `event_kind` 的常用筛选建立有界索引。

## 5. 登录记录

钉钉扫码和钉钉客户端免登都必须记录。

登录事件与新 Web Session 的创建放在同一个数据库事务中。当前 `consume_attempt_and_issue_session_v22` 的后继函数在成功消费登录尝试并创建 Session 后，同时插入唯一的 `login_succeeded` 记录，再提交事务。

这保证：

- 已返回给浏览器的成功 Session 一定有登录记录；
- 重放同一个登录尝试不会产生第二个 Session 或第二条登录记录；
- 数据库写入失败时登录整体失败，不出现“登录成功但记录不存在”的部分提交。

失败登录没有可信的 `internal_user_id`，首版不进入用户访问记录。现有认证限流与错误观测继续负责失败登录问题。

## 6. 页面访问上报

### 6.1 中央接口

Platform 提供：

```text
POST /api/v1/access-events/page-view
```

请求只包含：

```json
{
  "access_event_id": "浏览器为本次导航生成的 UUID",
  "workspace_key": "office",
  "page_key": "office.services",
  "agent_id": null
}
```

接口从 Platform Session 获取用户与 Session，使用数据库时间写入，成功返回 `204`。浏览器重复发送相同 `access_event_id` 时仍返回 `204`，但数据库只有一行。

### 6.2 上报时机

页面只有在以下条件都满足后才上报：

1. Platform 钉钉身份已经验证成功；
2. 当前页面的服务端授权已经通过；
3. 应用已经把原始地址解析为规范页面键。

初次页面加载、刷新以及一次真实的 SPA 路由切换各记一次。React 重渲染、数据刷新、重试接口和切换页内标签不得重复上报，除非该标签本身是产品定义中的独立页面。

### 6.3 同源写入边界

独立应用不复制 Platform Cookie，也不把 Cookie、用户 ID 或身份字段放进上报正文。浏览器在同源请求中自然携带现有 `__Host-platform_session`，Platform 后端独立解析身份。

为了让 `/office/*`、`/fae/*` 和 `/voc/*` 不需要读取 Platform CSRF Token，页面访问接口使用一个精确、受限的写入边界：

- 只豁免这一条端点的普通业务 CSRF Token 要求；
- 强制 `Origin` 精确等于 Platform 的 HTTPS Origin；
- 强制已认证 Session；
- 只接受 JSON、小于 2 KB 的请求体和已登记页面键；
- 按 Session 限制为每分钟最多 120 次；
- 用户只能为自己追加页面事件，不能选择 actor、Session 或时间；
- 任意校验失败都不写记录。

来自 `fae.orbbec.com.cn` 或其他 `orbbec.com.cn` 子域的请求虽然可能属于浏览器的 SameSite 范围，但 Origin 不相同，必须拒绝。

## 7. 规范页面目录

Platform 维护一个只包含展示语义的页面目录。上报和读取均使用固定 `page_key`，不保存原始 URL。

首版页面目录必须覆盖发布时所有可达且已授权的页面。下表是必须具备的最低集合；实施计划还要逐项对照各应用的正式路由表，任何未映射页面都会阻止发布：

| 工作区 | 页面键示例 | 展示名称 |
|---|---|---|
| Platform | `platform.brain` | Agent 大脑 |
| Platform | `platform.agent_directory` | 专业 Agent |
| Platform | `platform.account` | 企业账号 |
| Office | `office.chat` | 行政 · 问答 |
| Office | `office.services` | 行政 · 服务门户 |
| Office | `office.service_detail` | 行政 · 服务详情 |
| Office | `office.feedback` | 行政 · 服务反馈 |
| Office | `office.shuttle` | 行政 · 班车 |
| Office | `office.lodging` | 行政 · 住宿 |
| FAE | `fae.workspace` | FAE Agent |
| FAE | `fae.conversation` | FAE · 对话 |
| FAE | `fae.manage.overview` | FAE · 管理概览 |
| FAE | `fae.manage.sessions` | FAE · Sessions |
| FAE | `fae.manage.issues` | FAE · 反馈与修复 |
| FAE | `fae.manage.reports` | FAE · 分析报告 |
| VOC | `voc.workspace` | VOC Agent |
| VOC | `voc.records` | VOC · 我的记录 |
| VOC | `voc.manage` | VOC · 管理 |
| HR | `hr.workspace` | HR Agent |
| HR | `hr.conversation` | HR Agent · 对话 |
| Marketing | `marketing.workspace` | Marketing Agent |
| Marketing | `marketing.conversation` | Marketing Agent · 对话 |
| Admin | `admin.overview` | 管理中心 · 总览 |
| Admin | `admin.agents` | 管理中心 · Agent |
| Admin | `admin.sessions` | 管理中心 · Session |
| Admin | `admin.review` | 管理中心 · 复审闭环 |
| Admin | `admin.activity` | 管理中心 · 运行记录 |
| Admin | `admin.identity` | 管理中心 · 身份管理 |
| Admin | `admin.governance` | 管理中心 · 治理审计 |
| Admin | `admin.access_history` | 管理中心 · 访问记录 |

详情页面仍使用相同的规范页面键。例如具体 Session 地址只记录为 `admin.session_detail`，不记录 Session Key。具体 FAE Issue 只记录为 `fae.manage.issue_detail`，不记录 Issue ID。

Marketing 页面可以在 `agent_id` 中记录规范 Agent ID，以区分 Prospecting、Inbound、Voice、Intelligence 和 GTM；该字段必须通过 Agent Catalog 校验。任何 Conversation ID、Session Key、Issue ID、报告 ID、客户 ID 或候选人 ID 都不得进入访问记录。

Office 的 `?view=services` 等查询参数只用于应用本地选择规范页面键，原始查询字符串不得发送或保存。

## 8. 各应用接入责任

### 8.1 Agent Platform WebUI

Platform 在统一 App Shell 中放置一个 `AccessEventReporter`。它消费已经解析并授权的 Route，而不是读取原始 `window.location.href`。

该 Reporter 覆盖 Agent 大脑、目录、HR、Marketing、Platform 管理中心和 Platform 承载的 FAE 管理页面。它必须忽略登录页、错误页、兼容重定向中间页和未授权页面。

### 8.2 AI ADMIN

AI ADMIN 在其现有身份启动成功之后，对 `/office/*` 的规范路由调用同一个中央接口。此次接入不得改变 `/office/` 路由、行政门户首页、行政问答、服务、班车、住宿、反馈、CSRF 或业务授权。

### 8.3 AI FAE 与 VOC

由 `/fae/*` 和 `/voc/*` 的实际页面拥有者，在完成 Platform 身份与本地授权后调用中央接口。公共 `fae.orbbec.com.cn` 不接入。

### 8.4 新工作区

以后任何新增的 Platform 钉钉身份页面，在发布前必须：

1. 登记规范 `workspace_key` 和 `page_key`；
2. 在授权成功后发送一次页面访问事件；
3. 通过“不含原始 URL 和业务对象 ID”的测试；
4. 通过 Owner 可见、其他角色 403 的查询验收。

## 9. 查询接口与权限

Owner 查询接口：

```text
GET /api/v1/manage/access-events
```

支持有界的 `date_from`、`date_to`、`display_name`、`workspace_key`、`event_kind`、`limit` 和 `offset`。所有输入都需服务端校验；最大分页大小为 100。

后端授权规则只有一条：

```text
authenticated account role == platform_owner
```

当前这等价于只有苍渊可见，但权限不依赖字符串 `苍渊`。Owner 更换后，旧 Owner 立即失去查询能力，新 Owner 获得能力。

读取访问记录本身会正常产生一条 `admin.access_history` 页面访问记录，但不会向治理审计表写入每一次列表读取，避免递归和高频审计噪声。

## 10. 数据最小化与保留

访问记录明确不得保存：

- Cookie、Token、Authorization Header 或 CSRF Token；
- 钉钉原始身份标识；
- IP 地址、User-Agent 全文或设备指纹；
- URL 查询参数、fragment 或 Referer；
- 页面输入、对话、搜索词、表单、文件名和附件；
- 具体业务对象 ID。

记录默认保存 90 天。现有控制面维护任务每日调用限定的保留函数删除超过 90 天的记录。首版不提供人工单条编辑或删除功能。

本功能从生产发布完成时开始记录，不尝试从旧 Nginx 日志反推历史用户访问。旧日志无法可靠关联 Platform 内部身份，伪造回填会降低可信度。

## 11. 失败语义

- 页面事件上报失败不得阻断页面访问、行政业务、Agent 对话或管理操作；
- Reporter 失败时不弹出面向普通用户的错误提示，只记录有界的前端诊断；
- 查询接口或数据库不可用时，Owner 页面明确显示“访问记录暂不可用”，不得显示空数据冒充无访问；
- 未知页面键返回 400，未登录返回 401，非 Owner 查询返回 403；
- 重复事件返回成功但不重复写入；
- 登录记录与 Session 创建是同一事务，失败时不发放 Session；
- 页面访问日志不是安全授权依据，也不得参与用户权限判断。

## 12. 测试与验收

### 12.1 数据库与后端

- 登录 Session 与 `login_succeeded` 原子创建；
- QR 与 in-client 登录分别记录正确的 `login_kind`；
- 同一 Session 不能重复产生登录记录；
- 页面事件从认证上下文取得用户、Session 和服务端时间；
- 相同事件 ID 重放幂等；
- 页面事件字段组合、页面键和 Agent ID 均受数据库与应用校验；
- 90 天保留边界正确；
- app 角色不能更新或删除历史行；
- Owner 查询成功，Platform Admin、Viewer 和 Member 均返回 403；
- 数据库失败不影响已经存在的普通页面请求。

### 12.2 前端

- 初次打开、刷新和 SPA 路由切换各产生一次事件；
- React 重渲染和数据重新获取不重复上报；
- 未登录、无权访问、404 和兼容重定向中间页不上报成功页面访问；
- 动态页面不发送 Conversation、Session、Issue、报告或候选人 ID；
- Owner 能看到访问记录导航与分页列表；
- Platform Admin 和其他角色看不到入口，即使直接访问也由后端拒绝；
- 上报失败不影响业务页面。

### 12.3 跨应用

- 真实钉钉账号登录后产生一条登录记录；
- 依次进入 Agent 大脑、Office 服务门户、FAE、VOC、HR、一个 Marketing Agent 和管理中心，各产生正确的规范页面事件；
- `/office/?view=services` 只保存 `office.services`，不保存查询字符串；
- 外部 `fae.orbbec.com.cn` 不产生 Platform 页面事件；
- Office 接入前后行政门户、行政问答、班车、住宿和反馈行为一致；
- 任一工作区上报故障不影响其他工作区。

## 13. 发布顺序

1. 等 HR 工作台分支合入主线后，将实现分支更新到最新主线并确认迁移编号；
2. 发布数据库表、原子登录记录、页面上报接口和 Owner 查询 API；
3. 发布 Platform Reporter 与 `/admin/access`；
4. 分别接入 Office、内部 FAE 和 VOC 页面拥有者；
5. 使用真实钉钉身份完成跨工作区验收；
6. 验证 Platform Admin 无权读取、记录不含原始 URL 或业务对象 ID；
7. 观察 24 小时写入量、失败率和重复率后确认 90 天容量估算。

只完成 Platform 页面上报而未覆盖 Office、FAE 和 VOC 时，不得宣称“根域名全部页面访问记录”已经完成。

## 14. 完成定义

功能只有同时满足以下条件才算完成：

- 每次成功钉钉登录都有唯一、原子的登录记录；
- `agent.orbbec.com.cn` 下所有已启用 Platform 身份的业务工作区都记录规范页面访问；
- 记录能够回答“谁、什么时候、进入了什么页面”；
- 记录不包含用户输入、身份凭据、原始 URL 参数或具体业务对象 ID；
- 当前只有 Platform Owner 苍渊能够查看，其他角色的后端请求返回 403；
- 访问记录写入失败不会影响 Agent、行政、FAE、VOC、HR、Marketing 或管理中心的正常使用。
