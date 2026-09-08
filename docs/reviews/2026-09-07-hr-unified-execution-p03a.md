# P03a 执行归属、旧路径隔离与租约续期

状态：`df57888` 已完成控制器重新验证，完整范围独立规格/质量复审均 Approved；P03a 本地切片完成，完整 P03 尚未完成。

## 范围和前置裁决

Platform 基线 `ea6dee7`；MetaBot `dafe139`、Team `a6c028a` 保持只读。M04a 已完成本地复审，本任务不重做停止证明或模型执行器。

M04b 的认证恢复查询需要真正的 Platform Attempt/Relay Worker 归属绑定，现有 M03 接收器内部注册接口不能代替它。因此先完成 P03 基础，再接认证传输；不会另建一套恢复派发器。

本片段只包含：

- Turn 在受理时固定执行归属和路由代次，现有 Turn 永久保留 legacy 来源；当前会话路由用于新请求，Attempt 仍是实际执行状态的权威。
- HR 新轮次与 queued Attempt 原子创建，任何一步失败整体回滚。
- legacy 领取、运行创建和 API Mission 投影排除新 worker 轮次，同时允许旧在途 Mission 按原归属收尾。
- 基于真实数据库时间和当前归属的租约续期；旧进程、过期或不匹配代次不能续租。

## 必须保留的兼容边界

只新增未编号草案 `backend/control_migrations/pending/hr_direct_dispatch.sql`，不修改已编号迁移。未安装该草案时保留现有 legacy 行为；worker 新受理不能缺少归属字段而悄悄回落旧执行器。新测试明确安装 P02/P03 草案，P02-only 测试不冒充已安装 P03 的验收。

元数据触发器仅固定并保护来源字段，不创建 Attempt 或执行任务。已有受理/幂等重传锁顺序是 Conversation→Turn，因此新多行续租流程采用 Conversation→Turn→Attempt，不能反向加锁引入死锁。仅针对小型 Turn 身份/状态行做缺列兼容读取，不对消息或上下文做整行 JSON 展开。

## 验证状态

精确范围为 `ea6dee7..df57888`，6 个源代码/测试文件。逐项 RED→GREEN 报告在忽略目录 `.superpowers/sdd/hr-p03-report.md`，任务说明为 `.superpowers/sdd/hr-p03-brief.md`。原先只改变会话路由的 P02 测试数据已替换为“实际新建 HR 空会话→设置新路由→提交新轮次”，没有把旧 legacy Turn 冒称为 worker Turn。

实施者最终运行 155 项测试通过，其中 P03a 新增 29 项。控制器在精确提交上补跑已有网页/V2 对话接口，结果 **187 项通过，25.46 秒**；Python 编译、新测试与 `turn_attempts.py` 的 Ruff 通过。

输出并非全部无告警：补跑的两个未修改 API 测试文件分别产生 44、20 条 Starlette per-request cookies 弃用告警；不导入应用代码的最小 TestClient 探针也复现同一告警。新增任务测试无此告警。三个变更 legacy 文件仍有 16 条既有 Ruff 诊断，控制器通过基线 `git show` 和当前代码核对，规则及消息完全相同，没有扩大为无关清理，也不宣称全局 lint 清零。

真实数据库/编排验证包含：

- 原角色被拒绝插入 Attempt 时，Message、Mission、Turn、事件等整个受理事务回滚。
- 并发重复提交只创建一个 Turn 和一个 Attempt。
- 通过 `pg_blocking_pids` 确认真实重复提交持有 Conversation 锁时，续租遵循兼容顺序等待，两者均能结束。
- 租约在实际等待锁期间过期，拿到锁后按数据库当前时间拒绝续期；调用方事务回滚也撤销续租。
- 真实旧 MissionOrchestrator 在路由切换前启动，切换后仍用同一个 run 完成原答案。测试只替代网络执行边界，不把历史 fixture 行冒充 v5 Result 集成。

这些是数据库/线程和实际旧编排证据，不是 API/Worker 进程终止验收。没有运行模型或调用真实渠道。

## 独立裁决

审查者 `p03a_ownership_review` 只读完整六文件差异，规格符合、质量 Approved；Critical、Important、Minor 均无，没有重复运行整套测试或修改工作树。确认新受理原子性、固定来源、旧路径读写隔离及 Conversation→Turn→Attempt 锁序与需求一致。

审查提出的跨任务项全部保留：P03 后续派发/能力/序号/取消/调度，P04 Result 投影，M04b 认证核对与统一许可，以及 O02/O03 和真实进程/渠道验收。不能把 legacy 变更被拒绝当作 worker 取消功能已经实现。

## 后续而非已完成

加密命令/序号绑定、实际签名 v5 派发、能力探测、独立调度、M04b 认证恢复和 P04 Result 投影仍需后续集成。原请求可能仍在传输时，本地查询不到命令不能作为释放或重跑依据。未启用任何生产功能，未访问生产、调用真实模型或发送飞书业务消息。
