# SDK PHP 切换前冷备协调器

2026-10-29；只针对当前 ZenTao Web/DB 的现场回滚捕获。实现不会部署候选镜像、创建替代数据库、覆盖卷或声称离机 SDK 灾备完成。生产执行须由负责人审查本次实现及源身份后单独控制；本文中的命令是执行方案，不代表已经停启或备份。

## 固定资源与依赖

- 项目为 orbbec-sdk-workbench-trial。当前 Web 完整 ID 为 8f30af40ca3143ffbd1813262aff8d45a1f3d0a80c0b2d90ce3e22445c929162，image 为 sha256:1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce。
- 当前 DB 完整 ID 为 8de67a9b98076ba7605285c90affa66acc0ee2fe76d2764b512a5ffd6015769b，image 为 sha256:88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461。
- Web 卷 orbbec-sdk-workbench-trial_zentao-data 挂载 /data；DB 卷 orbbec-sdk-workbench-trial_zentao-db 挂载 /var/lib/mysql。仅接受清单中的 local/no-options 实际 Mountpoint，完整 mount 记录保留每个字段，只有列表顺序不影响比较。
- source 名称、project/service 标签、image、network 集合、loopback 18131 端口、unless-stopped 策略及 HostConfig 范围都要通过；不以短 ID、tag 或 Compose up 代替精确原容器。
- 复用同 checkout 的 deploy/cloud/backup_runtime.py 中 ProcessRunner、ContainerLifecycle、私有目录检查和原子 fsync 写入。安装时保持 deploy/sdk 与 deploy/cloud 的相对布局，并固定两个文件的准确版本，不能随便从其他仓/发布目录导入同名模块。
- 加密 image ID 由负责人显式传入并验证本机存在；不 pull。使用已准备好的 orbbec-agent-platform-backup-secrets 公钥卷，核对挂载公钥 SHA/32字节与受保护公钥文件一致，并在各次冷捕获前后复核。私钥始终留本机，不上云。

## 执行状态与失败恢复

1. 独立 work-root 及 private 子目录必须当前 root 拥有且模式0700；ciphertext 子目录必须 UID10001、0700。private 内独立 sdk-cold.lock，0600普通文件。锁前后均复查 source-inflight.json / backup-inflight.json；残留/符号链接均拒绝新 attempt。
2. 只读 preflight 核对源最初 running/健康、准确身份、完整挂载/HostConfig、目录 inode/权限、私有 sdk.env/sdk-compose.yaml 的0600/属主及 stat身份、三个配置的现场 hash、磁盘峰值空间、公钥、加密 image。source 环境变量和值不做宿主 inspect/export。发现任何额外 RW 挂载、宿主 writable FD，或任何额外 DB 私网 peer，均拒绝。检查只能证明所观察范围；不允许并行其他运维绕开此窗口。
3. 先写 Web stop intent 并 fsync，再仅对原 Web 执行30秒优雅 stop；核对 daemon stopped/PID0、原进程 start-time 与 cgroup.procs 全部退出。CLI超时不代表没有执行，使用随后实际 daemon 事实；无法确认则不捕获。
4. Web 已退出、DB 仍 running 时，在 DB 容器内消费现有环境口令；口令不回主机、不进入 argv。event_scheduler 预检和 quiesced 阶段均必须 OFF，不修改事件配置。以只读 repeatable-read consistent snapshot 对完整普通表/视图逐对象 COUNT，前后复核对象集合。普通表与私有既有清单的307项精确集合匹配，视图数量34；不使用旧60715作为行数期望。quiesced 新完整集合与 COUNT 才是此次离机验证基线。
5. quiesced 证据原子写入 private 并关联 SHA，然后写 DB stop intent；仅对原 DB 执行120秒优雅 stop（客户端外层150秒）。必须实际 exit0、无OOM/PID0/退出进程树，并取得本次时间窗内真实 MariaDB [Note] Shutdown complete。137、kill、只有旧日志或普通错误文本中的类似字样均拒绝。
6. 每次捕获前后核对两源仍 stopped、身份和全部 mount/HostConfig 不变；冷态完整树 stat摘要不变、无新增外部写入者、私有配置与公钥未变化。原生 producer 与加密器的退出都必须成功；不能把加密器对截断输入的成功当成备份成功。
7. 无论成功失败，finally 先尝试确认宿主进程组终止，并按 owner/attempt/name/image/完整ID清理仅本 attempt 的 helper。source Web/DB 从不登记给 ContainerLifecycle，也不允许其 rm。tar/cp/RR source producer 都只读；native或helper清理无法确认时阻断备份成功并保留journal，但仍经精确daemon身份与DB ready门优先恢复原服务，不能用只读清理未知制造长期停机。
8. 只恢复本 attempt 实际调用 stop 且随后观察到停止的原容器；preflight失败或 intent 写入失败但未调用 stop，绝不能 start 原本未停/不健康的源。优先恢复原 DB、限300秒观察原 health ready，再恢复原 Web、限60秒确认 running 与原私有登录页可读。任何 DB 身份/状态不明或未 ready，绝不启动 Web。不重建、不覆盖数据、不切候选、不自动业务重试。
9. DB/Web 恢复、六件捕获与 helper 清理都确认后才发布 complete manifest。失败保留 attempt 结果/残留密文；恢复或清理不能确认时保留阻断 journal。SIGINT/TERM走finally，恢复期间忽略第二次终止请求；SIGKILL不能保证执行finally，下一次必须由负责人核查残留 journal、同ID实际状态后处置，不能覆盖它重跑。

容器内只读 SQL 使用120秒 timeout（TERM，5秒后kill），宿主 SQL 客户端等待150秒、采集器180秒。这样宿主 exec 客户端失联也不留下无期限只读 COUNT；不会杀 DB 主服务。数据库口令只由容器内 shell 引用 MARIADB_ROOT_PASSWORD 到 MYSQL_PWD，现有 grants 保持启用。

