# 平台侧栏与名称调整实施计划

**Goal:** 顶部品牌和账号、可收起左侧导航、首页全景，沿用现有权限和路径。
**Architecture:** AppShell 保留身份与部署上下文；PlatformSidebar 负责导航分类和当前项；独立 CSS 处理可用宽高，业务页面仅改用户可见名称。
**Tech Stack:** 现有 React、TypeScript、lucide-react、CSS、Vitest；不引入新依赖。

用户已批准左侧分类与命名，后续补充业务入口保留 AI xxx Agent。设计源为 Orbbec-AI-Engineering/docs/design/2026-09-20-ai-engineering-homepage-final-design.md。按 executing-plans 在当前会话实施；使用 requesting-code-review 独立审查。

## 约束
- 不变更业务权限、Agent ID、后端协议、全景数据或独立子应用。
- 不省略历史任务、工程笔记、复审、owner 访问记录；只读部署隐藏复审。
- 使用既有 navigate 离开保护；独立应用链接使用原生文档导航和既有 beforeunload 确认，避免重复提示。
- 后台 AgentsPage 为状态查看；ActivityPage 为运行事件，不能误标成配置和任务执行。
- 窄屏默认收起；展示模式只在全景实际可见时隐藏外壳。
- 页面视觉与浏览器验收由用户完成；组件与构建由开发侧验证。

## Task 1：导航外壳
- [x] 核对 master/origin/master、工作树和路由，隔离工作树；原外壳相关 43 项测试通过。
- [x] 在 AppShell.sidebar.test.tsx 写失败用例：导航在 main 外，权限区别、深链高亮、收起与恢复、只读状态在 main 内、未确认身份无管理入口。
- [x] 新增 platform/PlatformSidebar.tsx、platform/platformShell.css，修改 AppShell.tsx。按路线名匹配当前入口；桌面收起偏好存 localStorage，读取失败不影响渲染。
- [x] 将 PanoramaLayoutHost 测试改走真实侧栏，保留未保存取消确认一次的断言。

## Task 2：名称一致性
- [x] 导航、目录标题、浏览器标题、返回入口同步为 AI 助手／全部 Agent／运行概览／Agent 状态／会话记录／运行事件／账号与权限／审计日志。
- [x] 四个业务卡片保留 AI FAE Agent / AI HR Agent / AI 行政 Agent / AI VOC Agent，不改后台 ID。
- [x] 适配旧导航布局断言，保留角色、只读、FAE scope、工作区边界回归；复用原功能测试。

## Task 3：验证与交付
- [x] 执行外壳、相关页面、全景、路由、权限、文案测试与 npm run build。
- [x] 独立代码审查，修复阻断问题，提交并归并 master，保持用户未跟踪材料。
- [x] 按既有发布授权从准确主线提交构建；若发布，核对真实生产基线，只替换平台 API，保留既有 Compose 输入、挂载、全景数据库、子应用和 nginx。
- [x] 更新唯一设计文档及准确发布记录；浏览器与真实业务验收不冒充已通过。

## 实现核验

- 基线外壳 43 项通过；新增侧栏用例先出现预期失败，再实现通过。
- 相关前端 37 文件 / 364 项通过（外壳、全景、路由、权限、对话、业务目录和文案）；审查修正后外壳 5 文件 / 51 项通过，含新增非 panorama 直达与跨路由只读用例。
- npm run build 通过；保留现有大 chunk 提示。jsdom 的 localStorage/scrollTo 环境提示不属于浏览器验收。
- 独立静态审查发现管理身份离开 admin 后会丢失只读环境；现已全页加载/保留，未确认或失败时隐藏复审。修正后复核无阻断问题。
- 独立应用使用原生链接和既有 beforeunload；SPA 使用既有 navigate guard，避免双重提示。

发布完成：应用 `5e9420d9c43a7e5f92dfc00ea50ac2af03d670e5`，见 `docs/operations/2026-09-20-platform-sidebar-release.md`。浏览器及真实业务验收仍由用户进行。

