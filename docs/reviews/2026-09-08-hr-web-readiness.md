# HR 网页就绪检查：本地验证与独立复审

状态：限定的就绪/领取切片已通过本地验证与独立规格、质量复审。不是整个 Worker、网页业务或生产完成。

## 范围

- Platform：465a47b915324584307a95ca5be230897bfef8d7 → bf1e29b862eb91b5151141c40dc056c597549e9f，14 个文件。
- MetaBot：91d55cccd23a1901e73eabccdf96bc32a1735d62 → bda5aa1b6277a3b1f76217c742909550033cf45a，6 个文件。
- 已实现：真实默认关闭的运行组合、只读就绪探测、签名新鲜观察、领取前检查、同会话占位、无能力时独立取消/核对领取。
- 新元数据只扩展既有 Worker/Attempt；没有新执行队列。预算和熔断读取采用无副作用方法，原执行入口不变。

## 主控独立验证

2026-09-08 固定上述提交，未改实现代码，执行：

```sh
# Platform backend cwd
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_readiness_v5.py tests/test_turn_attempts_database.py tests/test_hr_direct_worker.py tests/test_hr_direct_command_binding.py tests/test_execution_transport_v5.py tests/test_execution_v5_upload.py tests/test_execution_worker_v5_receiver.py tests/test_execution_worker_runtime.py tests/test_execution_worker_auth.py tests/test_execution_relay_api.py tests/test_execution_relay_repository.py tests/test_metabot_relay_client.py tests/test_metabot_collaboration_v4.py tests/test_agent_brain_conversation_summary.py tests/test_hr_task_result_projection_database.py tests/test_hr_candidate_analysis_artifact_migration.py tests/test_hr_position_package_database.py --tb=short
```

结果：499 passed in 46.36s，exit 0，无 warning/skip。

```sh
# MetaBot worktree cwd; both actual cross-repository probes enabled
PLATFORM_V5_TEST_BACKEND=/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-position-core-availability/backend PLATFORM_V5_TEST_PYTHON=/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/core-chat-v5-http.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-event-contract.test.ts tests/core-chat-routes.test.ts tests/http-server-cross-verify.test.ts tests/core-chat-event-outbox.test.ts tests/core-chat-callback-drain.test.ts tests/core-chat-v5-lifecycle.test.ts tests/core-chat-v5-stream-owner.test.ts tests/execution-recovery-ledger.test.ts tests/local-runtime-store.test.ts
```

结果：304 passed / 12 files，10.38s，exit 0，无 skip。实际就绪链路测试 3.167s；既有认证传输/丢回执恢复测试 4.494s。继承测试输出包含其自有合成 receiver PID 39840，不是生产进程或真实模型执行。

Python 13 文件编译和新/原干净范围 Ruff 通过。三个修改中的旧文件诊断逐条 code/message 与基线相同：middleware 3、metabot_client 8、worker 26，总计 37；不宣称全仓 lint 干净。MetaBot TypeScript noEmit、6 文件 ESLint 和范围 whitespace 通过。

真实边界包括自有 PG/HTTP、签名、MetaBot 服务探测、Worker loopback 验证和原 Attempt 领取；就绪探测不执行模型。传输故障测试仅替代 native/provider 边界；没有完整新 DirectWorker 进程或生产业务验收。

## 独立复审

独立 reviewer 读取两个完整原始 base→head 包；结论 Spec compliant / Task quality Approved。Critical、Important、Minor 均无。

额外定向检查：Worker 传输异常继承能否进入故障处理；只读预算/熔断方法是否保持原准入计算。均无新增缺陷。未重复执行套件或修改代码。

## 未完成边界与后续

真实 Result/Turn/message/event 原子发布、任务身份兼容、绑定 run 的核对/停止/原结果恢复、独立 DirectWorker、真实有界上下文、启动配置校验、网页快照及材料/PDF 尚未因本切片完成。

用户已确认 [最小可靠网页交付边界](../superpowers/specs/2026-09-08-hr-minimum-reliable-web-design.md)：接下来按 [网页闭环计划](../superpowers/plans/2026-09-08-hr-minimum-reliable-web.md) 推进；飞书统一迁移与自动重跑许可后移。共享签名配额保持原值，网页运行验收必须验证其实际调度预算。

未连接生产、应用迁移、启用默认 v5、修改模型、发送飞书消息、推送或合并；Team 和其他应用未变。原两份 dirty 文档及环境文件保留。
