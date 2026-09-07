# P03b2a：认证派发与持久事件上行

状态：本切片已完成控制器修后复验和完整范围独立复审；规格符合、质量 Approved，首轮 Important 已关闭，无剩余 Critical/Important/Minor。此记录不作为启用新流量的依据，完整 P03 仍在实施。

首个候选：Platform `1bb4e65..b35385f`（17 个文件）；MetaBot `8cb4252..91d55cc`（仅一个现有集成测试文件，运行时代码不变）。下列首轮验证不冒充后续修复提交的验证。

## 本切片边界

复用 P03b1 的加密冻结命令与 Attempt 归属，经现有签名 Worker 通道完成命令交接、MetaBot Bearer 接收回执和原始事件上行。不增加另一套执行状态权威，不改共享旧协议的作业租约约束。

- 回调凭据在现有加密作业载荷内产生一次并持久保存；同一命令重传、进程重启和新租约接管继续使用原凭据。回调地址和目标 Worker 固定，不能静默迁移。
- 目标 Worker 是现有 binding 上的传输路由事实，不占用排队作业的执行租约。派发前在现有锁序与有效租约下持久记录 offered；未知响应不能释放或复用命令序号。
- 当前租约持有者显式授权交接；确认代次只表示该授权的传输回执，不代表执行完成。回调登记必须先于云端接收确认提交。
- 原始事件按原始 UTF-8 字节签名和发送，遵守既有 1 MiB 请求上限。云端加密保存原文，原启动代次的迟到证据不直接改变 Turn/Attempt。
- 上行游标、缺序号补传、退避和单任务协议隔离持久化；回执丢失只补传原事件，未交付证据不删除。

## 首个候选验证

实现方相关回归：Platform 658 项 / 37.85 秒；MetaBot 287 项、11 个文件 / 8.01 秒。

控制器于 2026-09-08 在上述精确提交上复验：

- Platform 扩展回归 **762 项通过 / 47.39 秒**；保留旧网页/V2 接口的 64 条 Starlette cookie 弃用告警。
- MetaBot **287 项、11 个文件通过 / 7.98 秒**；48 项 HTTP 测试包括新增真实跨仓库探针（4.457 秒），没有跳过；保留既有测试接收进程 PID 输出。
- 9 个修改生产 Python 模块编译通过；11 个新增/定向文件 Ruff 通过。其余修改生产文件的 39 条既有 Ruff 诊断，代码和消息均与基线一致，不能称为全库静态检查无告警。MetaBot 编译、定向 ESLint 和两个仓库差异空白检查通过。

跨仓库探针必须同时设置 `PLATFORM_V5_TEST_BACKEND` 为该工作树的 `backend`，以及 `PLATFORM_V5_TEST_PYTHON` 为现有平台虚拟环境解释器；否则该项测试会显式跳过。控制器复验已经设置两者。

故障证据穿过实际签名云端 HTTP、Bearer MetaBot 接口、PostgreSQL 与测试专用回调端口：接收提交后响应 socket 丢失、回调登记前 401、同代次逆序回执、原启动代次终态经新传输代次上行、云端原事件提交后只返回部分响应，以及发送/上传子进程 SIGKILL 后由新进程恢复。测试只替换 PTY/模型执行边界，云端/接收服务器在子进程被杀时仍运行，不能称为整个 API、DirectWorker 或真实 Claude 被杀后的验收。

测试夹具的导入路径、被占用端口和错误合成材料身份不计作 RED。未接触占用端口的服务，改用操作系统分配的测试专用端口。

## 首轮独立复审及修复

规格：一项问题；质量：Needs fixes。无 Critical/Minor，其余本切片要求在审查范围内成立。

Important：`handoff()` 无界查询启动外层事务；逐候选 `connection.transaction()` 实为保存点。跳过已确认候选后，锁仍保留到整次轮询结束，后续阻塞候选可能拖住无关任务续租。控制器已核对源码确认。

