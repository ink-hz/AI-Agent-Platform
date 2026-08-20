# Agent Platform 与 Agent 大脑协作编排设计

日期：2026-08-20  
状态：产品设计已确认，待实施计划

## 1. 摘要

Orbbec Agent Platform 的下一阶段不是继续扩展管理看板，也不是新增一个普通聊天 Bot，而是从“Agent 管理与观测平台”升级为企业内部的统一 Agent 使用与治理平台。

平台由三个明确层次组成：

- **Agent Platform**：负责企业身份、权限、会话、执行、存储、审计、运行观测、反馈和数据飞轮；
- **Agent 大脑**：作为默认使用入口，理解用户的原始需求，规划和分发任务，组织专业 Agent 协作，检查结果并完成综合交付；
- **专业 Agent**：HR、FAE 和各 Marketing Agent，继续独立维护领域 Prompt、知识、工具与专业能力。

用户登录后应直接使用 Agent 大脑，而不是先面对 Dashboard、指标卡或工作流配置界面。用户仍可从专业 Agent 目录直接进入获授权的专业 Agent。现有总览、Session 全量查看、运行记录、Review、Trace 和数据飞轮进入管理员后台。

第一期只建设 Agent 使用、编排与可见协作闭环，不接入钉钉文档、消息发送、审批、业务数据库等具有外部副作用的企业系统能力。

## 2. 产品目标

### 2.1 目标

1. 用户只需表达原始需求，不必先判断应该使用哪个 Agent。
2. Agent 大脑可以直接处理通用问题，并把专业问题真实交给对应专业 Agent。
3. 一个复杂任务可以被拆成多个串行或并行的专业任务。
4. 用户可以看到 Agent 大脑向谁交付了什么任务、专业 Agent 返回了什么结果，以及 Agent 大脑如何检查和综合。
5. 每次任务都具有完整、可回放的父任务、子任务、事件、来源、附件、反馈和审计记录。
6. 用户仍可绕过编排，从专业 Agent 目录直接开始领域对话。
7. 所有调用服从现有钉钉身份、Agent 授权和 Session 所有权规则。
8. 编排失败必须显式、可诊断，不得通过静默换 Agent、换模型或普通回答伪装成功。

### 2.2 非目标

第一期不建设：

- 钉钉文档创建、消息发送或审批；
- 企业数据库、ERP、CRM 等系统写入；
- 拖拽式工作流；
- Prompt 在线编辑；
- 用户自建或自行发布 Agent；
- 用户选择模型或底层工具；
- 专业 Agent 之间的直接互调；
- 无预算约束的自主循环；
- FAE 对外客户入口的账号或产品形态改造；
- 原始思维链展示。

## 3. 产品信息架构

### 3.1 普通用户入口

```text
/                         Agent 大脑，登录后的默认入口
/sessions                 当前用户自己的历史任务与直接对话
/sessions/{session_id}    Mission 或专业 Agent Session 回放
/agents                   当前用户获授权的专业 Agent 目录
/agents/{agent_id}        专业 Agent 介绍与直接对话入口
```

首页不得以平台介绍、功能菜单、运营指标或管理卡片占据首屏。登录完成后，输入框应立即可用。首屏只保留：

- Agent 大脑身份；
- 主输入框和附件入口；
- 少量真实任务示例；
- 最近任务；
- 不抢占主路径的专业 Agent 入口。

### 3.2 管理员入口

```text
/admin                    管理中心
/admin/overview           现有总览
/admin/agents             Agent Registry、状态与授权
/admin/sessions           授权范围内的全量 Session
/admin/review             Review 与反馈修复闭环
/admin/activity           运行记录
/admin/operations         Operations 与数据飞轮
```

普通用户不显示管理入口。`platform_owner` 和明确授权的管理角色从头像菜单进入管理中心。前端隐藏不是授权边界；所有管理 API 继续由后端鉴权和审计。

## 4. 总体架构

```text
用户
  |
  v
Agent Platform
  +-- DingTalk Identity / Authorization
  +-- Agent Brain API
  +-- Mission / Session / Run Store
  +-- Orchestration Runtime
  +-- Unified Chat Gateway
  +-- Event Stream / Attachments / Feedback
  +-- Review / Trace / Operations / Audit
        |
        +-- MetaBot Adapter --> HR Agent
        |                  --> Marketing Prospecting
        |                  --> Marketing Inbound
        |                  --> Marketing Voice
        |                  --> Marketing Intelligence
        |                  --> Marketing GTM
        |                  --> internal FAE Agent
        |
        +-- future Adapter --> other professional Agents
```

