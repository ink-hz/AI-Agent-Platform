# HR Unified Execution Platform TDD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**状态：** v0.3，P01、P02 本地实现及 P03a 归属/续租切片已通过规格/质量独立复审；P03 后续集成与其他任务继续按依赖实施。未勾选原始示例不是通过证据，具体范围和实际结果见各执行记录。
**Goal:** 先让HR网页拥有唯一执行归属和一致读取，再接入飞书命令与投递。
**Architecture:** 在现有agent_brain/execution_relay包内新增小型契约、仓储和读取组件。保留Mission授权兼容数据，禁止新旧执行器同时推进；不为本轮物理去表。
**Tech Stack:** Python3/pytest/psycopg/PostgreSQL；React/TypeScript/Vitest。

## Global Constraints

- 唯一冻结依据：../specs/2026-09-07-hr-unified-execution-contract-design.md（v0.3）；旧 hr-result-pipeline-refactor 计划停止执行。
- 评审已通过，Owner明确无需逐项确认，P01完成后按依赖连续本地实施与验证；不应用生产迁移、不上线、不重放业务消息，不查询未授权生产数据。
- 每个任务逐条 RED → 最小实现 → GREEN → 回归 → 独立 diff 审查；禁止先把整批模块实现完再补测试。
- 已有修改和 backend/.venv 必须保留，不删除旧补丁、不提交环境；实施时重新确认工作树与三仓库 HEAD。
- 不引入 Kafka、向量库、访客执行、ClamAV 或新的招聘审批；模型配置保持现状。
- 只迁 HR direct/飞书单聊；Brain、其他 Bot、群聊原路径和共享 Nginx 不变。
- 冻结业务上下文与哈希；普通咨询无需岗位。生产情报仅导入/读取，不能采集或调用分析模型。
- 业务材料保留一年；未决 inbox/outbox 不按固定天数删；日志和测试证据不得含简历、prompt、token。
- 未知副作用、旧执行未证明停止、回执未知均不能触发新模型执行；重传、重跑、成果恢复、发送重试分别记账。
- 所有新文件路径均为拟创建，迁移版本号在实施前查当前 master 和已应用版本后分配；不可覆盖已应用迁移。
- 不以本地测试数量或服务 healthy 宣称业务可用。真实进程故障和飞书合成验收单列，不发送真实业务消息。

## 文件与测试组织

- 新仓储负责短事务与SQL，Worker负责调度，路由负责鉴权和序列化；不把所有逻辑继续加到ConversationPage或原repository大文件。
- PostgreSQL fixture复用 tests/test_control_plane_migration.py::control_database 与 tests/test_agent_brain_conversation_repository.py::conversation_database。
- 示例中的 attempt_repository、queued_attempt、result_projector、snapshot_reader 等fixture由所属任务测试文件建立，调用该任务公开接口并复用真实DB；fixture只建数据，不实现替代业务状态机。
- 示例未写出的导入按本任务接口所属文件引入，不能创建生产test-only查询；测试只读计数在测试adapter或SQL完成。
- 当前 master 最新迁移087，088仅在本工作分支。新迁移不预设编号：P02动手前依据届时master、088裁决和目标已应用清单，先将精确文件名记录到本计划与执行记录，再创建SQL；这一步是防冲突的编号分配，不允许实现者猜号或覆盖已应用文件。

## P01：共享v5样例、幂等身份与兼容解析

**完成证据：** 代码 `8311f97..62cdfce`，新增34项/指定回归98项/扩展回归161项；四项复核问题及UUID边界修复后，双项独立复审通过。详见[阶段0执行记录](../../reviews/2026-09-07-hr-unified-execution-stage-zero.md)。仅库层与离线样例，不启用运行时v5路由或发送。

**依赖：** 统一设计v0.3冻结；可与M01/M02共用同一fixture
**文件（相对Platform仓库根）：** 新增 contracts/hr-execution/v5/{README.md,command.schema.json,callback.schema.json,snapshot.schema.json,channel-bridge.schema.json,runtime-config.schema.json,cases.json}；新增 backend/app/execution_relay/contracts_v5.py；修改 backend/app/execution_relay/models.py、metabot_client.py；新增 backend/tests/{test_execution_contract_v5.py,verify_execution_contract_v5.ts}；回归 backend/tests/test_metabot_collaboration_v4.py。
**接口：** FeishuMessageIdentity(tenant_id, app_id, bot_id, message_id)；feishu_request_id(identity)->UUID；CoreChatCommandV5、CoreChatEventV5、CallbackAckV5 为严格额外字段拒绝模型。parse_v5_command(dict)->CoreChatCommandV5、parse_v5_event(dict)->CoreChatEventV5 为安全wire边界；turn_intake_content_hash(dict)->str、intake_replay_status(...) 固定纯函数裁决。真实入站存储/HTTP409由P07实施；v4入口不变。共享schema/cases由本任务产出，M02不另发明版本。

