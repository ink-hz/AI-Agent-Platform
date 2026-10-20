# SDK 禅道单点登录上线记录（2026-10-20）

## 范围与版本

用户要求把禅道个人账号接入 AI Agent Platform 的钉钉登录。上线源码准确提交为 `cb2f97d7bbfa5b4a56b8544f50699d23ecc38cbd`，生产 API 镜像 ID 为 `sha256:9feb38fd5377960a368920e791eb7bd885d6712411c22261cf4624252c8bfd85`。平台 `/sdk/` 使用一次性票据进入个人禅道会话；禅道继续决定需求、任务和产品权限。平台登出不即时注销既有禅道会话。

生产仍以旧全栈 `current` 为基线，并叠加 HR、全景、DAQ 等现行 Compose 配置。全栈 `deploy.sh` 不适合这个运行态；本次从正在运行的 API 标签读取 12 份有序 Compose 文件，用 `overlay_guard.py` 对比实际有效配置，仅允许 API 镜像、发布号和四项 SSO 参数变化。守卫确认 9 项服务配置，API 重建后其他 7 个运行容器 ID 未变。`platform.env` 保存非秘密 SSO 开关、禅道 HTTPS 源和接入码；专用密钥仅在 root 私密文件、禅道数据库及平台只读私密卷中，未写入仓库或环境变量。

## 备份、迁移与配置

- 私有备份：`/data/orbbec-agent-platform/backups/sdk-sso-20261020-cb2f97d7/`，含平台和禅道 Nginx 原配置、原环境、API 容器快照、生产与预览控制库转储、禅道 SQL 转储及 SHA-256 清单。两份控制库转储已用运行中 PostgreSQL 对应版本的 `pg_restore -l` 核验。
- 控制库迁移 118 已应用于预览和生产，迁移程序的临时 owner 角色授权已撤销。平台内部用户 `05259561-9032-4f4d-a63c-8c4292b897cf` 经 SDK 成员、有效钉钉身份和活跃平台所有者状态核对后，绑定禅道个人账号 `cangyuan`。这是一次运维初始化；后续使用受 CSRF 保护的所有者绑定 API。
- 禅道专用接入项 `sdk-sso` 的 `freePasswd=1`，IP 白名单为平台 SDK 私网地址 `172.23.0.3`；公网 `/api.php` 精确路径继续拒绝。平台代理只新增 `/sdk/` 专用 CSP，禅道代理只新增精确 `/sdk-sso/consume` 和 `/api.php` 拒绝规则。两份生产候选配置的 diff 只有这些新增块，在独立 Nginx 容器校验后，用生产完整配置的默认 `nginx -t` 检验、受控重载；共享代理临时目录在前后均为 `www-data:700`。未使用 `nginx -t -c` 在生产机测试独立配置。

## 验证与边界

- 本地后端相关回归 `160 passed, 1 skipped`；前端 SDK 组件 `10 passed`、生产构建通过。一个跳过项是本机缺少原生 Nginx 可执行文件，生产候选配置另在隔离容器中通过语法检查。
- 先经平台所在私网源调用真实禅道免密入口，取得 `cangyuan` 会话，带该会话访问首页为 200 且显示该员工。随后在生产选取该员工现有有效平台会话，通过平台票据仓库签发单次票据，走公开禅道 HTTPS `POST /sdk-sso/consume`，得到 303 和 `Secure; HttpOnly; SameSite=Lax` 个人会话；再次提交同票据为 403 且不设置 Cookie。此测试覆盖生产票据库、私网禅道、公开代理和浏览器会话 Cookie，但票据由运维程序签发，**没有代替员工实际钉钉浏览器点击 `/sdk/` 的验收**。
- 公开平台健康和 `/sdk/` 为 200；匿名签发为 401；无效票据为 403；公网旧 `apilogin` 为 403。SDK JS 资源经公开代理与本机上游逐字节相同，大小 1,089,429 字节；行政服务 JS 同样逐字节相同，大小 534,098 字节。API healthy，Nginx 近期权限错误日志为 0。生产 Brain 在本次发布前已是 unhealthy，本次未替换该容器。

员工实际钉钉登录后点击 `/sdk/` 入口的浏览器验收仍需用户执行；其余生产接口及会话链路已按上述范围验证。GitLab、SDK 构建和真机测试集成仍未纳入此功能。

## 浏览器交接热修（同日）

