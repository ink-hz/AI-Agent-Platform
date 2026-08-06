# Platform 人工用户名称全量适配设计

## 目标

AI Agent Platform 对所有 MetaBot 历史 Session、未来 Session 和逐轮问答统一显示 Flywheel 中负责人确认的人工名称。任何页面或 API 不再直接把飞书原始 `display_name` 当作最终名称。

本次不是针对单个 Session 写补丁。凡是通过同一内部 `user_id` 识别到的用户，其所有 MetaBot Agent、所有 Session 和所有 Turn 都立即使用同一人工名称。

## 已确认根因

Flywheel 的 `flywheel_identity.resolved_user_names` 已能把目标 Session 正确解析为人工名称，但 Platform 的 `platform_read.sessions` 和 `platform_read.turns` 仍直接聚合 `flywheel_identity.external_identities.display_name`。因此数据库人工映射正确，Platform API 仍返回空值或飞书原始名称。

## 方案选择

采用通用读层包装方案：

1. 将当前已验证的 `platform_read.sessions` 与 `platform_read.turns` 分别保留为内部基础视图；
2. 重新创建同名公共视图；
3. 公共视图仅对 `source_kind = 'metabot'` 的行，根据 Session 或 Turn 对应的 `sender_user_id` 连接 `flywheel_identity.resolved_user_names`；
4. `name_source` 为 `manual` 或 `feishu` 时使用 `preferred_name`，否则保留基础视图原值，禁止把 `union_id` 回退值显示在普通页面；
5. FAE、ADMIN 等独立数据源原样透传。

不选择以下方案：

- 不批量更新 `external_identities.display_name`，因为它是飞书原始观察值，会被后续同步覆盖；
- 不逐条修改 Session 或缓存，因为历史和新增数据会再次不一致；
- 不在前端维护姓名映射，因为列表、详情、API 和后续报表会产生多套口径。

## 覆盖范围

统一读层覆盖：

- Sessions 列表；
- Session 详情 API；
- 每一轮用户提问的发送者名称；
- Agent 详情中的最近 Session；
- Fleet、Operations、Review 等所有读取 `platform_read.sessions` 或 `platform_read.turns` 的后端查询；
- 已有历史数据和迁移后的新增数据。

人工映射只适用于具有 Flywheel 内部 `user_id` 的 MetaBot 数据。FAE 与 ADMIN 当前没有同一套 Flywheel 用户身份关系，因此保持原样，不伪造映射。

## 数据与隐私

- 最终名称优先级沿用 Flywheel：人工名称优先于飞书名称；
- Platform 普通读视图不显示 `union_id` 回退值；
- 不改写消息、会话、外部身份或人工映射表；
- 不把生产姓名或 Feishu ID 写入 Platform 仓库、测试夹具或日志；
- `flywheel_analyst` 只能读取最终 Platform 视图和 Flywheel 已授权的名称解析视图。

## 迁移与兼容

新增 `backend/migrations/006_manual_user_names.sql`，依赖 Flywheel 迁移 013 已存在。迁移可重复执行：第一次保留基础视图并创建最终视图，后续执行只替换最终视图。

公共 `sessions` 和 `turns` 的列名、顺序、类型保持不变，Backend 模型和 WebUI 无需修改。数据库视图替换立即对现有 API 生效，不需要重建前端或重启 Platform。

## 测试与验收

自动测试验证：

1. 新迁移同时覆盖 Session 和 Turn；
2. 使用 `resolved_user_names` 且人工名称优先；
3. 不把 `name_source = 'union_id'` 的值输出到普通页面；
4. FAE/ADMIN 行保持不变；
5. 迁移保留视图所有权和 analyst 只读权限；
6. 现有 Backend 和 WebUI 测试全部通过。

生产验收验证：

- 指定 Session 的 API 返回已确认人工名称；
- 该 Session 的所有 Turn 返回相同人工名称；
- 九个已确认用户的全部 MetaBot Session 和 Turn 均无错误名称；
- Session、Turn、消息、会话总数不变；
- 刷新现有页面即可看到正确结果。
