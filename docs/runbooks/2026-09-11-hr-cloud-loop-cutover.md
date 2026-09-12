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

供宿主 compose 读取的 runtime.env 须为 root 所有、0600、普通文件且非 symlink。容器内供 preflight 读取的 api-runtime.env / worker-runtime.env 是另外两份快照，须由实际检查用户 uid10001 所有、0600；不能把 root-owned 文件直接拿给 uid10001 的 preflight。API 和 Worker 可分别生成实际容器环境快照供 preflight 读取，但不得包含在发布证据正文；证据只保存 preflight 去敏 JSON。`platform-hr-agent-secrets`、`hr-knowledge` 和 `hr-work` 必须在服务启动前单独 provision，`hr-work` 为运行 uid/gid 所有且 0700。两类 keyring 分别验证 purpose/fingerprint：HR 工作记录 keyring 与附件 content keyring 不互相替代。

附件能力必须显式选择。完整候选人链要求 API 与 Worker 都是 `PLATFORM_CONVERSATION_ATTACHMENT_ENABLED=1` 且 storage/keyring 身份完全一致。公开/虚构无附件隔离模式可显式为 0，但不能称完整 HR ready。

## 3. 只读 preflight

preflight 从待发布镜像内运行；镜像必须包含 `backend/tools/hr_agent/preflight.py`。API/Worker 快照中的路径是容器路径，因此检查容器挂载与真实服务一致的 secrets、knowledge 和 work。快照文件由 provisioning 写入 `platform-hr-agent-secrets`，uid/gid 10001 所有、0600，不经 shell source。公开配置检查使用 `--network none`，不调用模型或网络；省略数据库参数时结果会明确 `database_not_checked`：

```bash
/usr/bin/docker run --rm --read-only --user 10001:10001 --network none \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m,uid=10001,gid=10001,mode=0700 \
  -v orbbec-agent-platform-hr-agent-secrets:/run/hr-agent-secrets:ro \
  -v orbbec-agent-platform-api-secrets:/run/secrets:ro \
  -v /data/orbbec-agent-platform/hr-knowledge:/data/hr-knowledge:ro \
  -v /data/orbbec-agent-platform/hr-work:/data/hr-work:ro \
  PLATFORM_IMAGE_SHA python -m tools.hr_agent.preflight \
  --scope public-only \
  --api-env-file /run/hr-agent-secrets/api-runtime.env \
  --worker-env-file /run/hr-agent-secrets/worker-runtime.env
```

已获相应数据库只读检查授权时，增加 app 身份 DSN 文件。工具为连接设置只读事务，调用现有 `load_hr_agent_settings`、`KnowledgeReleases` 和 `check_schema_ready`，并读取 096–104 回执及 public 102 cutover gate（103修订排空函数，104移除不可达条件并保持实际保护）：

```bash
/usr/bin/docker run --rm --read-only --user 10001:10001 \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m,uid=10001,gid=10001,mode=0700 \
  -v orbbec-agent-platform-hr-agent-secrets:/run/hr-agent-secrets:ro \
  -v orbbec-agent-platform-api-secrets:/run/secrets:ro \
  -v /data/orbbec-agent-platform/hr-knowledge:/data/hr-knowledge:ro \
  -v /data/orbbec-agent-platform/hr-work:/data/hr-work:ro \
  PLATFORM_IMAGE_SHA python -m tools.hr_agent.preflight \
  --scope public-only \
  --api-env-file /run/hr-agent-secrets/api-runtime.env \
  --worker-env-file /run/hr-agent-secrets/worker-runtime.env \
  --database-url-file /run/hr-agent-secrets/control-database-url
```

输出仅含稳定配置/知识/profile/attachment 指纹、迁移身份、cutover 状态、blockers 和 limitations；不输出 endpoint、路径、DSN、credential 或原始异常。相同的自报指纹只能证明两份输入一致，不能证明生产容器确实加载它们。容器存在/运行也不能证明认领、续租、知识、附件、provider 或业务功能正常，仍需后续 API 与进程 canary。

`--scope public-only` 不把个人材料策略混入公开材料配置结论，但仍不授权真实个人材料。`--scope full-candidate` 会保留 `personal_processing_authorizer_absent`；当前生产装配没有该 authorizer，profile 声称“approved”不能解除阻断。D7 尚无产品确认，两种 scope 都保留 `d7_product_approval_absent`。不得通过命令行布尔值伪造二者通过。

