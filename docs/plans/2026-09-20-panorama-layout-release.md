# 四层全景首页实现与发布计划

> 执行：使用 subagent-driven-development 分工实现，逐项验证、审查后归并 master，再从准确提交发布。

目标：把已获用户批准的四层布局、关联选择与管理员编辑发布能力上线。
设计：`/Users/neo/Developer/work/Orbbec-AI-Engineering/docs/design/2026-09-20-ai-engineering-homepage-final-design.md`，来源提交 bcb56e9。配套 overview/robotics SVG/PNG 是视觉依据。
架构：受保护的结构化图数据、React 分层页面、可持久化的管理员草稿/发布版本。复用原工作区、身份、动作与证据模块。SQLite 文件放专用持久化目录，不修改现有业务数据库。
技术：现有 React/CSS/SVG、FastAPI、Python sqlite3、Pillow。不增加图引擎或外部服务。

## 全局约束

- 首屏没有经营数字、指标、长段解释、型号墙或状态段落。
- 四层默认顺序：产业位置；产品与技术；Marketing ↔ Technology；支撑体系。节点选择后位置不变。
- 首屏外的节点详情可以呈现来源、型号、业务功能。功能在对应节点；保留实际独立入口标志。
- 现有管理员/owner 访问，全景编辑写操作拒绝 hard-stale 身份；业务动作仍执行原专业权限。
- 原导航、草稿保留、请求幂等、身份撤销、展示退出、员工 `/office/` `/hr/` `/voc/` `/fae/` 保持。
- 浏览器与手机由用户验收，Agent 不开浏览器；做 API、组件、构建、受控镜像与生产检查。
- 事实数据通过受保护 API；不打入公开 JS/SVG 静态包。
- 编辑持久化、冲突检测、预览、取消、发布、恢复上一版都必须实际实现；不做假按钮。
- 图修改不触发任意 URL/代码执行；动作/资料来源使用既有白名单。

## 固定接口契约

GET `/api/v1/ai-engineering/panorama` 返回 PanoramaData。
类型定义（两个实现方必须遵守，字段均必填）：
```ts
type LayerKind = 'industry' | 'portfolio' | 'workflow' | 'support';
type GroupRole = 'upstream' | 'company' | 'downstream' | 'products' | 'technology' | 'marketing' | 'delivery' | 'support';
interface PanoramaNode {
 id: string; title: string; subtitle: string; detail: string[];
 actions: PanoramaActionId[]; source_ids: string[];
}
interface PanoramaGroup { id: string; title: string; role: GroupRole; columns: number; node_ids: string[] }
interface PanoramaLayer { id: string; title: string; kind: LayerKind; groups: PanoramaGroup[] }
interface PanoramaEdge { id: string; from: string; to: string; kind: 'supply' | 'supports' | 'feedback'; label: string }
interface PanoramaData {
 version: string; updated_at: string; title: string;
 layers: PanoramaLayer[]; nodes: PanoramaNode[]; edges: PanoramaEdge[];
 sources: {id:string;label:string;document:AiEngineeringDocumentSlug}[];
}
interface PanoramaEditorState { revision:number; published:PanoramaData; draft:PanoramaData|null; previous:PanoramaData|null }
```
`PanoramaActionId` 和 `AiEngineeringDocumentSlug` 沿用已有枚举。
GET `/panorama/draft` -> PanoramaEditorState。
PUT `/panorama/draft` JSON `{expected_revision:number, data:PanoramaData}` -> PanoramaEditorState。
DELETE `/panorama/draft` JSON `{expected_revision:number}` -> PanoramaEditorState。
POST `/panorama/publish` JSON `{expected_revision:number}` -> PanoramaEditorState。
POST `/panorama/restore` JSON `{expected_revision:number}` -> PanoramaEditorState。
冲突 409，验证 422，未启用持久化编辑 503，全部 private/no-store；常规 GET 未启用编辑时仍可读取种子。
编辑请求通过既有 CSRF 机制；角色/硬过期守卫在真实中央授权与路由两层覆盖。
共享管理员草稿，每次成功写入递增 revision；发布/恢复分配服务端 version 与 updated_at，普通保存不改变 published。恢复将 previous 内容发布为新版本，同时保留当前内容为 previous。
环境 `PLATFORM_PANORAMA_STATE_PATH=/data/agent-platform/panorama/panorama.sqlite3`，专用宿主目录 `/data/orbbec-agent-platform/panorama`，不写只读镜像根目录。
导出仍用 `/export.svg` `/export.png`，必须来自当前已发布图，保留 ?version 冲突检查，不输出旧财务首页。

种子 ID 约定：
- industry/upstream: semiconductor, optoelectronics, precision; company: orbbec; downstream: robotics, scanning, biometrics, measurement, aiot。
- portfolio/products: chip-product, camera, lidar, smart-vision, industry-systems, software; technology: chip-tech, optics, depth-algorithm, sdk-tech, calibration。
- workflow/marketing: market-insight, requirements, product-planning, marketing-sales, customer-use；delivery: research, feasibility, development, manufacturing, integration。
- support: talent, finance, procurement, quality, legal, digital, projects, office。
- 默认 robot 代表关联为 camera → robotics（标签 Gemini 330）；software → robotics（SDK/ROS 2）；chip-tech/optics → camera；sdk-tech → software。用户可编辑补充；不推断全部产品适配或责任团队。
- VOC 挂 customer-use，FAE 挂 integration；HR 挂 talent；行政挂 office；全部平台原动作挂 digital（owner-only access 仍过滤）。

