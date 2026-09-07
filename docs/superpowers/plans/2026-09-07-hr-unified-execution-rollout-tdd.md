# HR Unified Execution Rollout TDD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**状态：** v0.3，2026-09-07 Owner 已指令开始阶段 0 / P01；其余任务待执行。示例是目标测试，不是已通过证据；实际结果见执行记录。
**Goal:** 证明跨进程恢复、单聊归属切换和回滚不会丢任务或重复执行，并保持HR定向发布边界。
**Architecture:** Orbbec-Agent-Team负责HR实例配置和切换工具，平台现有发布脚本负责云端协调Worker。复用运行合同和部署纪律，先本地故障注入，再受控双渠道验收。
**Tech Stack:** Node node:test、Python pytest/subprocess、PostgreSQL、现有PM2/容器发布工具。

## Global Constraints

- 唯一冻结依据：../specs/2026-09-07-hr-unified-execution-contract-design.md（v0.3）；旧 hr-result-pipeline-refactor 计划停止执行。
- 评审已通过，当前开始阶段0/P01本地契约与兼容测试；不应用生产迁移、不上线、不重放业务消息，不查询未授权生产数据。
- 每个任务逐条 RED → 最小实现 → GREEN → 回归 → 独立 diff 审查；禁止先把整批模块实现完再补测试。
- 已有修改和 backend/.venv 必须保留，不删除旧补丁、不提交环境；实施时重新确认工作树与三仓库 HEAD。
- 不引入 Kafka、向量库、访客执行、ClamAV 或新的招聘审批；模型配置保持现状。
- 只迁 HR direct/飞书单聊；Brain、其他 Bot、群聊原路径和共享 Nginx 不变。
- 冻结业务上下文与哈希；普通咨询无需岗位。生产情报仅导入/读取，不能采集或调用分析模型。
- 业务材料保留一年；未决 inbox/outbox 不按固定天数删；日志和测试证据不得含简历、prompt、token。
- 未知副作用、旧执行未证明停止、回执未知均不能触发新模型执行；重传、重跑、成果恢复、发送重试分别记账。
- 所有新文件路径均为拟创建，迁移版本号在实施前查当前 master 和已应用版本后分配；不可覆盖已应用迁移。
- 不以本地测试数量或服务 healthy 宣称业务可用。真实进程故障和飞书合成验收单列，不发送真实业务消息。

## 仓库路径与授权边界

- Platform：/Users/neo/Developer/work/AI-Agent-Platform；开发隔离工作树按索引说明。
- MetaBot：/Users/neo/Developer/work/metabot-dev。
- Team：/Users/neo/Developer/work/Orbbec-Agent-Team。
- 本轮不修改上述两个外部仓库。本文件是未来任务计划，不构成发送飞书消息、生产查询或部署的执行记录。
- S0统计先核对精确只读数据范围；真实飞书使用专用测试用户与test-bot，不用真实业务用户，也不把内部API调用称作飞书E2E。

## O01：基线、事件间隔与运行合同验证

**文件：**
- Platform新增 scripts/hr/execution-event-gap-report.py、backend/tests/test_hr_execution_event_gap_report.py。
- Team修改 scripts/reliability/check-runtime-contract.mjs（仅确有HR契约新增时），扩展 scripts/reliability/tests/runtime-contract.test.mjs。
- 报告拟保存 docs/reviews/2026-09-07-hr-execution-baseline.md；原始业务正文不入报告。

**接口：** summarize_events(rows, as_of)->GapReport。输入只含脱敏run引用、agent/job_kind、协议、状态、created_at、event_type、event_at、terminal_at；输出分层样本数、首事件/相邻事件/尾部间隔分位与右删失计数。数据获取与纯统计分开。

