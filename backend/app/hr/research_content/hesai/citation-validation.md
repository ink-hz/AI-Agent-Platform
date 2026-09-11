# 禾赛引用与身份核验

执行时间：`2026-09-09T04:05:47.571359+00:00`。校验脚本：[verify_sources.py](../../../../../.superpowers/sdd/hr-question-analysis/hesai/verify_sources.py)。

直接重开三个原始归档，核验原始字节 SHA-256；370 行与 364 个统一岗位身份对应，345 种职责与要求全文逐字去重关系成立。六份题目中的 80 段引文逐段与原始 `description` 或 `requirement` 比对，均为连续逐字片段，没有跨越人工加入的正文分隔符。Markdown 实际呈现的引文与校验登记逐项一致。

| 文档 | 逐字片段 | 结果 |
| --- | ---: | --- |
| [q9-technical-intent.md](q9-technical-intent.md) | 17 | 通过 |
| [q3-task-requirements.md](q3-task-requirements.md) | 17 | 通过 |
| [q4-product-stages.md](q4-product-stages.md) | 12 | 通过 |
| [q5-customers-applications.md](q5-customers-applications.md) | 11 | 通过 |
| [q10-gaps-responsibility-boundaries.md](q10-gaps-responsibility-boundaries.md) | 9 | 通过 |
| [q11-internal-consistency.md](q11-internal-consistency.md) | 14 | 通过 |

详细结果：[citation-validation.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/citation-validation.json)。原始身份记录：[records.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/records.json)。记录中的 `source_url` 保留统一层岗位详情页，`archive_source_url` 单独保留该行实际采集渠道，`locator` 指向原始数组位置；不把跨渠道重复混成新岗位。

该检查不计算业务合格分，不证明推理正确，也不以引文数量评价质量。完整阅读是本会话实际完成的工作，范围与截断重读记录见 [阅读台账](read-ledger.md)。本任务没有改接口或页面，未执行 API、浏览器或生产验收。
