# 大制造独立管理平台发布

2026-10-27，[大制造入口](https://agent.orbbec.com.cn/manufacturing)已切换为独立管理平台。路由在 Agent AppShell 外渲染，使用制造业务左侧导航、位置顶栏和实际登录账户菜单，继续复用企业登录与 owner/admin 权限。

宿主通过可选 user/onNavigatePlatform/onLogout 传递展示数据与真实账户动作；`updateUser` 更新姓名时保留草稿和 Markdown 所有权，身份/角色/CSRF 变化仍退役挂载。返回和退出在草稿保护清理后执行；异步失败、重复提交及退役回调有组件覆盖。没有新增后端接口或授权白名单。

| 项目 | 运行值 |
| --- | --- |
| 平台源码 | `22f65f19b5c9ff740c05de5a30ae4f1c5c237ef6` |
| 制造源码 | `19b58607dc75708999a4706d488eff7b9db96f44` |
| 镜像 | `orbbec-agent-platform:manufacturing-v2-22f65f19-19b58607` |
| 镜像 ID | `sha256:3a1b7d5b6e56104240ede3bad045cc11194014726514103c80984e56ff23c42b` |
| API 容器 | `4c27bc95174d20ee5d9b1f122589a6256b52ac2833e81788dd808daff9495ec3` |
| 当前维护入口 | `/opt/orbbec-agent-platform/staging/manufacturing-standalone-console-20261027/future-maintenance.json` |
| 维护清单摘要 | `9849fcb5658218e8dc7f01527b653efa3c3b2d813667ce99883d22f5677db943` |

从已评审、合并的主线提交构建，后端 delta 为 0，COPY 66 项静态产物，保留线上原有补丁。全有序 Compose 链由 41 增为 42 项；生产挂载仍为 15 项，原网络、安全配置、其他 29 个容器 ID/镜像和 Nginx 均保持。后续文档提交与实际运行源码分开记录。

合并后前端制造 133 项、平台受影响 47 项通过，0 失败/跳过；真实手册 16 图解析、TypeScript/Vite 和准确制品 IIFE 检查通过。两轮独立代码评审通过，保留一项非阻塞的宿主/库组合草稿离开测试改进。Node localStorage 和构建 chunk-size 既有警告仍记录；未重复无关后端全量套件。

隔离候选用实际应用和授权中间件、合成身份、临时 SQLite，验证业务流程、独立进程持久化及旧 `430fdca3` 源码读写兼容；不挂载生产数据库。生产 API healthy、重启 0；公网入口/health200，匿名私有制造接口/资源401且 no-store。切换前后13张业务表内容摘要一致，7个项目及关联记录保留，其中6个演示项目。

在线备份已验证：`/app/data/manufacturing/backups/8280b151ad1247acbf34f812a36a90ab/workbench.sqlite3`，987,136字节，SHA256 `861ac43039e519bcfc408faf7967ae8e6189d626a716dd23d750b2d5ecaeafa3`。回退保持当前数据/WAL，不覆盖之后业务写入。原手册摘要 `8966c7d91be4387b67271449d1c25b3214a7587a149c26c40c3a39dd035e2dfd` 不变，继续共享 Markdown/Mermaid。

浏览器桌面/手机视觉与实际角色业务验收由用户完成，尚未执行。ERP/MRP/MES 实际对接未新增或宣称连通。详细制品、备份与验证收据保留在上述私有发布目录。
