# Agent Platform 统一 Agent 入口实施计划

> 执行方式：在 `feat/agent-brain-conversations` 隔离工作树内逐项 TDD 实施；每个任务先写失败测试，再做最小实现，再提交。AI ADMIN `/office` 应用改造由其独立仓库执行，本计划负责 Platform 侧契约、入口、身份与 Nginx 所有权。

**目标：** 在不提前启用 Brain V2 的前提下，交付统一 Agent 目录、HR/Marketing 直接会话、FAE/行政专业入口、统一企业身份和可回滚发布门禁。

**基线设计：** `docs/superpowers/specs/2026-08-24-agent-platform-unified-agent-entry-design.md`

**技术栈：** FastAPI、PostgreSQL、React/TypeScript、Vitest、Pytest、Nginx、Docker Compose。

---

## Task 1：完成主线合并并建立绿色基线

**文件：**
- 修改：当前 merge index 中列出的文件
- 测试：`backend/tests/`、`webui/src/**/*.test.tsx`

1. 运行 `git diff --check && git status --short --branch`，确认无冲突标记且只保留已知 merge、账号入口和文档改动。
2. 运行 `.venv/bin/python -m pytest`，失败时只修复由当前合并产生的回归。
3. 运行 `cd webui && npm test -- --run && npm run build`。
4. 再运行 `git diff --check`，提交 merge 与已验证的账号入口改动：`git commit -m "merge: integrate latest platform identity changes"`。

## Task 2：根路径始终是使用入口

**文件：**
- 修改：`backend/app/control_plane/routes_auth.py`
- 修改：`webui/src/App.tsx`
- 新增：`webui/src/pages/BrainPreparingPage.tsx`
- 修改：`webui/src/AppShell.tsx`
- 修改：`deploy/cloud/accept.sh`
- 测试：`backend/tests/test_dingtalk_auth_api.py`
- 测试：`webui/src/AppShell.brain.test.tsx`
- 新增：`webui/src/BrainPreparingPage.test.tsx`

1. 先写测试：已登录且 Brain 关闭时 `GET /` 返回 200，不再 302 `/admin`，并带 `X-Platform-Entry-State: brain-preparing`。
2. 先写前端测试：根路径显示准备页；点击右上角“苍渊”进入 `/account`；管理中心只通过管理员导航进入。
3. 最小实现根路径壳与准备页，不改变 Brain 开启时的 `BrainWorkspacePage`。
4. 将 `accept.sh` 的回滚断言从 `302 Location: /admin` 改成 `200 + brain-preparing` 标记。
5. 运行相关 Pytest、Vitest 和构建后提交：`git commit -m "feat: keep root path as the agent use entry"`。

## Task 3：建立唯一规范的 8-Agent Catalog

**文件：**
- 修改：`backend/app/agent_brain/capabilities.yaml`
- 新增：`backend/app/catalog/models.py`
- 新增：`backend/app/catalog/repository.py`
- 新增：`backend/app/catalog/__init__.py`
- 修改：`backend/app/fleet/catalog.py`
- 测试：`backend/tests/test_fleet_catalog.py`
- 新增：`backend/tests/test_agent_catalog.py`

1. 写失败测试，规范集合必须恰好包含 HR 1 个、Marketing 5 个、FAE 1 个、AI ADMIN 1 个。
2. 写失败测试，`feishu-default`、`fae-bot`、`Test Bot`、`Codex Assistant`、个人 Workspace 和历史别名不得成为规范卡片。
3. 扩展能力卡字段：`domain_group`、`interaction_modes`、`workspace_url`、可空 `adapter_kind`、`dispatchable`；外部工作区固定为 FAE HTTPS 与同源 `/office/`，不得从 Registry `entry_url` 派生。
4. Fleet 与 Brain 运行时改为消费规范 Catalog 的投影；缺 Adapter 的可调度 Agent 显示 `unavailable`，不得静默删除。
5. 运行测试并提交：`git commit -m "feat: establish the canonical agent catalog"`。

