# FAE 管理工作台设计

**日期：** 2026-08-31

**状态：** 已批准，可以进入实施计划

**涉及仓库：** `AI-Agent-Platform`、`AI-FAE-Agent`

## 1. 背景

Agent Platform 已经具备通用的 Session 检索、Session Detail、逐回答 Feedback 和 Review
治理闭环。FAE 生产数据也已经通过 `source_kind=fae` 与
`agent_id=ai-fae-agent` 进入 Platform 统一可观测模型。现有能力能够回答“某次会话发生了
什么”，但需要在多个通用后台页面之间切换，不能直接回答 FAE 管理者最关心的问题：

1. FAE 今天和近期服务了多少人，当前数据是否新鲜？
2. 哪些 Session 或具体回答需要关注？
3. 发现的问题由谁处理，是否已经修复并验证？
4. FAE 的周期分析得出了什么结论，结论有什么原始证据？

因此，本项目不是重新建设一套 Session 或 Review 系统，而是在 Platform 内增加一个面向
FAE 运营和质量治理语境的独立工作区，把已有通用能力与 FAE 产生的分析报告组织成一条
完整工作链路。

## 2. 已确认的产品决策

### 2.1 首版用户

首版只面向 Platform `Owner` 和 `Admin`，用于全量 FAE 生产会话的运营与质量治理。

内部 FAE 处理人员的细分权限、合作方主管的组织级视图属于后续扩展。合作方主体即使能
使用 FAE，也不会因此自动获得 Platform 工作台权限。

### 2.2 独立工作区，复用通用能力

Platform 左侧导航增加一级入口 **FAE 工作台**，主路由为 `/admin/fae`。工作台具有自己的
信息架构，但不得复制 Session、Feedback、Review 的存储、查询和状态机。

现有通用页面继续服务全 Agent 观测；FAE 工作台通过固定作用域、共享组件和扩展读模型
提供专用体验：

```text
agent_id = ai-fae-agent
source_kind = fae
source_environment = production
```

### 2.3 完整问题闭环

“反馈问题”不是一条孤立备注。首版支持完整闭环：

```text
发现异常
  -> 打开 Session
  -> 定位具体 Turn / Answer
  -> 创建或关联问题
  -> 分类、定责和认领
  -> 提交修复证据
  -> 回放与人工验证
  -> 关闭或重新打开
  -> 纳入后续分析
```

同一根因导致的多条反馈应关联到一个主问题，保留每条原始证据，避免重复处理。

### 2.4 两层分析

工作台展示两类分析：

- **运营指标：** Platform 根据最近一次成功同步的 FAE 镜像计算 Session 数量、活跃主体、
  负反馈、异常结果、响应耗时和问题处理进度。
- **语义分析报告：** FAE 分析任务生成周报或专题报告，总结高频问题、典型案例、知识
  缺口、可能根因和改进建议，再通过稳定契约发布给 Platform。

现有生产业务同步为每日任务。因此首版不得把镜像统计标记成“实时数据”；概览必须展示
数据截止时间和新鲜度。未来如果增加近实时事件同步，同一读模型可以提高刷新频率而无需
重做页面。

## 3. 目标与非目标

### 3.1 目标

1. 让 Owner/Admin 从一个入口管理全部 FAE 生产 Session。
2. 让每个问题追溯到原始 Session、Turn、Answer、Feedback 和必要的运行证据。
3. 让问题从创建走到修复、验证和关闭，并保留完整审计记录。
4. 同时提供有新鲜度说明的运营指标与可阅读的 FAE 语义分析报告。
5. 让报告结论可以下钻到证据，也可以直接创建或关联治理问题。
6. 在 FAE 或报告任务暂时不可用时，保留历史数据查询和问题治理能力。

### 3.2 非目标

- 不在工作台内修改 FAE 模型、Prompt、Knowledge、Tool 或生产运行配置；
- 不新建另一套 Session、Feedback 或 Review 数据源；
- 不由 Platform 代替 FAE 生成语义分析报告；
- 不直接读取 `AI-FAE-Agent` 仓库中的 Markdown、评测目录或临时产物；
- 不向合作方坐席开放 Platform 管理入口；
- 不在首版建立完整的组织级多租户 RBAC；
- 不提供批量删除生产会话或原始反馈的能力；
- 不把普通离线 Eval 报告冒充为生产会话分析报告。