channel-bridge样例固定设计3.1.1的六个操作、Accepted/Deferred/Conflict、Deferred.blocking_turn_id/retry_after_seconds、附件块offset/SHA与DeliveryReceipt。runtime-config样例固定3.4.1四字段、HR-only范围、仅v4能力缺失原因；只含合成凭据文件路径，不含密钥。如此M01/P07、M02/O02可依同一合同独立实现，不靠先上线一端猜接口。

- [x] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
from uuid import UUID
from app.execution_relay.contracts_v5 import FeishuMessageIdentity, feishu_request_id

def test_channel_key_is_stable_uuid_and_bot_scoped():
    key = FeishuMessageIdentity("t", "app", "hr-bot", "om_123")
    other = FeishuMessageIdentity("t", "app", "other-bot", "om_123")
    assert isinstance(feishu_request_id(key), UUID)
    assert feishu_request_id(key) == feishu_request_id(key)
    assert feishu_request_id(key) != feishu_request_id(other)
~~~
- [x] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_contract_v5.py tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py
~~~
- [x] 逐个补齐的失败场景：同message_id不同tenant/app/bot不碰撞；同键异载荷conflict（持久HTTP409见P07）；v4搜索recovery仍通过、executionRecovery只能走v5；旧v4 direct第二轮仍按旧规则拒绝；v5允许稳定逻辑session与独立command；非loopback回调及篡改权限scope拒绝。Python/TS验证同一cases.json。
- [x] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~python
name = json.dumps(
    ["hr-feishu-intake-v1", identity.tenant_id, identity.app_id,
     identity.bot_id, identity.message_id],
    ensure_ascii=False, separators=(",", ":"),
)
request_id = uuid5(NAMESPACE_URL, name)
~~~
用版本分支保留cd50c02接收/公开投影边界；executionRecovery独立于search recovery。先新增接收能力，禁止提前向旧MetaBot发送v5。限定每个envelope大小，错误记录schema原因而非完整payload。
- [x] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [x] 验收与审查：跨语言case一致；未知版本明确拒绝；无模型调用；其他Bot v4 fixture不变。
- [x] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p01 共享v5样例、幂等身份与兼容解析`。不执行 `git add .`，不推送/发布。

## P02：真实数据库Attempt账本与租约排他

**本地验收完成：** `557b25a` / `d10727b`，独立规格/质量 Approved；36 项新增测试，扩展回归 263 项通过。详见 [P02 执行记录](../../reviews/2026-09-07-hr-unified-execution-p02.md)。生产编号核对仍未完成；下列清单保留为原始验收步骤，不能误读为已部署。

**执行补充（2026-09-07）：** Owner 已明确无需逐项确认，按依赖连续本地开发。当前master=`56af396`，最新编号087；本工作树088保留原样，未获目标已应用清单、不假设088状态。为不把缺少发布核对变为本地开发阻塞，唯一SQL草稿固定为 `backend/control_migrations/pending/hr_turn_attempts.sql`，仅一次性PG fixture显式加载；现有 `load_numbered_migrations` 仅扫描顶层三位编号文件，不会自动应用该草稿。不得复制第二份DDL、默认抢占089、调用生产迁移器或将编号检查勾为完成。发布前取得清单后再将同一文件移入最终编号路径并重验；P02的本地实现验收与发布编号验收分别记录。

**依赖：** P01
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/turn_attempts.py；新增 backend/tests/test_turn_attempts_database.py；新增 backend/control_migrations 下的 hr_turn_attempts 迁移（完整编号文件名先按下一步确定）；回归 backend/tests/test_agent_brain_conversation_repository.py。
**接口：** TurnAttemptRepository(dsn, codec)；create_queued(turn_id, executor_kind)->Attempt；claim_due(executor_id, lease_seconds)->Lease|None；request_cancel(owner_id,turn_id)->accepted|too_late；record_terminal(lease,evidence)->Outcome；get_for_owner(owner_id,attempt_id)->Attempt。Attempt含attempt_no；Lease含attempt_id/executor_id/lease_epoch/expires_at。公开读权限沿owner查会话。

- [ ] 编号分配：在Platform运行 `git ls-tree -r --name-only master backend/control_migrations`，与经授权取得的目标迁移清单对照；记录master SHA、088是否保留/是否已应用、分配的完整SQL路径。先更新本计划，再写测试/SQL；没有目标清单时可开发测试但不得应用迁移，禁止默认占用089。
- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_cancel_request_does_not_release_live_attempt(attempt_repository, queued_attempt):
    repo, attempt = attempt_repository, queued_attempt
    repo.request_cancel(attempt.owner_id, attempt.turn_id)
    with pytest.raises(ActiveAttemptConflict):
        repo.create_queued(attempt.turn_id, "worker_direct")
    row = repo.get_for_owner(attempt.owner_id, attempt.attempt_id)
    assert row.cancel_requested_at is not None
    assert row.status == "queued"
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_turn_attempts_database.py tests/test_agent_brain_conversation_repository.py tests/test_control_plane_migration.py
~~~
- [ ] 逐个补齐的失败场景：两进程/线程Barrier并发仅一方领取；旧/未来epoch、旧process UUID、过期租约写入全部拒绝；重复同终态幂等；result与cancelled争抢只能提交一个；queued executor_id允许NULL；取消/核对中不释放占位；无owner跨会话写入失败。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~sql
UPDATE platform_control.turn_attempts
SET status = 'completed', result_message_id = %s
WHERE attempt_id = %s AND executor_id = %s AND lease_epoch = %s
  AND lease_expires_at > clock_timestamp()
  AND status IN ('running', 'reconciling')
RETURNING attempt_id;
~~~
create/claim/finalize为短事务，不在锁内HTTP。新迁移包含FK、live部分索引、状态约束、角色grants与回滚读取兼容。fixture复用现有一次性PostgreSQL conversation_database，queued_attempt经真实Turn提交路径创建，不能直接填内存对象。get_for_owner只做产品需要的只读查询。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：断言受影响行数=1才拥有裁决权；记录终态须与Result事务接入P04，不能先独立commit completed；集成前可测事务回滚。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p02 真实数据库Attempt账本与租约排他`。不执行 `git add .`，不推送/发布。

