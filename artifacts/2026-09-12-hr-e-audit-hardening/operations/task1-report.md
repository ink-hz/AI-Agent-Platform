# Task1 运维闸门与手册修订报告

基线5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a。仅修改runbook、test_hr_cutover_runbook.py，新增独立test_hr_cutover_operations.py；未改102/103/104、产品状态机或其他后端实现，未提交。全部运行使用本地一次性PostgreSQL和自有测试进程；没有生产、模型、浏览器、消息发送或真实Docker/PM2调用。

## 逐项处理

1. **持有排他锁后的慢count**：原30秒语句上限用真实transition重现。测试先进入draining_legacy，临时重命名一次性DB中的原count，再以同owner/maintenance-only grants包装原count结果和pg_sleep(6)；原count实际执行，不改迁移文件。观察maintenance已取得ExclusiveLock且处于PgSleep，随后真实非HR ConversationRepository.append_turn在同一advisory key上等待ShareLock。新手册全部initialize/count/transition使用autocommit=True加显式transaction()，事务内lock_timeout2s、statement_timeout3s。真实QueryCanceled回滚并释放锁，非HR追加成功，phase/epoch/row_version与操作回执不变。记录见最终real-postgres-slow-count.json。
2. **恢复死角**：§6.2删除可执行PM2 restore、旧Worker启动、共享API重启和回legacy路线。当前transition命令也拒绝legacy目标；对应RED显示旧命令进入数据库，GREEN验证在调用前抛ValueError。当前仅承诺保持draining_cloud修复；旧链恢复须另行设计、验证中途/后续失败终点。没有添加状态机边。§5明确共享API初次force-recreate的影响，当前恢复路线不重启共享API。
3. **附件停旧→inspect→100→只启动修复镜像**：新增HR_ATTACHMENT_HOTFIX可执行块。校验批准runbook/migration SHA、镜像ID、compose实际解析镜像和镜像内erasure.py SHA；检查旧容器身份，禁用旧restart策略、stop并inspect。调用既有bootstrap后核验100账本和六列maintenance权限，再只up附件服务且不启动依赖。检查新容器准确镜像；失败尝试停止旧/新准确容器且不写成功回执。Docker不可达/stop失败不能保证停止，手册明确关闭后续步骤并要求独立inspect。真实擦除canary仍是外部上线前置，Running不代表业务通过。
4. **PM2因果与持久身份**：新HR_LEGACY_STOP块读取批准hostname、wrapper/ecosystem SHA、退出前状态和runbookSHA。先持久保存实际状态、其他实例快照、原wrapper/ecosystem字节及identity，再delete。mock状态只有执行delete-one成功才变absent；delete无效果不会凭scenario返回absent。absent且其他实例不变才save；save失败没有成功回执。缺批准、错主机/脚本/手册SHA、错退出前状态或复用证据目录均失败且不删除。restore已延期，所以不存在声称测试通过的restore mock路径。
5. **容器限制与诊断**：手册所有docker run增加cap-drop ALL/no-new-privileges:true，迁移助手段同步root已验证的SIGQUIT及容器限制。§5要求owner system-health的hr_agent.api_ready装配快照、数据库preflight、真实HR授权读取和切换后的实际canary；worker_checked=false和未做实时DB/Worker探测明确保留。自动compose HR探针延期，共享/api/health不算HR可用。
6. **准确证据身份**：最终完整runbook SHA与每个mock回执、实际渲染执行脚本、fence SHA和真实PG回执关联。快照保存完整源码、原始fence字节与相对5978eaa的可校验patch，不依赖/tmp路径或只有哈希。旧operations-final-1在手册中明确为历史中间运行，其30秒与旧恢复假设不能转为本轮结论。

## 所有试跑（命令、cwd、原始输出及exit均在唯一日志）