## 4. 方案选择

### 4.1 采用：Platform 内独立 FAE 工作台

工作台拥有专用入口、概览和导航，但组合现有 Observability、Session Detail 与 Review
能力。新增内容主要是 FAE 固定作用域、概览聚合、跨页面联动和报告读模型。

该方案同时满足产品完整性与工程复用，能够形成从异常发现到问题关闭的连续体验。

### 4.2 不采用：只给通用页面增加筛选条件

通用 Sessions 页面已经能够按 FAE 过滤，但它仍以跨 Agent 检索为中心，无法自然承载
FAE 待办、问题生命周期、分析报告和证据下钻。继续堆筛选条件会让管理者频繁切换页面，
也会把 FAE 特有语义扩散到通用页面。

### 4.3 不采用：直接嵌入或跳转 FAE 后台

该方案会把权限、审计和问题治理分散到两个产品中。报告与 Platform Session 镜像之间也
难以形成稳定深链。Platform 应消费 FAE 发布的数据契约，而不是通过 iframe、仓库文件或
内部页面耦合 FAE 实现。

## 5. 信息架构与路由

左侧导航增加一级入口 `FAE 工作台`。进入后保持 Platform 现有应用框架，工作区内包含四个
一级视图：

| 视图 | 建议路由 | 主要职责 |
|---|---|---|
| 概览 | `/admin/fae` | 运营摘要、异常与待办、最新报告 |
| Sessions | `/admin/fae/sessions` | FAE 生产会话检索与查看 |
| 问题治理 | `/admin/fae/issues` | 问题认领、定责、修复、验证和关闭 |
| 分析报告 | `/admin/fae/reports` | 周报、专题报告及其证据链 |

详情路由保持稳定且可分享：

```text
/admin/fae/sessions/:session_key
/admin/fae/issues/:issue_id
/admin/fae/reports/:report_id
```

筛选、时间范围、分页和选中项应写入 URL 查询参数。刷新、返回和分享链接后必须恢复相同
上下文。

## 6. 页面设计

### 6.1 概览

概览不是传统 BI 大屏，而是一个紧凑的治理入口，按以下顺序展示：

1. 数据截止时间、同步状态和统计时间范围；
2. Session、活跃主体、负反馈、异常结果、待处理问题等摘要；
3. 需要处理的事项：新负反馈、高严重度问题、超期问题、异常 Session；
4. 近期趋势与高频问题，不堆叠低价值图表；
5. 最新周报和专题报告。

每个数字都可以进入已经带好筛选条件的 Sessions 或问题列表。概览不得展示无法下钻或没有
口径说明的装饰性指标。

### 6.2 Sessions

Sessions 默认固定为 FAE 生产作用域，不再要求管理员手动选择 Agent 或 Source。支持按以下
条件组合筛选：

- 时间范围；
- 用户、部门或未来的主体类型与合作方组织；
- 渠道；
- Session 标题或内容关键字；
- Feedback 情况；
- Outcome、Fallback 和耗时区间；
- 是否已有治理问题及问题状态。

列表优先展示时间、主体、部门/组织、渠道、轮次数、结果、反馈和问题状态。底层继续使用
现有游标分页和 canonical Session key，不建立 FAE 专属分页语义。

### 6.3 Session Detail

Session Detail 采用“对话正文 + 右侧治理面板”结构：

- 中间按时间展示完整对话、附件、来源、Evidence 和公开执行阶段；
- 右侧展示主体、渠道、Session 结果、耗时、Feedback、Review、关联问题和审计摘要；
- 管理员可从具体 Answer 发起问题，系统自动携带 Session、Turn、问题、回答、模型结果和
  现有 Feedback；
- 已存在相似问题时优先提供关联入口，不阻止管理员在确有必要时新建问题；
- Session 级问题必须明确其作用范围，不能伪装成某个不存在的 Turn 证据。

