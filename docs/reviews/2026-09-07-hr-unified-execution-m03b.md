# HR 统一执行 M03b 本地验收记录

状态：M03b 本地实施与完整范围独立规格/质量复审通过；两项 Important 和一项 Minor 均已关闭。不代表整个 M03、v5 或业务已可用。

## 候选与范围

- 完整任务范围：MetaBot `672cc9f..8260853`、Platform `137573e..4499eab`，各 6 个文件、各 2 个源代码提交；完整范围包含初始实现与复审修订，不只审最后一个提交。
- 实际 Worker 接收器使用现有 loopback callback 路由、共享 v5 解析器和真实 PG；默认关闭新路由，不增加公网入口。
- 原启动租约、命令/运行/尝试身份与可轮换的传输凭据分离。原始事件不改写，回执游标与请求补送序号分离。
- 接收方原始事件/终态/连续游标同事务；发送方持久保存单次发送领取、退避、下次重试时间和协议隔离。每轮最多 32 次发送、due 扫描 16 轮、并发 4，5 秒网络上限、10 秒单次领取；网络不持有写事务。
- 原已应用 Worker v1 SQL 不改写；新增未应用扩展草稿。生产版本、角色与应用顺序仍由 O02 核对，不从本地测试推定。
- Team 保持 `a6c028a`；两份既有用户草案和 `.venv` 保留。无生产访问、上线、模型调用、飞书发送、依赖安装或推送。

## 实际边界验证

测试启动实际 Python WorkerRuntime/WorkerStore 子进程和 socket 接收器。代理在接收事务提交后丢弃确认，发送端关闭连接池、重新打开后补送；接收端仍只有同一份原始结果。不是内存替代的接收状态机。

覆盖真实 200/409 ACK、精确重复、同序不同内容冲突、缺序 5 补送至终态、本地缺 5 时仅隔离该轮、凭据切换后的迟到回复、并发发送者和有界调度。v4 继续使用 204；v5 不能把 204 当作 JSON 回执，旧强制终态不能删除 v5 证据。

“断网 10 分钟”使用受控 503 网络边界与注入时钟，验证退避计数/时间跨连接池重开保留，以及随后补齐原 cancelled 事件；不是实际等待十分钟或生产故障演练。M03a 已有的两个真实 SIGKILL/事务屏障测试作为回归保留，仍不等于 Claude/飞书进程验收。

## 初始候选主会话复验

- MetaBot `5ac4d0b`：12 文件 **283 passed，7.70s**；TypeScript noEmit、5 文件 ESLint、diff-check 通过。包含 3 行合成进程证据 stdout，另有 2 处未捕获的隔离告警 stderr，不能称为输出完全干净。
- Platform `f0884b0`：6 文件 **323 passed，6.22s**；新增 3 个 Python 文件 Ruff 通过。既有 worker 文件 Ruff 33 项，对照基线 35 项无新增，仅修正两处触及的 import 顺序；不是全仓 lint 通过。
- 编译产物实际 Node 导入 `CoreCallbackDrain`、`sendV5Callback`、`CoreEventOutbox.flushRun` 通过并自然退出 0。验证目录已可恢复地移到 `/Users/neo/.Trash/hr-.m03b-verify.CJHkkD`；没有删除源文件。

## 复审问题与修订验收

独立 reviewer 提出两项 Important：

1. 200 响应头之后，ACK 正文传输途中断线被错误归入永久 `protocol_ack` 隔离。
2. v5 接收器数据库/事务失败落入旧路径的空 409，发送方同样误判为永久协议错误。

主会话核对了两个异常捕获边界；reviewer 的独立 HTTP 探针也复现正文阶段 `TypeError terminated / UND_ERR_SOCKET`。修订要求区分传输读取失败与完整非法回执，并让 v5 存储不可用返回可重试状态；真正的 JSON gap/conflict、无效输入和 v4 语义保持严格。

Minor 为测试中预期隔离告警外溢到 stderr；保留生产告警，测试注入并断言既有隔离钩子，不无条件屏蔽日志。

实际修订与 RED/GREEN：

- ACK 正文测试由真实接收器先提交，代理返回 200 头与部分真实正文后断线。RED 为错误持久隔离 `protocol_ack`；拆分传输读取与完整正文解析后 GREEN。关闭、重开发送端连接池，真实 ACK 依次为 `accepted`、`duplicate`，两端只有原 result，未生成 error。
- 接收器测试在自有临时 PG 安装仅针对合成 run 的 CHECK 故障，真实接收事务回滚。Python 两项 RED 和跨仓 HTTP 一项 RED 均为 409 而非预期 503；新增内部脱敏 `V5ReceiverUnavailable` 与 503 映射后均 GREEN，撤销测试约束后原事件成功恢复。测试证明事务失败/回滚恢复，未单独注入 COMMIT 阶段故障；源码异常边界覆盖事务退出。
- 接收器鉴权失败仍为 401，无效输入仍为 400，真正 gap/conflict 仍为 JSON 409；完整非法 ACK 仍隔离，v4 语义未放宽。预期隔离告警由现有钩子捕获并断言 run/reason 和脱敏，不删除运行告警。

最终主会话在精确修订提交复跑：

- MetaBot `8260853`：12 文件 **285 passed，7.88s**，22:46:15 启动；实际 Worker 子进程 60721，保留 M03a SIGKILL 子进程 60714/60795。仅合成进程证据 stdout，无意外 warning stderr。TypeScript noEmit、5 文件 ESLint、diff-check 均 exit 0。
- Platform `4499eab`：6 文件 **323 passed，4.35s**；新增 3 个 Python 文件 Ruff、diff-check 通过。继承的 worker Ruff 33 项仍按前述基线记录，不声称全仓 lint 通过。
- 本段 Node 编译导入证明针对初始 `5ac4d0b`；最终修订另有 noEmit/lint/实际测试，不将旧编译目录冒充新产物。

同一独立 reviewer 重新读取上述两份完整任务范围与修订证据：规格符合、Task quality **Approved**；Critical/Important/Minor 均为 0。未以测试数量替代以下后续集成门槛。

## 尚未由本段证明

P03 的真实认证派发/云端投影，M03c 的流消费和持久 Flywheel/Trace 重建，M04 的停止核验/释放，M05 附件输入/成果，O02/O03 的迁移与受控双渠道验收仍须完成。原始接收证据不是第二套业务 Result 权威；本地测试不等于已向业务交付。
