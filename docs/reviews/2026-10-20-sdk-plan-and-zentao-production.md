# SDK 年度计划代录与禅道生产接入记录

时间：2026-10-20 08:15 CST。用户明确要求直接录入 2026 SDK CSV，并启动此前未部署的禅道。GitLab 开发集成仍按现有范围暂缓。

## 2026 计划

- 来源：`OrbbecSDK  v2 2026年任务计划.csv`，SHA-256 `41df018a029f1d27417bec3008bbe7aab3becd785cc38b312c563ce18ee8370d`。
- 使用平台现有 CSV 解析器和 `PlanRepository.import_plan` 以用户“苍渊”的内部 ID `05259561-9032-4f4d-a63c-8c4292b897cf` 代录；该操作由用户在会话中明确要求，不宣称经过企业浏览器会话、Origin 或 CSRF 验证。源文件传到私有目录、校验摘要后导入，随后删除临时副本。
- 生产批次 `1`：69 条来源记录，67 条任务、2 条目录行、17 个模块。批次 SHA 唯一约束提供重复导入去重。生产数据库读回为 1 个批次、69 条记录。
- 用现有 `SdkRepository.grant_member` 授予该用户 `contributor`，`sdk.member_events` 留下 1 条成员授权事件；生产读回角色为 `contributor`。

## 禅道与平台

- 此前生产只有年度计划入口：无禅道容器、无 SDK 网络与专用服务账号。现部署禅道开源版 22.6，镜像 `sha256:d7266f4cd4a5da594c987bcdaaac6c74b4a6a2946384cc378dc949b69ea0b99e`；MariaDB 10.6，镜像 `sha256:88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461`。数据库健康；禅道原生 HTTP 仅绑定主机 `127.0.0.1:18131`，SDK 网络为 Docker internal 网络。
- 首次安装完成，正式产品 `OpenOrbbecSDK` ID `1`、项目 ID `1`、执行 ID `2`。`sdk-api` 独立账号使用产品与执行的 `private` ACL 白名单、研发与产品经理权限组。管理员和服务账号密码分别存于生产主机私有文件；平台账号密码复制至只读卷 `/run/sdk-secrets/password`，文件归属 `10001:10001`、权限 `0600`。数据库密码在初始化后的诊断输出中出现过，已轮换并验证服务账号重新登录成功。
- 平台修复固定 Docker 主机名 `http://zentao:80` 的校验错误：提交 `1257435c4eb44589b4bb6ada24fd985670830c4c`，镜像 ID `sha256:0764532dad20ee903f7a713facdc791d957a1a0714d6793dc7aa0119ef86fec7`。SDK 测试 `61 passed, 3 skipped`。修复前用新镜像先从私网以固定主机名读取禅道产品与执行，结果通过。
- 生产 API 使用原有 9 份 Compose 文件加 1 份 SDK 覆盖文件重建，最终容器 ID `215e902f5439d59876a03c2d233d3214df8920682f035fb9e1ab83040d4654a3`，健康。切换前有效配置比较仅允许 API 镜像、SDK 环境、SDK 网络和只读密钥挂载变化；其他 8 个服务及 Brain、loopback 容器 ID 保持不变。`PLATFORM_SDK_PLAN_ENABLED=1`、`PLATFORM_SDK_WORKBENCH_ENABLED=1`。

## 场景与限制

- 真禅道服务账号写入：需求 ID `1` 为 `active`，任务 ID `1` 关联该需求及执行 ID `2`；需求标题、正文、验收标准和任务正文均按原值读回。这是接口部署验收记录，明确标记 `SDK-DEPLOY-CHECK-`，不代表 SDK 构建、单测或硬件测试通过。
- 新生产 API 容器内读回计划批次、成员角色、禅道产品与执行以及上述需求和任务，结果通过。首次发布时公网未登录 `/sdk/` 和 `/api/v1/sdk/me` 均返回 401；随后确认 `/sdk/` 的 401 阻断浏览器登录流程，见下方修复记录。企业真实会话、CSRF 与页面操作由用户按 `AGENTS.md` 进行最终验收；本次未声称通过。
- 生产切换前备份位置：`/opt/orbbec-agent-platform/backups/sdk-full-20261020-1257435c/`，含生产/预览控制库、自身环境文件原件、禅道 SQL 与数据卷归档，权限仅 root。回滚需使用切换前 9 份 Compose 文件及 `platform.env.before`，不能用基础 Compose 单独重建。

## SDK 登录入口修复（2026-10-20 09:05 CST）

- 用户反馈 `https://agent.orbbec.com.cn/sdk/` 直接显示 `{"detail":"authentication required"}`。公网复现为 401 JSON；后端已有 `/sdk/` 页面路由，但中间件公开页面列表、后端登录回跳白名单、前端回跳白名单均缺少 SDK 路径。修复提交 `2ef4386a663e2997fc76ef45ef29010ebfef5486` 已推送主线。
- 测试先失败后修复：后端 SDK 登录测试原为 5 失败、4 通过，前端 SDK 回跳测试原为 3 失败。修复后后端相关测试 `115 passed`，前端登录与路由测试 `178 passed`，前端构建通过。原有业务 API 未登录 401 保持不变。
- 从该提交构建生产镜像 `sha256:347f0438882e8ca2559b167e354867214897dc53031ff000b3702957b2be7fa0`。使用原有 10 份 Compose 文件加 SDK 登录镜像覆盖文件切换 API，有效配置预检返回 `SDK_OVERLAY_CONFIG_OK`，9 个服务仅 API 镜像和发布 SHA 变化；新 API 容器 `0b6d6071a06c651fb3fbd8d6430b904ea91cf77c0b57d943cfc8424fca73c8eb` 健康，其他服务容器 ID、API 网络和挂载未变。回滚时使用切换前原有 10 份 Compose 文件与原 API 镜像。
- 新容器内 `/sdk/`、`/sdk/requirements/1`、`/hr/` 均 200 HTML，`/api/v1/sdk/me` 仍 401 JSON。公网再次验证 `/sdk/`、`/sdk`、SDK 详情及 `/login` 均 200 HTML，HTML 含 `platform-identity-mode=enabled`，JS 资源 200；SDK API 和未知 `/sdk/other` 仍 401 JSON。未执行浏览器中的真实企业身份登录与页面操作，按 `AGENTS.md` 留给用户验收。
- 禅道 22.6 已有公开授权绕过漏洞 [CVE-2026-111346](https://osv.dev/vulnerability/CVE-2026-111346)。保持原生界面仅本机可达、BFF 私网访问；升级至上游修复版本前不得开放禅道公网入口。