现有通用 `SessionDetailPage` 与会话渲染组件应抽取或参数化复用，不能复制一份 FAE 版本后
分别演进。

### 6.4 问题治理

问题列表和详情复用现有 Review 闭环，首版至少保留：

- 来源 Session 与 Turn；
- 标题、分类、责任层、严重程度、优先级和影响范围；
- 状态、负责人、创建人与关键时间；
- 关联的 Feedback、相似案例和主问题；
- 根因、修复说明、代码或知识变更证据；
- 机器验证、回放验证和人工/独立语义评审；
- 关闭、重新打开和合并记录。

问题状态机沿用现有 Review 领域规则。工作台只能增加 FAE 作用域和呈现方式，不能创造与
通用 Review 不兼容的第二套状态。

### 6.5 分析报告

报告列表按周报与专题报告分组，展示报告周期、数据截止时间、生成状态、生成版本和关联
问题数量。报告详情保持技术报告的阅读感，主体结构为：

第一屏面向管理层，固定展示四类成果：

1. 累计服务规模；
2. 复杂业务承接；
3. 已形成业务价值；
4. 增长潜力。

首屏以下只保留四个一级分析维度，不扩展成七八个并列频道：

1. **使用情况**：使用趋势、多轮对话、图片附件、非工作时段、高频产品与场景；
2. **业务价值**：已承接的 FAE 工作、复杂技术咨询、已实现价值与潜在价值；
3. **回答效果**：独立复审覆盖、质量分布、完全解决、首轮解决、多轮收敛、反馈、时延与
   fallback；
4. **业务洞察与改进**：高频型号和需求、资料/兼容性/质量/功能信号、根因族、行动优先级和
   下一阶段建议。

可靠性、性能、Feedback 和问题闭环放在上述四个维度的下钻内，不单独再造一级导航。
每个数字必须同时展示周期、分母或口径；“已实现价值”和“潜在转化价值”必须视觉分区。
典型案例只能显示经业务批准的脱敏案例；当当前报告没有已批准案例时，页面明确显示
“典型案例待业务批准”，不从原始会话自动摘取充数。

每项主要发现都提供 `查看证据` 与 `创建/关联问题`。证据链接精确到 Session、Turn 或问题，
不得只链接到 Sessions 列表首页。

## 7. 系统边界与数据流

采用“**Platform 管理，FAE 生产分析**”的职责边界：

```text
FAE 生产会话
  -> Platform FAE 镜像与 canonical read model
  -> FAE 工作台运营指标 / Session 检索
  -> Platform Feedback 与 Review 问题治理

FAE 分析任务
  -> 版本化报告发布契约
  -> Platform 报告投影
  -> 报告阅读、证据下钻和问题关联
```

### 7.1 Platform 负责

- FAE 管理入口和页面信息架构；
- FAE Session 镜像、canonical 查询与运营指标聚合；
- Feedback、Review、问题状态机和审计；
- 报告的接收、校验、持久化、展示和证据解析；
- Platform Owner/Admin 权限判断。

### 7.2 FAE 负责

- 真实生产 Session、Turn、Answer、附件、来源和 Trace；
- 报告分析任务及其分析质量；
- 报告中发现、根因假设、案例和建议的生成；
- 按版本化契约发布不可变报告版本；
- 报告运行状态、失败原因和分析版本。

### 7.3 禁止的耦合

- Platform 不读取 FAE 仓库文件或运行目录作为产品接口；
- Web UI 不直接访问 FAE 数据库或私有 API；
- FAE 不直接修改 Platform Review 表；
- 报告不得用可变自然语言路径代替稳定 Evidence ID；
- 页面不得根据 FAE 原生表名分支。

## 8. 工作台读模型与 API

工作台 API 位于 Platform 管理 API 下，并由服务端强制注入 FAE 作用域。前端传入其他
`agent_id` 或 `source_kind` 时应被忽略或拒绝，不能只依赖 UI 隐藏筛选器。

建议的读接口：