- [ ] RED：完整目标测试：
~~~python
def test_inflight_silence_is_not_silently_excluded():
    rows = [
        {"run": "a", "agent": "hr-bot", "kind": "direct_agent", "protocol": "v4",
         "created": 0, "events": [1, 2], "terminal": 3, "status": "completed"},
        {"run": "b", "agent": "hr-bot", "kind": "direct_agent", "protocol": "v4",
         "created": 0, "events": [1], "terminal": None, "status": "running"},
    ]
    report = summarize_events(rows, as_of=1200)
    assert report.completed_runs == 1
    assert report.right_censored_runs == 1
    assert report.longest_observed_silence == 1199
~~~
- [ ] 在Platform/backend运行：
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_execution_event_gap_report.py
~~~
- [ ] 最小实现分别计算完整间隔与删失尾部，不能把running终点当成真实完成：
~~~python
completed_gaps = []
censored_tails = []
for row in rows:
    if row["terminal"] is None:
        censored_tails.append(as_of - (row["events"][-1] if row["events"] else row["created"]))
    else:
        timeline = [row["created"], *row["events"], row["terminal"]]
        completed_gaps.extend(b - a for a, b in zip(timeline, timeline[1:]))
~~~
- [ ] 继续RED→GREEN覆盖无事件、只有终态、失败任务、不同协议和机器时间偏移；计时优先使用同一存储接收时间，无法比较时报告时间源限制，不捏造精度。
- [ ] 准备只读SQL，使用BEGIN READ ONLY、statement_timeout和受控时间窗；只选择HR相关作业与事件元数据。不改生产超时、不导出正文、不扫描其他Bot。
- [ ] 执行前由Owner明确目标实例、起止时间/时区、允许的表与列、只读账号范围；没有这份范围记录不得连接生产。可以先用本地合成事件测试统计代码，不能把“阶段0可冻结”理解成生产查询授权。
- [ ] 对实际允许范围另行完成统计后，报告样本数、来源时间、分层P50/P90/P95/max、删失比例与“这些数据能/不能说明什么”。没有数据时保持未执行，不填虚构值。
- [ ] Team运行：
~~~sh
node --test scripts/reliability/tests/runtime-contract.test.mjs
~~~
当前合同已被平台解析器接受；若通过则不做“修复合同”的无效改动。HR新增capability只验证HR兼容，不要求改八个Bot的模型/目录。
- [ ] 限定提交脚本、测试和脱敏报告；提交消息 feat(hr-runtime): o01 measure event gaps without changing timeouts。

## O02：HR定向配置、切换与回滚

**文件：**
- Team新增 scripts/reliability/hr-execution-cutover.mjs、scripts/reliability/tests/hr-execution-cutover.test.mjs。
- Team修改 scripts/reliability/runtime-contract.mjs、generate-ecosystem.mjs、checks.mjs、deploy/metabot.runtime-contract.json；必要修改 generate-instance-configs.mjs；回归 scripts/reliability/tests/instance-configs.test.mjs、runtime-contract.test.mjs、deploy-gate.test.mjs、checks-runner.test.mjs。
- Platform必要修改 deploy/cloud/compose.yaml、deploy/cloud/deploy.sh、deploy/local-execution-worker/execution-worker.ecosystem.config.cjs；新增 backend/tests/test_hr_execution_deployment.py。
- 本地新状态目录通过现有HR实例合同解析，不硬编码到Release；云端持久数据仅 /data/<application>/。

**接口：** planCutover(observed,targetOwner)->steps|blocked。observed含HR单聊route_epoch、intake冻结状态、inbox/Attempt/未确认发送归属、v5接收能力、执行器停止证据；输出精确实例动作。applyCutover仅实施阶段在授权目标执行，dry-run无写入。

observed还需runtimeConfigValid、localIngressAuthenticated、quotaIsolationVerified；任一false时目标platform拒绝切换。每项为下述真实检查的脱敏结果，不能在调用端固定填true。合同新增对象为bots[name=hr-bot].instance.executionRuntime，四个字段与环境名逐字采用设计3.4.1；schemaVersion保持2，v4消费者应兼容可选对象。

