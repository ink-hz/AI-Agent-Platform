# HR Unified Execution MetaBot TDD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**状态：** v0.3，2026-09-07 Owner 已授权无需逐项确认、连续本地实施。P01/P02 本地实现通过复审；M02 在隔离工作树内实施，尚未接入业务。示例是目标测试，不是已通过证据；实际结果见执行记录。
**Goal:** 让MetaBot可靠接收、执行、保存结果并恢复投递，保留原生Agent能力。
**Architecture:** v5按能力协商，先双读后切HR流量；本地PG inbox/outbox与云端账本通过既有Relay传输。core-chat新命令身份不污染旧v4日志。
**Tech Stack:** TypeScript/Vitest、现有pg/PostgreSQL、node-sdk锁版本1.64.0。

## Global Constraints

- 唯一冻结依据：../specs/2026-09-07-hr-unified-execution-contract-design.md（v0.3）；旧 hr-result-pipeline-refactor 计划停止执行。
- 评审已通过，按依赖连续本地实施与验证；不应用生产迁移、不上线、不重放业务消息，不查询未授权生产数据。
- 每个任务逐条 RED → 最小实现 → GREEN → 回归 → 独立 diff 审查；禁止先把整批模块实现完再补测试。
- 已有修改和 backend/.venv 必须保留，不删除旧补丁、不提交环境；实施时重新确认工作树与三仓库 HEAD。
- 不引入 Kafka、向量库、访客执行、ClamAV 或新的招聘审批；模型配置保持现状。
- 只迁 HR direct/飞书单聊；Brain、其他 Bot、群聊原路径和共享 Nginx 不变。
- 冻结业务上下文与哈希；普通咨询无需岗位。生产情报仅导入/读取，不能采集或调用分析模型。
- 业务材料保留一年；未决 inbox/outbox 不按固定天数删；日志和测试证据不得含简历、prompt、token。
- 未知副作用、旧执行未证明停止、回执未知均不能触发新模型执行；重传、重跑、成果恢复、发送重试分别记账。
- 所有新文件路径均为拟创建，迁移版本号在实施前查当前 master 和已应用版本后分配；不可覆盖已应用迁移。
- 不以本地测试数量或服务 healthy 宣称业务可用。真实进程故障和飞书合成验收单列，不发送真实业务消息。

## 测试与存储准备

实际仓库 /Users/neo/Developer/work/metabot-dev；本轮隔离工作树为 `.worktrees/hr-unified-execution`，分支 `feat/hr-unified-execution`，基线 `fe5ad87`，不修改主工作区。P01共享fixture是接口输入；运行时校验器不从另一个仓库隐式读取源码，应把固定契约schema纳入对应发布构建并记录hash。

已有 tests/flywheel-integration.test.ts 使用专用 FLYWHEEL_TEST_* 连接与 pg_ctl 停库故障，但依赖外部测试环境，不能把 describe.skipIf 跳过算作新持久账本验证。参考 Team 的 flywheel/tests/testdb.sh 一次性PG启动方式，不执行其全量Flywheel迁移或照搬宽泛清理。

M02随v5 store建立 tests/helpers/local-runtime-database.ts：调用本机initdb/pg_ctl构建隔离数据库和专用role，返回连接信息、cleanup；路径用mkdtemp且只清理该fixture目录、仅停止该fixture启动的PG；缺少工具明确失败，不能静默skip。测试不得加载真实FLYWHEEL_DATABASE_URL或生产配置。M01及后续各store共用helper和runtime config loader，不另建测试内存数据库。这是M02的存储基础，不增加独立任务，也不让网页v5等待飞书入口实施。

deferred、clock、sender/executor观测器是测试工具：真实schema、事务和store不mock；观测器记录实际经过边界的请求。fixture命令由P01完整cases构造，不能漏掉run/epoch/身份等字段。

## M01：持久飞书收件箱与ACK边界

