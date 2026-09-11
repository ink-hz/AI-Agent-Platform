# HR C：计量质量汇总与材料 UUID 幂等兼容审读

**结论：限定最终 diff 内未发现剩余阻断。** 初版材料 UUID 规范化遗漏了旧大写请求记录，存在跨附件重用未报冲突的问题；最终兼容查询与新增红绿回归已覆盖该缺口。计量质量汇总只改变来源质量标签的计算，未降低原输入估算下限或改写历史账本数值。

审读者：Codex AI 子代理 `/root/b2b_workbench/review_b2b`。本次对代码与主执行者提供的既有测试日志作独立只读审查；未执行测试、联网或调用模型，未改业务代码、提交或迁移数据。此结论不是人类验收或生产验收。

## 范围与核对方法

任务范围为 `/tmp/hr-c-review-meter-parse.diff` 中 `material_parsing.py`、`repository.py` 的计量相关小节及两个测试文件。对照读取了当前格式化后的相同函数；未将并行代理的 pause_work/context 修改纳入本次结论。

审查固定 diff SHA-256：`070faf2a0471680748ce240419dd5b778a4e4c08cf41ba2b7a89c1a2b518eac3`。该 diff 的版本包含标准36位UUID限定、旧大写请求兼容查询，以及迟到用量回归。当前对应函数的排版变化未改变以下语义。

## 计量质量汇总

`_charged_usage_quality` 仅按同一 work 的 `charged_tokens > 0` attempt 汇总来源质量：没有扣记记录时返回 reported；只有一种来源时返回该来源；估算与实报并存时返回 mixed。prepared 初始为零扣记，不会把已经全实报的工作误标为 mixed。

发送路径先将该 attempt 更新为 sending 并记入原预留，再汇总；结算路径先更新该 attempt 的 charged_tokens 与 usage_quality，再汇总。两者与预算更新位于同一事务，相关入口通过 work 行锁串行化；汇总不会读到本次更新前的 attempt 质量。

中断不撤销已经发送的预留，因此 interrupted 的正扣记估算继续参与汇总。另一次实报结算后，两者正确为 mixed；前次迟到 usage 若达到原输入下限，原 attempt 可转为 reported，此时全工作质量可恢复 reported，不永久卡在 mixed。迟到结算的鉴权、prepared 拒绝及同观察内容幂等边界没有改变。

数值规则仍为 `actual=max(reported_input+reported_output, frozen_input_estimate_floor)`；发出时预留与结算时差额调整保持原样。零或偏低实报不能退到下限以下。迟到实报会按既有规则结清多预留部分，这不是本次放松计量下限。没有新增批量历史更新或重新计算旧 evidence 的操作；旧运行的 mixed 记录继续保留其原时点含义。

## 材料请求身份与历史兼容

最终实现只将完整匹配 `8-4-4-4-12` ASCII 十六进制格式的36位带连字符 UUID 转为小写。这个限定先于锁与查询：相同 owner 下大小写变体共用 canonical advisory transaction lock。32位无连字符、花括号/URN等其他字串没有被宣称为同一 UUID 身份，继续按原始不透明字符串精确匹配和区分大小写。

在 canonical 锁内，标准 UUID 使用 `lower(request_key)=canonical_key` 检索全部等价旧行；不透明键仍用精确等号。查询保留 owner 限制。任意旧行的 attachment_id 不同都会先报409；获取当前解析任务后，任意旧行 parse_id 不同也报409。不能只因第一个旧行匹配就掩盖另一个冲突行。

等价旧行一致时直接返回已有 receipt，不改原 request_key、不插入替代小写请求，不重置解析任务或历史回执。新请求仍受既有 `(owner, attachment, source SHA, parser release)` 任务唯一性约束。源码未新增历史数据重写；测试中的 UPDATE 是用于构造旧版大写持久状态的隔离夹具，不是产品迁移路径。

本次审读发现的初版缺口已关闭：旧大写键绑定附件A，规范化后对附件B请求必须409；对A重试复用原 receipt，数据库继续仅保留原大写键。32hex不透明键的大小写继续是两个请求身份，但同一附件的解析任务仍去重、只执行一次。

## 已有红绿证据与实际覆盖

本审读没有重跑，以下是读取现有日志后确认的结果，不能把不同阶段计数相加成最终总覆盖数：

| 阶段 | 日志结果 | 可支持的结论 |
| --- | --- | --- |
| 初始红测试 `/tmp/hr-c-review-red.log` | 4 failed、1 passed、16 deselected，2.51s | 三个计量参数用例原发送质量均错误为 mixed；UUID大小写原产生两条请求 |
| 第一版绿测试 `/tmp/hr-c-review-meter-parse.log` | 52 passed，63.10s | 第一版相关回归通过；尚未覆盖后来发现的旧大写记录与32hex兼容问题 |
| 兼容红测试 `/tmp/hr-c-parse-legacy-red.log` | 2 failed、11 deselected，1.57s | 旧大写键跨附件未报409；原32hex大小写身份被过度合并 |
| 最终相关绿测试 `/tmp/hr-c-review-meter-parse-final.log` | 38 passed，8.06s | 新增旧大写及不透明键回归通过；结合最终 diff 的计量用例与兼容分支审读，未发现剩余阻断 |

计量新增用例验证实报0/350/800、输入下限700、发送时 estimated；中断预留与实报重试混合；迟到900加另次800后总扣记1700、调用仍为2且质量恢复 reported。材料新增用例验证修复后大小写共用一个请求、跨附件冲突、单次解析；旧大写回执保持原键复用；32hex大小写两个请求但解析任务仅一次。

prepared 排除、全部等价旧行冲突与 canonical 锁行为是本次源码路径审查的结论；新增片段未分别提供这些组合的独立并发故障测试，故不把它们声称为已执行的专门进程或并发验收。最终全量集成由主执行者另行记录；本报告不提前声称该轮结果。页面、生产和真实模型验收不属于这两个修复的本次核查。

日志 SHA-256（用于主执行者导出时固定本次审查输入，不改写原日志）：

- 初始红：`57f1f81bfa7b68cf0095c2a977bd695d5e193c05c082b96d91230329dd1410f6`
- 第一版绿：`b21538d225ceb226f55ffbbf9fe9fcba85597bab9598a54167b19dff392327a0`
- 兼容红：`8f0c88a419b8053f4da6c763c6e41a6087d2255b78de8645e0b912c7dcf20077`
- 最终绿：`7dfca4f0b599dc16545f0a063b2a6ebb084efa06d1b4907c4b2b5177073f73cc`
