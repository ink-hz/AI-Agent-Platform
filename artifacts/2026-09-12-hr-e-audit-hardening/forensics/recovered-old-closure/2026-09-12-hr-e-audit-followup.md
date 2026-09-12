# HR E 审计保留项修订

日期：2026-09-12。复审基线 `192a448`，工作树 `.worktrees/hr-cloud-loop-e-release`，分支 `feat/hr-cloud-loop-e-review`。本次接受“工程通过有保留、上线前置不满足”的审计结论，处理支撑测试遗漏、计数与切换手册、证据表述；未部署、推送、访问生产或调用模型。

## 1. 上轮结论的修正

610/872/82均是各自实际运行的数字，但覆盖范围漏掉了附件绑定、direct摘要、旧Worker进度和知识注入相关文件，不能据此说W2/W7/W8/W9整项本地通过。新增回归不补算进旧数字。原handoff独立审读报告中的25 passed没有留存原始运行日志；本次重新执行相关测试属于新证据，不能恢复旧日志或认证旧运行。

PM2相关材料仅来自本机 `Orbbec-Agent-Team` 的入库配置和脚本，原记录明确 `production_identity_verified: false`。现行手册及评审已改为“已读取仓库配置”，不再称内网实际退出能力已核实。旧审计文件保留原字节，本文件纠正其被过度引用的含义。

旧8份前端中间日志仍然缺失，3项styles既有失败未修复；本次没有修改前端或重跑前端/构建，也没有浏览器验收。D的三项专业缺陷、真实候选人处理许可与authorizer、D7配置/计量、飞书去向及正式窗口仍未完成。

## 2. 实现与测试调整

| 项目 | 当前修订 | 证据与限制 |
| --- | --- | --- |
| 遗漏夹具 | 附件绑定及摘要测试改用已有完整部署迁移helper；direct Worker移除pending片段装配/清理，progress不再重复应用已部署恢复SQL | [夹具报告](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/README.md)保留每个文件原始失败、契约失败与最终通过；不绕过record_turn_scope_v6 |
| 迁移时序 | 另设一次性数据库按真实089→090→091顺序建旧轮次，091前确认归属列不存在，091后验证legacy回填、旧mission可领取及Worker拒绝接管 | [时序补充](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/provenance-upgrade-addendum.md)；明确为历史数据夹具，不声称当前HR intake可在094前运行 |
| 摘要测试契约 | 原HR direct摘要测试与D1的当前轮隔离冲突；改为非HR direct及明确的合成能力卡，保留原摘要阶段断言 | 明确改变了测试对象，不声称只是补迁移；不是FAE实际产品/生产验收 |
| 旧NULL上下文 | 持久旧HR轮次的hr_input_context为NULL，分别验证用户/助手历史和混合摘要不进入下一轮；build、build_direct及压缩候选均检查 | D1由16增为17项；临时模块副本收窄为is_hr_v6后历史/摘要断言失败，产品源码不改。短夹具未独立触发压缩阈值，不能宣称NULL旧数据的压缩闸门已单独突变验证 |
| 不可达计数分支 | 追加104重定义管理count函数，删除被028 CHECK排除的interrupted缺terminal_at分支；102/103原字节保留 | 这是合法数据上的等价清理，不伪造运行行为RED；[计数报告](../../artifacts/2026-09-12-hr-e-audit-followup/counts/README.md)列出真正保护及失败试跑 |
| 计数支撑 | 补真实约束、v5准确关联不匹配、不可变归属保护和13张依赖表缺失的fail-closed验证 | 不停用约束造假数据；不可变字段由数据库拒绝证明，不能冒充count分支逐条业务执行。生产仍只采过部分关联事实，未实跑当前函数 |
| 新链就绪 | runtime schema readiness新增102/103/104准确身份要求；preflight及迁移助手要求104存在且账本checksum一致 | 这是实际启动行为收紧：启用的新HR API/Worker不能在缺这些迁移时就绪；旧lane缺singleton兼容逻辑未变 |
| 共用夹具的使用方 | 首次关联整合发现三文件重复恢复迁移与readiness协议错配；移除重复迁移，让签名观测与已持久v6 scope一致，补v5-only不可放行v6轮次的反例。材料用例在真实intake前准备就绪附件，使scope及绑定原子冻结 | [使用方修订](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/transitive-fixture-addendum.md)、[readiness修订](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/readiness-followup.md)；原传输、grant及租约断言保留 |
| 切换命令 | 初始化、只读计数及transition使用2秒lock_timeout、30秒statement_timeout；打印成功回执，失败回滚 | 本地真实maintenance角色和双连接锁竞争；验证2秒锁超时及实际count；30秒只验证设置，没有额外等待30秒做取消实验 |
| failed草稿 | 进入drain前可重试；draining_legacy可dismiss或明确保留只读；cloud后保持可读但拒绝旧写入 | 既有实现可放弃，不是“完全没有出路”；本轮3项真实app角色repository/数据库验证，不是用户HTTP或解析质量验收 |
| 回滚条件 | 默认留在draining_cloud修复；显式恢复旧配置、实例、readiness和唯一归属后才允许legacy受理 | 手册补单实例恢复及同伴检查，4个纯模拟场景不代表真实主机恢复；不能靠phase变更重建已删除的PM2实例 |

