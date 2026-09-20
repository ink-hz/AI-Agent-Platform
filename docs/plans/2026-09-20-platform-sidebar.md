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
- [ ] 独立代码审查，修复阻断问题，提交并归并 master，保持用户未跟踪材料。
- [ ] 按既有发布授权从准确主线提交构建；若发布，核对真实生产基线，只替换平台 API，保留既有 Compose 输入、挂载、全景数据库、子应用和 nginx。
- [ ] 更新唯一设计文档及准确发布记录；浏览器与真实业务验收不冒充已通过。

## 实现核验

- 基线外壳 43 项通过；新增侧栏用例先出现预期失败，再实现通过。
- 相关前端 37 文件 / 364 项通过（外壳、全景、路由、权限、对话、业务目录和文案）；审查修正后外壳 5 文件 / 51 项通过，含新增非 panorama 直达与跨路由只读用例。
- npm run build 通过；保留现有大 chunk 提示。jsdom 的 localStorage/scrollTo 环境提示不属于浏览器验收。
- 独立静态审查发现管理身份离开 admin 后会丢失只读环境；现已全页加载/保留，未确认或失败时隐藏复审。修正后复核无阻断问题。
- 独立应用使用原生链接和既有 beforeunload；SPA 使用既有 navigate guard，避免双重提示。
