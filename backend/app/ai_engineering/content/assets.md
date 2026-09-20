# 已有 AI 资产盘点

日期：2026-09-20（Asia/Shanghai）｜版本：v0.1，本地只读证据盘点

## 管理层摘要

**已经建了什么。** 在本次检查的五个仓库中，已有统一身份与应用管理平台，以及 HR、行政、FAE、VOC 四个业务领域的应用代码；五者均找到历史生产运行或发布记录。行政同时包含制度问答与班车、住宿、车辆等流程应用，不能将所有流程功能都计为 AI 成果。平台中保留的 HR 代码与独立 HR 仓库也不能重复计数。[P01–P06、H01–H08、A01–A07、F01–F05、V01–V06]

**已有实际使用线索，但业务成效尚未闭合。** HR 的历史非合成快照含 8 个有用户消息的会话、204 条用户消息；行政班车的历史生产统计含 52 条有效预约、79 人次（含同行）；FAE 有线上负反馈及后续开发环境复测。它们分别证明历史交互、业务记录和反馈改进活动，不能合并为活跃人数、成功任务数或节省金额。VOC 较新的设计记录称生产复现了机器人问题，但缺少本次可匹配的发布回执与使用汇总。[H06、A06、F05、V06]

**当前主要缺口。** 五项的当前服务健康均未在本轮探测；已检材料也未形成五项统一口径的业务验收、采纳率、成本与收益基线。HR 的新修复仍有真实模型整链验证未通过的记录；行政近期自动检查主要覆盖匿名登录入口；VOC 的部署材料存在时间与范围差异。这些问题不应被“代码已实现”或测试数量掩盖。[H07–H08、A05、V04–V06]

**在全景中的使用。** 本清单已为[全景 v1](../panorama/2026-09-20-panorama-v1.md)提供已有能力与覆盖缺口的证据。版本对账、HR 可靠性和具体型号兼容资料等维护事项由相应应用／产品维护角色承接，不作为全景成图的前置任务。工程总监主线转向全景、领域关系与业务优先级确认；组织目录授权仍按既有协调清单推进，试点另需业务基线。

## 1. 范围与证据口径

本次只读 `~/Developer/work/` 下的 `AI-Agent-Platform`、`AI-HR-Agent`、`AI-ADMIN-Agent`、`AI-FAE-Agent`、`Orbbec-VOC-Agent`。核对代码入口、部署配置、已提交的评审及交接文档；没有执行部署、测试套件、模型调用、SSH、生产 API 或数据库查询，没有发送协调消息。只在本 AI 工程仓库写盘点材料。

证据分为三种，不能相互替代：

| 证据 | 本轮能确认的内容 | 本轮不能据此确认的内容 |
| --- | --- | --- |
| 代码／配置直接核对 | 文件、路由、编排与部署模板存在；内容与指定提交一致 | 功能已启用、部署成功、当前服务健康 |
| 历史记录转述 | 仓库文档记录了某日期、版本的发布、检查或使用汇总 | 本轮独立重放通过、当前生产状态、原始数据已被重新核验 |
| 分析与建议 | 从上述证据得出的复用方向、差异和待补项 | 已确定组织职责、已立项、业务收益已实现 |

“未找到”仅指本轮检查的材料，不等于公司没有该记录。所有测试数量和生产状态都是带日期的历史记录，不是本轮测试结果。使用汇总不复制人员身份、HR 正文、客户反馈、凭证或入群链接。来源编号对应文末文件索引；[机器可核验清单](2026-09-20-existing-ai-assets-evidence.json)记录完整 HEAD、文件摘要和证据类别。摘要用于固定本轮读到的材料，不能替代材料所述事实的独立验证。

## 2. 资产总表

