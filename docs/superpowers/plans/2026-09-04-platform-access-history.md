# Agent Platform 登录与页面访问记录 TDD 实施计划

> 执行说明：使用 executing-plans 按任务顺序实施；每项严格执行“失败测试 → 最小实现 → 通过测试 → 提交”。四个仓库均使用本地 worktree，本功能完成前不创建或推送远端功能分支；最终只在本地完成集成并按用户指令推送主线。

目标：在 agent.orbbec.com.cn 的 Platform 钉钉企业身份边界内，可靠记录“谁在什么时间成功登录、进入了哪个规范页面”，并提供仅 platform_owner（当前为苍渊）可见的 /admin/access 查询页面。

架构：Agent Platform PostgreSQL 是唯一事实源。登录事件与 Web Session 在同一数据库事务中创建；各前端在身份和页面授权成功后，以同源 POST /api/v1/access-events/page-view 上报规范页面键。Platform 后端负责 Session 绑定、页面目录校验、按 Session 限流、幂等写入、90 天保留和 Owner-only 查询。Office、内部 FAE、VOC 只增加无界面、失败开放的薄上报器，不改变各自业务或身份协议。

技术栈：PostgreSQL / PL/pgSQL、Python 3.11、FastAPI、Pydantic、React 19、TypeScript、Vitest、pytest、Playwright/Chromium、现有 Platform 发布与验收脚本。

设计依据：docs/superpowers/specs/2026-09-03-platform-access-history-design.md

---

## 实施前冻结项

1. Platform 实现分支为本地 feat/platform-access-history，worktree 为：

       /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history

2. 开始实现前先更新到已经合入 HR 工作台的最新本地主线；不覆盖任何已发布迁移。目标迁移为 065_user_access_history.sql。若最新主线已经占用 065，则使用下一个连续编号，并同步函数后缀、设计和测试常量。
3. AI ADMIN 主工作区有用户未跟踪文件，严禁在主工作区开发、清理或格式化。分别使用以下本地 worktree：

       /Users/neo/Developer/work/AI-ADMIN-Agent/.worktrees/platform-access-history
       /Users/neo/Developer/work/AI-FAE-Agent/.worktrees/platform-access-history
       /Users/neo/Developer/work/Orbbec-VOC-Agent/.worktrees/platform-access-history

4. FAE 实施前重新完整阅读 AGENTS.md、docs/AI_FAE_AGENT_ENGINEERING_PRINCIPLES.md 和 docs/DESIGN_INDEX.md。本功能不改 planner、能力、答案、知识或评测语义。
5. Office 发布继续使用 scripts/deploy_office_webui.py，发布后运行 scripts/verify_office_webui_runtime.mjs；不得直接替换 index.html。
6. 不回填旧 Nginx 日志，不记录失败登录，不记录原始 URL、query、fragment、业务对象 ID、IP、User-Agent、输入或附件。

## 规范页面目录

数据库目录和各前端映射必须覆盖下列精确集合。动态页面只发送固定 page_key，绝不发送详情 ID。

- Platform：platform.brain、platform.conversations、platform.conversation、platform.agent_directory、platform.missions、platform.mission_detail、platform.account、platform.ai_notes、platform.ai_note。
- HR：hr.workspace、hr.conversation。
- Marketing：marketing.workspace、marketing.conversation；agent_id 必须是五个规范 Marketing Agent ID 之一。
- Office：office.chat、office.services、office.management、office.service_detail、office.feedback、office.my_feedback、office.feedback_admin、office.shuttle、office.shuttle_admin、office.lodging、office.lodging_admin、office.vehicle_registration、office.vehicle_registration_admin、office.notification_admin。
- FAE：fae.workspace、fae.conversation、fae.manage.overview、fae.manage.sessions、fae.manage.session_detail、fae.manage.issues、fae.manage.issue_detail、fae.manage.reports、fae.manage.report_detail。
- VOC：voc.workspace、voc.records、voc.record_detail、voc.manage、voc.manage.record_detail。
- Admin：admin.overview、admin.agents、admin.agent_detail、admin.agent_runtime、admin.sessions、admin.session_detail、admin.review、admin.activity、admin.identity、admin.governance、admin.access_history。

