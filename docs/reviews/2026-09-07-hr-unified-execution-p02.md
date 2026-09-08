# P02 本地实现与验收记录

日期：2026-09-07。冻结设计 v0.3；P01 契约代码 `62cdfce`。

状态：本地实现已提交，独立规格/质量复审均通过；不是发布或业务可用声明。

## 范围与提交

- 基线 `80dd6f1`；实现 `557b25a`；backend Ruff import 修正 `d10727b`。
- 新增 Attempt 仓储、唯一未编号 SQL 草稿及真实 PostgreSQL 测试；没有改运行调度、路由、模型或生产配置。
- `create_queued` 是已经授权 Turn 的内部事务入口；公开读取/取消沿 Conversation owner 校验。既有 `ConversationCommandService` 仍负责用户提交授权，P03 才接新运行路径。
- 领取区分首次 `running` 与恢复/取消的 `reconciling`，后者不代表重新执行模型。恢复只增加 lease_epoch，不增加 attempt_no。
- 终态写入要求同 kind/process/epoch 和有效租约；受影响行数为 1 才拥有裁决权。相同终态重投只读返回，不产生第二次裁决。
- `record_terminal` 必须加入调用方非 autocommit 事务。完成证据必须关联本 Turn 的已持久 assistant 消息。P04 后续把 Result、Turn、事件及投递/成果意图接入同一事务，当前不宣称运行链路已接通。

## TDD 证据

以下命令均在工作树 `backend/` 运行，Python 为 `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`。

首个 RED：`python -m pytest -q tests/test_turn_attempts_database.py tests/test_agent_brain_conversation_repository.py tests/test_control_plane_migration.py`，取消请求未落库，`row.cancel_requested_at is not None` 断言失败：1 failed、66 passed。实现取消意图后目标文件 1 passed；不是 ImportError/fixture 错误。

之后逐项 RED/GREEN 覆盖并发单领取、过期接管、旧/未来代次和进程拒绝、幂等终态、结果归属、事务回滚、取消占位与完成竞态。另发现真实行锁等待期间租约过期仍可能通过单条 UPDATE 的时间谓词；先加测试复现接受错误，再修为先取得锁、后按数据库当前时间校验。

完整指定回归：102 passed（36 新测试、66 既有测试）。导入格式修正后目标文件 36 passed。

控制器在 `d10727b` 独立执行扩展回归：

```sh
python -m pytest -q tests/test_turn_attempts_database.py tests/test_agent_brain_conversation_repository.py tests/test_control_plane_migration.py tests/test_execution_contract_v5.py tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py tests/test_execution_relay_crypto.py tests/test_execution_worker_auth.py tests/test_agent_brain_metabot_collaboration.py
```

结果：263 passed in 9.88s，无 warning/skip。backend 目录执行 `python -m ruff check app/agent_brain/turn_attempts.py tests/test_turn_attempts_database.py --output-format concise`：All checks passed。

原实现者从仓库根运行 Ruff 的通过结果不等同 backend 配置通过；发现 I001 后仅修正新增测试 import，另提交保留审计记录，没有修改既有 Ruff 规则。

## 迁移与外部边界

实施前只读核对本地 master `56af396`，最新编号 087；本工作树原 088 保持原样。未获目标库已应用清单，不假设 088 是否已应用，不默认占用 089。

唯一草稿为 `backend/control_migrations/pending/hr_turn_attempts.sql`。真实一次性 PG fixture 显式加载同一文件；既有迁移器只扫描顶层三位编号文件，测试确认不会自动读取 pending。没有伪造 schema_migrations 编号。最终编号/发布迁移验证仍待目标清单核对，不勾为完成。

本轮仅使用本机临时 PG 与合成记录，没有读取真实 DSN、访问生产、发送飞书业务消息、改其他 Bot、Nginx、应用迁移、推送或部署。新旧执行器互斥接线、Result 收口和进程级验收分别留 P03/P04/O02/O03。

## 独立复审

范围 `80dd6f1..d10727b`，独立审查规格 compliant、质量 Approved；无 Critical、Important、Minor。

审查确认数据库约束、真实并发测试、到期后恢复同一 Attempt、锁后重新检查租约和终态只读重投。三项跨任务不可验证事项已逐项归位：生产编号/目标清单留发布前；续租、调度及 execution_owner 切换留 P03/O02；生产 Result/Turn/事件/投递意图原子事务和拒绝回滚留 P04。这里只完成 P02 本地仓储验收，不把这些后续契约标为已经实施。
