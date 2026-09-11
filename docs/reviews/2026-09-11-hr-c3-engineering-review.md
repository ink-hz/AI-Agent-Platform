# HR C3 工程收尾独立核查

日期：2026-09-11。

## 结论与边界

本轮核查未发现阻止 C3 进入用户验收的工程阻断项。该结论是 AI 对代码、持久化证据、测试运行记录和公开材料试验夹具的独立工程审查，不是用户验收、人工 HR 专业验收或生产验收。run9 的公开静态归档研究已有单独 AI 专业审读；run11 的正文仍在独立评审中，不能由本工程核查提前判定业务通过。

本报告不以测试数量、模型调用成功、全文返回、诊断 replay 或已有 AI 审读代替业务质量。真实候选人处理、现行官网、生产配置与发布均不在本轮范围。

## 已审代码链

本轮沿用并复核此前对 `764f8ec..32040a1` 的代码审查结论：

- 摘要请求携带当前目标、对象、准确引用和 checkpoint；前缀参与 token/硬窗口计算，但不进入历史 `derived_from`。摘要保存继承同输入修订的当前对象与冻结 dependencies，避免仅在前缀出现的候选人或材料被沉淀成无作用域摘要。后续历史仍按当前范围和权限重选，模型摘要不能覆盖持久 checkpoint。
- 待压缩历史以带 entry 身份的 `historical_records` 数据块发送，不继续充当活动 assistant/tool 对话；末尾重申摘要任务。包装后的实际消息、末尾指令和输出预算均进入窗口估算，entry provenance 保持可回查。
- 完整普通 `stop`/`end_turn` 且没有可见正文或工具时归类为 `empty_response`；Unicode whitespace 和 `Cf` 格式字符本身不算可见正文。该错误只使用既有持久 logical step 在预算内重试，最多三次实际发送；拒绝、截断、工具不匹配和其他畸形响应没有被并入此宽限。
- 最新工具批次必须先进入一次更高 ordinal 的已提交 work attempt，才可作为已消费历史压缩。summary、prepared、sending 和失败重试不算消费；判定限定当前 work 与 input revision。软阈值可为仍未消费的完整批次让出一次 work 调用，但完整输入加输出预算仍必须满足硬窗口，否则压缩其他旧历史或进入 `waiting_budget`。

`04c1de9` 只把既有工具权限契约写得更明确：`save_result.objects` 必须是当前输入已授权对象的子集；当前对象为空时使用 `objects=[]`，成果仍归当前 work。正文提及公司不产生对象授权。它没有增加工具、路由或权限，也不能追溯归因于此前未加载这段说明的 run9。

## C3 runner 核查

当前未提交的 `backend/tests/test_hr_agent_c_public_research.py` 是显式 opt-in 的公开材料真实供应商试验，不属于默认测试或生产路径。

预算续作不能被反馈掩盖。初始试验额度固定为 2 次模型调用并保留 1 次收尾 reserve，先形成 `waiting_budget`；HTTP 明确追加预算后才重新 claim 同一 work。resume 完成并写入 snapshot 后，runner 立即要求状态为 `completed` 或 `waiting_user`，随后才允许文件反馈或审读队列创建新输入。resume 若失败，任何后续反馈输入和模型调用都不会执行。

审读修订保留业务身份。每次反馈通过真实 `POST /works/{work_id}/inputs` 创建同一 work 的新 input revision，并携带服务器返回的当前 result refs。若进入反馈前已经存在成果，runner 记录原 result id/revision，并要求反馈后的结果中至少有同一 id 且 revision 已变化；另建新 id 不能冒充对原成果的修订。run9 的证据进一步显示详细稿与压缩稿使用同一 result id，后者的 `preceding_refs` 指向前一 revision。

审读队列等待不产生业务调用。runner 先输出包含 stage、work id、input revision 和 result refs 的 `ready-N.json`，随后只轮询对应 decision 文件并 `sleep(1)`。读到决定后先校验 work id 与 input revision，旧目录中的其他 work/revision 决定不能被消费；只有显式 `revise` 才调用真实输入 HTTP 和模型。最多三轮交接，第三轮不能继续 revise。`finish` 还要求存在独立 review record，但 runner 不把该字段本身解释为人类验收。

