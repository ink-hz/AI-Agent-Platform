# SDK 工作台禅道试运行

本目录只提供隔离试运行配置，不修改现有云生产服务。禅道数据与原平台数据库分开。镜像版本锁在禅道开源版 22.6，MariaDB 10.6；正式发布前应记录实际镜像 digest 并备份两个卷。

1. 在有 Docker Compose 且已有 `orbbec-agent-platform-internal` 网络的试运行主机上，创建仅当前用户可读的 `.env`，设置 `SDK_DB_PASSWORD` 和 `SDK_DB_ROOT_PASSWORD`，文件权限为 `0600`。不要把 `.env` 加入仓库。数据库和禅道均没有公网监听；禅道安装界面仅绑定主机 `127.0.0.1:18131`。
2. 执行 `docker compose --env-file .env -f deploy/sdk/compose.yaml up -d`。通过 SSH 端口转发访问 `127.0.0.1:18131` 完成初次安装。产品创建为 OpenOrbbecSDK，执行项目使用单独的 SDK 工作区。创建仅有该产品/执行范围 API 权限的 `sdk-api` 账号。
3. 将平台服务可读、权限 `0600` 的服务账号密码文件挂载到平台容器，例如 `/run/secrets/sdk-zentao-password`。在**待审核的正式部署变更**中给平台 API 配置：

   ```text
   PLATFORM_SDK_WORKBENCH_ENABLED=1
   PLATFORM_SDK_ZENTAO_BASE_URL=http://zentao:80
   PLATFORM_SDK_ZENTAO_ACCOUNT=sdk-api
   PLATFORM_SDK_ZENTAO_PASSWORD_FILE=/run/secrets/sdk-zentao-password
   PLATFORM_SDK_ZENTAO_PRODUCT_ID=<安装后实际产品 ID>
   PLATFORM_SDK_ZENTAO_EXECUTION_ID=<安装后实际执行 ID>
   ```

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

禅道异常、数据结构不符合契约、产品 ID 不匹配、请求 ID 冲突均在接口中显式返回错误。创建需求或任务的上游结果不确定时，操作停在 `reconcile_required`；平台所有者可在“待核对写入”中核对禅道记录并绑定 ID。参见[设计](../../docs/superpowers/specs/2026-10-19-sdk-development-v1-design.md)。

官方参考：[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[22.6 下载](https://www.zentao.net/downloads.html/)、[API v2 登录](https://www.zentao.net/book/api/post-users-login-2142.html)、[创建需求](https://www.zentao.net/book/api/post-stories-2169.html)、[创建任务](https://www.zentao.net/book/api/post-tasks-2207.html)。[22.6 漏洞条目](https://osv.dev/vulnerability/CVE-2026-111346)。
