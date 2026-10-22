# SDK 禅道直接入口

先前的 [CSV 代录和禅道实例接入](../../docs/reviews/2026-10-20-sdk-plan-and-zentao-production.md)属于历史试运行。当前目标是启用禅道原生需求管理。正式平台发布需保留 HR、全景、DAQ 等全部 Compose 覆盖；不能使用单文件 `cloud_env.py activate` 重建当前生产栈。

本目录提供独立的禅道配置；禅道数据与原平台数据库分开。`SDK_ZENTAO_IMAGE` 必须显式指定经过核验的加固镜像，Compose 不再允许默认使用未加固的 ZenTao 22.6。`Dockerfile.hardened` 从固定上游 digest 构建，并在源文件摘要不符时停止构建。MariaDB 镜像也应固定 digest；发布前备份两个卷。

禅道容器配置 `ZT_DEFAULT_LANG=zh-cn` 和 `ZT_WEB_ROOT=/sdk`。代理转发给禅道的请求统一设置 `Accept-Language: zh-cn`。这保证浏览器默认英文时，新会话仍以简体中文打开；用户手动选择的语言 Cookie 优先于该请求头。

## 原生需求入口发布

需求、评审、任务、Bug、测试、版本与发布继续保存在禅道员工账号下。`https://agent.orbbec.com.cn/sdk/` 直接展示禅道完整界面，不再运行平台自建 SDK 页面，也不把浏览器跳转到禅道 IP。旧平台需求、年度 CSV 和候选证据 API 在 `PLATFORM_SDK_NATIVE_ONLY=1` 时返回 410，历史数据留在数据库中。此模式尚无 GitLab 构建集成，不能据此声称 SDK 包或真机测试通过。

在正式主机从本目录构建加固镜像：

```bash
docker build -f deploy/sdk/Dockerfile.hardened -t orbbec-sdk-zentao:22.6-cve-2026-111346-verified deploy/sdk
docker image inspect orbbec-sdk-zentao:22.6-cve-2026-111346-verified --format '{{.Id}}'
```

将已核验镜像的 `sha256:<image-id>` 填入权限 `0600` 的私有 `sdk.env` 的 `SDK_ZENTAO_IMAGE`，然后用生产私有 Compose 重新创建禅道。Compose 强制使用镜像内的启动校验脚本；控制器文件摘要不符时容器无法启动。核对容器实际镜像 ID 等于构建结果，登录后访问 `action::editComment` 必须返回 403。升级禅道时重新评估补丁及控制器摘要。

`/sdk/` 首先核对钉钉会话、SDK 准入和个人账号，生成只限 `/sdk/` 的 `zentaosid`，随后打开禅道首页。禅道每个页面和静态资源都通过平台会话及准入检查；代理只向禅道转发禅道会话、语言和界面偏好 Cookie，平台会话 Cookie 不送给禅道服务端。现有 Q4 需求与其他禅道业务数据原位保留。当前生产样例需求、任务和用例均以 `SDK-SCENE-TEST-20261020` 为前缀，不算正式业务需求。

`/sdk/` 与平台 API 属于同一浏览器源；路径后缀不能阻止禅道页面脚本请求平台 API。平台注销后，边缘代理会拒绝继续访问 `/sdk/*`；禅道内部会话不会同步注销。回退时同时恢复发布前的平台 API 镜像、SDK 代理位置块和禅道 `ZT_WEB_ROOT`，**不删除禅道数据库或数据卷**。若新平台镜像先于代理切换，旧 SPA 在 `/sdk/` 显示短暂维护提示，避免重复加载同一 URL。生产共享 Nginx 只验证实际运行的完整配置，不对独立配置执行 `nginx -t -c`。

## 钉钉身份进入个人禅道账号

启用 `PLATFORM_SDK_SSO_ENABLED=1` 和 `PLATFORM_SDK_AUTO_PROVISION_ENABLED=1` 后，3D 传感技术研发中心及子部门的成员首次进入时按稳定目录身份自动建立个人禅道账号并授予工作区访问，无需逐人添加。平台 API 以该个人账号换取禅道浏览器会话。已有人工绑定保持有效；`admin`、`sdkadmin`、`sdk-api` 禁止作为个人绑定。人员离开准入范围时由既有访问同步机制撤权。原有跨源票据入口暂保留作回退路径。

