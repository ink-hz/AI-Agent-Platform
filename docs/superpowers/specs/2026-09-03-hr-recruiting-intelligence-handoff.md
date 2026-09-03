# HR Agent 招聘智能增强工作台交接任务书

**日期：** 2026-09-03

**任务：** 继续设计和实施 HR Agent 招聘智能增强工作台。

## 一、工作位置

主仓库：

```text
/Users/neo/Developer/work/AI-Agent-Platform
```

当前工作树：

```text
/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-agent-web-workspace
```

当前分支：

```text
feat/hr-agent-web-workspace
```

当前需求边界提交：

```text
a2bd65d docs(hr): freeze external integration boundary
```

主需求文档：

```text
docs/superpowers/specs/2026-09-03-hr-position-lifecycle-workbench-requirements.md
```

本交接文档：

```text
docs/superpowers/specs/2026-09-03-hr-recruiting-intelligence-handoff.md
```

发生冲突时，以主需求文档和本交接任务书的当前内容为准。

## 二、产品定位

HR Agent 工作台是招聘工作的 AI 智能增强层，不是招聘系统，也不替代北森。

需要尽可能完整地覆盖招聘过程中的 AI 工作：

```text
岗位需求
→ JD/JR
→ 人才画像
→ 人才来源研究
→ 候选人挖掘
→ 简历分析
→ 面试方案
→ 面试内容分析
→ 候选人比较
→ 人工反馈
→ 反向改进岗位画像和招聘策略
```

完整的是 AI 的理解、分析、生成、证据和反馈闭环，不是招聘审批、排期、Offer、入职等事务
流程。

判断边界：

```text
功能是否直接改善 AI 的理解、分析、生成、证据或复盘能力？
├─ 是：属于 HR Agent 工作台
└─ 否：如果是招聘事务能力，则不进入本产品
```

## 三、外部系统全部留空

用户已经明确：暂时不考虑外部业务系统对接。

以下能力本期全部留空：

- 北森招聘、人才库、组织、人事和绩效数据；
- OA 招聘需求申请、审批和任务分配；
- 猎聘、BOSS 直聘等企业账号、简历和沟通接口；
- 薪酬、绩效、入转调离和组织架构系统；
- 招聘进度、招聘经理达成率；
- 入职后岗位适配性和付薪合理性分析。

“留空”的含义不是制作占位功能，而是：

- 不做接口；
- 不做 Adapter；
- 不建同步任务；
- 不预建外部主键和流程字段；
- 不做空页面、灰色按钮、假数据或 Mock；
- 不为未知接口提前设计权限模型；
- 不让外部系统影响本期 HR 工作台可用性。

将来决定接入某个系统时，再根据真实 API、权限和数据治理要求单独立项。

以下能力可以继续使用，不属于新增外部系统对接：

- 现有 `hr-jd-sync` 官网岗位同步；
- 用户明确关注公司的公开网页和公开岗位研究；
- Platform 已有钉钉统一登录；
- HR Agent 已有飞书入口；
- 用户主动上传的岗位、简历、面试和研究材料。

飞书本期只是 HR Agent 的使用入口，不建设飞书到北森或 OA 的业务桥接。

## 四、核心业务对象

### 4.1 Position

Position 是招聘智能工作的主线，包括：

- 官网同步岗位；
- 对话新增的内部岗位；
- 岗位名称、部门、地点和基础信息；
- 官网 JD 与历史版本；
- 内部真实需求；
- JD、JR；
- 优选人才画像；
- 搜索方向和人才地图；
- 筛选及面试标准；
- 版本化岗位上下文。

岗位名称不能作为主键，必须使用不可变 `position_id`。

### 4.2 Candidate

Candidate 是独立、可跨岗位复用的人才对象，包括：

- 基础职业信息；
- 当前公司和职位；
- 简历及不同版本；
- 作品集和补充材料；
- 用户确认的公开专业身份；
- 信息来源和更新时间。

不能因为对话中出现一个姓名就自动创建 Candidate。

### 4.3 PositionCandidate

PositionCandidate 表达“某个候选人与某个岗位的关系”，包括：

