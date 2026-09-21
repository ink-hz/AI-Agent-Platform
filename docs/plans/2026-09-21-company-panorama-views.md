# 公司全景双视图实施计划

> 使用 subagent-driven-development：导航与页面集成由独立实现代理完成，主代理处理组织展示与发布；请求独立审查后归并。

**Goal:** 左侧“公司全景”包含“业务布局”和“组织架构”，各有独立完整画布，默认首页为业务布局。

**Architecture:** 新增 `/organization` SPA 路由，复用同一个管理员访问门控与存活中的 PanoramaSession。两张图分别懒加载，隐藏时停止请求，保留业务选中/编辑状态与组织展开状态；滚动位置按视图保存在当前会话内。真实组织数据仍来自原有只读目录接口，不修改数据库或同步器。

**Tech Stack:** React / TypeScript / CSS / Vitest，沿用已有发布流程。

## Global Constraints

- 业务布局保留四层：产业位置、产品与技术、Marketing ↔ Technology、支撑体系；移除组织嵌入插槽及相关旧测试。
- 新组织页沿用管理员/所有者门控；直接访问、撤权、401/403不得泄露隐藏树与详情。
- 组织页不依赖业务全景读取成功；业务页首次打开不读取通讯录。
- 图内无人数、人员、指标和说明墙；点击部门显示详情。当前快照时效和职位未同步仍在详情说明。
- 组织分类按已说明的真实一级分支归组、同支同色，不能从名字编造隶属关系。
- 真实部门/人员不得写入代码或 Git。无新依赖、不改变既有后台 API、数据和权限。
- 目录不采用技能名；更新唯一设计文档。无浏览器自动验收，保留用户文件。

## Task 1：导航、路由、独立画布及状态保存

Files: `backend/app/control_plane/{auth,authorization,middleware,routes_auth}.py` 及 `backend/tests/test_ai_engineering_api.py`（新 SPA 壳及登录返回路径，沿用既有管理员门控）；`webui/src/router.ts`, `documentTitle.ts`, `panoramaNavigation.ts`, `App.tsx`, `platform/PlatformSidebar.tsx`, `pages/AiEngineeringPage.tsx`, `panorama/PanoramaView.tsx`, `panorama/PanoramaCanvas.tsx` 与对应测试；如需可新建小型滚动容器组件。

Interface: `LandingProps.view?: 'business' | 'organization'`，默认 business；新路由 `{name:'organization'}` 对应 `/organization`；保留 `/`、`/ai-engineering` 及资料路径。

- [x] 写失败行为测试：初始 `/` 仅业务图且未请求 organization；点击左侧组织架构后仅显示组织图；直接 `/organization` 不请求 panorama；切换后业务搜索/选中与组织展开保留；滚动位置各自恢复；新路由非管理员拒绝，撤权清除整个外壳。运行并记录实际失败。
- [x] 路由增加 `if (clean === '/organization') return {name:'organization'};`，routePath/title、isPanoramaLocation 与 navigate 状态白名单同步；App 传入 `view={route.name === 'organization' ? 'organization' : 'business'}`。
- [x] 左侧首组“公司全景”含“业务布局” `/` 与“组织架构” `/organization`，分别高亮。角色门控沿用 App；管理之外的导航不变。
- [x] PanoramaSession 保持访问探测、focus 与周期复核；业务 fetch 仅业务视图首次需要时发生，失败可原位重试；组织渲染独立于 data。两个容器按视图 hidden，组件存活保留状态；组织 active 仅在组织页且无覆盖面板时为 true。
- [x] 删除 PanoramaView/Canvas 中 renderOrganization/organization 插槽；不改变业务图模型、编辑器或导出。图间切换保留未保存状态，不弹出丢弃确认；外部离开保护不变。
- [x] 处理路由切换与浏览器前进后退的滚动恢复，不使用被全局 navigate.scrollTo(0,0) 覆盖的窗口滚动。采用各自 DOM 滚动容器，离开图页记录位置并在可见后恢复；组织异步树返回后保持既有展开，避免恢复位置被短暂空树挤掉。
- [x] 跑路由/外壳/首页/恢复/草稿/组织组件相关测试和 build，提交实现并报告准确结果。

## Task 2：组织归组与配色

Files: `webui/src/organization/OrganizationLayout.tsx`, `organization.css`, `OrganizationLayout.test.tsx`；唯一设计文档。

- [x] 已给予回复时间，采用已说明的真实一级分支归组。
- [x] 为每个一级分支明确分组边界与色带，下级继承分支色系、用更浅底色，根为深蓝。用稳定 ID 决定色系，不按名称猜职责，不让顺序变化导致同支变色。颜色是辅助，同级容器/名称/连线保留层级信息。
- [x] 组织 active=false 时中止请求并清空个人详情；再次激活重新校验结构但保留已有树以维持排布，成功返回后保留仍存在的 expanded IDs；新快照移除失效 IDs。401/403 或读取失败清除旧结构，不把缓存当最新。
- [x] 写有意义测试：展开分支后切换再回来仍展开；失效部门移除；撤权清空；分支色系继承，名称不影响颜色。完成相关回归并提交。

## Task 3：审查、合并与发布

- [x] 独立全分支审查，修复明确问题；记录测试通过/失败/未执行边界。
- [x] 合并 master 并推送，以准确合并提交构建。复核当前线上 `7e30c30a`、镜像及完整21输入维护清单；本次无需数据库迁移。
- [x] 仅替换 platform-api，保留21输入和所有挂载，追加本次覆盖；验证新路径、静态资源、匿名目录API、只读目录读取及其他服务不变。
- [x] 同步唯一设计与发布记录，标明实际应用提交；清理自有工作区。

阶段记录：组织样式与状态变更 `b1a7590f`，新增2项先失败后通过；组织/API组件共15项通过。独立审查无阻塞，发布事务13项通过。后台新增组织页壳与登录返回路径先有13项失败，修复后后台HTTP/认证249项通过。导航/画布集成提交 `9875272b`，初始11项失败，相关前端17文件178项通过，正式前端构建通过。浏览器验收未执行。

发布前全链路复核发现并暂停发布的两处契约缺口：新路由已贯通 router 与后端，但前端登录返回路径有独立白名单；固定视口布局覆盖了同一外壳下的资料页，而滚动只分配给图和工作区。修复按职责补齐：`auth.ts` 的精确路径白名单与 LoginPage 请求联测保证登录前后同一路径；`.page.is-panorama-page > .ai-engineering-page` 拥有可收缩的滚动区域，业务、组织、工作区、资料各自负责内容滚动。沿用原有严格路径校验，不增加任意路径兜底；保留资料入口，不移除功能绕开布局问题。两处完成独立复审前不发布。

两处复核修复提交 `7d75da45`：先有3项预期失败，最终前端21文件338项通过，build通过；独立复审另跑170项通过，两处P2已关闭，无阻塞项。后台249项、发布事务13项结果保持有效；浏览器和生产登录会话未验收。

应用 `d619ac3c` 已上线。22输入维护链、线上新入口、11个匿名受保护接口及只读目录查询通过，详见 [发布记录](../operations/2026-09-21-company-panorama-views-release.md)。
