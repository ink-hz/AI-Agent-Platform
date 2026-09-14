# 岗位标准与成果闭环实施计划

基线：master 21091b13。用户于 2026-09-14 先授权 P1+P2，随后明确「旧的直接放弃」「对，旧的直接去掉」「根本就没人用，纯在浪费我们的注意力」。最新范围为当前闭环加旧界面直接退出，不建设兼容、迁移或历史阅读区。

## Global Constraints

- 当前标准唯一权威为 platform_hr_agent.standards；岗位页不读旧 context，不保留旧标准历史区，不映射或回退。
- 当前成果来自 /api/hr/agent/results 的岗位范围与准确修订；旧 /api/v1/hr/positions/{id}/results 退出前端调用，不保留历史成果区。
- 保留官网原文、当前标准、未确认建议的区别。保存成果不代表确认标准。
- 401/403 清除受保护内容；普通读取失败不能表现为空或零；岗位切换与晚到响应不得串数据。
- 接口优先、页面最后；保持真实身份、授权、CSRF、范围、持久化边界。模型提供方替换须明确披露。
- 最新指令覆盖原 P3–P6 的旧界面退出及岗位列表；复用当前候选人工作、当前情报资料与研究功能，不重造候选人管理、不扩建岗契约，不做 P7/P8 展示扩张，不恢复旧执行。
- 不暴露裸 UUID/hash；不把旧引用静默转换为云端授权。
- 从同步主线短期分支实施，验证审查后及时回并 master。生产只读核实 phase，不把本地验收称为生产验收。

### Task 1: 接口与数据库闭环证据

Files: 新增 backend/tests/test_hr_position_cloud_reading.py；必要时复用或小幅扩展 backend/tests/helpers 中本地测试设施，不改业务后端。

1. 先核对 backend/tests/hr_agent_support.py、test_hr_agent_b_routes.py、helpers/hr_history_replay.py 与生产 canary 夹具，选择真实本地一次性 PostgreSQL、真实 owner/岗位范围校验与 HTTP 身份/CSRF 边界的最小设施。
2. 通过正常保存工具事务和用户确认接口构造虚构成果/标准，不直接写数据库伪造成功；模型提供方可替换并标注。优先真实 TCP HTTP；如设施限制只能 TestClient，必须明示范围，不称真实网络验收。
3. 验证 cloud phase 下部分确认→current 同 revision、未选项不被覆盖、并发陈旧确认 409、保存成果→岗位过滤列表→准确修订同 id/revision；不同岗位/owner 不可越权。
4. 前端 Task 2 断言旧 context/results 根本不再被调用；已写的旧兼容夹具随最新指令移除，不维护旧读取能力。
5. 跑新增及必要标准相关回归；记录命令、结果、模型/身份替换边界。若真实 HTTP 设施过重，复用已存在真实 canary 而非削弱校验。
6. 提交测试，报告未覆盖的具体边界。

### Task 2: P1+P2 前端读取替换

Files: webui/src/workspaces/hr/HrPositionWorkflow.tsx 与测试；必要时新增相邻当前标准/成果阅读组件；webui/src/hrLoopApi.ts 与测试仅在契约支持需要时修改。其他前端文件仅为直接集成需要，不扩大 UI 改造。

1. 补组件回归：当前栏只显示云端；旧 context/results 无调用、无历史入口；成果可按准确版本阅读与下载。
2. 使用 memoized createHrLoopApi(account.csrf_token)，岗位存在校验之后加载云端 current 与岗位范围成果全部分页。标准 404 not_found 表示尚无当前值；其他错误保留真实失败。维护状态隔离与失败关闭。
3. 标准按 items 文本展示，以 confirmed_at 表达确认时间；准确 revision 留在数据引用和测试中（它本身是 UUID，不直接展示）。岗位总览同一数据来源。旧候选人组件退出；候选人材料与工作沿用主工作台已有云端组件，不伪造 contextVersionId。
4. 成果使用现有 SavedResult/ExactRef 协议，按准确 ref 打开正文与下载；验证读取 ref 完整一致、请求绑定当前岗位范围。注意服务器按 result_links 纳入后来关联的成果，SavedResult.objects 保留保存时范围；不能要求正文 objects 含岗位而误拒绝合法后关联成果。页面类型分组：requirements=role_calibration,jd,requirements,standard_proposal；sourcing=sourcing；candidates=candidate_assessment；interviews=interview_plan,interview_record；review=retrospective；research 不混入五阶段。旧 analysis 共用提示退出当前栏。
5. 岗位页保持阅读职责，已有普通“在主对话推进”入口保留。只在已存在的可靠云端 exact-ref 选择机制可复用时添加成果继续引用；不为 P1/P2 新建跨路由状态系统。所有提案确认仍由主对话已有标准提案 UI 和用户 HTTP 完成。
6. 覆盖五阶段归属、分页/准确下载、404/503/403、岗位切换晚到响应、旧接口无调用。运行相关组件/API 客户端测试，最后 build 与一次完整前端回归。
7. 提交代码并报告测试证据与已知限制。

