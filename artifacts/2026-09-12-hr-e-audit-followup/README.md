# E 审计保留项：新增证据入口

基线：`192a4489cc1a199259b4b10c591b0ee7b23420e7`。工作树：`.worktrees/hr-cloud-loop-e-release`；分支：`feat/hr-cloud-loop-e-review`。完成判断及W映射见[本轮审核报告](../../docs/reviews/2026-09-12-hr-e-audit-followup.md)。本目录只记录2026-09-12新增运行，不回填旧运行日志。

最终关联回归：[27文件，517通过、1条件跳过](final/integration-2.log)，退出码0；准确[命令及运行期间源码核验](final/integration-2-command.json)单列。随后两个测试文件仅清理注释/缩进，AST不变，[定向30项再次通过](final/cosmetic-regression.log)。两组结果重叠，不加总。变更Python的Ruff全规则仍有24条既有诊断；与基线按文件/规则/消息比较无新增诊断，不称整个lint绿色。

## 证据目录

- [fixtures](fixtures/README.md)：原遗漏文件逐项失败、部署迁移夹具修复、D1旧NULL范围、非HR摘要契约。另见[真实091升级时序](fixtures/provenance-upgrade-addendum.md)、[共用夹具使用方](fixtures/transitive-fixture-addendum.md)及[readiness协议](fixtures/readiness-followup.md)。`commands.tsv`留存实际命令和退出码。
- [counts](counts/README.md)：103合法状态的34项特征验证、追加104、真实约束/关联/缺表拒绝与102–104就绪要求。日志文件内含命令、原始输出和退出码。
- `operations`：直接执行手册Python片段的真实维护角色/锁/事务测试、三项failed草稿repository测试、四个纯模拟恢复Bash场景。每份pytest原日志都有同名`-command.json`。
- `final`：首次失败整合、最终关联整合、代码静态检查及历史身份核对。整合结果和定向运行重叠，不能加总为新覆盖数。
- `review`：未编写对应实现的AI所作有界审阅及源码指纹；它们没有重跑测试，不是人类签收或生产验收。

## 失败与边界必须一并读取

首次关联整合`integration-1.log`是486通过、7失败、8错误、1条件跳过。它揭示三份使用方重复迁移和v5声明/v6 scope不匹配，不是绿色结果。所有后续修复均留新日志。104初稿SQL语法错误、helper断言读错脱敏输出、摘要合成能力卡缺失，以及静态检查诊断也完整保留。

`test_actual_process_loop_same_request_and_api_restart`需要自有MetaBot进程夹具；本次未设置，仍为条件跳过。本轮迁移助手使用真实一次性PostgreSQL、子进程和信号，Docker CLI为替身。签名HTTP保留真实身份/授权/nonce/幂等和持久化，执行器响应及停止证明由合成夹具提供。failed草稿验证止于repository/数据库；PM2恢复只作Bash模拟。

未调用真实模型、未访问生产、未运行浏览器或前端测试、未启动真实MetaBot恢复场景。上一轮25项独立审读无原始运行日志，8份前端中间日志缺失，3项styles既有失败仍未修复。当前局部回归不能替代这些证据，也不能替代D专业、候选人许可/authorizer、D7、飞书去向与正式窗口。

## 历史和最终身份

[历史身份核对](final/historical-identity.json)逐文件比较基线下全部267份旧artifact，以及102/103两份迁移；269份均保持原字节。最终整合开始时的backend/deploy/runbook源码身份单独记录，命令回执核对运行期间是否发生变化。最终文件清单与SHA-256由本目录`manifest.json`记录；不把清单本身纳入自我哈希。

可在工作树根目录执行 `python3 artifacts/2026-09-12-hr-e-audit-followup/verify_evidence.py`，核对本轮文件成员/哈希、最终变更源码和269份历史身份。清单只排除自身以及保存此核验输出的`final/manifest-verification.json`，两项排除在清单内显式登记。
