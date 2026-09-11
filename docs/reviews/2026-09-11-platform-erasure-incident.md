# 平台附件擦除领取缺陷独立核查（2026-09-11）

时点说明：开篇为后续生产核查，其后保留独立代理先前的本地核查。主执行者按用户“查生产影响”进行了下列最小只读生产核查。没有停服务、领取/重排任务、对象删除或发布。

## 后续生产只读观察（05:45–05:48 UTC）

- 实际附件Worker镜像为`03e60f83cddcfffe704cce627c1ca62d29da0327`，与当前API的`44de9209b77343facf3117fe3ab4d3450eaf2062`不同；Worker文件中仍是`SELECT (platform_attachments.claim_attachment_erasure_job_v64(%s)).*`。
- 连接生产`agent_platform_control`，使用现有maintenance身份，`transaction_read_only=on`；擦除任务表无RLS/强制RLS，状态聚合结果为空。因此该时点没有可见queued/running/completed任务，不是被RLS过滤后的空表。
- maintenance对100所补六列的SELECT均为false。该身份无权读取迁移回执，错误42501单独记录；另以现有API身份只读查询064/100，确认064已于2026-09-04应用，100没有回执。
- 当前没有查到错配任务或孤立running的生产证据。空表不排除被清理的历史、其他环境或未记录的存储异常；未导出个人正文、密文、对象定位，也未扫描对象字节。preview未查询。
- [最终只读捕获](../../artifacts/2026-09-11-hr-c-review/production-erasure-readonly-final.json)、[API迁移回执](../../artifacts/2026-09-11-hr-c-review/production-migrations-readonly.json)。最初两份捕获的SQL布尔探测漏了schema限定名，误为false；followup已带准确SQL行，最终捕获修正匹配。原记录未删，不以错误布尔值否定实际源代码。

生产影响核查尚不等于修复完成。当前无任务及缺权状态使“立即已在错删”缺乏证据；下次附件服务发布前仍须独立修复，且先停旧Worker再应用100。具体可审补丁和发布顺序见处置手册，生产写操作尚未获本次只读取证授权。

## 补充附件普查与代码保留路径（06:34 UTC）

这次直接查询attachments，不依赖erasure_jobs JOIN：[完整聚合捕获](../../artifacts/2026-09-11-hr-c-followup/production-attachment-census.json)。生产共2条附件，均uploading、deleted_at为空；state=deleted、deleted_at非空、不一致标记、保留期已到期均为0。attachments与erasure_jobs均无RLS。擦除队列仍为空。未读对象定位或字节，也没有领取或修改。

审阅的正常应用路径只新增和更新erasure_jobs，没有删除任务行的生产代码；064的completed更新保留任务行。因此在这些代码正常运行且未发生外部清表/历史版本清理的前提下，空表支持“没有历史入队迹象”，比“当前没有running”强。它不是无法审计的人工维护或所有历史版本从未发生过入队的证明。此次独立附件普查也未发现被标记已删除的记录；仍不声称存储字节已核验。

064回执来源是前轮API脚本 `production_migration_readonly.py`，本轮将仓库064原始文件SHA-256与捕获回执实际比对，相同：[哈希比对](../../artifacts/2026-09-11-hr-c-followup/migration-064-compare.json)。发布用readonly SQL现在同时检查64和100，并加入独立附件普查，避免引用入口与结论来源混淆。

关于uploads JOIN：064明确 `attachment_id uuid not null unique`，生产也返回 `UNIQUE (attachment_id)`；现行schema下不可能一附件多upload，因此此前SQL不会因所指情形放大。仍改为先按attachment聚合uploads，保证查询自身表达清楚的一附件一行，不把防御性改写称为已存在的生产数据缺陷。

## 结论与风险

机制风险为 **高**；本轮生产观察未发现活动任务或实际错删证据，未查历史对象字节。旧 `AttachmentErasureRepository.claim` 使用 `SELECT (volatile_function()).*` 展开返回复合类型；PostgreSQL可能为每个展开字段重复执行函数。它破坏“一次调用只领取一个完整任务”的身份一致性。该缺陷同时影响通用平台附件，不限于 HR；但先前本地核查时没有生产查询，后续生产观察也没有对象字节盘点或历史审计证据，不能据此断言生产曾成功擦除、从未擦除，或已经删错对象。

