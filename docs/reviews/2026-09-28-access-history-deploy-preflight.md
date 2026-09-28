# 2026-09-28 访问记录发布预检中断

首轮发布候选 `e446ff923d0235c4e6dea439c51f72e209ef1374` 在 `remote-stage.sh` 的行政服务进程不变性捕获处前向停止。预检要求六个行政 unit 全部运行，但生产上 `ai-admin-dingtalk-bot.service` 和 `ai-admin-job-worker.service` 自 2026-09-07 起为已加载、正常停止；其余四个运行。此检查发生在镜像构建、数据库迁移和版本切换之前。

中断后的只读核对：Platform `current` 仍为 `2cecab5ef06f034858bacd2a3781f3f63214aefb`；本机及远端无发布/验收进程，staging 无残留目录；`/api/health` 返回 200；FAE 容器仍在运行，Brain 两项开关仍为 0。两个保护锁保留了本次候选及 token，时间约为 09:41 CST。未启动已停止的行政 unit，也未切换其他应用。

修复仅把行政 unit 的初态捕获扩为 loaded + Result=success + active/有效 PID 或 inactive/PID 0；继续逐字比较切换前后的完整状态、PID 和活动时间。failed、unloaded 或状态变化仍阻止发布。确认无进行中的操作、线上指针未变和健康检查通过后，按已记录的 owner 标识释放本次 stale lock，再用新提交重跑官方发布。