登录页、权限错误页、404、legacy-redirect 中间页和外部 fae.orbbec.com.cn 没有 page_key。

---

### Task 1：更新本地主线并锁定迁移编号

文件：

- 检查：backend/control_migrations/*.sql
- 检查：backend/tests/test_control_plane_migration.py
- 修改：设计和本计划，仅当迁移编号顺延

步骤 1：确认四个仓库状态，不修改主工作区。

    git -C /Users/neo/Developer/work/AI-Agent-Platform status --short --branch
    git -C /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history status --short --branch
    git -C /Users/neo/Developer/work/AI-ADMIN-Agent status --short --branch
    git -C /Users/neo/Developer/work/AI-FAE-Agent status --short --branch
    git -C /Users/neo/Developer/work/Orbbec-VOC-Agent status --short --branch

步骤 2：把 Platform 功能分支更新到包含 HR 工作台的最新本地主线。

    git -C /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history rebase master
    find /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history/backend/control_migrations -maxdepth 1 -name '*.sql' | sort -V | tail -5

预期最后一个版本为 064，访问记录使用 065。若事实不同，先顺延全部 v65 名称。

步骤 3：创建三个本地 worktree。

    git -C /Users/neo/Developer/work/AI-ADMIN-Agent worktree add .worktrees/platform-access-history -b feat/platform-access-history
    git -C /Users/neo/Developer/work/AI-FAE-Agent worktree add .worktrees/platform-access-history -b feat/platform-access-history
    git -C /Users/neo/Developer/work/Orbbec-VOC-Agent worktree add .worktrees/platform-access-history -b feat/platform-access-history

如果同名本地分支或 worktree 已存在，先检查来源和内容，不强制删除。

---

### Task 2：用迁移建立访问事件、页面目录和数据库边界

文件：

- 新建：backend/control_migrations/065_user_access_history.sql
- 修改：backend/tests/test_control_plane_migration.py

步骤 1：先写失败的迁移测试。

在 test_control_plane_migration.py：

- 最大迁移版本更新为 65；
- access_page_catalog、user_access_events 加入必需表；
- 断言 user_access_events.session_id 没有外键；
- 断言登录字段与页面字段的排他 CHECK；
- 断言每个 Session 只有一条 login_succeeded；
- 断言时间、用户、工作区、事件类型查询索引；
- 断言 app 角色没有表级 INSERT/UPDATE/DELETE/SELECT；
- 断言 app 角色只能执行限定函数；
- 断言 maintenance 角色只能执行保留函数；
- 断言页面目录精确等于本计划的规范集合；
- 断言 Marketing 以外禁止 agent_id，Marketing 只接受五个规范 ID；
- 断言 consume_attempt_and_issue_session_v65、append_page_view_v65、read_user_access_events_v65、retain_user_access_events_v65 存在。

在迁移测试中直接调用 retain_user_access_events_v65，覆盖小于 90 天不删、严格早于 90 天才删，以及 app 角色不能执行保留函数。维护 CLI 的集成留到 Task 8，避免尚未实现的 CLI 改动阻塞本任务提交。

步骤 2：运行红灯测试。

    cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history/backend
    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_control_plane_migration.py

步骤 3：实现两张表。

access_page_catalog 字段：

- workspace_key text not null
- page_key text primary key
- display_name text not null
- allows_agent_id boolean not null default false
- unique (workspace_key, page_key)

user_access_events 字段：

- access_event_id uuid primary key
- internal_user_id uuid not null，引用 internal_users
- session_id uuid not null，故意不引用 web_sessions
- event_kind text not null
- login_kind text
- workspace_key text
- page_key text
- agent_id text
- occurred_at timestamptz not null default clock_timestamp()

页面事件以 (workspace_key, page_key) 外键绑定目录。

步骤 4：实现四个 SECURITY DEFINER 函数。

1. consume_attempt_and_issue_session_v65：
   - 调用 v22；
   - v22 返回 Session 后，从已消费 login_attempt 读取 attempt_kind；
   - 同事务插入唯一 login_succeeded；
   - 不接受由浏览器或 Python 选择的 login_kind。

2. append_page_view_v65(event_id, actor_id, session_id, workspace_key, page_key, agent_id)：
   - 先检查 access_event_id；完全一致返回 duplicate，相同 ID 不同内容拒绝；
   - 用 pg_advisory_xact_lock 按 Session 串行化；
   - 验证 Session 属于 actor、未吊销且未过期；
   - 校验目录与 agent_id；
   - 查询同 Session 最近滚动 60 秒 page_view 数，达到 120 返回 rate_limited 和 Retry-After；
   - 成功用数据库时间写入并返回 inserted。

3. read_user_access_events_v65(requester_id, filters..., limit, offset)：
   - 函数内部再次验证 requester 是 active platform_owner；
   - display_name 使用内部用户规范花名精确匹配；
   - 日期范围、limit、offset 有界；
   - 固定 occurred_at DESC, access_event_id DESC；
   - 返回 display_name、event kind、login kind、workspace/page、页面显示名、agent_id、occurred_at；
   - 不返回 Session ID、内部 UUID 或原始路径。

4. retain_user_access_events_v65(cutoff)：
   - 只授权 maintenance 角色；
   - 删除严格早于 90 天 cutoff 的事件并返回数量。

步骤 5：通过测试并提交。

    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_control_plane_migration.py
    git add backend/control_migrations/065_user_access_history.sql backend/tests/test_control_plane_migration.py
    git commit -m "feat(access): add durable login and page history schema"

---

### Task 3：原子记录两种钉钉登录

文件：

- 修改：backend/app/control_plane/auth.py
- 修改：backend/tests/test_dingtalk_auth_api.py
- 修改：backend/tests/test_control_plane_migration.py

步骤 1：先写真实迁移数据库失败测试：

- QR 登录成功后 Session 与唯一 login_succeeded/qr 同时存在；
- in-client 登录成功后记录 login_succeeded/in_client；
- 重放 attempt 不产生第二个 Session 或事件；
- 强制事件插入失败时 Session 事务整体回滚；
- issue_session 不接受 route 提交的 login_kind，数据库从 attempt 推导。

步骤 2：运行红灯测试。

    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_dingtalk_auth_api.py -k "login and access_event"
    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_control_plane_migration.py -k "access_history or login_event"

步骤 3：将 WebSessionRepository.issue_session 的 SQL 调用从 consume_attempt_and_issue_session_v22 切换为 v65。DingTalkIdentityAuth._complete、complete_qr、complete_in_client 和 Cookie 发放顺序保持不变。

步骤 4：通过测试并提交。

    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_dingtalk_auth_api.py tests/test_control_plane_migration.py
    git add backend/app/control_plane/auth.py backend/tests/test_dingtalk_auth_api.py backend/tests/test_control_plane_migration.py
    git commit -m "feat(access): record successful DingTalk logins atomically"

---

### Task 4：实现页面上报 API、同源例外和 Owner-only 查询 API

文件：

- 新建：backend/app/control_plane/access_history.py
- 新建：backend/app/control_plane/routes_access_history.py
- 新建：backend/tests/test_access_history_api.py
- 修改：backend/app/control_plane/middleware.py
- 修改：backend/app/control_plane/authorization.py
- 修改：backend/app/main.py
- 修改：backend/tests/test_dingtalk_auth_api.py
- 修改：backend/tests/test_r1_authorization.py

步骤 1：先写 Repository 与路由失败测试。

test_access_history_api.py 覆盖：

- body 只能有 access_event_id/workspace_key/page_key/agent_id；
- actor、Session、时间来自 request.state.auth_context 与数据库；
- 正常写入 204；同 ID 重放 204 且只有一行；
- 同 ID 不同内容、未知页面、非法 Marketing Agent 返回 400；
- body 超过 2048 字节返回 413；非 JSON 返回 415；
- 第 121 个同 Session 页面事件返回 429 和 Retry-After；
- 数据库不可用返回 503；
- 查询默认最近 7 天、50 条，最大 100；
- 时间、花名、工作区、事件类型筛选正确；
- Owner 200；Admin/Viewer/Member 均 403；
- response 不含 session_id、internal_user_id、URL、query 或业务 ID。

步骤 2：先写 Middleware 与授权失败测试。

- 只有精确 POST /api/v1/access-events/page-view 可不带 X-CSRF-Token；
- 仍要求 Session 和精确 Origin: https://agent.orbbec.com.cn；
- fae.orbbec.com.cn、null、缺失 Origin 均 403；
- 相似路径和其他 POST 仍执行原 CSRF；
- 页面上报跳过现有按用户 60/分钟 mutation limiter，由 v65 执行按 Session 120/分钟；
- GET /api/v1/manage/access-events 是 exact owner-only；
- platform_admin 不因通用 _OWNER_ROUTES 获权。

步骤 3：运行红灯测试。

    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_access_history_api.py
    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_dingtalk_auth_api.py -k "access_event or csrf or origin"
    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_r1_authorization.py -k "access_event or owner"

步骤 4：实现稳定接口。

access_history.py：

    @dataclass(frozen=True)
    class PageAccessDescriptor:
        workspace_key: str
        page_key: str
        agent_id: str | None

    @dataclass(frozen=True)
    class AccessHistoryFilter:
        date_from: datetime
        date_to: datetime
        display_name: str | None
        workspace_key: str | None
        event_kind: Literal["login_succeeded", "page_view"] | None
        limit: int
        offset: int

    class AccessHistoryRepository:
        def record_page_view(self, event_id: UUID, context: AuthContext, page: PageAccessDescriptor) -> Literal["inserted", "duplicate", "rate_limited"]: ...
        def list_events(self, context: AuthContext, filters: AccessHistoryFilter) -> AccessHistoryPage: ...

Repository 只调用 SECURITY DEFINER 函数，不直接写表。

routes_access_history.py：

- POST /api/v1/access-events/page-view 使用 await request.body() 在 JSON 解析前执行 2 KB 限制，严格 extra=forbid；
- GET /api/v1/manage/access-events 返回 items/limit/offset/has_more；
- route dependency 再次执行 context.role is Role.PLATFORM_OWNER；
- 查询不可用返回 503“访问记录暂不可用”，不返回空列表伪装成功。

步骤 5：实现精确 Middleware 例外。

增加：

    def is_page_access_event_request(method: str, local_path: str | None) -> bool:
        return method == "POST" and local_path == "/api/v1/access-events/page-view"

仅在此函数为真时保留 Session 认证和 Origin，跳过 X-CSRF-Token 与通用 mutation limiter。不得把端点标为 public。

步骤 6：授权和 wiring。

- 页面 POST 加入 authenticated-self routes；
- 查询 GET 加入新的 _PLATFORM_OWNER_ONLY_ROUTES；
- main.py 挂载 router 与 Repository；
- 不引入对 Office、FAE、VOC 的后端依赖。

步骤 7：通过测试并提交。

    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_access_history_api.py tests/test_dingtalk_auth_api.py tests/test_r1_authorization.py
    git add backend/app/control_plane/access_history.py backend/app/control_plane/routes_access_history.py backend/app/control_plane/middleware.py backend/app/control_plane/authorization.py backend/app/main.py backend/tests/test_access_history_api.py backend/tests/test_dingtalk_auth_api.py backend/tests/test_r1_authorization.py
    git commit -m "feat(access): expose bounded page history APIs"

---

### Task 5：实现 Platform Reporter 与苍渊专属页面

文件：

- 新建：webui/src/accessEventReporter.tsx
- 新建：webui/src/accessEventReporter.test.tsx
- 新建：webui/src/accessHistoryApi.ts
- 新建：webui/src/accessHistoryApi.test.ts
- 新建：webui/src/pages/AccessHistoryPage.tsx
- 新建：webui/src/pages/AccessHistoryPage.test.tsx
- 修改：webui/src/App.tsx
- 修改：webui/src/AppShell.tsx
- 修改：webui/src/AppShell.brain.test.tsx
- 修改：webui/src/router.ts
- 修改：webui/src/router.test.ts
- 修改：webui/src/auth.ts
- 修改：webui/src/auth.test.ts
- 修改：webui/src/styles.css

步骤 1：先写 Route 投影和 Reporter 失败测试。

- 枚举 Route 每个 name，映射到固定页面键或明确不记录；
- 动态 conversation/mission/session/issue/report/article ID 不进入 body；
- Marketing agent_id 来自 MARKETING_AGENT_ID_BY_SLUG；
- 初次挂载、刷新、真实 SPA route 变化各一次；
- 同 route 重渲染和数据刷新不重复；
- crypto.randomUUID 生成事件 ID；
- fetch 固定同源路径，credentials include、JSON、keepalive；
- 失败不抛到页面、不渲染提示；
- 未登录、未授权、404、redirect 不调用。

步骤 2：先写 /admin/access 失败测试。

- 新 Route 名 admin-access；
- 登录 return path 接受精确 /admin/access；
- 只有 platform_owner 导航显示“访问记录”；
- 非 Owner 直接构造 route 也不查询 API；
- API parser 拒绝额外字段与非法筛选；
- 默认最近 7 天、limit 50、offset 0；
- 筛选重置第一页，上一页/下一页正确；
- 北京时间展示；
- 503 与真实空结果文案不同；
- 页面 DOM 不出现 Session、内部 UUID、原始路径或 query；
- 页面本身上报 admin.access_history。

步骤 3：运行红灯测试。

    cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history/webui
    npm test -- src/accessEventReporter.test.tsx src/accessHistoryApi.test.ts src/pages/AccessHistoryPage.test.tsx src/router.test.ts src/auth.test.ts src/AppShell.brain.test.tsx

步骤 4：实现 Route 投影与 Reporter。

    export type PageAccessEvent = Readonly<{
      workspace_key: string;
      page_key: string;
      agent_id: string | null;
    }>;

    export function accessEventForRoute(route: Route): PageAccessEvent | null;

使用 exhaustive switch 和 never。AccessEventReporter 只在 account 成功加载且当前 route 已通过产品权限检查后挂载。每次 route 语义变化生成 UUID 并 fire-and-forget；失败最多记录一次不含用户、路径和 ID 的固定诊断。

步骤 5：实现 Owner 页面。

- 表格列：花名、时间、类型、页面/登录方式、Agent；
- 默认北京时间最近 7 天；
- 50 条/页；
- 不做访问量 KPI；
- UI 隐藏不替代后端权限。

步骤 6：通过全量前端测试和构建并提交。

    npm test
    npm run build
    git add webui/src/accessEventReporter.tsx webui/src/accessEventReporter.test.tsx webui/src/accessHistoryApi.ts webui/src/accessHistoryApi.test.ts webui/src/pages/AccessHistoryPage.tsx webui/src/pages/AccessHistoryPage.test.tsx webui/src/App.tsx webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx webui/src/router.ts webui/src/router.test.ts webui/src/auth.ts webui/src/auth.test.ts webui/src/styles.css
    git commit -m "feat(access): add owner-only access history experience"

---

### Task 6：接入 AI ADMIN，不改变 /office 业务

仓库：/Users/neo/Developer/work/AI-ADMIN-Agent/.worktrees/platform-access-history

文件：

- 新建：webui/src/platformAccessReporter.ts
- 新建：webui/src/platformAccessReporter.test.ts
- 修改：webui/src/App.tsx
- 修改：webui/src/AppRender.test.tsx
- 检查：webui/src/appView.ts
- 检查：webui/src/appView.test.ts

步骤 1：先写失败测试。

- AppView 全部 kind 有固定映射；
- service_id、feedback_id、notification_id、compose、question、ticket 不进 body；
- 只有 runPortalBootstrap 返回 ready 且 session 非空后上报；
- loading、redirecting、blocked、legacy_ticket session:null 不上报；
- query SPA 切换一次，重渲染不重复；
- 失败不改变 Portal 页面和业务错误状态。

步骤 2：运行红灯测试。

    cd /Users/neo/Developer/work/AI-ADMIN-Agent/.worktrees/platform-access-history/webui
    npm test -- src/platformAccessReporter.test.ts src/AppRender.test.tsx src/appView.test.ts

步骤 3：实现无界面 Reporter。

固定 POST /api/v1/access-events/page-view，credentials include，JSON body 只含 UUID、workspace_key=office、page_key、agent_id=null。在 App.tsx 的 Portal 身份成功分支挂载；不读取 Cookie，不读取 csrf_token，不修改 Office 自有 CSRF。

步骤 4：执行 Office 门禁并提交。

    npm test
    npm run test:browser-mobile
    npm run build
    npm run check:bundle-budget
    git add webui/src/platformAccessReporter.ts webui/src/platformAccessReporter.test.ts webui/src/App.tsx webui/src/AppRender.test.tsx
    git commit -m "feat(access): report authenticated Office page views"

---

### Task 7：接入内部 FAE 与 VOC

#### 7A：内部 FAE

仓库：/Users/neo/Developer/work/AI-FAE-Agent/.worktrees/platform-access-history

文件：

- 新建：webui/src/platformAccessReporter.tsx
- 新建：webui/src/platformAccessReporter.test.tsx
- 修改：webui/src/App.tsx
- 修改：webui/src/AppRender.test.tsx
- 检查：webui/src/enterpriseIdentity.ts
- 检查：webui/src/runtimePaths.ts
- 检查：webui/src/routes.ts

先写测试：

- /fae/ + platform_enterprise 上报 fae.workspace；
- /fae/conversations/:id 上报 fae.conversation，body 无 ID；
- public_customer、platform_partner、/app/* 均不调用；
- IdentityGate loading/failed/forbidden 不调用；
- 打开历史会话、回到新会话各记一次；
- 失败不影响聊天、附件、历史或流式回答。

Reporter 的硬门禁：

    isInternalFaeSurface()
    && currentAuthenticatedAccount()?.mode === "platform_enterprise"

在 ChatWorkspace 中用 routeSessionId ?? sessionId 的有无区分 workspace/conversation，只发送页面键。

运行并提交：

    cd /Users/neo/Developer/work/AI-FAE-Agent/.worktrees/platform-access-history/webui
    npm test -- src/platformAccessReporter.test.tsx src/AppRender.test.tsx src/routes.test.ts
    npm test
    npm run build
    git add webui/src/platformAccessReporter.tsx webui/src/platformAccessReporter.test.tsx webui/src/App.tsx webui/src/AppRender.test.tsx
    git commit -m "feat(access): report internal enterprise FAE pages"

#### 7B：VOC

仓库：/Users/neo/Developer/work/Orbbec-VOC-Agent/.worktrees/platform-access-history

文件：

- 新建：webui/src/platformAccessReporter.tsx
- 新建：webui/src/platformAccessReporter.test.tsx
- 修改：webui/src/App.tsx
- 修改：webui/src/api.test.ts
- 修改：webui/src/routes.test.ts

先写测试：

- loadSession 成功之前不上报；
- feedback/records/record/management/management-record 分别映射规范键；
- vocNo 不进 body；
- legacy management 中间 route 不上报，canonical replace 后只记一次；
- 未授权管理页不记成功访问；
- SPA pushState、浏览器后退各一次；
- 失败不影响草稿、提交、列表或管理。

只有 session 非空、route 非 not-found、管理 route 已通过 voc_admin 后挂载 Reporter。不使用 VOC csrf_token。

运行并提交：

    cd /Users/neo/Developer/work/Orbbec-VOC-Agent/.worktrees/platform-access-history/webui
    npm test -- --run src/platformAccessReporter.test.tsx src/api.test.ts src/routes.test.ts
    npm test -- --run
    npm run build
    git add webui/src/platformAccessReporter.tsx webui/src/platformAccessReporter.test.tsx webui/src/App.tsx webui/src/api.test.ts webui/src/routes.test.ts
    git commit -m "feat(access): report authenticated VOC page views"

---

### Task 8：保留任务、跨仓发布门禁和本地集成

文件：

- 修改：backend/app/control_plane/maintenance_cli.py
- 修改：backend/tests/test_control_maintenance_cli.py
- 新建：deploy/cloud/access-history-probe.mjs
- 修改：deploy/cloud/accept.sh
- 修改：deploy/cloud/acceptance.sh

步骤 1：先写失败测试。

- maintenance JSON 返回 access_events；
- 90 天删除与现有 maintenance health gate 一致；
- accept.sh 要求迁移、表、函数和 Owner-only API 存在；
- 浏览器探针覆盖 Platform、Office、内部 FAE、VOC、HR、一个 Marketing、Admin；
- 外部 FAE 不产生事件；
- 非 Owner 查询为 403。

步骤 2：实现 maintenance。

MaintenanceRepository.purge_expired 在 time/WAL health 通过后调用 retain_user_access_events_v65，并把数量加入结果；不修改历史迁移 018。

步骤 3：实现 access-history-probe.mjs。

- 记录探针开始时间；
- 用真实受控浏览器 Session 依次打开 /、/office/?view=services、/fae/、/voc/、/hr/、/marketing/prospecting、/admin/；
- 等待各页面真实 ready 标记；
- Owner 查询 API，断言开始时间后出现相应 page_key；
- Office 只存 office.services，无 query；
- 动态页面无对象 ID；
- 打开 fae.orbbec.com.cn 后确认无 Platform 事件；
- Admin/Member 查询 403；
- /admin/access 显示真实记录。
- 探针只输出状态和计数，不输出 Cookie、用户 UUID、Session 或记录正文。

步骤 4：运行 Platform 全量门禁。

    cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/platform-access-history
    /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q backend/tests
    cd webui && npm test && npm run build
    cd .. && bash -n deploy/cloud/*.sh

步骤 5：提交 Platform 验收改动。

    git add backend/app/control_plane/maintenance_cli.py backend/tests/test_control_maintenance_cli.py deploy/cloud/access-history-probe.mjs deploy/cloud/accept.sh deploy/cloud/acceptance.sh
    git commit -m "test(access): gate cross-workspace access history release"

步骤 6：请求代码复审，重点审查：

- 原子登录与 Session 生命周期；
- CSRF 例外是否只命中一个精确路径；
- 120/分钟是否真正按 Session；
- Owner-only 是否由 authorization、route dependency、数据库函数三层保证；
- 页面映射是否遗漏或泄漏动态 ID；
- external FAE 和 Partner 是否排除；
- Reporter 失败是否完全不影响业务。

步骤 7：只在本地合并。

四个仓库分别把 feat/platform-access-history 合入各自本地 master。若主线前进，先整合最新提交并重跑对应全量测试。禁止 reset --hard，禁止覆盖其他会话改动，不推远端功能分支。

步骤 8：按依赖顺序发布。

1. Platform 数据库迁移、后端 API、Platform WebUI；
2. 验证新 API 在外部前端尚未接入时稳定；
3. Office 使用 scripts/deploy_office_webui.py，随后 scripts/verify_office_webui_runtime.mjs；
4. FAE 使用 deploy/scripts/deploy_prod.sh 和 deploy/scripts/verify_prod.sh，确认外部 FAE 不变；
5. VOC 使用 deploy/linux/deploy-remote.sh 和 deploy/linux/verify-remote.sh；
6. 运行 Platform deploy/cloud/accept.sh accept。

步骤 9：真实钉钉验收。

- 新登录只有一条事件，区分扫码/客户端免登；
- 苍渊访问 /admin/access 成功；
- 西门吹雪即使是 Platform Admin 也不能读取；
- 七类工作区页面键准确；
- Office、FAE、VOC 原业务正常；
- FAE 外部客户页不记录；
- 人为让页面上报 API 失败时，各业务页面仍可用。

步骤 10：观察 24 小时。

只观察每小时写入数、429、5xx、重复比例和表增长量。访问记录正文不得写普通日志。用真实增长量确认 90 天容量，首发不做猜测性分区。

---

## 完成判据

- Platform、Office、内部 FAE、VOC、HR、Marketing、Admin 都有真实上报证据；
- 登录事件与 Session 原子一致；
- 页面事件幂等，超过 Session 限额返回 429；
- 苍渊可见，Platform Admin/Viewer/Member 后端均 403；
- 数据库、API、前端与探针证明没有原始 URL、query、业务 ID、Cookie 或钉钉原始身份；
- 外部 fae.orbbec.com.cn 和 Partner 身份不记录；
- 上报故障不影响业务；
- 四仓全量测试、构建、Office 移动端门禁和生产真实验收通过；
- 本地功能分支完成本地集成后，再按用户指令推送主线，不留下无必要远端分支。