## Task 1：后端内容、编辑与导出

负责 `backend/app/ai_engineering/`、`backend/app/control_plane/authorization.py`、对应 backend tests。
- [x] 先新增失败测试：无财务字段的新契约；角色拒绝、CSRF/硬过期；保存不改变公开版；并发 revision 冲突；重启后读回；发布与恢复；非法动作/悬空关系/重复ID/越界文案拒绝；SVG 转义。
- [x] 创建清晰分离的 seed/model/store/export 模块，内容依据设计和现有产品/资产底稿；有事实来源。
- [x] SQLite 事务串行化 revision 检查、内容、actor 与历史记录。首次 GET 不要求已存在 DB；启用目录时原子初始化。损坏不静默回种子。
- [x] 加入五个编辑 API 和中央权限精确路由表；不绕过硬过期或 CSRF。
- [x] 导出与当前图同源，按四层实际节点/分组/关系排版，不能仍用固定旧财务布局。
- [x] 跑 `.venv/bin/python -m pytest tests/test_ai_engineering_api.py tests/test_r1_authorization.py` 加新增模块测试；报告命令与结果。

## Task 2：前端布局、关联与编辑

负责 `webui/src/panorama/`、`webui/src/panoramaTypes.ts`；不要改 AiEngineeringPage/导航/宿主测试（主代理集成）。
- [x] 契约 parser 更新，保留严格形状、ID/引用/白名单校验；API 客户端含 CSRF 与冲突处理。
- [x] 先写失败组件测试：默认四层无数字；robot 关联；选中技术查产品；详情内动作；owner 过滤；编辑预览、保存、取消、发布、恢复；失权清除编辑。
- [x] 基于实际 SVG 视觉图实现 React/CSS 首页；无主内容滚动的桌面布局，小屏重排；关系线避免遮挡文字且只在适当选择后显示。公司为视觉中心，技术为共用底座，双向流程两行，支撑横排。
- [x] 编辑面板支持节点增删/改名/排序/分组，层分组改名排序/columns，关系增删，绑定白名单动作。拖动和按钮都可排序。保存草稿真实持久化，冲突明确保留本地修改；发布成功回调刷新主图。
- [x] Props 保留 data/onAction/onEvidence/isOwner/active；新增可选 onDataChange(data)、onAuthorizationFailure(error)、onDirtyChange(bool)。父级会连接。
- [x] 保存/发布不可在请求结果未知时自动重试写入。关闭提示丢弃本地未保存输入；认证失效交父级清除。新编辑视图不劫持业务编辑快捷键。
- [x] 跑相关 vitest 及 `npm run build`。报告必要的宿主 fixture 契约变化，主代理处理。

## Task 3：宿主集成、验证与发布（主代理）

负责 AiEngineeringPage 与宿主恢复测试、新接口真实授权集成检查、发布脚本与回执。
- [x] 接通新图数据、编辑dirty与失权回调；保留原工作区生命周期。更新旧 fixture 为新契约，不删有价值的恢复断言。
- [x] 跑前后端相关回归、构建；独立审查最终差异，解决阻断项。
- [x] 合并 master 并从准确提交 archive 构建；生产基线为 39f5a039 / sha256:74cb361439f305f21215631f8e547c261d575dc962af53e167f1023174466136。
- [x] 延续现有十三份 Compose、append API overlay，唯一额外配置是专用全景状态路径/挂载。宿主目录创建 uid/gid 10001 mode0700。回退恢复原镜像/配置，保留布局数据。
- [x] 先准备审查可回退发布事务，验证限定变更；镜像内无网络 JSON/SVG/PNG 和临时 SQLite 持久化烟测。
- [x] 执行 API-only 发布，验证健康、匿名隔离、JS/CSS 内容、员工子路径、其他服务与代理不变；记录应用提交/镜像/维护输入。
- [x] 更新设计仓库落地状态与生产回执入口；向用户提供上线地址及实际验证边界。

## 进度

- 生产基线已只读核实，healthy / restarts 0。
- 主线与 origin/master 均 37d3404c；原六份未跟踪用户材料未动。
- 原宿主/恢复/导航相关基线 15 项通过；新增宿主回调回归 3 项先失败后通过。
- 发布事务新增专用状态挂载，保留十三份既有输入；13 项故障/回退测试通过。独立静态审查发现并修复候选 overlay 与回退输入摘要绑定问题，复审无阻断。脚本与回执准备位于 `/tmp/panorama-layout-release/`。

- 最终应用回归：后端 720 项；前端 68 项（全景/宿主/导航/云端模式及页面接口）；生产构建通过。未进行浏览器验收。
- 独立应用审查发现并修复保存期间新输入丢失、共享草稿核对与预览不一致、详情重复文案契约不一致；复审无阻断。
- 默认与选中关系按实际角色和节点位置计算；分组重排不改变供给方向。导出使用当前发布数据并支持重排，默认 PNG 为 1920×1080。

- 已发布应用 `03074620241588021f358d307af576e1bdd48158`，生产检查与维护组合见 [发布回执](../operations/2026-09-20-panorama-layout-release.md)。浏览器/手机仍由用户验收。