## Task 4：Catalog 与授权 API 脱离 Brain 开关

**文件：**
- 新增：`backend/app/catalog/routes.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/agent_brain/routes.py`
- 修改：`backend/app/agent_brain/authorization.py`
- 测试：`backend/tests/test_agent_brain_deployment.py`
- 新增：`backend/tests/test_agent_catalog_api.py`

1. 写失败测试：`PLATFORM_AGENT_BRAIN_ENABLED=0` 时，已登录用户仍可请求获授权的 Agent 目录。
2. 写失败测试：未授权 Agent 不显示，直接 URL/API 返回 403；外部工作区与直接会话使用同一授权判断。
3. 将 Catalog router 和 `AgentUseAuthorization` 的构造移出 Brain 条件块，删除 Brain router 中重复端点。
4. 保持 Brain API 在开关关闭时不可用，避免意外启用 V1/V2。
5. 运行测试并提交：`git commit -m "refactor: decouple catalog authorization from brain runtime"`。

## Task 5：直接会话脱离 Brain 开关

**文件：**
- 修改：`backend/app/config.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/agent_brain/conversation_repository.py`
- 修改：`backend/app/agent_brain/conversation_routes.py`
- 修改：`deploy/cloud/compose.yaml`
- 测试：`backend/tests/test_agent_brain_deployment.py`
- 测试：`backend/tests/test_agent_brain_conversation_api.py`

1. 新增 `PLATFORM_DIRECT_AGENT_ENABLED`，先写合法开关矩阵测试：Direct 可在 Brain 关闭时开启，但必须有生产身份、控制库、内容加密和 execution relay；Brain V2 仍蕴含 Brain 开启。
2. 写失败测试：Direct 开、Brain 关时可以创建 `mode=direct_agent` Conversation；不能创建 Brain Conversation，也不能改绑 `direct_agent_id`。
3. 把控制库连接、`ContentCodec`、`MissionRepository`/`ConversationRepository` 的直接会话所需依赖移出 Brain gate；仅编排器和 Brain routes 受 Brain gate 控制。
4. 为 HR/Marketing 保留 execution relay 依赖；relay 关闭时配置校验失败，不做本地假成功。
5. 运行测试并提交：`git commit -m "feat: run direct agent conversations without brain"`。

## Task 6：提供最小化的内部会话主体端点

**文件：**
- 修改：`backend/app/control_plane/routes_auth.py`
- 修改：`backend/app/control_plane/middleware.py`
- 修改：`deploy/cloud/agent-domain.nginx.conf`
- 测试：`backend/tests/test_web_session_security.py`
- 测试：`backend/tests/test_dingtalk_auth_api.py`

1. 写失败测试：回环请求 `GET /api/v1/internal/session/subject` 只返回 `{internal_user_id, display_name, active}`。
2. 写安全测试：无论请求携带哪些 Cookie，响应均不含手机号、真实姓名、部门、角色、CSRF、Token 或 `Set-Cookie`；失效身份返回 401，身份后端异常失败关闭。
3. 实现仅信任回环来源的专用端点；公网 Nginx 对该精确路径返回 404。
4. 保持 `/api/v1/account` 给 Platform 自身使用，不再让 AI ADMIN 依赖该超集接口。
5. 运行测试并提交：`git commit -m "feat: add a minimal loopback session subject endpoint"`。

## Task 7：把可信 requester subject 放入 Relay Envelope

**文件：**
- 修改：`backend/app/execution_relay/models.py`
- 修改：`backend/app/execution_relay/repository.py`
- 修改：`backend/app/execution_relay/worker.py`
- 修改：`backend/app/agent_brain/adapters/metabot_local.py`
- 测试：`backend/tests/test_execution_relay_repository.py`
- 测试：`backend/tests/test_agent_brain_metabot_adapter.py`

