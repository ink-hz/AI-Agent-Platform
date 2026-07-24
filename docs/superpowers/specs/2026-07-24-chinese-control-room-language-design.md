# Orbbec Agent Platform 中文控制台语言与表达设计

- 日期：2026-07-24
- 状态：设计已确认
- 范围：WebUI 全站文案、语言映射、日期表达与必要的视觉细节

## 1. 目标

保留 Orbbec Agent Platform 的系统监控大盘定位，将当前偏海外 SaaS、开源 Admin 模板的表达改造成中文优先的内部 Agent 管理控制台。

本次不改变监控数据、页面能力和信息架构。核心变化是让系统界面自然说中文，同时保持技术名词、Agent 身份和业务内容的原始语言，体现这是围绕 Orbbec Agent 集群持续建设的专属产品。

## 2. 核心语言规则

### 2.1 使用中文的内容

以下内容属于系统界面，由 Platform 负责表达，统一使用自然、直接的中文：

- 顶部导航与页面标题；
- 区域标题、字段名称、状态解释和操作入口；
- 加载、空状态、错误与降级提示；
- 日期、相对时间、运行天数和数量说明；
- 系统生成的 Agent 状态和观测结论；
- 返回链接、筛选项、分页和辅助说明。

中文文案采用内部管理平台语气，不使用营销话术，不使用开源模板式占位句，也不通过英文装饰标题制造“专业感”。

### 2.2 保持原文的内容

以下内容不是 Platform 创作的界面文案，不做强制翻译：

- Agent、Bot、Session、Trace、Skill、Backend 等技术名词；
- Agent 名称、业务名称、模型名、Engine、Backend 值和渠道名称；
- Agent 职责介绍及其他来自 registry、数据库或远端系统的业务原文；
- 用户问题、Agent 回答、工具调用、Trace 内容和证据内容；
- 代码标识、Session key、Trace ID、版本号和其他机器标识。

原文是中文就显示中文，原文是英文就显示英文。Platform 不为追求表面统一而改写业务内容。

### 2.3 禁止的表达方式

- 不再使用 `FLEET DIRECTORY`、`RUNTIME DETAIL`、`EVIDENCE` 等全大写英文 eyebrow；
- 不把普通界面概念包装成 `Fleet`、`Directory`、`Insights` 等泛化产品术语；
- 不把 `Agent`、`Session` 等已约定技术名词生硬翻成“智能体”“会话记录”等新别名；
- 不翻译模型名、Backend 值、Agent 名称或业务原文；
- 不在主页面展示只对开发者有意义的 source 名称和原始枚举值；
- 不使用“Awesome”“Enterprise-grade”一类开源或营销式文案。

## 3. 全站词汇规范

| 当前界面文案 | 新界面文案 |
|---|---|
| Overview | 总览 |
| Agents | Agent |
| Sessions | Session |
| Activity | 运行记录 |
| Fleet Overview | Agent 集群总览 |
| Fleet Directory | Agent 列表 |
| Recent Activity | 最近运行记录 |
| Recent Sessions | 最近 Session |
| Runtime | 运行状态 |
| Runtime Detail | 运行详情 |
| Production timeline | 运行周期 |
| Observed sources | 观测依据 |
| View all activity | 查看全部运行记录 |
| View Runtime detail | 查看运行详情 |
| All Agents | 返回 Agent 列表 |
| Session Replay | Session 回放 |
| Question | 用户提问 |
| Answer | Agent 回答 |
| Loading | 正在加载 |
| No Agents found | 暂无 Agent |
| Page not found | 页面不存在 |

技术名词嵌入中文句子时保留原写法，例如“查看全部 Session”“最新 Trace”“Backend 为 `pty`”。

## 4. 状态语言

后端枚举和 API 合同保持不变，前端只做展示映射：

| 原始状态 | 中文展示 |
|---|---|
| Ready | 正常 |
| Busy | 忙碌 |
| Limited | 受限 |
| Offline | 离线 |
| Unknown | 未知 |
| connected | 已连接 |
| connecting | 连接中 |
| reconnecting | 正在重连 |
| failed | 连接失败 |
| live | 实时 |
| stale | 数据已过期 |
| unavailable | 暂不可用 |

颜色、圆点与中文文字共同表达状态；不能只靠颜色。运行原因转成面向用户的中文说明，原始错误和敏感技术细节不直接暴露。

## 5. 页面表达

