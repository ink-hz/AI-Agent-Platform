# HR 独立 Worker 与本地工程验收

本记录只描述 A1 的本地工程验证和待审阅装配，不表示生产发布或真实模型业务验收。真实候选人处理仍须满足根目录总体架构 §6 的服务与数据处理授权。

## 运行边界

入口为 `python -m app.hr_agent.worker`，不启动 HTTP 服务，不读取旧执行队列，不初始化 Relay。Worker 只认领 `platform_hr_agent.works`，数据库连接使用已有 app DSN 校验，连接超时 3 秒、SQL 超时 10 秒；启动只检查新表与迁移身份，不执行 DDL。

默认租约 60 秒、独立线程每 15 秒续租。SIGTERM/SIGINT 停止新认领和下一步操作；正在等待的模型请求受单请求剩余活动预算及最长 120 秒硬截止约束，当前同步 ModelPort 不保证即时取消在途请求。已经发出的数据不能撤回；迟到响应不能越过输入修订/执行权检查写业务结果，可信用量可以单独结算。

Worker 与 API 都从已校验文件读取同一 HR 提供方、预算、诊断配置与内容密钥。`PLATFORM_CONTROL_DATABASE_URL_FILE` 必须是 app 角色，不能使用管理或迁移角色。配置缺失时阻止工作，错误日志不输出文件内容、DSN 或提供方响应。

附件读取由 `PLATFORM_CONVERSATION_ATTACHMENT_ENABLED` 单独开启，使用附件自己的存储配置和 `PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE`。HR 内容密钥不能替代附件密钥。A1 覆盖 UTF-8；B 增加独立的 PDF/DOCX 解析队列，详见下文。真实候选人材料仍未获本批验收授权。

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

## A1 不可变知识发布格式

`PLATFORM_HR_AGENT_KNOWLEDGE_DIR` 指向一份只读发布目录，必须有 `manifest.json`。例如：

```json
{
  "release_id": "hr-engineering-fixture-1",
  "role": {"path": "role.md", "sha256": "角色文件UTF-8原始字节的64位SHA-256"},
  "resources": [
    {
      "ref": {"kind": "method", "id": "evidence-review", "revision": "r1", "sha256": "正文文件UTF-8原始字节的64位SHA-256"},
      "path": "methods/evidence-review.md",
      "title": "证据审阅",
      "description": "用途、适用边界与需要考虑的限制",
      "objects": []
    }
  ]
}
```

上例摘要为占位说明，不能直接作为有效发布加载。实际目录的全部正文摘要在启动时验证；路径不得为绝对路径、`..` 或符号链接。A1 支持 method/intelligence 的受控公开正文；真正的专业内容和已有情报包适配在 B1/C2 完成，不把测试样例作为正式方法库。

工作输入固定配置修订、发布 ID 与 manifest 摘要。已有工作不会因为目录内容被替换而继续读新内容；部署另一份发布前需保留旧发布并规划旧工作的归属，A1 不实现跨发布自动切换。新工作/新输入只接受进程当前配置的完整有效发布。

## B 增补：解析与保留发布

B 要求独立 `backend/control_migrations/hr_agent/` 的 096、097 均已应用；启动验证 17 张表、所需权限与两份迁移摘要。它不使用 `hr_web/094/095`，不在 Worker 启动时执行 DDL。097 新增加密解析内容和幂等请求表；已有096不改。

从仓库根构建专业发布（输出必须是准备好的本地目录，不是生产路径）：

```sh
PYTHONPATH=backend backend/.venv/bin/python backend/tools/hr_agent/build_knowledge_release.py --output /tmp/hr-knowledge-review
```

把 `PLATFORM_HR_AGENT_KNOWLEDGE_DIR` 指向上述发布根。构建器核验来源摘要，写 `releases/<release_id>/` 后原子更新 `current.json`。API 与 Worker 都挂载同一发布根；新输入发现当前发布，旧输入仍读取原发布；删除原发布会阻塞旧执行，不能自动偷换。保留期与清理需随实际部署制定。兼容 A1 单 manifest 目录。

