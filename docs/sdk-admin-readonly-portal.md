# SDK 开发管理门户：首期只读资料版

`/sdk-admin` 提供总览、产品与版本、研发与交付、诊断与改进、知识与成果五个入口。`/sdk/` 保持现有直达禅道的行为。首期整合已产生的知识、流程与分析资料；实时产品/版本、待办、跨系统证据链、设备诊断及 SDK AI 执行另行接入。

## 内容发布包

原知识库与源码只读，内容导出器按冻结验收清单校验原件后生成独立发布包。运行需要 Python 3.11 和工程已有 PyYAML。

```sh
cd backend
python -m tools.sdk_admin.build_content_release \
  --platform-root /path/to/AI-SDK-Platform \
  --output /path/to/releases/sdk-admin-unique-version
```

输出目录必须不存在。首期包包含 865 正式知识页、19 导航/目录和9篇显式选取的项目资料；完整原始阅读正文、设备/平台/固件/状态条件及来源摘要保留。manifest、正文和包内链接共同校验；损坏时 API 返回不可用，不回落示例。

发布包独立于前端静态资源，不能放入公开 static 目录。运行时设置 `SDK_ADMIN_CONTENT_ROOT=/real/path/to/releases/sdk-admin-unique-version`，进程需要该目录及文件的只读权限。容器部署按版本目录只读挂载，并将环境变量指向容器内版本目录；此文不配置或执行生产挂载。路径及父目录不能是软链（macOS 使用 `/private/tmp` 而非 `/tmp`）。同一进程使用已校验不可变快照；切换发布包后重启相应进程，不原地替换正在使用的文件。

`generated_at` 是资料包快照时间。原项目文档之后可能被其他会话更新；旧快照保留原摘要和边界，不自动代表当前业务状态。知识静态验收与 SDK/设备运行验证分开，`not_run` 不显示为运行通过。

## 身份与接口

企业登录与会话沿用平台。各接口独立核定既有 SDK viewer / contributor / reviewer / release_manager 角色，平台 owner 可读共享 SDK 资料；其他平台角色不自动获得权限。不新增成员授权、不调用禅道开通、不写业务系统。

- `GET /api/v1/sdk-admin/access`：只读访问判定，无需内容包存在。
- `GET /api/v1/sdk-admin/catalog`：准确发布、分组、统计、来源接入及 AI 状态。
- `GET /api/v1/sdk-admin/documents`：按类型/分组/完整正文搜索和分页，q≤200，limit≤100。
- `GET /api/v1/sdk-admin/documents/{id}`：正文、基线、条件、摘要与包内链接。

匿名不返回内容。撤销成员权限后下一请求拒绝读取；身份中间件与 SDK 子路由均保留。SDK admin 名称不要求所有读者是平台管理员。页面根路径和五入口/知识ID的登录回跳精确匹配，API未设为公开资源。AI状态明确不可用，本期没有真实模型调用。

## 本地验证

接口和后台优先，浏览器验收由用户执行。常规定向验证：

```sh
cd backend
python -m pytest tests/test_sdk_admin_entry.py tests/test_sdk_admin_export.py tests/test_sdk_admin_routes.py -q
```

```sh
cd webui
npm test -- src/sdk-admin/sdkAdminApi.test.ts src/sdk-admin/SdkAdminPage.test.tsx src/router.test.ts src/auth.test.ts src/AppShell.sidebar.test.tsx
npm run build
```

`SdkAdminContent.test.tsx` 是可选的真实发布包响应契约验收：用本项目外的 `verify-real-content.py` 经本地 ASGI API 读取真实包并生成响应 fixture，再设置 `SDK_ADMIN_HTTP_FIXTURE=/absolute/path/real-content-http-fixture.json` 运行该测试。它使用真实正文及 API 响应，身份和浏览器请求边界是本地模拟；不代表企业 SSO、真实浏览器、SDK运行或生产验证。

无内容包、损坏包、未知ID、无权限和网络失败分别呈现真实状态。源代码/证据未纳入包时显示原引用路径；不生成不可访问的本机文件链接。新包的阅读与检索接入验收不改变原知识库静态验收结论。
