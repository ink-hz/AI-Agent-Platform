# HR 同机拆仓迁移记录（2026-09-15）

用户已确认补齐 G1/G2 后迁移。采用现行已完成岗位闭环代码，独立仓库、依赖、API/Worker 和前端构建；同一服务器、原数据库角色与密文、原附件实现，Nginx 直接分流。旧 A–C 代理/权限/建库架构不进入本轮。

## 已完成代码

- HR 前端与静态服务 `e9dc5b8`：base=/hr/，独立 assets，深链接 GET/HEAD 回退，缺失资产与未知 API 404，参数与登录回跳保留。
- HR 测试夹具 `334d284`：原 107 schema/checksum 的无业务数据测试快照，只用于一次性 PostgreSQL；原角色与迁移账本，无 runtime 建库依赖。同提交撤出旧实验运行时迁移/部署代码。
- HR 装配 `a291c4b`：独立 API 与 Worker；真实 account 会话、统一实际注册写路由的硬过期/Origin/CSRF 保护；原 hr-bot/owner 授权 SQL、材料/密文/租约/预算和现有票据接口。两仓固定相同黄金向量并各自配置 CI。
- 平台 `5b7b530a`：删除 DirectAgentWorkspace 五个死 prop，准备未启用的同机 Nginx 模板。
- 平台真实集成测试 `66701c69`；下载修复 `1f35f522`：合法的独立附件 conversation_id=None 可下载，owner/附件 UUID、状态、保留期、版本、摘要及擦除校验不变。

真实接口回归还修正了 HR 资源仓库 dict_row 装配、平台 loopback 票据 POST 的可信 HTTPS/IP 元数据。使用固定本机元数据，不透传客户端伪造转发头。没有新增附件接口。

## 本地验证

| 范围 | 结果和边界 |
|---|---|
| HR 自有 Python 环境完整后端 | 128 passed；其中原业务/数据库核心 91 项覆盖上下文、准确成果/标准、幂等、授权、预算和租约恢复 |
| 实际写路由硬过期保护 | 12 条当前写路由全部 503，另有注册集合覆盖断言；读路径保留、未知接口 404 |
| 两仓黄金向量 | 各 8 passed；同一固定摘要与版本、准确密文/AAD/旧 key_version 读回及篡改拒绝；CI 文件已配置，未宣称远端 CI 已执行 |
| 平台真实同机集成 | 4 passed；平台真实会话/CSRF/SQL 授权，经现有 LoopbackProxy，启动 HR 自有 Python 进程；撤权/登出/跨 owner/幂等与硬过期均验证 |
| 真实附件 HTTP 路径 | 无会话附件真实上传、complete、真实 processor、HR 资源读取与 ticket、平台下载得到相同 bytes，拒绝跨 owner 和过期附件；不伪造 ready 行 |
| 平台既有附件 API | 36 passed，含 7 项允许空 conversation_id、拒绝错误类型与缺少 owner/附件 ID 的边界回归 |
| Worker 实际 CLI | 实际启动、空队列轮询、healthcheck ready/准确 PID、SIGTERM 正常退出、状态清理、退出后探针失败；0 次模型调用 |
| 自有进程故障 | 原核心测试 SIGKILL 仓库方法子进程，真实 PostgreSQL 检查租约隔离与预算保持；此证据不等于新 Worker CLI 在模型执行中的整进程恢复验收 |
| HR 前端 | 自有 npm ci、167 tests 与生产构建通过；6 个静态服务测试，另以实际构建产物核实深链接、参数、JS/CSS/公共资源和 404 |
| 平台前端 | 共享 AgentUse/Marketing 相关 15 tests 与生产构建通过；现行 Nginx 文件未修改 |

模型响应/核心授权边界使用显式本地替身；集成登录 exchange 和对象存储为替身，后者验证大小、摘要和固定不可变版本。真实账号会话、授权、SQL、HTTP、HR 进程与附件 processor 均保留。未进行真实模型质量、真实 S3、浏览器或生产验收。systemd 和 Nginx 模板尚待实际发布环境核验。

## 发布与旧材料

当前完成本地代码迁移和发布材料，尚未安装生产服务、修改线上 Nginx 或启用独立生产 Worker。生产最近记录仍为平台 `570ea625`，本轮未重新探测生产。平台正在服务的 HR 入口及业务代码保留到实际切换后删除，避免迁移准备影响现行发布。

安装遵循 HR `deploy/README.md`，只将现有配置地址/文件路径映射到宿主可达位置；102–105 lane/drain 与 schema 升级继续由平台管理。Worker 的 PrivateTmp 健康检查使用服务挂载命名空间。独立代码/进程仍共用原数据库角色，已接受数据库可达范围相同。

HR 没有配置远端；本轮不创建或猜测远端。旧平台实验工作树保持原状；HR 旧实验四份未提交文件已逐字节校验后完整保留到 `AI-HR-Agent-stopped-experiment` 的 `archive/hr-extraction-stopped` 工作树，并保留具名 Git stash，未合并到当前实现。
