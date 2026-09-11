# HR 云端 Loop 部署与切换手册（E1c）

本文提供可审阅的操作路径，不授予执行权限。当前发布窗口、真实候选人模型服务及隐私决定、D7 延迟/成本取舍、独立 HR 飞书去向均未知；浏览器验收也未完成。在这些事项分别取得明确决定和证据前，只能做本地或隔离的公开/虚构材料验证，不能切换真实 HR 受理或发送真实个人材料。

## 1. 不可合并的两个发布

迁移 100 是平台附件擦除热修复的一部分，必须先独立完成。它不能由 HR 发布“顺便”应用。

平台附件热修复顺序固定为：

1. 解析当前附件 Worker 的准确容器和镜像，确认待启动新镜像包含 `SELECT * FROM platform_attachments.claim_attachment_erasure_job_v64(...)` 修复。
2. 获得附件暂停授权后停止旧 `platform-attachments`，用 `docker inspect` 确认它不再运行。
3. 使用 production control migrator 应用根迁移目录，其中包含 100；验证 ledger checksum 和 maintenance 六列最小权限。
4. 只启动并验证修复后的附件 Worker。若第 3 步以后失败，保持附件 Worker 停止；禁止自动恢复旧镜像。数据库迁移不回退。

通用 `remote-stage.sh` 的旧版回滚会恢复 previous release 的附件 Worker，因此没有附加的 fail-closed 防护时不能执行上述热修复。热修复完成回执至少绑定 release SHA、新附件镜像 ID、修复源文件 SHA、迁移 100 checksum、停止/启动时间和验证结果。

只有该回执通过后，HR 发布才能进入迁移阶段。

## 2. 固定 compose 生命周期

HR 的所有 config、stop、start、ps 和 cleanup 操作必须复用同一个数组，不能在不同阶段遗漏 overlay 或 profile：

```bash
hr_compose=(
  /usr/bin/docker compose
  --env-file /opt/orbbec-agent-platform/private/hr-agent/runtime.env
  -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
  -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.hr-agent.yaml
  --profile hr-agent
)
```

运行时快照须为 root 所有、0600、普通文件且非 symlink。API 和 Worker 可分别生成实际容器环境快照供 preflight 读取，但不得包含在发布证据正文；证据只保存 preflight 去敏 JSON。`platform-hr-agent-secrets`、`hr-knowledge` 和 `hr-work` 必须在服务启动前单独 provision，`hr-work` 为运行 uid/gid 所有且 0700。两类 keyring 分别验证 purpose/fingerprint：HR 工作记录 keyring 与附件 content keyring 不互相替代。

附件能力必须显式选择。完整候选人链要求 API 与 Worker 都是 `PLATFORM_CONVERSATION_ATTACHMENT_ENABLED=1` 且 storage/keyring 身份完全一致。公开/虚构无附件隔离模式可显式为 0，但不能称完整 HR ready。

## 3. 只读 preflight

先从 release 中运行，不调用模型或网络。省略数据库参数时只检查本地配置/知识，且结果会明确 `database_not_checked`：

```bash
cd /opt/orbbec-agent-platform/current/backend
python -m tools.hr_agent.preflight \
  --api-env-file /opt/orbbec-agent-platform/private/hr-agent/api-runtime.env \
  --worker-env-file /opt/orbbec-agent-platform/private/hr-agent/worker-runtime.env
```

已获相应数据库只读检查授权时，增加 app 身份 DSN 文件。工具为连接设置只读事务，调用现有 `load_hr_agent_settings`、`KnowledgeReleases` 和 `check_schema_ready`，并读取 096–101 回执及 public 102 cutover gate：

```bash
cd /opt/orbbec-agent-platform/current/backend
python -m tools.hr_agent.preflight \
  --api-env-file /opt/orbbec-agent-platform/private/hr-agent/api-runtime.env \
  --worker-env-file /opt/orbbec-agent-platform/private/hr-agent/worker-runtime.env \
  --database-url-file /opt/orbbec-agent-platform/private/hr-agent/control-database-url
```

输出仅含稳定配置/知识/profile/attachment 指纹、迁移身份、cutover 状态、blockers 和 limitations；不输出 endpoint、路径、DSN、credential 或原始异常。相同的自报指纹只能证明两份输入一致，不能证明生产容器确实加载它们。容器存在/运行也不能证明认领、续租、知识、附件、provider 或业务功能正常，仍需后续 API 与进程 canary。

当前生产装配没有 personal-processing authorizer，preflight 必须保留 `personal_processing_authorizer_absent`；profile 声称“approved”不能解除该阻断。D7 尚无产品确认，必须保留 `d7_product_approval_absent`。不得通过命令行布尔值伪造二者通过。

## 4. 控制库迁移（需单独执行授权）

以下是获准窗口内的具体命令形态。先应用根目录，再显式应用 HR 子目录；两者共享 `platform_control.schema_migrations`。根目录中的 100 必须已由第 1 节独立完成并验证，HR 操作者在此只做 checksum 幂等确认，不能在旧附件 Worker 仍可恢复时首次应用 100。