## 页面底色收尾（2026-09-20 用户反馈）

当前页面的白色/近白底面层次过弱，调整为深蓝平台导航、蓝灰工作区和彩色业务节点。
- [x] 仅修改 platform/platformShell.css、panorama/panorama.css、panoramaWorkspace.css 的表面色、文字色、焦点色和边界色；保留布局、权限、交互与节点语义颜色。
- [x] 检查文字对比度；运行既有侧栏/全景组件回归和构建，不为纯色值写镜像测试。
- [x] 独立静态审查，归并主线，按既有准确 Compose 维护链发布，核验线上 CSS 摘要与色值，记录实际发布版本。

底色验证：相关组件 30 项通过，npm build 通过；主要文字最低对比度 4.86:1；独立 CSS 审查未见阻断。浏览器视觉仍由用户验收。

底色发布：`084b10c361e460042efa459935dff0ad9485e43a`，见 `docs/operations/2026-09-20-platform-surface-release.md`。

## 业务工作台分类调整（2026-09-20 用户确认）

目标：业务入口表达完整业务域，Agent 保留为执行能力。首页独立置顶；AI 工作包含 AI 助手、Agent 目录、历史任务；业务工作台包含技术支持、人力资源、行政服务、客户洞察；工程笔记作为辅助入口保留。运行中心、平台管理沿用当前结构。

- [x] 修改 PlatformSidebar 分类与显示名，所有路径、角色、FAE scope、云只读、跳转类型保持不变。Agent 目录的具体 Agent 卡片保留名称与ID。
- [x] 同步平台内目录标题/返回链接/浏览器标题、FAE 工作台标题和全景动作标签；独立子应用内部界面不在此次平台发布内。
- [x] 适配已有侧栏与标题断言，验证分组归属、权限、深链、离开保护、FAE 工作台和构建；独立审查后归并发布。
- [x] 更新唯一设计文档和准确上线记录，保留最新 Compose 输入链与全景数据。

分类调整验证：110 项相关组件回归通过；最后返回链接空格修正后目录 15 项通过；npm build 和发布事务 13 项通过。分组、深链、权限、只读、离开保护保留。独立审查指出的中文空格已修复。

业务工作台分类已发布：`6499a5a9b2306eba8bc8b98defb62652e8cb0063`，见 `docs/operations/2026-09-20-business-workspaces-release.md`。

## 首页管理员访问限制（2026-09-20 用户确认）

首页及 AI 工程全景（含资料直链、全景内工作区）只允许 platform_admin/platform_owner。非管理员显示“无权限，请联系苍渊。”，不呈现全景或左侧导航。独立 office/hr/voc/fae 子应用授权保持。

- [x] HTTP `/` 与 `/ai-engineering` 对已登录非管理员返回不含应用脚本/数据的 403 提示页，private/no-store；未登录保留既有登录引导。预览前缀同样生效。
- [x] App 在首页/全景路由进入 AppShell 前检查真实账号角色，拒绝成员、只读观察者及无有效身份的 legacy 入口；AiEngineeringLanding 单独渲染也拒绝非管理员，不发内容请求。
- [x] 保留后台全景 API 管理员权限、CSRF、权限撤销及子应用授权，测试全角色矩阵、伪造角色头、SPA 跳转和资料直链；独立审查后发布。
- [x] 记录实际应用版本，保持全景数据、原有业务子应用和准确发布维护链。

本地验证：后端身份/全景 HTTP 回归 241 项通过；前端 87 项通过，包含角色撤销后清除外壳、恢复独立成员页面的检查；npm build 与发布事务 13 项通过。独立审查指出拒绝状态应局限于全景，已改用专门状态，避免影响其他成员页面。

权限限制已发布：`f6c3d8f33fe27610051f3e6723c334969a9e0790`，见 `docs/operations/2026-09-20-home-admin-access-release.md`。
