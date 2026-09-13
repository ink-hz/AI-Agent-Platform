# HR 云端与附件生产发布：最小影响执行计划审读

本文是 2026-09-13 的只读生产审查结果。除已授权且由根任务执行的首轮构建外，本审查没有停止服务、迁移数据库、创建生产配置、切换容器或调用模型。首轮 `ac8c275` 构建在 Web TypeScript 编译时失败，远端退出码为 1；两把活动锁、私有输入、staging、release 和镜像标签均已清理，没有容器引用该镜像。详见 `stage-build-cleanup-inspection-6826d064.json`。

## 固定现状与发布边界

- 当前 `/opt/orbbec-agent-platform/current` 指向 `65e7fbd14a3cbe99883b0b31e31b5705d183d1f9`。
- API 容器为 `3347e61ede7d3ab0bd96c0cdc25cff1d6808c89ab2b8a31f24953bebccd4fcf7`，镜像 ID `sha256:b16eccfad79350c324c1ba229f847eaf693e31d7cc5567115b81f1d7eeaf4a35`，健康。
- 附件 Worker 容器为 `908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819`，镜像 ID `sha256:ee24975e38f8ef51cac4e6036aa92c42e46d508c91615773eeba181ee38eb1b1`，健康。
- 旧 HR Web Worker 容器为 `ce6d1b1f1593a756c3f58cb11f1529c16af29a504721521b76e1cd118351d767`，镜像 ID `sha256:a8c0ded298d9c677d6cbd143e761cce276ab0f37a9efd188db64f947e23cf609`，运行中。
- PostgreSQL 容器为 `12ef0c782a5e458b9e52dbe40ffaa3aa877e07269ddea52051e93d258ebdbb0e`，健康。
- production ledger 是 1–95，共 95 条；preview 是 1–87，共 87 条。两库均没有 HR Agent schema、迁移 100 或 cutover 表。
- `/opt/orbbec-agent-platform/private/platform.env`、production/preview migrator DSN、production app/maintenance DSN 均为 root 所有普通文件、0600；内容没有被输出。
- `platform-hr-agent-secrets` volume、`/data/orbbec-agent-platform/hr-work`、`/data/orbbec-agent-platform/hr-knowledge/current.json` 和 `/opt/orbbec-agent-platform/private/hr-agent` 尚不存在。`/data/orbbec-agent-platform/hr-knowledge` 根目录已经存在并挂到旧 API；不能把 `current.json` 不存在误写成目录不存在。
- 当前公开情报是完整 Bundle v2 `aebca08a-1715-536a-869a-39663b81c8e4`，生产绝对路径 `/data/orbbec-agent-platform/hr-intelligence/bundles/aebca08a-1715-536a-869a-39663b81c8e4`，manifest SHA256 为 `d227bbb7f4ccb9ecf865bf259c06b17fee54ebd17b5fca2356eb6e3b34b0cad5`。数据库 current 指针和磁盘 manifest 一致。
- 可复核的本地完整副本为 `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-intelligence-experience/.superpowers/sdd/hr-topic-consumption/bundles/aebca08a-1715-536a-869a-39663b81c8e4`。现行 `verify_import_bundle()` 已对整个副本验证通过。manifest 的 `agent_document_index` 有 86 项，其中 85 项是 Markdown；构建器必须接收完整 Bundle，不能先摘出 85 个 Markdown 后伪装成已验证 Bundle。
- `/opt/orbbec-agent-platform/private/hr-p0-acceptance.json` 当前不存在。因此没有可复用的正式 owner session；不得读取浏览器 profile、临时 mint session 或伪造 owner。

完整容器、挂载、网络、环境变量名、非秘密开关、磁盘和私有路径元数据见 `production-inspection-receipt.json`。最终应用源码固定为 `ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7`、tree `a7489e2a43d09ee8727748488abdcbf64f45b9e9`；不可变镜像 ID 为 `sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59`，准确源文件与独立 migration helper 指纹见 `source-fingerprints-ce6c0f3.json`。首轮 `source-fingerprints-ac8c275.json` 只解释失败构建，不能冒充最终镜像源码。

## 不使用通用 deploy.sh

根 `deploy.sh` 要求干净 master，调用 `remote-stage.sh`，会迁移并重建多项共享服务。它的旧回滚还可能恢复迁移 100 之前的附件 Worker。此次只允许定点更新附件、API、HR Agent Worker 和最后停止旧 HR Worker，因此不能调用它。

