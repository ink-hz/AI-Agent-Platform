# HR Minimum Reliable Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 HR 工作台跑通“对话 → JD/JR → 确认岗位 → 简历分析 → 面试题 PDF 下载”，并证明刷新、重复提交及进程中断不会丢结果或重复执行。

**Architecture:** 保留已完成的 Turn/Attempt、冻结命令、签名 Relay、MetaBot v5 与附件域。先连接网页执行、原结果恢复和统一读取，再连接现有招聘成果路径；不实施飞书统一迁移或自动重跑许可。

**Tech Stack:** Python/pytest/psycopg/PostgreSQL；MetaBot TypeScript/Vitest；现有 React/TypeScript 工作台。

## Global Constraints

- 普通咨询无需岗位。已发布招聘情报按需读取，不在生产采集/分析。
- 未知副作用、旧执行未证明停止、回执未知均不能触发新模型执行；重传、重跑、成果恢复、发送重试分别记账。
- 不引入 Kafka、向量库、访客执行、ClamAV 或新的招聘审批；模型配置保持现状。
- 本次只接 HR 网页 direct；飞书、Brain、其他 Bot、群聊原路径和共享 Nginx 不变。
- 业务材料保留一年；未决 inbox/outbox 不按固定天数删；日志和测试证据不得含简历、prompt、token。
- 原始两份 dirty 文档、所有 .venv、已有 dist 和其他会话文件保留，不加入提交。
- 不以本地测试数量或服务 healthy 宣称业务可用。真实模型/生产/业务发送不在本地开发授权内。
- 不推送、合并或部署。新增 SQL 只用 pending 草稿和一次性测试数据库，生产编号以授权取得的目标清单为准。

## 当前基线与任务纪律

2026-09-08 用户再次明确测试策略：**接口优先，页面最后验收**，持续遵守项目 `AGENTS.md`。核心业务直接通过真实 HTTP/API、数据库结果与必要的进程故障注入验证；仅在接口链路通过后做一次必要的页面交互验收，不通过反复操作页面调试后端。

Platform 工作树 `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-position-core-availability`；MetaBot 工作树 `/Users/neo/Developer/work/metabot-dev/.worktrees/hr-unified-execution`。已有就绪候选 bf1e29b / bda5aa1 正在独立复审，先收口其实际问题；不重做已关闭的 P01/P02/M02/M03/M04a/P03a/b1/b2a。

旧 18 项计划的精确合同与测试仍可引用，但以下三个业务任务是当前执行队列。内部函数/表/配置步骤不再各自成为一轮设计审批；同一个业务任务内先 RED 再实现，最终做一次完整任务范围独立复审。若需超出本文件事实权威或生产权限才升级决策。

后端命令在 backend 目录执行，解释器固定 `/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python`；MetaBot 使用根仓库现有 node_modules；前端在 webui 运行 `npm test -- ...` 和 `npm run build`，不安装依赖。

### Task 1: 一轮网页对话可靠完成与恢复

**Files:**
- Create: `backend/app/agent_brain/direct_worker.py`, `direct_mission_adapter.py`, `turn_result_projection.py`, `turn_snapshot.py`。
- Create: `backend/app/execution_relay/recovery_v5.py` 和 `backend/control_migrations/pending/hr_web_result_recovery.sql`（必要的原执行观察与 Result 关联元数据，不建另一套执行队列）。
- Modify: `backend/app/agent_brain/conversation_context.py`, `conversation_routes.py`, `conversation_service.py`, `direct_command_binding.py`, `turn_attempts.py`; `backend/app/execution_relay/routes_v5.py`, `worker.py`, `metabot_client.py`; `backend/app/main.py`。
- MetaBot Modify: `src/api/routes/core-chat-v5-service.ts`, `core-chat-v5-runtime.ts`, `core-chat-v5-stream-owner.ts`, `core-chat-v5-store.ts`, `core-chat-v5-routes.ts`, `core-chat-routes.ts`; existing `src/bridge/execution-recovery-ledger.ts`。
- Web Modify: `webui/src/conversationApi.ts`, `webui/src/pages/ConversationPage.tsx`, `webui/src/components/conversation/PublicProgress.tsx`。
- Test/Create: `backend/tests/test_hr_web_reliable_loop.py`, `test_hr_direct_worker_process.py`, `test_turn_result_projection.py`, `test_turn_snapshot.py`, `test_execution_recovery_v5.py`; `metabot-dev/tests/core-chat-v5-recovery.test.ts`。
- Test/Modify: `backend/tests/test_hr_direct_worker.py`, `test_agent_brain_conversation_context.py`, `test_agent_brain_conversation_api.py`; `webui/src/conversationApi.test.ts`, `webui/src/pages/ConversationPage.test.tsx`。