影响有两种上界：单个可领取任务时，返回首字段的 job id、其余字段为空，随后无法构造任务；多个任务时，一条 SQL 可领取多个任务，返回字段可能来自不同任务。若后续读取与删除得以执行，可能按任务 B 的 attachment id 删除对象，却用任务 A 的 job id提交结果，造成数据库状态与存储字节交叉错配。所有被领取行本身均已进入擦除队列，这缩小了潜在对象集合，但不消除错配、残留或审计错误。

064 迁移只给 maintenance 读取 attachments、derivatives、task_grants、erasure_jobs 等所需权限，没有给 `uploads.write_attempt_id` 与 `upload_write_attempts` 的读取权限。现有 worker 在领取后的同一数据库事务内查询这些列；正常 PostgreSQL 权限下，此处报错并回滚该事务，因此可在对象删除前阻止破坏且通常不留下已提交的 running。但这只是代码、事务和声明权限推导：权限漂移、不同发布版本或不同运行身份仍未知，不能把它写成生产未受影响的事实。

迁移 100 补齐最小列权。若它先应用而旧 worker 尚在运行，原先的权限阻断会消失，旧领取 SQL 的错配路径可能短暂可达。因此处置不能直接运行常规“先迁移、后重建 worker”的发布顺序；必须先暂停附件 worker 并确认停止，再迁移和启动修复镜像。

## 本地复现证据

在临时 PostgreSQL、合成附件与真实 064 函数上直接执行旧 SQL，未连接生产：

- 单任务：返回 `erasure_job_id` 非空、`attachment_id` 为空；数据库内该任务在提交后为 `running`。
- 两任务：返回的 job id 与 attachment id 来自不同队列项；一次语句后两个任务均为 `running`。
- 临时回归命令结果为 `2 passed in 1.54s`。现有正式测试 `test_one_claim_does_not_consume_sibling_erasure_jobs` 验证修复后的 FROM 写法仅领取一个任务；`test_real_maintenance_erasure_after_candidate_confirmation` 验证迁移 100 后对象删除与状态提交；权限测试验证只授予六个必要列。

复现仅证明 SQL 语义与本地状态，不证明生产出现过同样队列形态或对象删除结果。

## 已知与未知

已知：064 的 claim 函数是 volatile、有 `FOR UPDATE SKIP LOCKED LIMIT 1`，每次调用会把一个 queued/partial 行更新为 running；worker 的 claim、定位读取处于同一连接事务，对象删除发生在该事务成功退出之后；记录结果只接收 job id，不接收 attachment id；worker 部署命令是 `python -m app.attachments.worker_runtime all`，处理、retention 与 erasure 共用一个常驻服务。

未知：生产当前镜像是否仍含旧 SQL；100 是否已应用及准确 checksum；maintenance 六列权限是否漂移；缺陷版本运行期间各状态数量、running 年龄与 attempt_count；对象存储实际存在字节是否与数据库状态一致；是否存在进程中断、部分删除或人工操作。现有 healthcheck 只验证数据库/S3/扫描器可达，不验证擦除权限、队列推进或对象与状态一致性。

## 工程处置状态

本地修复 `95f64d9` 将领取改为 `SELECT * FROM function(...)`，保证一次求值；公共迁移 100 只向 production/preview maintenance 角色授予 uploads 两列和 upload_write_attempts 四列 SELECT。没有新增普通 app 的 UPDATE 权限。当前核查没有部署、连接生产、重排任务或删除对象。

## 可重复执行证据补充

平台级永久回归已加入 `backend/tests/test_conversation_attachment_migration.py`，只依赖既有 control PostgreSQL fixture，不引用 HR intake/material fixture。命令 `.venv/bin/python -m pytest -q tests/test_conversation_attachment_migration.py -k 'legacy_composite_expansion'` 结果为 **2 passed, 33 deselected in 1.47s**；分别断言旧 SQL 的单任务空 attachment 返回，以及双任务跨行拼接和一次领取两项。

配套只读 SQL 已通过 `psql -X -v ON_ERROR_STOP=1 -f` 在临时 PostgreSQL 全文执行。100 存在时回执 checksum 与审阅文件一致且六项权限为真；临时撤销回执与六项授权后再次全文执行，回执为空且六项权限为假，随后在 `finally` 恢复 fixture。结果为 **1 passed in 1.45s**。命令、摘要和捕获输出见 `artifacts/2026-09-11-hr-c-review/platform-erasure-evidence.md`。

独立 hotfix 审阅包为 `docs/runbooks/2026-09-11-platform-erasure-hotfix.patch`，相对 master 仅包含 `backend/app/attachments/erasure.py` 与公共迁移 100；未应用到 master、未部署。
