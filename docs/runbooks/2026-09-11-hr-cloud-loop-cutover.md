# HR 云端 Loop 部署与切换手册（E1c）

本文提供可审阅的操作路径，执行权限来自当前任务。2026-09-13 用户明确要求“直接做到上线”、排除飞书，并授权从 HR 历史 session 提取问题做真实场景验证；该授权覆盖必要的 HR 与附件维护暂停。执行时仍须记录具体时间、准确镜像/配置、原执行器身份及共享 API 影响。真实候选人模型服务及保留/训练处理规则尚未完整确认，当前可装配的发布策略仅允许公开/已审阅合成场景，真实个人材料授权回调保持关闭。D7 采用单独受保护的发布评审文件绑定当前 provider/budget/diagnostic 内容，不能靠修改 profile 自称获准。用户随后明确允许绕过前端直接走接口：本次生产验收走真实HTTP，保留身份认证、授权、CSRF、幂等与持久化路径；浏览器不作为上线前置，页面效果仍未验。生产和真实场景验收分别记录实际结果；此处不预先宣布上线通过。

## 1. 不可合并的两个发布

迁移 100 是平台附件擦除热修复的一部分，必须先独立完成。它不能由 HR 发布“顺便”应用。

平台附件热修复顺序固定为：

1. 解析当前附件 Worker 的准确容器和镜像，确认待启动新镜像包含 `SELECT * FROM platform_attachments.claim_attachment_erasure_job_v64(...)` 修复。
2. 获得附件暂停授权后停止旧 `platform-attachments`，用 `docker inspect` 确认它不再运行。
3. 使用 production control migrator 应用根迁移目录，其中包含 100；验证 ledger checksum 和 maintenance 六列最小权限。
4. 只启动并验证修复后的附件 Worker。若第 3 步以后失败，保持附件 Worker 停止；禁止自动恢复旧镜像。数据库迁移不回退。

通用 `remote-stage.sh` 的旧版回滚会恢复 previous release 的附件 Worker，因此没有附加的 fail-closed 防护时不能执行上述热修复。热修复完成回执至少绑定 release SHA、新附件镜像 ID、修复源文件 SHA、迁移 100 checksum、停止/启动时间和验证结果。

执行者须先把下面APPROVED占位符替换为经批准的不可变release、容器、镜像ID与SHA；新source SHA来自独立审读的erasure.py，不能临时把错误镜像自身的SHA当批准值。证据目录须新建、0700，并在同一维护窗口内排除其他部署器；无此独占条件不执行。下面只完成停旧、100/六列权限与新镜像身份核验；启动后真实擦除canary另有业务验证回执，不能仅凭Running认定热修复完成。失败清理只尝试停止准确容器；若Docker不可达或stop失败，不能保证停止，保持后续发布关闭并要求独立inspect核验，不产生成功回执。

