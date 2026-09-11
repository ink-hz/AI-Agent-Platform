# 前端测试独立复核

2026-09-11；只读审阅测试与可达组件行为，仅新增本报告，未修改产品或测试。未运行真实浏览器、HTTP 服务、数据库、模型或任何生产操作。

审阅快照：

- `webui/src/workspaces/hr/HrP0Combined.acceptance.test.tsx` SHA-256 `0b8708906e5d7e25199b7ed05113f14cf82f829e763f2780dad448f7eb693ce3`
- `webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx` SHA-256 `f15cb45d01e033a3b50e27e42597ba0df98efc1e4b16b45165915a69b02d8832`

独立执行 `npm test -- src/workspaces/hr/HrP0Combined.acceptance.test.tsx src/workspaces/hr/HrCandidateWorkspace.test.tsx`，20:35:07 本地时间，Candidate 28 + P0 1 = 29 passed。该数量是两份前端测试，不是业务闭环或生产验收。

## 实际覆盖映射

| 要求 | 当前证据与边界 |
| --- | --- |
| 导入 3 份简历 | 实际在 jsdom file input 置入 3 个 File，经过前端 upload/content/complete 请求适配与“开始解析 3 份简历”点击；batch handler 严格断言三个附件 ID。解析返回由 fetch fixture 提供。 |
| 失败与重试 | 第三份失败提示、人类可读错误、不露 parser code、retry POST 路径与 expected_row_version=3 均断言；返回 ready 后的界面状态尚缺正向断言，见下文。 |
| 人工确认 2 份 | 分别点击甲/乙审阅和确认；handler 校验对应 draft ID、行版本、上下文、称谓、事实与 merge=null；最终 2 位已确认。没有自动确认第三份。 |
| A/B 准确范围及附件 | 甲分析、乙面试先检查 composer 名称不串人，再严格断言完整 messages POST body：各自 relation ID、各自简历附件、同一 position ID；岗位补充附件按当前 active attachment 规则保留。三次消息提交计数也受断言。 |
| 最新 active 附件 | P0 各候选人只有一份简历；新版 Candidate 单测另有旧版本与高版本 erased 干扰，精确断言最新 active 附件。不要把 P0 单独描述为多版本筛选验收。 |
| 结果重开 | unmount/remount App 后重新取 conversation results 索引，展开两个 details，读取各 result endpoint 并断言甲分析正文、乙面试正文及仅一个聊天宿主。结果存储由 completeCurrentTurn 手动更新内存 savedResults，证明前端重读/渲染，不证明后端结果生成、持久化或 fencing。 |
| 主对话草稿保持 | 切换 HR 情报再返回，严格检查同一个 chatHost 节点、未发送文字与已就绪岗位附件仍在；候选 onDraft 随后有意替换 composer 文本。此测试未断言每次候选侧栏往返的节点身份，亦未声称刷新后未发送草稿持久化。 |

## 具体发现与有界建议

1. **P0 重试成功的 UI 断言不足。** 当前在“重试解析”后立即审阅、确认甲乙，最后只检查 retry 请求发生。若组件发出合法 retry 请求但忽略 ready 返回，让第三份一直显示 failed，本用例仍能通过。建议在重试后定位第三份，断言可审阅/ready 且其失败提示消失；无需修改产品。现在可称“失败重试请求已覆盖”，不能单凭本用例称“重试恢复状态已验收”。
2. **删除旧接口步骤时连带减少一项仍可达的组合覆盖。** 原组合测试中“指定公司公开情报问答的来源 URL/版本展示，以及排除其他公司名称”的聊天展示断言被删。该普通对话展示并非 position-package/task 接口专属；新测试仅浏览 HR 情报后返回，没有替代这段问答。建议保留为独立聊天展示回归，或明确本次 P0 范围不再包含这项；原测试也是手工提供回答，不能宣称验证真实检索准确性。

原 PDF 新票据/重复下载步骤也从 P0 删除，但 Candidate 测试仍保留每次重新读取 Position resource、申请新 ticket、失败后重试等可达覆盖，因此这是组合范围收窄，不是整个套件丢失该行为。

## Candidate 复审

新 onDraft 测试使用 relation IDs 和精确附件数组，正向 calledWith/times 断言不会因可选点击未命中而空通过；独立候选人的文案与 scope 均有约束。首审指出的历史 comparison 渲染覆盖损失已补回：当前 candidateAnalyses fixture 的 comparison 条目通过实际详情路径渲染，并在 .hr-candidate-comparison-result 内校验两位摘要、覆盖数、未知项、空排序及非原始 JSON。旧 startTask/taskStatus/compareCandidates 执行断言移除符合当前入口；无需恢复旧接口。

## 最终冻结版本复审（追加，保留首审记录）

2026-09-11 20:39:38 本地时间，独立重跑相同两文件命令：Candidate 28 + P0 1 = **29 passed**。复审版本指纹：

- `webui/src/workspaces/hr/HrP0Combined.acceptance.test.tsx` SHA-256 `834798c33d4a6188bc8d2d08884d2816cfad17db2a2a643133e4d9f62872974e`
- `webui/src/workspaces/hr/HrCandidateWorkspace.test.tsx` SHA-256 `f15cb45d01e033a3b50e27e42597ba0df98efc1e4b16b45165915a69b02d8832`

首审两项建议均已落实：

1. P0 第 970–973 行在重试后等待“审阅候选人丙”按钮出现且启用，再断言“解析失败”及具体错误文案消失。button helper 自身断言节点存在；忽略 ready 返回而保留 failed UI 将不能满足这些断言。因此现在已覆盖 fixture 驱动的第三份重试恢复 UI。
2. P0 第 907–935 行恢复指定公司普通对话：严格校验提交正文、当前岗位附件与空候选 scope；在对应 assistant 消息内正向校验目标公司、版本、来源 URL，并排除另外两家公司。正向断言保证消息未找到时失败。该恢复保持 mock 回答展示的原有边界，不证明真实情报检索准确性。

恢复情报问答后总 messages POST 数已同步为 4，甲/乙精确 scope 断言对应 messageBodies[2]/[3]，岗位附件仅作为 active attachment 延续；两条候选结果重新挂载读取、人工确认两份及主对话草稿导航保持覆盖继续通过。Candidate 历史 comparison 渲染测试继续保留并通过。当前没有遗留的上述首审覆盖缺口。

本次只读复核测试与组件，唯一写入为本报告追加；未修改测试或产品，未执行生产、真实浏览器、模型、HTTP 服务或数据库操作。全仓前端/build 由 root 另行运行，本复审不冒充该结果。

## ES lib 兼容索引调整复核（追加）

当前 P0 SHA-256：`62ac73dae13dc25961e55e143da6cbf8c03c1cb1cbb24f6cee28440056482f34`。只读核对：将当前唯一的 `messageBodies[messageBodies.length - 1]?.scope` 在内存中还原为 `messageBodies.at(-1)?.scope` 后，全文 SHA-256 精确恢复为上一复审版本 `834798c33d4a6188bc8d2d08884d2816cfad17db2a2a643133e4d9f62872974e`。因此相对该版本唯一差异确为 ES lib 兼容索引调整；非空数组均读取最后一项，空数组均由 `?.scope` 安全得到 undefined，覆盖结论不变。未覆盖旧指纹，未修改测试或产品，也未重跑大范围测试；构建与当前目标测试结果由 root 的新日志提供。