Agent 大脑是 Platform 内受控的规划与编排能力，不是一个拥有特殊旁路权限的独立 Bot。规划可以由当前获支持的 Opus 运行时完成，但产品身份、数据模型和协议不得与某个模型版本绑定。

浏览器只连接 Platform。它不能提交可信的 `user_id`、角色、部门、上游地址、模型、Agent 权限或内部任务状态。Platform 根据服务端 Session 和 Registry 决定允许暴露与调用的能力。

## 5. Agent 大脑职责边界

Agent 大脑可以：

- 与用户进行通用对话和需求澄清；
- 判断请求是否需要专业能力；
- 创建任务计划；
- 选择一个或多个获授权的专业 Agent；
- 决定任务的串行、并行和依赖关系；
- 向专业 Agent 提供完成任务所需的最小上下文；
- 检查结果是否完整、相互冲突、缺少证据或未满足交付要求；
- 发起受预算约束的补充任务；
- 忠实综合专业 Agent 的结果；
- 在部分失败时交付明确标注的已有结果。

Agent 大脑不得：

- 在专业问题上跳过可用的专业 Agent 并假装给出专业结论；
- 篡改专业 Agent 返回的事实或来源；
- 调用用户无权使用的 Agent；
- 将一个领域的完整 Session 或敏感数据默认传给另一个领域；
- 向用户展示系统 Prompt、密钥、原始调试数据或模型内部思维链；
- 静默替换失败的 Agent、模型、工具或数据源。

当没有合适或获授权的专业 Agent 时，Agent 大脑应明确说明能力或权限缺口。它可以继续处理不依赖该能力的部分，但不得把缺失部分表述为已完成。

## 6. 专业 Agent 目录与能力卡

第一期接入以下真实专业 Agent：

- HR Agent；
- FAE Agent；
- Marketing Prospecting；
- Marketing Inbound；
- Marketing Voice；
- Marketing Intelligence；
- Marketing GTM。

五个 Marketing Agent 在目录中归入 `Marketing` 领域分组，但保留各自真实身份、直接入口、权限、Session 和运行记录。Agent 大脑直接调度真实执行者，不新增 Marketing 中间调度 Agent。

Registry 为每个 Agent 增加版本化能力卡，至少包含：

```text
agent_id
display_name
domain_group
mission
capabilities[]
exclusions[]
example_tasks[]
required_inputs[]
accepted_input_types[]
output_types[]
supports_attachments_in
supports_attachments_out
supports_evidence
supports_streaming
supports_cancellation
supports_idempotency
max_duration_seconds
data_classification
adapter_id
capability_version
```

能力卡描述“能做什么、不能做什么以及怎样可靠调用”，不包含完整系统 Prompt、凭据或内部知识内容。能力卡变更需要工程发布和版本记录，不能由业务用户在线修改。

## 7. 中心式协作模型

第一期采用星型拓扑：

```text
HR Agent ------------------+
FAE Agent -----------------+--> Agent 大脑 --> 用户
Marketing Agents ----------+
```

专业 Agent 之间不得直接调用。一个专业 Agent 如果发现缺少其他领域信息，应在结果中声明缺口或提出后续请求。Agent 大脑决定是否创建新的专业任务，再将必要结果传回原 Agent。

这一边界保证：

- 全部任务交付和结果回传具有唯一协调者；
- 权限、数据最小化、取消、超时和失败处理集中可控；
- 不会形成 Agent 相互调用的无限循环；
- 用户看到的协作时间线与真实执行记录一致；
- 后续数据飞轮可以判断路由、交付和复核分别哪里需要改进。

## 8. 任务与数据模型

### 8.1 核心对象

```text
Mission
  用户向 Agent 大脑发起的一次完整目标，拥有一个父 Session。

Plan
  Agent 大脑为 Mission 生成的版本化执行计划。

AgentTask
  交付给一个专业 Agent 的明确任务，包含目标、上下文、约束和交付要求。

ChildRun
  AgentTask 的一次真实执行，绑定 agent_id、adapter、版本和状态。

Handoff
  Agent 大脑与专业 Agent 之间真实传递的任务或结构化结果。

Review
  Agent 大脑对专业结果的可展示验收结论和补充要求。

FinalDelivery
  Agent 大脑基于已完成结果形成的最终交付。
```