**Interfaces:**
- Consume existing `TurnAttemptRepository.claim_due/renew/request_cancel/record_terminal`, `DirectCommandBindingRepository.get_prepared/prepare/authorize_transport`, actual signed handoff/source upload and `CoreChatEventV5` parser.
- Produce `DirectWorker.tick()->int` (bounded scheduling, not awaiting a model); `DirectMissionAdapter.prepare(lease)` and `reconcile(lease)`; `TurnResultProjector.commit(lease,event)->UUID|None`; `TurnSnapshotReader.get(owner_id,conversation_id,turn_id=None)->dict` conforming to existing P01 snapshot schema.
- `TurnResultProjector.commit_locked(connection,lease,event)->UUID|None` is the same publication implementation inside a caller-owned transaction; it verifies the original parsed event against the persisted authenticated source row, not a public caller assertion. `commit` owns one transaction and calls it. Source/event linkage and snapshot manifest references resolve real persisted rows; an intake manifest before command preparation must not claim a finished context hash.
- `ConversationContextBuilder.build_direct(conversation_id,turn_id)` returns existing ConversationContext with persisted summary and bounded real history; required current input/material references are never silently dropped. Read providers outside locks, revalidate their source versions at prepare. No summary/planner model.
- One real independent worker has its own process UUID and separate bounded renewal/recovery/dispatch work. Reuse existing signed HTTP, no cloud→MetaBot shortcut. API/SSE/snapshot only read/project via the independent worker, not API leader.
- Narrow bound-run inspect/stop travels through existing signed Worker and machine Bearer. Derive work from actual reconciling Attempt/pinned binding; report original launch identity independently of current writer. Reuse existing local native evidence. No positive replay permit, speculative snapshot/ACK tables or model POST for recovery. Missing local run after uncertain send retains its hold.
- Result commit uses one C→T→A→binding/job transaction with final DB-time fencing. Text, Turn, compatibility status, visible event and snapshot_version commit together. Web creates zero push Delivery. Result may be available while Attempt remains reconciling; later stopped proof releases without rewriting the original result.
- Compatibility task identity is derived from frozen command.run_id; do not call legacy create_run with a fresh random task_id. Existing HR tables remain consumers, not new execution authorities.

- [ ] RED: use actual `worker_conversation`, `repository`, `attempt_repository`, `direct_database` fixtures from `test_hr_direct_worker.py`; seed no fake completed state. A new integration fixture starts the real HTTP/PG/Worker/MetaBot composition, substitutes only the native provider and exposes transport counts/query access. First assertion must fail because a submitted worker Turn has no result; missing imports do not count.

```python
def test_same_request_and_refresh_recover_one_answer(web_loop):
    first = web_loop.submit("介绍一下你自己", request_id="one-request")
    again = web_loop.submit("介绍一下你自己", request_id="one-request")
    assert again.turn_id == first.turn_id
    web_loop.drive_until_available(first.turn_id)
    before = web_loop.snapshot(first.turn_id)
    assert before["answer"] is not None
    assert before["answer"]["content"].strip()
    web_loop.restart_api_process()
    after = web_loop.snapshot(first.turn_id)
    assert after["answer"] == before["answer"]
    assert web_loop.native_starts(first.turn_id) == 1
    assert web_loop.assistant_count(first.turn_id) == 1
```

`web_loop` in `tests/helpers/hr_web_loop.py` owns real temporary PG roles, sockets and subprocesses; `submit` calls public HTTP using a deterministic UUID idempotency mapping, `drive_until_available` polls real Worker/API with a bounded test deadline, `snapshot` calls the public snapshot endpoint, counts use read-only SQL/owned provider boundary. It cannot implement an in-memory state machine or simulate restart by replacing a service object.

- [ ] Implement sequentially within this task: actual encrypted source→Result transaction and source identity checks; same-run inspect/stop for lost ACK and late exit; actual context/worker/task binding; owner-authorized repeatable-read snapshot and read-only SSE; frontend snapshot consumption. Test each behavior before code, rather than implement all then add tests.

