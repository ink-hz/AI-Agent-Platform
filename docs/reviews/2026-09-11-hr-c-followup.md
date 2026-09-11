# C 再评审收尾与 D 准入判断

> 后续状态：用户随后明确要求继续，D 本地实施已启动，见[D实施记录](../superpowers/plans/2026-09-11-hr-d-execution.md)。下文“D暂停”为本次C评审时点结论；生产、真实候选人和长任务配置门槛分别约束相关验收，不再作为全部本地开发的停止条件。原验证与证据保持当时口径。

基线：`47f577f`。本轮补齐评审缺口；未启动D实施、未推送或部署。生产动作仅附件聚合只读普查；模型网关仅一次合成文本计数探针，不是生成或专业验收。

## 1. 附件生产影响：补齐独立普查

06:34 UTC的[捕获](../../artifacts/2026-09-11-hr-c-followup/production-attachment-census.json)直接查attachments：2条均uploading、deleted_at为空，deleted状态/非空删除时间/标记不一致/到期记录均0。附件与队列表均无RLS，队列仍为空，旧领取SQL仍在线。

正常应用代码没有删除erasure_jobs的路径，完成擦除也保留行，因此在这些路径且无外部清理前提下，空队列支持无历史入队迹象；不能据此证明所有历史版本或人工维护未曾清理。当前也没有被标记为已删除的附件，但未审计对象字节。064实际仓库SHA与前轮API回执相同；readonly SQL已包含64/100双回执及独立普查。详见[事故记录](2026-09-11-platform-erasure-incident.md)。

评审中JOIN放大的前提不成立：064和生产均有uploads.attachment_id唯一约束。查询仍改为预聚合uploads，作为防御性表达，不登记虚构的生产错误。一次性数据库测试在队列为空、已有deleted附件时仍能输出独立普查，并匹配064回执；先失败再修复。

## 2. 单次读取不应使新工作无法继续

整批逐条压缩不足以保护超大单条；新修复对read_resource成功回执做动态准入。校验对象是实际完整工具记录、当前目标与最终checkpoint（含本次读取产生的区间/来源变化），按配置tokenizer并加输出预留判断是否能进入一次摘要请求。

校验位于read/entry/checkpoint同一提交事务末尾；过大即整体回滚，外层只记录可重试错误并指引缩小limit，不把过大正文存入历史或登记成已读。独立审查另复现同批第二次1字符读取会让首次临界大读取失配；`21a5a79`因此用最终checkpoint逐条复验同批成功读取。新来源若破坏旧读取的摘要路径，回滚新增读取并提示先处理已有内容，不误导成单纯缩小limit就足够。模型可在同work继续读小范围，没有静默截断或假完整。固定大用户输入和已经存在的不可拆旧历史仍按此前blocked/context_too_large边界处理；不声称所有可能的大目录、输入或工具参数已自动可恢复，也不增加模型窗口、预算或业务硬编码。

## 3. 独立附件修复包

`f9712b5`更新[hotfix补丁](../runbooks/2026-09-11-platform-erasure-hotfix.patch)：FROM领取修复、公共100迁移和独立平台数据库测试一并交付。已在干净master `2bddc77f`实际apply并跑4项回归；不依赖hr_agent。对象删除使用生产writer加替身S3客户端，未冒充在线对象库验证。[完整记录](../../artifacts/2026-09-11-hr-c-followup/platform-erasure-hotfix-validation.md)。

[手册](../runbooks/2026-09-11-platform-erasure-triage.md)给出停止整个附件worker与容器/进程双检查命令、上传处理/解析/保留清扫影响、积压观察与恢复顺序。最长窗口由服务负责人在操作前写明并按既有请求时限约束，未知时限不编造。100已应用但新镜像启动失败须持续停止消费者，绝不能用旧SQL恢复服务。补丁和本地验证完成不等于已发版。

## 4. 计量探针与研究配置前置