```bash
(
set -euo pipefail
umask 077
# HR_ATTACHMENT_HOTFIX
export attachment_release=/opt/orbbec-agent-platform/releases/APPROVED_RELEASE
export attachment_release_sha=APPROVED_RELEASE_SHA
attachment_private=/opt/orbbec-agent-platform/private
export attachment_evidence=/opt/orbbec-agent-platform/release-evidence/APPROVED_ATTACHMENT_CHANGE
export attachment_image=APPROVED_NEW_IMAGE_ID
export attachment_old_image=APPROVED_OLD_IMAGE_ID
export attachment_old_id=APPROVED_OLD_CONTAINER_ID
export attachment_source_sha=APPROVED_SOURCE_SHA256
export attachment_migration_sha=APPROVED_MIGRATION_100_SHA256
export attachment_runbook_sha=APPROVED_RUNBOOK_SHA256
attachment_postgres=APPROVED_POSTGRES_CONTAINER_ID
test -d "$attachment_evidence"
export PLATFORM_IMAGE="$attachment_image"
attachment_compose=(/usr/bin/docker compose --env-file "$attachment_private/platform.env" -f "$attachment_release/deploy/cloud/compose.yaml")
# Verify the exact approved text and release inputs before stopping anything.
python3 - <<'PY'
import hashlib, os, pathlib, re
assert re.fullmatch('[0-9a-f]{40}', os.environ['attachment_release_sha'])
root = pathlib.Path(os.environ['attachment_evidence'])
assert not any((root / name).exists() for name in ('compose-before.json', 'attachment-hotfix-receipt.json', 'attachment-stop-identity.json'))
release = pathlib.Path(os.environ['attachment_release'])
for path, expected in (
    (release / 'docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md', os.environ['attachment_runbook_sha']),
    (release / 'backend/control_migrations/100_attachment_erasure_worker_access.sql', os.environ['attachment_migration_sha']),
):
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
assert os.environ['attachment_image'].startswith('sha256:') and len(os.environ['attachment_image']) == 71
PY
test "$(/usr/bin/docker image inspect --format '{{.Id}}' "$attachment_image")" = "$attachment_image"
"${attachment_compose[@]}" config --format json | python3 -c '
import json, os, pathlib, sys
image = json.load(sys.stdin)["services"]["platform-attachments"]["image"]
assert image == os.environ["attachment_image"]
with (pathlib.Path(os.environ["attachment_evidence"]) / "compose-before.json").open("x") as stream:
    json.dump({"service": "platform-attachments", "image": image}, stream)
'
test "$(/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm --read-only --user 10001:10001 --network none "$attachment_image" python -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("/app/backend/app/attachments/erasure.py").read_bytes()).hexdigest())')" = "$attachment_source_sha"
test "$(/usr/bin/docker inspect --format '{{.Image}}' "$attachment_old_id")" = "$attachment_old_image"
test "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$attachment_old_id")" = true
# Explicit stop plus restart-policy removal prevent daemon restart of the old image.
/usr/bin/docker update --restart=no "$attachment_old_id"
/usr/bin/docker stop "$attachment_old_id"
test "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$attachment_old_id")" = false
python3 - <<'PY'
import datetime, json, os, pathlib
with (pathlib.Path(os.environ['attachment_evidence']) / 'attachment-stop-identity.json').open('x') as stream:
    json.dump({'oldContainerId': os.environ['attachment_old_id'], 'oldImageId': os.environ['attachment_old_image'],
               'stoppedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}, stream)
PY
# After this boundary every failure keeps attachment workers stopped.
attachment_done=0
attachment_cleanup() {
  status=$?
  if test "$attachment_done" != 1; then
    /usr/bin/docker stop "$attachment_old_id" >/dev/null 2>&1 || true
    for candidate in $("${attachment_compose[@]}" ps -q platform-attachments); do
      /usr/bin/docker stop "$candidate" >/dev/null 2>&1 || true
    done
    echo ATTACHMENT_HOTFIX_FAILED_KEEP_STOPPED >&2
  fi
  exit "$status"
}
trap attachment_cleanup EXIT
/bin/bash "$attachment_release/deploy/cloud/bootstrap-control-db.sh" "$attachment_release" "$attachment_private" "$attachment_image" "$attachment_postgres"
# Verify100 and each of its six column privileges before starting any worker.
attachment_ledger=$(/usr/bin/docker exec "$attachment_postgres" psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d agent_platform_control -c "select sha256 || '|' || (has_column_privilege('platform_control_maintenance','platform_attachments.uploads','attachment_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.uploads','write_attempt_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','attachment_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','attempt_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','object_ref_ciphertext','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','object_ref_key_version','SELECT'))::text from platform_control.schema_migrations where version=100")
test "$attachment_ledger" = "$attachment_migration_sha|true"
"${attachment_compose[@]}" up -d --no-deps --force-recreate platform-attachments
export attachment_new_id
attachment_new_id=$("${attachment_compose[@]}" ps -q platform-attachments)
test -n "$attachment_new_id"
test "$attachment_new_id" != "$attachment_old_id"
test "$(/usr/bin/docker inspect --format '{{.Image}}' "$attachment_new_id")" = "$attachment_image"
test "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$attachment_new_id")" = true
python3 - <<'PY'
import datetime, json, os, pathlib
receipt = {key: os.environ[value] for key, value in {
    'runbookSha256': 'attachment_runbook_sha', 'releasePath': 'attachment_release', 'releaseSha': 'attachment_release_sha',
    'imageId': 'attachment_image', 'oldImageId': 'attachment_old_image',
    'oldContainerId': 'attachment_old_id', 'newContainerId': 'attachment_new_id',
    'sourceSha256': 'attachment_source_sha', 'migration100Sha256': 'attachment_migration_sha',
}.items()}
receipt['stoppedAt'] = json.loads((pathlib.Path(os.environ['attachment_evidence']) / 'attachment-stop-identity.json').read_text())['stoppedAt']
receipt.update(block='HR_ATTACHMENT_HOTFIX', checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               limitation='Identity/order verification only; real erasure canary is separately required')
with (pathlib.Path(os.environ['attachment_evidence']) / 'attachment-hotfix-receipt.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
PY
attachment_done=1
)
```

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
/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm --read-only --user 10001:10001 --network none \
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