所有生产写操作必须处于同一独占维护窗口，并持有：

1. `/opt/orbbec-agent-platform/private/agent-brain-action.lock`，owner 文件为本窗口 UUID、root 0600；释放时必须逐项验证目录、owner 文件类型/权限和准确 token 后才移动和删除。
2. `/opt/orbbec-agent-platform/private/deploy-input.lock`，用最终 release 内、哈希已固定的 `deploy-input-lock.py` 按 `acquire -> validate -> release` 管理准确 40 位 release 和 32 位 deployment ID。

首轮 stage/build 已证明这套清理路径能在编译失败时收敛。最终修复提交必须使用新的 `stage_build_v2.py` 和新的 run directory；首轮脚本及回执保持冻结。

## 1. 最终源码 staging 和单镜像构建

v2 脚本已固定完整 40 位 commit、tree、`deploy-input-lock.py` SHA256，并完成以下静态检查：

- `git archive` 只读固定 commit，不收 worktree/untracked/private 文件；最终 commit 的 tracked symlink 清单必须为空。
- 生成并远端复核 `MANIFEST.sha256`、源码 tar SHA256、Dockerfile SHA256；tar 只允许目录和普通文件。
- 远端私有输入固定在 `/opt/orbbec-agent-platform/private/stage-build-inputs/<release>-<deployment>/source.tar.gz`。
- 构建命令只允许 `docker build --pull --build-arg RELEASE_SHA=<release> -t orbbec-agent-platform:<release> -f <stage>/source/deploy/cloud/Dockerfile <stage>/source`。
- 成功时只产生不可变 release 目录和镜像；不切 `current`，不迁移，不 stop/up/restart。
- `start --execute` 返回本地 run directory 后，使用 `poll --run-dir <path> --wait` 读取远端原子 `exit_code` 并收回 stdout、stderr 和 result receipt。

进入下一阶段前，从成功 receipt 取值，不使用浮动 tag：

```bash
export FINAL_RELEASE_SHA=ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7
export RELEASE=/opt/orbbec-agent-platform/releases/$FINAL_RELEASE_SHA
export NEW_IMAGE_ID=sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59
export POSTGRES_ID=12ef0c782a5e458b9e52dbe40ffaa3aa877e07269ddea52051e93d258ebdbb0e
export PRIVATE=/opt/orbbec-agent-platform/private
export PLATFORM_ENV=$PRIVATE/platform.env
test "$NEW_IMAGE_ID" = "$(docker image inspect --format '{{.Id}}' "$NEW_IMAGE_ID")"
test "$FINAL_RELEASE_SHA" = "$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$NEW_IMAGE_ID" | sed -n 's/^PLATFORM_RELEASE_SHA=//p')"
test -f "$RELEASE/MANIFEST.sha256" && test ! -L "$RELEASE/MANIFEST.sha256"
```

v2 构建已成功并返回 `services_changed=false`；本地 run directory 为 `stage-build-v2-runs/ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7-970660b48a72071ecfe61456b56ecf47`。后续只接受上述 digest，不使用浮动 tag 或首轮失败构建。

## 2. 迁移前 production 私有备份与 schema 指纹

迁移只改 production，因此备份也只读取 production，不能以“对称”为由额外 dump preview。执行 `backup_control.py` 前再次核验 PostgreSQL 准确容器 ID；脚本在任何生产锁动作前写入原子、fsynced 的 running receipt，然后持有两把部署锁。备份目录 `/data/orbbec-agent-platform/private-backups/hr-cloud-<deployment>` 必须 root:root 0700；dump、globals、schema、ledger 和 receipt 均 0600，不进入 Git。

脚本只生成：

- `production.dump`：`agent_platform_control` custom-format dump；
- `globals.sql`：包含角色状态的私有 globals 备份；
- `production-schema.sql`：无 owner/privilege 的 schema-only 指纹源；
- `production-ledger.json`：001–095 的 version/checksum；
- `receipt.json`：只记录 SHA256、字节数、起止时间与清理状态。

每个数据库客户端带唯一 `application_name=hr_backup_<deployment>`、3 秒 lock timeout、120 秒 statement timeout 与 180 秒客户端上限；客户端超时或信号中断时只终止并核验该 deployment 自有的只读 session。`pg_restore --list` 只验证 archive 目录可读，不执行 restore。执行命令必须使用已独立审读的 `backup_control.py` SHA256 `47a26f1c313dc869d6c3a3501023c30c8ff9729f034281fb358ec79ba492941c`：