修复提交：Platform `22bb6d2`，仅修改命令绑定仓储和其 PostgreSQL 测试。数据库候选发现最多 16 条，发现事务先结束，每条候选再完成独立事务；所有检查过的候选按既有 v5 传输时间戳轮转，已确认/过期授权前缀不会长期挡住后续候选。权威锁序和最后加锁后的数据库租约校验保持不变。

真实 PostgreSQL RED→GREEN：首先用 `pg_blocking_pids` 证明前一条已确认任务的续租被后一条阻塞候选拖住；事务修复后，后一条仍阻塞时，前一条已可续租。随后真实查询 17 条而非 16 条的断言失败；添加 SQL 上限后又复现两个前缀导致第 17 条一直取不到，轮转修复后均通过。定向三项通过；修复方四个覆盖文件 93 项通过 / 17.04 秒；启用的真实跨仓库探针通过，其余 47 项在该定向调用中明确过滤。

控制器在最终组合 Platform `22bb6d2` / MetaBot `91d55cc` 上重新完整验证：**765 项 Python 通过 / 48.52 秒；287 项 MetaBot、11 个文件通过 / 7.97 秒**。48 项 HTTP 测试包括跨仓库探针（4.598 秒），未跳过。仍有相同的 64 条旧 cookie 告警和既有测试接收进程 PID 输出。修复两文件编译、11 文件定向 Ruff、完整原始提交范围空白检查通过；其他静态检查源文件与上一轮基线对照未变。

完整原始范围复审：Platform `1bb4e65..22bb6d2`（两个提交），MetaBot `8cb4252..91d55cc`；独立审查者核对修复和真实并发证据，最终规格符合、质量 Approved，无剩余 Critical/Important/Minor。所有跨任务关口保留到下节对应任务，不以本切片通过抹去。

## 控制器复验命令

Platform 工作树 `backend` 目录：

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_frozen_command_v5.py tests/test_execution_acceptance_v5.py tests/test_hr_direct_command_binding.py tests/test_hr_direct_worker.py tests/test_execution_contract_v5.py tests/test_turn_attempts_database.py tests/test_execution_relay_repository.py tests/test_execution_relay_api.py tests/test_execution_worker_auth.py tests/test_execution_worker_runtime.py tests/test_execution_worker_store.py tests/test_execution_worker_v5_receiver.py tests/test_metabot_relay_client.py tests/test_agent_brain_conversation_summary.py tests/test_hr_task_result_projection_database.py tests/test_hr_candidate_analysis_artifact_migration.py tests/test_hr_position_package_database.py tests/test_agent_brain_conversation_api.py tests/test_agent_brain_v2_conversation_api.py tests/test_agent_brain_conversation_repository.py tests/test_agent_brain_orchestrator.py tests/test_execution_transport_v5.py tests/test_execution_v5_upload.py
```

MetaBot 工作树根目录（必须保留这两个仅用于本地测试的变量）：

```sh
PLATFORM_V5_TEST_BACKEND=/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-position-core-availability/backend PLATFORM_V5_TEST_PYTHON=/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/core-chat-v5-http.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-event-contract.test.ts tests/core-chat-routes.test.ts tests/http-server-cross-verify.test.ts tests/core-chat-event-outbox.test.ts tests/core-chat-callback-drain.test.ts tests/core-chat-v5-lifecycle.test.ts tests/core-chat-v5-stream-owner.test.ts tests/execution-recovery-ledger.test.ts
```

## 后续强制关口

P03b2b 的实际独立 Worker、能力先于领取、真实上下文/任务关联和取消调度尚需实现；P04 Result 发布、M04b 认证停止/统一重跑预算、M05 附件与成果以及 O02/O03 启用和受控验收分别保留。传输交接与事件落库不等同于这些能力已经完成。

未连接生产、未应用生产迁移、未发布/切流量、未调用真实模型、未发送飞书业务消息。其他 Bot、共享 Nginx 和用户原有未提交文档不在改动范围。