| 文件 | 结果 | 含义 |
| --- | --- | --- |
| 20260912T054624389387Z-sql-red.log |3failed/5passed，exit1 |30秒与当前恢复路线不符合新契约；真实慢count未被3秒取消 |
| 20260912T054736169544Z-sql-green.log |8passed，exit0 |显式事务/3秒及路线修订后的初次绿色 |
| 20260912T054914878507Z-shell-red.log |2failed，exit1 |尚无唯一的新PM2/附件可执行块 |
| 20260912T055046950504Z-shell-green.log |16passed，exit0 |初版外部工具因果mock及负例 |
| 20260912T055344178869Z-operations-refined.log |27passed，exit0 |扩充批准身份、保存前置和证据复用负例 |
| 20260912T055422513381Z-lint-initial.log |exit1 |移除旧restore测试后3个unused imports |
| 20260912T055506934430Z-lint-fixed.log |ruff0/diff-check0 |移除unused imports |
| 20260912T055530467948Z-deferred-target-red.log |1failed，exit1 |原transition命令仍接受legacy目标进入DB |
| 20260912T055613629183Z-final-operations.log |29passed，exit0 |初次完整回执；随后仅同步root的SIGQUIT/owner-health正文，故该SHA为中间版本 |
| 20260912T055820127971Z-frozen-operations.log |29passed，exit0 |当前准确runbook SHA的最终Task1验证：9真实PG/手册测试+20外部工具mock测试 |
| 20260912T055907547751Z-static-and-snapshot.json |全部exit0 |11个bash fence语法、ruff、diff-check和完整源码快照 |

一次编辑工具调用曾因外层heredoc分隔符与内嵌PY相撞而被shell语法拒绝，未启动测试；改用唯一分隔符后继续，没有覆盖任何测试日志。

## 回执与覆盖范围

最终回执目录：20260912T055820127971Z-receipts，包含20个外部mock场景的原始执行脚本、stdout/stderr、退出码、因果调用序列/状态，以及1份真实PG慢count回执。上一份055613回执保留为中间版本，不覆盖。

源码快照：20260912T055907547751Z-source-snapshot/manifest.json、review.patch和三份完整源码。fence编号以此准确快照为准：

- fence1 HR_ATTACHMENT_HOTFIX和fence11 HR_LEGACY_STOP：实际执行Bash/Python控制逻辑，只有Docker/bootstrap/PM2外部命令被mock。不是实际Docker构建、容器、权限授予或PM2生产验证。
- fence6 INITIALIZE、7 COUNT、8 TRANSITION：内嵌Python执行真实一次性PG SQL/角色/锁/事务；外层Docker容器未运行。
- fence2 compose数组、3/4 preflight Docker、5 HR迁移helper、9共享API/Worker启动、10旧云HR Worker退出：未执行外部操作，仅bash语法检查。其余后端验收由root单独留证，不借用本Task1 mock结论。

数据库3秒timeout不承诺网络/主机挂起或Docker调用的墙钟上限。append观测只覆盖本地一个正常调度的实际等待者，不是生产负载SLA。附件bootstrap外部mock不验证其真实授权生命周期；root迁移helper信号测试为另一证据。PM2批准JSON代表窗口前人工核实材料，mock不证明生产身份、外部watchdog不存在、配置内容适合恢复或真实擦除成功。releaseSha为批准发布标识，实际镜像以imageId+sourceSha核验，不从手工标识推导整镜像业务正确。

## 最终身份

Runbook SHA-256：70174ca7b60dc86ae8b47c5da048aeb7c2890bfbcf71abdb00fa7d59f0d0adfb。

真实PG追加等待/完成耗时：2.9949393337592483秒；同锁阻塞已观察，QueryCanceled后追加完成，phase/epoch/row_version保持['draining_legacy', 2, 2]，新增操作回执0。

## 中间运行的源码身份限制

本任务早期RED和中间GREEN保存了命令、cwd、原始pytest输出及退出码，但没有在各次运行当时保存完整源码字节或可证实身份的源码SHA。因此这些日志不能单独证明当时每个未提交文件的准确内容，不能声称它们都有可校验的精确中间快照。未用事后猜测的重建补齐这项缺失，也不把最终源码套回早期RED。

055613回执绑定该次runbook SHA，但当时未另存完整runbook原字节；不能只凭SHA声称可恢复全部中间正文。当前最终055820运行已同时具有准确SHA回执与055907完整源码/patch/fence快照，后续root冻结提交再绑定该最终来源。此限制不抹去已保存日志，也不将旧/中间测试数量并为最终运行。