```text
GET /api/admin/fae/overview
GET /api/admin/fae/sessions
GET /api/admin/fae/sessions/{session_key}
GET /api/admin/fae/issues
GET /api/admin/fae/issues/{issue_id}
GET /api/admin/fae/reports
GET /api/admin/fae/reports/{report_id}
```

Session 与 Issue 接口可以是对现有 Observability/Review service 的薄封装，也可以由统一服务
接受可信的服务端 scope；不得复制 repository 查询。

概览返回分区结果，单个数据源失败不使整个页面失败：

```text
freshness
summary
attention_items
trends
latest_reports
```

每个分区携带自己的 `status`、`as_of` 和可选错误码。所有聚合数字必须共享明确的时间范围
和时区，默认使用 `Asia/Shanghai` 展示，服务端时间仍以带时区时间戳传输。

## 9. 报告发布契约

FAE 发布的每个报告版本至少包含：

```text
report_id
report_version
report_type: weekly | topic
title
period_start / period_end
data_cutoff_at
generated_at
status: generating | ready | failed
analysis_version
summary
metrics[]
findings[]
recommendations[]
failure_code / failure_summary
```

每个 `finding` 至少包含：

```text
finding_id
severity
title
description
root_cause_hypothesis
impact_scope
evidence_refs[]
recommendation_refs[]
linked_issue_ids[]
```

每个 `evidence_ref` 使用稳定、可校验的类型化引用：

```text
kind: session | turn | feedback | issue
canonical_key
optional_excerpt
```

`optional_excerpt` 只用于报告阅读，不能替代原始证据。Platform 接收报告时校验 schema、版本、
时间范围、重复 ID 和引用格式；无法解析的引用被标记为不可用，但不会丢弃整个已经通过
schema 校验的报告。

报告采用不可变版本。重新生成会创建新的 `report_version`，旧版本继续可审计。Platform
可以为报告附加本地的 Issue 关联投影，但不得改写 FAE 发布的分析正文。

报告超过其声明的数据截止时间或 Platform 已有更新的 Session generation 时，显示“数据已
过期”，而不是把报告状态改成失败。

## 10. 写操作、权限与审计

### 10.1 首版权限

- `Owner`：查看全部数据并执行完整问题治理操作；
- `Admin`：查看全部数据并执行其现有 Review 权限允许的操作；
- 其他角色：导航不可见，接口返回 403；
- 合作方主体：无 Platform Web Session，也无工作台访问权。

工作台入口与所有后端接口分别校验权限，不能只做前端路由守卫。

### 10.2 审计范围

以下操作必须记录操作者、发生时间、目标对象、前后状态和原因：

- 创建、关联、合并或重新打开问题；
- 修改分类、严重程度、责任层、负责人和影响范围；
- 提交修复证据或验证结论；
- 关闭问题；
- 手动触发报告重新生成；
- 查看受保护的跨主体 Session Detail。

报告查看本身可以使用访问日志，不要求为每次普通列表加载创建领域审计事件。

### 10.3 隐私与展示

工作台优先使用 Platform 已有的 presentation-safe 主体字段。不得把 Provider Token、原始
外部身份、密钥、私有文件路径或未脱敏 Trace Payload 暴露给前端。未来合作方数据进入后，
组织和主体筛选仍通过 canonical subject 投影，不直接读取身份 Provider 原始值。

## 11. 失败、新鲜度与空状态

- **FAE 不可用：** 已同步 Session、问题和历史报告继续可读；页面显示运行或同步异常。
- **同步失败：** 保留上一成功 generation，明确显示最后成功时间，不把旧数据标成实时。
- **报告生成失败：** 展示失败摘要和有权限的重新生成入口，不影响其他视图。
- **报告生成中：** 保留上一 ready 版本，同时显示新版本正在生成。
- **证据缺失：** 说明缺失、未同步或无权限，不能打开空白详情。
- **分区失败：** 概览其余分区继续渲染；失败卡片提供简短原因。
- **重复问题：** 展示可能的主问题和证据重合，允许关联；相似性建议不能自动合并。
- **空数据：** 区分没有 Session、筛选无结果、尚未同步和接口失败。
- **写入冲突：** 使用版本或更新时间检测并发修改，提示刷新，不静默覆盖他人处理结果。

