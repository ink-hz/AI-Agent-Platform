# Handoff、分类并发、ready 草稿与排空计数独立审阅

日期：2026-09-11

基线：`b974a87`

审阅对象：当前 index 中的 3 个运行文件、迁移 103 和 2 个 `test_hr_cutover_*review.py`。本次只读运行代码与测试，只新增本报告。

## 结论

未发现阻断发布整合的正确性缺陷。所审变更在共享 cutover advisory lock、业务行锁和实际写入之间保持了正确顺序；两份 review 测试在本地一次性 PostgreSQL 上独立执行为 **25 passed in 27.43s**。

这项结论限于本地工程行为，不能替代生产镜像、真实旧 Worker 停止、正式窗口排空或真实模型验收。

## 逐项判断

### 实际 `/v5/handoff`

- `DirectCommandBindingRepository.handoff()` 的候选发现不授予 offer 权限。每个候选进入独立事务后，先执行 `lock_admission(connection, "legacy", continuing=True)`，再进入 `_authorized()` 的租约、worker、binding 与 job 行锁，最后才 `mark_offered()`。因此 transition 与 offer 的先后关系由同一个 advisory lock 串行化。
- `legacy`、`draining_legacy` 可继续已经持久化并已授权的旧 lane 工作；`cloud`、`draining_cloud` 抛出 `CutoverRejected`，事务回滚并由 handoff 返回 `None`，HTTP 路由表现为 204，`offered_at` 不变。
- signed handoff 用例走 loopback socket、真实 Uvicorn app、Ed25519 签名校验、nonce、加密 binding、worker 授权与 PostgreSQL 事务。`draining_legacy` 用例继续走 acceptance 和 source-event callback；反向并发用例分别证明 handoff 等待已持有的 transition 锁，以及 transition 等待正在授权的 handoff 事务。
- acceptance/callback 没有新增 lane gate，仍由原有 worker 身份、租约 epoch 和 binding/run 来源约束保护。这个选择与“已接受工作允许完成回执”一致。

### 会话分类并发

- `append_turn()` 和 `resume_search_turn()` 都先取得 shared cutover lock，再锁 conversation，随后重新查询 `position_conversations`。这消除了“先分类为非 HR，等待 conversation 锁期间又提交 HR binding，之后仍放行”的陈旧分类窗口。
- lane 校验同时覆盖 `direct_agent_id='hr-bot'` 与真实 position binding。其他 Bot 仍参加统一锁顺序，但不执行 `require_lane()`；cloud 下 other-Bot append 的数据库用例通过。
- 动态竞态用例覆盖 `append_turn()`：测试在 conversation `FOR UPDATE` 前暂停 append，提交真实 position 与 `position_conversations` binding，恢复后确认新 turn 被拒绝且事务没有新增 turn。`resume_search_turn()` 使用同一锁序和重查实现，但本次没有独立的同型动态竞态用例；这是测试证据边界，不是已观察到的运行缺陷。

### ready 草稿

- 103 把 `ready` 视作执行排空终态，不代表已确认候选人。运行层仍在 confirm/dismiss 事务的任何业务写入之前取得 legacy continuation gate。
- `draining_legacy` 中 confirm/dismiss 可以完成；cloud 激活后两者映射为受控 `CandidateUnavailable`，草稿保持 `ready`，且 confirm 不会产生 candidate。并发用例还证明 cloud transition 已持有 exclusive gate 时草稿事务会等待，transition 提交后再拒绝写入。
- retry 保持新 admission 语义，没有使用 `continuing=True`。confirm/dismiss 的重放也会先过 gate，因此 cloud 后不会借幂等 replay 再写旧业务表。

### 103 排空计数

