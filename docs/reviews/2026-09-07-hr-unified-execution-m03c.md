# HR 统一执行 M03c 本地验收记录

状态：MetaBot `8260853..9fea6df`，12 个精确文件，完整范围规格符合、质量 Approved；Critical/Important/Minor 均为 0。M03a/b/c 本地切片均已复审通过，不等于运行配置已启用、执行器停止已证明或业务已上线。

## 本段实现

- 显式 `CoreChatV5StreamOwner` 消费现有 M02c 的单次 PTY stream，复用 StreamProcessor 的消息/答案判断，不改变模型、工具策略、冻结 prompt 或其他 Bot。未在 index 默认安装 owner，不宣告 v5 就绪。
- 每条安全进度先等待 PG 入账再拉下一条；新增 `appendNext` 与终态使用同一 session→command→intent 锁序，原启动 epoch 与事件内容不随传输凭据变化。进度分片至 16KiB UTF-8；不公开原始 thinking。
- 文字结果精确保留至 128KiB UTF-8；Provider 明确失败、没有有效答案及超限不伪装成功。未知写入/执行异常保持原 claimed 占位，资源 close 不等于停止证明；结果已入账后下游失败不另造 error。
- `CoreChatV5Observation` 从同一不可变事件补写实际 Flywheel API；独立持久游标、领取 UUID、重试时间和隔离原因，外部写入不持有运行库事务。32 次单轮投影、16 轮扫描、并发 4、10 秒领取、5 秒 ACK 上限。
- v5 专用 Writer 连接池限定连接 2 秒、服务端 statement 4 秒、query 4.5 秒，避免调用超时而底层写入无界堆积。原生 Writer/Queue 默认行为保持。
- 观测身份采用版本化、确定性的独立事件/recorder/attempt-turn/run ID，显式关联平台 Turn/run/command/attempt。先写 bootstrap 再投影源事件，适配实际 Trace 外键；不把平台 owner 编造成飞书 open_id。
- 只向观测端传明确允许的关联信息和公开文字，不传原始命令、prompt、凭据或附件 grant。失败尝试与成功尝试的观测 Turn 分开，避免旧空 assistant 占住唯一索引。

## 实际 TDD 与故障证据

首个业务 RED：真实 HTTP 接受 202 后，真实 PG 源事件为 `[]`，预期 `[raw_progress,result]`；接入 owner 与同锁序分配后 GREEN，重复 HTTP 命令不再消费 stream。随后 Provider 错误/空答案/超限的五条行为由无终态变为明确 error；精确 128KiB Unicode、长进度分片、写入背压与失败保留占位回归通过。

观测首个业务 RED：关闭易失队列、Writer 与运行库连接池并重建后，游标仍为 0 而非 1；实现持久重放后，实际选定 Flywheel SQL 与实际 PgFlywheelWriter 恢复了非空 assistant 和关联 Trace。还验证：

- 实际函数权限故障与提交后 ACK 丢失：保留重试时间，重开连接池后收到真实 duplicate；
- 并发领取、迟到 ACK、未知/拒绝 ACK、缺失/错误源身份：不越过新领取、不跳游标、不删除证据；
- 同一 owner 产生的 Result 先被真实 Python Worker 接收，Flywheel 故障不回滚该结果，恢复后补齐观测；
- 实际 PG 锁令旧 Writer 等 6 秒后提交的 RED，改为 v5 Writer 约 4 秒返回不可用的 GREEN；
- 历史失败→成功的合成来源快照生成两条独立观测 Turn/assistant，关联同一平台 Turn。这是 fixture-admin 的投影用历史快照，不是 attemptNo>1 已允许运行；重试执行门仍未放开。

所有数据库均为自有一次性 fixture，Team SQL 只选 001/002/003/004/005/007/012；没有读取真实 Flywheel 凭据或应用生产迁移。测试 fixture 的 schema/pgcrypto 权限调整只服务本次合成数据库，不放宽 HR 运行角色。

## 精确候选复验

- 实施者：18 文件 359 passed，11.00s；主会话在 `9fea6df` 再跑同一范围：**359 passed，11.16s**，23:21:48 开始。
- 相关文件：原 M02/M03a/b 十二文件、原生 flywheel-envelope/flywheel-stream/stream-processor/provider-public-events 四文件，以及新增 stream-owner/observation 两文件。
- 主会话 TypeScript noEmit、11 文件 ESLint、diff-check 均 exit 0；仅三行合成进程证据 stdout，无意外告警。
- 两条既有真实 SIGKILL 证据保留：71506 在 COMMIT 前留下 0 条事件，71547 在 COMMIT 后留下 1 条，均保留占位；实际 Worker 接收子进程为 71514。新增观测重建是连接池 close/reopen，不冒称进程崩溃恢复。
- 主会话在独立目录编译并以实际 Node 导入 owner/observer/outbox/writer 四模块，确认 writeV5，正常退出 0。4.9MB 自有构建目录可恢复地移至 `/Users/neo/.Trash/hr-m03c-native-main.HtXOQu`；现有 dist、源文件和 `.venv` 未动。
- 独立 reviewer 读取完整任务 diff；针对单槽进度捕获额外核对了 StreamProcessor/ProviderPublicEventProjector，确认每次最多一个合格事件、finish 只产生已排除的 thinking。规格符合、质量 Approved，未要求重做其他 Bot。

## 仍然保留的门槛

M04 负责真实停止身份/证明、累计副作用、心跳与释放/重试。M05 负责输入附件/成果；当前新附件命令仍在接受前明确拒绝。P03/P04 负责认证派发、云端业务权威与 Result 集成，O02/O03 负责目标迁移/权限、调度/配置、切换和受控渠道验收。业务材料一年保留与未决事件不删除继续约束后续发布，不能拿 Flywheel 的 90 天 evidence 期限替代。

Platform/Team 源码未改；两份用户草案保留。未访问生产、启动真实模型、发送飞书业务消息、安装依赖、推送或部署。
