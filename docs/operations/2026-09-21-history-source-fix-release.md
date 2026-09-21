# 历史任务读取修复发布

2026-09-21 已上线。历史任务从未注册的旧 Mission 接口改读当前 Conversation 历史；支持最近/归档、分页及现有对话详情入口。不启用旧 Brain 执行，不修改历史数据。换账号或授权失效清空列表，范围切换忽略旧响应，分页失败保留已读记录并可重试。

根因：原前端调用 `/api/v1/brain/missions`，线上 Brain 开关为 0，main.py 不注册此 router，身份中间件因此返回 403。生产有界只读查询已确认现行 Conversation 仓库可读取所有者的 brain/direct_agent 记录。通用报错中的 Agent 服务健康声明没有依据，已从该页面移除。

- 应用提交：`b3cdfd38587686467120b67ddfba413363469524`；前一应用：`1764b60ab5b96d54d31c958e7bcd24e59ef6ba02`。
- 镜像：`sha256:e8bde8798d1d87c8002ba88d61ba86721fb9c4a4840396e96a6389f6b05b644f`；API 容器：`13bc0baa9c97ff50ce9b5429bb2f15d81749ed067bb77c4af451ad0d15de4b8a`。
- 源码树：`269a93021200a3a88566aa215adf394816d5d45d`；归档 SHA-256：`172ca4241e22857a3784b5b95718f8d1f34ca52f1cc70c416713e30d3019dec6`；清单 SHA-256：`301876c19830ff4e2f153adbc7c87837ea35b0f38c7257d66922cfc99b4a9415`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-8094a850309046e08c1efd44db368b42/future-maintenance.json`；沿用 26 份 Compose 输入，仅追加 API 镜像/版本覆盖，共 27 份。

验证：105 项前端/路由测试、7 项真实一次性 PostgreSQL 接口测试、15 项发布事务测试通过；构建通过，独立审查无阻断项。数据库测试覆盖 Brain 入口关闭时读本人脑/专业历史及详情、他人隔离、分页游标、归档、身份/CSRF 边界。

线上 30 项检查通过：静态资源哈希与镜像一致，新历史组件标识存在；历史页面和列表/归档接口匿名返回 401/no-store；应用角色只读查询验证 active/archived、详情、分页。Brain 仍关闭、direct Agent 仍开启。API healthy、重启 0、发布锁释放。24 个其他容器、Nginx、systemd、持久挂载未改变。

未执行模型调用、业务消息、数据库迁移或真实历史写入。范围为平台当前保存的 Conversation 历史，不宣称覆盖所有子应用独立任务库；旧 Mission 深链接行为未变。线上仓库探查不等同于完整管理员登录 HTTP/浏览器验收；浏览器视觉和实际点击交用户验收。

证据：`/opt/orbbec-agent-platform/private/panorama-8094a850309046e08c1efd44db368b42`；本地回执：`/tmp/history-source-release`。本记录提交不改变上述应用版本。