- [ ] RED：
~~~javascript
test("cannot roll back while a platform attempt is unaccounted for", () => {
  const result = planCutover({
    scope: "hr-direct-message", epoch: 4, intakeFrozen: true,
    activeAttempts: 1, unresolvedInbox: 0, unknownExecutors: 1,
    pendingDeliveries: 1, deliveryOwnerRetained: true
  }, "metabot");
  assert.equal(result.kind, "blocked");
  assert.deepEqual(result.actions, []);
});
test("pending delivery keeps an owner after execution cutover", () => {
  const result = planCutover(drainedExecutionWithPendingDelivery, "metabot");
  assert.equal(result.kind, "ready");
  assert.equal(result.deliveryOwner, "platform");
});
test("generates only HR file references, never a database password", () => {
  const env = buildPm2Environment({}, runtimeContractWithHrExecution, hrBot);
  const config = hrBot.instance.executionRuntime;
  assert.equal(env.METABOT_RUNTIME_DATABASE_URL_FILE, config.databaseUrlFile);
  assert.equal(env.METABOT_RUNTIME_DATABASE_SCHEMA, "hr_runtime");
  assert.equal(env.METABOT_RELAY_BRIDGE_URL, config.relayBridgeUrl);
  assert.equal(env.METABOT_RELAY_BRIDGE_CREDENTIAL_FILE, config.relayBridgeCredentialFile);
  assert.equal(env.METABOT_RUNTIME_DATABASE_URL, undefined);
  assert.equal(buildPm2Environment({}, runtimeContractWithHrExecution, nonHrBot)
    .METABOT_RUNTIME_DATABASE_URL_FILE, undefined);
});
~~~
runtimeContractWithHrExecution基于现有合法合同fixture仅扩展HR实例，hrBot/nonHrBot从同一fixture选择；buildPm2Environment测试放现有deploy-gate.test.mjs，切换判断仍放hr-execution-cutover.test.mjs，不复制生成器实现。production secret文件不参与单元测试。
- [ ] 在Team运行：
~~~sh
node --test scripts/reliability/tests/hr-execution-cutover.test.mjs scripts/reliability/tests/instance-configs.test.mjs scripts/reliability/tests/runtime-contract.test.mjs scripts/reliability/tests/deploy-gate.test.mjs scripts/reliability/tests/checks-runner.test.mjs
~~~
- [ ] 最小决策顺序：
~~~javascript
if (!observed.intakeFrozen) return blocked("intake_not_frozen");
if (observed.activeAttempts || observed.unknownExecutors || observed.unresolvedInbox)
  return blocked("inflight_not_reconciled");
if (observed.pendingDeliveries && !observed.deliveryOwnerRetained)
  return blocked("delivery_owner_missing");
if (targetOwner === "platform" && !observed.runtimeConfigValid)
  return blocked("hr_runtime_configuration_invalid");
if (targetOwner === "platform" && !observed.localIngressAuthenticated)
  return blocked("local_ingress_not_ready");
if (targetOwner === "platform" && !observed.quotaIsolationVerified)
  return blocked("quota_isolation_not_verified");
