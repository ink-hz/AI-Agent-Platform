# 2026-10-30 Flywheel 统计超时与恢复展示修复

用户报告的 00:15 `business_data_unavailable` 来自本地业务使用统计。数据库日志 00:15:11 和 10:59:28 两次均逐字匹配实际运行的 `USAGE_SQL`，触发既有 5000ms statement timeout。两次分别在 00:16:11、11:01:01 自动恢复；云端原事件的 `status=resolved` 已正确保存，但共享页面组件只看原严重级别，仍显示“需关注”。

## 已发布内容与边界

修复已合入并推送主线 `08c16fd85a34a225d1cb9224da26e03ed4929d7e`。该提交在主线新增导航批次后 rebase，修复补丁与独立审查的 `6e421627` 逐字相同。

**本地读模型：** `017_usage_projection_pruning.sql`，SHA-256 `034a045b41748ed52cd5ee46c435ca4b9969a18ffe15f992d9f789c1c3b60341`，通过本提交的安全迁移器执行。仅重组 002 中原有分支表达式：MetaBot 的身份 CTE 下沉到其分支，FAE/Admin 直接 UNION ALL，避免顶层 WITH 阻止统计裁剪。应用仍使用原 `platform_read` 和 5000ms 限制，LaunchAgent 的应用代码和进程无需替换或重启。视图 OID、owner、ACL 与 Admin/DAQ/VOC 外层包装保留，analyst 仍无 FAE 原表 schema 权限。

**云端页面：** 实际运行镜像 `sha256:1f1e920f20d534b4255a938ac578e9a6442ff0efecd0970ec6791ec91ae83f54`，页面发布 SHA 为 `08c16fd8`。本次是静态范围发布，准确继承并行制造发布后的实际镜像 `sha256:104f9816e54ddde139cbac5f7baf9000ab278cb487fd2d6b27d8bd1fd73a56a3`（运行发布标记 `4fa65dee`）；后端及其制造 rule-density、OEM、DAQ 身份能力均由该基底继承。

前端从线上 c6 的源码构建，仅叠加上述主线提交的 `OperationalEventItem.tsx`。c6 与 4fa 的 WebUI Git 内容相同，原65个非 manifest载荷及完整64文件 active graph 与当前部署逐字节一致。并行发布重新生成的 Vite manifest 存在源码路径键差异，因此按实际文件名、长度、SHA 验证完整活动图；目标 manifest 保留当前和此前合法公开缓存资产。404个源码文件独立核对只有本次一个组件变化，主线新导航和新鉴权没有在本次静态发布中上线，不能用页面 SHA 推断完整主线后端已部署。

`resolved` 和恢复事件统一显示“已恢复”；active/historical 事件的原严重级别和历史事实保留。

## 验证结果

| 验证 | 结果 |
|---|---|
| 后端相关回归，含一次性 PostgreSQL、迁移器和 VOC | 14/14 通过，5.36s |
| 前端既有运维组件测试 | 30/30 通过；实际静态源码覆盖构建也单独通过 |
| TypeScript/Vite 构建 | 通过，实际覆盖构建3.88s；仅既有大 chunk 提示 |
| 独立审查 | SQL、ACL/OID、迁移器、UI、核心文档及静态发布上下文均通过 |
| 独立补充真实 PG 合成数据 | 非空 MetaBot 身份/人工姓名/反馈、verified Admin 的 raw/public Session、usage、trend 前后全行一致 |
| 实际原 usage SQL | 3660.31 → 441.97ms；trend 约28ms |
| 实际执行计划 | 总成本5469050.54 → 103532.87；FAE Session 扫描的无用详情子计划消失 |
| 业务统计与权限 | 14行统计除正常 DAQ/VOC 导入水位推进外完整哈希一致；22行trend哈希一致；全部读视图元数据和权限一致 |
| 真实采集 | 发布后连续多个 data-access 周期成功，最新11:21:57，active flywheel=0 |
| 云端事件 | 00:15与10:59故障均为resolved，恢复事件保留；FAE来源11:22成功、无来源错误 |
| 线上静态资源 | 349/349项状态、大小、SHA一致；原292、新63、重名6项字节一致 |
| 登录及权限 | 登录完整HTML按正式身份壳转换后与候选一致且no-store；health=200，匿名Session/Activity页面及API均401 |
| 运行隔离 | API healthy、其他容器未变、两把发布锁已释放 |

浏览器验收由用户负责，本轮未执行；未运行生产模型或生产评测。当前性能证据覆盖这次定位的结构性额外计算及实际采集窗口，不宣称所有未来负载或其他历史问题均已解决。

## 运维证据与回退

本地受限证据目录：`private-incidents/2026-10-30-flywheel-health-08c16fd8`。包含前后计划、统计哈希及水位差异、OID/ACL、017 ledger、旧视图DDL、校验和绑定的回退、组件/构建结果和静态发布回执；无数据库凭据或原始会话快照。

数据库迁移器起初在写入前拒绝了 `/tmp` 与其真实 `/private/tmp` 路径不一致，以及原 owner DSN 为 libpq keyword 格式的输入。使用真实路径及参数等值验证过的临时URL格式凭据后执行成功，凭据随即删除；未放宽迁移工具的路径或凭据约束。初次严格前后统计哈希不同，取证确认差异仅为正常导入的 DAQ/VOC `last_synced_at` 推进，随后验证其余完整内容哈希及trend一致。

并行制造发布期间，旧基底 guard 在构建前阻断一次；重新捕获实际104f基底后，经原样 helper 的 validate/build/plan/inspect-plan/apply 全部通过。服务器事务目录：`/opt/orbbec-agent-platform/private/flywheel-health-ui-08c16fd8-20261030/transaction`。保留准确镜像回退，数据库回退同时核对当前视图和017校验和，防止覆盖后续修复。

已安装备份检查器确认备份集 `20261030T011956089325Z-2da42c88` 正常，3项密文文件验证通过、保留策略完成、无进行中任务。

## 飞轮核心逻辑抽取

已整理现有领域规则、唯一入口、外围统计/传输边界及当前重跑/候选沉淀限制，见 `docs/architecture/2026-10-30-data-flywheel-core.md`。这是核心闭环与职责梳理，未另造状态机或重构反馈业务规则。