## 12. 工程复用与迁移策略

首版实现应按以下边界演进：

1. 把 Sessions 列表筛选、表格和 Session Detail 中可复用部分提取为共享能力；
2. 通过服务端 FAE scope 配置工作台，不 fork 一套 FAE repository；
3. 复用现有 Review 状态机、证据、验证与关闭规则；
4. 新增 Overview 聚合 service 与报告 read model；
5. 在报告契约稳定前，可使用契约 fixture 开发 Platform UI，但不得用固定演示报告进入生产；
6. 现有 `/admin/sessions` 与通用 Review 页面保持兼容，原深链不失效；
7. FAE 报告发布与 Platform 报告消费可以分仓实现，但必须共享同一版本化 contract fixture
   和兼容性测试。

## 13. 测试策略

### 13.1 Backend

- 所有工作台接口强制限定 `ai-fae-agent`、`fae` 和生产环境；
- 无权限用户即使直接调用接口也得到 403；
- 概览指标口径、时间范围和新鲜度正确；
- Session、Feedback、Issue 与报告 Evidence 能双向解析；
- 报告 schema、版本、幂等发布和不可变版本行为正确；
- 旧报告、生成中、失败、过期和部分坏引用行为正确；
- FAE 或同步异常时继续读取上一成功 generation；
- 问题写入沿用既有状态机并产生审计事件；
- 并发修改不会静默覆盖；
- API 不返回 Secret、原始 Provider 身份或未脱敏 Trace。

### 13.2 Frontend

- 左侧一级入口只对允许角色显示；
- 四个视图及稳定详情路由正确；
- URL 能恢复筛选、分页和选中上下文；
- 概览数字可以带筛选条件下钻；
- Session Detail 能从具体 Answer 创建或关联问题；
- 报告 Finding 能打开精确 Evidence 并创建问题；
- 加载、空数据、失败、旧数据和部分失败状态可区分；
- 窄屏下对话正文和治理面板仍可读，核心操作不依赖 hover；
- 通用 Sessions 和 Review 页面回归行为不变。

### 13.3 跨仓契约

- FAE producer fixture 能通过 Platform consumer contract test；
- Platform 支持当前版本和约定的前一兼容版本；
- 未知必需版本被明确拒绝并记录，不进行宽松猜测；
- Evidence 引用使用真实 canonical key fixture，不能只测孤立文本。

## 14. 上线顺序

1. 建立共享 FAE scope、工作台路由和权限守卫；
2. 上线概览、Sessions 与 Session Detail，不改变通用页面；
3. 接入完整问题治理和审计；
4. 与 FAE 仓库冻结报告 contract 和 fixture；
5. 接入报告列表、阅读、Evidence 下钻与 Issue 关联；
6. 使用真实生产镜像做 Owner/Admin 验收后开放导航入口。

报告接口未就绪时，工作台可以先上线前三个视图，但分析报告页只能展示明确的“报告能力
尚未接入”状态，不能用假数据填充。

## 15. 验收标准

1. Owner/Admin 能从左侧一级入口看到全部 FAE 生产 Session，并明确知道数据截止时间。
2. 任意治理问题都能追溯到原始 Session 和具体 Turn/Answer，Session 级问题有明确作用域。
3. 问题可以完成创建、定责、修复证据、验证、关闭和重新打开，关键动作均可审计。
4. 报告的每个关键 Finding 都具有可点击、有效或明确失效原因的 Evidence 链。
5. 管理员能从报告 Finding 创建或关联 Issue，并从 Issue 返回报告与原始 Session。
6. FAE 服务、同步或报告任务异常时，历史数据与问题治理仍可用且不会误报新鲜度。
7. 普通用户和合作方主体无法通过导航、直接 URL 或 API 访问工作台。
8. 通用 Sessions、Session Detail 和 Review 功能不存在因工作台上线产生的行为回归。

满足以上条件后，FAE 工作台才算形成完整的“会话观察—问题治理—分析反馈”管理闭环。