```python
# Result transaction shape; every operation uses this same connection.
with attempts.transaction() as connection:
    lease = attempts.renew(lease, lease_seconds=60, connection=connection)
    message_id = projector.commit_locked(connection, lease, event)
    # commit_locked persists the real parsed original event, not a synthetic result;
    # it increments snapshot_version and only closes Attempt with stop evidence.
```

- [ ] Focused commands: `python -m pytest -q tests/test_turn_result_projection.py tests/test_turn_snapshot.py tests/test_execution_recovery_v5.py tests/test_hr_web_reliable_loop.py tests/test_hr_direct_worker_process.py` using the fixed interpreter. Add the existing context/summary/worker/transport/API covering suites for the final candidate. MetaBot: existing v5 HTTP/store/event/drain/lifecycle tests plus new recovery test. Web: `npm test -- src/conversationApi.test.ts src/pages/ConversationPage.test.tsx src/components/conversation/PublicProgress.test.tsx`.
- [ ] Required cases: 32KiB Chinese answer; 120-message history; empty freeform conversation; safe real progress without raw thinking; queued cancel without capability; accepted response lost before receiver registration then cancel; Worker/API killed independently; result persisted before stop; same-conversation hold; no older writer changes; read endpoints leave DB state unchanged; unknown execution shows explicit reconciliation state and cannot silently retry.
- [ ] GREEN/self-review: real HTTP→Worker→MetaBot→source→assistant→snapshot proof, no fake planner/Result/ready-only completion. Exact file staging and commit `feat(hr-web): complete reliable conversation result loop`; review full task range once, fix concrete blocking findings, record the user-visible checkpoint.

### Task 2: 接上岗位、简历和可下载成果

**Files:**
- Modify: `backend/app/hr/task_context.py`, `position_package_projection.py`, `panorama_context.py`; `backend/app/attachments/artifact_service.py`, `result_projection.py`; Task1 `direct_mission_adapter.py`, `turn_result_projection.py`, `conversation_context.py`。
- Create: `backend/app/agent_brain/artifact_recovery.py`, `backend/tests/test_result_artifact_recovery.py`。
- MetaBot Modify: existing v5 runtime/stream owner and actual attachment download/output adapters; exact adapters are resolved from existing v4 artifact implementation, not a second file-transfer service.
- Web Modify only necessary consumers: `webui/src/workspaces/hr/HrPositionWorkspace.tsx`, `HrPositionDetailsDrawer.tsx`, `HrPositionProposalCard.tsx`, `HrCandidateWorkspace.tsx`; existing attachment/material/artifact components.
- Test: `backend/tests/test_hr_p0_recruiting_loop.py`, `test_hr_task_context.py`, `test_hr_task_context_recovery.py`, `test_hr_position_package_database.py`, `test_hr_candidate_analysis_artifact_migration.py`, `test_attachment_artifacts.py`, `test_hr_panorama_context.py`; Web existing `HrRecruitingLoop.acceptance.test.tsx`, `HrP0Combined.acceptance.test.tsx`, `HrOfficialPositionPanel.test.tsx`, `HrPositionWorkspace.test.tsx`。

**Interfaces:** Task1 web_loop adds existing public HR/attachment HTTP methods `confirm_position`, `upload_resume`, `read_position`, `download_artifact`; no new product API where existing endpoints suffice. `ArtifactRecovery.on_ready(attachment_id)->int` and `retry_due(limit)->int` consume fixed Result intents through the existing attachment authority, separately from the execution Attempt. Input grants preserve archive ID/SHA and actual known order; historical UUID-sorted selection is not relabeled original order.

- [ ] RED: extend the Task1 real harness and existing synthetic HR P0 fixtures. Assert generated JD/JR remain drafts until explicit confirmation; confirm creates one Position and repeated confirmation does not duplicate it. Next real model-boundary captured input must include that Position's exact JD/JR and selected resume, not another Position or inactive material.

```python
def test_interview_pdf_failure_does_not_lose_answer(recruiting_loop):
    turn = recruiting_loop.request_interview_from_confirmed_position()
    recruiting_loop.fail_next_file_transfer()
    recruiting_loop.drive_until_text_available(turn)
    original = recruiting_loop.snapshot(turn)["answer"]
    assert original is not None and original["content"].strip()
    recruiting_loop.restore_file_transfer()
    pdf = recruiting_loop.download_when_ready(turn)
    assert pdf.startswith(b"%PDF-")
    assert recruiting_loop.snapshot(turn)["answer"] == original
    assert recruiting_loop.native_starts(turn) == 1
```

