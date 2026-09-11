# E 历史记录勘误（2026-09-11）

原 `artifacts/2026-09-11-hr-e/` 保持原字节，供复现此前评审；本文件是当前解读的勘误，不把今天的修正伪装成当时的记录。原 C/D 证据同样不回填。

1. `integration/legacy/merge-resolution.txt` 中“有 positionId 即保留同岗位历史”的 Resolution note 是**曾采用、随后被 D1 RED 推翻的工作区中间方案**，不是交付方案。“不会重新引入候选人泄漏”撤回。最终 `aa53906` 已按 is_hr_agent 收窄为当前轮输入。RED 早于该合并提交；应称“未提交合并解决方案曾重开 D1”，不能声称已提交生产整合代码先带入该缺陷。
2. `integration/independent-review.md` 的 `5aa83e5` 仅为审读时 checkout HEAD，不是被测树身份。当时有未提交实现和测试；该提交不包含文中全部测试。报告末的六项文件 SHA 是审读工作区快照，9/61 项日志只证明该快照内有界审读，不能宣称在干净 5aa83e5 上通过。后来持久 scope 断言也已在原文单列，不并入早前指纹。
3. E 合并时 D1 文件由 12 减至 6 项，旧附件、显式比较对抗、大历史压缩、非 HR 摘要四类保护未完整保留。当前修订恢复为 16 项，并另补受控异常与 adapter 错误分支；见 `context/review.md`。旧 6 项数量仍是当时事实，不追溯改成 16。
4. E 的 `config.py` 把知识配置的运行底座条件从仅允许旧 HR Web Worker，扩为旧 Worker **或**云端 HR Agent enabled。这是有意的运行时代码行为变化，不是 fixture 修正。旧评审包“修复只改合法 fixture/测试输入”仅适用于文中那两轮具体夹具失败，不能概括整次合并；本次也恢复了 `build_direct` 的受控异常映射。
5. `6874` 是跨两个包的 `intelligence_bundle_jobs` **行数**，不是内部岗位数、去重岗位身份数或当前包数量。“新链 13 项表”改为 13 个查询项、12 张不同表。不能从查询项数推算物理表数。
6. 先前 app 与 maintenance 盘点并非同一镜像/权限快照；089–095 已应用的证据来自两次 app 身份采集，maintenance 的迁移账本不可读，不把它列为第三份验证。最新本轮采集单独绑定 fe10fae 与实际 image，不能混成同一次生产观察。
7. 旧三条 queued 不是经验证的三个未完成用户任务；最新 job_kind/轮次引用聚合见 `inventory/production-job-references.json`。28 条 interrupted 的技术终态处置另列，不据此推定成功、远端已停止或可以自动重放。