### 8.2 Handoff 请求

向专业 Agent 交付的任务至少包含：

```text
task_id
mission_id
objective
relevant_context
constraints
deliverables
accepted_input_refs
deadline
request_id
```

`relevant_context` 必须由 Agent 大脑按任务提取，不默认复制父 Session 全文。附件使用受权限控制的引用，不把对象存储凭据交给 Agent。

### 8.3 Handoff 结果

专业 Agent 返回：

```text
task_id
status
summary
deliverables[]
evidence[]
attachments[]
gaps[]
followup_requests[]
confidence_state
```

自然语言正文可以作为 `deliverables` 的一种内容类型，但状态、来源、缺口和附件必须保持结构化，供 Agent 大脑和 Platform 校验。

### 8.4 唯一性与所有权

- Mission、Plan、AgentTask、ChildRun 和 Handoff ID 均由 Platform 服务端生成；
- Mission 绑定 `internal_user_id`；
- ChildRun 同时绑定父 Mission、真实 `agent_id` 和能力卡版本；
- 普通用户只能读取自己的 Mission 与直接对话 Session；
- 管理员跨用户读取继续受范围授权和审计约束；
- 专业 Agent 的原生 Session ID 只是外部关联标识，不构成访问凭据。

## 9. 执行生命周期

一次请求按以下顺序处理：

1. Platform 验证登录、用户状态和 Agent 大脑使用权限。
2. 保存用户消息并创建幂等 Mission Run。
3. Agent 大脑判断直接回答、单 Agent 任务或多 Agent Mission。
4. 如需专业任务，Agent 大脑创建可展示的 Plan。
5. Platform 校验计划中的 Agent 授权、能力、输入类型和运行限制。
6. Platform 按依赖图串行或并行调用 Adapter。
7. 专业 Agent 持续返回安全进度，最终返回结构化结果。
8. Agent 大脑生成可展示的 Review；必要时发起一次受控补充。
9. Agent 大脑综合最终结果，并保留专业事实与 Evidence 的可追踪关系。
10. Platform 保存 FinalDelivery、反馈入口、Trace 和 Operations 数据。

Agent 大脑可以修改尚未执行的计划，但每次修改必须形成新的 Plan 版本和事件。已经开始的专业任务不得被悄悄改写。

## 10. 用户可见的真实协作时间线

协作过程是一等产品能力，而不是伪造动画。Platform 只展示已经持久化或已被事件存储接受的真实事件。

第一期新增事件：

```text
mission.started
brain.responding
plan.created
plan.revised
task.dispatched
agent.accepted
agent.progress
agent.result
task.reviewed
task.revision_requested
synthesis.started
mission.partially_completed
mission.completed
mission.failed
mission.cancelled
```

每个事件包含 `mission_id`、`run_id`、单调序号、时间、显示主体和安全的用户可见内容。事件流复用统一 Chat Gateway 的 SSE、断线续传、幂等和重放限制。

### 10.1 默认展示

主页面仍然是一条干净的对话时间线。复杂任务在对话中自然出现：

- Agent 大脑的任务拆分；
- 专业 Agent 任务卡；
- 当前安全进度；
- 结果摘要；
- Agent 大脑的复核和补充任务；
- 最终交付。

不新增工作流编辑器，不要求用户查看技术 Trace 才能理解任务状态。

### 10.2 展开内容

用户可以展开任务卡查看：

- Agent 大脑交付给专业 Agent 的完整任务；
- 专业 Agent 返回的完整可展示结果；
- Evidence、附件、耗时和状态；
- 补充要求和修订结果。

管理角色还可以从管理中心查看 Adapter、模型/运行时版本、Trace、错误分类和审计记录。

### 10.3 不展示内容

- 原始思维链；
- 隐藏系统 Prompt；
- 凭据、令牌和内部身份映射；
- 未经脱敏的调试载荷；
- 与当前用户无关的其他 Session 内容。

可展示的“为什么调用这个 Agent”是简洁、可审计的决策摘要，不是模型内部推理转录。

## 11. 用户控制

Mission 执行过程中，用户可以：

- **补充要求**：新信息先交给 Agent 大脑，由它判断需要更新计划、影响哪些未开始任务，或为已完成任务创建补充任务；
- **停止任务**：取消尚未完成且支持取消的 ChildRun，并保留已经产生的部分结果和明确状态。

