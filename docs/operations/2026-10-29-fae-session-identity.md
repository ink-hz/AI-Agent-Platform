# FAE 会话提问人身份修复

用户批准：补齐 FAE 已验证身份到平台 `/admin/sessions` 的链路。架构层是 channel/context 的身份记录与 trace 展示，不涉及问答模型或答案质量。

## 身份契约

- 仅使用 FAE authoritative `owner_subject_id`/`owner_subject_type` 与一致的认证模式；忽略 metadata 自报身份、昵称和 IP。
- Office importer 产生 typed `verified_subject`；`CLOUD_SYNC_INCLUDE_VERIFIED_SUBJECT=true` 显式开启定时签名导出。普通 export 默认仍不包含此引用。
- 云端引用继续与会话正文一起加密保存，不上传姓名、钉钉原始 ID、手机、邮箱等身份资料。
- 会话 API 在已有会话授权之后，使用有效管理会话调用 `read_fae_session_subjects_v126`；DB 再校验 owner/admin 或 FAE scoped viewer。每页至多 100 个主体，只返回已请求主体的可信姓名材料。
- 企业姓名来自 enterprise link/internal user；外部姓名来自 subject 的绑定加密资料，按原 AAD 解密。撤销提问人的使用权限不会抹掉历史归属。
- 无 owner 的历史记录显示“提问人未知”；有可信引用但无 profile 显示主体 ID 和“姓名暂不可用”。没有 resolver 时也清除副本旧昵称，不能把旧昵称当真实姓名。
- 配置未启用 content keyring 时保留内部登录兼容；需要解析外部姓名时明确 503。已配置的 keyring 继续严格校验；不猜名字、不静默降级。

## 已证实断点

2026-10-29 只读聚合：最近 7 天源库 19 个 enterprise-owned、29 个 partner-owned、35 个无 owner legacy 会话。云端最新 20 个 FAE 会话均无 `verified_subject`/sender name。实际 LaunchAgent 的 export 未传 opt-in；服务端没有 profile 解析，列表组件排除了 FAE。

## 发布基线与范围

生产镜像 revision 标签是 `9926939e`，不能视为其完整运行源码。逐文件核对显示当前主线 `a2f15ce8` 的 511 个 backend/app 文件与实际运行相同，差异只有未纳入本次替换的 loopback_proxy、sync_remote/cli 和缺失的 ci_evidence。实际 main.py 和 observability/routes.py 的字节均与 a2 相同。

从干净 a2 archive 重建 frontend：65 个文件与线上一致，包括 index.html 及现役引用资源；仅 manifest 不同，线上另外保留旧构建的 hashed assets。

因此本次从已归并主线的本次提交制作精确增量镜像，FROM 实际运行 image，限定替换 main.py、observability/routes.py、新 identity.py、migration126 与本次完整 dist。保留其余文件与旧 hashed assets，保留全部现役 Compose overlays、环境、挂载和安全设置，不改 Nginx、不重启 FAE。

仅增量应用 migration126，不重跑无关业务迁移。控制库当前已有 001–125；发布前备份，保留旧镜像与 API-only rollback overlay。启用定时导出后，使用正常 FAE source import 更新 source_synced_at 并通过现有签名序列重发历史；不直接改写 owner 或云端明文资料。

## 验证与已知基线

红绿回归覆盖缺少解析器/同步参数、内部/外部姓名、legacy、类型不匹配、解密错误、缺密钥的 owned-auth 应用启动。一次性 PostgreSQL 覆盖真实角色/授权/会话/限制与加密导入；端到端覆盖外部真实 mapping/grant/audit、签名导出、加密落库、幂等 replay、真实 QR 会话签发与 Cookie、中间件、列表/详情及撤销后 401。提供方 code exchange 使用测试边界，无真实模型调用。

全量前端在此任务分支得到 1451 passed、8 failed、15 skipped；8 个失败在未改动的 a2 主线相同四个文件中逐一复现（authQr 3、dashboard 1、legacyHrRoutes 3、PermissionAccessPage 1），均非本次更改。相关四文件组件套件 21 passed，生产 frontend build 通过。不能把本次结果称为全量无失败。

独立静态审查发现并关闭两项问题：可选 content keyring 启动兼容、无 resolver 的旧昵称直通；复核 GO，无剩余 Critical/Important。后端最终数量、发布版本、回填覆盖与生产验收见发布回执，尚未发布前不声称已上线。

浏览器交互验收按 AGENTS.md 由用户承担；开发侧用接口、真实存储与只读聚合验收，不使用 computer use，不在生产运行问答评测。

## 实际发布与回填结果

发布源码：`28272ec4cd1c15ae165a11ecaaed28e166e70405`；实际增量镜像 `sha256:195465f0e0a740f6a343b5db8f9c52c29422f0125b0377206e7d99927f8ef290`。迁移126已应用，382,970,919字节控制库备份通过 TOC 验证，临时 migrator→owner membership 已撤销并复核为 false。

本机与公网共126次静态资源内容校验通过，JS/CSS MIME正确；71个产物文件hash一致。API healthy、启动错误0，管理页面与会话API匿名请求仍401，运行环境/挂载/安全设置保持一致，切换时其他24个运行容器未变化。全局发布锁已释放。没有改 Nginx 或重启 FAE。

正常 importer 仅刷新90个已验证 owner 的 session headers（61个 enterprise_member、29个 partner_operator），未改1527个无 owner legacy headers；源端补采和 importer 未改写 turn/trace 表，正常副本导出及只读验收继续读取已有轮次。显式启用定时导出后，签名序列16047–16050收到精确ACK，16050覆盖首次追平批次。逐条只读核对：90/90云端引用与源归属一致、90/90可信姓名解析成功、90/90详情及轮次归属一致；另外3个旧无归属样本仍未知。

生产身份解析核验使用既有有效管理会话的 DB projection 授权，未伪造新管理会话；它不是已登录浏览器验收。实际 Cookie签发/鉴权/撤销及HTTP列表/详情链路在一次性数据库端到端测试中完成。最终相关后端371 passed，前端相关21 passed；全量前端的8个既有失败另列于上文。定时导出已恢复 loaded/enabled，并实际运行退出0。

现有源采集时刻仍为每日03:20，云端push每300秒；本次另做了有界身份header补同步，不把这条链路称作实时采集。具体版本、回执和验证边界见同目录 `2026-10-29-fae-session-identity.receipt.json`。

发布过程保留两次预检失败：首次迁移在SET ROLE阶段因常态撤销的owner membership被拒绝，未应用DDL；随后沿既有bootstrap机制临时授权、执行126并在finally撤销。首次独立启动预检因沿用服务固定IP而在网络创建阶段失败，未运行应用、未切换；调整为独立预检IP且不发布端口后通过。正式激活一次通过，无回滚。
