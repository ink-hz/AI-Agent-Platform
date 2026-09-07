# HR 统一执行 M02b 本地验收记录

状态：M02b 本地切片完成，独立规格/质量复审 Approved，无遗留 Critical/Important/Minor。这里只记录命令协议与持久会话切片，不代表 M02 全部完成或业务已经可用。

## 范围与提交

- MetaBot 工作树：`/Users/neo/Developer/work/metabot-dev/.worktrees/hr-unified-execution`。
- 最终范围：`884f30c..9770c0d`，初始提交 `428b41f feat(hr-runtime): m02b durable v5 command identity and sessions`，复审修订 `9770c0d fix(hr-runtime): reject retries of cancelled commands`。
- 新增 v5 校验/哈希和命令仓储、真实 PG 测试；扩展同一未应用 `runtime_migrations/pending/hr_runtime.sql`；导出已有冻结 command schema 并记录来源。
- 未修改旧 v3/v4 路由和 JSONL store，未启用 v5 运行配置、声明持久终态能力、访问生产、执行模型或发送飞书消息；未安装依赖、推送或应用生产迁移。

## 已验证行为

稳定逻辑 Session 绑定 principal/会话/Bot，与每条命令的 command/run/attempt 身份分开。失败、取消、中断的命令只有经调用者终态证据落账后才释放占位，不再永久封死整个会话。未知执行仍占位，不能因重启自动再次执行。

命令、已接受序号和单份执行意图使用同一个 PG 事务。序号查询不消费序号，接受提交才前进；重复请求返回原记录。回调地址、令牌、租约属于单独传输状态，不能覆盖冻结业务内容。同租约合法轮换可行，旧租约不能覆盖新租约，真实 HTTP 鉴权还须 M02c 接入。

真实临时 PG 验证了并发接受、同命令竞争、失败后的下一轮、接受/终态与相邻 SQL 的原子回滚、数据库停止后重新启动、pending/claimed 执行意图保留，以及重复尝试编号的拒绝。终态 API 采用调用者事务，后续 M03 必须在同一个事务中写入真实终态/outbox。

重试命令只能先预留，`attemptNo > 1` 的执行领取目前明确拒绝为 `replay_permit_required`，待 M04 接入持久停止/副作用/预算证明；不存在内存许可开关或隐式模型重跑。

冻结协议允许 prompt/principal 中的转义 NUL。仓储用 JSON 编码 TEXT 保存原始命令及 principal 标量，实际 PG 回读保持原值和归属判断；未收紧 wire 契约或创建通用编码框架。

## TDD 与当前提交复验

实施报告记录了逐项业务 RED/GREEN，首个真实 PG RED 为“失败后下一条命令返回 conflict 而非 new”。不是将缺失模块当作 RED。命令校验包含全部 9 个共享负向 mutation、跨字段绑定、UTF-8/Unicode、最大 envelope、源端固定哈希以及时间/字符串边界。

主会话在 `428b41f` 上重新执行：

```sh
node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/local-runtime-config.test.ts tests/local-runtime-store.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts
node /Users/neo/Developer/work/metabot-dev/node_modules/typescript/bin/tsc -p tsconfig.bridge.json --noEmit --composite false --incremental false
node /Users/neo/Developer/work/metabot-dev/node_modules/eslint/bin/eslint.js src/api/routes/core-chat-v5-contract.ts src/api/routes/core-chat-v5-store.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-store.test.ts tests/helpers/local-runtime-database.ts
git diff --check
```

结果：6 文件 **134 tests passed，1.76s**，无跳过/警告；编译、lint、diff-check 通过。其中 v5 contract 48、v5 store 28、原有基础与旧协议回归 58。测试全部使用 fixture 自有临时数据库，不是生产统计或 Node 进程级崩溃矩阵。

主会话另用实际 Node 导入 TypeScript 构建产物，独立验证源 schema 与 MetaBot 导出逐字节一致、构建后 schema 深相等、manifest 深相等、固定 command hash 和 9 个负向 mutation 均通过。

| 资产 | 源/导出 SHA-256 | 构建后 SHA-256 |
| --- | --- | --- |
| command.schema.json | `118b0e193f006259a03bc9cfb905ceeee377726a66e3e38e5ee5d61ddb513ba5` | `32281abc15b6f1c0ed408b9cb421f45306e1c3e0540b948cab82044bf79f5657` |
| runtime-config.schema.json | `802094ca27ef00e44a90d0839ea21cfa3752092ae4f81329e1cc7dfa968f3e54` | `7575d12b14e8da5c32e17861e5e01324692277501d023f69f3f9f49579086de4` |

构建后 JSON 因 TypeScript 格式化而字节 SHA 不同，语义一致；没有为此重写构建系统。来源为 P01 `62cdfce`，未重新定义规范。

## 具体遗留与跨任务边界

1. **P01 新发现的校验缺口：** Python `AwareDatetime` 接受数字字符串 `expiresAt="1788768900"`，冻结 schema 的 date-time 和实际已安装 Ajv+formats 拒绝。TS 保持 schema 与语义的交集；主会话已独立复现，安排窄范围 Python wire 校验修复。不能据本记录宣称所有输入的跨语言校验完全一致。当前 Python 环境的 jsonschema FormatChecker 未启用有效 date-time 校验，不能作为这项格式的证据。
2. **M02c/P03：** 真实 HTTP 身份校验、可信 callback origin、合法传输更新与领取后的真实执行入口尚未接上。commandSeq 在 hash 内，发送端须明确鉴权序号协商；当前只有非消费型内部 lookup，不能声称 HTTP 协商端点已存在。
3. **M03：** 真正的唯一终态、结果与 outbox 原子提交、ACK/gap 和进程级恢复尚未实现；本切片只验证其事务组合接口。
4. **M04：** 旧执行器停止、累计副作用、持久重试许可尚未实现。未知 claimed 命令不恢复为 pending，重试领取继续 fail-closed。
5. **O02/O03：** 生产迁移编号/角色配置/发布打包、故障矩阵及实际渠道验收另行执行。没有获得生产查询、切换或业务重放授权。

## 独立复审闭环

初审发现 1 项 Important：此前仅排除 completed 重试前驱，漏掉 cancelled，导致被取消任务还能消耗序号和会话占位。修订先以真实 PG 测试复现 `new` 而非 `conflict`（1 failed / 28 passed），再限定前驱只能是 failed/interrupted。新测试同时确认没有新增命令/意图、序号不变、占位为空、下一独立 Turn 可接受。

在 `9770c0d` 上，主会话再次执行同一组 6 文件回归：**135 tests passed，1.74s**；编译器、两处变更文件的 lint 均通过，无警告或跳过。完整复审范围为 `884f30c..9770c0d`，规格与质量均 Approved，无遗留问题。构建产物验证对应 `428b41f` 的 parser/schema；修订未改这两部分，不将旧构建误记为新 store 的构建证据。

以上跨任务事项均已落实到 M02c/P03/M03/M04/O02/O03 及单独的 Python wire 修复，属于明确未完成的集成/发布验收，不冒充本切片已经提供。继续实施无需 Owner 逐项确认；生产边界保持不变。