证据捕获限定于公开测试夹具。runner 在第一次发送前记录指定运行文件的 SHA-256，并把准确字节复制到试验输出目录的 `runner-sources/`，因此即使 runner 尚未提交，实际执行源码仍可复现。每次 canonical ModelRequest 在发送前写入 `public-requests.json`，transport 记录 stop、正文/工具参数字符数、有限 usage 字段、错误码和时长。完整请求可能含全部提示与材料，所以该机制仅用于显式 public-only fixture，不能迁入生产日志；`HR_C_PUBLIC_SCENARIO_FILE` 的内容公开性仍由发起试验的人负责，代码中的说明不构成自动脱敏证明。

16k 输出试验没有修改私有默认配置。runner 只读 `HR_C_REAL_PROFILE_FILE`，在 pytest `tmp_path` 中生成 mode `0600` 的临时 provider profile 与 budget 文件；`HR_C_REAL_MAX_OUTPUT_TOKENS=16384` 只改变该次试验的单次输出上限，并把收尾 token reserve 提高到至少两倍输出额度。service 的模型调用、总 token 和活动时长上限保持 24、1,200,000 和 1,800 秒，生产配置及源私有文件没有写入。run10 的 16k 对照只支持“原 8192 输出额度可能不足”的诊断：参数完整返回不等于成果已保存或专业质量通过。

## 运行证据

已检查 `/tmp/hr-c3-final-regression2.log`：运行结果为 **330 passed, 5 skipped**，耗时 151.17 秒。五项跳过均是需要显式公开 bundle、真实模型 profile 或本地情报 bundle 的 opt-in 测试；日志没有把这些跳过写成已执行验收。

根任务另报告本轮后续针对性范围 **32 passed**。本审查没有重复运行该范围，也没有取得另一份独立日志，因此将其记录为本轮执行方提供的验证事实，不把它提升为独立复跑证据。

run9 证明了同一 work 的实际预算暂停、显式追加、续作、权限拒绝、反馈新 revision 和同 result id 修订。其最终静态 19 岗成果已有 `2026-09-11-c3-run9-independent-research-review.md` 单独 AI 审读，结论可提交用户验收，但明确不是人类 HR 验收。run11 使用 7 岗公开场景、同一自然问题与试验性 16k 输出额度；在独立正文审读结束前，只能记录为运行/保存证据，不能写成 C3 业务验收通过。

## 仍未覆盖

- 没有生产部署、生产配置变更、现行官网核验或真实候选人模型验收。
- 没有人类 HR、用人经理或组织数据负责人的专业确认；AI 审读不能替代这些角色。
- 公开场景文件与完整请求捕获依靠显式测试操作者维持 public-only 边界，未实现通用内容分类或脱敏。
- 330 项默认/工程回归中的 opt-in 真实模型与本地 bundle 项保持跳过；run9/run11 的独立证据用于补充对应场景，但不能冒充默认套件已经执行这些外部依赖。

## run11 最终时点与文档一致性

run11 此后已完成显式公开场景实跑：pytest **1 passed，1115.50 秒**，墙钟时间包含独立审读等待。同一 work 的阶段依次为 `waiting_budget`（2 次调用）、显式追加预算后的 `completed`（累计 3 次），以及真实审读输入产生新 input revision 后的 `completed`（累计 7 次）。最终累计扣记 427,241 tokens，质量为 mixed，活动时间 571.80 秒；这些是工作账本数字，不是供应商精确账单。

第三阶段沿用原 result id 并保存新 revision，`preceding_refs` 指向旧 revision，符合 runner 对“修订而非另建成果冒充”的约束。独立 AI 正文审读认为七岗样本内的承重错误已修复，同时保留两项轻微限制：跨域人才池的“最可能”只能作为待验证搜寻假设；两份 requirement 是空白归一后的语义内容相同，不能称字节完全相同。该审读明确不是盲评、人类专业签收、用户验收、生产验收或 W11 全条通过。

最终局部一致性核查覆盖 `docs/reviews/2026-09-11-hr-cloud-loop-c.md`、`HR总体架构设计.md`、`HR_Agent工作流.md` 与 `docs/superpowers/plans/2026-09-11-hr-c-execution.md`。四处均把 C0/C1/C2 表述为工程完成，把 C3 限定为两个公开样例完成且独立 AI 审读可接受，并明确仍需用户评审；W11 表格写明“全条不判通过”。它们还保留了 3,437 岗规模、真实候选人、原生浏览器、生产盘点/配置/迁移、现行 D1 发布、D/E 及人类专业验收的未完成状态。16k 输出额度只归于 run11 测试副本，生产默认与总预算未变。没有发现用 run9/run11、诊断 replay、测试数量或 AI 审读宣称全部 C 或生产已经通过的表述。
