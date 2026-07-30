# Session Markdown 与消息时间戳设计

日期：2026-07-30
状态：已确认，待实施

## 目标

所有 Agent 的 Session 回放使用同一套消息展示规范：用户提问和 Agent 回答都安全渲染 Markdown，并分别显示中国时间的消息时间戳。时间必须说明精度，不能用一个 Turn 时间冒充两条消息的真实时间。

本次范围覆盖三个代码库：

- `AI-Agent-Platform`：统一读取模型、API、Session UI 和本地同步目标。
- `AI-FAE-Agent`：新增精确的提问与回答时间采集。
- `AI-ADMIN-Agent`：新增精确的提问与回答时间采集。

## 已确认的现状

截至 2026-07-30，只读统计如下：

| 来源 | 历史 Turn | 时间现状 |
|---|---:|---|
| MetaBot | 175 | 159 条同时有真实提问和回答时间；15 条没有回答消息；1 条没有提问消息 |
| AI FAE | 349 | 只有 Turn 落库时间和 `duration_ms`，没有两个独立消息时间 |
| AI ADMIN | 210 | 只有 Turn 落库时间和 `duration_ms`，没有两个独立消息时间 |
| Iris Codex | 0 | 暂无统一 Session 历史 Turn；未来直接遵循新契约 |

用户提供的 HR Session 中 4 个 Turn 均可从 MetaBot 消息表恢复真实的提问和回答时间。

当前 Markdown 缺失的直接原因是 `TurnCard` 使用普通 `<p>` 输出原始字符串。当前回答时间缺失的直接原因是 `platform_read.turns` 将同一 Turn 的消息折叠为 `min(occurred_at) as created_at`，丢弃了 assistant 消息自己的 `occurred_at`。

## 产品展示

每个 Turn 保留现有“用户提问 / Agent 回答”结构。每个消息标题同行显示自己的时间：

- 精确时间：`7月29日 15:28:32`
- 估算时间：`约 7月29日 15:28:32`
- 无法获得：`时间未记录`

时间按 `Asia/Shanghai` 显示到秒，完整 ISO 时间保留在 `<time datetime>` 中。页面不显示数据库字段名、来源表名或同步内部状态。

提问者姓名和部门继续显示在用户提问区域；时间与身份信息互不替代。

## 统一 Turn 时间契约

Platform 的 `TurnDetail` 新增：

- `question_at: datetime | null`
- `answer_at: datetime | null`
- `question_time_status: exact | estimated | unavailable`
- `answer_time_status: exact | estimated | unavailable`

现有 `created_at` 暂时保留，兼容 Session 排序、活动统计和旧调用方。本次不改变 Conversation、Session 或使用量的计数语义。

### MetaBot

`platform_read.turns` 从 `flywheel_analytics.messages` 分别聚合：

- `question_at`：该 Turn 中 user 消息最早的 `occurred_at`。
- `answer_at`：该 Turn 中 assistant 消息最晚的 `occurred_at`。
- 找到对应消息时状态为 `exact`，缺少对应消息时为 `unavailable`。

MetaBot、HR Bot、Marketing Bot、FAE Bot、Iris Codex 等使用统一 Flywheel 消息采集的 Agent 都自动获得这一能力，不做 Agent ID 特判。

### AI FAE 与 AI ADMIN 新数据

两个采集端都新增原生字段：

- `question_at timestamptz null`
- `answer_at timestamptz null`

请求进入 Agent 执行前记录 `question_at`；最终答案生成完成时记录 `answer_at`。两者随 `ChatTurnRecord` 同事务写入。新数据只要字段存在即标记为 `exact`。

远端导出、本地导入和 `platform_source_fae` / `platform_source_admin` 镜像表同步新增这两个字段。同步仍按现有每日节奏运行。

### AI FAE 与 AI ADMIN 历史数据

旧数据无法恢复两个真实事件时间，不进行虚假“精确回填”：

- `answer_at = created_at`，状态为 `estimated`。这里的 `created_at` 是 Turn 持久化时间，只能近似回答完成时间。
- 当 `duration_ms` 有效时，`question_at = created_at - duration_ms`，状态为 `estimated`。
- 缺少有效 `duration_ms` 时，`question_at = null`，状态为 `unavailable`。

历史源表保持原值，不批量改写。估算只在统一读取视图中计算，因此未来拿到更可靠时间后可以无损替换。

## Markdown 渲染与安全边界

