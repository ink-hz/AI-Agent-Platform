# 组织布局发布记录

2026-09-21，首页增加组织布局：默认公司根与全部一级部门，箭头展开下级，点击名称按需读取人数与人员。范围为平台可读的全部组织；职位尚未同步。首页与接口仅平台管理员/所有者可读，其他已登录账号提示“无权限，请联系苍渊。”。通讯录独立于业务全景草稿，业务图导出不包含组织数据。

## 实际发布

| 项目 | 已核验值 |
| --- | --- |
| 应用提交 | `7e30c30ac6872ec911a0b5b44cdbac30fedd1e97`，已归并并推送 master |
| 镜像 | `sha256:a09be735c4171ad42d96b8ab53d326c3e80a170b349c95fc3dd6e3a62c3d249a` |
| API 容器 | `d2b18643e0fef9a06c724f42fb940eb362afa59cec042d0f723d4425d8e9c537` |
| 前一应用提交 | `f6c3d8f33fe27610051f3e6723c334969a9e0790` |
| 前一镜像 | `sha256:78238c6a821756661493654eded74e8b4b670722a396e469664451626ae9fc56` |
| 源码树 | `4811a171220049d685b7817cacd17aee9a258b06` |
| 源码归档 SHA-256 | `412e74bd47fc6e1f7b7936602511c92865d51eee6f0d29da0002b77072d4af84` |
| Manifest SHA-256 | `2517c53ce722d829b77de7330b9a78168ee736d220b2fc2dc487ac6f125b17ff` |
| 发布事务 | `53bfc7d7182d45a8ba8875e99d830aba`，deployed / verified |

只替换 platform-api；24 个其他容器、Nginx、相关 systemd 单元及业务子应用响应保持不变。API healthy、重启次数 0，发布锁已释放。业务布局持久目录 `/data/orbbec-agent-platform/panorama` 保持 RW 挂载与 UID/GID 10001、0700 权限。

后续维护必须使用本次完整 **21 个 Compose 输入**，保留原 20 个并追加 API 覆盖，不得只运行基础 Compose。权威维护清单位于：

`/opt/orbbec-agent-platform/private/panorama-53bfc7d7182d45a8ba8875e99d830aba/future-maintenance.json`

同目录保存 `baseline.json`、`build-receipt.json`、`receipt.json`、`post-checks.json` 及执行脚本；本地核验副本位于 `/tmp/organization-panorama-release-fixed/`，临时副本不是永久维护入口。

## 数据库迁移与首次构建问题

通过原有 `hr_agent_migrate.py --migration-set root --environment production` 执行 109；台账由 108 到 109，既有校验和不变。新增只读函数 `platform_control.read_organization_directory_v109`，仅本环境 app 角色可执行，不扩大原表权限，不触发同步。迁移文件 SHA-256：`d4d8a708bc7fc2f9bb786643af7fd4b5e2ce7325ead49a94ead120cecbacb697`。

迁移回执 `migration-receipts/6e0ac521-40d9-42e5-ad8a-69175cef62b0.json` 为 completed、cleanup_verified=true，包含账本前后值以及临时容器、owner 授予和会话的清理验证。

首次候选 `b3cc97d6` 的迁移在 Python 导入阶段失败，SQL 未执行、台账仍 108，清理通过；该候选未激活。根因是源码归档在私有 umask 下提取后，代码子目录 0700 被 COPY 到镜像，受限 root 进程没有 DAC 覆盖能力，无法遍历属于应用用户的目录。Dockerfile 将四个代码树的目录统一为 0755，秘密目录权限及进程能力限制保持。正式候选增加断网、只读、cap-drop=ALL、root 身份的迁移模块导入检查，检查通过后执行迁移。原失败回执保留在事务 `f92bedbfdbd3458c822586b6473a7287` 中。

## 验证与边界

- 后端相关回归 268 通过、0 失败、0 跳过，含真实 PostgreSQL 的权限、树完整性、去重、分页、快照变化及 HTTP 角色矩阵。
- 前端相关回归 68 通过、0 失败；覆盖默认图、详情按需读取、过期响应、撤权清空及首页集成。生产构建通过，保留已有大 chunk 提示。
- 发布事务测试 13 通过；全分支与构建权限修正分别经过独立审查，无阻塞问题。正式镜像字体与受限迁移导入检查通过。
- 线上 11 个受保护接口匿名访问均为 401、private/no-store，包含组织树和部门详情；入口、JS/CSS 哈希和登录发起验证通过，未执行登录回调。
- 使用正式容器的 app 身份执行只读 repository 查询，结构与根详情同代、成员页有界、状态合计一致、职位可用性为 false；未打印人员字段。
- 快照完成时间为北京时间 2026-09-20 20:03:07，代次 `5ea09cf1-d9eb-4a42-8492-40aa399ffc1d`；返回 926 名目录成员、199 个节点（含一个合成根，即 198 个实际部门），时效为 warning。人数不是 HR 在职统计，未将旧快照称为实时数据。

生产管理员完整登录会话、浏览器与投屏视觉验收未执行，由用户验收。线上角色访问的结论分别来自匿名 HTTP 检查、本地真实 HTTP 角色测试和生产 app 只读查询，不把它们合称为生产管理员端到端验收。

实现计划见 [组织布局实施计划](../plans/2026-09-21-organization-panorama.md)。后续仅文档提交不改变上述实际运行版本。