已获相应数据库只读检查授权时，增加 app 身份 DSN 文件。工具为连接设置只读事务，调用现有 `load_hr_agent_settings`、`KnowledgeReleases` 和 `check_schema_ready`，并读取 096–105 回执及 public 102 cutover gate（103修订排空函数，104移除不可达条件并保持实际保护）：

```bash
/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm --read-only --user 10001:10001 \
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

`--scope public-only` 不把个人材料策略混入公开材料配置结论，但仍不授权真实个人材料。`--scope full-candidate` 会保留 `personal_processing_authorizer_absent`；当前生产装配没有该 authorizer，profile 声称“approved”不能解除阻断。D7 只从配置绑定的受保护 `PLATFORM_HR_AGENT_RELEASE_POLICY_FILE` 读取评审依据；文件缺失或两进程配置不一致时保留 `d7_product_approval_absent`。文件仅接受 public-only，不赋予个人处理权限。不得通过命令行布尔值伪造二者通过。

## 4. 控制库迁移（需单独执行授权）

以下是获准窗口内的具体命令形态。根迁移由现有 `bootstrap-control-db.sh` 的短时 owner membership 生命周期负责；它必须已经应用并验证 102、103、104、105。根目录中的 100 必须更早由第 1 节独立附件热修复完成，不能在旧附件 Worker 仍可恢复时首次应用。HR opt-in 096–099/101 只由 `migrate-hr-agent.sh` 应用；它不会运行根迁移目录。

Production：

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/migrate-hr-agent.sh \
  /opt/orbbec-agent-platform/current \
  /opt/orbbec-agent-platform/private \
  PLATFORM_IMAGE_SHA \
  PLATFORM_POSTGRES_CONTAINER_ID
```

`PLATFORM_IMAGE_SHA` 和 `PLATFORM_POSTGRES_CONTAINER_ID` 必须替换为已核验的准确值，镜像不能使用浮动 tag。provisioning须确认两个 migrator DSN 为root-owned mode-0600文件；助手实际检查普通文件和0600，未检查owner UID，这一项仍是外部前置。助手先验证：owner membership at rest 为0且没有migrator会话；production/preview 的根迁移100、102、103、104、105 checksum与当前release一致。任一条件不满足时，在授予权限前失败，不补跑根迁移。

助手由 `hr_agent_migrate.py` 监督实际具名迁移容器。授予的是**整个控制库 owner 的角色成员身份**，不是 HR 表级权限，也不是会话级权限。production 清理并核实后才授予 preview；不跨两个迁移同时持权。同部署主机的文件锁阻止本助手并发；其他部署工具仍须遵守独占维护窗口。开始前必须核实两个 owner 的所有 membership 为 0、两个 migrator 的数据库会话为 0。

每个环境的迁移等待默认 900 秒，`--migration-timeout` 可明确设为不超过 3600 秒；单个管理命令默认 10 秒，`--command-timeout` 不超过 30 秒，SQL 另有 statement/lock timeout。容器使用不可变镜像、`--read-only`、`--cap-drop ALL`、`--security-opt no-new-privileges:true`、受限 tmpfs 和该环境唯一的 DSN 文件。INT/TERM/HUP/QUIT、SQL 失败和超时均进入清理：检查并停止准确容器，必要时 kill，再撤销 membership、核实容器停止且两个 migrator 会话及 membership 均为 0。不能用 Docker CLI 已退出代替容器停止，不能用 REVOKE 代替已 SET ROLE 会话结束。