```bash
test 47a26f1c313dc869d6c3a3501023c30c8ff9729f034281fb358ec79ba492941c = "$(sha256sum "$PRIVATE/hr-launch-maintenance/backup_control.py" | cut -d' ' -f1)"
/usr/bin/python3 -I "$PRIVATE/hr-launch-maintenance/backup_control.py"
```

成功条件同时包括 001–095 ledger 准确匹配、4 个制品非空、custom archive list 可读、两个锁准确释放、`backup_sessions_zero=true` 与 `cleanup_verified=true`。公开回执只复制文件 SHA256/大小/时间和 schema/ledger SHA，不复制 dump、globals、schema SQL、DSN 或 credential。

数据库迁移成功后不自动 restore。恢复 dump 会覆盖同期其他 Bot 的合法写入，只能在另一个明确停写和恢复决策中执行。

## 3. 附件 fail-closed 边界与 production-only 根迁移

停止前再次核验准确旧容器、镜像和最终镜像内修复源：

```bash
export OLD_ATTACHMENT_ID=908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819
export OLD_ATTACHMENT_IMAGE=sha256:ee24975e38f8ef51cac4e6036aa92c42e46d508c91615773eeba181ee38eb1b1
test "$OLD_ATTACHMENT_IMAGE" = "$(docker inspect --format '{{.Image}}' "$OLD_ATTACHMENT_ID")"
test true = "$(docker inspect --format '{{.State.Running}}' "$OLD_ATTACHMENT_ID")"
test e4862777c8e256fc3a37c6fe73cb8ad401329b660cbf69c05194768f60bd93be = "$(
  docker run --rm --read-only --network none --cap-drop ALL --security-opt no-new-privileges:true \
    --user 10001:10001 "$NEW_IMAGE_ID" python -c \
    'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("/app/backend/app/attachments/erasure.py").read_bytes()).hexdigest())'
)"
docker update --restart=no "$OLD_ATTACHMENT_ID"
docker stop "$OLD_ATTACHMENT_ID"
test false = "$(docker inspect --format '{{.State.Running}}' "$OLD_ATTACHMENT_ID")"
```

从这一步起，任何失败都保持所有 `platform-attachments` 候选容器停止；绝不恢复旧镜像。

根 `bootstrap-control-db.sh` **不用于此次迁移**。其 477–478 行会同时授予 production 与 preview owner，499 行迁移容器没有具名身份、墙钟超时、read-only rootfs、cap-drop 或授权前持久回执；EXIT 路径只做 revoke，不验证迁移会话归零。它还包含 credential/bootstrap 初始化，与当前已完整的数据库、角色和凭据状态不相称。旧稿把它称为“完整监督路径”并声称会把 preview 的 88–95 一并迁移是错误的：89–95 位于 `hr_web` 子目录，根目录 runner 不会应用它们。

本次只使用提交 `404862b93c57e6782016182f089b67766ab569ff` 中经过审读的维护 helper。它不在 `ce6c0f3` 应用镜像里，必须从准确 Git 对象把下面两个文件装入新的 root:root 0700 维护目录，文件为 root:root 0600，绝不从 moving worktree 复制：

- `hr_agent_migrate.py` SHA256 `381dbfbcf89bdc286dc1544982705f656ba9b1a0f2ba4442769f7498a56455b2`；
- `preflight-execution-job-kind.sh` SHA256 `88644e92079949a49e695474ab0c5d15e77cc238d13116e76db880f14d2e3b2e`。

早期 `source-fingerprints-ce6c0f3.json` 的 companion 记录少了末尾 `e`，只有 63 位；该历史快照保留不改，不能作为发布输入。上述 64 位值已直接对提交 `404862b93c57e6782016182f089b67766ab569ff` 的 blob 重算确认，后续安装和执行只接受该值。

安装后以受保护维护目录的准确绝对路径执行，并保存其 private receipt：

