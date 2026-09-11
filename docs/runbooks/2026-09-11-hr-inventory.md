# HR E1 只读资产盘点运行手册

`tools.hr_agent.inventory` 只生成新旧 HR 存储的聚合计数与引用诊断。它不读取业务正文或逐条身份，不导入、映射或修改数据，也不提供自由 SQL。

## 运行前提

- 仅在已经批准的环境运行；本地验证使用一次性 PostgreSQL。
- 凭据写入独立普通文件，文件必须是 mode `0600`、非符号链接。命令不会回显路径或内容。
- 数据库角色只需目标表的 `SELECT`。缺表、缺列或无权限会分别报告，不能解释为零数据。
- 首次运行不要直接指向生产。先在与目标迁移版本一致的隔离环境核对输出 schema 和权限。

## 命令

从 `backend/` 目录运行：

```bash
python -m tools.hr_agent.inventory --dsn-file /approved/path/hr-inventory.dsn
```

写入文件时目标必须尚不存在、父目录必须存在；工具以 mode `0600` 原子新建，拒绝覆盖和符号链接：

```bash
python -m tools.hr_agent.inventory \
  --dsn-file /approved/path/hr-inventory.dsn \
  --output /approved/path/hr-inventory-report.json
```

工具开启 `BEGIN READ ONLY`，设置 statement/lock/idle transaction 超时，对每个固定查询使用 savepoint 隔离，并在结束时 `ROLLBACK`。运行时只执行 `SELECT`、`SHOW transaction_read_only`、事务控制和 `SET LOCAL`；报告中的 `transaction_read_only` 必须为 `true`。写入拒绝只在一次性测试数据库中验证，不在获准环境执行写探针。

## 输出解释

每个固定资产返回一个状态：

- `ok`：查询成功，`total` 和可选 `groups` 是聚合值。
- `missing_table`：schema 或表不存在。
- `missing_column`：当前表版本缺少查询所需列。
- `unreadable`：当前角色没有表级 SELECT 权限。
- `query_error`：预检通过但聚合查询失败；错误正文不会写入报告。

成功资产另带 `scope`。`complete` 表示 catalog 未发现会限制当前角色可见行的 RLS；`scope_limited` 表示主表或 join 依赖启用了当前角色不能绕过的 RLS，此时计数只是可见范围，不能当作全库计数。该标记不证明 `complete` 已获得业务上的全量环境授权。

状态、kind 和引用结果均经过迁移或 HTTP 契约白名单。数据库出现新枚举时输出合并后的单一 `unknown` 桶，不会把任意原值带出。旧执行只把精确 `agent_id='hr-bot'` 计入 `hr`，其余计入 `other`；不读取或解密 payload。部署前应将该身份与获准环境配置逐项核对，未知 agent 不可自动归为 HR。

引用诊断当前只解析：

- 新候选岗位关系到同 owner 的 `platform_hr.positions`，结果为 `resolvable`、`wrong_owner` 或 `missing`。
- 新成果的 position link；其他 object kind 统一为 `unsupported`。
- `reference_edges` 只按受控 source kind 计数；它不宣称引用已全部有效。

报告不包含 UUID、姓名、岗位标题、文件名、URL、hash、manifest、JSON payload、密文或 DSN。运行日志也不得增加数据库异常正文或连接字符串。

## 验证与留证

接口优先策略下，本工具的验收是数据库与 CLI 自动化，不需要浏览器。运行测试：

```bash
pytest -q tests/test_hr_agent_inventory.py
```

测试使用 `hr_agent_support` 启动一次性 PostgreSQL，覆盖非零旧候选/新成果、pending 草稿、10 种成果契约中的非 research 类型、HR 与其他 Bot、缺 schema、join 依赖缺列、拒权、RLS 范围提示、只读写拒绝、严格且大小写不敏感的 UUID 解析、悬空/错 owner 引用、未知桶合并、敏感哨兵不出现在输出或错误，以及 DSN/output 文件安全。

将本地验证日志保存在 `artifacts/2026-09-11-hr-e/inventory/`。获准环境的报告应保存到权限受控目录，并记录代码提交、报告文件 SHA-256、运行者与环境名称；不要保存 DSN 文件副本。报告只能支持下一步人工制定承接清单，不能据此自动复制 owner 不明数据、旧私聊、摘要或个人附件正文，也不授权暂停旧链、切流或发布。

## 本版明确未覆盖的旧资产

本注册表是 E1 的有界盘点面，并非完整 P2。以下旧资产仍未纳入计数：

- `platform_hr.position_task_requests` 与 `position_task_records` 的请求/记录关系；record 没有状态列，需要先确定安全的完成判据。
- `platform_control.direct_command_bindings`、`turn_attempts`、`v5_source_events` 与 mission run 的旧 v5/v6/v7 派发、接受和恢复关系。
- 架构文档提到的 `tool_operations_v6` 敏感操作存储；当前本地迁移和代码未找到同名表定义，不能猜测实际表名或计数。
- `result_artifact_intents` 到 source event、message、grant、attachment 的完整悬空关系，以及 artifact current revision 的一致性。
- panorama 的旧 run/batch/publication/current 链和情报 bundle current 指针悬空诊断。
- conversation、turn、summary 和旧解析提交之间的在途归属；本工具刻意不读取消息或 payload 来推断。

这些缺口必须通过后续固定、非敏感 join 设计补齐。当前报告不得用于宣称旧链已经排空、全部历史成果可承接或 P2 已完成。
