# HR Agent 招聘智能增强工作台交接任务书（范围冻结版）

**日期：** 2026-09-03

**接手目标：** 在不接入外部业务系统的前提下，复审需求和现有实现，形成差距分析；用户确认
后再拆 TDD 实施计划。

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

已提交的需求演进：

```text
2c282fd docs(hr): define position lifecycle workbench requirements
f267654 docs(hr): focus workbench on recruiting intelligence
08e5524 docs(hr): add recruiting intelligence handoff
```

发生冲突时，以主需求文档和本交接任务书当前内容为准，不要回退到早期提交的产品范围。

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

## 3. 外部系统留空边界

用户已经明确：暂时不考虑北森、OA 等外部系统对接；凡是必须依赖外部企业系统才能成立的
需求，本期全部留空。

“留空”的准确含义是：

- 不做接口集成；
- 不建同步任务和 Adapter；
- 不预建外部主键、流程字段和推测性权限映射；
- 不做空页面、灰色入口、假数据或 Mock；
- 不把外部系统不可用当成 HR 工作台不可用的原因；
- 未来真正决定接入时，按真实 API 和权限要求单独立项。

本期明确不接入：

| 外部系统或数据 | 本期处理 |
|---|---|
| 北森招聘、人才库、组织、人事和绩效 | 全部留空 |
| OA 招聘需求申请、审批和分配 | 全部留空 |
| 猎聘、BOSS 直聘企业账号、简历和沟通接口 | 全部留空 |
| 薪酬、绩效、入转调离和组织架构系统 | 全部留空 |
| 招聘进度、招聘经理达成率、入职后适配和付薪合理性 | 不采集、不估算、不展示 |

以下已有能力可以继续使用，不属于新增企业系统对接：

- `hr-jd-sync` 对公司官网公开岗位的既有同步；
- 对用户明确关注公司的公开网页和公开岗位研究；
- Platform 已有钉钉统一登录；
- HR Agent 已有飞书入口，但飞书仅作为使用入口，不建设飞书到北森或 OA 的业务桥接；
- 用户主动上传或输入的岗位、简历、面试和研究材料。

## 4. 核心业务模型

### 4.1 Position

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

### 4.2 Candidate

候选人是独立、稳定、可跨岗位复用的人才对象，不能只是某个 Session 的一份附件。它包含：

- 候选人基础信息；
- 当前公司、职位和职业经历；
- 简历及不同版本；
- 用户补充材料；
- 已核验的公开专业证据；
- 信息来源和更新时间。

### 4.3 PositionCandidate

PositionCandidate 保存“某候选人与某岗位的关系”：

- 使用的岗位画像版本；
- 岗位匹配分析；
- 证据、冲突和未知项；
- 面试待验证问题；
- 面试题、面试材料和面试分析；
- AI 建议；
- 人工评价结果。

同一 Candidate 可以关联多个 Position，但各岗位分析不得混用。

### 4.4 AI 分析和人工反馈

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

### 4.5 人才与组织情报

人才与组织情报是与岗位智能、候选人智能并列的第三项核心能力。

- TalentSource：公司、学校、实验室、研究机构或专业社区；
- PositionTalentSource：关注对象与某个岗位的关系、理由和优先级；
- PublicJobSnapshot：关注公司的公开招聘岗位及版本变化；
- TalentInsightVersion：技术方向、招聘投入、人才供给和岗位策略的 AI 分析。

该能力持续关注用户明确选择的对象，把公开招聘变化转化为岗位画像、人才搜寻和面试建议。它不
建设全网职位聚合器，也不执行招聘渠道和投递事务。

## 5. 必须提供的 AI 增强能力

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
14. 从候选人和面试结果反向改善岗位画像、搜索方向和面试标准；
15. 持续关注目标公司公开招聘岗位及变化；
16. 对招聘岗位按技术、产品、区域和职级等方向聚类；
17. 识别公司、学校、研究机构和社区中的人才来源信号；
18. 把人才情报转化为当前岗位的画像、搜索和面试建议。

## 6. 明确不建设

- 招聘申请、审批、编制和预算；
- 职位渠道发布和投递管理；
- 候选人招聘阶段流转；
- 面试排期、会议室、日历和通知；
- Offer、背调、入职和电子签；
- 招聘专员任务分配和绩效管理；
- 完整招聘漏斗和运营报表；
- 无边界的全网职位聚合器；
- 北森候选人主档复制品；
- 自动联系、自动淘汰或自动录用；
- 面试实时录音和会议机器人。

不要因为“工作流完整”而扩展成招聘事务系统。

候选人搜索仅包括用户材料、已有 Platform 数据和合法公开网页研究；不得将“搜索候选人”解释
为北森人才库、猎聘或 BOSS 直聘企业接口接入。

## 7. Agent 上下文目标

HR Agent 每次执行不能只收到用户当前一句话。Platform 应当组装：

```text
当前岗位上下文
+ 当前候选人上下文（如有）
+ 与当前岗位相关的人才与组织情报
+ 本轮显式启用的材料
+ 用户已经确认的历史结论
+ 用户当前请求
```

必须阻止其他岗位、其他候选人和未启用材料进入当前上下文。

## 8. 真实数据分析依据

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
- 多次出现关注公司岗位、外部 JD 对标、薪酬研究、技术方向聚类以及学校/研究机构人才地图。

网页探针和 Canary 已排除。Flywheel 与当前运行库存在历史投影差异，不能简单相加。

HR 历史 Session 此前已经恢复。不要再次修复、回填、重放或迁移历史 Session。

## 9. 现有实现必须复用

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

## 10. 现有 HR Agent 规则需要调整

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

## 11. 官网岗位现有能力

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

## 12. 工作树保护

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

## 13. 接手会话第一步

不要立即编码。先阅读主需求文档和现有实现，并输出：

1. 对产品边界的理解；
2. 对现有 HR 工作区实现的核查；
3. Position、Candidate、PositionCandidate、TalentSource 与现有 Conversation/Attachment 的差距；
4. 可以直接复用的现有能力；
5. 必须调整的旧设计和 HR Agent 规则；
6. 与当前附件生命周期修改是否冲突；
7. 建议的实施阶段；
8. 仍需用户确认的产品问题。

完成复审并获得用户确认后，再写 TDD 实施计划。

不要重新做数据库需求分析，不要重建附件底座，不要把产品扩展成招聘系统。不要为北森、OA、
猎聘、BOSS 直聘或其他外部系统设计占位能力；如果实现计划中某项依赖这些系统，直接标记为
本期排除并从计划删除。

接手会话的第一份输出必须明确确认以下四件事：

1. 当前任务只做 HR Agent 招聘智能增强；
2. Position、Candidate、PositionCandidate 和人才情报是核心业务对象；
3. 外部系统对接全部留空且不做假数据；
4. 现有会话、附件、成果、官网岗位同步和 HR MetaBot 底座必须复用。
