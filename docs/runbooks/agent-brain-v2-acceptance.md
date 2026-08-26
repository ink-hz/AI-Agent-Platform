# Agent Brain V2 验收与发布

本文是 Agent Brain V2 的可执行发布合同。它覆盖本地确定性测试、云端真实
Provider 探测、恢复演练、独立答案评审、FAE 不干扰检查和生产切换。任何一步
失败都不得手工补写通过标记，也不得临时切换模型、Adapter 或旧 Mission 链路。

## 发布边界

- Conversation 的事实源是 Platform PostgreSQL。
- Brain Loop 运行在无公网端口的 `platform-brain` 容器。
- MetaBot 只是 `metabot_local` 专业执行 Adapter；Mac 离线不能影响 Brain
  直答、历史和其他 Adapter。
- V2 不写 `platform_control.missions` 或 `mission_runs`。
- FAE 不在本次接入范围内，不修改、不重启、不重新挂载。
- 原始 Provider 响应、Prompt、用户内容、附件内容、思维链和 Adapter payload
  不进入验收证据。

## 20 个确定性场景

`backend/app/agent_brain/acceptance_contract.py` 是场景清单的唯一机器可读来源：

1. `direct_answer`
2. `one_agent`
3. `two_agent_batch`
4. `two_round_replan`
5. `success_plus_timeout`
6. `metabot_offline`
7. `provider_interruption`
8. `provider_refusal`
9. `crash_recovery`
10. `duplicate_replay`
11. `concurrent_turn`
12. `waiting_user_resume`
13. `authorization_revoked`
14. `generation_refresh`
15. `capability_changed`
16. `forced_submission`
17. `zero_tool_retry`
18. `parallel_overflow`
19. `long_context`
20. `attachment_minimization`

每个场景都必须通过，并在场景前后确认 `V2_MISSION_RUN_WRITES=0`。测试使用独立
脚本模型，不让待发布的 Opus 自己评判自己。

## 1. 本地完整门禁