回执保存于 private 下 `hr-agent-migration-receipts/<run UUID>.json`（目录 0700，文件 0600），在 GRANT 前记录可能的授权与具名容器，包含阶段、环境、容器身份、时间及 `cleanup_verified`，不含 DSN、SQL 输出或异常原文。`cleanup_verified=false` 或 `HR_AGENT_MIGRATIONS_CLEANUP_UNRESOLVED` 是必须交给发布监控处理的失败信号；本轮没有装配生产告警系统。成功回执证明本次监督范围内的清理，不证明 HR 业务上线。

SIGKILL、主机掉电、Docker daemon 长期不可用不能被进程内 trap 保证恢复；上述超时也不是这类故障下的角色 TTL。出现此情况禁止直接重跑或恢复旧发布。值班管理员按最后一份 private 回执：用 `docker inspect <准确 container_id/name>` 确认并停止对应容器；检查 `pg_stat_activity` 中两个 migrator 的会话，核实身份后终止残留会话；撤销准确 owner/migrator membership；复核容器、会话与 membership 三项均为空或已停止，并另存人工恢复回执。恢复操作需要维护窗口的数据库/容器权限，不由助手自动扩大权限。对未确认归属的容器或会话不得猜测后删除。

public 102 提供 `platform_control.hr_execution_cutover` 单例状态、幂等操作记录和只读在途计数。迁移不会自动创建单例行；在 operational activation 前必须通过经授权的 `platform_control.initialize_hr_execution_cutover_v102(uuid)` 创建 `legacy` gate。状态切换只使用 `platform_control.transition_hr_execution_cutover_v102(text,uuid)`；在途计数只使用 `platform_control.hr_execution_cutover_counts_v102()`。三个函数均由 maintenance 身份执行，app 只对 gate 有 SELECT，不能写 gate 或执行管理函数。preflight 读取 gate 的 `singleton, phase, epoch, transitioned_at, row_version` 并核对这些权限；无行、多行或权限偏移均不 ready。不得手写 gate/operations 表。

经单独授权初始化时，maintenance 容器的具体调用形态为：

```bash
/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm -i --read-only --user 0:0 \
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
with psycopg.connect(dsn, connect_timeout=3, autocommit=True, row_factory=dict_row) as connection:
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '3s'")
        receipt = connection.execute(
            "select * from platform_control.initialize_hr_execution_cutover_v102(%s)",
            (os.environ["HR_CUTOVER_REQUEST_ID"],),
        ).fetchone()
print(json.dumps(receipt, default=str))
PY
```

正式窗口先用maintenance身份执行以下准确计数。事务只读且使用一致快照；返回的是阻止切换的占用计数，不是去重用户任务数。超时或错误必须停止步骤，不能填零、复用旧计数或自动进入下一phase。

```bash
/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  PLATFORM_IMAGE_SHA python - <<'PY'
import json
import psycopg
from app.local_secrets import read_secret_file

# HR_CUTOVER_COUNT
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as connection:
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '3s'")
        counts = connection.execute(
            "select * from platform_control.hr_execution_cutover_counts_v102()"
        ).fetchone()
print(json.dumps(dict(zip(("legacy_nonterminal", "cloud_nonterminal"), counts))))
PY
```

进入drain或完成切换时，使用准确目标phase和新的显式request UUID。当前命令只允许draining_legacy、cloud和draining_cloud，legacy恢复目标按§6.2延期，不在可执行入口中开放。切换函数会在互斥锁内重新计数，前一次只读盘点不代替该检查。

```bash
/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm -i --read-only --user 0:0 \
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
if target not in {"draining_legacy", "cloud", "draining_cloud"}:
    raise ValueError("invalid HR cutover target phase")
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, autocommit=True, row_factory=dict_row) as connection:
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '3s'")
        receipt = connection.execute(
            "select * from platform_control.transition_hr_execution_cutover_v102(%s,%s)",
            (target, os.environ["HR_CUTOVER_REQUEST_ID"]),
        ).fetchone()
print(json.dumps(receipt, default=str))
PY
```

初始化、计数和切换的数据库锁等待上限均为2秒，单条SQL上限3秒，显著低于应用追加事务的10秒statement_timeout。连接显式使用transaction()，即使autocommit开启SET LOCAL也在该事务内生效；超时抛错并回滚事务，释放切换排他锁。该界限控制数据库执行，不保证网络断连、主机挂起或Docker调用的墙钟上限；命令成功打印的回执须与变更记录一起留存。`EXPLICIT_UUID`和`EXPLICIT_PHASE`须替换为窗口中的准确值；这些命令不授予执行许可。若响应丢失，不换新request UUID盲重试，应核对原操作回执后用原UUID进行幂等重放。