- 使用的岗位画像版本；
- 岗位匹配分析；
- 匹配证据、冲突和未知项；
- 面试待验证问题；
- 面试题和面试分析；
- AI 建议；
- 人工评价。

同一 Candidate 可以关联多个 Position，但各岗位分析必须隔离。

### 4.4 AI 分析与人工反馈

AI 分析必须版本化，不得覆盖历史，至少包括：

- 简历信息提取；
- 岗位匹配分析；
- 公开专业证据分析；
- 候选人挖掘结果分析；
- 面试前分析；
- 面试后分析；
- 多候选人比较；
- 候选人综合总结。

AI 建议、人工评价和人工反馈必须分开保存。AI 不得伪造或改写“人工确认”。

### 4.5 人才与组织情报

这是第三项核心能力，与岗位智能、候选人智能并列。

核心对象：

- `TalentSource`：公司、学校、实验室、研究机构或专业社区；
- `PositionTalentSource`：某个人才来源与岗位的关系、理由和优先级；
- `PublicJobSnapshot`：关注公司的公开招聘岗位及变化；
- `TalentInsightVersion`：技术方向、招聘投入、人才供给和岗位策略分析。

需要支持：

- 关注目标公司；
- 发现公开岗位新增、修改和疑似下线；
- 按光学、硬件、结构、软件、算法、制造工艺等方向聚类；
- 分析对方可能在建设的产品、技术和团队能力；
- 研究学校、实验室、研究院和专业社区；
- 把情报转化为当前岗位的人才画像、搜索关键词和面试重点。

公开事实、AI 推断和未知项必须明确区分。

不建设无边界的全网职位聚合器。

## 五、本期必须建设的能力

- 官网岗位同步投影、状态和版本；
- 通过自然语言新增内部岗位；
- 岗位持续对话；
- JD/JR 生成、修改和版本比较；
- 优选人才画像；
- 目标公司、学校和研究机构分析；
- 人才地图和候选人挖掘；
- 单份及批量简历解析；
- 简历与岗位匹配分析；
- 岗位通用面试题；
- 候选人专属面试题；
- 面试记录和转写文本分析；
- 同岗位候选人比较；
- 人工纠正进入后续 Agent 上下文；
- 从候选人及面试结果反向改善岗位画像；
- 关注公司公开招聘变化；
- 把人才情报转化为岗位、搜索和面试建议。

## 六、明确不建设

- 招聘申请和审批；
- 编制及预算；
- 招聘渠道发布和投递管理；
- 候选人招聘阶段流转；
- 面试排期、会议室、日历和通知；
- Offer、背调、入职和电子签；
- 招聘专员任务分配和绩效管理；
- 完整招聘漏斗和招聘运营系统；
- 自动联系、自动淘汰或自动录用；
- 面试实时录音机器人；
- 北森候选人主档复制品。

不要把这个项目扩展成 ATS 或招聘管理系统。

候选人搜索仅包括用户材料、已有 Platform 数据和合法公开网页研究，不得将“搜索候选人”解释
为北森人才库、猎聘或 BOSS 直聘企业接口接入。

## 七、Agent 上下文要求

HR Agent 每次执行不能只收到用户当前一句话。

Platform 应组装：

```text
当前岗位上下文
+ 当前候选人上下文（如有）
+ 与岗位有关的人才与组织情报
+ 本轮明确启用的材料
+ 用户已经确认的历史结论
+ 用户当前请求
```

必须阻止：

- 其他岗位材料进入当前岗位；
- 其他候选人材料进入当前候选人分析；
- 未经用户启用的材料自动进入上下文；
- 旧岗位画像被当成当前版本；
- AI 推断被当成确认事实。

## 八、现有能力必须复用

不要重新建设以下底座：

- Platform 持续 Conversation；
- Turn 和长任务恢复；
- 附件上传、处理和下载；
- Session 材料；
- Agent 输入附件授权；
- Agent 输出成果登记；
- 成果版本；
- 搜索恢复和未读状态；
- HR 专属工作区路由；
- HR MetaBot Runtime；
- `hr-jd-sync` 官网岗位同步。