员工首次点击时，`POST /api/v1/sdk/sso/start` 已返回 200，但禅道没有收到表单。根因是 `/sdk/` 同时返回上游 `form-action 'none'` 与边缘允许禅道的 CSP；浏览器同时执行两条策略，阻断提交。隐藏上游 CSP 后，浏览器提交到禅道，但返回 `request origin rejected`。独立 Chrome 场景复现确认 `/sdk/` 的 `Referrer-Policy: no-referrer` 会使跨源表单的 `Origin` 为 `null`。因此 `/sdk/` 仅保留边缘 CSP 和 `strict-origin-when-cross-origin`，其他工作区策略不变。前端增加 CSP 阻断提示与 15 秒超时，避免按钮一直停在提交态。

修复提交 `f864dc9691ff3b8736ea5f662ff9a263cabebbe9` 已合入主线并部署，API 镜像 ID 为 `sha256:258008881750ddf35cc64ffe352c91d54cb83e49640af2d873ab961db7cf58af`。新 API 发布前 `overlay_guard.py` 核对 13 个原 Compose 文件、9 个服务配置；仅 API 容器重建，其他 7 个运行容器 ID 未变。API 健康，公网 `/sdk/`、`/api/health`、`/office/` 为 200；新 SDK JS 和行政服务 JS 的公网哈希分别与镜像、行政服务上游一致。Nginx 的完整配置校验与重载后，代理临时目录仍为 `www-data:700`，没有新增权限错误。回滚参考 `/data/orbbec-agent-platform/backups/sdk-sso-hotfix-f864dc96/` 的原 API 容器配置和原环境；Nginx 两次单项改动的原件分别在 `/root/nginx-backups/sdk-sso-csp-20261020T033749Z.conf`、`/root/nginx-backups/sdk-sso-origin-20261020T034042Z.conf`。

生产浏览器从线上 `/sdk/` 提交无效测试票据时，发出 `Origin: https://agent.orbbec.com.cn`，禅道接票接口返回 `sdk_sso_ticket_invalid`，证明 CSP、浏览器导航和来源校验已贯通；此测试没有创建禅道会话。员工本人再次点击并确认个人会话仍是待验收项。

用户希望使用 `orbbec.com.cn/sdk`。该地址当前返回 404；`orbbec.com.cn` 解析到 `120.25.206.12`，平台服务器为 `47.106.112.69`。官网入口需由官网服务器或其发布系统配置；平台服务器的 Nginx 改动不能接管该路径。是否作为入口重定向，或要求禅道会话全程保留该路径，待业务方确定。

## 入口地址更正与默认语言

用户随后将入口地址更正为 `https://agent.orbbec.com.cn/sdk`。公网该地址返回 301 到 `/sdk/`，最终页面的 CSP 允许向禅道源提交单次票据；它是平台钉钉身份入口，完整禅道页面仍在独立浏览器源。审查确认，把完整禅道代理到平台同源 `/sdk/` 会使禅道脚本取得主平台 API 的同源访问能力，因此该方案未上线。拟用独立企业子域名承载禅道；`sdk.orbbec.com.cn` 的 DNS 尚未配置，域名切换仍待外部 DNS 生效。

默认语言修复源码提交 `10d7235525d699f141d3309916f8af0ed0ee0c55` 已合并并推送主线。生产禅道私有 Compose 增加 `ZT_DEFAULT_LANG=zh-cn`，没有重建运行容器；运行实例此前已有中文回退配置。独立禅道 IP 入口的 Nginx 代理增加 `Accept-Language: zh-cn`，配置 SHA-256 为 `7812a19f583686657d99f2c7baa8b3d69c62494d54876b5165c4ef99c210f4fb`。变更前备份在 `/data/orbbec-agent-platform/backups/sdk-domain-path-20261020-5ba6dc88/`，校验清单全项通过。

隔离 Nginx 容器的语法及运行测试通过；生产只用完整默认 `nginx -t` 检查后重载，未用独立 `-c` 检查。公网新会话携 `Accept-Language: en` 返回禅道 `html lang=zh-cn` 与 `lang=zh-cn` Cookie；显式 `lang=en` Cookie 返回英文。平台 `/api/health`、`/sdk/`、`/office/` 均为 200，SDK 与行政服务 JavaScript 的完整 SHA-256 与变更前相同。代理临时目录仍为 `www-data:www-data 700`，Nginx 近五分钟日志无权限错误。
