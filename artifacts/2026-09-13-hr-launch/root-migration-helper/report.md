# 宿主生产根迁移能力交接

在现有hr_agent_migrate.py Supervisor复用同一个validate/create/grant/start/cleanup流程，新增显式 `--migration-set root --environment production`。默认 `hr/all` 保持两库；HR也可显式production。root不接受省略production、all或preview，不bootstrap凭证、不创建数据库、不修改preview。production模式不读取preview秘密或数据库，不GRANT preview；仍只读观察集群两种migrator的会话/成员资格，保持既有独占前置。

根模式的边界是本次pre-HR维护：完整基线为根001–088加hr_web089–095。读取并逐条比较这95个已入库SHA，且所有已入库版本都须有允许集合内的精确源SHA。root100/102–105已经入库时允许精确重跑；HR_agent096–099/101或未知版本存在时拒绝。hr_web只用于95基线字节核对，不执行。真正容器挂载/执行目录是根目录，app.control_plane.migrate只迭代直接子文件；根模式不运行hr_agent子目录。发布目录必须含100/102–105完整目标，不能缺105却成功。

before/after ledger、根/基线文件SHA、helper SHA、image、requested environment、migration_set、job-kind脚本SHA进入0600 receipt。after要求所有应有版本已记录且SHA精确，源文件变化不报成功。先create具名只读/cap-drop/no-new-privileges容器，GRANT前写grant_pending；墙钟、INT/TERM/HUP/QUIT、stop失败后kill、停止确认、revoke及会话/成员归零复用既有路径。失败不会根据原始子进程输出构造错误文案。

## job-kind兼容与范围

原preflight是042分类器，只允许3旧kind；直接对本次95数据库调用会拒绝合法worker_direct_v5。新增宿主已review companion脚本的 `--baseline95` 只读分支，Supervisor在完整95 checksum校验后调用：

- 验证091的实际、已validated CHECK精确定义，四kind限定legacy_brain/direct_agent/metabot_local/worker_direct_v5；CHECK缺失或漂移拒绝。
- v5必须有job→binding→attempt→turn精确链，transport_run/job.run一致、conversation一致、executor/turn owner为worker_direct，job agent为hr-bot。
- 不以queued认定活跃或失败，不要求terminal，不取消或更新任何job。真正排空仍由104计数和切换条件承担，本预检不能冒充排空。
- 原两参数preflight默认分类逻辑不变。伴随脚本复用helper的HR_MIGRATION_DOCKER测试边界，默认仍/usr/bin/docker；SQL只读、5秒statement_timeout/2秒lock_timeout，host外层command_timeout受限。宿主只接受固定classified回执，潜在stderr业务行不进入用户输出/receipt。

该现代CHECK期望来自不可变091源，不是从线上枚举看到什么就允许什么。95文件另逐条与离线65e7fbd Git对象对比，95项SHA一致，见baseline95-offline-comparison.json；这是离线源码核对，不是新增生产DB审计。

## 测试与证据

- red-1：初始10项新行为失败；原helper/新测试/source快照与命令保留。
- green-attempt-1.log：首10项17.57s通过，只留原日志，未声称该中间状态具备独立完整源码快照；随后快照运行取代其作为交付依据。
- focused-2：默认双库及新生产模式/四信号/stop-kill/timeout等37项76.72s通过，独立源码快照。
- red-modern-kind-1与green-modern-kind-1/2最初混入测试子进程PYTHONPATH缺失及psql布尔输出格式失配，不能单用它们宣称kind语义RED。
- red-modern-kind-corrected-fixture-1：修正测试边界后，用**已保存原helper/script准确字节**重新跑，2failed/1passed（合法三条v5被旧分类器拒绝、缺失CHECK未拒绝）。source-override.json给出准确环境覆盖与SHA，不修改旧源码快照。
- green-modern-kind-3：3项通过。真实signed handoff/result及fenced projection产生3个completed turn/attempt，job仍queued；没有UPDATE伪造成功。Docker CLI为边界double，SQL/身份关联在真实一次性PG中。
- red-incomplete-target-1：95起点缺105时旧实现错误成功，新增用例失败；修复为GRANT/create前拒绝。该轮期间另追加未参与本次所选用例的现代lineage测试，command记录可能报告source_unchanged=false；所选测试/helper/script未改变，不称整个五文件集合冻结。
- final-focused-1：**45 passed，90.75s**，exit0且五个源文件运行前后SHA一致；完整命令在command.json，包含真实95起点升级/精确重跑、错误SHA/缺95/未知/已HR拒绝、生产专用、四信号、timeout无法确认不能成功、现代v5孤儿及transport/executor/conversation异常、原双库和bootstrap默认分支。
- lint-final-1与shell-final-1：ruff及bash -n exit0。所有最终源SHA在sources-final-sha256.json，最终完整源在final-focused-1/source/，不依赖hash恢复文件。

未运行模型、浏览器或生产操作；未修改HR角色/runtime、D证据、已经staged的ce6c0f3 release。测试Docker边界double不证明真实daemon/容器/网络/宿主掉电恢复；SIGKILL/host loss仍需外部对账，沿用既有限制。

## 后续操作方使用边界（本任务未执行）

宿主维护目录应同时放入并固定hash的hr_agent_migrate.py和preflight-execution-job-kind.sh，不把新helper写进已staged不可变release。以现有ce6c0f3 release作为参数读取已固定迁移文件，并固定image digest、生产secret目录、PG容器和receipt目录。先root/production完成及核对receipt，再按已授权步骤以hr/production执行HR子目录；HR production gate仍在production库查100/102–105精确SHA。两次都由同一Supervisor建立/清理临时owner权限。

root模式成功输出沿用既有HR_AGENT_MIGRATIONS_OK前缀，具体范围以receipt的migration_set=root/requested_environment=production和environments=1为准；默认双库原输出不变。此模式是专用pre-HR维护工具，不是通用旧库初始化/修复器，不替代上线窗口排空和业务验收。
