# 岗位标准与成果闭环实施计划

基线：master 21091b13。用户于 2026-09-14 授权修订产品设计第一阶段 P1+P2。

## Global Constraints

- 当前标准唯一权威为 platform_hr_agent.standards；旧 context 只作历史确认记录，禁止映射或回退为当前值。
- 当前成果来自 /api/hr/agent/results 的岗位范围与准确修订；旧 /api/v1/hr/positions/{id}/results 只作只读历史成果。
- 保留官网原文、当前标准、未确认建议的区别。保存成果不代表确认标准。
- 401/403 清除受保护内容；普通读取失败不能表现为空或零；岗位切换与晚到响应不得串数据。
- 接口优先、页面最后；保持真实身份、授权、CSRF、范围、持久化边界。模型提供方替换须明确披露。
- 第一阶段不实施 P3–P8：不改岗位库列表、候选人编辑/比较、情报导航、建岗契约，不恢复旧执行。
- 不暴露裸 UUID/hash；不把旧引用静默转换为云端授权。
- 从同步主线短期分支实施，验证审查后及时回并 master。生产只读核实 phase，不把本地验收称为生产验收。

### Task 1: 接口与数据库闭环证据

Files: 新增 backend/tests/test_hr_position_cloud_reading.py；必要时复用或小幅扩展 backend/tests/helpers 中本地测试设施，不改业务后端。

1. 先核对 backend/tests/hr_agent_support.py、test_hr_agent_b_routes.py、helpers/hr_history_replay.py 与生产 canary 夹具，选择真实本地一次性 PostgreSQL、真实 owner/岗位范围校验与 HTTP 身份/CSRF 边界的最小设施。
2. 通过正常保存工具事务和用户确认接口构造虚构成果/标准，不直接写数据库伪造成功；模型提供方可替换并标注。优先真实 TCP HTTP；如设施限制只能 TestClient，必须明示范围，不称真实网络验收。
3. 验证 cloud phase 下部分确认→current 同 revision、未选项不被覆盖、并发陈旧确认 409、保存成果→岗位过滤列表→准确修订同 id/revision；不同岗位/owner 不可越权。
4. 旧 context/results 在 cloud 仍可正常读取的反向证据；前端 Task 2 断言这些返回值只作历史。
5. 跑新增及必要标准相关回归；记录命令、结果、模型/身份替换边界。若真实 HTTP 设施过重，复用已存在真实 canary 而非削弱校验。
6. 提交测试，报告未覆盖的具体边界。

### Task 2: P1+P2 前端读取替换

Files: webui/src/workspaces/hr/HrPositionWorkflow.tsx 与测试；必要时新增相邻当前标准/成果阅读组件；webui/src/hrLoopApi.ts 与测试仅在契约支持需要时修改。其他前端文件仅为直接集成需要，不扩大 UI 改造。

1. 先补失败组件回归：旧 context/current 与旧 results 返回非空，云端返回不同内容，当前栏只显示云端；历史栏包括旧 current 及 superseded，旧成果可读可下载但不可确认或执行。
2. 使用 memoized createHrLoopApi(account.csrf_token)，岗位存在校验之后加载云端 current 与岗位范围成果全部分页。标准 404 not_found 表示尚无当前值；其他错误保留真实失败。维护状态隔离与失败关闭。
3. 标准按 items 文本展示，标注 revision；不输出内部 ID。岗位总览同一数据来源。旧候选人组件依赖的 context 暂保留作为旧链数据，不用云端 revision 伪造 contextVersionId。
4. 成果使用现有 SavedResult/ExactRef 协议，按准确 ref 打开正文与下载；验证读取 ref/岗位范围。页面类型分组：requirements=role_calibration,jd,requirements,standard_proposal；sourcing=sourcing；candidates=candidate_assessment；interviews=interview_plan,interview_record；review=retrospective；research 不混入五阶段。旧 analysis 共用提示退出当前栏。
5. 岗位页保持阅读职责，已有普通“在主对话推进”入口保留。只在已存在的可靠云端 exact-ref 选择机制可复用时添加成果继续引用；不为 P1/P2 新建跨路由状态系统。所有提案确认仍由主对话已有标准提案 UI 和用户 HTTP 完成。
6. 覆盖五阶段归属、分页/准确下载、404/503/403、岗位切换晚到响应、历史只读。运行相关组件/API 客户端测试，最后 build 与一次完整前端回归。
7. 提交代码并报告测试证据与已知限制。

### Task 3: 验收、文档和主线闭合（主代理）

1. SSH 只读核实生产 cutover phase，记录实际发布提交，不输出秘密。
2. 同步 HR总体架构设计.md、HR_Agent工作流.md 与 AGENTS.md 的最新授权/读取权威；旧设计留历史。
3. 接口与组件通过后做关键页面阅读/滚动验收；工具不可用则准确标注未验收。
4. 独立审查各任务及最终分支，修复重要问题，再合入并推送 master。
5. 分项报告接口、进程故障、组件、浏览器、生产范围；清理仅本任务工作树，保留原有用户文件。
