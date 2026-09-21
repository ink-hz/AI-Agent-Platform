# 历史任务读取修复

现象：历史任务显示通用读取失败。线上应用 1764b60a，Nginx 记录 /api/v1/brain/missions 返回 403。生产 PLATFORM_AGENT_BRAIN_ENABLED=0、PLATFORM_DIRECT_AGENT_ENABLED=1；main.py 不注册旧 Mission router，身份中间件拒绝未注册路由。MissionsPage 仍调用旧 listMissions，因而入口必然失败。

架构判断：当前使用 Conversation/Turn 保存工作记录，/api/v1/conversations 读取不受 Brain 新任务入口开关限制。生产所有者的有界只读仓库查询成功，返回 brain/direct_agent 两类记录。页面接入已有 conversationApi，不开旧 Brain 执行开关，不对错误返回空列表，不增加第二套历史聚合后端。保留 /missions 页面地址和历史任务名称，条目进入现有 /conversations/:id 详情；既有旧 Mission 深链接保持原状。范围是平台保存的对话历史，不宣称涵盖独立子应用全部记录。

页面只保留标题及「最近｜已归档」筛选。按现有 API 分页，显示标题、来源、已知执行状态和更新时间；无执行状态时不推断任务完成。错误区分失效登录、无权读取及读取失败，不再声称 Agent 服务不受影响。分页失败保留已有记录并允许重试，快速切换取消/忽略旧响应。页面按账号及角色隔离挂载。

- [x] 最小前端失败复现：真实 conversationApi 请求及解析，验证首屏不再调用旧接口。
- [x] 接入当前列表、分页、归档及正确详情链接；验证空记录、授权失败、分页重试与切换竞态。
- [x] 真实一次性数据库接口验证：Brain 禁用仍读取已有脑/专业 Agent 历史；验证所有者隔离、分页游标和归档。
- [x] 相关前端测试/构建、独立审查、主线归并。
- [x] 沿用已授权 API-only 发布；生产只读验证及记录。不开旧执行开关，不发送业务消息，不修改真实历史数据。

本地验证：105 项前端/路由测试、7 项真实一次性 PostgreSQL 接口测试、生产构建通过；独立审查无阻断项。发布事务回归 15 项通过。

应用 `b3cdfd38` 已发布，见[发布记录](../operations/2026-09-21-history-source-fix-release.md)。