return readyWithIncrementedEpoch(observed, targetOwner);
~~~
- [ ] 逐项RED→GREEN：forwarding中回滚、accepted但上行ACK丢失、已发送未回执、旧epoch消息重推、无活跃任务但子执行器仍活、群聊/单聊路由、其他Bot配置hash不变；每种情况下不能双执行。
- [ ] 测试配置动作只允许metabot-hr及本次协调Worker；拒绝pm2 restart all、按未验证名称继承环境、修改其他Bot和共享Nginx。单聊开新路径不全局关闭群聊handleMessage。
- [ ] 所有候选接收端先具备v5与旧版本读取，探测通过后才能打开发送；回滚目标必须仍能读取新数据及snapshot。不把新v5命令转成legacy重新派发。
- [ ] 合同配置逐项RED→GREEN：allowedEnvironment包含四个文件引用/非敏感配置变量，实际生成器确实注入且只注入HR；拒绝额外明文DSN/token字段、非HR的executionRuntime、相对路径/Release路径、非loopback URL、错误schema、callback端口不一致。逐实例checks读取HR自身环境，不拿default Bot第一份环境代替；其他Bot生成结果hash不变。平台MetaBotRuntimeMap.from_contract继续接受扩展合同，旧v4配置无对象时仍按旧路径运行。
- [ ] 定向预检：以agentops身份核对HR两个文件owner/0600、父目录0700、无symlink；不打印文件内容。验证专用PG角色仅有hr_runtime运行权限、不是superuser、所需schema已迁移、真实数据目录在持久实例中；Worker使用同一合同读取HR bridge凭据，不能读取MetaBot DB secret。缺配置/连接错误返回hr_runtime_configuration_invalid，不自动建库或fallback。
- [ ] F01入口预检与测试：真实loopback缺/错凭据拒绝，合法HR resolve请求只使用合成渠道身份；不得通过真实业务intake探测。read-only resolve之外的写操作仅在O03受控测试环境验证。无本地入口/能力只v4时planCutover必须blocked；P03运行时也应显式queued原因，不依赖这条门禁单独兜底。
- [ ] F02配额切换前检查：记录API进程数、process-local每桶预算120/60/120/120及有效总量、执行保留至少2连接槽/渠道最多2/附件最多1；合成三附件上传并耗尽入站桶，验证真实签名heartbeat/events/terminal与receipt继续通过、各自429携带Retry-After且只退避所属队列。证明没有隐藏共享120桶；若执行心跳/终态仍被饿死，quotaIsolationVerified=false，禁止切换。负载用隔离或已授权专用测试范围，不压真实业务。
- [ ] F03切换前检查：持久due索引、每chat排他领取、启动/30秒补扫、5秒deferred重试及queued_notice消费者均存在；仅“数据库表已建”不足以引流。
- [ ] Platform部署测试覆盖：
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_execution_deployment.py
~~~
- [ ] 生产磁盘门禁测试必须模拟根盘24GB拒绝、预计19GB拒绝、发布后76%拒绝；净增长>1GB必须列明目录/镜像。staging限定 /data/staging/<application>/<deployment_id>/，成功失败均trap精确清理，不使用宽泛rm/glob或docker system prune -a。
- [ ] 发布报告模板包含前后df、新增大小、当前+两个回滚、归档删除版本、staging、服务镜像、HTTP与业务验收、是否碰其他应用/Nginx。历史归档最多10个或30天取严；Release不携带数据、.venv、node_modules等。
- [ ] 代码/配置限定提交；没有明确上线指令不执行applyCutover或发布。

## O03：真实进程故障与双渠道业务验收

**文件：**
- Platform新增 backend/tests/test_hr_unified_process_recovery.py、deploy/cloud/accept-hr-unified-execution.sh。
- MetaBot新增 tests/hr-unified-process-recovery.test.ts。
- Team新增 scripts/reliability/hr-unified-scenario.mjs、scripts/reliability/tests/hr-unified-scenario.test.mjs。
- 验收记录保存 docs/reviews/2026-09-07-hr-unified-execution-acceptance.md，未执行场景必须标未执行，不填绿色。

**测试夹具：** 启动独立临时PG、云端API、协调Worker、本地Relay回调端、MetaBot v5；模型和飞书边界先用故障代理。每个子进程记录PID+启动身份+专用端口，cleanup只终止本次启动的精确进程。不得杀共享Postgres、真实Claude或PM2服务。