已有相关提交包括：

```text
01c4772 feat(hr): polish workspace routing and unread state
8d7c11f feat(webui): deliver downloadable HR results
e34e1dc fix(webui): harden attachment upload queue
47a911d feat(webui): add session materials workspace
a770895 feat(brain): deliver attachments and resumable results
6513f90 feat(attachments): grant task inputs and register outputs
1e359ff feat(attachments): add conversation attachment schema
```

新增业务能力应建立在这些底座上，把业务归属从 Session 扩展到：

```text
Position
Candidate
PositionCandidate
TalentSource
```

## 九、真实数据分析依据

已经只读分析过 HR Agent 的历史数据，不要重新做数据库需求分析。

主要结论：

- 8 个真实历史飞书业务会话；
- 169 条有效用户问题；
- 当前运行库有 7 个恢复后的飞书会话；
- 154 条用户消息；
- 150 条 Assistant 消息；
- 最长真实会话达到 67 轮；
- 真实业务 Turn 中位数约 117 秒；
- P90 约 12.5 分钟；
- P95 约 19 分钟；
- 40 条问题明确涉及图片、简历、文件或 PPT；
- 41 条问题承接上文或要求持续修改；
- 高频任务包括 JD/JR、画像、人才搜寻、简历分析、面试题、面试分析和正式文件交付；
- 多次出现关注公司招聘岗位、外部 JD 对标、薪酬研究、技术方向聚类、学校和研究机构人才地图。

HR 历史 Session 已经恢复，不要再次回填、修复、重放或迁移。

## 十、建议发布阶段

### Release 1：岗位智能

- Position；
- PositionContextVersion；
- 官网岗位投影；
- 对话新增岗位；
- 岗位持续对话；
- JD/JR；
- 人才画像；
- TalentSource；
- 公开岗位快照；
- 人才与组织情报；
- 岗位材料和成果归属。

### Release 2：候选人智能

- Candidate；
- CandidateDocument；
- PositionCandidate；
- CandidateAnalysisVersion；
- CandidateEvaluation；
- 单份及批量简历；
- 岗位匹配分析；
- 候选人挖掘结果转 Candidate；
- 候选人的多岗位复用。

### Release 3：面试与反馈闭环

- 岗位通用面试方案；
- 候选人专属面试方案；
- 面试记录和转写分析；
- HumanFeedback；
- 同岗位候选人比较；
- 岗位复盘；
- 新版人才画像建议。

外部系统接入不属于上述 Release，也不属于本需求的后续阶段。

## 十一、工作树保护

当前工作树存在其他会话的修改。开始前必须运行：

```bash
git status --short --branch
git log --oneline -10
git diff --name-only
git diff --cached --name-only
```

不得：

- `reset`；
- 用 `checkout` 覆盖现有文件；
- stash 他人修改；
- clean 未跟踪文件；
- 删除 `backend/.venv`；
- 把其他会话文件一起提交；
- 在未确认归属前覆盖或重排提交。

当前可能存在其他会话的未跟踪验收和发布文件，必须先识别归属，不得擅自处理。

## 十二、接手后的第一步

不要立即编码。

先阅读主需求文档和现有 HR 工作区实现，然后输出：

1. 对产品边界的理解；
2. 对现有实现的核查；
3. Position、Candidate、PositionCandidate、TalentSource 与现有 Conversation/Attachment 的差距；
4. 可以直接复用的能力；
5. 需要调整的旧设计和 HR Agent 规则；
6. 与当前并行修改是否冲突；
7. 推荐的实施阶段；
8. 仍然需要用户确认的问题。

接手会话必须先明确确认：

- 当前任务只做 HR Agent 招聘智能增强；
- 不把它做成招聘系统；
- 外部系统对接全部留空；
- 不做占位页面和假数据；
- 现有会话、附件、成果、官网岗位同步和 HR MetaBot 必须复用；
- 不重新处理已经恢复的 HR 历史 Session。

完成复审并获得用户确认后，再编写 TDD 实施计划。