提问和回答共用一个小型 `MessageMarkdown` 组件，使用 `react-markdown` 和 `remark-gfm`：

- 支持标题、段落、粗体、斜体、列表、引用、链接、行内代码、代码块、表格和删除线。
- 不启用 `rehype-raw`，原始 HTML 不作为 DOM 执行。
- 不使用 `dangerouslySetInnerHTML`。
- 外部链接使用安全的 `rel` 属性；不允许 Markdown 改写 Platform 自身导航状态。
- 空内容继续显示现有中文缺失提示，不把缺失提示当作 Markdown 数据。

Markdown 组件只负责内容渲染，不解析业务语义，不翻译原始提问和回答。

## 样式

新增 `.message-markdown` 样式域，只影响 Session 消息正文：

- 继承现有中文字体、颜色和正文大小。
- 标题层级在消息卡片内收敛，不与页面标题竞争。
- 列表、引用、表格和代码块在桌面与窄屏均可读。
- 宽表格和代码块允许消息区域内部横向滚动，不撑破卡片。
- 连续段落保持清楚间距，首尾不制造多余空白。

## 数据流与迁移顺序

1. 为 AI FAE、AI ADMIN 源数据库添加 nullable 时间字段。
2. 更新两个 Agent 的 `ChatTurnRecord` 和写入路径，开始产生精确时间。
3. 为 Platform 两个镜像表添加同名 nullable 字段。
4. 更新远端导出与本地导入白名单。
5. 更新 `platform_read.turns`，统一产生四个时间字段和状态。
6. 更新 Platform 后端模型、仓库映射和 API。
7. 更新 WebUI Markdown、时间显示和样式。
8. 执行一次同步并验证 MetaBot、FAE、ADMIN 三种来源。

迁移必须可重复执行。新增字段全部 nullable，旧导出包在升级期间仍可导入；部署顺序不得让导出脚本在源表尚未迁移时请求不存在的列。

## 错误与降级

- 单个时间缺失不隐藏 Turn，也不影响另一条消息的时间。
- Markdown 中的非法或不支持语法按普通文本处理，不能导致整个 Session 页面失败。
- 时间字符串无效时显示“时间未记录”，不显示 `Invalid Date`。
- 同步包缺少新字段时按历史数据规则读取，不能导致每日同步失败。
- 未完成的 Turn 可以只有提问时间；没有回答消息时回答区域显示“时间未记录”。

## 测试

### AI-Agent-Platform 后端

- MetaBot 同一 Turn 正确选择 user 最早时间和 assistant 最晚时间。
- MetaBot 缺少任一角色消息时只将对应时间标记为 unavailable。
- FAE / ADMIN 新字段存在时返回 exact。
- FAE / ADMIN 旧行按 `created_at` 与 `duration_ms` 返回 estimated。
- 无效或缺失 duration 不产生伪造提问时间。
- 迁移重复执行安全，分析只读角色仍能读取新视图。

### 同步链路

- 新导出包包含两个时间字段。
- 旧导出包缺少字段时仍能导入。
- corrected answer、Trace 和 sender identity 等现有字段不回归。

### WebUI

- 提问和回答分别显示自己的上海时区时间。
- estimated 显示“约”，unavailable 显示“时间未记录”。
- 标题、列表、GFM 表格、引用、链接和代码块正确渲染。
- 原始 HTML 不执行，不出现 `dangerouslySetInnerHTML`。
- 中文、英文和混合内容保持原文。
- Session 返回筛选条件与滚动位置的现有行为不回归。

## 验收标准

- 用户提供的 HR Session 每轮提问和回答均显示真实、不同的时间戳。
- 所有 Session 来源都显示两个时间位置，并明确 exact、estimated 或 unavailable 的结果。
- 新产生的 FAE / ADMIN Turn 拥有真实的 `question_at` 与 `answer_at`。
- 旧 FAE / ADMIN Turn 显示带“约”的历史估算时间。
- 提问与回答的 Markdown 在桌面和移动布局中正确、安全、可读。
- 三个代码库测试通过，Platform 生产构建成功，重启后真实 API 与页面可用。

## 非目标

- 不在本次增加消息编辑、复制、导出或全文搜索。
- 不修改反馈、Review、Trace 或 Evidence 的业务语义。
- 不伪造无法恢复的历史精确时间。
- 不把 Markdown 渲染扩展到 Agent 描述、运行事件或其他非消息内容。
