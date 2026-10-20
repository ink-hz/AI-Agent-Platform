# SDK 工作台禅道试运行

2026 年度计划入口的首轮生产发布按[叠加式发布与回滚步骤](plan-only-rollout.md)执行。2026-10-20 已完成 [CSV 代录、禅道实例和平台 API 生产接入](../../docs/reviews/2026-10-20-sdk-plan-and-zentao-production.md)；企业浏览器会话、打包及真机测试尚未验收。本文件以下保留禅道部署与验收方法。现行生产 API 有多份 HR、全景与 DAQ Compose 覆盖，以下单文件 `cloud_env.py activate` 命令**不能用于当前生产栈**。

本目录提供独立的禅道试运行配置；禅道数据与原平台数据库分开。当前镜像默认使用禅道开源版 `22.6`、MariaDB `10.6` 标签，标签可能移动；真实试验与发布要在私有 `.env` 中设置 `SDK_ZENTAO_IMAGE`、`SDK_DB_IMAGE` 为核对过的对应架构 `仓库@sha256:<digest>`，并备份两个卷。

## 本地真实接口试验

先确认 Docker context 指向本机。在**本机 Docker**中创建试验专用内部网络 `orbbec-agent-platform-sdk`（正式云平台 API 也只接入该 SDK 网络；禅道不接入平台数据库所在网络），使用随机值写入被 Git 忽略、权限 `0600` 的 `deploy/sdk/.env`，包含 `SDK_DB_PASSWORD`、`SDK_DB_ROOT_PASSWORD` 及两个经核对的镜像 digest 变量。然后执行：

```bash
docker network create --internal --subnet 172.28.0.0/29 orbbec-agent-platform-sdk
docker-compose --env-file deploy/sdk/.env -f deploy/sdk/compose.yaml up -d
docker-compose --env-file deploy/sdk/.env -f deploy/sdk/compose.yaml ps
```

首次在本机 `http://127.0.0.1:18131/` 完成禅道安装；安装第三步必须核对默认语言为 `zh-cn`，安装后先确认 `/api.php/v2/users/login` 可返回 JSON。创建名称以 `SDK-V1-ACCEPT-` 开头的独立测试产品、项目与执行，并给测试账号授予该范围内创建/查看需求与任务的权限。将该账号密码写入本机权限 `0600` 的文件，在执行测试的终端设置 `SDK_LIVE_ACCEPT=1`、`SDK_LIVE_ZENTAO_URL=http://127.0.0.1:18131`、`SDK_LIVE_ZENTAO_ACCOUNT`、`SDK_LIVE_ZENTAO_PASSWORD_FILE`、`SDK_LIVE_PRODUCT_ID`、`SDK_LIVE_PRODUCT_NAME`、`SDK_LIVE_EXECUTION_ID`，再运行：

```bash
backend/.venv/bin/python -m pytest -q -s backend/tests/test_sdk_zentao_live.py
```

该测试会在隔离产品留下带 `SDK-V1-ACCEPT-` 前缀的真实需求与任务，核对读回的 ID、正文、验收标准和关联执行。平台掌管需求评审，禅道需求用 `needNotReview=1` 保持可开发状态；平台审批完成前仍禁止通过平台创建任务。测试失败要保留首个失败状态及对应记录，不能自动重发创建请求。记录实际镜像 digest、产品/执行 ID 与结果；密码和 Token 不进入记录。

设置 `SDK_LIVE_SOURCE_DIR` 为完整初始化的打包仓库路径、`SDK_LIVE_ARTIFACT_PATH` 为**实际构建**的 SDK 产物路径。再设置 `SDK_LIVE_PROJECT_ID`、`SDK_LIVE_EXECUTION_NAME`、`SDK_LIVE_ANCHOR_TASK_ID`（上一步真实契约试验创建的任务 ID），以及 `SDK_LIVE_HARDWARE_BLOCKER_PATH`（记录无设备事实的本机文件绝对路径），可执行平台 API → 一次性 PostgreSQL → 真实禅道的完整本地链路。写入前试验会核对产品、项目、执行及锚点任务与需求的关联；仅有回环 URL 不足以证明它不是正式环境。该试验注入本地身份上下文，不能替代正式企业登录与 CSRF 验收；缺少单测和真机报告时会记录硬件 `blocked` 并核对发布门槛关闭。