| 资产 | 直接核对的代码能力 | 历史部署与运行证据 | 验收记录及限制 | 使用／效果证据 |
| --- | --- | --- | --- | --- |
| 平台 | 身份与目录接入、Agent 编排、专业应用入口、附件及 AI notes 路由 | 9 月 15 日迁移回执记录平台 `041495e0`、容器 healthy、入口及 peer 检查通过；不等于本地 HEAD 已部署 | 迁移记录包含真实数据库、HTTP 与独立 HR 进程集成；模型、真实个人材料、受认证浏览器验收未覆盖 | 有支持 HR／ADMIN／VOC 的集成依据；未找到本次范围内可核定的平台活跃人数或任务价值汇总 |
| HR | Hannah／岗位、标准与成果、候选人、来源研究；独立 API、Worker 与前端 | 9 月 15 日独立切换；9 月 20 日记录 API／Worker `4d2a1b7`、前端 `2d4884c`，两服务 active、重启计数 0 | 有工程集成与真实模型验证记录；9 月 20 日代表整链只执行 2/4 轮，不能标业务验收通过；后续断流诊断仍未关闭根因 | 9 月 16 日历史非合成数据分析：30 个会话中 8 个含用户消息，共 204 条用户消息；不是 8 位用户或 204 个成功任务 |
| ADMIN | 行政知识问答；班车、住宿、车辆、通知、日程及楼层服务等流程路由 | 9 月 20 日回执区分 UI `231c3af`、后端 `cbd44cf`、楼层资源 `a3789d4`；systemd 部署 | 新资源 33 项后端、4 项浏览器回归；UI 的 80 项线上检查是匿名入口／登录边界，真实钉钉跳转待验收 | 9 月 14 日班车统计有 52 条有效预约、79 人次；证明业务记录存在，不证明 AI 问答有效或乘车实际完成 |
| FAE | 产品选型、规格／SDK 证据、故障排查、多证据组织、附件与图像辅助 | 7 月 14 日明确发布回执；7 月 22 日记录不可变发布 succeeded；8 月 28 日评审另标生产／实施基线 `084f58d…`，不能据此推算当前运行版 | 8 月 28 日开发环境 20 条运行门禁通过，独立语义判断 19 pass／1 partial；该批未部署、未关闭线上反馈 | 有线上负反馈来源与修复活动；缺独立用户数、全部请求分母、现场问题解决率与业务成本基线 |
| VOC | 工作台草稿／确认提交／本人记录／管理查询；机器人查询与修正工具 | 9 月 3 日交接记录旧 workspace `6a0fe1b…` healthy，新独立入口／Bot 当时发布阻塞；9 月 6 日设计称问题已在生产复现，最新部署版本待核对 | 早期交接有本地测试，但生产 smoke 和角色验收未运行；后续运维手册是流程，不是执行回执 | 有生产问题描述这一使用线索，不能判定来自员工日常使用还是发布测试；无可核定采纳或反馈闭环收益 |

本表来源分别为 P01–P06、H01–H08、A01–A07、F01–F05、V01–V06。**五项当前健康状态统一记为“本轮未检查”，业务效果统一记为“尚无本轮可核定的效果基线”。**这不否认已有局部验收或使用记录。

## 3. 每项资产可复用什么、还缺什么

### 3.1 平台：身份和应用连接底座

`backend/app/main.py` 装配身份、目录、Agent、附件及业务路由；行政收件人目录明确提供目录 generation 与成员信息，AI notes 有索引和文章接口。`MissionOrchestrator` 提供任务编排代码；这些可以作为全景中的平台支撑能力。不能因为有文章路由就宣称已有完整 AI 知识库，也不能把目录数据当组织职责说明。[P01–P04]

部署判断必须使用组合配置和发布回执。基础 Compose 仍有旧 HR Worker 定义，但 9 月 15 日切换记录说明旧 Worker 已停止，并由最终覆盖配置固定为零副本；独立 HR 通过同机路由运行，继续复用平台身份及既有数据库角色。代码重复、服务独立与数据权限隔离是不同问题。[P05–P06、H05]

**待补：**当前运行版本与入口清单；过去一个固定周期内按业务应用去重的使用汇总；平台与业务应用成本的分摊规则。平台内其他 Agent 名称和入口未做同等审计，不计入本次已核验资产数。

### 3.2 HR：已有业务工作流，可靠性与成本仍需验证