Production：

```bash
/usr/bin/docker run --rm --read-only --user 10001:10001 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/current/backend/control_migrations:/app/backend/control_migrations:ro \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  -e PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/control-migrator-database-url \
  -e PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner \
  -e PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations \
  PLATFORM_IMAGE_SHA python -m app.control_plane.migrate

/usr/bin/docker run --rm --read-only --user 10001:10001 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/current/backend/control_migrations:/app/backend/control_migrations:ro \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  -e PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/run/control-secrets/control-migrator-database-url \
  -e PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner \
  -e PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations/hr_agent \
  PLATFORM_IMAGE_SHA python -m app.control_plane.migrate
```

Preview 使用独立 preview migrator DSN 和 `PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner_preview`，不得复用 production secret。`PLATFORM_IMAGE_SHA` 必须替换为已核验的不可变镜像引用，不能使用浮动 tag。

public 102 提供 `platform_control.hr_execution_cutover` 单例状态和只读在途计数。迁移不会自动创建单例行；在 operational activation 前必须通过经授权的初始化操作创建 `legacy` gate。preflight 只读 `singleton, phase, epoch, transitioned_at, row_version`，无行或多行均不 ready。初始化和状态切换由 102 的 advisory-lock 管理函数执行；在 102 checksum/API 定稿前不得猜测或手写表数据。

迁移后再次运行带数据库的 preflight。096–101 任一缺失/checksum 不符、102 gate 未初始化或权限不符时保持 HR disabled。

## 5. 隔离启动与验证

迁移和 provisioning 完成后，只能先启动隔离服务，HR 新受理仍为关闭状态：

```bash
"${hr_compose[@]}" up -d --force-recreate platform-api platform-hr-agent-worker
"${hr_compose[@]}" ps platform-api platform-hr-agent-worker
```

随后按接口优先执行：

1. 认证 HTTP 检查 HR readiness；平台 `/api/health` 通过不算 HR ready。
2. 用一次性公开/虚构 work 验证受理、Worker 认领、独立 heartbeat、结果保存、kill/restart 恢复和幂等。
3. 显式附件模式下验证上传/读取/解析；personal-processing authorizer 缺失时，虚构 personal source 的模型请求数必须为 0，并返回受控阻断。
4. 核对 provider 目标和普通日志去敏；不得发送真实候选材料。
5. 最后才做页面上传、恢复、滚动和状态呈现验收。当前该浏览器验收未完成。

存在进程只证明 supervisor 状态，不证明上述功能。每项证据绑定 release/image/config/knowledge 指纹和测试数据身份。

## 6. 切换、排空与回滚

切换前需产品负责人确认具体窗口、用户影响、执行责任人、独立 HR 飞书去向，以及真实候选人模型/保留/日志和 D7 取舍。未确认时 public 102 保持 `legacy`，旧链接单，新链仅隔离测试。

获准后按 102 状态机执行：

1. `legacy -> draining_legacy`：暂停旧 HR 新消息和新简历解析调度；读取旧链与新链非终态计数及准确清单。
2. owner-preserving drain：已受理工作留给原执行者完成或明确取消；旧 Worker 停止领取清单外任务。不得将旧命令改写为新任务，不得同一附件双解析。
3. 旧链非终态为 0 且证据完整后，`draining_legacy -> cloud`，只允许云端 Loop 新受理。
4. 切换后持续核对新链任务、等待、恢复和解析归属；不能把单次 health 当稳定性证明。

回滚不回退数据库、不删除或覆盖成果，也不把同一 work 自动交给旧执行器：

1. `cloud -> draining_cloud`，立即关闭云端新受理并停止新认领。
2. 按 owner/work/source 身份排空或明确取消新链非终态工作，保留已保存成果、确认标准和材料。
3. 只有另行确认旧链仍安全、飞书去向允许且不存在重复归属后，才可 `draining_cloud -> legacy` 恢复旧接单；否则保持停止接单并修复。
4. 附件热修复后的旧附件 Worker始终保持停止。HR 回滚绝不能恢复它，即使当前 release 或通用 deploy 失败。

所有状态切换使用唯一 transition request id，记录前后 epoch/row_version 和只读计数；不得直接 UPDATE 102 表或伪造“零在途”。

## 7. 当前明确未完成

- 发布窗口、停服授权和执行责任人未知。
- 真实候选人模型服务、传输、保留、训练与日志策略未获确认；生产 personal-processing authorizer 不存在。
- D7 的 300 秒/16384 输出候选配置尚未由产品确认延迟和成本，计量校准也未完成。
- public 102 checksum 和最终操作接口需随实现回执核对，不能从本文草案推断已发布。
- 生产/私有配置、实际容器、在途清单和数据库状态未在本地 E1c 中读取。
- 浏览器验收、真实模型专业审读和生产验收均未完成。