### 5.1 顶部与全局框架

- 品牌名继续使用 `Orbbec Agent Platform`；
- 导航调整为“总览 / Agent / Session / 运行记录”；
- 删除永久显示的 `Read-only` 标签；
- 页脚不再使用“只读展示，不控制 Agent”作为产品口号；
- 浏览器标题保留 `Orbbec Agent Platform`，页面上下文使用中文，例如“运行详情 · Marketing Inbound · Orbbec Agent Platform”。

### 5.2 总览

- 页面标题使用“Agent 集群总览”；
- 页面说明直接描述已接入 Agent、当前运行状态和真实使用情况；
- 摘要、近 7 天趋势、Agent 运行情况和最近运行记录使用中文栏目名；
- Agent 名称、介绍、模型、渠道和业务数据保持原文；
- 异常只在真实发生时突出，不长期占据主视觉。

### 5.3 Agent 列表与 Agent 详情

- 列表标题使用“Agent 列表”，不使用 Fleet Directory；
- Agent 详情按“Agent 简介 / 运行状态 / 最近运行记录 / 最近 Session”组织；
- Runtime 摘要中的状态、运行时长和入口使用中文；
- 模型、Backend、Channel 与业务介绍保持原文；
- System Agent 继续与业务 Agent 分组，但分组说明使用中文。

### 5.4 运行详情

- 页面标题使用“运行详情”；
- 信息按“当前运行状态 / 运行环境 / 运行周期 / 观测依据”组织；
- Model、Engine、Backend、Channel 等技术字段名可以保留英文，解释文字使用中文；
- `Current process` 显示为“当前进程”，并明确进程重启后重新计时；
- evidence source 的机器值不作为主标题，使用中文说明其来源类别，必要时在次级位置保留原值。

### 5.5 Session 与运行记录

- 列表、筛选、分页和空状态使用中文；
- Session key、Agent 名称、Channel、Trace 和消息正文保持原文；
- Session 详情使用“Session 回放 / 用户提问 / Agent 回答 / 执行链路”；
- 运行记录使用真实业务动作说明，不显示通用模板句。

## 6. 视觉调整边界

本次保留已确认的白色专业界面、Orbbec 蓝、卡片重量和状态色，不重新设计整套视觉系统。

只做以下必要调整：

- 中文界面字体栈优先使用 `PingFang SC`、`Microsoft YaHei` 等系统中文字体；
- 删除全大写装饰标题、过宽字距和不必要的英文胶囊；
- 减少宣传式副标题与无信息留白；
- 合并仅为模板感而拆分的小卡片，但不降低监控信息可读性；
- Agent 身份区域继续保留业务色、Logo、名称和原始介绍；
- 不增加第三方字体、图标库或 UI 框架。

## 7. 实现边界

- 不修改后端 API、数据库、监控轮询或 readiness 计算；
- 建立集中的 UI 文案和状态映射，避免各页面自行翻译；
- 日期和相对时间统一使用 `zh-CN` 与 `Asia/Shanghai` 语境；
- 保留 URL、路由名称和 API 字段，避免破坏现有链接；
- 不修改 Agent 原始数据以实现中文化；
- Runtime 请求失败仍只影响运行状态区域，不阻塞 Agent 详情其他内容。

## 8. 验收标准

1. 顶部导航、页面标题、栏目、状态、操作和系统提示均为自然中文；
2. 全站不再出现装饰性全大写英文 eyebrow；
3. Agent、Session、Trace、Skill、Backend、模型名和业务原文未被翻译；
4. `Ready` 等原始状态在界面中显示为中文，API 合同保持不变；
5. 日期、相对时间和运行天数符合中文阅读习惯；
6. `Read-only` 和开源模板式页脚表达已移除；
7. 总览、Agent、运行详情、Session 和运行记录页面遵循同一语言边界；
8. 桌面与窄屏布局均无新增溢出、截断或字号下降；
9. 所有现有数据请求、路由、过滤返回和滚动恢复行为保持正常；
10. 前端测试和生产构建通过，后端不产生功能性改动。

## 9. 非目标

- 不改造成 Agent 团队工作台或营销门户；
- 不取消系统监控大盘定位；
- 不做全量汉化；
- 不新增权限、控制按钮、告警、部署或调度功能；
- 不修改 Agent 数据、Session 数据或数据飞轮；
- 不重新设计 Logo、品牌色或后端运行状态模型。