独立入口装配岗位、资源与会话，Agent 路由含候选批次、候选人、面试记录和任务受理。systemd 模板与 9 月 15 日切换回执互相支持“独立 API／Worker 已有历史部署”这一判断；同一迁移文档中的切换前记录不能覆盖后附的切换回执。[H01–H05]

9 月 16 日汇总的消息范围为 7 月 14 日至 9 月 15 日：204 条用户消息中，153 条归为业务及职业任务、22 条一般交互、6 条服务反馈、23 条私人非 HR 目的。业务消息人工整理成 61 条任务链，不是 61 个成功交付或独立统计样本；也不能直接归给 9 月 15 日之后的新独立版本。[H06]

9 月 20 日修复报告明确：本地隔离环境的真实模型代表链仅完成 2/4 轮，以 `waiting_budget / finalizing / partial` 停止，不能用工程回归通过替代业务交付。后续网关诊断指出客户端与网关记录的响应交付差异，修复的是 trace 关联缺口，传输根因未关闭；这些本地测试失败不能写成生产事故。[H07–H08]

**待补：**限定一个岗位工作任务，约定可交付成果、人工审阅质量、耗时及单次成本；分别确认生产版本和候选修复版本。候选人材料属于高敏感业务资料，后续基线优先使用获授权的脱敏／合成材料，生产业务成果另由责任人验收。

### 3.3 ADMIN：AI 问答与事务流程分别记账

`AdminOrchestrator` 有模型控制器、知识来源、快速问答及反馈存储，结果字段区分 `llm_used` 和 `fallback_used`；应用同时装配住宿、班车、车辆、通知等路由。前者是 AI 问答资产，后者是业务流程资产，适合分别建立质量与流程指标，不能把新增二维码入口直接算作 AI 提效。[A01–A02、A07]

最近的发布使用不同部件版本：UI、后端、静态楼层资源各有回执，不能用仓库 HEAD 统一代表整套线上系统。80 项检查验证匿名入口与登录边界，文档明确 `business_verification=not_performed`。[A03–A05]

班车已有更具体的业务数据证据：9 月 14 日固定截止点的 9 月统计为 52 条有效预约、79 人次；历史部门补录处理了 93 条空缺中的 79 条，另外 14 条跳过。两处“79”分别是预约人次和历史补录记录数，不能混用。统计未给出测试记录剔除规则、去重员工数、实际乘车完成率，故仅作为生产业务记录存在的依据。[A06]

**待补：**由行政分别确认问答、预约、住宿等模块的负责人及一个完整业务周期；采纳指标剔除测试、管理员代办及重复记录，流程效果与 AI 贡献分开归因。已有真实记录不代表近期每项手机交互已经验收。

### 3.4 FAE：优先复用产品证据与反馈资产

编排器已有 catalog、spec、sdk、experience、troubleshoot 等证据能力；Gemini 330 专项审计覆盖 335／336／335L／336L 的规格页及不同 USB 链路的深度档位，Gemini 335 软件材料还保存主机、系统、SDK／固件来源。这是后续兼容矩阵的现成起点，但旧快照仍需与最新官方版本对照，不能将推荐平台当完整认证清单。[F01、F06–F07]

已有实际质量改进依据：8 月 28 日复审由六个线上负反馈问题扩展为 20 条开发环境用例，运行门禁全部通过，但语义结果为 19 pass／1 partial；partial 涉及全宽／半宽措辞。该结果只支持该候选版本与该集合的结论，不能转为线上总体正确率，也不证明后续版本已经发布。[F05]

**待补：**产品／FAE 负责人确认首条代表链和真实验收问题；补生产版本、反馈总体分母、问题解决与人工转接结果。兼容矩阵、知识问答和现场验收应共享来源身份及版本，不另造一套无法追溯的答案库。

### 3.5 VOC：先补发布证据连续性

代码直接支持工作台草稿、确认提交、本人记录、补充信息和管理查询；机器人工具支持查询本人 VOC、确认草稿和确认修正。Compose 模板包括 PostgreSQL、workspace、bot-ingest、bot-interact 和一次性迁移。以上是能力与部署形态证据，不代表所有流程都在运行。[V01–V03]