上一轮operations-final-1属于历史中间运行，仅覆盖当时的30秒命令和旧恢复假设；不能套用到本版3秒持锁上限与恢复延期路线。新的运维回执必须绑定本版准确runbook SHA，并分别列出已执行和未覆盖的fenced块。

迁移后再次运行带数据库的 preflight。096–105 任一缺失/checksum 不符、102 gate 未初始化或权限不符时保持 HR disabled。

## 5. 隔离启动与验证

迁移和 provisioning 完成后，可先启动 production 服务但保持 102 gate 为 `legacy`，因此云端新 root admission 会被拒绝：

```bash
"${hr_compose[@]}" up -d --no-deps --force-recreate platform-api platform-hr-agent-worker
"${hr_compose[@]}" ps platform-api platform-hr-agent-worker
```

预激活阶段只执行不创建工作的检查：

1. 先以真实 platform owner 身份读取并留存 `GET /api/v1/manage/hr-readiness`：HTTP 200、ready 为 true，检查实时数据库迁移/权限、知识与已装配配置的身份；此时 phase=legacy，new_admission_enabled 为 false。该接口受 owner 授权和审计保护，不使用公共 health 代替。随后执行准确 Worker 容器内 `python -m app.hr_agent.worker healthcheck`，要求私有 Unix socket 返回常驻进程的 PID/start/nonce、实时依赖及持续工作进度；compose healthcheck 使用同一命令。API 与 Worker 的 release_sha、configuration_sha256、knowledge 身份必须匹配，另外按 preflight 比对附件装配。再由真实认证且有 HR 权限的用户读取 `GET /api/hr/agent/configuration` 和知识目录，验证实际身份边界。共享 `/api/health` 仍仅表示公共 liveness。
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

本轮执行授权与范围见本文开头。实际切换时记录窗口时间和执行者，按当前公开/合成范围运行，飞书不作前置；真实候选人处理在规则未配置时仍关闭。发布评审文件须与实际 API/Worker 配置一致，不能用 public-only 预检证明完整候选人工作可用。

获准后按 102 状态机执行：

1. `legacy -> draining_legacy`：暂停旧 HR 新根请求和新简历批次受理；读取旧链与新链非终态计数及准确清单。旧 Worker 继续领取/恢复转换前已经 accepted/queued 的本 lane 工作及其派生解析，直至完成或有明确取消证据。
2. owner-preserving drain：已受理工作留给原执行者完成、认领、恢复或明确取消；只拒绝新的 root admission，不停止已有 continuation 的领取。不得将旧命令改写为新任务，不得同一附件双解析。
3. 按下节停止旧 HR Worker 并核实后，旧链非终态为 0 且证据完整，才执行 `draining_legacy -> cloud`，只允许云端 Loop 新受理。
4. 切换后持续核对新链任务、等待、恢复和解析归属；不能把单次 health 当稳定性证明。

回滚不回退数据库、不删除或覆盖成果，也不把同一 work 自动交给旧执行器：

1. `cloud -> draining_cloud`，立即关闭云端新 root admission；云端 Worker 继续领取/恢复该 lane 已 accepted/queued 的 work 及派生 parse/candidate work。
2. 按 owner/work/source 身份让新链 continuation 完成或明确取消，直到非终态计数为 0；保留已保存成果、确认标准和材料。不得笼统停止认领而把 accepted 工作滞留在队列。
3. 在 `draining_cloud` 修复新链并验证配置、知识、实时依赖及已有 work 归属后，使用上节相同的 3 秒事务切换命令，以新 request UUID 指定 `cloud`。新增 105 只增加 `draining_cloud -> cloud` 同链恢复，允许原云端工作仍在执行，继续要求旧链占用为零；不改 work、租约或历史成果。并发同 UUID 只产生一份回执，其他目标重放拒绝。102/103/104 原字节保留；旧执行器恢复仍按§6.2延期，不得先切legacy再用draining_legacy伪装为可逆回滚。
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

