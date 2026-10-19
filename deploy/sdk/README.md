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

4. 先运行 `backend/control_migrations/114_sdk_workbench_requirements.sql`。平台所有者进入 `/sdk/` 授予 SDK 角色后，以普通成员身份创建一条真实需求、评审并拆解任务。验收请求需使用平台原有企业会话、同源 Origin 和 CSRF；不得直接把服务账号 token 给浏览器。

禅道异常、数据结构不符合契约、产品 ID 不匹配、请求 ID 冲突均在接口中显式返回错误。创建需求或任务的上游结果不确定时，操作停在 `reconcile_required`；平台所有者可在“待核对写入”中核对禅道记录并绑定 ID。参见[设计](../../docs/superpowers/specs/2026-10-19-sdk-development-v1-design.md)。

官方参考：[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[API v2 登录](https://www.zentao.net/book/api/post-users-login-2142.html)、[创建需求](https://www.zentao.net/book/api/post-stories-2169.html)、[创建任务](https://www.zentao.net/book/api/post-tasks-2207.html)。
