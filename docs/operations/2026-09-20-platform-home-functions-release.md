# 首页平台入口与全景配色发布

2026-09-20｜应用提交 `b7cfb188a452cb82ec8b2f4ebccb681d3454d0be`｜入口 https://agent.orbbec.com.cn/

用户明确：首页上面是平台功能，下面是全景图。顶部现分通用功能、平台管理、业务应用三组，固定入口不再依赖图中节点选择；下方四层全景按业务角色着色。图数据加载失败不阻塞已授权平台入口，身份失效仍清除整体内容。owner、FAE 范围、云端复审显示规则和既有导航/草稿保护保持。

## 发布身份

- 镜像 `sha256:bd50c307ec7f34cd222fdb719d2fd9d48e570a74251c0a1963c4b34d93419c4b`；API 容器 `1db8c2fe29371a2af51fc42c293dad630a23a546af4e4895afba7edd5a14467e`。
- 上一发布 `03074620241588021f358d307af576e1bdd48158`，上一镜像 `sha256:b3ae2f5d75aa808be94391c0a1da62b9deb123ef322137f6cd1332bff2090430`。
- 仅更新 platform-api 镜像/版本，十四份原 Compose 后追加一份覆盖；专用布局目录、全部挂载与其他配置相同，没有数据库迁移或重置草稿。
- 后续维护权威输入 `/opt/orbbec-agent-platform/private/panorama-4eb321bf569b423ea4fcbe4dec35e793/future-maintenance.json`，共十五份 Compose。
- 源树 `072fe97d587af62c0228fc127c9b56dd47fe47b5`；源码 archive SHA-256 `11baa1e12102cd5bbe11d74ed9b574af367f0a3fc0045adf115711c01160c46c`；manifest SHA-256 `7b3c572ec6180fb4a91258c7efd2bc6abce2135cf3085fe26360e21bc82d8055`。
- 配色单独候选 010c64f3 仅完成构建，用户追加上下布局后未激活，最终统一发布本提交。

## 验证与边界

- 32 项相关后端导出/API 回归、90 项相关前端回归通过；前端构建通过。八类角色默认文字对比度最低 5.62:1，SVG/PNG 与页面使用同一组角色配色。
- 13 项发布事务/回退测试通过，应用及发布差异独立审查无阻断。镜像内中文导出和临时 SQLite 保存/发布/恢复检查通过。
- 上线 API healthy、重启计数 0；current 正确、发布锁已释放，无回退。
- 24 个其他容器、独立应用 systemd、nginx 状态及配置保持；/office/、/hr/、/voc/、/fae/ 响应与发布前一致。
- 九个受保护全景接口匿名均 401 且 private/no-store；在线 JS/CSS 与容器摘要一致，并确认顶部功能区域与四色样式标记存在。
- 钉钉登录发起 200；未执行 OAuth 回调或使用真实管理员会话操作业务。
- 浏览器、手机与真实账号页面效果仍由用户验收；导出图已检查，不能替代浏览器验收。

回执目录 `/opt/orbbec-agent-platform/private/panorama-4eb321bf569b423ea4fcbe4dec35e793/`。后续文档提交不代表再次发布。
