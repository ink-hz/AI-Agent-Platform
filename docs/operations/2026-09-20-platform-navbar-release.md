# 恢复独立平台顶栏发布

2026-09-20｜https://agent.orbbec.com.cn/ ｜应用 `42f6fb6e0f5fa448de2f999fa991bde237ba6a57`

恢复 AppShell 原有全站顶栏（品牌、一级导航、账号）和管理中心二级导航；删除首页正文三组功能按钮区。全景配色、图数据和编辑持久化保持，展示模式暂时隐藏全站导航。唯一设计同步描述平台外壳与全景正文的关系。

- 镜像 `sha256:b5175eb9247a92297be6c2b463631777368d4a436da3172b4bf18332161f58e0`，API `edf546470c81664cbf72aae142445b0462e42effe5f226c9de509af9faaa44ee`。
- 上一发布 `b7cfb188a452cb82ec8b2f4ebccb681d3454d0be`，仅替换 API 镜像与版本；所有既有挂载/数据保留，无迁移。
- 后续维护权威输入 `/opt/orbbec-agent-platform/private/panorama-00a03f9a22f84455bde0d320cb417e82/future-maintenance.json`，共十六份 Compose；不得丢弃原有组合。
- 源树 `a8e4ac9aac61648f08353ab3ca8a13046e49b444`；archive SHA-256 `31dfd700fd7482ab13067ae3a0d350f52b5c9448ccd2e6098f2eb301225ecd4f`；manifest SHA-256 `90e39721b7fb1823c4fb419cbeb116d92cb26ab24b61ee77911b566f8fdd4640`。
- 86 项相关前端回归和构建通过，13 项事务测试通过；独立审查无阻断。验证顶栏处于 main 外、原角色/云端导航、未保存离开取消、全景读取失败仍有全站导航。
- 镜像内中文导出及临时 SQLite 烟测通过。线上 JS/CSS 与镜像一致；存在原 topbar，已移除 platform-functions 按钮区标记，角色配色仍在。
- API healthy、重启 0，current 正确，锁已释放；24 个其他容器、systemd、nginx 与四个业务子路径响应均保持。未触发回退。
- 九个受保护接口匿名 401/private/no-store；登录发起 200，未执行 OAuth 回调。浏览器/手机视觉验收由用户完成。

私有回执 `/opt/orbbec-agent-platform/private/panorama-00a03f9a22f84455bde0d320cb417e82/`。文档提交不是新的运行版本。
