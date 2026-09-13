# H03/H13 replay harness 产品契约修正

基线为已封存提交 `d1524d3`；其 helper 与测试在修改前分别为 SHA256 `4c610bd4ff630010d23eb28f218dbbcb118ec5fc68a09d173be73645da13fb1a`、`199aca5205ceead8c39cc71a91ec1237e261837c81a42e5e860597cc1893ac47`。旧 `history-real-run-1` RED、角色、语料和独立内容审读均未修改。

本次只修两项额外工程门槛：

- H03 的“写 JD/JR”允许保存为一个 `kind=jd`、正文同时含 JD/JR 的合并成果。四轮仍使用同一 work/thread/result ID；每轮 input revision、result revision 与 SHA 均变化；上一轮精确 result ref 必须进入下一轮请求；原材料必须经过成功、完整的 `read_resource` 并继续进入成果 `source_refs`。H01 原有 `jd` 与 `requirements` 两 kind 门槛保持不变。
- H13 的分析请求允许直接在完整对话中交付。无保存成果时，只接受 work `completed`、`answer_state=ended` 且存在非空、可见 assistant message；默认工程 fixture 先完整读取 material，再给出含“数据事实 / 解释假设 / 需补证”的回答，不调用 `save_result`。已有 runtime 回归继续证明空响应不会被当成完成回答。

新增测试经过真实本地 HTTP/PG、合法登录会话、CSRF/Origin、幂等重放、错误 owner、附件上传/解析、material GET、work/message/result GET 与持久化操作收据。模型边界仍为 ScriptModel；fixture 中的 JD/JR 和三段文字只证明工程形态，不是专业质量评分。

RED 证据 `replay-harness-contract-red-1/`：两个用例分别在 H03 仍有两个成果、H13 仍强制 research 成果处失败，封存复跑日志为 `2 failed in 59.27s`。写测试后首次交互执行也得到相同两个断言失败，耗时 `60.40s`，但该次没有把 stdout 单独落盘；因此正式证据以未改写的 59.27 秒复跑日志为准，不把两个不同执行时长混称同一次。GREEN 证据 `replay-harness-contract-green-1/`：整份 history replay `14 passed, 1 skipped in 54.53s`；正常回答及空响应重试边界 `2 passed in 1.19s`；py_compile 与 diff-check 通过。GREEN `command.json` 为执行后根据真实 tool call 补录，明确没有虚构起止时间。

最终 helper SHA256 为 `ebc1562fe491b5e1e0064c03e101927e7c19ce5a27b50e07f594587c5a992b38`，测试 SHA256 为 `d12d6369b3ccc3d5c86e8d2b422b300e5514114e51d12cdb47dbe029316b81de`，准确 patch SHA256 为 `50c22cfdbf9a7ee3503960cc7ac91bcd045409bf9825565c6f376c88741e0c51`。

这只证明 replay harness 与既定产品保存语义一致。`history-real-review-1` 对 H01 的分级/依据强度问题、H13 把“未提供”误判为“未采集”的内容问题，以及 H03 第一轮建议性门槛风险仍然成立；不能据此宣布真实模型或专业质量通过。本次没有运行真实模型、浏览器或生产。