年度计划的 67 条既有需求按文件摘要、行标识和禅道导入标识核对后原位补全。责任人仅在钉钉目录中唯一匹配时才写入禅道“指派给”；多人、别名和存疑姓名保留原文并标待确认。原文件没有提出人列，不把导入账号或责任人伪装成提出人。开始、结束时间保存为结构化来源日期，同时保留禅道需求正文中的原始时间；它们不是需求创建日期。可重跑的修复工具为 `python -m app.sdk_workbench.annual_reconcile --sha256 <文件摘要>`，默认只读，确认报告后添加 `--apply`。

禅道“二次开发 → 接入禅道”创建专用 `freePasswd=1` 入口，`code` 与 `PLATFORM_SDK_SSO_ENTRY_CODE` 相同，32 字符随机 `key` 只保存于禅道和平台 `platform-sdk-secrets` 卷的 `/run/sdk-secrets/sso-entry-key`，后者须属主 `10001:10001`、权限 `0600`。入口 IP 白名单只允许平台 API 在 `orbbec-agent-platform-sdk` 私网的固定地址，禁止 `*`。旧跨源票据入口使用的 `PLATFORM_SDK_SSO_PUBLIC_ORIGIN` 仍是禅道 IP HTTPS 源；新 `/sdk/` 入口不依赖该跨源表单。禅道 IP vhost 继续封锁公网 `/api.php`。

`/sdk/` 代理保留禅道原生页面策略，并屏蔽公网 `/sdk/api.php`、安装和升级入口。禅道使用 `/sdk` 作为 Web Root 后，静态资源和 Cookie 路径均随之调整。切换前核对表单与附件操作是否依赖被屏蔽的公网 API。

核对顺序：匿名 `/sdk/` 跳转钉钉登录；无绑定或无 SDK 权限不能读取 `/sdk/*`；绑定员工进入后禅道首页显示同一员工、能访问其授权产品；另一员工不能借用该会话；平台登出后 `/sdk/*` 被拒绝；行政服务脚本与静态资源内容正常。生产修改 Nginx 前先在独立容器校验候选配置；共享主机不得用独立配置运行 `nginx -t -c`。

## 验收顺序

1. 备份禅道数据库和数据卷，记录镜像 ID、产品/项目/执行 ID。
2. 用员工账号验证：登录前不能读取产品数据；获得产品权限后可新建需求、提交评审、关联任务和用例；无权限账号仍不能查看产品。
3. 核对 `action::editComment` 返回 403，`sdk-api` 服务账号不用于浏览器登录。
4. 核对 `https://agent.orbbec.com.cn/sdk/` 的钉钉登录跳转；登录后原域名内显示禅道需求列表、2026 Q4 来源、需求详情、创建、编辑、指派、评审、我的 SDK 任务、Bug、测试、版本和发布。平台旧需求、CSV、候选 API 在原生模式应返回 410，`/api/v1/sdk/me` 应返回 `native_only: true`。
5. `/sdk/` 页面交互由业务方按仓库 `AGENTS.md` 验收。GitLab、打包产物和真机测试没有完成时，不标记发布证据通过。

需求变更请走禅道原生界面并选择评审人。v2 `PUT /stories/:id/change` 省略 `status` 时，本实例会让变更后的需求保持 `active`，不能作为强制评审入口。产品负责人也可以在原生界面选择“不需要评审”；如果团队要求每次变更必须由其他人评审，需另设产品负责人与评审人并调整评审策略。[禅道需求评审说明](https://www.zentao.net/book/zentaopms/887.html)、[v2 变更接口](https://www.zentao.net/book/api/put-stories-storyid-change-2171.html)。

官方参考：[Docker 部署](https://www.zentao.net/book/zentaopms/docker-1111.html)、[API v2 登录](https://www.zentao.net/book/api/post-users-login-2142.html)、[创建需求](https://www.zentao.net/book/api/post-stories-2169.html)、[创建任务](https://www.zentao.net/book/api/post-tasks-2207.html)。[漏洞记录](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/111xxx/CVE-2026-111346.json)。
