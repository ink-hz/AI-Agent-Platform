# C 补充修订独立审查

日期：2026-09-11。审查者：独立分工 AI `/root/c_followup_review`。基线：`47f577f160ba0dd613a3865c6bbb56a1a37119b7`；最终代码：`21a5a790e78dd9f12ef69ff1abe29194195b0b71`。本人未参与原 run9/run11 业务成果制作，本轮阅读代码、差异、原始文件及留存验证记录，并独立执行两项最终数据库边界测试，没有生产访问、发布或对象操作；不是人类 HR 专业验收。

**规格符合性：本轮范围通过。实现质量：本轮范围通过。** 审查发现的最终检查点增长、同批第二次读取使旧条目失去摘要路径均已闭合，未发现本轮仍需修复的阻断项。该结论允许提交 C 补充评审；D 仍暂停，未替代其前置或用户验收。

## 已核实的事实

1. **uploads JOIN 放大不是该基线的已成立缺陷。** 064 的 `uploads.attachment_id` 唯一约束使同一附件最多匹配一个 upload；新[生产盘点](../../artifacts/2026-09-11-hr-c-followup/production-attachment-census.json)也返回 `UNIQUE (attachment_id)`。本轮先聚合 uploads 的 SQL 写法可以保留，但不能把它描述成已经证实的生产重复计数修复。
2. **run11 专业保留归属在基线已明确。** 直接读取 `git show 47f577f:docs/reviews/2026-09-11-hr-cloud-loop-c.md` 确认第77行原有“以下两项保留仅属于run11”，分别是跨域人才池“最可能”仅作待验证搜寻假设、模板相同不是字节相同。本轮无需制造一次不存在的归属补写。
3. **064 校验回执相符。** 本地文件实际 SHA256 为 `1f9f083e76c3fc14e14a2ccfc403fcfb83716b4f1602cd5c41882e9ffb04c3aa`，与旧生产迁移回执及新[比较记录](../../artifacts/2026-09-11-hr-c-followup/migration-064-compare.json)一致。该结论是文件和回执比对，不等于全面证明数据库从未被额外变更。
4. **补存计量日志与旧哈希一致。** [原始日志](../../artifacts/2026-09-11-hr-c-followup/meter-parse-first-green.log)实际 SHA256 为 `b21538d225ceb226f55ffbbf9fe9fcba85597bab9598a54167b19dff392327a0`，与原计量审读所列哈希完全一致；末尾为 `52 passed in 63.10s`。这是恢复可复核性，不是本轮重新运行的52项测试。

## 附件影响与热修复边界

新盘点直接查询 attachments，不依赖擦除队列；脚本采用只读事务、超时及 rollback，仅输出聚合和必要诊断。留存结果显示观测时共2个 uploading 附件，deleted 状态和时间标记均为0、不一致标记为0、到期未删为0，任务表为空；attachments 和 erasure_jobs 的 RLS 均未启用。

064 的正常入队、领取、结果写回路径保留 erasure_jobs 行，完成只是更新状态。本次空表结合代码支持“在当前正常、没有清除任务记录的应用路径下未见曾入队任务”；**不能证明历史从未存在任务**，也不能排除人工清理、旧版本删除或其他未留存操作。现有证据未显示实际错删，不等于对象库完整性审计或生产擦除恢复验收。

[独立热修复验证记录](../../artifacts/2026-09-11-hr-c-followup/platform-erasure-hotfix-validation.md)说明补丁可独立应用到本地 master `2bddc77f49bb823bfc4d0271605990eb8bebad46`，无需 HR 私有迁移或 app.hr_agent；4项真实 PostgreSQL 回归覆盖旧单任务空字段、旧多任务交叉领取、修复后只领取一个任务并删除四类对象、迁移100的准确六列权限。对象客户端为确定性替身，经实际 AttachmentObjectWriter 调用；不是 live MinIO 或生产对象库验证。

处置手册明确暂停整个附件 worker 对扫描、解析、保留调度及擦除的影响，并要求操作前由服务负责人明确最长暂停窗口。先确认旧 worker 停止，再应用100；100后新镜像不可启动时继续保持停止，不能恢复旧镜像。该包是可审阅的独立发布材料，尚未执行生产暂停、迁移或发布。

## 读取边界与已闭合发现

