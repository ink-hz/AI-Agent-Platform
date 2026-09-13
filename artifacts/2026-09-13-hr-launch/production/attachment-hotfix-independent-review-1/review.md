# 4f08431 附件维护监督器独立安全审读

结论：所审冻结提交4f08431的attachment_hotfix.py在已授权本次维护、准确固定输入与现有串行锁条件下，没有发现新增阻断，可进入根控制的实际执行。不是生产已执行或物理擦除通过。作者未执行生产，本审阅也未调用生产、模型、业务消息或停止任何服务。

源码SHA256 `2e13d1e2f508d6bdf06761fd5e830a795130dc01df4d4eb7e8dd2a6fb6301b34`；测试 `c864f2ade756a55999d864a64447794edab10578ee04175981c0b651c9e392a4`。snapshot/从准确提交导出完整监督器、测试、原green命令/日志、固定inputs与三项依赖；完整commit/SHA在fingerprints.json。

## 承重顺序和边界

preflight只接受固定旧65e7镜像/容器ID、当前指针、固定ce6镜像digest；不读取动态值后反写为“新基线”。备份receipt要求completed、cleanup/session零、archive可列及95ledger，且dump/globals/schema/ledger字节与尺寸固定核对。这是已完成备份可用性证据，不宣称做过恢复演练（receipt明确restore_rehearsal=false）。

action lock采用root0700目录和0600 owner token/inode；deploy-input-lock实际helper SHA固定，使用其真实acquire/validate/release契约，无自造标记字段。对private environment、compose、迁移脚本、迁移helper与peer容器的id/image/running/started/restart/policy重复核对，发现漂移直接拒绝，不学习新的expected值。只允许platform-attachments和自己的probe例外，不重建API或其他peer。

stop_old先预写failclosed marker与receipt，再准确ID/image检查、update restart=no、stop。verify_old_stopped要求旧ID仍存在、正确name/image、Running=false及restart=no；容器消失/替换不当作停止证明。migrate入口再次验证，不能绕过此前检查直接开迁移。

## 真实迁移监督器与外层清理

调用的是固定SHA `381dbfbcf89bdc286dc1544982705f656ba9b1a0f2ba4442769f7498a56455b2` 的hr_agent_migrate.py，显式root/production，指定已固定release/private/image/Postgres及私有receipt目录。不是旧bootstrap两库GRANT脚本。root集合为原95+100/102–105；本次不仅100，计划应准确保留这一事实。现代job-kind preflight使用已审固定宿主源码，完整已应用ledger校验、95基线和后续rootchecksum仍由该监督器落实。

子监督器在GRANT前记录grant_pending，确实包含environment=production及name/id；具名只读cap-drop/no-new-privileges迁移容器、四信号、stop/kill、revoke和membership/session核验路径保持原样。外层等待1000秒、子迁移900秒；外层收到四信号先停止准确子客户端进程组，给120秒清理机会，随后才强制kill。Docker子客户端/daemon并非同一存活边界，所以外层另按已证明receipt的container name/id/image/cmd收敛容器，并独立核对成员和session归零，不能以客户端消失证明迁移停了。

关键修复已确认：Docker inspect/stop异常不会跳过撤回已证明本次production GRANT；revocation是独立try。revoke不代表既有SET ROLE会话终止，at_rest仍必须0|0；不明/未归零标记cleanup_verified=false、保留锁/标记，不启动新镜像、不杀不明peer会话。缺receipt不擅自认领其他授予。输入/peer在迁移后再次核对，准确最终ledger及六个maintenance列SELECT权限全部验证后才走创建。

## 唯一新容器与实际失败

compose up仅platform-attachments，使用--no-start --no-deps --force-recreate；要求只返回一个新ID，正确image/name/project/service/cmd且未运行。先保存new_id和new_identity_owned receipt，才update restart=no并重新验证，之后再次check_inputs/validate_locks再docker start。测试明确模拟此处写盘失败，不能到start；内存new_id仍可供失败清理定位。

健康后仍复核peer/input/locks与迁移零状态，才恢复新容器期望unless-stopped并释放所持锁。成功状态是installed_awaiting_erasure_canary，保留failclosed marker及physical_erasure_verified=false。worker健康不是队列业务或S3擦除证明；root仍需实际canary对象图/DELETE/物理缺失/job证据。

任何捕获失败只停止准确已拥有的新容器及旧容器，不包含旧镜像重启/restore命令；失败不删除锁和marker。若本地文件系统持续写入失败，最终receipt可能无法完成，但预写的持久边界应保留，不能将异常退出视作清理成功。SIGKILL/主机故障仍需要外部依据receipt核验，不是自动恢复保证。同一窗口禁止另一个operator绕过锁直接改Docker/文件；检测漂移不等于防止root级非合作写入。

## 测试证据准确范围

作者最终原日志16 passed /0.47s，source_unchanged=true，开始HEAD828e89e；不是声称运行于提交4f08431之后。测试包括真实自有子进程四信号及timeout回收；其他是注入Docker/SQL/文件系统故障的控制流测试：peer漂移、停止后错误不复旧、旧ID消失/替换拒绝、Docker故障仍revoke、migrate入口接线、新ID写盘失败不启动、child cleanup/六列权限缺失拒绝。不是实际Docker部署、真实迁移集成或生产验收。受复用迁移helper先前真实PG/故障证据另存原目录，不把这16项当作又一次实库迁移。

独立仅重跑exact commit快照的daemon_failure_does_not_skip_revocation_of_proven_own_grant：1 passed /0.03s，command.json与focused.log记录exit0。未重跑全套、未修改作者源码/历史RED/已staged release。所审最终SHA新增TypeError/KeyError/Timeout清理保护已核对，不以前一版31ed源码冒充最终。

执行授权属于根已有生产维护任务，本报告不新增操作范围。实际运行若固定旧ID、peer/input/备份或权限不符，必须接受失败，不能现场改常量、删校验或恢复旧代码凑通过。
