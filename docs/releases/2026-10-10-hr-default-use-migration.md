# 平台 owner/管理员默认使用 HR：迁移记录（2026-10-10）

主线提交 `0c7aaf28132bea7a9ecb1ed5d6b68f585353e57c` 的迁移 `112_hr_privileged_default_use.sql` 已在生产及预览 `agent_platform_control` 库应用。迁移重定义统一的 `has_agent_use_scope_v29`：当前目录内的活跃 `platform_owner` 与 `platform_admin` 对 `hr-bot` 默认允许；其他 Agent、普通成员的授权查询及现有授权记录语义保持原样。HR API 和平台 Agent 目录仍查询同一决策，数据读取继续以当前用户 ID 隔离。

执行前，两库最大迁移版本为 109；本次标准迁移器按顺序应用 110、111、112，没有切换平台 API 容器。迁移 112 的源端和服务器哈希均为 `9e6c6be16915226e5afb3959c563e497e805ed060c7b63b9d55dc86dc77b0d28`。完成后两库版本列表为 109、110、111、112；生产活跃 owner/管理员 3 人，对 HR 的决策 3 人允许。临时迁移角色成员资格核对为 false/false。旧函数定义和版本清单保存在服务器私有目录 `hr-privileged-default-20261010-001`。

一次性 PostgreSQL 授权及迁移相关测试 35/35 通过，覆盖 owner、管理员、降权与停用；未用真实用户浏览器会话验收页面。HR 前端故障另由独立 HR 提交 `f51753224a30d06ffd14393e7bc4bf67ca4384b9` 修复并发布，不能把控制库迁移单独当作岗位详情页已可用的证明。
