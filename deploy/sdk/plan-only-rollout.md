# SDK 年度计划入口：叠加式生产发布

本轮只开放 `/sdk/` 的年度计划导入与浏览。禅道及正式需求写入保持关闭；无需部署禅道或 SDK 密钥卷。发布候选必须是已推送主线的完整 40 位提交 SHA，并从该提交构建镜像。原始 CSV 只由企业登录的 SDK contributor 通过页面上传，不放入镜像、Git 或运维日志。

## 发布前保存事实

在生产主机记录 API、Brain、loopback 的容器 ID、镜像 ID、健康状态；从 API 的 `com.docker.compose.project.config_files` 标签获取**有序的全部** Compose 路径，并记录每个文件 SHA-256。记录 `/opt/orbbec-agent-platform/private/platform.env` 摘要、`PLATFORM_HR_AGENT_MODEL` 实际运行值，以及控制库与预览库最高迁移版本。不得用 `current/deploy/cloud/compose.yaml` 单文件重建；现行 HR generation、全景与 DAQ 覆盖需要保留。

将 `platform.env` 原件以 `0600 root:root` 复制到本次独立备份目录。对 `agent_platform_control`、`agent_platform_control_preview` 分别执行 `pg_dump -U platform_owner --format=custom --no-password`，检查 `pg_restore -l` 可读。数据库备份和环境文件不得进入公开目录。保留旧 API 镜像，确认新镜像构建和备份后再切换。

## 镜像与配置预检

将已推送主线准确提交归档到 `/opt/orbbec-agent-platform/releases/<SHA>`，用该目录的 `deploy/cloud/Dockerfile` 构建 `orbbec-agent-platform:<SHA>`，传入 `--build-arg RELEASE_SHA=<SHA>`；构建后核对镜像中的 `PLATFORM_RELEASE_SHA` 与 `<SHA>`。生成权限 `0600` 的 JSON 覆盖文件，内容严格如下：

```json
{"services":{"platform-api":{"image":"orbbec-agent-platform:<SHA>","environment":{"PLATFORM_SDK_PLAN_ENABLED":"1"}}}}
```

把 `<SHA>` 换为真实 40 位提交。先用当前版本 `cloud_env.py apply` 和权限 `0600` 的 SDK 设置文件原子写入五项关闭的禅道配置及 `PLATFORM_SDK_PLAN_ENABLED=1`；此操作自带平台发布锁。随后取得同一平台发布锁，在锁内重新读取 API 容器 ID，**在任何 `up` 之前**调用本目录 `overlay_guard.py`，传入覆盖文件绝对路径、目标镜像名和刚读取的 API 完整容器 ID：

```bash
/usr/bin/python3 /opt/orbbec-agent-platform/releases/<SHA>/deploy/sdk/overlay_guard.py \
  /opt/orbbec-agent-platform/private/sdk-plan-overlay-<SHA>.json \
  orbbec-agent-platform:<SHA> \
  <切换前API完整容器ID>
```

要求输出 `SDK_OVERLAY_CONFIG_OK`，记录其中的 `container_id`、`compose_files`、`services`、`current_image`、`target_image`、`runtime_overrides`。脚本从实际容器标签取有序 Compose 文件，比较叠加前后有效配置，只有 API 镜像与计划开关可变；同时核对现行运行环境与渲染环境。`PLATFORM_HR_AGENT_MODEL` 是现行 API 的运行时 Compose 变量，脚本按实际容器值参与两侧渲染；执行下文 `up` 时必须传入**同一值**。若检查失败，不进行迁移或切换。

## 迁移与切换

使用新镜像和现有私有 DSN，在生产 PostgreSQL 所在 `orbbec-agent-platform-internal` 网络运行 `python -m app.control_plane.migrate`：先预览库，后生产库。迁移程序需要 `platform_control_migrator_preview → platform_control_owner_preview` 与 `platform_control_migrator → platform_control_owner` 的临时角色授权；按 `bootstrap-control-db.sh` 的做法执行，并在成功、失败和中断时都撤销这两项授权。运行容器仅挂载 `/opt/orbbec-agent-platform/private:/run/control-secrets:ro`，分别指定 `preview-control-migrator-database-url`、`control-migrator-database-url`，`PLATFORM_CONTROL_OWNER_ROLE` 与 `PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations`。核对两库迁移版本与 SQL checksum 至 117；不要运行整个 `bootstrap-control-db.sh`，它包含其他服务和凭据操作。

在同一个有操作锁的发布会话内，重新核对 API 容器 ID 与上述预检收据相同，再用标签记录的全部旧 Compose 文件及新增覆盖文件执行：

```bash
env -i PATH=/usr/bin:/bin PLATFORM_HR_AGENT_MODEL='<切换前实际值>' \
  /usr/bin/docker compose --project-name orbbec-agent-platform \
  --env-file /opt/orbbec-agent-platform/private/platform.env \
  -f '<旧Compose文件1>' -f '<旧Compose文件2>' ... -f '<旧Compose文件N>' \
  -f /opt/orbbec-agent-platform/private/sdk-plan-overlay-<SHA>.json \
  up -d --force-recreate --no-deps platform-api
```

仅替换 `platform-api`；核对新 API 镜像、`PLATFORM_RELEASE_SHA`、`PLATFORM_SDK_PLAN_ENABLED=1`、健康状态和本机健康接口，且 Brain、loopback 及其他容器 ID 未变。再次核对 API 的有效挂载、网络和 HR/全景/DAQ 环境字段与预检一致。公网 `/sdk/` 未登录访问的 401 是预期认证结果，不能单凭它宣布页面或 CSV 可用。

发布前写入 `platform.env` 的计划状态供以后正常发布继承。不得调用 `cloud_env.py activate`，该函数目前只读取基础 Compose。发布前后的 `platform.env` 摘要与备份路径记入私有收据。

## 回滚

如果 API 健康、身份接口或计划 API 失败，在保留原始八份 Compose 文件的前提下，以备份的 `platform.env` 原件替换当前文件，重验权限 `0600 root:root`，用**原有八份 Compose 文件**和同一个 `PLATFORM_HR_AGENT_MODEL` 值执行 `up -d --force-recreate --no-deps platform-api`。核对原镜像 ID、健康、挂载、网络及其他容器 ID。**不要调用旧 release 的 `cloud_env.py`**：它不识别新增的 `PLATFORM_SDK_PLAN_ENABLED`，必须先恢复原件。114–117 为向前兼容的加法迁移，回滚应用时保留表和数据，不手工删除。

## 场景验收边界

本地真实 CSV 经过 HTTP、企业身份测试中间件、授权、CSRF、PostgreSQL 与幂等场景。生产还需企业登录的 SDK contributor 上传 CSV 并核对 69 条来源、67 条任务、17 个栏目、SHA-256 `41df018a029f1d27417bec3008bbe7aab3becd785cc38b312c563ce18ee8370d`，重复上传仍是同一批次；由用户按仓库 `AGENTS.md` 执行页面验收。正式需求、评审、任务与 SDK 发布仍需禅道服务和真实设备证据，不能从计划入口健康推断为已通过。
