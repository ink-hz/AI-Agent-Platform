# SDK 入口直接承载禅道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `https://agent.orbbec.com.cn/sdk/` 切换为钉钉免密进入的禅道完整界面，保留既有业务数据。

**Architecture:** 精确 `/sdk/` 由平台 API 生成当前员工的个人禅道会话；`/sdk/*` 由 Nginx 在平台会话和 SDK 准入检查后代理到设置 `ZT_WEB_ROOT=/sdk` 的禅道。代理仅转发禅道 Cookie。切换、测试及回退限定到 SDK 路由和禅道环境。

**Tech Stack:** FastAPI、httpx、Nginx、Docker Compose、pytest、Vitest。

## Global Constraints

- 禅道 22.6 继续作为需求、任务、Bug、用例、测试、版本和发布的数据源；现有 Q4 需求原位保留。
- 平台及禅道均使用当前员工身份；无 SDK 准入者不得通过直链浏览 `/sdk/`。
- 生产共享 Nginx 不执行独立配置的 `nginx -t -c` 或 `nginx -T -c`。
- 切换前验证行政服务和平台静态资源；上线后核对内容字节与权限错误日志。
- `/sdk/` 与平台 API 同源的残余风险需在发布记录中注明。

---

### Task 1: 个人禅道直接入口

**Files:** `backend/app/sdk_workbench/sso.py`、`backend/app/control_plane/authorization.py`、`backend/tests/test_sdk_sso_routes.py`。

**Interfaces:** `GET /api/v1/sdk/sso/entry` 返回带 `zentaosid; Path=/sdk/; Secure; HttpOnly; SameSite=Lax` 的 303，目标为 `/sdk/index.php?m=my&f=index`；`GET /api/v1/sdk/sso/access` 对当前已绑定准入员工返回 204，其余返回 401/403/503。`entry` 沿用 `native_account()` 与 `login_personal_session()`；`access` 只读取 `repository.identity()`，不触发建号或禅道登录。

- [ ] 先在 `test_sdk_sso_routes.py` 加测试，覆盖准入员工取得个人 Cookie、未准入拒绝、目录过期拒绝、登录失败不设置 Cookie、access 不创建新的禅道浏览器会话；运行测试，确认新接口缺失而失败。
- [ ] 实现两个 GET 路由；只让 `entry` 调用 `login_personal_session`，`access` 只核对绑定与准入；将两条路由纳入 SDK 授权表。
- [ ] 运行 `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest backend/tests/test_sdk_sso_routes.py -q`，要求全部通过。
- [ ] 提交该独立后端变更。

### Task 2: SDK 子路径代理

**Files:** `deploy/cloud/agent-domain.nginx.conf`、`deploy/sdk/compose.yaml`、`backend/tests/test_sdk_cloud_deployment.py`、`backend/tests/test_sdk_direct_zentao_proxy.py`。

**Interfaces:** `/sdk` → `/sdk/`；精确 `/sdk/` → Task 1 入口；`/_sdk_access` 内部转发 Task 1 access；`/sdk/*` → `127.0.0.1:18131`，保留 URI；平台 Cookie 不转给禅道；`/sdk/api.php*`、安装/升级入口对公网拒绝。禅道环境设置 `ZT_WEB_ROOT=/sdk`。

- [ ] 先用配置契约测试断言四条路由、身份校验、Cookie 白名单、`ZT_WEB_ROOT=/sdk`、只影响 SDK 块且没有旧 SDK CSP；运行 pytest，确认失败。
- [ ] 修改 Nginx SDK 位置和禅道 Compose 环境；原有 `/office/`、根 `/`、其他服务位置不改。`auth_request` 401 跳到 `/login?return_path=%2Fsdk%2F`，403 保持拒绝。
- [ ] 在独立 Nginx 容器以独立配置、临时目录、日志和 PID 校验完整候选；使用本地禅道 22.6 加固镜像与临时数据库核对路由、登录页资源和 Cookie 路径，不挂载生产 Nginx 可写目录。
- [ ] 运行配置契约测试并提交。

### Task 3: 旧 SDK 页面退出公网入口

**Files:** `webui/src/App.tsx`、`webui/src/AppShell.sidebar.test.tsx`、`webui/src/router.test.ts`、`deploy/sdk/README.md`。

**Interfaces:** 平台导航继续提供 `/sdk/` 整页链接；若旧平台壳经缓存到达 SDK 路由，整页跳转 `/sdk/`；`/login?return_path=/sdk/` 保持整页返回。运行时不再渲染 `SdkWorkbench`。

- [ ] 先加前端测试证明 SDK 路由只能发起整页导航而不会挂载旧工作台；运行 Vitest 确认失败。
- [ ] 将 SDK 路由改为文档导航，移除运行时 `SdkWorkbench` 引用；更新部署说明、同源风险和回退步骤。
- [ ] 运行前端定向测试及 `npm run build`，提交。

### Task 4: 发布与场景验收

**Files:** `docs/operations/2026-10-22-sdk-direct-zentao-release.md`。

- [ ] 汇总准确源提交、Nginx/禅道/平台容器和数据卷基线；备份禅道数据库与数据卷、现行代理配置和平台环境。对照候选确认非 SDK 服务不变。
- [ ] 在隔离 Nginx 与禅道环境验证匿名登录、员工 SSO、无权限、需求/任务/Bug/测试/发布入口、附件、深链接及旧 IP 回退路径；未通过则不切换。
- [ ] 受控部署并核对平台、行政、`/sdk/` 的 HTTP 状态与完整 JS/CSS 内容摘要；检查权限错误和服务状态。真实员工浏览器的最终交互验收由业务方执行。
- [ ] 任一 SDK 登录或非 SDK 服务检查失败则恢复旧代理块及禅道环境；记录准确结果，不将未验收项写成通过。
