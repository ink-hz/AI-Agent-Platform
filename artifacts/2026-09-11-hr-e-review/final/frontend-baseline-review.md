# 前端 11 项失败的基线定性与测试修订

日期：2026-09-11

## 修改前的独立基线核查

`final/frontend.log` 的 **11 failed / 305 passed** 不是新生产提交 `fe10fae` 或本次 no-commit 整合新增的前端回归。修改测试前，我分别在 `b974a87318955f14c21a26bbcad386dc8ea93ad1`、`fe10fae1969b4c42f8c05d48c8eaf507a787b2b5` 的 detached 临时 worktree 和当前 worktree 运行目标两文件，三次都是 **11 failed / 17 passed**，失败名称和断言形态逐项一致。临时 worktree已删除。

当时下列直接相关 blob 在两个提交和当前 HEAD/index/worktree 中完全相同：

| 文件 | 修改前 Git blob |
| --- | --- |
| `HrCandidateWorkspace.tsx` | `8718a6d6a8320fc4ee9258fb34f8824fe109365d` |
| `HrCandidateWorkspace.test.tsx` | `079b13eae0ee341ab1ca430085c084823b02d5b0` |
| `HrP0Combined.acceptance.test.tsx` | `3cbdf59ba16f3ec6466688fe8bffba9b03679684` |
| `conversationApi.ts` | `6c96306b90ca12821bda8d8fdc2c762849af5233` |
| `HrPositionDetailsDrawer.tsx` | `0acae4be4aa19c60bbf518f89798627b91fe250a` |
| `HrCandidateAnalysisCard.tsx` | `cd805cbdc2159fee9052640cab53d69867a78a95` |
| `hrMutationRequest.ts` | `c81b8976aa776f03ae634d6b710dca47269bd300` |
| `package.json` | `647016ab1fc8e69f91114f37c63320ad47a61c0b` |
| `package-lock.json` | `e6ddc474a3cc364817beae849275972621d8b609` |
| `vite.config.ts` | `3961444d1376c98dc1151a28e3f01360d2ad5ffd` |

上述结论只描述修改前状态。此后我按现行契约修改了两个测试文件；当前 worktree blob 分别为 Candidate test `189e942565bffcebd1df9bea3db97c92e3d18456`、P0 combined test `82339c20cdc3fbc7b11993bde3280dea3dcb5f9f`，已与表中不同。产品源、序列化实现和依赖文件没有因这组修订改变。

三次隔离运行共用根工程已有的 `webui/node_modules`，但三版本的 `package.json`、`package-lock.json` 和 `vite.config.ts` blob 相同，运行器均为 Vitest 3.2.6。这足以支持源码和测试版本归因，不构成新的 clean `npm ci` 供应链复验。

## 根因

Candidate workspace 的 10 项失败来自 `7b04117` 后未同步的旧测试。现行组件把候选人分析、面试和比较操作转换成 `onDraft(text, positionCandidateIds, attachmentIds)`，再由唯一 HR 主对话提交；组件已不使用 `startTask`、`taskStatus` 或 `compareCandidates`。没有 `onDraft` 时操作禁用，可比较性按 relation 是否为 `active` 判断。

P0 combined 的首个失败来自消息 exact equality 缺少现行 `scope` 和 `inputResultRefs`。补上这两个字段后，测试继续在 `POSITION PACKAGE · V2` 处失败，证明它不只是两字段漏写：后半段仍假设已退出页面的 position-package 卡片、确认草稿按钮和 `/positions/:id/tasks` 流程。当前页面使用岗位 picker、岗位详情 drawer、主对话 scoped message、conversation results list 和 result detail API。

## 测试修订与数量变化

修改前两个文件共有 **28 项原文测试**：Candidate 27，P0 combined 1。修改后共有 **29 项**：Candidate 28，P0 combined 1；没有新增 `skip`。

Candidate 的 10 个契约失配用例改为现行行为：

- 无 `onDraft` 时禁用分析/面试/比较，并确认旧 API 零调用。
- 比较草稿携带两条准确 relation id，并为每位候选人只取最新 active 简历；单个候选人材料读取失败时不产生半成品草稿，重试成功后才产生完整草稿。
- 同一候选人的重复动作保持相同 scope；不同候选人的草稿相互独立。
- 匹配和面试 prompt 只包含当前候选人，使用其最新 active 简历。
- active relation 可比较，archived relation 禁用；人工纠正绑定最新持久化分析版本。
- 分析刷新成功展示最新版本，刷新失败仍保留已有版本。

原 comparison renderer 覆盖不再通过已删除的 `compareCandidates` mutation 触发。我新增 1 个独立用例，把 frozen `analysisKind: "comparison"` fixture 挂到仍可达的 `candidateAnalyses` 历史读取链路，打开候选人详情后在 `.hr-candidate-comparison-result` 中验证：两位候选人摘要、证据覆盖数、未知项数、`null` ranking 的“未提供单一排序”文案，以及不展示原始 JSON。

