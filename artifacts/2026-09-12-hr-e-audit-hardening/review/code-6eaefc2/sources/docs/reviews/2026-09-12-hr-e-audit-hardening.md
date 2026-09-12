# E 第二轮审计修订交接

基线为`5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a`，工作树`.worktrees/hr-cloud-loop-e-release`、分支`feat/hr-cloud-loop-e-review`。本轮只修本地工程与证据；没有生产访问、发布、模型、浏览器或业务消息操作。上一轮517通过/1条件跳过属于上一轮准确范围，不包含当时遗漏且仍失败的`test_agent_brain_search_recovery.py`。

## 1. 按审计优先级逐项处理

| 意见 | 本轮处理 | 证据及未关闭范围 |
| --- | --- | --- |
| 切换已持锁时仍可阻塞30秒 | 手册initialize/count/transition采用显式transaction，2秒lock_timeout、3秒statement_timeout；真实transition调用原count，再注入可取消延迟 | 观察同一key的maintenance ExclusiveLock与非HR追加ShareLock等待；QueryCanceled回滚，追加约2.995秒成功，phase/epoch/回执不变。[运维报告](../../artifacts/2026-09-12-hr-e-audit-hardening/operations/task1-report.md)。不承诺网络、主机挂起或生产负载SLA；生产准确count与负载仍未测 |
| 恢复legacy后失败落入无出口的draining_legacy | §6.2移除旧Worker/PM2恢复、共享API重启和回legacy步骤；当前transition命令拒绝legacy目标 | 当前唯一承诺是保持draining_cloud修复。旧链恢复延期，须先设计和验证失败返回路径；没有增加102状态机边，也不称已经支持安全回滚 |
| 第四个红文件遗漏、resume闸门未验 | 先复现search recovery三失败，仅替换完整迁移fixture；保留原三个测试断言，新增三阶段拒绝及数据库无副作用验证 | [原三失败](../../artifacts/2026-09-12-hr-e-audit-hardening/runs/search-baseline/output.log)、[修订后六通过](../../artifacts/2026-09-12-hr-e-audit-hardening/runs/search-green/output.log)。resume创建新轮次，是新受理，draining_legacy也拒绝；不是旧执行continuation |
| 附件发布顺序只有正文、mock缺手册SHA | 新增可执行HR_ATTACHMENT_HOTFIX块，批准身份→镜像/源文件核对→停旧/inspect→100账本及最小权限核验→仅启动准确新镜像；失败关闭后续步骤 | 因果mock覆盖停失败/停无效/迁移失败/账本失败/源文件及镜像不符；20个shell场景的回执绑定完整runbook、具体fence和实际执行脚本SHA。真实Docker、擦除和生产验证仍未执行 |
| 审阅正文与cosmetic前字节未保存 | 保存两份旧cosmetic源码与旧closure正文的精确重建、两份仍存活临时patch；验证原记录SHA | [法证报告](../../artifacts/2026-09-12-hr-e-audit-hardening/forensics/report.md)区分复制与重建，未假称当时原件保存。`aeff5307…`正文仍不可得，旧审阅只能作为该未知快照的报告，不能绑定任一当前提交。新正文先提交冻结，再审准确commit，不再修改正文加入其自身结案段落 |

## 2. 其余意见的完整登记

