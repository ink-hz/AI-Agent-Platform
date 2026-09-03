# HR Agent 招聘智能增强工作台交接任务书

**日期：** 2026-09-03

**接手目标：** 复审需求和现有实现，形成差距分析；用户确认后再拆 TDD 实施计划。

## 1. 工作位置

主仓库：

```text
/Users/neo/Developer/work/AI-Agent-Platform
```

现有 HR 工作树：

```text
/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-agent-web-workspace
```

现有分支：

```text
feat/hr-agent-web-workspace
```

主需求文档：

```text
docs/superpowers/specs/2026-09-03-hr-position-lifecycle-workbench-requirements.md
```

需求提交：

```text
f267654 docs(hr): focus workbench on recruiting intelligence
```

初版需求提交 `2c282fd` 已被 `f267654` 结构性重写。发生冲突时，以 `f267654` 和主需求文档
当前内容为准。

## 2. 已确认的产品定位

HR Agent 工作台是招聘 AI 智能增强层，不是招聘系统，也不替代北森。

最高原则：

> 完整的是 AI 的认知、分析、生成和反馈链路，不是招聘申请、审批、排期、Offer、入职等事务
> 流程。

招聘工作流可以尽可能完整，但只建设直接增强 AI 能力所必需的数据和功能。

判断边界：

```text
功能是否直接改善 AI 的理解、分析、生成、证据或复盘能力？
├─ 是：属于 HR Agent 工作台
└─ 否：如果是招聘事务能力，交给北森
```

北森本期不接入，也不展示空占位功能。未来北森提供流程事实，Platform 提供 AI 增强内容。

## 3. 核心业务模型

### 3.1 Position

岗位是招聘智能工作的主线和用户主要入口，包含：

- 官网同步岗位；
- 对话新增内部岗位；
- 岗位基础信息；
- 官网 JD 及版本；
- 内部真实需求；
- JD、JR；
- 人才画像；
- 搜索方向和人才地图；
- 筛选及面试标准；
- 版本化岗位上下文。

岗位名称可以修改或重复，数据库使用不可变 `position_id`。

### 3.2 Candidate

候选人是独立、稳定、可跨岗位复用的人才对象，不能只是某个 Session 的一份附件。它包含：

- 候选人基础信息；
- 当前公司、职位和职业经历；
- 简历及不同版本；
- 用户补充材料；
- 已核验的公开专业证据；
- 信息来源和更新时间。

### 3.3 PositionCandidate

PositionCandidate 保存“某候选人与某岗位的关系”：

- 使用的岗位画像版本；
- 岗位匹配分析；
- 证据、冲突和未知项；
- 面试待验证问题；
- 面试题、面试材料和面试分析；
- AI 建议；
- 人工评价结果。

同一 Candidate 可以关联多个 Position，但各岗位分析不得混用。

### 3.4 AI 分析和人工反馈

CandidateAnalysisVersion 至少支持：

- 简历信息提取；
- 岗位匹配分析；
- 公开证据分析；
- 候选人挖掘结果分析；
- 面试前分析；
- 面试后分析；
- 多候选人比较；
- 候选人综合总结。

每个分析必须记录使用的岗位上下文版本、简历版本、面试材料和证据。旧分析不得被静默覆盖。

CandidateEvaluation 与 HumanFeedback 分别保存候选人评价和人工纠正。AI 建议、人工评价、人工
反馈必须物理分离，AI 不得生成或改写“人工确认”。

## 4. 必须提供的 AI 增强能力

1. 官网岗位同步、版本和使用前核验；
2. 通过自然语言新增内部岗位；
3. JD/JR 生成、修改和版本比较；
4. 优选人才画像；
5. 目标公司、学校、实验室和人才地图；
6. 候选人挖掘；
7. 单份和批量简历解析；
8. 简历与岗位匹配分析；
9. 岗位通用面试题；
10. 候选人专属面试题；
11. 面试记录、转写和评价材料分析；
12. 同岗位候选人比较；
13. 人工纠正进入后续 Agent 上下文；
14. 从候选人和面试结果反向改善岗位画像、搜索方向和面试标准。

## 5. 明确不建设

- 招聘申请、审批、编制和预算；
- 职位渠道发布和投递管理；
- 候选人招聘阶段流转；
- 面试排期、会议室、日历和通知；
- Offer、背调、入职和电子签；
- 招聘专员任务分配和绩效管理；
- 完整招聘漏斗和运营报表；
- 北森候选人主档复制品；
- 自动联系、自动淘汰或自动录用；
- 面试实时录音和会议机器人。

不要因为“工作流完整”而扩展成招聘事务系统。

## 6. Agent 上下文目标

HR Agent 每次执行不能只收到用户当前一句话。Platform 应当组装：

```text
当前岗位上下文
+ 当前候选人上下文（如有）
+ 本轮显式启用的材料
+ 用户已经确认的历史结论
+ 用户当前请求
```