## P03：独立云端Worker与Mission兼容适配

**实施切片：** 先做 P03a 固定 Turn 受理来源、原子 Attempt 受理、旧领取/写入/投影隔离和续租；再接同一权威下的加密命令/序号绑定、认证派发及调度。M04b 真实认证恢复依赖该归属绑定，不另建恢复派发器。来源字段固定后，旧在途 legacy Mission 不因当前会话改路由而被阻断。

**P03a 本地验收：** `df57888`，155 项相关回归通过；控制器扩展网页/V2 接口后 187 项通过，编译和新增范围 Ruff 通过；完整范围规格/质量独立复审 Approved。详见 [P03a 执行记录](../../reviews/2026-09-07-hr-unified-execution-p03a.md)，其中明确保留 64 条既有 API cookie 弃用告警及 16 条既有 Ruff 诊断，不冒称全局无告警。后续多行事务采用实际受理兼容的 Conversation→Turn→Attempt 顺序；后续绑定进展见 P03b1，P03 整项继续进行中。

**P03b1 本地验收：** Platform `55dcfbf`、MetaBot `8cb4252`，加密冻结命令、Attempt/命令/序号元数据、旧 Relay 写路径隔离及真实启动代次接收回执已完成。主控固定提交扩展验证 731/286 项通过，独立规格/质量复审 Approved；保留既有警告，详见 [P03b1 执行记录](../../reviews/2026-09-07-hr-unified-execution-p03b1.md)。P01 哈希资产不变，未增加新的执行状态权威。绑定锁顺序进一步固定为 Conversation→Turn→Attempt→binding→job，并在最后加锁后复核数据库租约。当前仅文字/工具策略绑定入口；真实上下文/任务/附件、能力先于领取、稳定回调凭据、认证派发、取消调度、P04/M04b 和进程验收仍需后续完成，不能启用新流量。

**依赖：** P02；实际派发需M02/M03能力握手
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/direct_worker.py、direct_mission_adapter.py；修改 backend/app/main.py、agent_brain/repository.py、conversation_service.py、conversation_projection.py；新增 backend/tests/test_hr_direct_worker.py、test_hr_direct_worker_process.py；回归 test_agent_brain_conversation_summary.py、test_hr_task_result_projection_database.py、test_hr_candidate_analysis_artifact_migration.py、test_hr_position_package_database.py。
**接口：** DirectWorker.tick()->int；DirectMissionAdapter.prepare(lease)->RelayJobPayload、reconcile(lease)->TerminalEvidence|None；执行记录由P02裁决。conversations.execution_owner与route_epoch从同一事务读取；不是另建HR任务状态机。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_legacy_claim_excludes_worker_owned_turn(legacy_repository, worker_turn):
    claimed = legacy_repository.claim_pending(limit=50)
    assert worker_turn.mission_id not in {row.mission_id for row in claimed}