`recruiting_loop` extends the same real web_loop with public existing HR/attachment calls and socket faults; no in-memory artifact readiness. Actual artifact bytes are produced by an owned local synthetic provider/test PDF fixture, then traverse production upload/validation/binding/download code. Record model substitution separately from business quality verification.

- [ ] Implement necessary Task1 grant/context extension, frozen artifact intents with task_id=command.run_id, actual upload independent of text, late-ready binding/retry without renewed model execution. Preserve existing v64/v71/v76/v77 owner/source/version checks and one-year retention; do not use expired upload authority to introduce a new unlisted file.
- [ ] Fix only the pinned lightweight intelligence fallback needed by this path: bounded published Markdown/structured fragments and explicit unknown; never fetch full analysis jobs into context or start online research.
- [ ] GREEN: `python -m pytest -q tests/test_hr_p0_recruiting_loop.py tests/test_hr_task_context.py tests/test_hr_task_context_recovery.py tests/test_hr_position_package_database.py tests/test_hr_candidate_analysis_artifact_migration.py tests/test_attachment_artifacts.py tests/test_result_artifact_recovery.py tests/test_hr_panorama_context.py`; Web `npm test -- src/workspaces/hr/HrRecruitingLoop.acceptance.test.tsx src/workspaces/hr/HrP0Combined.acceptance.test.tsx src/workspaces/hr/HrOfficialPositionPanel.test.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx`.
- [ ] Verify exact download checksum, input ownership, actual selected data in prompt, no invented source JR, unchanged conversations when switching Position tabs, retained original answer after file recovery. Commit exact owned files `feat(hr-web): connect recruiting context and downloadable outcomes`; full task-range independent review.

### Task 3: 完整网页验收与可发布交接

**Files:** Task1/2 actual integration tests; existing `backend/app/hr/p0_acceptance_cli.py`, `backend/tests/test_hr_p0_acceptance_cli.py`, `test_hr_p0_cloud_acceptance.py`; scoped existing deployment/Team HR configuration scripts only where needed for the actual new process entrypoint. Add `docs/reviews/2026-09-08-hr-minimum-reliable-web.md` with exact commands and evidence.

**Interfaces:** use only the already implemented public conversation/HR/attachment endpoints and real explicit runtime composition. The existing acceptance CLI runs against owned local services; no production URL, user's logged-in credentials or business recipient is assumed.

- [ ] RED where existing acceptance misses the new path: fail if any turn is legacy-owned, a native start count exceeds1 after same submission/restart, selected material is absent from actual input, answer missing after API restart, downloaded PDF checksum differs, or read-only endpoints write task state.
- [ ] Run the complete task1/task2 affected backend/MetaBot/Web suites once against a fixed candidate, then `npm run build` and a real local browser walkthrough. Browser scope is local owned test accounts/data. Native build/import check must use owned temporary output, not overwrite existing dist.
- [ ] Real process matrix: API down while Relay retains result; cloud Worker dies after offer; MetaBot parent dies with owned child alive; upload response lost after commit; user repeats send or refreshes while running; 120-message history. Expected outcome is original result recovered or explicit uncertainty held, never automatic replay.
- [ ] Verify real runtime option wiring and dry-run HR-only release boundaries, prepared migration list, signed polling budget, and rollback no-double-execution. Full Feishu quota/inbox/delivery migration is deferred; required web transport must not starve its own heartbeat/result/recovery under existing quota. Keep fixes scoped to this actual path.
- [ ] Run authorized controlled real-model/production acceptance only after the separately required authority is available. Until then, report local engineering acceptance and this exact remaining boundary; do not mark production/business quality accepted.
- [ ] Whole-branch review of both repository ranges. Preserve dirty user files; no push/merge/deploy. Handoff shows completed business checks, exact current/rollback intent, outstanding authorization boundary and no changes to other apps. Production deployment, when authorized, must follow the user's full disk/staging/release report discipline.

## Coverage/self-review

Task1 covers request/exec/result/read/failure reliability; Task2 covers Position/selected resume/PDF and read-only intelligence; Task3 covers process/browser/runtime and release evidence. Existing v0.3 numerical wire limits and per-object ownership are unchanged. No Feishu Delivery for web, no automatic replay, no P03-ready-only completion, no full redesign of existing HR UI. Owner's approval and no-per-item-confirmation instruction replace a redundant document/execution-choice approval round.
