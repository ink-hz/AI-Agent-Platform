# 本地平台HR在途最小只读核对

结论：已确认实际PM2加载身份；**本地平台HR非终态数仍unknown，不能宣布排空**。独立飞书会话未计数、未读取。没有停机、消息、模型、SQL写入或session/连接凭据读取。

04:06:31 UTC 的sanitized-pm2 jlist在内存捕获后仅输出两个选定进程的白名单字段（pm2-selected.json），原环境、args、令牌、完整jlist均未保存：

| 进程 | PID/状态 | 实际cwd与启动脚本 |
|---|---|---|
| orbbec-agent-execution-worker |4221 / online / restart0| platform-releases/1a705ebb4b6598c61bc0d767ff44e2afc038a539/backend；脚本为platform/backend/.venv/bin/python |
| metabot-hr |66872 / online / restart0| metabot-releases/releases/d49adf0519a415d64a73f04d3b4191a53a4629dd；脚本为该目录dist/index.js，interpreter=node |

启动时间原始毫秒值保留于JSON。PM2 uid字段null，不能据此伪称已读OS启动UID；PM2命令以agentops用户执行。磁盘ecosystem与current symlink不能代替本次实际进程路径。旧scope报告明确的磁盘限制在这里落实为不同加载目录，不改写旧报告。

最初两次从不可遍历的neo worktree执行wrapper返回1，未输出原stderr；第二次只存stderr SHA。改cwd=/tmp、sudo -n -H -u agentops /bin/bash wrapper jlist成功。三个attempt元数据保留，没有绕开wrapper直接读取PM2环境。

结构读取范围：实际worker.py第1513–1514行从PLATFORM_WORKER_DATABASE_URL_FILE初始化WorkerStore；worker_store.py是psycopg持久存储，含明确run_id/agent_id/state/dispatched_at恢复概念。未读取该凭据文件，未假定其他库的同名数据就是当前worker库。

实际MetaBot core-chat-routes.js包含平台core-chat专用入口和v5/v6/v7 acceptance/recovery分流。core-chat-session-store.js的SESSION_STORE_DIR/core-chat/collaboration-v3.jsonl仅保存v3/v4 taskSessionId/runId/targetBot/status，不覆盖v5。v5-routes.js转入V5CommandStore、CoreChatV5Service和RecoveryLedger，并校验签名machine bearer及trusted callback origin；恢复依赖launchLeaseEpoch。不能用v3 JSONL空、sessions.db为空或全部聊天session数替代这些持久接受/launch/recovery状态。

因此本轮在识别两条不同平台持久路径后停止，没有读取聊天正文或扩展大范围数据盘点。准确缺口是：用已授权只读连接确认正在运行的worker库身份及hr-bot run/job非终态；再确认d49adf05实际v5存储位置/schema，以平台contract和准确command/attempt/run关联核对接受、launch、recovery/stop状态，并与云端同一身份对应。未证明关联的行必须unknown，不计入飞书也不擅自忽略。若有launch残留，技术终态不证明进程已止。

`result.json`用null表示unknown，不填0。指纹只覆盖已读源码/wrapper，不复制私有配置。没有执行未核准SQL，故没有虚构SQL收据。当前切换不能以此报告声称旧本地平台执行已排空；也不能从unknown倒推出必须关闭独立飞书渠道。