必须同时保留三个时间点：9 月 3 日交接（文件名为 9 月 1 日）记录旧 workspace 健康、独立入口与 Bot 的新发布因身份验收配置不全而未开始；9 月 6 日设计又记载机器人重复确认／Markdown 问题已在生产复现；当前运维手册描述四个常驻服务与迁移 020。这提示早期阻塞记录不是最终实况，但后两份材料仍缺少可将实际运行版与本地 `f8e1602` 对齐的发布回执。[V04–V06]

**待补：**一份最新发布回执，包含工作台／Bot 运行版本、角色边界与验收范围；再统计有效 VOC、确认转化、重复／修正及业务处理结果。不要把“反馈已入库”直接视为“客户问题已解决”，也不把仓库里的分析／报表模块存在视为该能力已启用。

## 4. 全景与下一阶段如何使用本清单

| 全景位置 | 已有可复用资产 | 下一步最小证据 | 建议确认角色（尚非已确认负责人） |
| --- | --- | --- | --- |
| 共用平台 | 身份、目录、附件、应用入口与运行证据接口 | 运行版、入口和应用责任清单；固定周期使用汇总 | 平台／IT |
| 组织人才 | HR 工作流、历史任务分类、可靠性诊断 | 一类岗位任务的成果验收、完成率、耗时与成本基线 | HR 业务负责人＋HR 应用维护人 |
| 行政管理 | 制度问答、预约／住宿等事务流程、通知 | 一条流程去除测试后的业务量与人工耗时；问答单独评估 | 行政负责人＋应用维护人 |
| 产品研发与客户技术支持 | FAE 产品资料、证据审计、线上反馈问题集 | 一个 Gemini 330 型号的兼容组合与产品负责人认可的验收题 | 产品／研发／FAE |
| 客户反馈 | VOC 草稿／确认／查询、机器人代码与身份边界 | 补最新发布回执，再补从提交到业务处理的结果基线 | 客户反馈业务负责人＋VOC 维护人 |

这是复用和取证顺序建议，不是五个新项目立项。HR 的预算和响应交付问题应先纳入现有应用可靠性治理；FAE 的产品证据适合直接支撑公开资料深挖；ADMIN 的真实流程记录可用于设计业务基线；VOC 先消除部署版本不确定性。组织树、系统现状问卷、首条产品链牵头人仍按原设计补齐，不由仓库名称推定部门职责。

下一版统一填写：统计期间与时区、应用／代码／运行版本、业务责任人、排除测试的规则、去重用户与任务、成功／失败／转人工、人工复核质量、耗时、成本、证据链接。先测基线，再依[设计中的投入收益规则](../design/2026-09-20-ai-engineering-panorama-design.md)提出试点。

## 5. 源码快照与复核方法

本轮选用的 34 份来源文件均与各自 HEAD 内容一致。FAE 两份已修改的设计文件及各仓未跟踪材料没有作为本清单的证据；没有清理它们。完整提交、分支、工作区状态摘要、文件 Git 对象和 SHA-256 见配套 JSON。文件链接按这些仓库在同一 `work/` 下的结构提供，交付到其他环境时可按仓库名与提交定位。

| 仓库 | 本轮证据冻结 HEAD | 分支 |
| --- | --- | --- |
| AI-Agent-Platform | `ca601c5e2b74` | `master` |
| AI-HR-Agent | `1bab9c5f7859` | `master` |
| AI-ADMIN-Agent | `065daaf836ae` | `feat/admin-invitations-20260911` |
| AI-FAE-Agent | `b49eeede39bf` | `master` |
| Orbbec-VOC-Agent | `f8e1602248aa` | `master` |

ADMIN 初次记录 HEAD 为 `a3789d4`，冻结证据时已推进至 `065daaf`；两者差异仅为当前工作说明与楼层资源发布交接。本轮没有向源仓库写入，引用以已提交的 `065daaf` 为准，资源发布身份仍为 `a3789d4`。五仓工作区状态摘要与初次盘点一致，不据此宣称其 HEAD 全程未变化。