第一期不允许用户直接篡改单个执行中的 AgentTask，也不允许从 Mission 内绕过 Agent 大脑私聊执行中的专业 Agent。用户需要单独领域对话时，从专业 Agent 入口创建独立 Session。

## 12. 直接专业 Agent 入口

Agent 大脑是默认入口，但不是强制入口。直接进入专业 Agent 时：

- Platform 仍执行身份、Agent 授权和 Session 所有权检查；
- 使用相同的消息、附件、Evidence、Feedback 和事件协议；
- Session 明确标记为 `direct_agent`，不伪装成 Mission；
- 用户可以在后续新建 Agent 大脑 Mission 并引用该 Session，但引用必须由用户显式选择；
- 直接 Session 不会被 Agent 大脑后台自动读取或纳入其他任务。

## 13. 授权与数据隔离

Agent 大脑是所有有效 Platform 成员都可使用的默认平台能力，不单独要求一个 Agent grant。其有效专业能力集合等于当前用户获授权专业 Agent 的集合，而不是系统全部 Agent 的集合。即使用户没有任何专业 Agent grant，仍可使用 Agent 大脑的通用对话和需求澄清能力；涉及未授权专业能力时必须明确报告权限缺口。

在计划校验和每次 ChildRun 启动时，Platform 都重新检查：

```text
用户仍为有效成员
用户有权使用 Agent 大脑
用户有权使用目标专业 Agent
Mission 属于当前用户
输入附件属于当前用户且允许用于目标 Agent
目标 Agent 和 Adapter 当前可用
```

授权不能只在规划时检查。长任务启动子任务前必须重新检查，以处理用户停用或权限撤销。

下游身份凭证继续采用短时、audience-bound 的 Platform 签名令牌，并增加 `mission_id`、`task_id`、`session_owner` 和 `request_id`。下游 Agent 不得信任浏览器提交的身份。

## 14. 可靠性与执行预算

第一期使用可配置的 Platform 硬限制，默认值为：

| 限制 | 默认值 |
|---|---:|
| 单 Mission 专业 AgentTask 总数 | 6 |
| 同时运行 ChildRun | 3 |
| 单任务自动补充/返工 | 1 次 |
| 整个 Mission 计划修订 | 2 次 |
| 单 ChildRun 最大时长 | 采用能力卡上限，且不超过 300 秒 |
| Mission 同步执行总时长 | 15 分钟 |

超过同步总时长时，不无限保持浏览器连接。第一期将 Mission 标记为明确的 `interrupted` 或 `partially_completed`；后续如确有长任务需求，再设计异步任务通知协议。

可靠性规则：

- Adapter 只有声明并实现幂等时，才允许在首个输出前重试一次；
- 首个输出后不得静默重跑；
- Agent 失败不自动换 Agent 或普通模型兜底；
- 一个并行任务失败不删除其他已完成结果；
- Agent 大脑可以形成部分交付，但必须逐项标记未完成内容；
- 取消、超时、权限撤销、Adapter 不可用和格式校验失败使用不同错误状态；
- 用户刷新或网络断开后从最后已接受事件序号恢复，不创建新 Mission；
- Platform 先持久化任务与事件，再向浏览器展示已接受状态。

## 15. FAE 边界

第一期 Agent 大脑调用的是企业内部获授权的 FAE Agent 接口。现有面向外部客户的 FAE 产品、域名、账号体系和专业界面保持独立，不因 Agent 大脑上线而改造。

内部 FAE Adapter 与外部客户入口必须使用独立的受众、凭据和权限边界。Platform 不把企业钉钉身份、内部 Mission 或其他 Agent 的上下文暴露给外部客户 Session。

## 16. Feedback、Review 与数据飞轮

每个 Mission 收集三类可区分反馈：

- 对最终交付的反馈；
- 对某个专业 Agent 结果的反馈；
- 对路由与协作过程的反馈，例如“选错 Agent”“漏掉一个专家”“任务交付不清楚”。

数据飞轮应能够计算：

- 直接回答与专业调用的边界是否正确；
- Agent 选择是否正确；
- 任务交付是否包含必要上下文和明确交付物；
- 专业 Agent 结果是否被 Agent 大脑忠实综合；
- 补充调用是否真正提升结果；
- 失败属于规划、权限、Adapter、Agent、证据还是综合阶段。

反馈不会自动修改 Prompt、能力卡或路由规则。改进仍经过 Review、工程变更、测试和发布。

## 17. API 与协议方向

在现有版本化 Chat Gateway 基础上增加：