def test_worker_reconciliation_does_not_call_planner(direct_worker, pending_result):
    direct_worker.tick()
    assert pending_result.reload().outcome == "completed"
    assert pending_result.model_requests(agent_id="agent-brain-bot") == []

def test_v4_only_executor_keeps_attempt_queued_with_reason(direct_worker, worker_turn, metabot_transport):
    metabot_transport.advertise_contracts(["core_chat_collaboration_v4"])
    direct_worker.tick()
    attempt = worker_turn.reload().attempt
    assert attempt.status == "queued"
    assert attempt.reason_code == "executor_capability_missing"
    assert attempt.transport_run_id is None
    assert metabot_transport.model_posts == []
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_direct_worker.py tests/test_hr_direct_worker_process.py tests/test_agent_brain_conversation_summary.py tests/test_hr_task_result_projection_database.py tests/test_hr_candidate_analysis_artifact_migration.py tests/test_hr_position_package_database.py
~~~
- [ ] 逐个补齐的失败场景：先停止legacy领取再允许worker owner；旧在途Mission由旧owner收尾；新owner保留064/071/077所需task关联但API不得推进；076仍由Turn/Message消费；长会话不因summary调用额外规划模型；一个慢run不堵其他终态；模型调用期间数据库锁释放。
- [ ] 能力退化RED→GREEN：仅v4、缺持久终态能力、能力探测不可达分别阻止派发；快照显示原因而非静默转圈；30秒重查且5分钟告警，用户取消能释放queued占位；能力恢复后清除原因并只派发一次原Attempt，不建legacy run。metabot_transport是实际HTTP边界的假服务，只记录真实POST，worker_turn读取真实DB；这两个fixture在本任务测试文件定义。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~sql
SELECT a.attempt_id
FROM platform_control.turn_attempts a
JOIN platform_control.conversation_turns t USING (turn_id)
JOIN platform_control.conversations c USING (conversation_id)
WHERE c.execution_owner = 'worker_direct'
  AND a.executor_kind = 'worker_direct' AND a.status = 'queued'
ORDER BY a.created_at, a.attempt_id
FOR UPDATE OF a SKIP LOCKED
LIMIT 1;
~~~
用现有ContextBuilder冻结输入；直接模式summary预算不足时使用已持久摘要/有界历史与显式完整材料引用，不调用额外planner、不静默丢用户本轮输入。外部读取在事务前准备，提交时校验版本。关闭API时本地Relay缓存终态；恢复API后才断言新上传结果入云，不能要求不存在的上行通道。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：真实子进程杀API/杀云端Worker分别验收；明确本地Relay仍依赖云端接收API。不得全局关闭其他Bot/Brain的leader。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p03 独立云端Worker与Mission兼容适配`。不执行 `git add .`，不推送/发布。

## P04：Result文字与成果恢复意图原子落库

**依赖：** P02与M03终态契约；M05文件意图契约
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/turn_result_projection.py、backend/app/agent_brain/artifact_recovery.py；修改 backend/app/attachments/result_projection.py、backend/app/attachments/artifact_service.py、backend/app/agent_brain/result_delivery.py；新增 backend/tests/test_turn_result_projection.py、backend/tests/test_result_artifact_recovery.py；回归 backend/tests/test_attachment_artifacts.py、backend/tests/test_agent_brain_result_delivery.py。
**接口：** TurnResultProjector.commit(lease,TerminalEvidence)->ResultRef；ArtifactRecovery.on_ready(attachment_id)->int、retry_due(limit)->int；TerminalEvidence由P01定义，含非空text与有界artifact_intents。ResultRef是message_id，不复制一份正文。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_feishu_text_commits_when_file_transport_is_unavailable(result_projector, feishu_active_lease, terminal_with_file, database):
    result = result_projector.commit(feishu_active_lease, terminal_with_file)
    assert database.message(result.message_id).content == terminal_with_file.text
    assert database.turn(feishu_active_lease.turn_id).status == "completed"
    assert database.artifact_intent(result.message_id).status == "pending"
    assert database.pending_delivery(result.message_id).count == 1

def test_web_result_is_available_without_push_delivery(result_projector, web_active_lease, terminal_with_file, database):
    result = result_projector.commit(web_active_lease, terminal_with_file)
    assert database.message(result.message_id).content == terminal_with_file.text
    assert database.turn(web_active_lease.turn_id).status == "completed"
    assert database.artifact_intent(result.message_id).status == "pending"
    assert database.pending_delivery(result.message_id).count == 0
~~~
feishu_active_lease通过真实绑定单聊路由创建，web_active_lease通过网页Turn入口创建；pending_delivery只查turn_deliveries，不能把conversation_result_deliveries的成果恢复行算成推送。两例均断言文字可查询，不伪造网页已送达/已读。
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_turn_result_projection.py tests/test_result_artifact_recovery.py tests/test_attachment_artifacts.py tests/test_agent_brain_result_delivery.py
~~~
- [ ] 逐个补齐的失败场景：结果commit后进程死，重建仍一条assistant、一组意图；事务中间失败全部回滚；文件上传挂起文字可用；已入库文件12分钟后解析ready可绑定，即使原upload grant已过期；未登记且不在冻结清单文件拒绝；外部task/被撤销/失败任务不能借恢复放行；32KiB中文面试题不触发旧8KiB失败；超inline上限不静默截断。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~sql
BEGIN;
-- 同事务：校验当前租约 → 插入唯一message → 更新Turn/Attempt
-- → 固定artifact_intents → 建立飞书Delivery（有目标时）
-- → 递增snapshot_version → 追加可见事件。
-- 任一写入失败全部ROLLBACK；网络上传与绑定均在COMMIT之后。
COMMIT;
~~~
冻结成果意图的新授权只允许指定Result/task/hash/字节数；不得把b2d3422扩大为任意新output grant。已处理文件的绑定就绪窗口与upload lease分开。永久拒绝错误隔离，暂时不可用指数退避+就绪通知+补扫。测试database fixture只能查询真实PostgreSQL，不伪造计数。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：跨附件角色/RLS真实测试；任务终态后发送/解析/绑定互不回滚文字；保持现有Task1 public projection隐私测试。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p04 Result文字与成果恢复意图原子落库`。不执行 `git add .`，不推送/发布。