1. 写失败测试：服务器从已验证 Session 生成 `{internal_user_id, display_name}`，忽略浏览器提交的同名字段。
2. 写兼容测试：旧 Worker 可忽略新字段；新 Worker 可读取字段；payload 继续经 `ContentCodec` 加密落库。
3. 先发布 Worker 兼容读取，再发布 Platform 写入 `requester_subject`；未升级 Worker 明确不启用依赖该身份的业务功能。
4. 保证日志、任务公开事件和错误正文不输出内部用户 ID。
5. 运行测试并提交：`git commit -m "feat: carry verified requester identity through relay"`。

## Task 8：统一专业 Agent 目录和直接使用页

**文件：**
- 修改：`webui/src/App.tsx`
- 修改：`webui/src/pages/AgentUseDirectoryPage.tsx`
- 修改：`webui/src/pages/AgentUsePage.tsx`
- 修改：`webui/src/conversationApi.ts`
- 修改：`webui/src/styles.css`
- 测试：`webui/src/AgentUseDirectoryPage.test.tsx`
- 测试：`webui/src/AgentUsePage.test.tsx`

1. 写失败测试：目录只显示用户获授权的规范卡片，按 HR、Marketing、专业工作区分组。
2. 写失败测试：HR/Marketing 进入已有 `/agents/:agentId` 直接会话；FAE 打开 `https://fae.orbbec.com.cn/`；行政打开同源 `/office/?view=services`。
3. 实现外部工作区白名单导航，禁止 Catalog 数据注入任意 URL。
4. 保持“点击即使用”，目录不显示健康、同步、副本等管理噪音；离线状态用明确文案呈现。
5. 运行 Vitest 与构建后提交：`git commit -m "feat: expose unified professional agent entry points"`。

## Task 9：按 Agent 保留连续会话与 Marketing 切换

**文件：**
- 修改：`webui/src/components/conversation/ConversationSidebar.tsx`
- 修改：`webui/src/pages/AgentUsePage.tsx`
- 修改：`webui/src/conversationApi.ts`
- 修改：`backend/app/agent_brain/conversation_routes.py`
- 测试：`webui/src/components/conversation/ConversationSidebar.test.tsx`
- 测试：`backend/tests/test_agent_brain_conversation_api.py`

1. 写失败测试：会话列表可按 `direct_agent_id` 服务端过滤，普通用户只能看本人会话。
2. 写失败测试：Marketing 页面可以切换五个 Agent；切换 Agent 创建新 Session，不把旧 Session 改绑。
3. 实现 agent filter、左侧历史列表和持续对话恢复；分页游标继续生效。
4. HR 使用同一组件但固定 HR Agent，不额外复制页面或状态机。
5. 运行测试并提交：`git commit -m "feat: add agent-scoped continuous conversation history"`。

## Task 10：建立 Admin Session 的平台身份桥

**文件：**
- 新增：`backend/migrations/011_admin_session_subject_links.sql`
- 修改：`backend/app/sync_remote/importer.py`
- 修改：`backend/app/sync_remote/export.py`
- 测试：`backend/tests/test_observability_migration.py`
- 测试：`backend/tests/test_sync_remote_cli.py`

1. 写失败测试：观测侧以 `(source_kind, native_session_id)` 唯一关联 `internal_user_id`，并记录 `verification_method`、`verified_at`。
2. 写失败测试：无可信 subject 的历史 Session 保持未绑定，不按姓名或发送者文本猜测归属。
3. 实现 `session_subject_links` 和导入协议；AI ADMIN 必须在其仓库导出经 Platform 验证的 `internal_user_id`，否则只进入待治理状态。
4. 展示名继续来自人工名称映射；钉钉名称和手机号不得反向覆盖人工名称。
5. 运行迁移与同步测试后提交：`git commit -m "feat: link admin sessions to verified platform subjects"`。

## Task 11：规范 Registry 关联并加入治理告警

**文件：**
- 修改：`deploy/cloud/registry.yaml`
- 修改：`backend/app/registry/models.py`
- 修改：`backend/app/registry/repository.py`
- 修改：`backend/app/fleet/catalog.yaml`
- 测试：`backend/tests/test_registry_models.py`
- 测试：`backend/tests/test_registry_repository.py`