`a2ae5cd` 已把读取校验放在事务内最终 checkpoint 之后，复用真实 read_id、entry_id、seq，失败回滚成功回执、读取记录和进度。这关闭了提交前估算漏掉新增 reading/ref/ranges/revision，以及不同 tokenizer 对占位 UUID 计数不同的问题。

**已闭合：第二条小读取使第一条近窗读取不再可摘要。** 独立临时测试在 `a2ae5cd` 使用真实 PostgreSQL 与实际 repository/tool 路径复现：同批第一条读取5000个中文，第二条对另一个合成 method ref 只读取1字符；把硬窗口固定在第一条实际最终摘要需求21998。第一条接受后摘要上下文可构造；第二条自身最终摘要只需7223，故也接受，但它新增的 reading 使第一条历史摘要超窗，随后构造上下文触发 `context_too_large`。未调用模型，未向数据库伪造成功结果。正式失败回归见[顺序读取红记录](../../artifacts/2026-09-11-hr-c-followup/checkpoint-growth-red.log)。

`21a5a79` 在本次最终 checkpoint 下重新检查同 attempt 已成功读取的完整条目；若新读取使旧条目不可摘要，回滚本次读取，返回先处理已有内容的错误，第一条仍可构造摘要。事务中历史解密之前，既有 `_dependencies` 已合并本 input revision 全部 tool source_refs 并逐项验权；同 revision 的对象来源固定输入，未新增越权历史解密入口。

[专项绿记录](../../artifacts/2026-09-11-hr-c-followup/checkpoint-growth-green.log)为4 passed / 1.66s，[上下文及仓库回归](../../artifacts/2026-09-11-hr-c-followup/read-resource-window-regression.log)为40 passed / 6.06s。本人独立在最终代码执行 `test_second_read_cannot_grow_existing_unconsumed_receipt_out_of_window` 和 `test_read_resource_guard_accounts_for_transactional_checkpoint_growth`，首次工具输出观察为2 passed / 1.43s，未单独落盘；为保留可复核原始 stdout，再次执行相同两项并保存[独立回归日志](../../artifacts/2026-09-11-hr-c-followup/independent-read-regression.log)，结果 **2 passed / 1.31s**。前者确认第二条被拒绝、只有一条已读记录且第一条摘要仍可达；后者确认使用提交后最终检查点判定并整体回滚。`git diff --check` 通过。

保证范围是本轮 read_resource 及同批未消费读取的准入恢复路径，不自动修复旧不可拆分历史、用户固定超大输入或任意来源的目录/检查点无限增长；这些仍保留明确的 context_too_large 边界。模型是否自主采用缩小范围重试以及摘要专业质量，不由本地数据库测试证明。

## D 前置与验收范围

D1 现行隔离处置发布及其影响验收、D5 独立附件发布、D7 显式研究 profile 和计量校准仍需按根架构与工作流执行。run9/run11 只证明显式300秒配置下公开样例；7岗另使用16384输出额度。默认120秒没有成功 C3 记录，不得下调保守估算下限或把扣记称为供应商账单。

新[count_tokens 探针](../../artifacts/2026-09-11-hr-c-followup/token-count-probe.json)对同配置网关 Messages 路径追加 `/count_tokens`，只提交公开合成文本、不请求生成、不跟随重定向，留存404。它只证明本次这个路径未成功，不能推断网关所有计数能力不存在，也没有完成同网关计量校准。D 仍暂停，D7 不因此关闭。

本次独立审查没有重跑无关全量套件，没有进程故障测试、前端组件测试、浏览器验收或生产验收。现有工程记录、数据库回归和生产只读留存各自仅支持其覆盖范围；3437岗规模、真实候选人及人类专业签收仍不在本轮通过范围。

主执行者在最终代码运行的[集成日志](../../artifacts/2026-09-11-hr-c-followup/integration-final.log)为 **397 passed，5 skipped / 158.87s，退出码0**；范围为 HR 全部相关测试、文档自检、现行 HR 历史隔离、附件迁移、独立擦除热修复及只读手册回归。本人核对留存 stdout，没有独立复跑该套件。5项跳过仍是显式真实提供方/公开归档或本地 Bundle 等外部样本条件；不据此宣称新一次真实模型、实际资产导入、进程故障或生产验证通过。旧 `integration-a2ae5cd.log` 不是最终代码验证结果。