必须阻止其他岗位、其他候选人和未启用材料进入当前上下文。

## 7. 真实数据分析依据

已经只读核对 Flywheel 历史数据和 HR MetaBot 当前运行库，没有修改、回填或迁移数据。

主要事实：

- 8 个真实历史飞书业务会话；
- 169 条有效用户问题；
- 当前运行库有 7 个恢复后的飞书会话；
- 154 条用户消息、150 条 Assistant 消息；
- 最长真实会话达到 67 轮；
- 真实业务 Turn 耗时中位数约 117 秒；
- P90 约 12.5 分钟，P95 约 19 分钟；
- 40 条问题明确引用图片、简历、文件或 PPT；
- 41 条问题承接上文或要求反复修改；
- 高频任务为 JD/JR、画像、人才搜寻、简历分析、面试题、面试分析和正式文件交付。

网页探针和 Canary 已排除。Flywheel 与当前运行库存在历史投影差异，不能简单相加。

HR 历史 Session 此前已经恢复。不要再次修复、回填、重放或迁移历史 Session。

## 8. 现有实现必须复用

`feat/hr-agent-web-workspace` 已经完成大量附件和持续会话底座开发，不得推倒重来。

相关提交包括：

```text
01c4772 feat(hr): polish workspace routing and unread state
8d7c11f feat(webui): deliver downloadable HR results
e34e1dc fix(webui): harden attachment upload queue
47a911d feat(webui): add session materials workspace
a770895 feat(brain): deliver attachments and resumable results
6513f90 feat(attachments): grant task inputs and register outputs
1e359ff feat(attachments): add conversation attachment schema
```

已有能力包括：

- 持续对话；
- 附件上传、验证、处理和下载；
- Session 材料；
- Agent 输入附件授权；
- Agent 输出成果登记；
- 成果版本；
- 搜索恢复；
- 长任务和未读状态；
- HR 专属工作区路由。

新需求应复用底座，把业务归属从 Session 扩展到 Position、Candidate 和 PositionCandidate。

## 9. 现有 HR Agent 规则需要调整

当前文件禁止长期保存候选人卡片、逐人评价和候选人结果：

```text
/Users/neo/Developer/work/Orbbec-Agent-Team/bots/hr/CLAUDE.md
/Users/neo/Developer/work/Orbbec-Agent-Team/bots/hr/.claude/skills/role-calibration/SKILL.md
```

新边界是：

- 继续禁止写入 Agent 知识库、FAQ、官网 JD 注册表和共享训练语料；
- 允许通过 Platform 结构化接口写入 Candidate、PositionCandidate、Analysis、Evaluation 和
  HumanFeedback；
- Agent 不直接读写数据库文件；
- 只有用户明确创建、关联、分析或确认时才写入；
- 人工反馈和历史分析不能被模型覆盖。

## 10. 官网岗位现有能力

不要重新制作官网抓取器。

现有实现：

```text
/Users/neo/Developer/work/Orbbec-Agent-Team/services/hr-jd-sync
```

现有 Agent Skill：

```text
/Users/neo/Developer/work/Orbbec-Agent-Team/bots/hr/.claude/skills/jd-registry/SKILL.md
```

它已经支持官网岗位同步、J 编号、`active / stale / suspected_inactive / inactive` 状态、版本
历史和使用前核验。

Platform 需要把官网岗位事实投影为 Position。官网更新不得覆盖内部画像、候选人或 AI 分析。

## 11. 工作树保护

`hr-agent-web-workspace` 当前存在其他会话正在进行的附件生命周期修改。

开始任何操作前必须运行：

```bash
git status --short --branch
git log --oneline -10
git diff --name-only
git diff --cached --name-only
```

不得：

- reset；
- checkout 覆盖文件；
- stash 他人修改；
- clean 未跟踪文件；
- 修改或提交当前其他会话的附件代码；
- 删除 `backend/.venv`；
- 在未识别归属前合并、重排或覆盖提交。

如果开始实现，应等待当前并行修改收口，再从 `feat/hr-agent-web-workspace` 最新已提交 HEAD 创建
独立工作树和新分支。

## 12. 接手会话第一步

不要立即编码。先阅读主需求文档和现有实现，并输出：

1. 对产品边界的理解；
2. 对现有 HR 工作区实现的核查；
3. Position、Candidate、PositionCandidate 与现有 Conversation/Attachment 的差距；
4. 可以直接复用的现有能力；
5. 必须调整的旧设计和 HR Agent 规则；
6. 与当前附件生命周期修改是否冲突；
7. 建议的实施阶段；
8. 仍需用户确认的产品问题。

完成复审并获得用户确认后，再写 TDD 实施计划。

不要重新做数据库需求分析，不要重建附件底座，不要把产品扩展成招聘系统。