复核示例（在 `~/Developer/work/AI-HR-Agent` 执行，完整提交取配套 JSON）：

```bash
git show 1bab9c5f78595dad7a26744d4bfaed608a9818e6:docs/reviews/2026-09-16-hr-pg-session-scenario-analysis.md
```

这只复现盘点引用的已提交材料，不连接其文内提及的生产数据源。历史回执的原始证据与本轮源文件核验是两个层次。

## 6. 来源索引

以下由配套清单生成，编号与正文对应。代码／模板只用于能力或部署形态核对，生产与使用结论均依具体历史记录注明边界。

| 编号 | 仓库 | 来源文件 | 类别 |
| --- | --- | --- | --- |
| P01 | AI-Agent-Platform | [backend/app/main.py](../../../AI-Agent-Platform/backend/app/main.py) | 代码 |
| P02 | AI-Agent-Platform | [backend/app/control_plane/office_recipients.py](../../../AI-Agent-Platform/backend/app/control_plane/office_recipients.py) | 代码 |
| P03 | AI-Agent-Platform | [backend/app/agent_brain/orchestrator.py](../../../AI-Agent-Platform/backend/app/agent_brain/orchestrator.py) | 代码 |
| P04 | AI-Agent-Platform | [backend/app/ai_notes/routes.py](../../../AI-Agent-Platform/backend/app/ai_notes/routes.py) | 代码 |
| P05 | AI-Agent-Platform | [deploy/cloud/compose.yaml](../../../AI-Agent-Platform/deploy/cloud/compose.yaml) | 部署模板 |
| P06 | AI-Agent-Platform | [docs/reviews/2026-09-15-hr-same-host-migration.md](../../../AI-Agent-Platform/docs/reviews/2026-09-15-hr-same-host-migration.md) | 历史记录 |
| H01 | AI-HR-Agent | [backend/app/main.py](../../../AI-HR-Agent/backend/app/main.py) | 代码 |
| H02 | AI-HR-Agent | [backend/app/hr_agent/routes.py](../../../AI-HR-Agent/backend/app/hr_agent/routes.py) | 代码 |
| H03 | AI-HR-Agent | [deploy/systemd/ai-hr-agent.service](../../../AI-HR-Agent/deploy/systemd/ai-hr-agent.service) | 部署模板 |
| H04 | AI-HR-Agent | [deploy/systemd/ai-hr-worker.service](../../../AI-HR-Agent/deploy/systemd/ai-hr-worker.service) | 部署模板 |
| H05 | AI-HR-Agent | [docs/reviews/2026-09-15-same-host-migration.md](../../../AI-HR-Agent/docs/reviews/2026-09-15-same-host-migration.md) | 历史记录 |
| H06 | AI-HR-Agent | [docs/reviews/2026-09-16-hr-pg-session-scenario-analysis.md](../../../AI-HR-Agent/docs/reviews/2026-09-16-hr-pg-session-scenario-analysis.md) | 历史使用／业务量汇总 |
| H07 | AI-HR-Agent | [docs/reviews/2026-09-20-hr-chain-closure.md](../../../AI-HR-Agent/docs/reviews/2026-09-20-hr-chain-closure.md) | 历史记录 |
| H08 | AI-HR-Agent | [docs/reviews/2026-09-20-hr-stream-gateway-diagnosis.md](../../../AI-HR-Agent/docs/reviews/2026-09-20-hr-stream-gateway-diagnosis.md) | 历史记录 |
| A01 | AI-ADMIN-Agent | [src/api/server.py](../../../AI-ADMIN-Agent/src/api/server.py) | 代码 |
| A02 | AI-ADMIN-Agent | [src/admin_agent/orchestrator.py](../../../AI-ADMIN-Agent/src/admin_agent/orchestrator.py) | 代码 |
| A03 | AI-ADMIN-Agent | [docs/CURRENT_WORK.md](../../../AI-ADMIN-Agent/docs/CURRENT_WORK.md) | 历史记录 |
| A04 | AI-ADMIN-Agent | [docs/handoffs/2026-09-20-floor7-replacement-release.md](../../../AI-ADMIN-Agent/docs/handoffs/2026-09-20-floor7-replacement-release.md) | 历史记录 |
| A05 | AI-ADMIN-Agent | [docs/handoffs/2026-09-20-floor-join-button-release.md](../../../AI-ADMIN-Agent/docs/handoffs/2026-09-20-floor-join-button-release.md) | 历史记录 |
| A06 | AI-ADMIN-Agent | [docs/handoffs/2026-09-14-shuttle-department-release.md](../../../AI-ADMIN-Agent/docs/handoffs/2026-09-14-shuttle-department-release.md) | 历史使用／业务量汇总 |
| A07 | AI-ADMIN-Agent | [docs/handoffs/2026-09-15-home-chat-release.md](../../../AI-ADMIN-Agent/docs/handoffs/2026-09-15-home-chat-release.md) | 历史记录 |
| F01 | AI-FAE-Agent | [src/agent/orchestrator.py](../../../AI-FAE-Agent/src/agent/orchestrator.py) | 代码 |
| F02 | AI-FAE-Agent | [deploy/docker-compose.prod.yml](../../../AI-FAE-Agent/deploy/docker-compose.prod.yml) | 部署模板 |
| F03 | AI-FAE-Agent | [docs/handovers/上线最终验收报告_20260714.md](../../../AI-FAE-Agent/docs/handovers/上线最终验收报告_20260714.md) | 历史记录 |
| F04 | AI-FAE-Agent | [docs/reviews/2026-07-22-attachment-multimodal-codex-review.md](../../../AI-FAE-Agent/docs/reviews/2026-07-22-attachment-multimodal-codex-review.md) | 历史记录 |
| F05 | AI-FAE-Agent | [docs/reviews/2026-08-28-post-release-feedback-root-remediation-codex-review.md](../../../AI-FAE-Agent/docs/reviews/2026-08-28-post-release-feedback-root-remediation-codex-review.md) | 历史记录 |
| F06 | AI-FAE-Agent | [docs/handovers/Gemini330深度Profile源文档审计_20260721.md](../../../AI-FAE-Agent/docs/handovers/Gemini330深度Profile源文档审计_20260721.md) | 历史来源审计 |
| F07 | AI-FAE-Agent | [Knowledge/Gemini_335/software.md](../../../AI-FAE-Agent/Knowledge/Gemini_335/software.md) | 整理后的知识资料 |
| V01 | Orbbec-VOC-Agent | [src/orbbec_voc/workspace/api.py](../../../Orbbec-VOC-Agent/src/orbbec_voc/workspace/api.py) | 代码 |
| V02 | Orbbec-VOC-Agent | [src/orbbec_voc/interact/tool_executor.py](../../../Orbbec-VOC-Agent/src/orbbec_voc/interact/tool_executor.py) | 代码 |
| V03 | Orbbec-VOC-Agent | [deploy/linux/compose.yaml](../../../Orbbec-VOC-Agent/deploy/linux/compose.yaml) | 部署模板 |
| V04 | Orbbec-VOC-Agent | [docs/handoffs/2026-09-01-voc-standalone-web-and-bot-release.md](../../../Orbbec-VOC-Agent/docs/handoffs/2026-09-01-voc-standalone-web-and-bot-release.md) | 历史记录 |
| V05 | Orbbec-VOC-Agent | [docs/runbooks/voc-aliyun-mvp.md](../../../Orbbec-VOC-Agent/docs/runbooks/voc-aliyun-mvp.md) | 运维流程，非执行回执 |
| V06 | Orbbec-VOC-Agent | [docs/superpowers/specs/2026-09-06-voc-single-confirmation-markdown-design.md](../../../Orbbec-VOC-Agent/docs/superpowers/specs/2026-09-06-voc-single-confirmation-markdown-design.md) | 设计，含生产现象记录 |