## P05：同一读取版本快照、最新分页和SSE纯读

**依赖：** P02/P04
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/turn_snapshot.py；修改 conversation_routes.py、conversation_repository.py、conversation_models.py；新增 backend/tests/test_conversation_snapshot_database.py、test_conversation_snapshot_api.py；回归 test_agent_brain_conversation_api.py、test_agent_brain_v2_conversation_api.py。
**接口：** TurnSnapshotReader.read(owner_id,conversation_id,turn_id|None)->SnapshotV1；messages_page(owner_id,conversation_id,before_seq|None,limit=50)->MessagePage。SnapshotV1与P01schema一致；current为空返回200且turn=null。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_failure_without_answer_is_terminal(snapshot_reader, failed_turn):
    result = snapshot_reader.read(failed_turn.owner_id, failed_turn.conversation_id, failed_turn.turn_id)
    assert result.outcome.terminal is True
    assert result.outcome.kind == "failed"
    assert result.answer is None

def test_latest_page_contains_message_120(message_repository, conversation_120):
    page = message_repository.messages_page(conversation_120.owner_id, conversation_120.id, None, 50)
    assert [message.seq for message in page.items] == list(range(71, 121))
    assert page.next_before_seq == 71
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_conversation_snapshot_database.py tests/test_conversation_snapshot_api.py tests/test_agent_brain_conversation_api.py tests/test_agent_brain_v2_conversation_api.py
~~~
- [ ] 逐个补齐的失败场景：Barrier令另一连接在读取Turn和answer间提交，快照不能混新cursor旧answer；文件ready/Delivery变化也增version；success必有answer；failed/cancelled/interrupted无answer结束；50/50/20页向上无漏重；他人会话404；SSE用户读权限下无UPDATE/INSERT且不调用projection。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~sql
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
-- 在同一连接读取owner、snapshot_version、Turn、引用message、
-- enrichment、Delivery与已提交事件cursor。
COMMIT;
~~~
新snapshot_version迁移与每个公共写侧同事务更新一起交付，不能只给GET拼一个时间戳。先让独立投影消费者覆盖legacy和new owner，再从SSE移除两处写入。旧after消息接口保持，另加before分页，不复用含糊参数。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：测试使用真实readonly事务捕捉写入；读不依赖UI连接；本地断流仍可快照恢复。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p05 同一读取版本快照、最新分页和SSE纯读`。不执行 `git add .`，不推送/发布。

## P06：薄前端消费快照，替换旧终态扫描

**依赖：** P05快照fixture/schema
**文件（相对Platform仓库根）：** 修改 webui/src/conversationApi.ts、conversationTypes.ts、pages/ConversationPage.tsx、pages/ConversationPage.test.tsx、conversationApi.test.ts；新增 webui/src/conversation/useTurnSnapshot.ts、useTurnSnapshot.test.tsx。
**接口：** fetchTurnSnapshot(conversationId,turnId|'current',signal)->Promise<TurnSnapshot>；fetchMessagePage(conversationId,{beforeSeq,limit},signal)->Promise<MessagePage>；useTurnSnapshot负责单飞读取、版本丢弃与取消，不调用POST。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~tsx
it("finishes the latest turn beyond the first 100 messages", async () => {
  const view = renderConversationWithServerFixture("history-120-terminal");
  await screen.findByText("第120条：完整回答");
  expect(view.onSettled).toHaveBeenCalledTimes(1);
  expect(view.network.postedMessages()).toHaveLength(0);
  await view.advancePolling(15000);
  expect(view.onSettled).toHaveBeenCalledTimes(1);
});
~~~
- [ ] 运行并记录失败：在webui目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
npm test -- --run src/pages/ConversationPage.test.tsx src/conversationApi.test.ts src/conversation/useTurnSnapshot.test.tsx
~~~
- [ ] 逐个补齐的失败场景：完整mock仅替换HTTP边界且经schema解析；latestpage与snapshot重叠message只渲染一次；120条终态停止；空会话不重连循环；失败无answer停止；附件pending不锁发送；旧version响应不得覆盖新结果；切换岗位/会话取消旧请求，不清空另一个会话草稿；Enter与附件下置样式不退化。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~tsx
if (incoming.read_version < latestVersion.current) return;
latestVersion.current = incoming.read_version;
setSnapshot(incoming);
if (incoming.outcome?.terminal && !settledTurnIds.has(incoming.turn.turn_id)) {
  settledTurnIds.add(incoming.turn.turn_id);
  onSettled(incoming.turn.turn_id);
}
~~~
renderConversationWithServerFixture新增于测试文件内，只配置真实ConversationPage的完整响应与可控网络，不mock被测试组件；advancePolling用fake timers异步推进。保留idle turn=null分支。成果/Delivery独立慢刷新，不以它们尚未完成阻止新消息。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：目标集→全webui tests→tsc/Vite build；现有warning如实记录。不能仅单文件绿色就合并。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p06 薄前端消费快照，替换旧终态扫描`。不执行 `git add .`，不推送/发布。

