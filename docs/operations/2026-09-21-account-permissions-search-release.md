# 账号与权限简化发布

2026-09-21 已上线。名称保持“账号与权限”，默认只显示平台所有者及管理员，通过花名／目录姓名搜索候选、按部门区分同名并选择具体账号授权。其他权限通过独立二级入口管理。沿用所有者授权、后台审计、CSRF、幂等及不确定结果恢复。

- 应用提交：`84e726e762bcc87bd7254aa2331344ede82589db`。
- 镜像：`sha256:6ff41182b823b228f42ca10130bedd7073e8bfbc9aa0bb8fcc33cb4c934037f3`。
- API 容器：`f5d2529b569cdc533651ff8655023a013fcf05e17c2fda38046ec1a672abd613`；healthy、重启 0、发布锁已释放。
- 前一应用：`e8d8c3f176822791bfe70463d7c25d1431691fd7`；前一镜像：`sha256:ec1dcd582a912e7a80b7ca52c245df74d8e686e9b2e5567ee800cf24737ebd15`。
- 源码树：`82b891caf8f174fb76c493dd8d92f04dfb9134b4`；归档 SHA-256：`a887ece4e9467b9c674bda149b8ba2866f84610169395dc5d4ad5dd7a6efd716`；清单 SHA-256：`ce74e7601eb2e7a9a03ca99be9fc4f296393a5880640e027ba1b59d80b0c862e`。
- 后续维护入口：`/opt/orbbec-agent-platform/private/panorama-b1bc5cd6f994427d89cf5eae7c570773/future-maintenance.json`。保留原 23 份 Compose 输入，仅追加平台 API 镜像/版本覆盖，共 24 份；后续必须使用完整清单。

仅替换 platform-api 及其前端资源。24 个其他容器、Nginx、systemd 单元、持久挂载及 HR／Office／VOC／FAE 入口保持不变。未执行数据库迁移、组织同步或真实账号授权变更。

## 验证

- 实现阶段：324 项前端测试、158 项后端测试通过，包含真实一次性 PostgreSQL 与 AuditWriter 集成；TypeScript/Vite 构建通过。
- 发布事务：修正检查后 15 项通过，包括未登录权限页面返回 401 时正常发布，以及匿名返回 200 时回滚。
- 线上：JS/CSS 与运行镜像资源哈希一致，新搜索及名单标识存在；容器版本、镜像、健康、重启次数与锁状态已核定。
- 五个权限页面与两个管理查询接口匿名返回 401/no-store；11 个全景保护接口匿名返回 401/private,no-store。
- 生产应用角色的只读仓库检查通过：管理员投影、空查询、实际候选搜索和部门投影；不输出人员姓名、内部标识或凭据。
- 登录发起接口通过，返回路径为 `/admin/identity`；未执行管理员登录回调、浏览器视觉验收或生产角色写入。生产仓库查询不等同于完整登录后的 HTTP 审计链验收；后者的服务端契约已由本地真实数据库测试覆盖。

## 首次尝试与修正

首次执行 `4d10c4d6b180490a9065876bb8c6d2c4` 的发布检查错误地将受保护 HTML 页面预期设为 200，检测到 401 后自动回滚并核验成功。独立读取确认原版相同页面也返回 401/no-store；管理接口同样是 no-store，并非必须带 private。

修正发布脚本中的 HTTP 契约，增加失败复现与回归测试，并在切换前先验证既有契约。第二次执行 `b1bc5cd6f994427d89cf5eae7c570773` 使用同一不可变镜像，重新记录基线后发布成功。首次失败、回滚证据与第二次成功证据分别保留，不覆盖首次记录。

初次构建与证据目录：`/opt/orbbec-agent-platform/private/panorama-4d10c4d6b180490a9065876bb8c6d2c4`。
最终发布与检查目录：`/opt/orbbec-agent-platform/private/panorama-b1bc5cd6f994427d89cf5eae7c570773`。
本地回执：`/tmp/account-permissions-release-retry/`。

本发布记录的提交不改变上述应用版本。