```bash
backend/.venv/bin/python -m pytest -q -s backend/tests/test_sdk_platform_live.py
```

结束后可停容器保留卷复查：`docker-compose --env-file deploy/sdk/.env -f deploy/sdk/compose.yaml stop`。清理卷前先备份或确认测试记录已转存。

平台完整验收还需 SDK 源码清单、实际构建日志与产物摘要、单测和硬件报告。没有设备或报告时，在平台记录 `blocked` 并确认审批门槛仍关闭；接口试验成功不能替代此业务证据。正式平台员工入口为 `https://agent.orbbec.com.cn/sdk/`。

1. 正式主机先由平台云 Compose 创建 `orbbec-agent-platform-sdk` 私有网络，禅道 Compose 作为外部网络加入；本地独立试验按上面的命令手动创建。创建仅当前用户可读的 `.env`，设置 `SDK_DB_PASSWORD`、`SDK_DB_ROOT_PASSWORD`、`SDK_ZENTAO_IMAGE` 和 `SDK_DB_IMAGE`，后两项须为正式主机架构对应的固定 digest；文件权限为 `0600`。不要把 `.env` 加入仓库。数据库和禅道均没有公网监听；禅道安装界面仅绑定主机 `127.0.0.1:18131`。`sdk-admin` 普通 bridge 为本机端口映射提供路径，也允许禅道容器出站访问；它不接入平台数据库。正式部署应通过主机出口策略限制该容器出站范围。
2. 从仓库根目录执行 `docker compose --env-file deploy/sdk/.env -f deploy/sdk/compose.yaml up -d`（若主机仅安装独立 Compose，使用 `docker-compose`）。通过 SSH 端口转发访问 `127.0.0.1:18131` 完成初次安装。正式产品创建为 OpenOrbbecSDK，执行项目使用单独的 SDK 工作区。创建仅有该产品/执行范围 API 权限的 `sdk-api` 账号，勿将本地测试产品 ID 用作正式配置。
3. 将服务账号密码放入独立的 `orbbec-agent-platform-sdk-secrets` 卷，卷内文件为 `password`，归属平台 API 的 UID/GID `10001:10001`，权限为 `0600`。该卷只挂载到 `platform-api:/run/sdk-secrets:ro`，不挂载到 HR Worker。密码不得写入 `.env` 或提交到仓库。在**待审核的正式部署变更**中给平台 API 配置：

   ```text
   PLATFORM_SDK_WORKBENCH_ENABLED=1
   PLATFORM_SDK_ZENTAO_BASE_URL=http://zentao:80
   PLATFORM_SDK_ZENTAO_ACCOUNT=sdk-api
   PLATFORM_SDK_ZENTAO_PASSWORD_FILE=/run/sdk-secrets/password
   PLATFORM_SDK_ZENTAO_PRODUCT_ID=<安装后实际产品 ID>
   PLATFORM_SDK_ZENTAO_EXECUTION_ID=<安装后实际执行 ID>
   ```

   `platform.env` 由正式发布脚本管理。SDK 配置文件须属于执行用户且权限为 `0600`，包含上述除 `PASSWORD_FILE` 外的五项。`/usr/bin/python3 <当前release>/deploy/sdk/cloud_env.py render <platform.env绝对路径>` 只输出五项供复核。启用前应核对禅道 digest、产品/执行 ID、服务账号范围、密码卷内文件的 UID/GID 与权限，并完成备份。正式启用或关闭执行 `/usr/bin/python3 <当前release>/deploy/sdk/cloud_env.py activate /opt/orbbec-agent-platform/private/platform.env <SDK配置文件绝对路径> /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml`；该命令与正式发布共用操作锁，原子写入配置、仅重建 `platform-api`，核对容器实际 SDK 配置及健康。失败时尝试恢复旧配置和 API；若恢复也失败，停止后续操作并排查。配置文件的关闭值为开关 `0`、地址/账号空、两个 ID 为 `0`。每次正式发布都会校验并保留已启用的五项；如密钥卷不符合要求，发布在停止旧 API 前失败。密钥文件始终不进入该配置文件。

