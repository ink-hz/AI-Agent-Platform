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

工具开启 `BEGIN READ ONLY`，设置 statement/lock/idle transaction 超时，对每个固定查询使用 savepoint 隔离，并在结束时 `ROLLBACK`。启动时执行零行 `UPDATE` 探针，预期输出 `"write_probe":"rejected"`；若出现 `unexpectedly_allowed`，本次结果不得用于切换判断。

## 输出解释

每个固定资产返回一个状态：

- `ok`：查询成功，`total` 和可选 `groups` 是聚合值。
- `missing_table`：schema 或表不存在。
- `missing_column`：当前表版本缺少查询所需列。
- `unreadable`：当前角色没有表级 SELECT 权限。
- `query_error`：预检通过但聚合查询失败；错误正文不会写入报告。

状态、kind 和引用结果均经过白名单。数据库出现新枚举时输出 `unknown`，不会把任意原值带出。旧执行只用固定 `agent_id` 分类：`hr-bot`、`hannah`、`hr-agent` 计入 `hr`，其余计入 `other`；不读取或解密 payload。部署前应将固定白名单与获准环境实际 HR agent 配置逐项核对，未知 agent 不可自动归为 HR。

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

测试使用 `hr_agent_support` 启动一次性 PostgreSQL，覆盖非零旧候选/新成果、HR 与其他 Bot、缺 schema、缺列、拒权、只读写拒绝、悬空引用、敏感哨兵不出现在输出或错误，以及 DSN/output 文件安全。

将本地验证日志保存在 `artifacts/2026-09-11-hr-e/inventory/`。获准环境的报告应保存到权限受控目录，并记录代码提交、报告文件 SHA-256、运行者与环境名称；不要保存 DSN 文件副本。报告只能支持下一步人工制定承接清单，不能据此自动复制 owner 不明数据、旧私聊、摘要或个人附件正文，也不授权暂停旧链、切流或发布。
