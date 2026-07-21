# Agent Platform 源语言命名与界面文案设计

- 日期：2026-07-21
- 状态：已完成方向确认
- 范围：当前 Agent Overview 首页、Agent 展示目录及其加载、异常和空状态文案

## 1. 目标

移除当前界面中生硬的中文翻译、重复的“助手”后缀和偏营销化的“AI 团队”表达，让 Platform 更像专业的 Agent operations 产品。

界面采用“源语言优先”：Agent、Bot、Platform、Flywheel 及各业务能力名称保留英文；原本就是中文的职责介绍、解释和错误提示继续使用简洁中文，不为追求表面统一而强制翻译。

## 2. 文案原则

1. Agent 名称以运行 Bot 的英文业务标识为基础，不创造中文别名。
2. 不使用“助手”“数字员工”“AI 团队”等泛化或营销化称呼。
3. Prospecting、Inbound、Voice、GTM、Intelligence、FAE 等术语保持英文。
4. 导航、页面标题、指标标题和卡片字段采用简短英文产品术语。
5. 职责介绍、故障说明、数据解释和相对时间保留中文。
6. 不发明新的品牌代号，不修改 `bot_id`、数据库映射或统计口径。
7. 未知 Bot 继续使用运行契约名称，不自动翻译。

## 3. Agent 命名目录

| `bot_id` | 页面名称 | Domain | Glyph | 中文介绍 |
|---|---|---|---|---|
| `feishu-default` | Feishu Default | Feishu | FS | 承接飞书默认会话与日常协作任务。 |
| `hr-bot` | HR | HR | HR | 处理招聘、人事与员工服务相关工作。 |
| `marketing-prospecting-bot` | Marketing Prospecting | Marketing | PRO | 发现、筛选并跟进潜在客户线索。 |
| `marketing-inbound-bot` | Marketing Inbound | Marketing | IN | 处理入站线索、内容触达与客户咨询。 |
| `marketing-voice-bot` | Marketing Voice | Marketing | VO | 处理语音触达、通话沟通与结果整理。 |
| `fae-bot` | FAE | FAE | FAE | 处理产品咨询、问题诊断与现场应用。 |
| `test-bot` | Test | System | T | 用于接口联调、集成测试与运行验证。 |
| `marketing-gtm-bot` | Marketing GTM | Marketing | GTM | 负责市场进入策略、节奏规划与执行协同。 |
| `marketing-intelligence-bot` | Marketing Intelligence | Marketing | INT | 收集并整理市场动态与竞争信息。 |

历史别名 `marketing-bot` 仍只映射至 `marketing-prospecting-bot`，但不会出现在页面名称中。

## 4. 页面文案体系

### 4.1 顶部与导航

| 位置 | 新文案 |
|---|---|
| 品牌 | Orbbec Agent Platform |
| 导航 | Overview / Agents / Sessions / Flywheel |
| 只读标签 | Read-only |
| Eyebrow | AGENT OPERATIONS |
| 页面标题 | Agent Overview |
| 页面介绍 | 查看 Agent 运行状态、真实使用量和最近活动。 |
| 正常状态 | `{running} Agents 运行中` |
| 异常状态 | `{count} Agents 需要关注` |

### 4.2 汇总与洞察

| 位置 | 新文案 |
|---|---|
| 汇总区域 | OPERATIONS / Fleet Snapshot |
| Agent 数量 | Agents |
| 运行数量 | Online |
| 累计对话 | Total Conversations |
| 近七天对话 | Last 7 Days |
| 趋势区域 | USAGE / 7-Day Trend |
| 排行区域 | USAGE / Active Agents |
| 排行说明 | 按真实对话排序 |

指标解释继续使用中文，例如“已纳入 Platform”“数据来自 Flywheel”“较上期 +18%”。数据不可用时继续显示 `—`，不以 `0` 代替未知值。

### 4.3 Agent 卡片

| 当前字段 | 新字段 |
|---|---|
| 累计对话 | Total Conversations |
| 近 7 天 | Last 7 Days |
| 运行时长 | Uptime |
| 最近活动 | Last Activity |
| 最近 | Recent |

产品状态使用英文：`Active`、`Online`、`Degraded`、`Offline`、`Checking`。相对时间和空状态保留中文，例如“2 分钟前”“暂无活动”“尚无真实对话记录”。

### 4.4 Agent 列表与页脚

| 位置 | 新文案 |
|---|---|
| Agent 区域 | CATALOG / Agents |
| 数量与刷新 | `{count} Agents · 每 10 秒自动刷新` |
| 页脚 | Orbbec Agent Platform · 只读展示，不控制 Agent |

## 5. 降级与错误文案

错误信息属于解释性内容，保留中文并缩短：

- Platform 接口暂不可用，显示最后一次成功数据并继续重试。
- Agent 状态暂不可用，使用数据仍可查看。
- Agent 状态超过 30 秒未更新，显示最后一次成功状态。
- Flywheel 暂不可用，显示最后一次成功数据；不使用模拟数据。
- 对话数据暂不可用，Agent 状态仍在更新。

加载状态统一为：

- 标题：正在加载 Agent Overview
- 说明：正在汇总 Agent 状态和真实对话数据。
- 失败标题：暂时无法读取 Agent 数据

## 6. 实现边界

- 修改 `backend/app/fleet/catalog.yaml` 中的展示名称、Domain、Glyph 和中文介绍。
- 修改 React 首页及展示格式中的可见文案。
- 更新受影响的目录、组件和格式化测试。
- 不修改统计 SQL、API 字段、Bot 身份、运行状态推导或 Flywheel 数据。
- 不修改或重启 MetaBot；仅在构建完成后重启 Platform。
- 保留当前工作区中与本任务无关的未提交修改和未跟踪文件。

## 7. 验收标准

1. 生产页面中不存在任何 Agent 名称以“助手”结尾。
2. Prospecting、Inbound、Voice、GTM、Intelligence 和 FAE 均保持英文。
3. 页面不再出现“AI 团队”“团队成员”“今天的 AI 团队”或“数字员工”。
4. 9 个 Agent 的名称、Domain、Glyph 和介绍与本规格一致。
5. 描述、错误和空状态保持自然、简洁的中文。
6. 页面仍只显示真实使用数据，不增加任何控制入口。
7. 后端、前端测试、生产构建、依赖审计和在线接口验收通过。
