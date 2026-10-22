# SDK 工作台禅道试运行

先前的 [CSV 代录和禅道实例接入](../../docs/reviews/2026-10-20-sdk-plan-and-zentao-production.md)属于历史试运行。当前目标是启用禅道原生需求管理。正式平台发布需保留 HR、全景、DAQ 等全部 Compose 覆盖；不能使用单文件 `cloud_env.py activate` 重建当前生产栈。

本目录提供独立的禅道配置；禅道数据与原平台数据库分开。`SDK_ZENTAO_IMAGE` 必须显式指定经过核验的加固镜像，Compose 不再允许默认使用未加固的 ZenTao 22.6。`Dockerfile.hardened` 从固定上游 digest 构建，并在源文件摘要不符时停止构建。MariaDB 镜像也应固定 digest；发布前备份两个卷。

禅道容器配置 `ZT_DEFAULT_LANG=zh-cn`，代理转发给禅道的请求统一设置 `Accept-Language: zh-cn`。这保证浏览器默认英文时，新会话仍以简体中文打开；用户手动选择的语言 Cookie 优先于该请求头。

## 原生需求入口发布

原生模式把需求、评审与任务保存在禅道员工账号下；平台 `/sdk/` 直接呈现需求管理和“我的任务”界面，不再把浏览器跳转到禅道 IP。旧平台需求、年度 CSV 和候选证据 API 在 `PLATFORM_SDK_NATIVE_ONLY=1` 时返回 410，历史数据留在数据库中。此模式尚无 GitLab 构建集成，不能据此声称 SDK 包或真机测试通过。

在正式主机从本目录构建加固镜像：

```bash
docker build -f deploy/sdk/Dockerfile.hardened -t orbbec-sdk-zentao:22.6-cve-2026-111346-verified deploy/sdk
docker image inspect orbbec-sdk-zentao:22.6-cve-2026-111346-verified --format '{{.Id}}'
```

将已核验镜像的 `sha256:<image-id>` 填入权限 `0600` 的私有 `sdk.env` 的 `SDK_ZENTAO_IMAGE`，然后用生产私有 Compose 重新创建禅道。Compose 强制使用镜像内的启动校验脚本；控制器文件摘要不符时容器无法启动。核对容器实际镜像 ID 等于构建结果，登录后访问 `action::editComment` 必须返回 403。升级禅道时重新评估补丁及控制器摘要。

当前入口为 `https://agent.orbbec.com.cn/sdk/`。浏览器只使用平台钉钉会话；平台 API 在服务端换取当前员工的个人禅道会话并调用私有禅道 API。禅道原生界面继续作为管理员和高级操作入口，但日常需求不依赖独立浏览器源。当前生产样例需求、任务和用例均以 `SDK-SCENE-TEST-20261020` 为前缀，不算正式业务需求。

## 钉钉身份进入个人禅道账号

启用 `PLATFORM_SDK_SSO_ENABLED=1` 和 `PLATFORM_SDK_AUTO_PROVISION_ENABLED=1` 后，3D 传感技术研发中心及子部门的成员首次进入时按稳定目录身份自动建立个人禅道账号并授予工作区访问，无需逐人添加。需求页面通过该个人账号访问禅道私有 API；会话仅在平台服务端缓存。已有人工绑定保持有效；`admin`、`sdkadmin`、`sdk-api` 禁止作为个人绑定。人员离开准入范围时由既有访问同步机制撤权。原有表单票据入口保留给禅道高级界面使用。

年度计划的 67 条既有需求按文件摘要、行标识和禅道导入标识核对后原位补全。责任人仅在钉钉目录中唯一匹配时才写入禅道“指派给”；多人、别名和存疑姓名保留原文并标待确认。原文件没有提出人列，不把导入账号或责任人伪装成提出人。开始、结束时间保存为结构化来源日期，同时保留禅道需求正文中的原始时间；它们不是需求创建日期。可重跑的修复工具为 `python -m app.sdk_workbench.annual_reconcile --sha256 <文件摘要>`，默认只读，确认报告后添加 `--apply`。

禅道“二次开发 → 接入禅道”创建专用 `freePasswd=1` 入口，`code` 与 `PLATFORM_SDK_SSO_ENTRY_CODE` 相同，32 字符随机 `key` 只保存于禅道和平台 `platform-sdk-secrets` 卷的 `/run/sdk-secrets/sso-entry-key`，后者须属主 `10001:10001`、权限 `0600`。入口 IP 白名单只允许平台 API 在 `orbbec-agent-platform-sdk` 私网的固定地址，禁止 `*`。`PLATFORM_SDK_SSO_PUBLIC_ORIGIN` 必须是禅道实际 HTTPS 源。工作区页面 CSP 的 `form-action` 只额外允许该源，进入 SDK 时必须加载 `/sdk/` 文档以取得该策略。禅道 vhost 只代理精确 `/sdk-sso/consume`，继续封锁公网 `/api.php`。

`/sdk/` 代理必须隐藏上游的 CSP 与 Referrer-Policy，仅保留边缘配置的 `form-action` 禅道源和 `strict-origin-when-cross-origin`。上游 `form-action 'none'` 会阻断表单；`no-referrer` 会使 Chrome 的跨源 POST 发出 `Origin: null`。

核对顺序：匿名 `POST /api/v1/sdk/sso/start` 为 401；无绑定或无 SDK 权限不能领票；票据重放或过期不能创建会话；绑定员工经平台钉钉会话点击后，禅道首页显示同一员工、能访问其授权产品；另一员工不能借用该会话；行政服务脚本与静态资源内容正常。平台登出不会即时注销已经创建的禅道会话；此范围只实现登录。生产修改 Nginx 前先在独立容器校验候选配置；共享主机不得用独立配置运行 `nginx -t -c`。

## 验收顺序

1. 备份禅道数据库和数据卷，记录镜像 ID、产品/项目/执行 ID。
2. 用员工账号验证：登录前不能读取产品数据；获得产品权限后可新建需求、提交评审、关联任务和用例；无权限账号仍不能查看产品。
3. 核对 `action::editComment` 返回 403，`sdk-api` 服务账号不用于浏览器登录。
4. 核对 `https://agent.orbbec.com.cn/sdk/` 的钉钉登录跳转；登录后原域名内显示需求列表、2026 Q4 来源、需求详情、创建、编辑、指派、评审和我的 SDK 任务。平台旧需求、CSV、候选 API 在原生模式应返回 410，`/api/v1/sdk/me` 应返回 `native_only: true`。
5. `/sdk/` 页面交互由业务方按仓库 `AGENTS.md` 验收。GitLab、打包产物和真机测试没有完成时，不标记发布证据通过。

需求变更请走禅道原生界面并选择评审人。v2 `PUT /stories/:id/change` 省略 `status` 时，本实例会让变更后的需求保持 `active`，不能作为强制评审入口。产品负责人也可以在原生界面选择“不需要评审”；如果团队要求每次变更必须由其他人评审，需另设产品负责人与评审人并调整评审策略。[禅道需求评审说明](https://www.zentao.net/book/zentaopms/887.html)、[v2 变更接口](https://www.zentao.net/book/api/put-stories-storyid-change-2171.html)。

官方参考：[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[API v2 登录](https://www.zentao.net/book/api/post-users-login-2142.html)、[创建需求](https://www.zentao.net/book/api/post-stories-2169.html)、[创建任务](https://www.zentao.net/book/api/post-tasks-2207.html)。[漏洞记录](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/111xxx/CVE-2026-111346.json)。