## 3. 测试证据如何读取

新增证据独立放在 [本轮目录](../../artifacts/2026-09-12-hr-e-audit-followup/)。每轮命令与原始输出保留；计数初次104生成时的SQL语法错误、两个helper测试误读脱敏stderr的断言错误、摘要测试契约调整过程都没有删除。它们分别标为装配/测试错误，不冒充业务失败的RED。

夹具定向结果：附件28、摘要5、direct Worker30、progress11、知识2、D1 17均通过。计数34项先在原103上通过，证明不可达分支清理前已有相同合法状态语义；新增就绪要求有明确RED。operations早期12项通过不包含后来补的初始化回执测试。

首次关联整合是486通过、7失败、8错误、1条件跳过，见[原始命令](../../artifacts/2026-09-12-hr-e-audit-followup/final/integration-1-command.json)及[失败日志](../../artifacts/2026-09-12-hr-e-audit-followup/final/integration-1.log)。其中3份测试重复应用恢复迁移，6项readiness用例的签名v5声明不满足已持久v6 scope。修正这些夹具并新增协议不匹配反例，没有改变产品派发或领取行为。单独[派发运行](../../artifacts/2026-09-12-hr-e-audit-followup/final/dispatch-final.log)14项通过，它们随后仍纳入最终集合，重叠结果不加总。

最终关联回归为**27个文件、517通过、1条件跳过、10条cookie弃用警告，退出码0**，耗时365.41秒。准确文件集合、时点、边界见[最终命令回执](../../artifacts/2026-09-12-hr-e-audit-followup/final/integration-2-command.json)，原始输出见[最终日志](../../artifacts/2026-09-12-hr-e-audit-followup/final/integration-2.log)。运行期间1137份backend/deploy/runbook源码指纹没有变化；这是本轮相关范围的回归，不是全部后端套件或整条W的业务签收。

整合后仅清理两个测试文件的冗余lint注释和续行缩进，[AST核对](../../artifacts/2026-09-12-hr-e-audit-followup/final/cosmetic-delta.json)确认语义结构不变，仍将这两个文件[重跑30项通过](../../artifacts/2026-09-12-hr-e-audit-followup/final/cosmetic-regression.log)，不加总到517。变更Python文件的Ruff全规则检查仍有24条既有诊断，退出码1；[逐文件/规则/消息对比基线](../../artifacts/2026-09-12-hr-e-audit-followup/final/lint-final-2-comparison.json)无新增诊断，不能写为全部lint通过。

测试类型分开解释：签名handoff HTTP保留真实签名/nonce/权限/持久化，执行器结果及stop proof是合成夹具；附件用户HTTP测试使用真实路由和数据库但测试登录身份；迁移助手使用真实数据库、子进程与信号，Docker CLI被替换；失败草稿测试是repository层；停用/恢复Bash场景完全模拟；本轮没有真实模型、浏览器或生产验收。`test_actual_process_loop_same_request_and_api_restart`因未设置自有MetaBot进程夹具而条件跳过，本次未启动该进程场景。

## 4. W1–W12当前映射

