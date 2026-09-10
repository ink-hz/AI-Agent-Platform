# HR A1 独立 Worker 与本地工程验收

本记录只描述 A1 的本地工程验证和待审阅装配，不表示生产发布或真实模型业务验收。真实候选人处理仍须满足根目录总体架构 §6 的服务与数据处理授权。

## 运行边界

入口为 `python -m app.hr_agent.worker`，不启动 HTTP 服务，不读取旧执行队列，不初始化 Relay。Worker 只认领 `platform_hr_agent.works`，数据库连接使用已有 app DSN 校验，连接超时 3 秒、SQL 超时 10 秒；启动只检查新表与迁移身份，不执行 DDL。

默认租约 60 秒、独立线程每 15 秒续租。SIGTERM/SIGINT 停止新认领和下一步操作；正在等待的模型请求受单请求剩余活动预算及最长 120 秒硬截止约束，当前同步 ModelPort 不保证即时取消在途请求。已经发出的数据不能撤回；迟到响应不能越过输入修订/执行权检查写业务结果，可信用量可以单独结算。

Worker 与 API 都从已校验文件读取同一 HR 提供方、预算、诊断配置与内容密钥。`PLATFORM_CONTROL_DATABASE_URL_FILE` 必须是 app 角色，不能使用管理或迁移角色。配置缺失时阻止工作，错误日志不输出文件内容、DSN 或提供方响应。

附件读取由 `PLATFORM_CONVERSATION_ATTACHMENT_ENABLED` 单独开启，使用附件自己的存储配置和 `PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE`。HR 内容密钥不能替代附件密钥。当前只覆盖已授权 UTF-8 正文，其他格式和生产材料边界见后续批次。

## Compose 待审阅装配

基础 `deploy/cloud/compose.yaml` 的 HR Worker 位于默认关闭的 `hr-agent` profile。新增 `compose.hr-agent.yaml` 只在显式传入时为 API 加载 HR 配置、知识目录和工作目录；不会更改其他 Agent 使用的 API Relay 配置。

只检查合并配置（不启动服务）：

```sh
docker compose -f deploy/cloud/compose.yaml -f deploy/cloud/compose.hr-agent.yaml --profile hr-agent config --quiet
```

实际启用前需单独完成部署授权、迁移、秘密卷与目录准备。HR 专用秘密卷名为 `orbbec-agent-platform-hr-agent-secrets`，其中包含四个 `hr-*-profile.json` / `hr-content-keyring.json` 文件、app DSN 及模型凭据；具体文件名见 Compose。共享提供方 profile 的 `credential_file` 必须使用两进程均可见的 `/run/hr-agent-secrets/` 路径，凭据文件权限为 0600。

知识目录按固定 manifest 发布并只读挂载到 `/data/hr-knowledge`。工作目录 `/data/hr-work` 必须归 UID/GID 10001 所有，权限 0700，并符合部署的私有存储与保留策略。配置文件使用两进程相同的容器路径，以保持配置修订身份一致。Worker 附件开关为 `PLATFORM_HR_AGENT_ATTACHMENTS_ENABLED`，默认 0；开启前补齐专用秘密卷中的附件凭据与内容密钥。

本文件不提供或执行生产启动命令。基础 profile 与覆盖文件属于可审阅的部署配置，不表示服务、凭据、网络或生产存储已经验证。

## 工程验证范围

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_runtime.py tests/test_hr_agent_worker_process.py -q
```

测试使用一次性 PostgreSQL、本地 HTTP SSE 提供方和真实子进程；模型语义由脚本替代，不调用真实模型。完整装配用真实测试目录身份和 HR 授权、正式上下文/工具及固定公开知识 manifest，验证发现、读取、保存、回答。故障测试覆盖 prepared、sending、模型已提交、成果回执已提交、写事务未提交、回答/摘要投影、取消、新输入、收尾与追加预算。

普通日志只包含允许的身份、状态、用量、耗时和错误码。测试故障观察器仅在测试模块内注入，没有生产环境变量、浏览器参数或模型工具入口。

增长上下文测试使用明确标识的 UTF-8 字节上界估算与缺失 usage 的保守扣记，不称真实 tokenizer 或长任务成本校准。完整工具闭环另使用显式 24000/20000 输入触发/目标测试配置；调用数、累计 token 和活动时间总额不因此放宽。真实模型 profile 的 tokenizer、窗口和产品预算仍需校准并批准。