在隔离 worktree 的仓库根目录执行：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../webui
npm test -- --run
npm run build
npm audit --omit=dev --audit-level=high
cd ..
bash -n deploy/cloud/*.sh deploy/local-execution-worker/*.sh
docker compose -f deploy/cloud/compose.yaml config >/dev/null
git diff --check
```

Docker 不可用时只能记为“本机未执行”，不能写成通过；必须在 Dev/preview 或 CI
补跑 Compose render 后才可发布。

确定性矩阵也可单独执行：

```bash
cd backend
mapfile -t cases < <(.venv/bin/python -m app.agent_brain.acceptance_contract pytest-args)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/test_agent_brain_v2_acceptance.py "${cases[@]}"
```

macOS 自带 Bash 没有 `mapfile`；正式脚本 `deploy/cloud/accept.sh` 使用兼容的
`while read` 数组装载方式。

## 2. 独立答案质量评审

在接受脚本配置文件同目录创建 `quality-review.json`，目录权限 `0700`，文件权限
`0600`。评审人必须是独立 Codex 或具名业务专家，不能是生成答案的同一个 Opus。

```json
{
  "schema_version": 1,
  "release_sha": "替换为待发布的40位Git SHA",
  "reviewer_id": "具名评审人或受控工号",
  "decision": "approved",
  "scenarios": [
    {
      "scenario_id": "hr_quality",
      "outcome": "approved",
      "material_defects": []
    },
    {
      "scenario_id": "marketing_quality",
      "outcome": "approved",
      "material_defects": []
    }
  ]
}
```

以下任一情况必须写为不通过：隐藏子 Agent 失败、声称使用了实际未使用的 Task、
暴露内部推理、或明显弱于直接使用对应专业 Agent。评审文件只记录结论和缺陷枚举，
不复制问题和答案正文。

## 3. 生成 Reference 恢复证据

先部署 Stage A（V1/V2 intake 都关闭），然后在本机执行：

```bash
deploy/cloud/accept.sh /绝对路径/acceptance-config.json reference
```

脚本会核对本地 HEAD 与云端 release SHA，运行 20 个真实自动化场景，随后原子写入
云端 root-only 文件：

```text
/opt/orbbec-agent-platform/private/agent-brain-v2/reference-recovery.passed
```

它是绑定 release SHA 和 `acceptance_contract.py` SHA256 的 JSON，不是可复用的
裸 `passed` 文本。完整成功标记为：

```text
AGENT_BRAIN_V2_REFERENCE_OK
```

## 4. 真实 Provider 能力探测

`release` 会在私有 `platform-brain` 容器内调用 `provider_probe`，核对：

- `claude-opus-5` 与 1M 上下文档位；
- adaptive thinking，`display=summarized`，且摘要不会包含在探测证据中；
- 流式请求和 `max_output_tokens=65536`；
- 强制 `submit_answer` 时 tools 字节不变；
- 不发送 `temperature`、`top_p`、`top_k` 或 `fallbacks`；
- 会话中 system message 能力；
- 稳定前缀 `1h` 和滚动前缀 `5m` cache TTL。

输出只允许能力布尔值、请求 ID、token 计数和摘要哈希，写入：

```text
/opt/orbbec-agent-platform/private/agent-brain-v2/provider-evidence.json
/opt/orbbec-agent-platform/private/agent-brain-v2/provider-evidence.sha256
```

## 5. Worker 崩溃与恢复演练

在 Dev/preview 对同一 release 分别在以下事务边界终止并恢复
`platform-brain`，每次都确认只有一个 Task、一个终态事件和一个最终 Message：

1. 模型响应持久化前；
2. 模型响应持久化后、Task 派发前；
3. Adapter 投递提交后、终态事件写入前；
4. 终态事件提交后、整批唤醒前；
5. 最终 Message 事务提交前；
6. 最终 Message 事务提交后、客户端重连前。

恢复只依赖 append-only `brain_steps`、`brain_tool_calls`、`agent_tasks` 和
`agent_task_events`；checkpoint 删除后结果必须一致。禁止通过重建 Loop、改
row status 或新建 V1 Mission“修复”演练。

## 6. Mac 离线隔离

在 Dev/preview 暂停本地 Agent Worker，并完成两个检查：

- 一个明确要求 Brain 直接回答的 Turn 仍然完成；
- 一个需要 HR 或 Marketing 的 Turn 只把 `metabot_local` 标为 unavailable，
  页面仍显示 Brain 的明确部分结果或失败原因。

恢复 Worker 后确认同一个业务幂等键不会产生第二个任务。不要停止或重启 FAE。

## 7. 缓存与成本证据

运营指标必须分别输出三条路径，不能合并成一个平均命中率：

- `continuous_step`
- `first_waiting_agents_resume`
- `later_waiting_agents_resume`

每条只记录请求数、input/output/cache-read/cache-write tokens 和估算成本。若
`waiting_agents` 恢复路径的命中率显著低于连续 Step，必须重新评估 1h TTL，不能
用连续 Step 的高命中率掩盖。

## 8. FAE 不干扰与零旧链路写入

切换前后必须逐项相同：FAE container ID、image ID、StartedAt、RestartCount、
Config/Mounts 哈希、健康状态、域名页面哈希和原 IP 页面哈希。最终门禁必须精确
输出：

```text
V2_MISSION_RUN_WRITES=0
FAE_MANAGED_FILES_UNCHANGED=true
```

## 9. 生产发布、复验与回滚

完整发布：

```bash
deploy/cloud/accept.sh /绝对路径/acceptance-config.json release
```

它按顺序执行本地 Worker canary、Reference 矩阵、真实 Provider probe、V1
非终态清零、V2 零 Mission 写入、FAE 快照、原子开启 V2、真实钉钉用户会话和独立
质量评审验证。唯一完整成功标记为：

```text
AGENT_BRAIN_V2_ACCEPTANCE_OK
```

不切换、只复验当前 V2：

```bash
deploy/cloud/accept.sh /绝对路径/acceptance-config.json accept
```

回滚只关闭新 V2 intake，不改写已完成或进行中的 V2 行，不转入 V1：

```bash
deploy/cloud/accept.sh /绝对路径/acceptance-config.json rollback
```

成功必须输出：

```text
V2_ROLLBACK_HISTORY_PRESERVED=true
AGENT_BRAIN_V2_ROLLBACK_OK
```

恢复同一 V2 release：

```bash
deploy/cloud/accept.sh /绝对路径/acceptance-config.json restore
```

## 10. 证据保留

验收结果写入配置中的 owner-only `evidence_file`，仅包含 release/digest、场景
计数、Conversation/Turn ID、事件类型摘要、计数、评审身份与结论、FAE 不变性。
它不得包含 Secret、Cookie、Prompt、用户内容、答案正文、原始 Provider response、
thinking block 或 Adapter payload。运行期证据保存在受保护部署证据目录并通过
SHA256 引用，不提交到 Git。