## 4. 控制库迁移（需单独执行授权）

以下是获准窗口内的具体命令形态。根迁移由现有 `bootstrap-control-db.sh` 的短时 owner membership 生命周期负责；它必须已经应用并验证 102、103、104。根目录中的 100 必须更早由第 1 节独立附件热修复完成，不能在旧附件 Worker 仍可恢复时首次应用。HR opt-in 096–099/101 只由 `migrate-hr-agent.sh` 应用；它不会运行根迁移目录。

Production：

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/migrate-hr-agent.sh \
  /opt/orbbec-agent-platform/current \
  /opt/orbbec-agent-platform/private \
  PLATFORM_IMAGE_SHA \
  PLATFORM_POSTGRES_CONTAINER_ID
```

`PLATFORM_IMAGE_SHA` 和 `PLATFORM_POSTGRES_CONTAINER_ID` 必须替换为已核验的准确值，镜像不能使用浮动 tag。provisioning须确认两个 migrator DSN 为root-owned mode-0600文件；助手实际检查普通文件和0600，未检查owner UID，这一项仍是外部前置。助手先验证：owner membership at rest 为0且没有migrator会话；production/preview 的根迁移100、102、103、104 checksum与当前release一致。任一条件不满足时，在授予权限前失败，不补跑根迁移。

助手由 `hr_agent_migrate.py` 监督实际具名迁移容器。授予的是**整个控制库 owner 的角色成员身份**，不是 HR 表级权限，也不是会话级权限。production 清理并核实后才授予 preview；不跨两个迁移同时持权。同部署主机的文件锁阻止本助手并发；其他部署工具仍须遵守独占维护窗口。开始前必须核实两个 owner 的所有 membership 为 0、两个 migrator 的数据库会话为 0。

每个环境的迁移等待默认 900 秒，`--migration-timeout` 可明确设为不超过 3600 秒；单个管理命令默认 10 秒，`--command-timeout` 不超过 30 秒，SQL 另有 statement/lock timeout。容器使用不可变镜像、`--read-only`、受限 tmpfs 和该环境唯一的 DSN 文件。INT/TERM/HUP、SQL 失败和超时均进入清理：检查并停止准确容器，必要时 kill，再撤销 membership、核实容器停止且两个 migrator 会话及 membership 均为 0。不能用 Docker CLI 已退出代替容器停止，不能用 REVOKE 代替已 SET ROLE 会话结束。

回执保存于 private 下 `hr-agent-migration-receipts/<run UUID>.json`（目录 0700，文件 0600），在 GRANT 前记录可能的授权与具名容器，包含阶段、环境、容器身份、时间及 `cleanup_verified`，不含 DSN、SQL 输出或异常原文。`cleanup_verified=false` 或 `HR_AGENT_MIGRATIONS_CLEANUP_UNRESOLVED` 是必须交给发布监控处理的失败信号；本轮没有装配生产告警系统。成功回执证明本次监督范围内的清理，不证明 HR 业务上线。

SIGKILL、主机掉电、Docker daemon 长期不可用不能被进程内 trap 保证恢复；上述超时也不是这类故障下的角色 TTL。出现此情况禁止直接重跑或恢复旧发布。值班管理员按最后一份 private 回执：用 `docker inspect <准确 container_id/name>` 确认并停止对应容器；检查 `pg_stat_activity` 中两个 migrator 的会话，核实身份后终止残留会话；撤销准确 owner/migrator membership；复核容器、会话与 membership 三项均为空或已停止，并另存人工恢复回执。恢复操作需要维护窗口的数据库/容器权限，不由助手自动扩大权限。对未确认归属的容器或会话不得猜测后删除。

public 102 提供 `platform_control.hr_execution_cutover` 单例状态、幂等操作记录和只读在途计数。迁移不会自动创建单例行；在 operational activation 前必须通过经授权的 `platform_control.initialize_hr_execution_cutover_v102(uuid)` 创建 `legacy` gate。状态切换只使用 `platform_control.transition_hr_execution_cutover_v102(text,uuid)`；在途计数只使用 `platform_control.hr_execution_cutover_counts_v102()`。三个函数均由 maintenance 身份执行，app 只对 gate 有 SELECT，不能写 gate 或执行管理函数。preflight 读取 gate 的 `singleton, phase, epoch, transitioned_at, row_version` 并核对这些权限；无行、多行或权限偏移均不 ready。不得手写 gate/operations 表。

经单独授权初始化时，maintenance 容器的具体调用形态为：

```bash
/usr/bin/docker run --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  -e HR_CUTOVER_REQUEST_ID=EXPLICIT_UUID PLATFORM_IMAGE_SHA \
  python - <<'PY'
