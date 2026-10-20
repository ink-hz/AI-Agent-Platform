# 禅道 22.6 自动建号接口实测

2026-10-20 在本机独立的 ZenTao 22.6 / MariaDB 10.6 容器中测试，测试账号和产品均为临时数据，不含生产数据。该容器镜像为官方 `hub.zentao.net/app/zentao:22.6`；线上镜像在此基础上有安全加固，正式启用前仍须对同一镜像候选重复场景测试。

| 操作 | 隔离实例结果 |
| --- | --- |
| `POST /api.php/v2/users/login` | 管理员得到 `status=success` 和私网 Token。 |
| `POST /api.php/v1/users` | 即使提交 `passwordPlain`，此版本的口令校验返回 400；不能以此接口建号。 |
| `POST /api.php/v2/users` | 请求含 `account,password,realname,group='',visions=['rnd'],commiter=<随机关联标记>`，返回 `status=success,id`；数据库确认此时无权限组且不在私有产品白名单。官方 v2 文档未列出 `group`，因此实现复验实际组成员关系，不能只看成功响应。 |
| `GET /api.php/v1/users/{account}` | 返回准确的 `account,realname,id`；不存在时为 404。v2 的账号字符串路径在此镜像返回用户不存在，不能用来核对账号。 |
| `GET /api.php/v1/groups` | 返回组及账号映射，可核对创建后确实在目标组。 |
| `GET/PUT /api.php/v1/products/1` | 私有产品当前白名单可读取；平台完成准入重验与绑定后，PUT 仅提交 `whitelist` 数组追加账号；复读确认。撤权时移除账号并复读确认。全量 PUT 需要单写者运维约束。 |
| 新用户个人登录 | 使用随机口令的临时用户登录成功，加入白名单后其 `/api.php/v2/products` 列出私有产品。 |
| 专用建号服务账号 | 隔离实例中的服务权限组仅有 `user:create/edit/profile`、`group:browse`、`product:view/edit` 六项，且服务账号本身在私有产品白名单中。使用该账号完成全套建号、核对、撤权与恢复。 |
| 精简成员权限组 | 从默认 DEV/QA/PO 权限中选取 56 项，只覆盖需求、任务、测试、发布及必要页面。经 `/api.php` 个人免密入口登录后，四个固定目标页面均返回 200；撤销组与产品白名单后四个入口不再返回业务页面，恢复后重新返回 200。SQL 见 `deploy/cloud/sdk_zentao_groups.sql`。同一隔离实例又用正式代码跑通“无权限创建 → 授权 → 撤权”，结果分别为无组/无产品、仅 SDK 组/有产品、无组/无产品。 |
| 已有 API Token 权限变化 | 移除权限组后，原有 Token 访问 `/api.php/v2/products` 返回 403；浏览器既有会话的即时注销仍未证明。 |

镜像数据库 `zt_user.account` 为 `varchar(30)`。账号策略因此采用 `sdk_` 加 26 位小写 Base32 无填充 UUID，长度刚好 30；原设计的 32 位十六进制 UUID 超长。生产现有 SDK 产品 ID 为 1，`acl=private`，白名单当前为服务账号与人工绑定账号；自动开通需要追加白名单并复验。服务凭据应有建号及修改该产品权限，且仅从平台容器私网调用，独立于 SDK 数据读取账号。

隔离实例仅验证页面返回、路由可达与权限拒绝；业务方按仓库约定验收浏览器中的导航、表单体验。自动撤权任务最多约五分钟轮询一次，不能宣称已打开的浏览器页面即时退出。生产启用前仍需在加固镜像上复验服务权限与四个入口。