```bash
export MIGRATION_HELPER_COMMIT=404862b93c57e6782016182f089b67766ab569ff
export MIGRATION_HELPER_DIR="$PRIVATE/hr-migration-helper-$MIGRATION_HELPER_COMMIT"
test 381dbfbcf89bdc286dc1544982705f656ba9b1a0f2ba4442769f7498a56455b2 = "$(sha256sum "$MIGRATION_HELPER_DIR/hr_agent_migrate.py" | cut -d' ' -f1)"
test 88644e92079949a49e695474ab0c5d15e77cc238d13116e76db880f14d2e3b2e = "$(sha256sum "$MIGRATION_HELPER_DIR/preflight-execution-job-kind.sh" | cut -d' ' -f1)"
/usr/bin/python3 "$MIGRATION_HELPER_DIR/hr_agent_migrate.py" \
  "$RELEASE" "$PRIVATE" "$NEW_IMAGE_ID" "$POSTGRES_ID" \
  --migration-set root --environment production \
  --receipt-dir "$PRIVATE/hr-agent-migration-receipts"
```

该 supervisor 先要求 production ledger 的 001–095 每项 checksum 与固定 release 完全相同，再用 companion 的 `--baseline95` 检查 job-kind 已 classified；之后只为 production 创建具名、read-only、cap-drop 的迁移容器，在 GRANT 前持久化 receipt，按墙钟超时 stop/kill，撤销准确 membership，并确认 migrator session 与 membership 都归零。生产只新增根迁移 100、102、103、104、105，结果为 100 条 ledger（001–095 加这 5 条）；HR 子迁移 096–099/101 此时尚未应用。preview 保持原 001–087、87 条，完全不变。

随后验证 production 的 100 checksum 为 `15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177`，且 maintenance 对六列的 SELECT privilege 全为 true。再用正确的现存 env 文件定点启动附件；`docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md` 第1节曾写成不存在的 `$PRIVATE/runtime.env`，不能照抄。

```bash
export PLATFORM_IMAGE="$NEW_IMAGE_ID"
attachment_compose=(
  docker compose --project-name orbbec-agent-platform
  --env-file "$PLATFORM_ENV"
  -f "$RELEASE/deploy/cloud/compose.yaml"
)
"${attachment_compose[@]}" up -d --no-deps --force-recreate platform-attachments
NEW_ATTACHMENT_ID="$("${attachment_compose[@]}" ps -q platform-attachments)"
test -n "$NEW_ATTACHMENT_ID" && test "$NEW_ATTACHMENT_ID" != "$OLD_ATTACHMENT_ID"
test "$NEW_IMAGE_ID" = "$(docker inspect --format '{{.Image}}' "$NEW_ATTACHMENT_ID")"
test healthy = "$(docker inspect --format '{{.State.Health.Status}}' "$NEW_ATTACHMENT_ID")"
```

进程健康后仍需执行一份真实、一次性附件擦除 canary，并绑定 attachment/work identity、claim、对象删除、ledger 终态和幂等重读；没有该业务回执不能把 Running/healthy 称为热修复闭环。当前 owner acceptance 配置缺失，所以 canary 的认证参数尚不可得。

## 4. HR 私有配置、知识和卷

宿主私有目录 `/opt/orbbec-agent-platform/private/hr-agent` 必须 root:root 0700，以下每个文件为 root:root 0600、普通文件且非 symlink：

- `hr-content-keyring.json`
- `hr-provider-profile.json` 和 profile 指定的 credential 文件
- `hr-budget-profile.json`
- `hr-diagnostic-profile.json`
- `hr-release-policy.json`
- `hr-provider-credential`
- `api-runtime.env`、`worker-runtime.env`
- 供 Compose 读取的 `runtime.env`

production app DSN、附件 access key/secret key 和旧 content keyring 不落入上述宿主目录；它们从当前准确 API 容器的 `orbbec-agent-platform-api-secrets` 卷，逐个复制到新 `orbbec-agent-platform-hr-agent-secrets` 卷。HR 卷只允许这 4 个既有秘密、6 个 HR 配置文件，以及从 Compose 完整 JSON 内存解析得到的 `api-runtime.env`、`worker-runtime.env`，共 12 个 10001:10001、0600 普通文件。后两个 env 文件让镜像内 preflight 能从最终 secrets mount 读取与后续 API/Worker 完全相同的环境快照。