| W | 本次有证据的范围 | 仍不能据此判定通过的范围 |
| --- | --- | --- |
| W1 | 沿用上传/材料/成果已有工程实现，本次未扩展 | 未新增模型、输入交互或专业验收 |
| W2 | [D1定向17项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-agent-brain-hr-history-isolation.log)含NULL旧数据；[附件28项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-conversation-attachment-binding.log)、[direct30项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-hr-direct-worker.log)及[摘要5项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-agent-brain-conversation-summary.log)补回归 | 不等于完整附件正文、全部新链隔离或生产验收；保守HR策略仍降低旧连续性 |
| W3 | 读取兼容与已承接的fe10fae研究阅读未改 | 本次未新增跨视图业务/候选人专业通过 |
| W4 | 无新模型试验；保留未知/未做混淆、前后矛盾、记录范围当全场三项问题 | 专业验收仍未通过 |
| W5 | ready既有写闸门；failed在drain可放弃或保留只读，重试仍为新受理 | 执行计数不检查人工处置清单；真实解析质量与候选人授权装配未完成 |
| W6 | 旧草稿confirm/dismiss闸门与并发回归保留 | 不把旧草稿操作等同于新标准确认的完整专业验收 |
| W7 | [知识冻结注入和旧轮次不注入两项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-hr-knowledge-context.log)恢复可运行；[用户知识HTTP](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/transitive-fixture-addendum.md)恢复；现行情报精确引用机制未改 | 知识测试只属支撑证据，不能替代B1/B2整条情报场景 |
| W8 | [附件文件28项](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-conversation-attachment-binding.log)包括删除/取消与绑定竞争；100独立发布顺序保留 | 不能从两项竞态推定撤权/历史摘要/下载全部通过；生产擦除仍未验 |
| W9 | [派发14项](../../artifacts/2026-09-12-hr-e-audit-followup/final/dispatch-final.log)含handoff与双向锁等待；[count约束/lineage/缺表拒绝](../../artifacts/2026-09-12-hr-e-audit-followup/counts/README.md)；[最终关联回归](../../artifacts/2026-09-12-hr-e-audit-followup/final/integration-2.log)包含手册锁超时与幂等回执 | 生产准确计数、全部实际lineage及旧执行器停止未验；本轮未重跑真实Worker故障全场景 |
| W10 | 无模型或个人材料发送；权限及去敏边界未放宽 | 真实个人数据处理许可、生产装配仍缺 |
| W11 | 无预算/计量变更或新专业成功样例 | D7研究300秒/16384及计量方案未决定 |
| W12 | [知识冻结注入、重试不重建及旧轮次不注入](../../artifacts/2026-09-12-hr-e-audit-followup/fixtures/green-hr-knowledge-context.log)测试恢复；规格自检仍单列 | 不证明方法使用质量，也不替代全部M1/M2丢失恢复场景 |

## 5. 正式上线仍需满足的条件

明确业务范围及专业签收、真实候选人处理许可/authorizer、D7与飞书去向；固定窗口及执行人，重新核实生产HEAD、实际镜像和配置。按手册完成附件100独立热修复及相关迁移，验证104后的准确计数和实际执行器停止；ready/failed逐项登记处置，不能只看零占用。实际容器、公开canary和最后页面验收仍待完成。旧执行器恢复需按新增显式流程另留真实证据，当前不能称已验证安全回滚。

## 6. 独立复审与证据身份

[计数与运维审阅](../../artifacts/2026-09-12-hr-e-audit-followup/review/counts-operations-independent-20260912T013115391051Z/review.md)发现初始化没有打印回执，已补打印和幂等回执测试，[追加审阅](../../artifacts/2026-09-12-hr-e-audit-followup/review/counts-operations-independent-20260912T013115391051Z/receipt-followup-review.md)确认该问题关闭。[初次夹具审阅](../../artifacts/2026-09-12-hr-e-audit-followup/review/fixture-review.md)要求保留迁移升级时序，已另补真实091升级测试。[后续夹具、readiness与文档审阅](../../artifacts/2026-09-12-hr-e-audit-followup/review/fixture-readiness-docs-independent-20260912T014230390760Z/review.md)未发现断言削弱，要求明确首轮整合失败并为W行补直接证据链接，已在上文落实。各审阅者没有编写所审实现，也没有重跑测试；报告保留其检查时的源码指纹，不冒充最终提交的人类签收。

[历史身份记录](../../artifacts/2026-09-12-hr-e-audit-followup/final/historical-identity.json)核对基线267份旧artifact和102/103两份迁移，全部原字节保留。本轮证据清单、最终源码指纹及可重复核验入口集中在[新增证据目录](../../artifacts/2026-09-12-hr-e-audit-followup/README.md)。

当前两个判断：**本轮工程修订完成，可交独立会话复审；正式上线前置仍不满足。**专业质量、真实进程及生产验收的保留项见第4、5节，不能用517的测试数量覆盖。