P0 combined 仍是 1 个组合用例，但已按现行页面完整改写，保留以下可达业务断言：

- 唯一 HR 主对话在前往 HR 情报再返回后保持同一挂载节点，未发送草稿和已上传附件仍保留。
- 情报问答显示指定公司、全景版本和来源链接，并排除另外两家公司。
- 通过现行岗位 picker 选择岗位，并从岗位详情读取已确认 JD/JR。
- 三份简历独立上传并创建 batch；一份失败后仅重试该份，返回 ready 后 UI 出现“审阅候选人丙”且失败原因消失；另外两份经过人工审阅与确认。
- 候选人甲分析和候选人乙面试分别回填并提交到主对话。每条消息 exact 检查 `positionId`、唯一 `positionCandidateIds`、该候选人最新简历 attachment、仍 active 的会话附件、空 `inputResultRefs`；两条 prompt 不互相包含另一候选人。
- 重挂载同一 conversation 后，通过 `GET /api/v1/hr/conversations/:id/results` 找到两项成果，并经 `GET /api/v1/hr/results/:id` 重开候选人分析和面试成果正文。
- 明确断言没有 position-package 请求，也没有 `/positions/:id/tasks` 请求。

退役的是旧 position-package 页面步骤和 Candidate 组件内旧 task lifecycle。当前循环与恢复职责已有以下具体测试承接：

- `conversationApi.test.ts`：`reuses one conversation for a follow-up and retains its UUID across retries`
- `ConversationPage.test.tsx`：`recovers a completed professional-Agent turn when the SSE slot is temporarily unavailable`
- `HrLoopWorkspace.test.tsx`：`starts without a position using server budget and retains key after network failure`
- `HrLoopWorkspace.test.tsx`：`restores references and appends only the continuation contract`

这些测试覆盖现行会话复用、提交重试、完成轮次恢复及 Loop 续跑，不声称继续验证已删除的旧 `/tasks` UI 状态机。

## 验证结果

修改前原扩大范围：**29 files；11 failed / 305 passed（316）**。

修订后的目标及现行链路相关范围：

```sh
cd webui
npm test -- --run \
  src/workspaces/hr/HrCandidateWorkspace.test.tsx \
  src/workspaces/hr/HrP0Combined.acceptance.test.tsx \
  src/workspaces/hr/HrTurnResults.test.tsx \
  src/workspaces/hr/HrWorkspacePage.test.tsx \
  src/workspaces/hr/HrLoopWorkspace.test.tsx \
  src/conversationApi.test.ts \
  src/pages/ConversationPage.test.tsx
```

结果：**7 files passed；153 passed / 153**。

修订后的原扩大范围命令：

```sh
cd webui
npm test -- --run src/workspaces/hr src/hrAgentApi.test.ts src/auth.test.ts src/documentTitle.test.tsx
```

结果：**29 files passed；317 passed / 317**。比修改前多 1 项，来自独立恢复的 persisted comparison renderer 覆盖。

全仓 build 首轮还发现测试 fixture 使用的 `Array.prototype.at` 超出项目 `ES2020` lib；已仅把该行改为 `messageBodies[messageBodies.length - 1]`，没有调整项目 target/lib 或产品源。随后再次运行 P0 单文件验证该 fixture 修正。

## 证据与边界

- 修改前原扩大范围：[frontend.log](./frontend.log)
- 修改前当前目标复跑：[frontend-current-targeted.log](./frontend-current-targeted.log)
- `b974a87` 修改前隔离复跑：[frontend-b974a87-targeted.log](./frontend-b974a87-targeted.log)
- `fe10fae` 修改前隔离复跑：[frontend-fe10fae-targeted.log](./frontend-fe10fae-targeted.log)
- 修改前 blob、失败名称与 blame：[frontend-blob-comparison.log](./frontend-blob-comparison.log)
- 修订后最终相关范围：[frontend-corrected-related-final.log](./frontend-corrected-related-final.log)
- 修订后最终原扩大范围：[frontend-corrected-broad-final.log](./frontend-corrected-broad-final.log)
- P0 独立 reviewer 后续：[frontend-p0-review-followup.log](./frontend-p0-review-followup.log)
- ES2020 fixture 修正后 P0 复跑：[frontend-p0-es2020-final.log](./frontend-p0-es2020-final.log)

本次只修改测试和本报告/日志，没有修改产品源，没有进行浏览器验收、生产调用或模型质量验收。组件测试使用受控 provider/API fixture；它验证请求边界、UI 状态和结果重开，不代表真实后端或真实模型验收。