1. 写失败测试：Registry 只通过 `flywheel_agent_id` 关联规范 Catalog；AI ADMIN 必须是 `ai-admin-agent`。
2. 写失败测试：别名只用于观测投影，`pc-bot`、`quality-bot` 等 unresolved alias 不成为规范 Catalog 的未知 Agent 告警。
3. 移除生产 `<admin-host>` 占位符；`entry_url` 仅用于服务发现和健康检查，不生成用户 `workspace_url`。
4. 未识别的真实 source agent 进入治理告警，不静默增加可使用 Agent。
5. 运行测试并提交：`git commit -m "fix: normalize registry and catalog identity joins"`。

## Task 12：固化迁移、回滚和 `/office` Nginx 门禁

**文件：**
- 修改：`deploy/cloud/agent-domain.nginx.conf`
- 修改：`deploy/cloud/accept.sh`
- 修改：`docs/runbooks/cloud-platform.md`
- 修改：`backend/tests/test_agent_brain_v2_migration.py`
- 修改：`backend/tests/test_agent_brain_deployment.py`

1. 为 042 增加发布前只读查询，发现无 `mission_runs` 对应项或无法判定的 `execution_jobs` 时停止发布并输出人工分类证据，不盲目归类。
2. 在 Platform 所有的唯一 HTTPS Server Block 显式占有 `/admin/` 与 `/office/`；`/office/` 仅代理回环 8011，保留 `/` 和 Platform API。
3. 为 `/office/` 写完整安全头集合；默认 1 MB，仅 Admin 精确附件端点 12 MB，Platform 精确附件端点 50 MB；Admin 自身必须验证严格 Origin/Host/CSRF，来自 FAE 的写请求必须拒绝。
4. 回滚前排空或显式失败在飞 `metabot_local` 任务；Nginx 回滚基线固定为阶段 0 之后，并重新验收 `/office/`。`/office/health` 的 404 由 Admin 侧验收。
5. 运行配置结构测试、`nginx -t` 候选验证和相关 Pytest 后提交：`git commit -m "chore: harden platform and office release gates"`。

## Task 13：全量验证、复审与分阶段发布

**文件：**
- 修改：`docs/runbooks/cloud-platform.md`
- 新增：`docs/superpowers/reviews/2026-08-24-platform-unified-agent-entry-verification.md`

1. 运行 `.venv/bin/python -m pytest`。
2. 运行 `cd webui && npm test -- --run && npm run build`。
3. 运行 `git diff --check`、Catalog 精确集合检查、配置开关矩阵和 migration 042 生产只读预检。
4. 按顺序发布：Worker 兼容字段 → Platform schema/API → Catalog/Direct UI → AI ADMIN `/office` 候选 → Nginx 原子切换；Brain V2 保持关闭。
5. 真实账号验收根路径、账号页、8-Agent 目录、HR/Marketing 连续会话、FAE 外链、行政统一身份、手机端、401/403/503、CSRF、附件限制和回滚。
6. 记录 release、镜像、迁移、Nginx diff、测试结果与未完成项；通过独立代码复审后提交：`git commit -m "docs: record unified agent entry verification"`。

---

## 发布完成条件

- `/` 始终是 Agent 使用入口；Brain 关闭时是明确准备页，不跳管理中心。
- `/admin/*` 只属于 Platform，`/office/*` 只属于 AI ADMIN，FAE 域名保持不变。
- 目录恰好呈现经授权的 8 个规范 Agent，不包含系统、测试、个人或别名条目。
- HR/Marketing 在 Brain 关闭时可持续直接会话；本地节点离线明确失败且不改派。
- 一个钉钉身份贯通 Platform 与 AI ADMIN，但业务权限仍由各系统后端判断。
- 所有 Session 所有权、附件、反馈、跨用户查看继续后端鉴权与审计。
- migration 042、Worker 版本、Admin `/office` 和 Nginx 切换均有前置检查及可执行回滚。
- Brain V2 只有在本计划完成并单独通过 durable-loop 验收后才启用。
