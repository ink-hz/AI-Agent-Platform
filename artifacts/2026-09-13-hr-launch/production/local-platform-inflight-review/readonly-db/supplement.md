# 授权后只读数据库补证（不改此前unknown记录）

根明确授权在受控脚本内部读取准确DSN文件，仅用于只读连接；这不是读取/创建用户session。按实际PM2白名单配置定位worker与metabot-hr数据库文件，内容仅进内存，从未输出或保存。连接设置default_transaction_read_only=on、connect_timeout5s、statement_timeout3s、lock_timeout1s。脚本/固定SQL、表字段和聚合分别见read_structure.py、counts.py、structure.json、counts.json。

04:09:37 UTC两侧实际可读平台存储观察：

- execution_worker.local_runs中agent_id=hr-bot：13 completed、3 interrupted，无其他state；该精确集合未送达event_outbox为0。
- hr_runtime.sessions中target_bot=hr-bot且active_command_id非空：0。这是平台core-chat逻辑会话，不是sessions.db中的飞书聊天数。
- 经该平台session归属join得到3条command：全部terminal_kind=completed、有terminal_event_seq、intent_state=ended、executor exited_at存在、recovery stopped_at存在。3条(command_id,run_id,attempt_id)均与worker v5_callback_runs精确匹配，worker已收到并上传terminal。只输出三元组SHA，未输出原ID、payload、callback origin/token或个人文本。
- 三条reconciliation_required都true，明确保留。实际d49adf05 RecoveryLedger.markReconciliationRequired会设true/evidence_complete=false；recordNativeExit只在绑定且准确native identity匹配、无剩余alive ownership后写stop proof/stopped_at，并不清除reconciliation标志。因而true不是仍有活executor的证明，但表示历史效果/恢复证据仍不应说已完整核清；不据此自动重跑旧工作。

这补充支持“当前已明确关联的本地平台HR集合没有非终态、三条v5有native退出/停止记录且terminal已上传”，比仅看云端queued或技术终态更强。它不是对任意未知/孤立记录、v3/v4文件路径、操作系统所有子进程或飞书队列的全量审计，也不替代切换窗口再次核对gate和云端同一任务归属。没有把未分类行虚构为零，没有启动恢复、取消或停止进程。独立飞书渠道仍不在范围。

执行失误披露：第一次脚本命名inspect.py遮蔽Python标准库inspect，导入psycopg失败（exit1），其源字节保留为attempt-1-source.py.txt；并未成功查询业务表。重命名read_structure.py后成功连接与读结构（exit0），counts.py随后成功读聚合（exit0）。初次失败中内部暂记unknown不作业务结论。此前review.md/result.json为获得DSN使用授权之前状态，原字节保留，由本补充给出后续真实结果。
