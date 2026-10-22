# SDK `/sdk/` 禅道直达发布记录（2026-10-22）

## 发布版本与范围

- 源码从 `master` 的 `300341f721fef139d01466088800dd83c7eef0b0` 构建；平台 API 镜像为 `orbbec-agent-platform:300341f721fef139d01466088800dd83c7eef0b0`。
- `https://agent.orbbec.com.cn/sdk/` 改为平台钉钉身份入口及禅道 22.6 界面。只重建 `platform-api` 和 `orbbec-sdk-workbench-trial-zentao-1`；禅道数据库和其他平台容器未重建。
- 禅道使用 `ZT_WEB_ROOT=/sdk`，现有产品、需求和权限数据保留。旧 IP 入口仍可作为受禅道自身登录保护的回退入口；其公开 API、安装和升级路径已封禁。
- 当前平台会话、当前用户、禅道账号和禅道会话 ID 共同绑定访问签名。Nginx 每次访问 `/sdk/*` 向平台校验，过滤平台 Cookie，不转发给禅道。首次打开合法禅道深链接会先免密登录，再回到该链接。

## 发布前备份与隔离验收

- 服务器私有备份目录：`/opt/orbbec-agent-platform/staging/sdk-direct-20261022/backup/`，包含禅道 SQL、数据卷归档、现行 Nginx 两个站点配置、禅道 Compose、环境与容器检查信息；SQL 与数据卷归档权限为 `0600`。
- 最新切换前代理 SHA-256：`e6ae3ce22949c079a6f5112f066a989c04bb41678f0d2a46217cea2bc6f9a596`；旧 IP 配置：`7812a19f583686657d99f2c7baa8b3d69c62494d54876b5165c4ef99c210f4fb`；禅道 Compose：`5a18692aa618fe15fa781705e9d67682583d847beac60d1937df8ddaa3ef3e0a`。
- 候选 Nginx 配置由**当时线上实际配置**生成，只修改 SDK 路由和公开 API 防护。隔离 Nginx 1.27 容器语法检查通过；生产仅对实际完整配置执行默认 `nginx -t`，没有执行独立配置的 `nginx -t -c`。
- 禅道生产备份副本部署在隔离容器中，个人账号免密登录返回 302，`zentaosid` Cookie 仅限 `/sdk/`；个人首页、需求、任务、Bug、用例、发布列表以及需求与 Bug 创建表单均返回对应页面。隔离副本产品 1 有 69 条未删除需求，生产切换后仍为 69 条。测试副本没有现有附件，故没有可用的真实附件下载样本。
- 后端定向回归 **736 passed**，前端定向回归 **198 passed**，前端构建成功；代码复查通过。Nginx 隔离功能测试覆盖匿名登录跳转、入口发 Cookie、授权页、拒绝访问、Cookie 过滤、公开 API 拒绝。

## 生产切换与检查

- 先加固旧 IP vhost，再更新平台 API 镜像，然后只重建禅道 Web 容器，最后切换 `agent.orbbec.com.cn` 的 SDK 路由。Compose 配置比较确认平台步骤只改变 API 镜像和发布 SHA；禅道步骤只增加 `ZT_WEB_ROOT=/sdk`。
- 生产默认 `nginx -t` 两次通过，Nginx 服务运行中；`/var/lib/nginx/agent_platform_proxy` 和 `agent_platform_client` 均为 `www-data:www-data`、`0700`。检查的 Nginx 错误日志没有新的权限错误。
- 匿名 `/sdk/` 及需求深链接均 302 至 `/login?return_path=%2Fsdk%2F`；域名 `/sdk/api.php/v1/users`、`/sdk/install.php` 以及旧 IP `/api.php/v1/users`、`/install.php` 均为 403。
- 平台 API 健康，未认证的两个 SDK SSO 接口均为 401；禅道 Web 运行中，数据库健康且未重建。本机禅道 `/sdk/` 返回登录跳转，登录 HTML 16,776 字节，CSS 440,613 字节。
- 行政服务 `/office/`、JS、CSS 与切换前 SHA-256 完全一致：HTML `1c36dd2768959dd26f0195e38ebbe992030f063d17cbc50a4f5dd46c87c6c378`；JS `02fd67f8306bfcd74dc2f2c5045524802761cc1cc014e7197475a7b4046b88a6`；CSS `6bae263f9322baef52cafe0e50993810b9eed2ae227226a95afdba1a485323a6`。平台登录页引用的新 JS/CSS 与本次构建内容哈希一致。

## 待业务验收与边界

- 员工在已登录钉钉的浏览器中点击 SDK 导航、进入个人禅道、实际提交需求及上传附件，按既定分工由业务方做最终交互验收。此次没有取得员工真实浏览器会话，不能把匿名线上探针或隔离副本测试写成该项通过。
- 禅道与平台同源，路径本身不构成浏览器安全隔离；继续以禅道加固、双层权限、审计和版本更新控制此风险。旧 IP 仍可访问禅道自身登录页，后续是否关闭应单独决定。
- 已有 `platform-brain` 容器在发布前即处于 `unhealthy`，本次未触碰该容器；不把它计为 SDK 发布造成的新故障。

## 回退

1. 将 `backup/agent-domain-pre-cutover.conf` 恢复到 `/etc/nginx/sites-available/agent-domain.conf`，将 `backup/sdk-ip-pre-cutover.conf` 恢复到 `/etc/nginx/sites-available/sdk-ip-https.conf`；核对 `www-data` 运行用户及独立临时目录属主后，执行**默认** `nginx -t` 并重载。
2. 将 `backup/sdk-compose-pre-cutover.yaml` 恢复到 `/opt/orbbec-agent-platform/private/sdk-compose.yaml`，只重建 `zentao` Web 容器，不改动禅道数据库和数据卷。
3. 平台 Compose 使用原有 24 个配置文件，不附加本次 `overlay.json`，仅重建 `platform-api`，恢复镜像 `orbbec-agent-platform:0e7506b18ad17048fcfbb314438202525da459aa`。所有文件列表存于发布目录 `compose-files.json`。
4. 再核对 `/sdk/`、行政服务完整静态文件哈希、平台健康状态和 Nginx 权限错误。业务数据不随应用回退。