私有运行配置必须持久化 `PLATFORM_HR_WEB_WORKER_ENABLED=0`，日常发布及自动回滚清单不得再启动 `platform-hr-web-worker`；当前流程不改回；另行恢复设计按§6.2延期。核实API实际配置与镜像。没有匹配容器时上面的步骤失败而非推定已经停用，须由服务负责人提供“该环境不存在旧HR direct Worker”的准确进程/服务证据。

已读取本机Team仓库的入库配置与脚本，其中将HR Bot映射为独立PM2条目 `metabot-hr`，并提供单实例控制命令。它仅证明仓库声明的能力，实际生产部署身份未核验（原指纹记录为 `production_identity_verified: false`），不能称内网实际控制能力已验。见[仓库只读审计](../../artifacts/2026-09-11-hr-e-review/final/legacy-worker-stop-audit.md)。实际主机、部署wrapper/checksum、无外部watchdog重建及批准目标absent核实后，才可采用以下退出操作；命令未执行：

```bash
(
set -euo pipefail
umask 077
# HR_LEGACY_STOP
export OPS_PM2=/Users/agentops/AgentRuntime/deploy-tools/reliability/sanitized-pm2.sh
export ECOSYSTEM=/Users/agentops/AgentRuntime/metabot/ecosystem.config.cjs
export hr_stop_evidence=/Users/agentops/AgentRuntime/release-evidence/APPROVED_CHANGE
test -d "$hr_stop_evidence"
# approved-hr-stop.json and the exact approved runbook.md must already exist.
python3 - <<'PY'
import hashlib, json, os, pathlib, socket
root = pathlib.Path(os.environ['hr_stop_evidence'])
approved = json.loads((root / 'approved-hr-stop.json').read_text())
assert not any((root / name).exists() for name in ('hr-before.txt', 'others-before.json', 'hr-before-identity.json', 'wrapper-before.sh', 'ecosystem-before.cjs', 'pm2-stop-receipt.json'))
assert approved['hostname'] == socket.gethostname()
assert approved['expectedState'] in {'online', 'stopped'}
for path, key in ((pathlib.Path(os.environ['OPS_PM2']), 'wrapperSha256'),
                  (pathlib.Path(os.environ['ECOSYSTEM']), 'ecosystemSha256'),
                  (root / 'runbook.md', 'runbookSha256')):
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == approved[key]
PY
sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr > "$hr_stop_evidence/hr-before.txt"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-before.json"
python3 - <<'PY'
import json, os, pathlib
root = pathlib.Path(os.environ['hr_stop_evidence'])
identity = json.loads((root / 'approved-hr-stop.json').read_text())
identity['beforeState'] = (root / 'hr-before.txt').read_text().strip()
assert identity['beforeState'] == identity['expectedState']
identity['otherInstances'] = json.loads((root / 'others-before.json').read_text())
for variable, name in (('OPS_PM2', 'wrapper-before.sh'), ('ECOSYSTEM', 'ecosystem-before.cjs')):
    with (root / name).open('xb') as stream:
        stream.write(pathlib.Path(os.environ[variable]).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
# Persist exit prerequisites before delete, without overwriting earlier evidence.
with (root / 'hr-before-identity.json').open('x') as stream:
    json.dump(identity, stream, indent=2)
    stream.flush()
    os.fsync(stream.fileno())
PY
sudo -n -H -u agentops /bin/bash "$OPS_PM2" delete-one metabot-hr
test "$(sudo -n -H -u agentops /bin/bash "$OPS_PM2" state-one metabot-hr)" = absent
sudo -n -H -u agentops /bin/bash "$OPS_PM2" snapshot-except metabot-hr > "$hr_stop_evidence/others-after.json"
cmp "$hr_stop_evidence/others-before.json" "$hr_stop_evidence/others-after.json"
sudo -n -H -u agentops /bin/bash "$OPS_PM2" save
python3 - <<'PY'
import datetime, json, os, pathlib
root = pathlib.Path(os.environ['hr_stop_evidence'])
receipt = json.loads((root / 'hr-before-identity.json').read_text())
receipt.update(block='HR_LEGACY_STOP', afterState='absent', saved=True,
               checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
with (root / 'pm2-stop-receipt.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
PY
)
```

