# 最小钉钉登录演示运行手册

状态：代码已准备，目标机未部署。本手册不代表生产切换，也不授权执行生产变更。

本演示只发布 `https://agent.orbbec.com.cn/_preview/dingtalk-r1/`。现有
`https://agent.orbbec.com.cn/` Basic Auth、`/admin/`、FAE 域名与 IP 入口必须
保持原状。它验证 Task 1–3 已实现的白名单身份、隔离容器和 Nginx 预览路由；完整
生产账号体系仍需后续 Release 1 工作。

## 固定边界

- 目标机：`root@47.106.112.69`
- SSH 私钥：`/Users/neo/.ssh/orbbec_aliyun_ed25519`
- 预览端口：仅 `127.0.0.1:8081`
- 当前 Nginx 文件锁定 SHA-256：
  `382d733e1a581569f4ceedd03ce24ab9113f61a595015bc0449e1319026c1e97`
- 发布流程：prepare → verify → activate
- 发布命令只接受 Git commit SHA 与该 commit 的 `git archive` SHA-256；秘密不得
  作为参数、环境回显或日志内容传入。

## 执行前置条件

执行人应先以只读方式确认以下条件，任一不满足即停止：

1. 本地仓库处于待发布 commit，包含 Task 1–3 和 Task 4，且 tracked、untracked
   文件均为空；SSH host key 已可信固定。
2. 目标机 DNS、证书和 HTTPS 正常；根路径仍返回 Basic Auth `401`，FAE 域名与
   `http://47.106.112.69/` 均返回 `200`。
3. `/etc/nginx/sites-enabled/agent-domain.conf` 的实际目标文件摘要严格等于上面的
   固定值，Nginx 配置有效。
4. `8000`、`8080` 的现有服务健康，`8081` 没有监听，公网监听面没有异常；目标盘
   至少有 2 GiB 可用空间。
5. `/opt/orbbec-agent-platform/current` 是有效 release 链接，且
   `/opt/orbbec-agent-platform/private/platform.env` 已存在。
6. 预览数据库仅为 `agent_platform_control_preview`，四条 DSN 分别绑定
   `platform_control_app_preview`、`platform_audit_append_preview`、
   `platform_directory_worker_preview`、`platform_control_migrator_preview`。

秘密目录必须是
`/opt/orbbec-agent-platform/private/demo-preview`，owner 为 root、mode 为
`0700`。首次执行前只放以下 5 个 operator 输入，且必须恰好只有这 5 个普通
非符号链接文件，每个均为 root:0600：

```text
dingtalk-app-key
dingtalk-agent-id
dingtalk-corp-id
dingtalk-app-secret
demo-userids
```

不要手工创建 DSN、数据库密码或 keyring。verify 阶段的 root-only prerequisite
bootstrap 会在 sibling root:0700 状态目录中一次生成四套独立的 64 位十六进制
数据库密码和三套独立的 32-byte keyring，幂等创建仅属于 demo 的 preview 数据库
和角色，验证成功后再发布 7 个 root:0600 文件。失败时保留不对外可读的 staging
以便用同一批密码恢复，不会在重跑时静默轮换。

成功生成后的目录必须恰好是以下 12 个文件：

```text
dingtalk-app-key
dingtalk-agent-id
dingtalk-corp-id
dingtalk-app-secret
preview-control-database-url
preview-control-audit-database-url
preview-control-directory-worker-database-url
preview-control-migrator-database-url
preview-identity-hmac-keyring
preview-identity-encryption-keyring
preview-rate-limit-hmac-keyring
demo-userids
```

`demo-userids` 只允许 1–3 行唯一、稳定的钉钉 userid。不要写姓名、手机号或邮箱，
不要把文件内容复制到终端记录。应用密钥、三套 keyring 和四条 DSN 彼此按 Task 2
约束隔离。已有完整 12 文件的幂等重跑只验证并复用现有密码，不生成替代值。

数据库 bootstrap 使用正在运行的 `platform-postgres` 容器及其现有 owner secret，
创建 `platform_control_owner_preview`（NOLOGIN/NOINHERIT）、四个相互独立的
LOGIN/NOINHERIT 角色，以及 migration 001 引用但 demo 不启用的两个 NOLOGIN
角色。数据库 owner 只属于 preview owner，migrator 仅因 NOINHERIT membership
才能显式 `SET ROLE`；API 看不到 migrator、directory worker 或 allowlist secret。

## 生成不可变发布参数

在干净 worktree 中计算参数；计算后不要再修改 commit：

```bash
demo_release_sha="$(git rev-parse HEAD)"
demo_archive_sha256="$(git archive --format=tar "$demo_release_sha" | shasum -a 256 | awk '{print $1}')"
git status --porcelain=v1 --untracked-files=all
```

最后一条命令必须没有输出。执行脚本会重新生成 archive 并核对 commit、摘要与
manifest；脏 worktree、错误 SHA 或错误摘要都会在连接目标机前失败。

## 发布

仅在所有前置条件人工复核后运行：

```bash
./deploy/cloud/deploy-demo-preview.sh "$demo_release_sha" "$demo_archive_sha256"
```

脚本依次执行：