受控来源在 `backend/hr_agent_knowledge/provenance.json`：七份方法正文、来源台账与已核验案例沿用 Team `7757ca4a2ce380206ff5019917b39de8bef10814`，角色仅适配云端五工具。目录浏览和 Agent Read 引用同一正文；模型是否使用方法由其判断。

PDF/DOCX 通过用户 `POST /api/hr/agent/materials/{attachment_id}/parse` 入队，GET 不启动解析。每轮 Worker 先处理至多一个解析再认领工作。源上限20 MiB、解压32 MiB、最多500页/100万字符、子进程默认20秒、最多3次尝试；失败不冒充全文。没有 OCR；PDF视觉/阅读顺序和DOCX忽略对象均显式报告覆盖边界。Linux 子进程地址空间限制512 MiB，macOS没有等效强制限制；两者仍有输入/输出/时间上限。解析前和提交前均检查当前 HR 权限与原件可用性。

独立试用入口 `/hr/agent`，可带准确 `position`、`work` UUID。模型没有确认标准工具，确认只走真实用户 HTTP。精确成果 `/file` 是已保存 Markdown 的确定性导出，每次下载重查权限/来源。当前没有历史标准专用 HTTP 展示入口；旧基准的删除/替换提案须先请求修订，不能盲选无法展示的原条目。

## B 公开模型验证配置

用户指定沿用 AI-FAE-Agent `.env`：Anthropic Messages 网关模型别名 `claude-opus-4-8`，Bearer 认证。profile 的 `auth_scheme: bearer` 仅由服务器配置；`credential_file` 为0600私有文件，不复制进源码或浏览器。本批仅发送公开 JD 与方法，未授权真实候选人。模型别名不作为官方型号或能力声明。

该网关实测拒绝工具输入 schema 顶层组合关键字。适配器把顶层组合条件移入工具说明供模型阅读，服务端继续使用完整 JSON Schema 校验；未放宽业务条件。错误反馈只返回 schema 字段和条件，不回显用户参数值。

公开实测采用20次调用、60万累计 token、900秒活动时间，输出上限4096；使用UTF-8字节上界估算，输入压缩触发/目标70,000/50,000，配置窗口131,072。它们是验证配置，不是供应商窗口实测或生产成本默认值。真实输出与评审限制见 `docs/reviews/artifacts/2026-09-10-hr-b-public-model/`。

B 未启动云端服务，也未切换现行 HR。Compose 的生产凭据、网络、TLS、对象存储和容量仍需 E 阶段演练与实际授权。

## 2026-09-11 HR 专用 env 与 Opus 5.0

用户指定 HR 改用网关标识 `claude-opus-5`。项目根目录本机 `.env.hr` 设置 `PLATFORM_HR_AGENT_MODEL=claude-opus-5`，并以 `PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE` 指向本机 `.hr-agent/provider.json`；凭据单独保存为0600文件，目录0700，均排除出 Git。模型优先采用非空 HR env 配置，否则使用 provider JSON；API 与 Worker 必须装载同一 env。模型值进入冻结配置摘要，不隐式续跑旧配置工作。

环境文件不会自动启动或启用 HR；本机运行时须由启动器载入（例如 `set -a; source /实际项目路径/.env.hr; set +a`），并另外提供已审阅的数据库、内容密钥、预算、知识与工作目录配置。这里的本机绝对路径不能直接用于云端。Compose 的 HR API/Worker 均显式转发 `PLATFORM_HR_AGENT_MODEL`，云端 provider/凭据仍由专用秘密卷提供，不从本机 env 上传秘密。

此前 B 的 `claude-opus-4-8` 只描述9月10日历史证据；不能将那些结果改标为 Opus 5。新探针见 `docs/reviews/artifacts/2026-09-11-hr-ab-revision/`，响应名称只代表网关自报。尚未用 Opus 5 重跑完整 HR 专业质量旅程。
