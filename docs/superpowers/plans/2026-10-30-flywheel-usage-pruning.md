# Flywheel 统计投影裁剪实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 执行 `executing-plans` 的逐项验证与独立审查流程。

**Goal:** 统计读取不执行无关的会话详情子查询，消除已确认的主要超时来源。

**Architecture:** 保持应用使用 `platform_read` 和既有 5000ms 限制，通过第 017 版迁移把 `sessions_raw_identity` 的顶层 WITH 下沉到 MetaBot 分支；FAE/Admin 分支保持原表达式直接 UNION ALL。用 `CREATE OR REPLACE` 保留 OID、owner、ACL、身份包装和 DAQ/VOC。

**Tech Stack:** PostgreSQL、psycopg、pytest、checksummed migration runner。

## 全局约束

- 无生产模型评测、无业务数据改写、无扩大 analyst 原表权限。
- 不提高超时、不把故障标为健康、不删除历史告警。
- MetaBot、FAE、Admin、DAQ、VOC 的归属和过滤不变；保持上海自然日、distinct turn、空答案排除及完整 Session 数量。

## 任务

- [x] 对照数据库日志与实际运行 SQL；保存无业务内容的原 EXPLAIN 和视图定义。
- [x] 创建隔离 PostgreSQL 回归，确认原 stats 计划读取 FAE 详情表，修复缺失时失败。
- [x] 迁移仅重组 002 的原始分支表达式；将 017 加入现有安全迁移器白名单和权限后置检查。
- [x] 隔离验证：修复后不读取无关详情表，全部 Session 行、usage/trend 结果、所有读视图 OID/owner/ACL 不变，覆盖 DAQ/VOC、空 Session 和 7/前 7 日边界；连续应用两次仅一条迁移记录。
- [x] 独立审查迁移、权限、结果对照与 UI；合并主线准确提交 `08c16fd8`。
- [x] 生产仅执行已提交的 017 迁移：旧 view DDL、OID/ACL、统计摘要哈希均保留；迁移后原 SQL 实测 3660.31 → 441.97ms，Session 详情 SubPlan 消失，统计仅有正常来源水位推进。
- [x] 后台 data-access 连续成功，无 active flywheel；云端 FAE 读取正常。共享事件 UI 发布，349/349 项公开资源校验通过。
- [x] 详见 `docs/operations/2026-10-30-flywheel-health-repair.md`，保留有基线守卫的视图/ledger 回退及静态发布事务。