证据目录须在窗口前新建、0700并禁止复用；APPROVED_CHANGE换成准确变更号。approved-hr-stop.json须持久保存并经批准，包含hostname、wrapperSha256、ecosystemSha256、expectedState（online或stopped）及runbookSha256；runbook.md须为该批准SHA对应的准确正文副本。代码会逐项读取校验后持久保存hr-before-identity.json、wrapper-before.sh和ecosystem-before.cjs，再执行delete；任一记录缺失、身份不符或退出前状态不符都不删除。只有HR absent、其他实例快照一致才save；任一步失败停止切换并调查，不自动全量恢复。选择delete-one是因为wrapper的restore-one stopped会先重新启动该实例再停止，不适合作为退出命令。禁止replace-all/delete-all；后续fleet发布和自动回滚清单必须排除HR，当前流程没有恢复例外，另行恢复设计按§6.2延期。实际主机和持久状态未在本轮验证，仓库提供操作能力不等于已停止。

Relay 注册仍通常共用于多个 Bot，现有 register_worker CLI 固定非验收 Worker 的完整 agent 列表，不能假设支持只移除 hr-bot。保留共享 Relay 及其他 Bot，依赖活 handoff gate 拒绝其 HR 派发；禁止整体 revoke 来冒充 HR 退出。若实际存在 HR 专用 Worker，维护者才可按已确认 worker_id 使用 `python -m app.execution_relay.register_worker revoke-worker <worker_id> <change_reference>` 注销，并保留维护回执。

活的 `/v5/handoff` 已在每次 offer 前取共享闸门锁；cloud/draining_cloud 不再发 HR 命令，legacy/draining_legacy 允许准确已受理工作的续作。已接受工作回调保留原身份/租约校验，避免因切换丢掉完成回执。确认门控代码已在实际 API 镜像生效；本地签名 HTTP 测试不能替代这一步。本轮用户明确排除飞书，飞书去向不作为 cloud 切换条件；共享 Relay 及其他 Bot 仍必须保留。

### 6.2 旧HR执行器恢复延期

本轮按§6在draining_cloud修复后经105恢复同一cloud链，不提供恢复PM2、启用旧Worker或切回legacy的执行步骤。状态机仍没有draining_legacy直接撤销入口；旧链恢复后再次失败时原路线会落入不能原地撤销的阶段，因此不能把它称为安全恢复。

旧链恢复必须另行设计并验证：读取§6.1持久保存的退出前主机、wrapper、ecosystem和实例状态，核验批准镜像、配置、实际readiness、飞书去向及唯一执行归属；明确恢复中途失败和恢复后canary失败的可达处理终点。缺失任何退出前记录不得猜测或创建原本不存在的PM2实例。仅批准启动旧进程不足以批准状态机回切，不以读取不到的hr-before文件作为恢复依据。

后续方案若需重建platform-api，必须单独批准共享API短时中断及其他Bot影响，并验证非HR受理连续性；现行draining_cloud修复路线没有为恢复旧Worker重启共享API的步骤。§5初次部署platform-api的force-recreate同样可能短时影响所有共享API用户，须纳入原发布窗口影响确认。

## 7. 上线记录状态

2026-09-13 实施中；以下未完成项不得由本地测试数量代替。

- 当前任务已明确授权上线及必要维护暂停，飞书排除。执行时间、操作者、部署独占锁及共享 API 短时重建影响须保存在实际回执。
- 真实候选人模型服务及保留/训练处理规则尚未完整确认；生产 personal-processing authorizer 仍关闭。当前发布策略只允许公开/已审阅合成范围。
- D7 采用 300 秒/16384 输出和有限预算；评审文件必须绑定实际装载配置。token 仍保守估算，校准及实际账单不能由该文件证明。
- 100/102/103/104/105 的发布回执、实际准确排空计数、原执行器持久停止及 handoff 镜像尚待本次生产操作留证。
- 已只读核实本机 agentops 的 metabot-hr 在线及实际状态目录；停止动作尚未执行。共享 Worker 不受影响须另取真实快照。
- 本轮已承接生产 65e7fbd 和 origin/master，准确合并及路由修复审读见 2026-09-13 artifacts。发布前仍复查生产当前镜像，不能覆盖并行部署。
- 历史意图语料已审读，运行材料与故障前提正在补齐；浏览器、真实模型专业审读和生产验收以各自新运行回执为准。
