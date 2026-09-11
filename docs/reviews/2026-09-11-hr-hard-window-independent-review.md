# HR 硬窗口修复独立审查

日期：2026-09-11
审查范围：`c944665` 与 `ffdda53`，重点为上下文构建、持久历史、预算和恢复语义。本文是代码与测试证据审查，不是生产或业务质量验收。

## 结论

未发现阻塞项。`c944665` 关闭了“最新未消费多工具批次整体超过硬窗口后永久等待预算”的回归：软阈值下仍保护最新批次，使它先进入一次 work；仅当完整 work 加输出预留确实超过模型硬窗口时，才允许按完整 entry 逐项摘要。摘要成功后重新构建上下文，继续压缩下一项，直到剩余原始工具正文能进入可发送 work，而不是只执行一次 summary 后停住。

我先前指出的“较短 rev2 不能恢复同一超大 rev1 work”并非通过丢弃旧历史修复。`ffdda53` 将产品契约收窄为可兑现边界：旧 work 继续 blocked，旧输入、历史与成果保留；用户拆分范围后创建新 work，或由管理员核实窗口配置。新增数据库测试明确断言较短 rev2 仍为 `context_too_large`、两个 user entry 原文均可解封回查、独立新 work 可构建普通 work 上下文。界面和运行规格也不再承诺“缩短同一 work 输入”即可恢复。该变更关闭了审查阻断，且没有静默删除历史。

## 机制核对

`hard_window_fits` 使用完整普通 messages、工具 schema 和 `max_output_tokens` 预留判断。普通输入仍按 trigger/target 做主动压缩；当未消费工具批次仍能装入硬窗口时，压缩分支被跳过，保持原始批次先由 work 消费。硬窗口超限时才把这些 entry 纳入候选，且候选将非 summary 排在旧 summary 前，因此每次成功摘要覆盖新的大正文；旧 summary 不会抢占有限摘要窗口，也不会被单独反复摘要。

候选以 entry 为原子，tool entry 重建为完整 assistant tool-call 与 tool outcome 对，不截断正文。若固定摘要前缀加空历史和输出预留已经超窗，或第一个不可拆 entry 单独也无法装入，则 work 转为 `blocked/context_too_large`。这类结构限制不再伪装成追加预算可解除的 `budget_exhausted`。

逐项摘要测试建立同一 committed work attempt 的三条大工具结果。循环中每次 context 的 `derived_from` 只有一个完整 entry；两次 commit_model/commit_summary 后，第三条原始 tool outcome 出现在普通 work 上下文，work 保持 running。该断言覆盖整批大于硬窗口而单项可摘要的关键路径。单项工具结果本身不可装入摘要窗口时则 blocked，且既有模型调用扣记仍为一，不因结构失败回退调用账本。

每个摘要仍走 `prepare_model`、`mark_model_sending`、`commit_model` 和 `commit_summary`。输入 token 估算与输出预留写入持久 model attempt，发送时再次经过模型调用数、总 token、活动时间和收尾 reserve 检查；没有新增预算外摘要通道。摘要重试或 prepared/sending 状态不构成“工具已消费”，只有同 input revision 上更高 ordinal 的 committed work attempt 才解除未消费保护。重启后这些判定来自持久 attempts、operations 和 entries，不依赖进程内循环计数。

summary provenance 仍记录准确 entry id、seq 和 input revision。读取时递归验证摘要祖先存在、顺序早于摘要、当前作用域仍允许，并以 coverage 去除已替代原文；撤权或祖先缺失会排除摘要。保存摘要继承当前 input 的 objects、冻结请求 dependencies 以及被覆盖 entry 的对象/引用标签。逐 entry 压缩没有扩大 objects 或引用权限，当前 checkpoint 仍只来自固定前缀，不由模型摘要覆盖。

新 input revision 会重建 checkpoint 和 fence；旧 fence 不能提交新 attempt。不可拆的旧 user entry继续留在原 work 历史中，这是保留追溯而非循环缺陷。新建 work 的查询以 work id 隔离，不导入旧 work entries，因此拆分后的较小工作可发送，同时原 work 保持 blocked 可查。

## 测试与边界

我审读了新增及相邻断言，包括：软阈值保护、summary 重试不算消费、更高 ordinal committed work 才算消费、三项大批次逐项摘要至 work、固定前缀/单 entry 超窗、较短 rev2 保留历史且仍 blocked、新 work 可发送，以及摘要来源、checkpoint、对象和引用范围测试。实施记录报告相关后端 **79 passed**、前端 **28 passed**；本审查没有重复运行这两套完整范围，不能把报告数量当作独立复跑证据。

仍有明确产品限制：不可拆的单条历史不会在同一 work 内自动恢复；需新建较小工作或调整经审核的模型窗口配置。摘要正文是否真实保留了承重证据仍取决于模型输出和后续业务审读。上述限制已在规格、界面和测试中一致表达，不构成本次工程阻塞。