## 六件密文与消费协议

set_id 为 sdk-cold-UTC时间-8hex，filename 均为 kind-set_id.orb。所有 source 内容只走 stdout→加密 helper stdin；不落明文 tar/dump，不把私有 env 内容解析为宿主变量或日志。helper 固定10001:10001、rootfs只读、cap-drop ALL、no-new-privileges、network none、restart no、log-driver none；只读公钥卷和本次 ciphertext bind，没有业务源卷挂载。

| 顺序/kind | scope | 明文归档 |
|---|---|---|
| db-volume | mariadb_data_directory | 宿主原生 tar，从DB卷根 . 捕获，numeric owner |
| web-volume | zentao_data_directory | 同上，从Web卷根 . 捕获 |
| app-config | container_file:/apps/zentao/config/config.php | docker cp 原停止Web:/path -，单文件 config.php |
| php-config | container_file:/opt/zbox/etc/php/php.ini | 同上，单文件 php.ini |
| apache-config | container_file:/opt/zbox/etc/apache/httpd.conf | 同上，单文件 httpd.conf |
| deployment-config | sdk_private_configuration | 私有目录原生tar，仅 sdk.env 与 sdk-compose.yaml |

每件含 kind/scope/filename、capture_started_at/finished_at、ciphertext_sha256、file_size/encrypted_size/plaintext_size、producer_returncode/encryptor_returncode、encryptor_container_id/encryptor_daemon_exit_code。三个单文件另有 original_path/archive_member_basename/expected_file_sha256。现有加密 CLI receipt 不输出 plaintext_sha256；消费端要完整解密验证 AEAD header 与明文尺寸/hash，不能只信 manifest。

顶层 version=1，backup_kind=sdk_cold_onsite_rollback，status=complete，sources.web / sources.db 保存原完整 id、image_id、mounts、host_config 等安全元数据；recovery_public_key={sha256,size_bytes:32}。snapshot_consistency 只有证明冷态后才是 two_sources_stopped_with_db_clean_shutdown。

quiesced_baseline 的确切结构：

~~~json
{
  "event_scheduler": "OFF",
  "transaction": "repeatable_read_read_only",
  "observed_at": "UTC ISO8601",
  "objects": [
    {"schema": "zentao", "table": "technical_name", "type": "BASE TABLE", "engine": "InnoDB", "rows": 0},
    {"schema": "zentao", "table": "technical_view", "type": "VIEW", "engine": "", "rows": 0}
  ]
}
~~~

objects 按 schema/table 排序，普通表与视图都 COUNT。单独证据文件与内嵌 baseline 相同，编码为 sort_keys=True、separators=(',',':')、ensure_ascii=True 的UTF-8 JSON；quiesced_baseline_evidence={filename,sha256} 对应该文件完整字节。此为技术元数据，private模式0600；公开CLI只输出状态、set_id、数量，不输出对象名、业务正文或口令。

容器 health 元数据仅保留 Status，不采集 Health.Log/Output。内部 probe 子命令stdout只供父进程私有捕获，不作为公开报告。

## 准备与执行入口

负责人先创建自己专属 work-root/private/ciphertext，按上面的属主/模式检查；复制已有只含技术元数据的 zentao-cold-backup-inventory.json 至受保护路径，保留307普通表完整集合。不要把工作根放在两个源卷内；不要并行更换源容器、修改配置/公钥或进行SDK业务写入。

~~~sh
python3 deploy/sdk/backup_sdk.py \
  --work-root "$SDK_COLD_ROOT" \
  --deployment-dir /opt/orbbec-agent-platform/private \
  --public-key /opt/orbbec-agent-platform/private/backup-recovery-x25519.pub \
  --encryptor-image-id "$SDK_COLD_ENCRYPTOR_IMAGE_ID" \
  --inventory "$SDK_COLD_SOURCE_INVENTORY"
~~~

以上命令会短暂停止并恢复两个正式SDK源，只能在负责人批准窗口执行。工具不申请、修改SSH/安全组，不停止平台、行政、FAE或其他服务。部署前应审查脚本、共享runtime的准确版本、容器内 timeout 可用性和生产路径；不能把本文命令当成“只读检查”。

## 独立离机验证与结论边界

此工具仅完成现场旧 image + env + 两卷 + 三份现场配置的加密捕获，并恢复原服务。原现场配置不能仅因 entrypoint存在就假定可完整再生，因此保存准确文件/hash。平台 SDK 对接密钥卷、本机旧镜像留存/异机镜像资产、完整SSO授权和业务访问路径是另行责任。

生产捕获后由负责人独立下载六件密文，先完整 AEAD/tar 校验，再在无网络、无持久日志、资源受限的同准确 MariaDB image 隔离环境恢复 tmpfs，使用正常授权（不 skip grants）验证新 quiesced 对象集合/COUNT，及配置 hash。私钥永不上云；密码只在本机RAM从解密的 sdk.env 读取并送隔离客户端 stdin，不写宿主明文文件或泄漏到参数/报告。

未执行上述独立真实启动前，off_machine_sdk_dr_validated 始终 false；即使通过也不能等同于完整SDK权限/SSO业务DR。备份失败、恢复失败、未执行与生产验收分别记录，不重采旧跨时点数据、不删除参数或重试到通过。

本地验证：

~~~sh
backend/.venv/bin/python -m pytest backend/tests/test_sdk_cold_backup.py -q
~~~

测试为合成 Docker 边界与固定时钟，加上共享 ProcessRunner/原加密CLI的真实本地合成管道；没有调用生产、模型或业务发送。
