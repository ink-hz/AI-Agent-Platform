# 业务工作台分类发布

2026-09-20 https://agent.orbbec.com.cn/ 已发布。首页独立置顶；AI 工作包含 AI 助手、Agent 目录、历史任务；业务工作台包含技术支持、人力资源、行政服务、客户洞察；工程笔记作为辅助入口保留。Agent 目录的具体 Agent 名称与 ID 保持，技术支持的平台页面标题同步为技术支持工作台。

- 应用提交：`6499a5a9b2306eba8bc8b98defb62652e8cb0063`；上一版 `084b10c361e460042efa459935dff0ad9485e43a`。
- 镜像：`sha256:7a255da1bd36a1ab162bc7ea78e733242a164a9eaad057b25598d9faf3734901`。
- 容器：`619bccee00756f6ef0adfe67e85d7cc01d5bca2ecc8354d5bdca419232307032`。
- 源树：`d31db5b6954169a587388e4723a81e2df8483a56`。
- Archive SHA-256：`04f880c56f42b7df1e5370d7075fb8fc9f63faa3cd91f5e725ccd756b1bb0ca8`。
- Manifest SHA-256：`86b7b206b293503ea323ad4d85bbb132d11005f180f4cf9cfb0a371b7c55fdc6`。

验证：110 项相关回归通过，最终返回链接文字调整后目录 15 项通过（与前者重叠），npm build 与 13 项发布事务测试通过。独立代码与事务审查通过，指出的中文空格已修复。无后端协议、路由、权限、持久化和独立应用修改；只读隐藏、FAE scope、深链高亮及离开保护沿用。

线上：API healthy/restarts=0；JS/CSS 摘要与容器文件一致，新分组和四个业务域标签可在生产资源核验，深蓝/蓝灰及业务角色配色仍在。九个受保护全景接口匿名 401/private/no-store，钉钉 login-start 200，未执行 OAuth callback。24 个其他容器、独立应用服务和 nginx 不变；office/hr/voc/fae 响应状态、摘要、跳转不变。全景数据库挂载及写权限保留；无回滚，发布锁已释放。

后续维护准确输入：`/opt/orbbec-agent-platform/private/panorama-aa12575b659d436dbc06cdcb569295d6/future-maintenance.json`。沿用原 18 份有序 Compose 输入，仅追加镜像/release 覆盖，共 19 份。不得从通用 deploy 脚本丢弃此维护链。

该发布文档提交不同于运行应用版本。没有浏览器视觉或实际管理员业务验收；独立子应用内部标题沿用原界面，本次调整平台入口与平台内工作台标题。