| 意见 | 当前结果或延期原因 |
| --- | --- |
| /v5/recovery仍可续签授权 | 已确认不是纯只读：v6/v7分支会刷新businessToolGrant。非停止请求现在先取共享闸门，再取领域行锁，按legacy continuation检查；错误链既不返回已有授权也不新发授权，不改payload/grant/poll时间。停止请求及原身份观察/结果回执保留。真实签名HTTP、v6授权、同锁等待重读与数据库故障回归已补；v7未独立重跑专业流程 |
| SIGQUIT没有清理 | 加入原INT/TERM/HUP处理集合；新增真实SIGQUIT先复现退出-3，再验证131退出、停止自有模拟容器、membership归零及cleanup_verified。Docker CLI仍为替身；SIGKILL、宿主机失效和长期daemon不可达仍需人工恢复 |
| 迁移/运维容器缺加固参数 | create/run增加`--cap-drop=ALL`和`--security-opt=no-new-privileges:true`；测试读取实际传入模拟Docker的创建参数，不仅搜索源码。未运行真实容器验证 |
| preflight重算磁盘“期望值” | 逐迁移期望值改用评审常量；账本match与镜像image_match分开报告。修改102/103/104镜像文件且是否同时篡改账本共六个负例；镜像缺失也拒绝。不能再以被改文件自身作为正确身份 |
| API可启动而HR503、编排无感知 | owner审计健康详情新增`dependencies.services.hr_agent.api_ready`装配状态及`worker_checked=false`；不改变公共liveness。自动compose HR探针仍延期：需独立HR探针、权限和失败处置设计，不能让HR停用错误地触发共享API/其他Bot重启。上线必须核对数据库preflight、实际owner装配状态、真实HR授权读取及canary；未宣称自动发布闸门已经完整 |
| CandidateUnavailable吞数据库故障 | 外部503契约保持；内部可见日志及结构化字段区分cutover_paused/database/data_contract，仅输出固定类别和SQLSTATE，不输出异常正文、SQL或traceback。真实暂停与缺表两类回归验证可区分 |
| count positional参数、SET LOCAL可能无效 | 执行片段采用显式事务；count列名与输出位置仍由已固定返回签名对应，签名变更属于需重审的契约变更。没有把autocommit下的SET LOCAL当作有效保护 |
| PM2 mock success无因果 | 状态必须由实际delete操作改变；delete无效、错状态、同伴改变、save失败均不能写成功回执。旧restore mock随未获支持的恢复路径一起移除，明确延期，未当作保留测试通过 |
| hr-before与ecosystem前置不被读取 | 批准hostname、wrapper/ecosystem SHA、退出前状态、runbook SHA均须读入核对；持久保存真实退出前状态、脚本字节和同伴快照后才删除。证据目录复用拒绝；未知部署身份仍为正式窗口前置 |
| 恢复步骤会重启共享API | 现行恢复路径移除该命令；初次启动的共享API重建影响在§5明确列出，仍需批准窗口 |
| operations-final-1不是最终运行 | 原12项不含后来初始化回执测试，属于历史中间运行；不改文件名、不覆盖原字节。本轮完整运维29项绑定新手册SHA，不能与原12项相加 |

## 3. 验证范围与失败记录

定向证据分别为运维29项（9个真实PG/手册用例、20个外部工具因果mock）、部署/preflight37项、恢复/诊断/search22项；范围有交叠，不能加总为全仓覆盖。新恢复测试曾因缺导入、使用v5冻结夹具、未匹配v6冻结策略/哈希而收集失败或夹具失败，完整记录保留；最终v6负例在未修实现上4失败/3通过，修闸门后及补同锁/权限失败案例后9项通过。这些夹具调试错误不冒充产品缺陷RED。

root运行的命令、退出码、原始输出、起始git HEAD、worktree patch与新测试原字节在本轮`runs/<name>/`。Task1早期运行未在运行当时保存准确源码，只有命令/输出，明确不补猜测快照；最终运维运行已有完整源码、patch、fence与SHA回执。root本轮import排序前字节直接保存在`cosmetic-before/`，不是哈希自述。

接口回归保留真实路由、签名/nonce、权限、幂等与数据库；部分owner登录使用既有测试身份，HR装配对象在健康呈现测试中替换。失败草稿及计数另含repository/数据库验证。真实进程验证限本地PostgreSQL、自有HTTP服务和迁移监督/信号，Docker/PM2为明确替身。未启动已有MetaBot进程恢复场景，未调用真实模型，未进行前端组件或浏览器验收、生产验收。

## 4. W表与上线判断

W2仅沿用D1含NULL旧数据的已验证隔离范围；旧NULL大历史压缩阈值尚未独立突变验证。W7/W8不因fixture恢复可运行而升级为整个情报/撤权业务通过。W9新增search恢复新受理闸门、恢复授权及持锁超时证据；仍不能证明生产准确在途、远端进程停止或所有恢复场景通过。W5继续要求ready/failed逐项处置，零占用不代替人工决定。W1/W3/W4/W6/W10/W11/W12没有新增模型或专业通过结论。

工程修订及最终关联结果按本轮独立日志审阅；正式上线前置仍不满足。D三项专业缺陷、真实候选人许可/authorizer、D7配置/计量、飞书去向、正式窗口/镜像、生产迁移/计数/停用、浏览器与canary仍待完成。旧8份缺失日志和3项styles失败不变，旧25项无运行原日志的限制不变。自动HR编排探针与旧链安全恢复现在明确延期，不再隐含为已完成。

本报告为提交内的事实快照。最终独立审阅作为单独文件交付，以准确commit、patch及文件字节为审核对象；不向本报告追加会改变其被审SHA的结案段落。