import json
import os
import psycopg
from psycopg.rows import dict_row
from app.local_secrets import read_secret_file

# HR_CUTOVER_INITIALIZE
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as connection:
    connection.execute("SET LOCAL lock_timeout = '2s'")
    connection.execute("SET LOCAL statement_timeout = '30s'")
    receipt = connection.execute(
        "select * from platform_control.initialize_hr_execution_cutover_v102(%s)",
        (os.environ["HR_CUTOVER_REQUEST_ID"],),
    ).fetchone()
print(json.dumps(receipt, default=str))
PY
```

正式窗口先用maintenance身份执行以下准确计数。事务只读且使用一致快照；返回的是阻止切换的占用计数，不是去重用户任务数。超时或错误必须停止步骤，不能填零、复用旧计数或自动进入下一phase。

```bash
/usr/bin/docker run --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  PLATFORM_IMAGE_SHA python - <<'PY'
import json
import psycopg
from app.local_secrets import read_secret_file

# HR_CUTOVER_COUNT
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3) as connection:
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    connection.execute("SET LOCAL lock_timeout = '2s'")
    connection.execute("SET LOCAL statement_timeout = '30s'")
    counts = connection.execute(
        "select * from platform_control.hr_execution_cutover_counts_v102()"
    ).fetchone()
print(json.dumps(dict(zip(("legacy_nonterminal", "cloud_nonterminal"), counts))))
PY
```

进入drain或完成切换时，使用准确目标phase和新的显式request UUID。切换函数会在互斥锁内重新计数，前一次只读盘点不代替该检查。

```bash
/usr/bin/docker run --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  -e HR_CUTOVER_REQUEST_ID=EXPLICIT_UUID \
  -e HR_CUTOVER_TARGET_PHASE=EXPLICIT_PHASE PLATFORM_IMAGE_SHA \
  python - <<'PY'
import json
import os
import psycopg
from psycopg.rows import dict_row
from app.local_secrets import read_secret_file

# HR_CUTOVER_TRANSITION
target = os.environ["HR_CUTOVER_TARGET_PHASE"]
if target not in {"legacy", "draining_legacy", "cloud", "draining_cloud"}:
    raise ValueError("invalid HR cutover target phase")
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as connection:
    connection.execute("SET LOCAL lock_timeout = '2s'")
    connection.execute("SET LOCAL statement_timeout = '30s'")
    receipt = connection.execute(
        "select * from platform_control.transition_hr_execution_cutover_v102(%s,%s)",
        (target, os.environ["HR_CUTOVER_REQUEST_ID"]),
    ).fetchone()
