# HR 迁移监督器独立审阅

审阅者未编写本迁移实现；仅运行隔离 PostgreSQL、受控 Docker CLI 替身和真实本地信号，未修改实现，未连接生产或启动真实 Docker 容器。首次审阅的 `deploy/cloud/hr_agent_migrate.py` SHA-256 为 `149a47741696822a2cb0fe35db535a7bbfb295f161d6618744b57fe903f95990`。实现正在由 root 修订；以下首审结论绑定该指纹，不能直接套用到之后的文件。

## 首审发现

### P1：清理期间收到终止信号仍启动下一个环境

位置：`Supervisor.on_signal`、`migrate` 尾部及 `run` 环境循环（首审文件约 235–249 行）。

`cleaning=True` 时信号处理器只设置 `signal_number`，不会抛出异常；production 清理成功后，环境循环没有检查该标志，继续创建、授权并执行 preview。最终退出码虽是 143、回执是 interrupted，取消信号之后却已执行新的迁移。

复现：Docker 替身只在 production REVOKE 前延迟 0.7 秒，标记到达后向真实监督器进程发送 SIGTERM，其余角色 GRANT/REVOKE 和迁移都作用于一次性 PostgreSQL。结果是 `start_count=2`、环境顺序 `production, preview`，最后 membership 为 0。证据：`independent-probe.jsonl` 第一行；可复现脚本 `independent_review_probe.py`。

修复判据：清理应完成，但收到信号后不得开始任何后续环境；退出仍表明中断并保留已执行范围。

### P2：启动前权限不干净时回执仍声称 cleanup_verified=true

位置：`validate` 的 at-rest 检查、`cleanup` 的初值及 `run` 的 finally（首审文件约 126–127、152–155、188、257–264 行）。

已有 owner membership 时拒绝迁移是正确行为，且不应替其他操作撤销其权限。但由于本进程没有记录 `granted_owner`，cleanup 将 revoked 初始化为 True，最终写入 `cleanup_verified=true`；实际集群中 membership 仍为 1，stderr 也没有 unresolved 标记。

复现结果：`failure_code=at_rest_not_clean`、`start_count=0`、退出 1；同时 `owner_memberships_remaining=1`、`cleanup_verified=true`。证据：`independent-probe.jsonl` 第二行。

修复判据：明确保留 at-rest 未解除的状态，不能把“本次没有新授权可撤销”解释为“全局权限已清洁”；不自动撤销原有权限或终止非本次会话。

## 已观察到的有效控制与边界

- wrapper 使用 `exec python3`，不会额外保留一个 shell 层来吞信号。
- 授权发生在 named container 创建后，并在 GRANT 前持久记录可能暴露的身份；丢失授权回执时仍有撤销目标。
- 清理先检查/停止/必要时 kill 具名容器，再 REVOKE；不能确认容器停止或权限/会话归零时不应通过。撤销 membership 不会撤销其他已执行 SET ROLE 的会话，故 session 检查是必要条件。
- CLI 超时与 PostgreSQL statement/lock timeout 是两层边界。杀 Docker CLI 不等于杀 Docker 容器；代码为此另有 inspect/stop/kill，而不是仅依赖 subprocess 返回。
- 每个 CLI 调用有单独时限，清理是多个调用的顺序组合；这不是整个部署严格等于 `migration_timeout` 的总时限。SIGKILL、宿主机丢失、Docker daemon 不可达、已有 SET ROLE 会话的实际停机恢复仍需外部对账，不能由这组测试宣称闭合。
- 父进程不打印子进程 stdout/stderr，内部错误码固定；回执使用 0600、临时文件与 replace，未把 DSN 或子进程异常正文加入回执。没有验证实际 Docker daemon 日志保留策略，也没有进行生产凭据泄漏演练。
- 首审验证器检查 DSN 文件为普通文件且 mode=0600，但没有检查文件 owner UID。独立探针成功接受 UID 501 的文件。因此运行手册的“root-owned”不能被描述为已由该实现校验；如这是外部 provisioning 前置，必须明确其外部边界。
- 首审指纹只检查根迁移 100/102。独立审阅期间 root 已加入 103 检查；后续复审以新指纹为准，不将旧检查扩称覆盖 103。

## 测试证据的实际含义

`independent-existing-tests.log` 记录审阅期间执行 `backend/tests/test_hr_agent_migration_deployment.py`：12 passed。该测试文件和实现当时仍在被其他作者修订，这次执行不作为首审旧指纹的冻结全量验收。

测试采用真实一次性 PostgreSQL、真实 migrator/owner membership、真实 OS 信号与超时。Docker CLI 被替换：`start` 在本地 Python 子进程执行迁移，`stop`/`kill` 仅更改状态文件，未实际停止 daemon 管理的容器。因此它证明监督器的分支、命令顺序和数据库权限检查，不能证明真实 Docker 容器生命周期。

另外，首审测试替身名为 `inspect-failure` 的故障分支位于 `stop/kill/rm` 分支内，`inspect` 自身总是正常读取状态文件；该场景实测的是 stop/kill 失败，而非 inspect RPC 失败。真实 daemon 断连、create 回执丢失、GRANT 回执丢失、REVOKE 权限失败等并未由首审测试全部覆盖。

复审结果将在实现修订完成并固定新指纹后追加。首审两项问题已报告实现作者，当前不据 12 个通过测试判为无缺陷。

## 修订后独立复审

复审指纹：`deploy/cloud/hr_agent_migrate.py` SHA-256 **`4747d3452224e155a3c41fd52434bc10903118c7e8c59aa8cb1d4a7c07099b2b`**。复审只读取作者修订并原样重跑独立探针，没有修改实现或放宽断言。

| 场景 | 首审结果 | 复审结果 |
| --- | --- | --- |
| production REVOKE 窗口收到真实 SIGTERM | production、preview 均 start | 仅 production start；没有创建 preview；退出 143、interrupted，membership=0 |
| 启动前已有 owner membership=1 | 拒绝启动，但 cleanup_verified=true | 拒绝启动，cleanup_verified=false，stderr 明确 `CLEANUP_UNRESOLVED external_recovery_required=1`；原 membership 保持为 1 |

证据：`independent-recheck.jsonl`。源码确认每个环境迁移前后检查 `signal_number`；`at_rest_unresolved` 参与清理判定，且只撤销本次记录的授权。首审 P1、P2 在这两个可复现边界上已闭合。

新指纹的根迁移预检包含 **100、102、103**，按各 release 文件 SHA-256 检查 production/preview ledger。检查子目录和 Docker 命令仍限于 HR migration dir；没有因 103 校验而自动运行根迁移。

本轮独立审阅没有再发现需要追加的实现阻塞项。该结论仅覆盖已读代码和上述本地工程复现；前述 root-owned 文件外部前置、实际 Docker daemon/容器、SIGKILL/宿主机丢失、生产会话对账及敏感日志保留边界仍然有效，不升级为生产发布验收。