`runtime.env` 从现行 `/opt/orbbec-agent-platform/private/platform.env` 完整复制，再以末尾准确键覆盖 `PLATFORM_IMAGE=<digest>`、`PLATFORM_HR_AGENT_MODEL`、`PLATFORM_HR_AGENT_ATTACHMENTS_ENABLED=1` 和预切换 `PLATFORM_HR_WEB_WORKER_ENABLED=1`。不得用 shell `source` 读取它，也不得重写或遗漏现有 Brain、Direct、DingTalk、HR 角色包/旧知识等变量。

知识构建使用冻结方法/role source commit `fc339d8288ba99fec4fb702045c8409f837c007d` 和上述**完整**本地 Bundle v2。`build_intelligence_release` 先调用 `verify_import_bundle` 校验整个 bundle 的 manifest/checksums，再仅按 manifest 复制 85 个 `agent/**/*.md`，并加入方法文件、`intelligence-source-manifest.json` 和 `intelligence-provenance.json`。不得复制源 bundle 的 `analysis.json`（154,170,921 bytes）、29 个 evidence 原件、raw index、xlsx/pdf 或候选人材料。

本地成品已经生成：release ID `hr-intelligence-57d25a1702f4718737fb7779`，94 个资源（85 intelligence + 9 method），私有 tar SHA256 `1156e8e5b75eabd8d342636b3dfac401758f06cfb5c0311bb448a11cde256652`，208,224 bytes。逐文件回执在 `knowledge-build-receipt.json`；私有 tar 本身不进入 Git。

成品验证至少要求：

- source manifest SHA256 正好是 `d227bbb7f4ccb9ecf865bf259c06b17fee54ebd17b5fca2356eb6e3b34b0cad5`；
- intelligence Markdown 数正好是 85，逐项 SHA 与完整 Bundle manifest 一致；
- release manifest 通过 `PublishedKnowledge.check()`；
- `current.json` 指向同根下不可变 release，旧 release 不删除；
- 方法/role source tree 或独立内容 SHA 写入受保护回执；
- 输出树不存在 `analysis.json`、`evidence/`、raw evidence、PDF/XLSX。

安装前对现有 `/data/orbbec-agent-platform/hr-knowledge` 做只读树快照：逐项记录相对路径、类型、owner、mode、size 和 SHA256，并拒绝 symlink。安装只允许新增 `releases/` 容器目录、本次内容寻址目录 `releases/hr-intelligence-57d25a1702f4718737fb7779`，以及原先不存在的 `current.json`；当前生产没有 `releases/`。目标 release/current 任一已出现都停止，不覆盖。安装后重算旧树，原有两个 legacy SHA 目录及其每个字节/owner/mode 必须完全相同；不清理旧 release，不递归 chown/chmod，不改变旧知识文件权限。新 release 自身归 uid/gid 10001，`current.json` 以同目录临时普通文件校验后原子 rename。

新建 work 目录 `/data/orbbec-agent-platform/hr-work` 为 10001:10001、0700。`provision_hr_release.py` 已固定 `ce6c0f3`、不可变镜像、当前 API 容器、6 个配置、知识 tar SHA/大小、release manifest 与两把锁的 helper SHA；它默认不执行，只有 `start --execute` 才上传受保护输入。脚本先验证整个 `MANIFEST.sha256`，再取得 action lock 和固定 release/deployment 的 deploy-input lock。在 `--network none`、read-only、cap-drop 环境里运行 `python -m tools.hr_agent.preflight`，只接受 `ok=false`、`runtime_match=true`、唯一 blocker `database_not_checked`，并逐一核对 API/Worker 的配置、知识、release-policy 与附件身份；去敏原始 JSON 以 0600 保存到任务 metadata 并由 `poll` 取回。之后才增量安装；不迁移、不启动服务。Compose 完整 JSON 只写任务私有 staging 并在内存解析为 `api-runtime.env`/`worker-runtime.env`，不进入普通日志。

实际 provision 已用冻结脚本 SHA256 `b649b93987c7333d49e9927aa721321b60b28012561c40917cfd48ec8e87afcf` 完成，deployment `4cd463bbaf897639b891b04555bd0c78` 返回 exit 0；配置/卷/知识身份、旧知识保留、两把活动锁释放、input/staging/current 临时文件清理和服务未变均已只读核验。该冻结脚本有一个已知权限缺口：nohup 的 stdout/stderr 重定向发生在任务内 `umask 077` 之前，原始两个日志生成为 root:root 0644；metadata 父目录始终为 root:root 0700。已用仅针对这两个准确文件的操作把 mode 改为 0600，内容 SHA 不变，回执保存在 `production/provision-log-mode-repair-1/`。本轮原脚本仍按上述 SHA 封存，不能宣称它原生保证这两个 mode；未来复用必须在外层重定向前设置 `umask 077` 并重新审读。