print(json.dumps(receipt, default=str))
PY
```

初始化、计数和切换的数据库锁等待上限均为2秒，单条SQL上限30秒，超时抛错并回滚事务。它们不是Docker/网络的总运行时限；命令成功打印的回执须与变更记录一起留存。`EXPLICIT_UUID`和`EXPLICIT_PHASE`须替换为窗口中的准确值；这些命令不授予执行许可。若响应丢失，不换新request UUID盲重试，应核对原操作回执后用原UUID进行幂等重放。

迁移后再次运行带数据库的 preflight。096–104 任一缺失/checksum 不符、102 gate 未初始化或权限不符时保持 HR disabled。

## 5. 隔离启动与验证

迁移和 provisioning 完成后，可先启动 production 服务但保持 102 gate 为 `legacy`，因此云端新 root admission 会被拒绝：

```bash
"${hr_compose[@]}" up -d --force-recreate platform-api platform-hr-agent-worker
"${hr_compose[@]}" ps platform-api platform-hr-agent-worker
```

预激活阶段只执行不创建工作的检查：

1. 用真实认证用户读取现有 `GET /api/hr/agent/configuration` 和 `GET /api/hr/agent/knowledge?kind=method`。两者成功证明 API 当前能通过身份、HR 权限、schema/config 和知识读取；仓库没有独立 HR health/readiness endpoint，平台 `/api/health` 通过不算 HR ready。
2. 检查 API/Worker 容器使用预期 image、配置/knowledge 指纹，且 Worker 在没有 cloud-lane continuation 时保持空闲。进程存在只证明 supervisor 状态。
3. 不提交 public canary work：`legacy` gate 正确拒绝 cloud root admission。在 production gate 仍为 `legacy` 时强行创建 canary 会绕过切换契约。

若要在正式切换前做完整 public canary，必须另有独立 preview 数据库、compose project、secrets volumes、102 `cloud` gate 和非生产 provider/材料；当前 compose/runbook 没有提供这套隔离拓扑，因此本文不写一个可能误连 production 的 preview 示例。

只有获准完成 §6 的 `draining_legacy -> cloud` 后，才在 production 执行一次性公开/虚构 work canary：

1. 验证受理、Worker 认领、独立 heartbeat、结果保存、kill/restart 恢复和幂等。
2. 显式附件模式下验证上传/读取/解析；full-candidate 仍因 personal-processing authorizer 缺失而阻断，不发送真实或虚构 personal source 到模型。
3. 核对 provider 目标和普通日志去敏；不得发送真实候选材料。
4. 最后才做页面上传、恢复、滚动和状态呈现验收。当前该浏览器验收未完成。

每项证据绑定 release/image/config/knowledge 指纹、102 phase/epoch 和测试数据身份。production canary 失败按 §6 进入 `draining_cloud`，不能无记录地切回 legacy。

## 6. 切换、排空与回滚

切换前需产品负责人确认具体窗口、用户影响、执行责任人、独立 HR 飞书去向，以及真实候选人模型/保留/日志和 D7 取舍。未确认时 public 102 保持 `legacy`，旧链接单，新链仅隔离测试。

获准后按 102 状态机执行：

1. `legacy -> draining_legacy`：暂停旧 HR 新根请求和新简历批次受理；读取旧链与新链非终态计数及准确清单。旧 Worker 继续领取/恢复转换前已经 accepted/queued 的本 lane 工作及其派生解析，直至完成或有明确取消证据。
2. owner-preserving drain：已受理工作留给原执行者完成、认领、恢复或明确取消；只拒绝新的 root admission，不停止已有 continuation 的领取。不得将旧命令改写为新任务，不得同一附件双解析。
3. 按下节停止旧 HR Worker 并核实后，旧链非终态为 0 且证据完整，才执行 `draining_legacy -> cloud`，只允许云端 Loop 新受理。
4. 切换后持续核对新链任务、等待、恢复和解析归属；不能把单次 health 当稳定性证明。

回滚不回退数据库、不删除或覆盖成果，也不把同一 work 自动交给旧执行器：

1. `cloud -> draining_cloud`，立即关闭云端新 root admission；云端 Worker 继续领取/恢复该 lane 已 accepted/queued 的 work 及派生 parse/candidate work。
2. 按 owner/work/source 身份让新链 continuation 完成或明确取消，直到非终态计数为 0；保留已保存成果、确认标准和材料。不得笼统停止认领而把 accepted 工作滞留在队列。
3. 默认保持 `draining_cloud` 修复新链。只有另行批准恢复旧链、按§6.2恢复并核验旧HR执行器、确认飞书去向及无重复归属后，才可 `draining_cloud -> legacy`。数据库phase切回不会重新创建已删除的PM2条目，也不会把停用配置改回启用。
4. 附件热修复后的旧附件 Worker始终保持停止。HR 回滚绝不能恢复它，即使当前 release 或通用 deploy 失败。

所有状态切换使用唯一 transition request id，记录前后 epoch/row_version 和只读计数；不得直接 UPDATE 102 表或伪造“零在途”。


### 6.1 排空记录与旧 HR 执行器停止

103/104不改历史状态。028已强制interrupted具有terminal_at；104从当前生效函数移除不可能命中的NULL分支，真实阻断依靠未确认stop、活跃关联与v5准确lineage。v5 queued 只是命令记录时，只有 job/binding/transport-run/Attempt/Turn 准确关联且 Attempt、Turn 都已终结才从占用中排除；孤立或关联不明仍阻断。任何未确认停止或关联活跃 Turn/Attempt 均阻断，包括 interrupted。旧 interrupted 有 terminal_at、无待确认停止及活跃关联时按技术终态排除，**不代表任务已成功，也不证明远端进程停止**。2026-09-11 11:51 UTC 的聚合盘点仅覆盖v5排除条件的部分关联事实，有3条终结轮次关联的queued v5记录，以及 28 条满足上述聚合技术终态条件的 interrupted；没有修改或重放这些记录。正式窗口仍须运行104生效后的计数函数及原执行器停止核验，不能把聚合结果当作已经切换。

ready和failed旧简历草稿都必须在切换清单中逐项登记处置，而不是只清点仍在执行的解析。计数将二者视为执行终态，不表示已确认、成功或用户不再需要处理；SQL计数不校验这份人工处置清单。

| 草稿与阶段 | 当前可执行操作 | 切换前必须登记的去向 |
| --- | --- | --- |
| failed，尚在legacy | 单份retry，沿用原附件；成功后人工核对 | 要求旧链重试的，必须在进入draining_legacy前完成 |
| ready，draining_legacy | 用户confirm或dismiss；也可保留只读 | 记录选定动作、准确草稿/材料身份与回执，保留只读须明确仍未确认 |
| failed，draining_legacy | 用户dismiss或保留只读；retry作为新受理会被拒绝 | 记录失败原因和用户选择；没有选择就不以计数为零继续切换 |
| ready/failed，cloud或draining_cloud | 原授权范围内只读；旧confirm/dismiss/retry及重放均拒绝 | 保留历史，不自动转为新链待办或再次上传/解析 |

排空过程中刚失败的旧解析也进入上述清单。需要重试但尚未取得处置决定时，暂停后续切换；现行状态机没有 `draining_legacy -> legacy` 直接撤销入口，不能手改phase表或绕过新受理闸门。失败草稿在drain内可放弃，在cloud后仍可读的数据库验证仅证明这些接口可达，不代替用户已选择。任何后续重新处理都须明确授权与归属，不能同一材料双解析。

下列命令只在已批准窗口内执行。先保持 draining_legacy，让已有工作和派生解析完成，再停云端旧 HR direct Worker；保持与 §2 完全相同的 compose 数组，并为该服务显式启用其旧 profile：

```bash
(
set -euo pipefail
legacy_hr_ids=$("${hr_compose[@]}" --profile hr-web ps -q platform-hr-web-worker)
test -n "$legacy_hr_ids"
"${hr_compose[@]}" --profile hr-web stop platform-hr-web-worker
for legacy_hr_id in $legacy_hr_ids; do
  test "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$legacy_hr_id")" = false