Anthropic官方提供`POST /v1/messages/count_tokens`，其返回值仍是估计，可能与创建消息实际输入略有不同；也不包含真实缓存命中结算逻辑。[官方说明](https://platform.claude.com/docs/en/build-with-claude/token-counting)

对HR当前私有profile的同网关、同model `claude-opus-5`、相同鉴权，向配置Messages路径追加`/count_tokens`发送一次公开合成文本，**返回404 / invalid_request_error**。[捕获与复跑脚本](../../artifacts/2026-09-11-hr-c-followup/token-count-probe.json)。不记录端点/凭据，只留端点哈希与profile修订；不跟随重定向、未尝试其他网关、未调用生成接口。此结果只证明该标准路径本次不可用，不证明网关所有可能计数路径都不存在，也不认证底层模型供应商。

计量前置因此仍未关闭：先由网关维护方明确是否透出计数接口；若没有，使用同网关的中英/工具/缓存/长历史合成生成样本定标，独立留验证集和误差边界，零报/缺报继续保守回退。有限样本系数不是可证明的绝对上界，不因拟合良好就撤掉异常防护。300秒/16384研究配置还只是已实跑候选，120秒默认没有C3成功记录；尚未改默认、私有profile或生产配置。

## 5. 证据准确性

run11两项**专业保留**在47f577f的C报告第77行已限定run11；本轮另在[证据修订](2026-09-11-hr-c3-evidence-corrections.md)明确它们与预留token不同，不归给run9。缺失的52 passed绿日志已从本机原件补存，SHA匹配先前记录b21538d2…；不是新生成或伪造历史日志。原C3 97份证据与原manifest继续保留。

## 6. 最终验证与判断

**本轮工程修订可验收，仍不进入D。**

| 验证 | 实际结果与证据 |
| --- | --- |
| 最终集成：`backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py backend/tests/test_agent_brain_hr_history_isolation.py backend/tests/test_conversation_attachment_migration.py backend/tests/test_attachment_erasure_hotfix_database.py backend/tests/test_attachment_erasure_readonly_runbook.py -q -rs` | **397 passed、5 skipped，158.87s**，[最终日志](../../artifacts/2026-09-11-hr-c-followup/integration-final.log) |
| 读取专项：中文/非BMP、最终checkpoint增长、同批先前读取失配 | **4 passed**，另context+repository **40 passed**；独立复审另外复跑两个关键用例 |
| 独立master应用hotfix后的真实PG测试 | **4 passed**，平台独立包记录如上，不与397相加 |
| readonly SQL独立普查回归 | **1 failed→1 passed**，证明队列为空仍能观察已有deleted附件及核验064；计入最终397 |
| 根目录Ruff相关文件 + docs selfcheck | 通过；**55定义、134例、18条件覆盖ID、6正文证据ID** |
| 原始证据 | 原C3的97份及manifest与edf62e0一致；上轮修订20份及manifest与47f577f一致 |

5项skip仍是B真实公开模型、C原始包、C真实模型、两项真实情报包条件；没有新增skip/xfail。早于顺序修复的396项日志另存`integration-a2ae5cd.log`，不当作最终代码的结果。本轮没改前端，未重复组件/浏览器验收；既有styles三个失败的披露继续有效。独立审读报告：[规格符合性与实现质量均通过本轮范围](2026-09-11-hr-c-followup-independent-review.md)。本次不声称新增真实模型研究、候选人或生产发布验收。

| D前置 | 当前判断 |
| --- | --- |
| 本轮读取自锁与证据缺口 | 已闭合；通过事务内真实最终形状和顺序回归，不依赖任意框架常数 |
| 平台附件修复 | 独立补丁、测试、操作手册就绪；生产尚未停Worker、应用100或发布，不能标生产处置完成 |
| 研究profile与计量 | 300秒/16384为候选；标准计数路径本次404，网关能力确认/同网关定标与产品时长成本决定仍未完成 |
| 原C业务门槛 | 真实候选人获准服务与验收、现行D1发布影响及其他原列事项未被本轮替代 |

因此维持D暂停。下一步分别推进附件独立发布与研究配置/计量决策；不能把本轮工程测试通过换成D或生产启用授权。