## P07：可信渠道身份、绑定与入站Turn

**依赖：** P01/P02；M01为真实上行集成依赖，可先按P01契约实现；未绑定仅引导已确认
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/channel_intake.py、channel_identity.py、backend/app/execution_relay/channel_bridge.py；修改 backend/app/execution_relay/routes.py、worker.py及附件上传适配；新增 backend/tests/test_feishu_turn_intake.py、test_feishu_identity_binding.py、test_channel_attachment_ingress.py、test_channel_bridge_auth.py、test_execution_worker_operation_limits.py；沿用provider_identities和现有身份加密。
**接口：** resolve_channel(worker_context,ChannelIdentity)->BoundChannel|BindingRequired；accept_intake(worker_context,IntakeV1)->Accepted|Deferred|Conflict；BindingService.issue(owner_id)->one_time_code、consume(channel_identity,code)->Binding。IntakeV1从P01共享schema新增分支，包含完整身份、消息原ID、正文与有序已归档附件，不接受owner覆盖。

ChannelBridge.handle(request)->HTTPResponse：六条固定POST路径，HR文件凭据鉴权在任何云端操作之前。OperationQuota.check(worker_id,operation_class)->retry_after_seconds|None：服务端路由选择execution/channel_ingress/channel_delivery/attachment_ingress，默认120/60/120/120次每60秒，沿用process-local语义。Deferred含blocking_turn_id和retry_after_seconds=5；幂等已接受查询先于active冲突判断。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_same_message_retry_returns_same_turn(intake, bound_message):
    first = intake.accept(bound_message)
    second = intake.accept(bound_message)
    assert second.turn_id == first.turn_id
    assert second.replayed is True
    assert intake.count_model_attempts(first.turn_id) == 1

def test_unbound_sender_cannot_create_turn(intake, unbound_message):
    result = intake.accept(unbound_message)
    assert result.kind == "binding_required"
    assert result.turn_id is None

def test_run_callback_token_is_not_an_ingress_credential(bridge_client, run_callback_token, signed_cloud):
    response = bridge_client.resolve_channel(bearer=run_callback_token)
    assert response.status_code == 401
    assert signed_cloud.requests == []

