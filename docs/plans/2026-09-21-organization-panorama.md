# 组织布局实施计划

> 执行：使用 subagent-driven-development 分别实施目录读模型与组织图组件，主代理完成集成、接口验证及发布审查。

**Goal:** 首页直接展示公司组织层级，点击部门才读取人数与人员详情。

**Architecture:** 现有钉钉同步为唯一来源；新增受限的只读 SQL 函数与管理员 API，结构和详情拆分。组织图独立于可编辑的业务全景 JSON，复用 React/CSS；真实部门不能被布局编辑改名或改父级。

**Tech Stack:** PostgreSQL / psycopg / FastAPI / React / TypeScript / Vitest。

## Global Constraints

- 范围是平台能读取到的全部组织，不排除子公司/工厂/海外，不承诺应用范围外的完整性。
- 主图没有人数、指标、头像或说明墙；名称打开详情，独立箭头展开下级。默认公司根和全部一级部门可见。
- root 是同步器合成的 Organization，展示标题“公司组织”；不能把名为公司的平级部门改成其他部门的父级。
- 详情只含名称、部门关系、通讯录状态与统计；无手机号、工资、私人信息、钉钉原始 ID。职位明确尚未同步。
- 统计包括全部目录成员状态，distinct member_key，子树总人数去重；公司总数包含未归部门人员。状态不是在职口径。
- 只允许 platform_admin/platform_owner，接口 private/no-store；角色撤销后清空组件。沿用现有 CSRF 与业务入口权限。
- 树、计数、成员页绑定 generation_id；快照更换返回409，客户端重载结构；警示和过期沿用8/24小时，失败不能显示0人数。
- 不在仓库/静态包中包含真实组织/人员数据，不写生产目录、不触发同步、不修改原有同步worker。
- 先接口/组件验证，浏览器视觉由用户验收；只跑相关检查。

## 接口契约

`GET /api/v1/ai-engineering/organization`：
```json
{"generation_id":"uuid","completed_at":"ISO8601","freshness":"fresh|warning|hard_stale","scope":"visible_directory","root_id":"uuid","departments":[{"id":"uuid","parent_id":null,"name":"Organization"}]}
```
不返回人数和人员。

`GET /api/v1/ai-engineering/organization/departments/{department_id}?generation_id=uuid&cursor=uuid&limit=50`：
```json
{"generation_id":"uuid","department_id":"uuid","direct_count":0,"total_count":0,"status_counts":{"active":0,"inactive":0,"disabled":0},"position_available":false,"members":[{"id":"uuid","name":"示例","status":"active","departments":[{"id":"uuid","name":"示例部门"}]}],"next_cursor":null}
```
详情人员为含下级的去重成员，按稳定 ID 游标分页，limit 1..100；公司根总人数包括无部门成员。无需任何人员 ID 解密。400/422输入非法、401未登录、403非管理员、404部门不存在、409代次变化、503目录不可用。

## Task 1: 后端目录读模型

Files: 新增 `backend/control_migrations/109_organization_directory.sql`、`backend/app/ai_engineering/organization.py` 与 `backend/tests/test_organization_directory.py`；修改 `ai_engineering/routes.py`、`control_plane/authorization.py`、`main.py`。

- [x] 先写角色矩阵、只读结构不含人员、重复成员去重、无部门成员、分页、同名部门、代次变化、空/过期快照和SQL实际权限测试，观察失败。
- [x] SQL `platform_control.read_organization_directory_v109(department_id uuid,generation_id uuid,cursor uuid,page_limit integer) returns jsonb`：department_id=NULL表示树；其余表示详情。只读 complete active generation，security definer +固定search_path +仅本环境app角色execute。拒绝PUBLIC和其他环境，保持原表权限。
- [x] `OrganizationDirectoryRepository(database_url).tree()` 与 `.department(department_id,generation_id,cursor=None,limit=50)` 封装只读短事务与错误映射；API复用已有管理员guard和缓存约束。由main注入现有app DSN，不使用worker或owner凭证。
- [x] 跑新测试与既有全景/身份回归。不得用SQL字符串断言代替真实PostgreSQL结果验收。

## Task 2: 组织图组件

Files: 新增 `webui/src/organization/OrganizationLayout.tsx`、`organizationApi.ts`、`organization.css` 及相关测试。

- [x] 先写主图无计数/人员、展开与选中分离、详情懒加载、游标追加、换节点旧响应忽略、409换代、权限拒绝、键盘/窄屏语义测试，观察失败。
- [x] 导出 `OrganizationLayout({active:boolean,onAuthorizationFailure:(error:AiEngineeringApiError)=>void})`。树默认根+一级，点箭头逐层展开；点击根/部门打开独立详情面板，名称和层级保持原位。浅蓝灰背景、部门淡青蓝、选中深蓝描边。
- [x] 结构初次可见加载；active=false中止请求，权限错误清空并通知父组件；后续focus复核沿用父层，重新active应更新树。详情显示时间、完整部门路径、直属/含下级人数和状态口径、职位未同步、人员分页。数字仅在详情中。
- [x] 复用fetch/platformPath，验证响应形状、防止HTML字符串注入，无新依赖/原始真实数据。失败显示可重试，不生成假树；409提示组织更新并刷新结构，旧详情清空。
- [x] 跑组件/API测试与build，报告结果。

## Task 3: 主代理集成与发布

Files: `PanoramaCanvas.tsx`、`PanoramaView.tsx`、`AiEngineeringPage.tsx` 及集成测试；必要的部署/运营记录。

- [x] 组织组件作为独立slot放在workflow后support前；不写入PanoramaData/SQLite、不影响布局发布恢复，隐藏工作区时中止组织请求。业务全景现有SVG/PNG导出仍只导出业务布局，组织不含在其中，明确入口文案。
- [x] 复核完整登录->结构->部门->分页->撤权链，角色矩阵保持；独立代码/事务审查，合并准确master。
- [x] 生产109只通过现有有账本校验的owner migration runner应用；先核对版本/台账/维护链，函数为向后兼容新增，不改现有数据/同步。
- [x] 从准确主线构建，仅替换platform-api，保留20个Compose输入及所有持久挂载，追加本次API覆盖后共21个输入。线上读取验证树/详情结构（不打印人员），匿名401/private/no-store，业务子应用与其他容器不变。
- [x] 同步唯一设计文档和准确发布记录，清理自有隔离工作区，保留用户材料。

验证记录：后台 268 项通过（含真实 PostgreSQL）；前端 68 项通过；npm build 通过，发布事务 13 项通过。全分支独立审查无重要问题。浏览器视觉未执行。

发布构建核验：首次109迁移在导入阶段失败，SQL未执行、台账仍108、清理通过。定位为私有umask提取的代码子目录0700被COPY进入镜像，受限root进程无DAC覆盖能力无法导入。Dockerfile将四个代码树的目录统一0755，秘密目录权限保持。以原失败镜像加同一修正构建一次隔离镜像，受限root导入通过且临时镜像已清除；正式构建增加同条件smoke，独立静态审查通过。

正式应用 `7e30c30a` 已上线，109迁移与权限清理、11个匿名接口和生产只读组织查询通过；页面视觉待用户验收。准确镜像、迁移回执和后续维护清单见 [发布记录](../operations/2026-09-21-organization-panorama-release.md)。