## 5. production-only HR 子迁移

附件 Worker 已使用新镜像且 production migration 100/六列权限验证通过后，继续使用同一准确 maintenance helper，并显式指定 HR migration set 与 production：

```bash
/usr/bin/python3 "$MIGRATION_HELPER_DIR/hr_agent_migrate.py" \
  "$RELEASE" "$PRIVATE" "$NEW_IMAGE_ID" "$POSTGRES_ID" \
  --migration-set hr --environment production \
  --receipt-dir "$PRIVATE/hr-agent-migration-receipts"
```

不得调用 release 内旧的 `migrate-hr-agent.sh`，也不得省略 `--environment production`；旧 helper 的默认行为是 HR/all，会修改 preview。新 supervisor 对 production 应用 HR 子目录 096、097、098、099、101；执行前要求 production 的 100、102、103、104、105 checksum 全部准确存在。它为唯一环境创建具名只读迁移容器、先持久化授权风险回执、短时授予 production owner membership、超时 stop/kill、撤销 membership，并验证迁移容器停止、production migrator session 为零、owner membership 为零。

完成后 production ledger 应为 001–105 连续 105 条；preview 仍为 001–087、87 条。保存 private receipt；公开回执只含环境、版本/checksum集合、helper/image身份、起止时间和 `cleanup_verified`，不含 DSN 或 SQL 正文。

之后以 production maintenance 身份调用 `initialize_hr_execution_cutover_v102(<fresh UUID>)`，初始化唯一 `legacy` gate；不手写表。先运行 public-only 无数据库 preflight，再运行带 production app DSN 的数据库 preflight。两次均用最终镜像、实际 secrets/knowledge/work mounts、uid 10001、read-only root、cap-drop ALL；保存去敏 JSON。任何 migration/config/knowledge/attachment/release-policy blocker 都停止。

## 6. 定点启动 API 与新 HR Worker

固定 Compose 数组必须贯穿 config、up、ps、stop：

```bash
hr_compose=(
  docker compose --project-name orbbec-agent-platform
  --env-file "$PRIVATE/hr-agent/runtime.env"
  -f "$RELEASE/deploy/cloud/compose.yaml"
  -f "$RELEASE/deploy/cloud/compose.hr-agent.yaml"
  --profile hr-agent
)
"${hr_compose[@]}" config --services
"${hr_compose[@]}" up -d --no-deps --force-recreate platform-api platform-hr-agent-worker
"${hr_compose[@]}" ps platform-api platform-hr-agent-worker
```

此时保留 `PLATFORM_HR_WEB_WORKER_ENABLED=1`，102 phase 保持 `legacy`，因此新云端 root admission 应拒绝，旧链仍可完成已有 work。不得在 legacy gate 下绕过闸门创建 cloud canary。

启动前记录所有共享容器 ID、image ID、startedAt、restartCount；启动后除 API 和目标 Worker 外逐项相等。API 必须健康，新 Worker 的 `python -m app.hr_agent.worker healthcheck` 必须返回常驻进程自身的 pid/start/nonce 和依赖状态。API/Worker 的 release、configuration、knowledge、provider、budget、diagnostic、release-policy 和附件指纹必须一致。共享 `/api/health` 只证明 liveness，不能替代 HR readiness 或其他 Bot 业务连续性。

`/opt/orbbec-agent-platform/current` 暂不切；所有命令继续使用准确 `$RELEASE`。这样 API 预激活失败时可定点重建旧 API，同时保留已经不可回退的新附件 Worker和数据库。

## 7. 真实身份验收、排空和 cutover

必须先取得合法现存 DingTalk owner 登录，且：

1. `GET /api/v1/account` 返回 200、`role=platform_owner`，响应 owner 与授权配置内部比对但不写正文；
2. `GET /api/v1/catalog/agents` 中准确存在 `hr-bot`，证明当前授权决策允许；
3. owner-only `GET /api/v1/manage/hr-readiness` 返回 200、`ready=true`、phase `legacy`、`new_admission_enabled=false`；
4. 同一真实会话读取 `GET /api/hr/agent/configuration` 和 `GET /api/hr/agent/knowledge`，指纹与 preflight 相等。