4. 按现有控制面迁移程序依次运行 `114_sdk_workbench_requirements.sql`、`115_sdk_release_evidence.sql`，分别在预览和生产库核对权限。平台所有者进入 `/sdk/` 授予 SDK 角色后，以普通成员身份创建一条真实需求、评审并拆解任务。验收请求需使用平台原有企业会话、同源 Origin 和 CSRF；不得直接把服务账号 token 给浏览器。

## 首版验收路径

1. 开发成员创建需求，填写场景、设备/平台和验收标准；评审成员批准当前禅道需求版本；开发成员创建至少一个关联任务。
2. 开发成员冻结候选：填写打包仓库完整 40 位 SHA、SDK 版本、构建脚本路径、7 个一级子模块和 Viewer 内嵌 SDK 子模块的完整 SHA，并规划至少一个 OS/架构目标。确认候选详情不可编辑，页面标明“人工登记，待核对”。
3. 为每个目标登记构建。成功构建须录入产物名、大小、SHA-256、URI 和日志 URI；失败、超时或取消构建不得附产物。最近一次失败构建应让门槛关闭。
4. 评审成员对每个最近成功构建的产物分别登记单元测试和硬件测试。硬件测试填写设备、固件及报告 URI；`blocked`、`failed`、`skipped` 或非零失败/跳过数应让门槛关闭。
5. 与候选创建人不同的发布负责人审批当前证据版本，再登记发布渠道、时间和回退 URI。详情应能反查需求 ID、任务 ID、完整基线、构建产物摘要、测试结果、审批人和发布记录。审批后新增构建或测试证据，应要求重新审批。
6. 用同一 `request_id` 重放写请求，应返回首次结果；改变请求体重放应返回 409。禅道写入结果不确定时，不得自动重试创建，应由平台所有者在“待核对写入”核对并绑定。

仅有自动化 HTTP/数据库和前端组件测试时，不能将本清单标记为真实禅道或浏览器验收通过。浏览器连接与页面验收按本仓库 `AGENTS.md` 由用户执行。试运行记录应包括禅道实际版本与镜像 digest、平台提交、产品/执行 ID、两次迁移、每步请求状态与未通过项；不得记录密码、Token、会话 Cookie。

22.6 版本应保持在私网、仅由 BFF 服务账号访问；公开漏洞库已记录该版本的评论授权绕过问题。正式开放前复核上游修复版本并升级或继续隔离，不对外开放禅道原生入口。

禅道异常、数据结构不符合契约、产品 ID 不匹配、请求 ID 冲突均在接口中显式返回错误。创建需求或任务的上游结果不确定时，操作停在 `reconcile_required`；平台所有者可在“待核对写入”查看原提交摘要，核对禅道记录后绑定 ID，平台会校验字段并拒绝重复外部 ID。令牌过期时 BFF 重新认证一次；确定的 HTTP 认证或权限拒绝不会进入对账队列，修复服务账号后可复用原请求 ID 重试。参见[设计](../../docs/superpowers/specs/2026-10-19-sdk-development-v1-design.md)。

官方参考：[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[22.6 下载](https://www.zentao.net/downloads.html/)、[API v2 登录](https://www.zentao.net/book/api/post-users-login-2142.html)、[创建需求](https://www.zentao.net/book/api/post-stories-2169.html)、[创建任务](https://www.zentao.net/book/api/post-tasks-2207.html)。[22.6 漏洞条目](https://osv.dev/vulnerability/CVE-2026-111346)。