- 103 只 `CREATE OR REPLACE` 102 的 count 函数，未重写 102 ledger 或历史 job。`CREATE OR REPLACE` 保留函数 owner/ACL；测试证明 app role 无执行权，maintenance role 可以计数和 transition。
- `worker_direct_v5` queued 信封只有在以下关系同时成立时才从占用中排除：job 对应唯一 binding、binding 对应 Attempt、`Attempt.transport_run_id = job.run_id`、binding conversation 与 Turn conversation 一致、Attempt/Turn 的 executor owner 均为 worker-direct，且 Attempt 与 Turn 都是终态。测试通过 3 条真实 signed handoff → acceptance → signed result → fenced `TurnResultProjector.commit()` 链路证明 job 仍为 queued 时 count 为 0，真实 transition 可以进入 cloud。
- orphan v5、非终态 Attempt、非终态 Turn、未确认 stop，以及 HR job 关联的其他 Bot 活跃 Turn/Attempt 都保持非零并阻断 transition。非 v5 的 historical interrupted 仅在已有 `terminal_at`、无未确认 stop、无关联活跃工作时按排空终态处理；这没有把它们声明为业务成功。
- candidate batch 以附件基数和已创建 draft 数量不相等作为未完成占用；schema 的 `(owner, batch_request_id, attachment_id)` unique 约束防止同附件重复 draft 伪装为完整批次。
- `legacy_nonterminal` 是不同占用类型相加的 gate 指标，同一业务链可能同时贡献 conversation 与 job 计数。测试和 transition 契约证明的是准确的零/非零排空边界，不应把数值解释为去重后的“任务条数”。

## 非阻断证据边界

1. callback-after-cloud 和 wrong-lane residual handoff 用例通过直接修改 phase 构造故障状态；它们证明残留旧 Worker 的运行时行为，不证明合法 transition 能跨过非终态工作。合法 transition 的拒绝由另外的 count 用例覆盖。
2. v5 成功链使用真实签名 HTTP 与 fenced projector，但 result 内容和 `executorStopProofRef='fixture:durable-stop'` 是合成契约夹具；没有启动原生执行器、模型或真实停止流程。
3. ready 草稿走真实 repository 和 security-definer SQL 函数，没有另测用户侧 candidate HTTP 身份与路由。
4. historical interrupted、orphan 和反常 lineage 是管理员 SQL 故障/历史夹具，只用于排空判定；不能作为历史任务成功或远端进程已停止的证据。
5. 两份 review 测试依赖本分支中另外已暂存的 PostgreSQL helper 与既有测试 fixture 修订，因此 25 项通过证明当前整合 index，不证明把这 6 个文件单独应用到基线也能建立相同 fixture。
6. 没有浏览器、生产数据库、生产容器或实际 `/v5/handoff` 镜像验收；正式切换仍需部署后准确清单、103 实际计数和旧 HR Worker 停止证据。

## 独立验证

执行命令：

```sh
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_hr_cutover_dispatch_review.py \
  backend/tests/test_hr_cutover_count_review.py -q --tb=short
```

结果：`25 passed in 27.43s`。`--collect-only` 同样收集 25 项，其中 dispatch 14 项、count 11 项。目标文件的 staged diff 通过 `git diff --cached --check`。

审阅时 index 内容 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `conversation_repository.py` | `eadfbee00ecc5224c5b35a63d905ff2df589486925c54947fbd528bc5c0bc395` |
| `direct_command_binding.py` | `e3e130268f4fd3fb3ee13e6c9d04b513fc64486686cae930b155f7d78bd89aa0` |
| `candidate_repository.py` | `e4adc3a20abd2670dcee6548f38e8ae14883a5f60fbadb84efa58c36589576b8` |
| `103_hr_execution_drain_occupancy.sql` | `1795af66ae8ae5034bc0cb7385cd51ac3485601611a4be7258517bd40aadd6d0` |
| `test_hr_cutover_count_review.py` | `538fb40e6c748aa4e19723ee1b3437370beb864af2b8cee7d159143814074883` |
| `test_hr_cutover_dispatch_review.py` | `c1eb6c470640846197f58adef7e52bd64ffcab94971a39f5186e75723a29a37a` |