def test_attachment_quota_does_not_consume_execution_capacity(operation_quota):
    for _ in range(120):
        assert operation_quota.check("worker-test", "attachment_ingress") is None
    assert operation_quota.check("worker-test", "attachment_ingress") is not None
    assert operation_quota.check("worker-test", "execution") is None
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_feishu_turn_intake.py tests/test_feishu_identity_binding.py tests/test_channel_attachment_ingress.py tests/test_channel_bridge_auth.py tests/test_execution_worker_operation_limits.py tests/test_execution_worker_auth.py tests/test_execution_relay_api.py
~~~
- [ ] 逐个补齐的失败场景：关闭Flywheel照常有主体；同名不同union_id不合并；tenant/app隔离；伪造body owner拒绝；绑定码过期/重用/暴力猜测限流；同键异正文409；并发单聊空thread唯一；已有active Turn返回deferred不丢消息；相同文件重传不重复附件、原序不变；下载失败不静默少图执行。
- [ ] F01逐项RED→GREEN：缺/错Bearer、其他Bot凭据、run token、非loopback peer、错误method/path、任意URL/owner/文件路径、超1MiB body、符号链接/错误owner/非0600文件/非0700父目录都拒绝；错误不回显凭据。正确HR凭据才能进入签名云端代理；云端仍拒绝错tenant/app和未绑定主体；凭据轮换后重传沿用原业务键。
- [ ] F02逐项RED→GREEN：耗尽入站及附件桶后，实际签名HTTP heartbeat/events/terminal和delivery receipt仍可接受；伪造操作类别header无效；429携带Retry-After；没有新桶前的共享120扣减。以受控时钟测60秒窗口，保持既有其他Bot/v4限流回归；本地独立连接槽用挂起上传故障验证，而非只测计数器。
- [ ] F03逐项RED→GREEN：平台先查已接受幂等键，ACK丢失重试返回同Turn而非deferred；真正deferred不插入Turn/Attempt，返回明确阻塞引用；完成、失败、取消后的再次提交只接受一次，旧执行仍reconciling则不放行。排队续送和提示由M01的真实本地DB测试覆盖。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~python
client_request_id = feishu_request_id(identity)
# owner只来自数据库已验证映射，不来自Intake body
# resolve会话 → 校验已上传附件owner/conversation/hash
# 短事务接受Turn；唯一键冲突比较canonical业务hash，
# 相同返回原turn_id，不同返回409。繁忙返回deferred由inbox保留。
~~~
签名复用现有device auth、nonce与时间窗口，新增operation范围只允许HR。上传权限由resolve结果签发，不能让Worker任意代传给其他owner。新接口不是公网Bot直连通道。

bridge_client启动真实listener并用临时0700目录/0600合成凭据发HTTP；signed_cloud仅替换云端网络接收，记录是否被调用。operation_quota是实际限流器配受控时钟，不是实现正确答案的mock。新channel_bridge模块复用现有安全文件读取纪律但使用独立HR凭据，禁止给callbacks路由放宽鉴权。网络调度执行保留至少2连接槽、渠道最多2、附件最多1；429持久next_attempt_at，重签nonce但不换幂等键。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：真实DB并发与现有签名鉴权回归；不创建匿名内部用户，不扩充游客工具权限。测试查询计数从DB读取，不给生产service加count_model_attempts；上述计数放测试adapter。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p07 可信渠道身份、绑定与入站Turn`。不执行 `git add .`，不推送/发布。

## P08：渠道Delivery领取、回执与迟到收尾

**依赖：** P04/P07；M06消费同一P01冻结回执契约，集成时再合验
**文件（相对Platform仓库根）：** 新增 backend/app/agent_brain/channel_delivery.py；修改 execution_relay/routes.py、worker.py、worker_store.py；新增 backend/tests/test_channel_delivery_database.py、test_channel_delivery_transport.py。
**接口：** ChannelDeliveryService.claim(executor_id)->DeliveryLease|None；record_receipt(lease,DeliveryReceipt)->DeliveryState；DeliveryReceipt含operation_id、status、message_id可空、content_hash、route_epoch。仅持久回执推进Delivered；租约与Attempt分离。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_lost_channel_receipt_does_not_restart_model(deliveries, completed_turn, unknown_receipt):
    before = completed_turn.attempt_ids()
    deliveries.record_receipt(completed_turn.delivery_lease, unknown_receipt)
    assert completed_turn.reload().delivery.status == "receipt_unknown"
    assert completed_turn.attempt_ids() == before
    assert completed_turn.reload().answer == completed_turn.answer
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_channel_delivery_database.py tests/test_channel_delivery_transport.py tests/test_execution_worker_store.py
~~~
- [ ] 逐个补齐的失败场景：租约重领只重投同operation_id；失去执行租约仍能补渠道回执；回滚后旧epoch已接受Delivery能完成但不能新开Attempt；相同message_id重复receipt幂等；不同content_hash不能覆盖；多part只重投未确认部分且不乱序；失败Turn有system通知；网页available不伪造送达。
- [ ] 配额与回执RED→GREEN：耗尽channel_ingress/attachment_ingress桶并挂起一个上传时，channel_delivery桶仍可接受同operation回执且execution心跳/终态可上传；delivery桶自身429后持久保留receipt并遵守Retry-After，恢复只补账不新建卡片/Attempt。回执入口沿用P07 HR鉴权，但还须校验已存在的operation/内容hash/接收范围，不能以机器凭据冒领任意Delivery。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~sql
UPDATE platform_control.turn_deliveries
SET status='delivered', receipt_ref=%s
WHERE delivery_id=%s AND lease_epoch=%s AND content_hash=%s
  AND status IN ('pending','sending','receipt_unknown')
RETURNING delivery_id;
~~~
更新delivery与snapshot_version/事件同事务；对迟到已确认回执走只核对operation身份的幂等路径，不让过期sender任意改内容。pending意图与Result同事务创建，避免执行成功却无投递记录。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：模拟已发送未回执后杀云端/本地Worker并分别恢复，只补账，不新增模型调用。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p08 渠道Delivery领取、回执与迟到收尾`。不执行 `git add .`，不推送/发布。