当前 `hr-p0-acceptance.json` 缺失，以上是实际阻断项；不得用匿名 health、浏览器 cookie/profile读取或新造 owner 身份替代。

验收通过后按状态机执行：

1. 用 maintenance 函数和 fresh request UUID 执行 `legacy -> draining_legacy`。
2. 运行 migration 104 提供的准确 occupancy/count 函数，并按其 terminal-lineage 规则判断，不以 `execution_jobs.status='queued'` 的裸总数代替。迁移前只读库存里的 3 个 `worker_direct_v5/queued` 都已有 binding/mission/turn，Attempt 与 Turn 均 completed、`pending_stop=false`；104 明确允许这类 immutable envelope，因此不能把它们取消或当作排空 blocker。旧候选草稿为 0；切换仍以迁移后的真实函数输出为准。
3. 104 gate 满足后，用同一个 `hr_compose` 加 `--profile hr-web` 定点停止 `platform-hr-web-worker`，并核验准确旧容器不运行。然后在 `runtime.env` 末尾持久化 `PLATFORM_HR_WEB_WORKER_ENABLED=0`，仅重建 API，使旧云 worker 不再被日常配置复活。
4. 再次运行 104 occupancy/count；只有它允许才以 fresh request UUID 执行 `draining_legacy -> cloud`。不得用手写 COUNT 或清理历史 job 冒充 gate 满足。此 gate 覆盖平台 conversation/job/draft/new-work 及网页/API 旧 worker；`cloud` 后平台旧 HR dispatch、offer、admission 与 recovery 都应由 102/104 状态机拒绝。
5. 执行一次 public/synthetic API canary：真实认证、CSRF、受理、认领、heartbeat、保存、kill/restart恢复、幂等重读和附件路径。`full-candidate` 因 personal-processing authorizer 缺失仍须拒绝；不得发送真实候选人材料。
6. 用户已明确允许完全通过真实 HTTP/API 验收，本次页面与浏览器不构成上线前置。飞书独立 session/worker 与平台 web work 不是同一执行链，且用户明确排除飞书，因此不停止本机 `metabot-hr`、不修改其 runtime-contract/ecosystem/PM2 状态，也不把它计入本次 migration 104 occupancy。
7. 全部 API/进程验收通过后才原子切换 `/opt/orbbec-agent-platform/current` 到 `$RELEASE`，记录 symlink 前后目标；保留 current 加两个准确回滚 release，不做泛化清理。

## 8. 分阶段回退

- staging/build 失败：不影响服务；清理准确 input/stage/两锁，保留失败 metadata。首轮已按此路径验证。
- 停旧附件后、migration 100 之前失败：在准确核验 100 仍不存在时，可以恢复原附件 restart policy并启动旧容器。
- migration 100 或任何后续根迁移一旦应用：数据库不回退，旧附件 Worker永不恢复。新附件失败时保持所有附件 Worker停止，修复后只启动新镜像。
- gate 仍为 `legacy` 且新 API/HR Worker失败：停止新 HR Worker，定点用原 `platform.env` 和旧 API image 重建 API；其他服务不动。新附件和已应用 schema 保留。
- 已进入 `draining_legacy`：现行状态机没有安全的 `draining_legacy -> legacy`，不能手改表；暂停、完成/处置旧 work 后再决定是否继续。
- 已进入 `cloud`：使用 `cloud -> draining_cloud` 关闭新受理，让云链已有 work 完成或明确取消；修复后按 migration 105 允许的 `draining_cloud -> cloud` 恢复。不得切回旧执行器或重放同一 work。
- 任何回退都不删除已保存成果、知识 release、材料或历史，不运行 `docker system prune -a`，不恢复迁移前附件镜像。

## 当前真正不可得的执行参数

- 每个变更窗口、cutover initialize/transition 的 fresh UUID。
- migration 104 安装后真实 production occupancy/count 输出；迁移前库存只提供分类基线，不能预先替代 gate。
- 合法 owner session。现存 acceptance 文件缺失，等待用户完成正常登录后才能取得。
- 附件真实擦除 canary 的一次性业务对象身份和最终回执。

最终应用 release/image、公开知识、public-only 私有配置及 production-only migration helper 已冻结。上述缺口不阻止备份、provision 或 production-only schema migration；它们分别阻止本机旧执行器退出、真实身份验收或最终 cutover。
