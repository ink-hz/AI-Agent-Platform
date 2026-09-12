# Task 2：排空计数与 104 契约修订

范围：隔离工作树，基线 `192a448`。只使用一次性本地 PostgreSQL、合成数据和本地签名 HTTP；没有访问生产、调用模型、浏览器验收或提交代码。迁移助手测试使用真实 PostgreSQL、受控 Docker CLI 替身及本地进程/信号；不代表真实 Docker 或生产部署验收。

## 变更与结论

- 102/103 字节与基线相同。追加 104 重新定义原管理函数；函数体唯一改动是删除 `status='interrupted' AND terminal_at IS NULL`。028 的真实 CHECK 已要求所有终态具有 terminal_at；删除分支不改变合法数据库状态下的计数行为，因此没有伪造“计数修复前 RED”。
- 计数的实际保护仍为未确认 stop、关联活跃 Turn/Attempt，以及准确且终态的 v5 lineage。queued v5 传输记录本身不代表工作仍活跃，interrupted 也不代表业务成功或进程停止。
- 维护角色可以计数/切换，应用角色不能调用管理函数；CREATE OR REPLACE 保留管理函数 owner/grants。
- preflight 与迁移助手新增强制 104 文件指纹/账本校验。运行时 `check_schema_ready` 原只检查96–101，现新增102/103/104准确指纹。缺失或不匹配时，启用的新云端 HR API 不装配可用仓库，Worker拒绝启动。该检查只用于 opt-in 云端 HR 服务/Worker；未修改旧链缺 singleton 的 legacy 兼容逻辑。
- 本地验证准确查询，不升级生产结论。生产旧只读聚合只观察七项 lineage 条件中的四项；本轮没有运行生产准确计数或生产104，仍必须在授权上线窗口核验。

## 覆盖边界

`test_hr_cutover_count_review.py` 在原103上34 passed；104稳定版本运行中该文件也全部通过：

- 四类 job_kind 的 interrupted 缺 terminal_at 均被真实028 CHECK拒绝，未删除/停用约束。
- 既有签名受理、执行结果与 fenced projection 产生完成记录后，分别注入 transport_run_id 不匹配、executor_kind 不匹配，以及 Attempt转接到另一真实完成 Turn造成conversation lineage不匹配；准确计数增加1且实际transition拒绝。
- 对 Turn execution_owner、绑定conversation_id/job_id的改写分别验证真实不可变触发器拒绝。这里证明不可变约束，不将“禁用触发器后造错行”称作查询的独立业务验收。
- 既有 orphan、非终态Attempt、非终态Turn仍阻止切换；三个完成v5 envelope保留queued但不阻止切换；28条历史interrupted夹具不被改写为成功。
- 未确认停止及关联活跃Turn/Attempt保持阻塞。
- 分别重命名13张依赖表（含由异常分支捕获的conversation_turns），真实count和transition均fail closed，阶段保持draining_legacy。保留所有数据和约束并回滚重命名。
- preflight对103/104缺失及错误账本指纹拒绝；runtime schema对102/103/104两类错误拒绝；迁移助手对104两类错误拒绝、没有启动迁移容器流程、收据确认权限清理。助手stderr维持脱敏，细分拒绝原因在收据中验证。

## 全部试跑记录

每份日志包含cwd、实际命令、原始pytest输出和退出码；日期时间文件名唯一，没有覆盖旧日志。

| 日志后缀 | 结果 | 解释 |
| --- | --- | --- |
| readiness-red | 6 failed / 77 deselected，exit1 | 行为 RED：runtime原来接受缺失/错误102–104。该命令的-k同时筛掉其他文件，不冒充其已执行。 |
| count-characterization | 34 passed，exit0 | 原103的新旧计数用例；语义等价澄清的基线，不是RED。 |
| readiness-preflight-helper-red | 5 failed / 2 passed / 23 deselected，exit1 | preflight/helper尚未要求104。 |
| focused-green | 6 failed / 77 passed / 34 errors，exit1 | 初次104生成误截取注释，造成SQL语法错误；运行过程中纠正SQL和指纹，因此该混合运行不可作最终通过依据。另有两项新测试错误期待stderr包含内部失败原因。 |
| corrected-green | 115 passed / 2 failed，exit1 | 稳定104下其余用例通过；仅两个新helper断言应读取收据而非脱敏stderr。 |
| helper-receipt-corrected | 16 passed，exit0 | 修正测试读取收据后，完整迁移助手文件通过；无放宽实现、授权或清理契约。 |

不得把上述结果改写成“单次117全部通过”；整合绿色结果由root另留证。SQL生成错误和测试断言错误均保留原日志，不是业务缺陷RED。

## 身份与交付

`*-fingerprint-verification.json` 保存所有本任务源码/测试指纹，验证102/103与基线原字节相同、104函数体仅删除不可达分支，以及所分配文件的 `git diff --check` exit0。

104 SHA-256：`cb25b4f81b01bfd7ee3ea3056604b32794c3925ff4c336254d238456fa7a4a8d`。

两份HR根文档、runbook、最终评审和合并提交由root统一处理。准确生产计数、旧执行器实际停用、D专业/真实候选人质量、生产部署身份仍未在本任务验收。