## P09：轻量情报组合降级与真实上下文回归

**依赖：** 独立；不依赖新runtime
**文件（相对Platform仓库根）：** 修改 backend/app/hr/panorama_context.py、panorama_repository.py；扩展 backend/tests/test_hr_panorama_context.py、test_hr_panorama_repository_projection.py、test_hr_task_context.py、test_agent_brain_conversation_context.py。
**接口：** 现有current_context_bundle保持轻量；新增必要的结构化有界读取按bundle_id固定，不读新current。复用既有ContextBuilder与冻结manifest，不改变freeform/position分路。

- [ ] RED：先为下列首个行为写目标测试；fixture用真实实现创建记录。每个剩余场景再单独循环。
~~~python
def test_lightweight_row_can_fallback_at_the_pinned_bundle(source, markdown_store, context_provider):
    source.current_row.pop("analysis", None)
    markdown_store.corrupt_selected_chunk()
    expected_bundle = source.current_row["bundle_id"]
    result = context_provider.for_current_test_turn()
    assert result.bundle_id == expected_bundle
    assert result.is_degraded is True
    assert result.excerpts
    assert source.producer_calls == []
~~~
- [ ] 运行并记录失败：在backend目录执行下列命令。首次缺接口可补无行为接口，但必须得到业务断言失败，不能把ImportError当回归复现。
~~~sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_hr_panorama_context.py tests/test_hr_panorama_repository_projection.py tests/test_hr_task_context.py tests/test_agent_brain_conversation_context.py
~~~
- [ ] 逐个补齐的失败场景：在现有MarkdownStore/BundleSource fixture扩展组合故障，不能继续用完整fixture冒充轻量行；当前bundle并发切换后仍读旧pin；结构化数据亦缺失时明确unknown主对话不失败；发送给MetaBot的冻结prompt实际含官网职责/要求、仅选中材料、候选人范围；freeform不强迫创建Position。
- [ ] 最小实现只覆盖上述行为，关键事务/算法边界如下：
~~~python
# 仅在Markdown不可用时，按固定bundle_id取有界结构化片段。
# 不重新调用current_bundle，不将完整analysis加入列表查询。
# 没有可核验证据则返回明确degraded/unavailable上下文，
# 由正常主对话继续响应，而不是触发研究任务。
~~~
示例fixture行为在现有测试类上实现，不向产品service加测试专用函数。将producer_calls断言放HTTP/adapter边界，数据库与检索逻辑不mock。
- [ ] GREEN：重跑同一命令，目标断言与列出的既有回归全部通过；发现其他失败不得删测试掩盖。
- [ ] 验收与审查：至少先复现analysis缺列+Markdown失败同现，而非各自单独测试；任务可独立交付，不借机重跑情报采集。
- [ ] 仅提交本任务实际修改的精确文件；提交消息：`feat(hr-runtime): p09 轻量情报组合降级与真实上下文回归`。不执行 `git add .`，不推送/发布。

## 最后一次平台回归

在backend运行本计划新增测试以及所有 tests/test_agent_brain_*.py、tests/test_hr_*.py、tests/test_attachment_*.py、tests/test_conversation_attachment_*.py、tests/test_execution_*.py 和 tests/test_control_plane_migration.py。这是只读源码匹配选择测试文件，不是清理glob。

在webui运行：
~~~sh
npm test -- --run --maxWorkers=1
npm run build
~~~

P03/P04/P05的进程故障证据见O03；代码与模拟测试通过不允许直接跳过受控验收上线。