- [ ] RED代表用例：
~~~python
def test_api_outage_preserves_result_until_ingress_returns(system):
    turn = system.submit_web("生成32KiB以上面试方案并输出PDF")
    system.stop_owned_process("api")
    system.model.complete(turn, text_bytes=32768, output_pdf=True)
    assert system.local_outbox.has_terminal(turn)
    assert system.model.execution_count(turn) == 1
    system.restart_owned_process("api")
    system.wait_for_result(turn)
    assert system.cloud.assistant_count(turn) == 1
    assert system.download_pdf(turn).starts_with(b"%PDF-")
~~~
- [ ] 运行本地进程测试：
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_unified_process_recovery.py
~~~
~~~sh
npx vitest run tests/hr-unified-process-recovery.test.ts
~~~
- [ ] 每个故障先让旧实现的行为断言失败，再接入最小修复；system仅是测试进程/网络控制器，实际状态读真实数据库，不造内存成功状态。
- [ ] 完整故障矩阵：

| 场景 | 必须观察到的结果 |
|---|---|
| ACK前本地PG写失败 | SDK不成功ACK、无模型派发 |
| inbox提交后进程死 | 重推只一条消息意图，恢复不丢输入 |
| deferred期间终态通知丢失/Ingress重启 | 补扫按原键续送，同chat FIFO、其他chat不受阻，排队提示不重复 |
| 三附件上传与入站桶耗尽 | 执行心跳/终态和Delivery回执不饥饿，429只退避对应队列 |
| 两执行器同时领取 | 一方拥有有效lease，另一方不能写终态 |
| 协调Worker死、Relay继续 | 同run结果可恢复，不开启新模型 |
| MetaBot死但子执行器未停 | reconciling且禁止重跑，不能用日志closed充当证明 |
| result持久后回调响应丢失 | 只有同一result补发，没有error第二终态 |
| 中间seq丢失 | 补齐再收终态；缺失不可恢复时隔离该run |
| 取消期间断线 | cancelled及前序证据保留；不会再接受同run result |
| 文件上传/处理延迟12分钟 | 文字可见、输入可用，文件恢复不调用模型 |
| 120条历史、空会话、失败无answer | 快照正确收口，不永久轮询/不重复POST |
| 飞书发送成功但回执丢失 | 同uuid补账或receipt_unknown，不新建模型/盲发新卡 |
| 切换/回滚中有inbox和Delivery | 各记录有唯一owner，已接受投递继续收尾 |

- [ ] 专用测试用户真实飞书验收（单独执行，不能以HTTP模拟替代）：
  - 接收相同事件的重推/重复只一Turn；
  - 平台断网5分钟，本地可靠接收后恢复回答；
  - 连发两条消息，第二条先收到排队提示；第一条成功或失败后第二条自动继续，原顺序、单次执行；
  - uuid36字符create通过，同uuid重复响应行为/1小时窗口得到实测证据；
  - 文字、图片、文件、已知卡片patch、未知create回执分别验收；
  - 未绑定只提示绑定，绑定后同owner可使用岗位上下文；
  - 群聊保持原路径，不混入平台单owner会话。
- [ ] 真正业务P0闭环：普通对话→生成岗位需求/JD/JR→确认入岗位库→上传合成简历→匹配分析→候选人专属面试题→PDF下载。检查实际发送prompt/grant有官网职责要求、仅选中材料、正确候选人；产出不靠mock固定答案。
- [ ] 独立验收四项：飞书完整回复；Flywheel assistant消息；同turn关联Trace答案非空；云端同步显示回答/明确执行失败。任一缺失不能标全部完成，但观测投影失败不得回滚业务结果。
- [ ] 记录所有运行命令、时间、候选SHA、协议hash、脱敏turn/run、实际PDF哈希；测试失败先定位，不允许用重发真实用户任务作探针。
- [ ] 最终跨仓库diff审查和全量回归后，才进入用户要求的上线流程；只推送允许的主分支，不推工作特性分支，不自动迁移其他服务。