```text
POST /api/v1/brain/missions
GET  /api/v1/missions/{mission_id}
POST /api/v1/missions/{mission_id}/messages
POST /api/v1/missions/{mission_id}/cancel
GET  /api/v1/missions/{mission_id}/events?after={last_sequence}
GET  /api/v1/missions/{mission_id}/tasks
GET  /api/v1/tasks/{task_id}
GET  /api/v1/agents
POST /api/v1/agents/{agent_id}/sessions
```

路由名可在实施计划中根据现有代码结构调整，但以下边界不可改变：

- Mission 与直接 Agent Session 是不同类型；
- ID 由服务端生成；
- 所有读取和写入做后端所有权校验；
- SSE 重连不会重复执行任务；
- Agent 选择、Adapter 地址和模型不能由浏览器指定；
- 管理投影不得作为在线授权事实源。

## 18. 实施分解

这份设计作为一个产品基线，实施计划拆成三个可独立验收的增量：

### 增量 A：统一直接使用底座

- 普通用户首页和导航；
- `/admin` 管理入口隔离；
- 版本化 Chat Gateway；
- MetaBot Adapter；
- 专业 Agent 目录和直接 Session；
- 所有权、附件、事件和 Feedback。

### 增量 B：Agent 大脑 Mission

- 能力卡；
- Agent 大脑规划；
- Mission、Plan、AgentTask、ChildRun 和 Handoff；
- 单 Agent 与多 Agent 串并行执行；
- 中心式补充任务；
- 补充要求和停止任务；
- 最终综合。

### 增量 C：可见协作与治理闭环

- 真实协作时间线；
- 子任务展开；
- 路由、交付、结果和综合反馈；
- Review、Trace、Operations 和数据飞轮指标；
- 生产运行与故障演练。

增量 A 是 B 的前置，B 是 C 的前置。不得先做只有动画、没有真实 Mission 和 ChildRun 数据支撑的“协作界面”。

## 19. 测试与验收

第一期至少通过以下自动化和真实环境验收：

1. 登录后根路径直接进入 Agent 大脑输入界面。
2. 普通用户无法看到或直接访问管理入口。
3. Agent 目录只显示当前用户获授权的专业 Agent。
4. 通用问题可以由 Agent 大脑直接回答。
5. HR、FAE 或 Marketing 专业问题会真实创建对应 ChildRun。
6. 一个跨领域请求能形成至少两个并行或串行的真实 AgentTask。
7. 页面展示的任务交付和结果与持久化 Handoff 完全一致。
8. 展开任务卡可以查看完整可展示任务、结果、Evidence 和附件。
9. 专业 Agent 不可用时显式显示失败或部分完成，不发生静默替换。
10. 用户补充要求形成新的消息和必要的 Plan 版本，不篡改历史任务。
11. 用户停止任务后，支持取消的 ChildRun 被取消，已完成结果仍保留。
12. 用户不能读取或继续他人的 Mission、Task、Run、事件和附件。
13. 用户不能通过 Agent 大脑调用未获授权的 Agent。
14. 专业 Agent 不能直接互调或伪造另一个 Agent 的结果。
15. SSE 断线续传不重复创建 Mission 或 ChildRun。
16. 事件、日志和前端不出现原始思维链、系统 Prompt、密钥或钉钉敏感标识。
17. 最终交付中的事实和 Evidence 可以追踪到产生它的真实专业 Agent。
18. Feedback 能分别绑定最终交付、专业结果和路由过程。
19. 管理角色可以在 `/admin` 查看授权范围内的 Mission 运行与审计。
20. 现有 FAE 外部客户入口和独立访问方式不受影响。

## 20. 产品成功标准

第一期上线后，不以“创建了多少工作流”或“用户浏览了多少管理页面”衡量成功。核心指标是：

- 用户从登录到发出第一个真实任务的时间；
- 原始需求无需用户手动选择 Agent 即完成的比例；
- 路由正确率；
- 跨 Agent Mission 完成率；
- 专业结果被忠实综合的比例；
- 用户查看协作过程后对结果的信任度；
- 直接专业 Agent 与 Agent 大脑两种入口的实际复用率；
- 失败是否可理解、可定位、可进入 Review，而不是被隐藏。

最终体验目标是：

> 用户打开 Agent Platform，直接向 Agent 大脑表达原始需求；Agent 大脑在用户面前组织真实的专业 Agent 团队工作，并交付经过检查、可追踪、可继续的结果。
