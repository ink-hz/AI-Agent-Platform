# 账号与权限搜索授权实施计划

> 使用 subagent-driven-development 分工实现与审查；持续完成已授权工作。

**Goal:** 默认管理员名单、按需花名搜索授权，保留账号与权限名称。
**Architecture:** 复用现有管理路由、审计和管理员变更状态机；在现有 users 读取上增加查询投影，其他权限迁到关联二级页面。
**Tech Stack:** FastAPI / PostgreSQL / React / TypeScript / Vitest。

## 全局约束
- 页面和导航保持「账号与权限」。
- 管理员增删仅平台所有者操作，所有者不可移除；保留 CSRF、审计、幂等和不确定结果恢复。
- 空搜索不返回人员全集；默认仅管理员；候选匹配目录显示名，绑定 internal_user_id。
- 不扩展加密实名、电话、邮箱读取；部门来自现有安全投影函数。
- 保留观察者、合作方、FAE、VOC 权限的可达入口和原后台权限边界。
- 无生产权限变更；接口和组件验证，用户浏览器验收；本轮实现不等于已部署。

## Task 1：服务端查询（后端子任务）
- [x] 增加 API 测试：管理员过滤、候选大小写/中文匹配、空查询、20 条限制和 truncated、403、字段脱敏、旧响应兼容。
- [x] GET /api/v1/manage/users 支持 view=administrators|candidates，q，limit（默认20，上限50）；无 view 保持旧契约。
- [x] administrators 返回 owner/admin；candidates 仅 owner 可查 active member，按 display_name 字面子串匹配；禁止通配符扩大。
- [x] 新投影增加 departments: string[]；旧响应不变。新响应为 {users, truncated}。不保存查询文本到审计。
- [x] 保持服务读审计，SQL 参数化、过滤/限制先于部门投影；使用 read_account_departments_v27。
- [x] 跑 backend/tests/test_governance_audit_api.py 及必要仓库 SQL 测试，报告范围与数据库集成条件。

## Task 2：管理员交互（主任务）
- [x] 新增/调整组件测试，先验证默认隐藏成员、搜索选择、重复花名、查询乱序、失败状态和管理员只读。
- [x] administratorDirectory.ts 增加 listAdministratorUsers()/searchAdministratorCandidates(query, signal) 及投影解析，保留 auth.ts 中的 listManagedUsers。
- [x] IdentityManagementPage 保留完整管理员变更状态机，删除首页 viewer/scope/panel 展开；搜索独立组件，取消与重查清空旧结果。
- [x] 管理员变更通过原 API，成功后重新读取名单；撤销成功以管理员名单中不再存在目标确认。
- [x] 保留所有现有管理员失败恢复测试并适配搜索流程。

## Task 3：授权页迁移与收尾
- [x] 观察者和合作方独立二级页面；FAE/VOC 权限关联业务工作台，通过路由和权限入口可达，按需挂载原面板。
- [x] 平台所有者之外不能打开业务授权组件，不触发它们的读取。
- [x] 简洁样式，手机宽度可换行；页面/导航/浏览器标题保持名称。
- [x] 跑相关 API、auth、identity、business permission、router、sidebar 回归和 npm run build。
- [x] 独立代码审查，处理发现；更新设计与验证记录，提交并归并主线。按用户后续指令完成发布。


## 验证记录

2026-09-21：前端 14 个相关测试文件共 324 项通过；后端 governance API 与 web session security 共 158 项通过，包含真实一次性 PostgreSQL 和 AuditWriter 集成；生产构建通过（保留现有大包体积提示）。浏览器视觉由用户验收，未执行生产变更或上线。

独立审查已完成。修正两处：截断搜索结果不允许直接授权；新增读取沿用现有审计 target=all 和严格字段契约。新增权限页的登录返回路径和访问记录亦已覆盖。

2026-09-21 用户随后授权上线，应用 `84e726e7` 已发布；发布检查、首次回滚原因与最终维护入口见[发布记录](../operations/2026-09-21-account-permissions-search-release.md)。