**依赖：** P01共享渠道身份fixture、M02本地存储/配置基础；实现不依赖平台在线，真实上行需P07
**文件（相对metabot-dev）：** 新增 src/feishu/durable-inbox.ts、ingress-worker.ts、relay-bridge-client.ts、tests/feishu-durable-inbox.test.ts、tests/feishu-ws-ack.test.ts、tests/feishu-ingress-queue.test.ts、tests/relay-bridge-client.test.ts；修改 src/feishu/event-handler.ts、src/index.ts；回归 tests/flywheel-event-handler.test.ts。本地PostgreSQL schema迁移与此任务同交付。
**接口：** FeishuInbox.persist(VerifiedInbound)->Promise<{inboxId,duplicate}>；claimNext(channelKey)->InboxLease|null；IngressWorker.flush()->Promise<number>、wake(channelKey)->void；RelayBridgeClient.resolveChannel/submitIntake/createAttachment/uploadChunk/completeAttachment/recordReceipt各自返回P01固定envelope。channelKey为tenant/app/bot/chat/thread全键；worker注入clock与timer。VerifiedInbound须在Flywheel之外提取tenant/app/bot/open_id/union_id及原message_id，权限不从正文读取。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("does not acknowledge before the inbox commits", async () => {
  const commit = deferred<void>();
  const received = invokeRealHandlerWithInboxBarrier(commit.promise);
  expect(received.protocolReplies()).toEqual([]);
  commit.resolve();
  await received.completed;
  expect(received.protocolReplies()).toEqual([200]);
});
it("propagates durable-write failure to SDK 500", async () => {
  const received = await invokeSdkWithBrokenInbox();
  expect(received.protocolReplies()).toEqual([500]);
  expect(received.modelCalls()).toBe(0);
});
it("resumes a deferred message after restart without a terminal notification", async () => {
  const pending = await queueScenario.enqueueWhilePreviousTurnActive();
  await ingressWorker.flush();
  expect(await queueScenario.inboxStatus(pending)).toBe("deferred");
  expect(await queueScenario.noticeCount(pending, "queued_notice")).toBe(1);
  await queueScenario.restartIngressProcess();
  queueScenario.cloud.markPreviousTurnTerminal("failed");
  await queueScenario.advanceTimeBy(6000);
  expect(await queueScenario.acceptedTurnCount(pending)).toBe(1);
  expect(await queueScenario.noticeCount(pending, "queued_notice")).toBe(1);
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/feishu-durable-inbox.test.ts tests/feishu-ws-ack.test.ts tests/feishu-ingress-queue.test.ts tests/relay-bridge-client.test.ts tests/flywheel-event-handler.test.ts
~~~
- [ ] 逐个扩展的失败场景：handler→SDK真实调用链而非mock SDK ACK；实际PG提交前kill进程不能回200；提交后ACK丢失重推只一条inbox与卡片意图；下载/查群/初始卡片失败不影响落库；关闭Flywheel仍有身份；忙时后续消息持久排队；平台停5分钟后同message上行只一Turn；同message_id不同Bot独立。
- [ ] F01 RED→GREEN：只有配置文件读取的HR凭据以Authorization header进入六条固定loopback路径；不把run token或DSN发送出去；错误host/port/prefix、文件权限错误启动即拒绝新路径；401保留inbox并报告配置故障，不调用原生模型兜底。凭据轮换、网络超时、重复409验证业务键/hash不变。
- [ ] F03 RED→GREEN：连发两条/三条按ingest_seq，前项结果未知不越过，其他chat继续；deferred持久blocking_turn_id与next_attempt_at；完成/失败/取消后的5–6秒重试只接受一次；停止证明不足仍排队；无通知、进程重启、forwarding租约过期均可补扫；429遵守Retry-After且不每秒重新发网络请求。
- [ ] 排队体验RED→GREEN：首次deferred在同事务写queued_notice，正常通道5秒内尝试更新；轮询/重推/重启不新增通知；已知message_id才patch，初始create回执未知不盲发另一卡；迟到queued永不覆盖final，提示失败不阻止Turn提交。queueScenario在tests/feishu-ingress-queue.test.ts实现，只提供真实PG查询、实际消费者子进程的启停/时钟控制和受控云端HTTP服务，不mock inbox状态机；跨云端真实实现的验收留O03。
- [ ] 最小实现核心顺序：
~~~typescript
await inbox.persist(verifiedEvent);
return; // SDK只有在此后才能发送成功ACK
// catch只能记录无敏感内容的错误并继续throw；
// 下载、业务handleMessage、卡片网络调用全部移到后台消费者。
~~~
最前置handler仅对迁移的HR单聊改变；群聊/其他Bot保持既有路径。invokeRealHandlerWithInboxBarrier/invokeSdkWithBrokenInbox为测试helper，在真实PG事务边界和真实WSClient.sendMessage捕获上注入故障，不能模拟一个始终正确的ACK实现。旧event-handler外层catch必须按路由正确传播。

inbox新增持久状态received/preparing/forwarding/deferred/accepted/blocked/handled_without_turn/cancelled，保存ingest_seq、lease_epoch/expires_at、blocking_turn_id、next_attempt_at与accepted_turn_id；原输入不可覆盖。accepted、handled_without_turn、经平台幂等核对的显式cancelled才允许同chat下一条越过。未绑定输入与绑定码在引导/身份意图可靠入账后标handled_without_turn，不永久阻塞该chat，也不在绑定后自动执行旧输入；身份操作不排在业务Turn执行槽后面。loop每1秒只查due的最多20个chat，网络并发受合同限制；首次/启动扫描，30秒恢复过期租约；deferred重试5秒加0–1秒抖动，网络退避1–30秒，429优先Retry-After。wake只缩短安全的due等待，不解除平台活跃占位；不新建事件总线。

queued_notice是本地接收卡片生命周期的独立幂等用途，正文固定为“已收到，HR Agent 正在处理上一条消息，随后处理这条。”M01交付接收/排队意图与恢复发送的最小机制，M06扩展最终投递/未知回执对账；二者共用持久operation模型，禁止两套发送计数。没有message_id时挂起patch等待初始create核对，不影响业务上行。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：锁版本1.64.0的SDK测试须在安装版本升级时重跑；本地探针200/500不代替飞书真实重推验收。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m01 持久飞书收件箱与ACK边界`；不升级依赖、不推送、不部署。

## M02：v5命令身份与独立Session生命周期

**实施切片：** 为控制单次事务/协议修改范围，M02 内部按 M02a 配置/本地 PG 基础、M02b 命令校验与持久 Session、M02c 真实 HTTP 接线顺序实现并复审；不增加业务任务，不在 M02a 完成时标记整个 M02 完成。使用唯一未应用草稿 `runtime_migrations/pending/hr_runtime.sql`，存储 `schema_version=1` 与 wire v5 分离。四变量 loader 不另要求 Worker 专用的 `PLATFORM_METABOT_RUNTIME_CONTRACT`；Team 合同到生成环境的一致性仍由 O02 验证。M02b 前共享资产按 P01 代码 `62cdfce` 精确导出并记录 SHA，不能新增独立规范。

**M02a 本地验收：** `c4a3d29` / `884f30c`，58 项回归、编译器/lint 通过；规格/质量独立复审 Approved，无遗留问题。见 [M02a 执行记录](../../reviews/2026-09-07-hr-unified-execution-m02a.md)。仅基础库完成；命令/会话、HTTP、终态 outbox 和生产配置尚未接入，不代表整个 M02 或业务已可用。

**M02b 本地验收：** `428b41f` / `9770c0d`，最终 135 项回归、编译器/lint 通过；规格/质量独立复审 Approved。见 [M02b 执行记录](../../reviews/2026-09-07-hr-unified-execution-m02b.md)。失败后下一轮、重启去重、事务与取消重试占位已在真实临时 PG 验证。Python 数字字符串 expiry 差异已由 `4ed7765` 单独修复并通过 113 项回归/复审；M02c HTTP/执行入口仍未接入，不能将 M02 整项勾选或声明业务已可用。

**M02c 实施边界补充：** 源码核对发现 SDK 单次入口会改变既有 PTY 后端，旧 Bridge/Registry/Persistent 包装层另含自动恢复、LRU 或长期会话提示。M02c 仅为现有 `ptyQuery` 增加窄适配入口，保留 HR 已配置模型/工具策略/兼容与网关配置；不 resume、不复用旧历史、不自动重放。真实 HTTP + PG + 该适配器测试只替代最末端进程/网络边界。生命周期消费者必须显式提供，默认不在生产 index 启用。此切片暂只验证纯文字：带输入附件的新命令明确在接受前拒绝，不消耗序号、不忽略文件；既存命令重传仍可恢复读取。现有附件 transfer 与 M03 持久 stream/终态、M05 成果处理接齐后，才能完成 M02 全部业务覆盖并声明 v5 就绪。不是削减最终附件需求，也不能把接口测试挪用为完整业务验收。

**M02c 本地验收：** `b5fae68` / `5506629`，185 项回归、编译器/lint 通过；完整范围规格/质量独立复审 Approved。见 [M02c 执行记录](../../reviews/2026-09-07-hr-unified-execution-m02c.md)。新增控制输入拒绝保留 LF/Unicode 与已存命令恢复，不修改 parser/hash；literal TAB/其他 C0/DEL 仍不支持。M03/M04 生命周期、附件与发送端序号/取消归属尚未接齐，M02 整项继续保持进行中。

**依赖：** P01 schema/cases；保持v3/v4读取兼容
**文件（相对metabot-dev）：** 新增 src/api/routes/core-chat-v5-contract.ts、core-chat-v5-store.ts、src/runtime/local-runtime-config.ts、local-runtime-store.ts、tests/helpers/local-runtime-database.ts、tests/local-runtime-config.test.ts、tests/core-chat-v5-contract.test.ts、tests/core-chat-v5-store.test.ts；修改 src/api/routes/core-chat-contract.ts、core-chat-session-store.ts、core-chat-routes.ts；回归 tests/core-chat-session-store.test.ts、tests/core-chat-routes.test.ts。
**接口：** V5Command与P01共享JSON Schema字段一致；V5CommandStore.accept(command)->new|duplicate|conflict；endRun(commandId,outcome)；allocateCommandSeq(logicalSessionId,commandId)->number。命令业务hash排除callback/token，身份重传不新开Claude。

loadLocalRuntimeConfig(env)->LocalRuntimeConfig消费设计3.4.1四变量并安全读文件；openLocalRuntimeStore(config)->LocalRuntimeStore封装pg.Pool/短事务/关闭。store仅在授权新schema工作，SQL schema固定hr_runtime不拼接任意输入；日志/异常对DSN与凭据脱敏，读取失败不回退Flywheel URL或内存。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("allows the next command after a failed command", async () => {
  await store.accept(firstCommand);
  await store.endRun(firstCommand.commandId, { kind: "failed" });
  const accepted = await store.accept(nextCommand);
  expect(accepted.kind).toBe("new");
});
it("retransmission never starts another execution", async () => {
  await route.submit(firstCommand);
  await route.submit({ ...firstCommand, eventCallbackUrl: rotatedTrustedCallback });
  expect(await executor.startCount(firstCommand.commandId)).toBe(1);
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/local-runtime-config.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts
~~~
- [ ] 逐个扩展的失败场景：第二Turn、旧会话第67轮切换首个command_seq=1、派发前失败不消耗已接受命令序号；同command异prompt/hash拒绝；新attempt必须独立command+授权retry_of；错误/取消后下一Turn可用；回调地址合法轮换不改变业务hash且仍需认证；不同owner或context epoch不能借稳定session读取旧隐式材料。
- [ ] 配置RED→GREEN：四变量缺项/合同不一致、明文URL环境覆盖、远程DSN、超级用户/越schema权限、symlink与非0700/0600权限拒绝；正确一次性PG角色可读写hr_runtime但不能访问Flywheel或其他Bot数据；断库恢复不丢已提交命令。采用专用临时文件，不读真实运行secret；Team配置生成测试在O02，本任务用P01 fixture。
- [ ] 最小实现核心顺序：
~~~typescript
const existing = await store.find(command.commandId);
if (existing) {
  if (existing.businessHash !== canonicalBusinessHash(command)) return conflict();
  return duplicate(existing.acceptedThrough);
}
return store.insertCommandAndExecutionIntent(command);
~~~
接口返回的是持久接受不是执行完成。新store用本地PG，不向旧v4 JSONL追加不兼容行；v4恢复/搜索字段不重定义。稳定逻辑session不是默认持久Claude上下文，本期frozen_prompt只消费平台清单。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：跨Python/TS cases一致；route层也测试，不能只测store。旧v4由原测试验证原语义不改变。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m02 v5命令身份与独立Session生命周期`；不升级依赖、不推送、不部署。

## M03：唯一终态与可恢复回调outbox

**实施切片：** 按同一任务内依赖分别验收 M03a 回调契约与 PG 唯一终态/进程事务证据、M03b 实际 Relay receiver 与持久 drain/ACK/gap、M03c stream 消费与同源 Flywheel/Trace 重建。逐片复审，不增加业务任务，不以本地 outbox 库代替对端/观测集成。终态已持久不等于旧执行已停止；释放执行占位仍须 M04 停止证明。回调源端 createdAt/observedAt 数字字符串强制转换缺口随 M03a 用 TDD 修正，不更改共享 wire schema。

**M03a 本地验收：** MetaBot `672cc9f`、Platform `2592327` / `40ed7a9`；266 项 MetaBot 与 168 项 Platform 回归、编译器/lint 通过，真实自有子进程 COMMIT 前后 SIGKILL 验证通过，完整范围规格/质量独立复审 Approved。见 [M03a 执行记录](../../reviews/2026-09-07-hr-unified-execution-m03a.md)。三条日期入口的正向格式校验同时修正此前 expiry 黑名单未覆盖的数值别名；冻结 schema 不变。M03b/c 和 M04 的接收/恢复投递/观测/停止核验继续实施，不声明整个 M03 或业务完成。

**M03b 本地验收：** MetaBot `5ac4d0b` / `8260853`、Platform `f0884b0` / `4499eab`；285 项 MetaBot 与 323 项 Platform 回归、编译器/范围内 lint 通过，完整范围规格/质量独立复审 Approved。真实接收器验证提交后丢 ACK、正文中途断线、PG 回滚与重传，保持原唯一终态；详见 [M03b 执行记录](../../reviews/2026-09-07-hr-unified-execution-m03b.md)。未应用生产迁移或启用路由；M03c 观测重建、M04 停止、M05 附件、P03 派发/投影与 O02/O03 门槛继续，不声明完整 M03 或业务已可用。

**M03c 本地验收：** MetaBot `9fea6df`；359 项回归、编译器/范围内 lint、四个模块原生编译导入通过，完整范围规格/质量独立复审 Approved。实际 HTTP/PG stream owner、同源 Flywheel/Trace 重建、独立游标与超时边界见 [M03c 执行记录](../../reviews/2026-09-07-hr-unified-execution-m03c.md)。M03a/b/c 本地切片均已验收；运行启用、真实停止/心跳、附件与云端集成仍按 M04/M05/P03/P04/O02/O03 继续，不将连接池重建或本地通过数冒充业务上线。

**依赖：** M02/P01回调契约
**文件（相对metabot-dev）：** 新增 src/api/routes/core-chat-event-outbox.ts、tests/core-chat-event-outbox.test.ts、tests/core-chat-event-outbox-process.test.ts；修改 core-chat-routes.ts 与v5 store；平台侧对应P01/P04及execution_relay/worker.py、worker_store.py。
**接口：** CoreEventOutbox.append(commandId,event)->PersistedEvent；commitTerminal(commandId,outcome)->PersistedEvent；flushRun(runId,send)->Promise<Cursor>；send返回P01 CallbackAckV5。append/commitTerminal与command状态同一事务；终态后拒绝新业务终态。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("does not invent an error after a result loses its ack", async () => {
  await outbox.commitTerminal(commandId, resultOutcome);
  await transport.dropNextResponseAfterAccept();
  await outbox.flushRun(runId, transport.send);
  const reopened = await reopenStoreFromSameDatabase();
  await reopened.flushRun(runId, transport.send);
  expect(await reopened.terminalKinds(runId)).toEqual(["result"]);
  expect(await receiver.terminalKinds(runId)).toEqual(["result"]);
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/core-chat-event-outbox.test.ts tests/core-chat-event-outbox-process.test.ts tests/core-chat-routes.test.ts
~~~
- [ ] 逐个扩展的失败场景：PG终态事务前/后kill、Worker离线10分钟、ACK丢失、重复seq、同seq异内容conflict；gap expectedSeq=5补发5之后按序到终态；本地缺5隔离该run但其他run继续；cancelled在Worker离线期间仍留outbox并最终到达；取消不能delete证据；v4 204仅旧分支接受、v5必须JSON。
- [ ] 最小实现核心顺序：
~~~typescript
await database.transaction(async tx => {
  const terminal = await tx.lockTerminal(commandId);
  if (terminal) return assertSameOutcomeOrConflict(terminal, outcome);
  await tx.persistOutcome(commandId, outcome);
  await tx.appendNextEvent(commandId, outcome);
});
// transport/drain失败只安排重送，不进入业务error生成分支。
~~~
退避计数/next_attempt_at持久化，per-run隔离、并发有界。新协议不能复用Worker adopt_terminal删除未确认outbox的v4分支。reopenStoreFromSameDatabase必须断开旧连接重新创建store；进程测试另用child_process，不能仅重建对象就称crash测试。v5 Flywheel/Trace关联投影从同一持久事件重放，固定event幂等键；增加“关闭/丢弃易失Flywheel队列后重建仍补齐assistant”的RED→GREEN测试，修改src/flywheel/envelope.ts、src/flywheel/queue.ts、src/flywheel/writer.ts仅限v5适配入口，原生其他Bot语义保持。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：对端receiver使用实际Worker store和协议解析；网络可故障代理，不能mock幂等状态。目标：一条业务终态，允许多次同事件传输。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m03 唯一终态与可恢复回调outbox`；不升级依赖、不推送、不部署。

## M04：停止核验、重试预算和任务心跳

**依赖：** M02/M03；P02 reconciling/fencing
**文件（相对metabot-dev）：** 新增 src/bridge/execution-recovery-ledger.ts、tests/execution-recovery-ledger.test.ts、tests/execution-recovery-process.test.ts；修改 src/bridge/message-bridge.ts、src/bridge/session-corruption-recovery.ts、src/engines/claude/stream-processor.ts、src/engines/claude/persistent-executor.ts；回归 tests/turn-replay-budget.test.ts、tests/persistent-executor-abort.test.ts、tests/message-bridge-session-corruption.test.ts。
**接口：** RecoveryLedger.recordEffect(turnId,commandId,effect)；requestReplayPermit(turnId,evidence)->granted|denied；ExecutorStopVerifier.verify(executorIdentity)->stopped|unknown；RunHeartbeat(runId,attemptId,leaseEpoch)。principal归属、启动身份与工具证据持久化。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("keeps uncertain old execution from being replayed", async () => {
  await ledger.markReconciliationRequired(commandId);
  stopVerifier.report("unknown");
  const permit = await recovery.requestReplayPermit(turnId);
  expect(permit.kind).toBe("denied");
  expect(await runtime.newExecutionCount(turnId)).toBe(0);
});
it("never weakens cumulative effect after stream reconstruction", async () => {
  await ledger.recordEffect(turnId, commandId, "local_idempotent");
  await ledger.recordEffect(turnId, retryCommandId, "read_only");
  expect(await ledger.cumulativeEffect(turnId)).toBe("local_idempotent");
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/execution-recovery-ledger.test.ts tests/execution-recovery-process.test.ts tests/turn-replay-budget.test.ts tests/persistent-executor-abort.test.ts tests/message-bridge-session-corruption.test.ts
~~~
- [ ] 逐个扩展的失败场景：真实父进程死而测试子执行器仍活着；stop超时不宣告stopped；PID被复用启动身份不同不得误杀；本地replay已用完平台不能再给一次；证据缺失为unknown；result已在outbox优先恢复；HTTP/PTY不同后端不能用同一个closed布尔替代退出证明；心跳有无与进展分开。
- [ ] 最小实现核心顺序：
~~~typescript
const proof = await verifier.verify(identity);
if (proof.kind !== "stopped") return { kind: "denied", reason: "execution_uncertain" };
if (!evidence.complete || evidence.hasOutput || evidence.effect !== "read_only")
  return { kind: "denied", reason: "replay_not_safe" };
return ledger.consumePlatformReplayPermit(turnId);
~~~
首版不因租约过期增加自动重跑。A+仅恢复启动失败且无输出/副作用，permit上行仍经Relay签名，不直接从MetaBot访问云。示例local_idempotent累积不代表允许重跑。测试stop只操作本测试生成的子进程和精确身份，不影响真实Claude或其他Bot。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：新增run_heartbeat为v5私有事件，不冒充用户进展；持久证据即使Flywheel关闭也存在；不重置现有强副作用证据。扩展src/engines/claude/executor-registry.ts及tests/executor-registry-race.test.ts验证活跃项不被LRU淘汰，持久队列背压时同command不重复占位。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m04 停止核验、重试预算和任务心跳`；不升级依赖、不推送、不部署。

## M05：文字完成与成果上传分离

**依赖：** M03唯一终态、P04冻结成果意图/grant
**文件（相对metabot-dev）：** 新增 src/api/routes/core-chat-artifact-outbox.ts、tests/core-chat-artifact-outbox.test.ts；修改 core-chat-routes.ts、src/bridge/message-bridge.ts、src/bridge/output-handler.ts；回归 tests/message-bridge.test.ts、tests/core-chat-routes.test.ts。
**接口：** ArtifactOutbox.stage(commandId,files)->ArtifactIntent[]；uploadDue(send)->Promise<number>。stage产生持久spool/SHA/MIME/大小与原顺序；文字终态引用意图，不等待uploadDue。平台授权只允许冻结意图，迟到成果不进已终态执行序列。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("delivers valid text while output upload is blocked", async () => {
  const uploadGate = deferred<void>();
  uploader.blockUntil(uploadGate.promise);
  await executor.finish({ text: "完整面试方案", files: [pdfFixture] });
  expect(await eventStore.lastTerminal(runId)).toMatchObject({
    type: "result", payload: { text: "完整面试方案" }
  });
  expect(await artifactStore.pendingCount(runId)).toBe(1);
  uploadGate.resolve();
  await artifacts.uploadDue(uploader.send);
  expect(await executor.startCount(commandId)).toBe(1);
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/core-chat-artifact-outbox.test.ts tests/core-chat-routes.test.ts tests/message-bridge.test.ts
~~~
- [ ] 逐个扩展的失败场景：上传500/超时不吞文字；spool写失败仅文件不可用；MetaBot重启后仅恢复上传；原grant失效不能擅自续；已声明SHA与上传内容不一致拒绝；重复file同序意图只一份；多个文件独立失败/成功；PDF可下载且并非HTML错误页；文件存储不在Release。
- [ ] 最小实现核心顺序：
~~~typescript
const staged = await artifacts.stage(commandId, generatedFiles);
await events.commitTerminal(commandId, { type: "result", text, artifact_intents: staged });
artifacts.wakeUploader(); // 提示可丢，持久pending扫描仍恢复

~~~
实际executeApiTask的file回调不再await网络上传才能return正文，仅v5路径改行为；v4依旧通过原回归。必须包含PDF内容/哈希测试，不能只断言出现一个下载URL。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：MetaBot result、平台Result、附件ready和飞书投递分开记录；跨端集成证明上传停12分钟仍仅一次模型执行。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m05 文字完成与成果上传分离`；不升级依赖、不推送、不部署。

## M06：飞书持久发送意图、回执未知与分片

**依赖：** M01本地存储、P08 Delivery契约
**文件（相对metabot-dev）：** 新增 src/feishu/durable-outbox.ts、tests/feishu-durable-outbox.test.ts；修改 src/feishu/message-sender.ts、src/bridge/final-delivery.ts；回归 tests/message-sender-receipts.test.ts。
**接口：** FeishuOutbox.accept(DeliveryCommand)->duplicate|accepted；sendDue(sender)->Promise<number>；reconcileKnownMessage(operationId)->DeliveryReceipt。operation_id为36字符UUID，内容hash/目标/purpose/part_no固定，初始卡片与最终新建卡片不同ID。

- [ ] RED：实现以下目标测试及必要测试fixture；mock只放网络/执行器边界，状态来自真实本地PG。
~~~typescript
it("does not create another message beyond the dedupe window", async () => {
  await outbox.recordUnknownCreate(operationId, sentAt);
  clock.advanceBy(61 * 60 * 1000);
  await outbox.sendDue(sender);
  expect(sender.createRequests()).toHaveLength(0);
  expect(await outbox.status(operationId)).toBe("receipt_unknown");
});
it("retries the same create identity inside the verified window", async () => {
  await outbox.sendDue(sender.timeoutAfterRemoteAccept());
  await outbox.sendDue(sender);
  expect(new Set(sender.createRequests().map(r => r.uuid)).size).toBe(1);
});
~~~
- [ ] 运行：在metabot-dev执行；补齐接口后须看到业务断言失败，缺模块错误不算完成RED。
~~~sh
npx vitest run tests/feishu-durable-outbox.test.ts tests/message-sender-receipts.test.ts
~~~
- [ ] 逐个扩展的失败场景：create成功response丢失，不用未知message_id查询；相同uuid同payload重试，超过窗口人工核对；已知卡片patch重复安全，迟到进度不能盖终态；message_id落盘后上行失败只补receipt；多part顺序/目的不同uuid；初始卡片失败不阻塞最终发送；回滚后旧epoch已接受Delivery继续收尾。
- [ ] 最小实现核心顺序：
~~~typescript
if (intent.receiptUnknown && !intent.messageId && now >= intent.dedupeUntil) {
  return store.markReceiptUnknown(intent.operationId, "dedupe_window_elapsed");
}
return sender.send({
  uuid: intent.operationId,
  target: intent.target,
  content: intent.immutableContent
});
~~~
sender只能fake网络外边界，outbox与重启用真实PG。dedupeUntil基于首次发送时间，不随重试刷新。Create实际长度限制/重复响应需O03受控验收，不能从36字符能编译推导飞书已接受。
- [ ] GREEN：重跑上述同一命令与列出的原有回归；不通过不能进入下一个行为。
- [ ] 审查门槛：执行成功不依赖渠道发送；停止自动发送不抹掉receipt_unknown；不重发真实历史任务或使用生产业务用户探针。
- [ ] 仅提交本任务精确文件，消息 `feat(hr-runtime): m06 飞书持久发送意图、回执未知与分片`；不升级依赖、不推送、不部署。

## 全量回归

~~~sh
npm run test:bridge
npm run build
~~~

若改到packages中共享接口，另运行 npm run test:packages；未涉及不借机重构其他package。所有新测试都必须纳入CI test:bridge，不能仅手动运行后遗留。

MetaBot回调+平台Worker数据库的真实集成在O03执行；飞书API实测与生产发布不包含在任何M任务的“完成”定义里。