done
)
```

私有运行配置必须持久化 `PLATFORM_HR_WEB_WORKER_ENABLED=0`，日常发布及自动回滚清单不得再启动 `platform-hr-web-worker`；只有§6.2显式批准的旧链恢复流程可以改回。核实API实际配置与镜像。没有匹配容器时上面的步骤失败而非推定已经停用，须由服务负责人提供“该环境不存在旧HR direct Worker”的准确进程/服务证据。

已读取本机Team仓库的入库配置与脚本，其中将HR Bot映射为独立PM2条目 `metabot-hr`，并提供单实例控制命令。它仅证明仓库声明的能力，实际生产部署身份未核验（原指纹记录为 `production_identity_verified: false`），不能称内网实际控制能力已验。见[仓库只读审计](../../artifacts/2026-09-11-hr-e-review/final/legacy-worker-stop-audit.md)。实际主机、部署wrapper/checksum、无外部watchdog重建及批准目标absent核实后，才可采用以下退出操作；命令未执行：

```bash
(
set -euo pipefail
OPS_PM2=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
hr_stop_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_CHANGE
test -d "$hr_stop_evidence"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr > "$hr_stop_evidence/hr-before.txt"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-before.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" delete-one metabot-hr
test "$(sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr)" = absent
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-after.json"
cmp "$hr_stop_evidence/others-before.json" "$hr_stop_evidence/others-after.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
)
```

证据目录须在窗口前创建并限制权限；APPROVED_CHANGE 换成准确变更号。只有HR absent、其他实例快照一致才save；任一步失败停止切换并调查，不自动全量恢复。选择delete-one是因为wrapper的restore-one stopped会先重新启动该实例再停止，不适合作为退出命令。禁止replace-all/delete-all；后续fleet发布和自动回滚清单必须排除HR，只有§6.2的显式恢复例外。实际主机和持久状态未在本轮验证，仓库提供操作能力不等于已停止。

Relay 注册仍通常共用于多个 Bot，现有 register_worker CLI 固定非验收 Worker 的完整 agent 列表，不能假设支持只移除 hr-bot。保留共享 Relay 及其他 Bot，依赖活 handoff gate 拒绝其 HR 派发；禁止整体 revoke 来冒充 HR 退出。若实际存在 HR 专用 Worker，维护者才可按已确认 worker_id 使用 `python -m app.execution_relay.register_worker revoke-worker <worker_id> <change_reference>` 注销，并保留维护回执。

活的 `/v5/handoff` 已在每次 offer 前取共享闸门锁；cloud/draining_cloud 不再发 HR 命令，legacy/draining_legacy 允许准确已受理工作的续作。已接受工作回调保留原身份/租约校验，避免因切换丢掉完成回执。确认门控代码已在实际 API 镜像生效；本地签名 HTTP 测试不能替代这一步。Feishu HR 的用户入口、存量会话去向与 HR Bot 停用必须由产品/服务负责人一起确认，仍未知时不能进入 cloud。

### 6.2 仅在明确批准时恢复旧HR执行器

此流程是持久退出的显式例外，不是通用deploy失败后的自动动作。开始前保持 `draining_cloud`，新链在途均完成或明确取消；旧链恢复镜像必须保留D1保守隔离、活handoff闸门及附件热修复，不可简单回退到存在这些缺陷的旧release。核对飞书恢复去向、共享Relay状态、原部署清单及唯一执行归属；缺少任一项时保持draining_cloud修复新链。

1. 从已批准的配置记录恢复旧HR direct Worker所需的 `PLATFORM_HR_WEB_WORKER_ENABLED=1` 和准确镜像引用，保留当前数据库及附件热修复。重新生成并核对API实际配置；不是整份旧env无差别覆盖。使用§2同一compose数组及 `--profile hr-web up -d --force-recreate platform-api platform-hr-web-worker`，随后inspect准确容器镜像/状态并核对实际readiness。此时gate仍为draining_cloud，旧handoff必须拒绝offer。
2. 若实际部署清单包含内网HR Bot，先核实旧ecosystem和wrapper的准确身份。按下面仅恢复 `metabot-hr`；其他Bot快照一致且HR online后才save。若退出前并不存在该执行器，不能因手册列了命令就创建它。

```bash
(
set -euo pipefail
# HR_LEGACY_RESTORE
OPS_PM2=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
ECOSYSTEM=/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs
hr_restore_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_RESTORE
test -d "$hr_restore_evidence"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_restore_evidence/others-before.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" restore-one "$ECOSYSTEM" metabot-hr online
test "$(sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr)" = online
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_restore_evidence/others-after.json"
cmp "$hr_restore_evidence/others-before.json" "$hr_restore_evidence/others-after.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
)
```

3. 按原准确Worker身份核查HR注册、协议readiness及单一执行器配置；共享Relay不整体撤销/重注册。进程online只证明进程状态，不能当作执行能力通过。所有恢复命令失败均停止，不执行phase转换；部分已启动的旧进程仍受draining_cloud闸门约束，逐项记录状态并修复。
4. 确认旧运行配置、实际实例、readiness与无新链在途后，才用§4有时限的transition命令执行 `draining_cloud -> legacy`，保存epoch和操作回执，再按批准范围做一次公开/虚构canary。失败则 `legacy -> draining_legacy` 停新受理并保留原归属，不恢复旧附件Worker或抹掉数据。

此恢复拓扑及实际命令尚未在生产验证；缺少部署指纹和窗口决定时，不因手册已有步骤宣称支持安全回滚。

## 7. 当前明确未完成

- 发布窗口、停服授权和执行责任人未知。
- 真实候选人模型服务、传输、保留、训练与日志策略未获确认；生产 personal-processing authorizer 不存在。
- D7 的 300 秒/16384 输出候选配置尚未由产品确认延迟和成本，计量校准也未完成。
- 100/102/103/104的发布回执、104生效后的实际排空计数、完整v5关联条件及handoff镜像未在生产验收。
- 已做受限只读聚合盘点，并读取本机仓库中metabot-hr单实例退出脚本；实际主机部署身份、持久停止/恢复证据、共享Worker不受影响及独立HR飞书去向仍待验。
- 最新只读观察生产为 fe10fae；发布分支已在96f412f承接该生产代码；发布窗口仍须复查最新生产HEAD，不能覆盖随后上线的能力。
- 浏览器验收、真实模型专业审读和生产验收均未完成。
