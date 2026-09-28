# 2026-09-28 访问记录发布预检中断

首轮发布候选 `e446ff923d0235c4e6dea439c51f72e209ef1374` 在 `remote-stage.sh` 的行政服务进程不变性捕获处前向停止。预检要求六个行政 unit 全部运行，但生产上 `ai-admin-dingtalk-bot.service` 和 `ai-admin-job-worker.service` 自 2026-09-07 起为已加载、正常停止；其余四个运行。此检查发生在镜像构建、数据库迁移和版本切换之前。

中断后的只读核对：Platform `current` 仍为 `2cecab5ef06f034858bacd2a3781f3f63214aefb`；本机及远端无发布/验收进程，staging 无残留目录；`/api/health` 返回 200；FAE 容器仍在运行，Brain 两项开关仍为 0。两个保护锁保留了本次候选及 token，时间约为 09:41 CST。未启动已停止的行政 unit，也未切换其他应用。

修复仅把行政 unit 的初态捕获扩为 loaded + Result=success + active/有效 PID 或 inactive/PID 0；继续逐字比较切换前后的完整状态、PID 和活动时间。failed、unloaded 或状态变化仍阻止发布。确认无进行中的操作、线上指针未变和健康检查通过后，按已记录的 owner 标识释放本次 stale lock，再用新提交重跑官方发布。

## 第二轮发布与恢复（09:52–10:25 CST）

候选 `96f861e7` 通过行政 unit 检查后，在控制库 bootstrap 的 job_kind 预检中失败：现有 4 条 `worker_direct_v5` 记录被旧 042 分类规则误判。只读核对生产库仍停在迁移 109，迁移 110 未执行；四条记录属于既有类别，未修改业务数据。失败前脚本已停止旧控制库消费者，自动回滚使用了基础 Compose，未继承原 API 的 HR 覆盖配置，导致 API 因知识路径已配置但 HR 运行时未启用而循环重启。

人工核对旧版镜像、`platform.env`、HR 配置与代际挂载后，仅用旧版基础 Compose + `compose.hr-agent.yaml` + 既有 HR generation 覆盖配置重建 API；保持 `PLATFORM_HR_WEB_WORKER_ENABLED=0`。API、loopback 与 attachment 均恢复 healthy，公开 `/api/health` 返回 200。附件恢复前确认迁移版本仍是 109。脑服务的四个心跳中三个 healthy，旧候选解析项 degraded；当前 HR 切换阶段为 cloud。曾尝试最小 SELECT 授权以验证错误来源，未恢复健康，随即撤回并核对权限回到原值。此处不宣称完整生产健康或业务验收通过。

`deploy-input.lock` 与 `agent-brain-action.lock` 保留第二轮 owner 文件；远端无仍运行的发布进程。未再次发布、未切换 `current`（仍为 `2cecab5ef06f034858bacd2a3781f3f63214aefb`）。源码新增切换前 HR 覆盖配置保护门禁，避免同类环境下再次停服务；它不替代正式的 HR 配置继承、现代 job_kind 预检和脑服务修复。完成这些发布链路修复与验证前，不重试 Platform 上线。