### Task 3: 验收、文档和主线闭合（主代理）

1. SSH 只读核实生产 cutover phase，记录实际发布提交，不输出秘密。
2. 同步 HR总体架构设计.md、HR_Agent工作流.md 与 AGENTS.md 的最新授权/读取权威；旧设计留历史。
3. 接口与组件通过后做关键页面阅读/滚动验收；工具不可用则准确标注未验收。
4. 独立审查各任务及最终分支，修复重要问题，再合入并推送 master。
5. 分项报告接口、进程故障、组件、浏览器、生产范围；清理仅本任务工作树，保留原有用户文件。

### Task 4: 旧界面直接退出与岗位列表

Files: App.tsx、HrWorkspacePage.tsx（重写为只含当前岗位/情报页面的轻薄组合或删除换明确页面）、HrPositionIndex.tsx/测试、HrPanoramaWorkspace.tsx/测试、hrCloudLaunch.ts/HrLoopWorkspace.tsx（仅现有候选人/方法入口接入）、相关零引用旧组件/测试；必要时 hrApi.ts 清无调用死方法。

1. /hr/、/hr/agent、/hr/chat 仍为当前 HrLoopWorkspace。岗位列表与岗位流程保持独立、可滚动页面；不通过「点岗位就弹回对话」替代页面。
2. HrWorkspacePage 移除 DirectAgentWorkspace、旧对话/旧成果/旧候选人/旧知识抽屉、cloudPrimary 双链布尔及对应状态。旧 /hr/conversations、/hr/positions/:id/conversations、/hr/panorama/reports 路由停止承载旧 UI（普通 not-found 或当前主入口，不能保留旧阅读实现或新建兼容页）。
3. 岗位库改为真正单列列表，保留搜索、状态、岗位详情入口；删除「待确认」、确认/合并/忽略以及「用对话新建岗位」死链。只读取实际存在岗位列表；不承诺当前没有的建岗能力。
4. 保留已发布的 HrPanoramaWorkspace 原始资料/AI 分析报告两层及 HrResearchWorkspace/HrSourceWorkspace，它们是当前有效功能。删除 archive/topics/company/topic/bundle_id 触发的 HrLegacyPanoramaWorkspace 归档分支，移除仅为旧归档服务的组件和 API 客户端。
5. 旧 HrCandidateWorkspace 退出。岗位候选人阶段通过明确「候选人材料」「候选人工作」入口打开主工作台已有 HrLoopCandidatesPanel/HrLoopCandidateWorkspace。可以扩展现有 owner/position 内存草稿交接为可选 panel，自动打开正确当前面板；不自动发送，不转换旧引用，不另建跨页面状态系统。跨岗位候选人和现有确认/局限约束保持当前实现。
6. 顶部导航只保留对话/岗位/HR 情报；方法从当前主工作台既有专业方法入口进入，删除旧知识弹层。
7. 以 rg 的全仓引用证据删除零引用旧组件、自有测试及只服务旧 UI 的 API 方法；保护 HrPositionPicker、当前官网原文和材料文件面板、现行情报 source/research、其他产品共享 DirectAgentWorkspace。
8. 新增路由/列表/面板接入回归，保留当前 API 身份/范围边界。删除新 Task1 中为旧 context/results 兼容额外建立的夹具及断言；保留真实会话、标准与成果过滤/准确修订测试，重跑 TCP helper。
9. 先相关 API/组件，再 build 和本次跨路由更改的一轮完整前端回归，最后一轮当前页面验收。任务报告写实际退出代码/调用，不做旧数据迁移或生产删除。
