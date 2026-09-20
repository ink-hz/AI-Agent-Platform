# 平台底色调整发布

2026-09-20 https://agent.orbbec.com.cn/ 已发布。用户反馈页面太白，本轮仅调整三份 CSS 的底色、边界和文字色：顶栏 `#1b304b`、侧栏 `#213b59`、工作区/全景画布 `#e2eaf3`。保留白色输入、详情、卡片和原有业务节点角色色；布局尺寸、行为、权限、后端及数据未改。

- 应用提交：`084b10c361e460042efa459935dff0ad9485e43a`；上一版 `5e9420d9c43a7e5f92dfc00ea50ac2af03d670e5`。
- 镜像：`sha256:397efae87d79ae00729c87ada0c9ab2c5f03dec0993a71f1b8a5c96bc5e3bcaa`。
- 容器：`6ecbd4074717cd1442562c2c3fc2929144447d22b8efae8f7eab735d7a43ebf5`。
- 源树：`9c3a0dffc6827db145e2bc220b5b65a2d5756be5`。
- Archive SHA-256：`ebcb0d479ca8f551eaea2fd5d9e3d8d3686e79dfc03d8b5450e5affa18d777a2`。
- Manifest SHA-256：`3a73174304b015908a4f81fc3a17c3d6f91215482081849e7dffd09f2142f1c6`。

验证：30 项相关组件回归、npm build、13 项发布事务测试通过；主要文字对比度最低 4.86:1。CSS 及事务均完成独立静态审查。发布镜像隔离中文导出及临时布局 SQLite 冒烟通过。

线上：容器 healthy/restarts=0，JS/CSS 与容器摘要一致，CSS 包含新底色和旧业务角色色；九个受保护全景接口匿名 401/private/no-store，钉钉 login-start 200，未执行 OAuth callback。24 个其他容器、子应用服务和 nginx 不变；office/hr/voc/fae 子路径响应不变；保留全景持久化挂载和运行用户写权限。无回滚，锁释放。

后续准确维护输入：`/opt/orbbec-agent-platform/private/panorama-7f4309427be6437485e551c8670fbca9/future-maintenance.json`，沿用 17 份 Compose，追加镜像/release 覆盖，共 18 份。不得用通用 deploy 脚本丢弃该维护链。

该文档的提交不等于运行应用提交。浏览器实际视觉与真实业务验收由用户完成，未宣称已验收。
