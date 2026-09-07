# M02a 本地持久存储基础验收

日期：2026-09-07。M02 的第一段实现，不代表命令去重、会话执行或 HTTP 链路已完成。

基线：MetaBot `fe5ad87`；首轮候选 `c4a3d29`；复审修订 `884f30c`，工作树 `metabot-dev/.worktrees/hr-unified-execution`。Platform P01 契约权威 `62cdfce`。M02a 最终规格/质量复审均通过，M02 整体仍在实施。

## 已实现边界

四变量配置 loader、固定 hr_runtime 的本地 PostgreSQL pool/短事务、唯一未应用 SQL 草稿、一次性 PG helper。只读预检检查专用角色/权限/存储版本，不修改 ACL、不创建数据库。当前 SQL 只有版本元数据，命令/Session/outbox 不在本段。

DSN 和渠道凭据仅从安全文件读取；不回退 Flywheel URL 或内存。实际连接设置不继承 PGOPTIONS 改变 search_path。真实测试只停启自己的临时 PG，没有查询生产、启动模型、发送飞书消息或更改旧 v3/v4 路由。

## 第一轮验证

配置、事务持久化/回滚、失联恢复等分别经历 RED/GREEN。真实 PG 验证已提交内容跨停启保留，未提交事务回滚，回调不自动重放。错误 UID 测试使用真实文件元数据和模拟进程 UID 的 OS 边界，不宣称启动了第二 UID 进程。

在候选 SHA 上，控制器独立执行：

```sh
node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/local-runtime-config.test.ts tests/local-runtime-store.test.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts
node /Users/neo/Developer/work/metabot-dev/node_modules/typescript/bin/tsc -p tsconfig.bridge.json --noEmit --composite false --incremental false
node /Users/neo/Developer/work/metabot-dev/node_modules/eslint/bin/eslint.js src/runtime/local-runtime-config.ts src/runtime/local-runtime-store.ts tests/local-runtime-config.test.ts tests/local-runtime-store.test.ts tests/helpers/local-runtime-database.ts
```

55 项通过（15 新增、40 旧协议），无 warning/skip；编译器与 scoped lint 无诊断。临时编译后的模块经实际 Node v26.5.0 导入和失败配置断言验证；schema 与源 JSON 深度相等。控制器还对两份导出逐字节比较 Platform 源文件：2/2 相同。

源 `runtime-config.schema.json` SHA：`802094ca27ef00e44a90d0839ea21cfa3752092ae4f81329e1cc7dfa968f3e54`；tsc 重排空白后的产物 SHA：`7575d12b14e8da5c32e17861e5e01324692277501d023f69f3f9f49579086de4`。两者语义相同，不假称字节哈希相等。源 manifest 记录源字节；cases/manifest 本段用于验证，后续 M02b 扩展命令资产构建。

## 独立复审与修订

第一轮规格/质量结论 Needs fixes，无 Critical/Minor，三项 Important 已进入真实 PG TDD 修正：

1. 持有 hr_runtime schema/对象所有权的角色，撤销普通 ACL 后仍能绕过非 owner 预检。
2. 仅按列授予 metadata UPDATE，不会被表级权限检查识别。
3. 回调吞下 SQL 错误后，COMMIT 可能实际返回 ROLLBACK；仓储不能据“驱动没有抛异常”返回成功。

修订 `884f30c` 仅修改存储和对应测试。三项都先在真实 PG 中复现，再分别修复：明确拒绝 HR schema/关系/函数所有权；单独核对 metadata 列级写权限；提交完成标签必须为 COMMIT。测试证明捕获 SQL 错误后，仓储不再返回 `apparent-success`，已插入行回滚，回调只调用一次。测试恢复自己的所有权/ACL，未修改其他数据库或真实角色。

最终控制器在 `884f30c` 重跑同一套命令：58 项通过（18 新增、40 旧协议），无 warning/skip；TypeScript 与 scoped lint 通过。第二次独立复审规格 compliant、质量 Approved；三项 Important 全部关闭，无 Critical/Important/Minor 遗留。源/产物证明已由控制器独立核对，其余跨任务范围按下面边界保留。

O02 仍负责生产角色配置、迁移编号和 Team 合同到环境的核对；M02b/M02c/M03 仍负责命令/会话、真实 HTTP 鉴权和终态 outbox。没有部署或业务可用声明。
