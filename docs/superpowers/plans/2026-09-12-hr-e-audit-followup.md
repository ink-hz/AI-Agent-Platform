# HR E 审计保留项修订计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 修复遗漏回归的夹具，补齐旧HR数据和排空边界验证，纠正W映射与切换手册中的过强结论。

**Architecture:** 继续使用现有云端Loop及事务切换闸门；不扩大模型、个人材料或生产操作授权。数据库历史与验收证据保留，实际计数修订采用追加迁移，避免改写103已留存的身份。

**Tech Stack:** Python、pytest、真实一次性PostgreSQL、签名HTTP、现有Bash/Python运维片段。

## Global Constraints

- 接口优先，页面最后验收；本轮无产品页面修改，不进行浏览器巡视。
- 不访问生产、不调用模型、不停用服务、不发送消息。
- 不修改原C/D/E与接管证据，不恢复或伪造缺失日志；新增证据写入 `artifacts/2026-09-12-hr-e-audit-followup/`。
- 所有新试跑使用独立文件保留命令、退出码和原始输出；旧25项审读无日志的事实独立披露。
- 当前工作树 `.worktrees/hr-cloud-loop-e-release` 已隔离，基线 `192a448`；各任务仅修改分配文件，不提交他人修改。
- HR总体架构设计和工作流同步执行与验收边界；保留正式上线未完成状态。

## Task 1: 夹具和D1旧数据覆盖

Files: `backend/tests/test_conversation_attachment_binding.py`、相关direct/知识上下文夹具、`backend/tests/test_agent_brain_hr_history_isolation.py`。

- [x] 定位审核中的四个漏跑文件，逐文件运行并保留收集错误/失败。
- [x] 与已修复的direct binding夹具对照，使用真实hr_web迁移建立当前schema，不绕过record_turn_scope_v6。
- [x] 新增持久 `hr_input_context IS NULL` 的旧HR轮次测试，同时覆盖用户历史、助手历史及滚动摘要排除；把闸门收窄为is_hr_v6的临时隔离突变必须失败。
- [x] 修复夹具后运行四文件、D1和共用夹具受影响的直接调用回归；保留原断言或解释契约变更。

## Task 2: 排空边界

Files: 追加 `backend/control_migrations/104_hr_execution_drain_terminal_contract.sql`、`backend/tests/test_hr_cutover_count_review.py`、schema/preflight/迁移助手所需检查。

- [x] 验证028约束拒绝interrupted缺terminal_at；实际保护应为未确认stop、活跃关联及v5准确lineage。
- [x] 在104重新定义count函数去掉不可达分支，保留102/103原字节与管理权限；无新运行行为时不伪造修复前失败。
- [x] 对v5 lineage中尚未独立覆盖的条件、缺表拒绝与真实计数查询补测试，缺少依据时保留上线现场核验前置。
- [x] 将104纳入必要的就绪及部署检查，聚焦运行排空/preflight/迁移回归。

## Task 3: 切换命令与失败草稿处置

Files: `docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md`、必要的本地运维测试。

- [x] 为准确计数和transition命令设置事务局部 `lock_timeout` 与 `statement_timeout`；真实双连接竞争验证超时与事务回滚，空闲情况下计数可执行。
- [x] 列清ready及failed草稿去向；重试仍是新受理，draining期间不改成自动跨链恢复。将failed保留只读、放弃及进入drain前重试的条件写清；现行状态机无draining_legacy直接撤回legacy入口。
- [x] 明确旧执行器持久退出后不能直接切回legacy接单；仅在旧运行配置/实例按批准快照恢复并验证后才允许恢复入口，否则保持draining_cloud修复。

## Task 4: W表与证据收尾

Files: 两份根HR文档、E复审入口、新评审说明与本轮证据manifest。

- [x] W2/W7/W8/W9逐项绑定本轮具体日志；其他W不得因测试数增加升级为业务通过。
- [x] 披露原独立审读25passed无原日志；新复跑只能记新证据，不补写旧运行。
- [x] PM2表述统一为已读取本地入库配置及脚本，实际生产部署身份未核验。
- [x] 说明上一轮绿色集合漏了四文件、原日志8项缺失、styles三项既有失败、实际生产计数仍未执行。
- [x] 有界独立代码审阅、相关整合回归、历史证据哈希核对；按范围提交后给出工程与上线两个判断。