1. **prepare**：拒绝脏源码，生成并核对不可变 archive、release manifest 和摘要。
2. **verify**：上传到固定 incoming 路径；在目标机再次核对摘要、12 个秘密文件、
   operator 输入、当前根路径与 FAE、Nginx 摘要、磁盘和端口；生成并验证 preview
   数据库/角色/DSN/keyring；构建带 commit SHA 的镜像；用基础 Compose 加预览
   overlay 生成实际配置；migration 和成员 bootstrap 都通过无 host port 的
   `platform-demo-preview-runner` 同时连接 internal 与 edge 网络，前者只访问 preview
   PostgreSQL，后者可调用钉钉；重新解析 1–3 个白名单成员；只启动两个预览服务并
   验证 `127.0.0.1:8081`。此阶段不修改 Nginx，失败只停止并移除预览服务。
3. **activate**：把 `current` 原子切到已验证 release，使用 Task 3 installer 的
   锁定 Nginx 摘要安装单一路由，再运行自动验收。激活后的任意失败自动调用预览
   rollback，并恢复之前的 `current` 链接。

成功输出只能是固定安全标记，不包含秘密、userid、Cookie、登录 code 或钉钉响应
正文。自动验收也可以在目标机单独复跑：

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 root@47.106.112.69 \
  /opt/orbbec-agent-platform/current/deploy/cloud/accept-demo-preview.sh
```

## 自动验收范围

`accept-demo-preview.sh` 应全部 PASS：

- 两个预览容器使用当前不可变镜像且健康；8081 只绑定 loopback。
- 现有非预览容器的 ID、镜像、StartedAt、RestartCount 与 verify 前一致。
- 公网监听、根路径、ADMIN、FAE 域名与 FAE IP 响应与 verify 前一致；根路径仍有
  Basic Auth challenge。
- 预览 HTTPS health 只返回 `{"status":"ok"}` 和 JSON content type。
- 登录页、至少一个构建资源和 QR start 可用。
- 登录 challenge Cookie 具备 `Secure`、`HttpOnly`、`SameSite=Lax` 与
  `Path=/_preview/dingtalk-r1/`。
- 未认证 account 路由为 `401`；无效 state 及重复提交均为 `401`，不触发身份
  provider 调用。
- root:0600 白名单仍为 1–3 个唯一 userid，且安全保存的 bootstrap 成员数一致，
  形成“目录中没有其他可登录成员”的自动化拒绝证据。

## 必须人工完成的 QR 验收

自动检查通过后才进行人工测试：

1. 由一个已批准账号打开演示 URL，完成钉钉扫码。
2. 确认跳回预览前缀、页面显示预期的内部身份与角色；不要截图或记录 provider ID、
   Cookie、code 或 token。
3. 退出后确认 Session 失效，再登录一次确认状态单次使用和 Session 轮换。
4. 使用未列入 `demo-userids` 的账号扫码，确认得到通用拒绝且没有 Session。若暂时
   没有第二账号，记录“未执行”，保留自动 allowlist/bootstrap 一致性证据，不能
   写成真实拒绝测试通过。
5. 再次运行自动验收，确认根路径、ADMIN、FAE、容器和监听不变量仍然通过。

## 回滚

任何异常或演示结束后运行固定回滚命令：

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 root@47.106.112.69 \
  /opt/orbbec-agent-platform/current/deploy/cloud/rollback-demo-preview.sh
```

它只删除预览 Nginx include、reload Nginx，并停止/移除两个预览服务；不会执行
`docker compose down`，不会重启 FAE、ADMIN 或现有 Platform。回滚后再次确认
根路径 `401`、FAE `200`、8081 不再监听，并记录 Task 3 rollback 的固定安全结果。

## 发布证据（执行后填写，不含秘密）

当前状态：未部署；以下字段为空不代表失败，也不代表生产切换。

```text
执行时间：未执行
release SHA：未记录
archive SHA-256：未记录
image digest：未记录
preview container IDs：未记录
preview migration version：未记录
active Nginx config SHA-256：未记录
自动验收：未执行
批准账号 QR：未执行
未批准账号 QR：未执行
rollback rehearsal：未执行
```

证据只允许记录不可变构建标识、容器标识、迁移版本、配置摘要和固定 PASS/FAIL。
不得记录 stable userid、姓名映射、Cookie、授权 URL、provider 响应或任何秘密。

## 已知同集群 CONNECT 限制

PostgreSQL 普通数据库默认通过 `PUBLIC` 授予 CONNECT，且 PostgreSQL ACL 没有
“按角色拒绝”语义。本 demo 会对 preview 角色显式撤销其它数据库的直接 CONNECT
grant，并验证它们在其它数据库的 schema、relation、routine 和 type 上没有任何
直接权限；但它不会为了 demo 撤销现有生产数据库的 PUBLIC CONNECT，因为这可能
打断未知生产用户。因此同集群层面仍存在“可以建立空连接、但没有对象权限”的残余
边界。四条 DSN 和应用 DSN validator 均严格锁定
`agent_platform_control_preview`。正式生产隔离必须在后续工作中采用独立数据库
集群或显式 pg_hba/全量角色授权重构，不能把本 demo 声称为完全网络隔离。
