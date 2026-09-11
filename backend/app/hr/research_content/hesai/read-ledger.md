# 禾赛完整阅读台账

## 实际读取范围

本会话逐段完整读取三个飞书原始归档的职责 `description` 与要求 `requirement`，覆盖全部 364 个统一岗位。未用标题选样，也没有跳过销售、职能、校招或实习正文。

原始渠道合计 370 行，其中 6 个原始 ID 跨渠道重复，对应 364 个唯一岗位。先比较两字段的完整字符串，再按逐字相同全文复用阅读：共 345 种不同正文实际通读，另外 25 行复用已读正文。其中不仅有同 ID 重复，也有不同岗位 ID 的相同正文；后者保留为不同岗位身份，没有将 364 改成 345 个岗位。不是相似文本去重，任意差异都会保留为另一种全文。

每种正文在 [reading-records.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/reading-records.json) 中有连续 `read_index`；每个原始行到实际阅读项的映射见 [read-coverage.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/read-coverage.json)。逐条身份、全文、原始数组位置及原页在 [records.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/records.json)。`body` 是职责、人工分隔符 `【要求】`、要求的串接；引用校验直接检查原始字段，不将分隔符冒充原文。

| 实际阅读范围 | 完成情况与阅读笔记 |
| --- | --- |
| 1–50 | 完整。首次 1–30 输出中 8–20 被截断，已单独完整重读 8–20。 |
| 51–156 | 完整。51–75 输出中 59–64 被截断，已单独完整重读 59–64。 |
| 157–222 | 完整。质量评测、驱动材料与集成、芯片测试及代工接口等笔记已记录。 |
| 223–239 | 完整。培养边界、芯片需求到测试项、系统接入与制造质量。 |
| 240–256 | 完整。多端测试、制造接管、客户质量、器件测试与材料。 |
| 257–276 | 完整。相干光模块、EEL/SiPh、系统指标、SPAD、DFT与封装。 |
| 277–296 | 完整。安全、系统建模、制造SDK、FPGA与芯片设计验证。 |
| 297–314 | 完整。VCSEL、各实习岗位、Kosmo自动化、安全培养与数据流程。 |
| 315–336 | 完整。实习岗位的真实入口、开源内核扩展、控制年限、数据闭环。 |
| 337–345 | 完整。职能与校招、热设计、安全/机械/FPGA、360影像、技术资料和重建算法。 |

其余批次没有未补读的截断。中途出现的 shell `python` 命令不存在仅导致空输出，随即改用 `python3`，未将失败调用计作阅读。过程笔记保存在 [reading-notes.md](../../../../../.superpowers/sdd/hr-question-analysis/hesai/reading-notes.md)，其中早期候选线索不等于最终判断；六题仅在完成 345 种全文后，按 Q9、Q3、Q4、Q5、Q10、Q11 顺序分别成稿。

## 原始归档与时间

| 渠道 | 原始行 | SHA-256 |
| --- | ---: | --- |
| `https://kwh0jtf778.jobs.feishu.cn/index` | 160 | `eb6fdf1d60e35a3d87394721a314a14c9b2ce6852b0355f02e3b26946cf1a365` |
| `https://kwh0jtf778.jobs.feishu.cn/229043` | 150 | `c63a3490e7116b49b3e20fb72f0b56964a38a57a8aba8c9d25039d60240088c8` |
| `https://kwh0jtf778.jobs.feishu.cn/073183` | 60 | `51ffc8c55d15cd005c0acfd4a3bad7db856c37eecd669e334d8d336e8d78c5b6` |

路径均为本包 `evidence/sha256/前两位/完整哈希`，行定位为 `#data.job_post_list[i]`（零基索引）。三份归档已直接重新打开并核验原始字节哈希。

这家公司的真实观测时间为 **2026-09-06 22:10:58.153665 至 22:10:59.954061（北京时间）**，以 `source-coverage.json` 为准；没有沿用任务书对全包的 22:37 概述。本文未用发布时间推招聘趋势。

六个跨渠道重复原始 ID 均已逐字确认两字段相同：`7664462204881078554`、`7654484860152924459`、`7628905100249221403`、`7591429089061161262`、`7569126239321409811`、`7233622408628226363`。全部逐字重复分组见 [duplicate-bodies.json](../../../../../.superpowers/sdd/hr-question-analysis/hesai/duplicate-bodies.json)。相同正文的其他岗位只复用阅读，保留其独立 `job_id`。

## 已知内容边界

- `https://www.hesaitech.com` 在 2026-09-06 22:11:00.343213 采集失败，错误为 `source_rejected`，没有官网产品或客户事实可补充。
- 370 行部门 ID 均为空；没有用职责推组织树、职级或编制。
- 电子技术员 `030a4c8b-c994-52c5-983a-0d0112c954ff` 的职责是字面“无”。已完整读取这一原始值及要求，但没有把它说成职责完整，也未反推缺失任务。
- `normalized-jobs.jsonl` 只用于身份、原页和归档映射；正文判断来自原始归档。没有读取本包 `aggregates.json` 或旧 `analysis.json`，没有按技能正则分类计数。

## 产物与核验

六份题目：[Q9](q9-technical-intent.md)、[Q3](q3-task-requirements.md)、[Q4](q4-product-stages.md)、[Q5](q5-customers-applications.md)、[Q10](q10-gaps-responsibility-boundaries.md)、[Q11](q11-internal-consistency.md)。

已执行 [引用与身份核验](citation-validation.md)。脚本可以确认身份、完整原文保存、逐字重复及引用来源；它不能证明“模型阅读过”或替推断打合格分。上面的完整阅读记录对应本会话实际逐批打开并通读的范围。
