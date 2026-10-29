# SDK PHP 切换前冷备协调器

2026-10-29；只针对当前 ZenTao Web/DB 的现场回滚捕获。实现不会部署候选镜像、创建替代数据库、覆盖卷或声称离机 SDK 灾备完成。生产执行须由负责人审查本次实现及源身份后单独控制；本文中的命令是执行方案，不代表已经停启或备份。

## 固定资源与依赖

- 项目为 orbbec-sdk-workbench-trial。当前 Web 完整 ID 为 8f30af40ca3143ffbd1813262aff8d45a1f3d0a80c0b2d90ce3e22445c929162，image 为 sha256:1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce。
- 当前 DB 完整 ID 为 8de67a9b98076ba7605285c90affa66acc0ee2fe76d2764b512a5ffd6015769b，image 为 sha256:88ac718dafee1e8fb415233c04f314f3c3866af99177ce25d436106c62090461。
- Web 卷 orbbec-sdk-workbench-trial_zentao-data 挂载 /data；DB 卷 orbbec-sdk-workbench-trial_zentao-db 挂载 /var/lib/mysql。仅接受清单中的 local/no-options 实际 Mountpoint，完整 mount 记录保留每个字段，只有列表顺序不影响比较。
- source 名称、project/service 标签、image、network 集合、loopback 18131 端口、unless-stopped 策略及 HostConfig 范围都要通过；不以短 ID、tag 或 Compose up 代替精确原容器。
- 复用同 checkout 的 deploy/cloud/backup_runtime.py 中 ProcessRunner、ContainerLifecycle、私有目录检查和原子 fsync 写入。安装时保持 deploy/sdk 与 deploy/cloud 的相对布局，并固定两个文件的准确版本，不能随便从其他仓/发布目录导入同名模块。
- 加密 image ID 由负责人显式传入并验证本机存在；不 pull。使用已准备好的 orbbec-agent-platform-backup-secrets 公钥卷，核对挂载公钥 SHA/32字节与受保护公钥文件一致，并在各次冷捕获前后复核。私钥始终留本机，不上云。

## Docker 元数据合同的架构复核

两次相邻预检失败的共同原因是把 Docker API 的可选/空值表示当成语义身份：首次 Go formatter 直接访问不存在的 Health；第二次 SDK 把 local 卷 Options=null 与测试中的 {} 做整 dict 比较。既有 deploy/cloud/backup_runtime.py 的 RuntimeBackup._volume 已将 null 按无 options 处理，而 SDK 重写比较时遗漏此约定；FakeRunner 固定返回 {} 又掩盖了真实接口差异。此次统一入口约定，不靠第三次完整冷备 job 逐字段试探。

| 消费者/字段 | 表示到语义的明确映射与保持边界 |
|---|---|
| source/helper 的 INSPECT health | 用 index 安全读取可选 Health；缺失/null 输出 null，存在时只保留 Status。Web 可无 healthcheck；DB 仍须明确 healthy，不能把未知映射为健康。 |
| 公钥卷、Web卷、DB卷的 VOLUME_FORMAT | 统一 SDK local_volume_identity，遵循共享 resolver 的 no-options 语义：仅 null 或空 dict 归一成 {}。四个字段 name/driver/options/mountpoint 必须齐全，拒绝额外字段、错误类型、非空 options；name/local driver 精确，源 mountpoint 精确，公钥卷实际 mountpoint 必须绝对路径并核验实际公钥 hash。不会把任意假值、缺字段或 bind/NFS 等选项当作无选项。 |
| source Mounts | 仅列表顺序无关；每项全部字段、值与重复项都保留，再按 canonical JSON 排序比较。未知字段、RW/Mode/Propagation/Source/Name/Driver 差异仍拒绝，不丢字段、不只比目标路径。 |
| networks/Compose labels | 网络名称按集合、标签按映射键读取；project/service 标签必须精确。网络内部 IP 等运行时数据及其余标签保留为证据，不作为固定身份。缺少或增加网络名称拒绝；不按网络 map 的序列顺序判断漂移。 |
| source HostConfig/Compose 有序值 | 初检保留现有权限、模式、restart、loopback port 边界；完整 HostConfig 留存并在后续按全部值比较。JSON map 的键顺序无意义，但数组顺序不通用归一：SecurityOpt/CapDrop/PortBindings 列表、Compose 命令/entrypoint/env_file/覆盖文件顺序均保持原语义。工具不解析/重排 Compose 或读取宿主 env 值；私有 env/compose 只以 stat fence 和原始 tar 内容保存。DB 无 PortBindings 的 null/{} 仍只表示无发布端口。 |
| image、状态与其他 preflight 消费者 | image 始终按完整 ID 比较，RepoTags 可 null且不作为身份；running/PID/exit/OOM 按 daemon 实际值判断，不归一未知。目录 inode/owner/mode、公钥32字节/hash、固定配置 hash、event_scheduler=OFF、进程树及外部 writer 各保留原检查；没有统一“or {}”放宽所有字段。 |

下一次完整 job 的前置门是负责人在独立诊断 root/private/lock/metadata journal 下单独调用完整 SDKColdBackup._preflight，并保存真实只读结果：公钥/两源/两卷/三个本地 image/现场配置 hash/scheduler/process tree/writer/磁盘检查必须一次全部通过；只能使用只读 Docker inspect/ps、固定 sha256sum、容器内只读 scheduler SQL、私有 HTTP GET 和宿主 stat/FD 检查。不得调用 run、ContainerLifecycle、helper、stop/start 或捕获；诊断 journal 的 source_actions 必须始终为空。该真实 preflight 尚未通过时，不允许第三个完整 job，也不把本地合成测试当作生产门已通过。

## 执行状态与失败恢复

同一生命周期契约同时约束证据与恢复：preflight 的活源检查只决定能否开始，不能代替停机后实际捕获字节的验证；CLI 返回/异常也不能代替 daemon 的最终状态。三个 rootfs 配置须在实际 docker cp→加密管道中透明转发并校验 tar：恰好一个准确 basename 的普通文件、文件最多16MiB且归档最多17MiB、实际内容 SHA 与固定清单一致；拒绝链接、稀疏文件、额外成员与非零尾部。任何成员/摘要/截断/producer 退出异常均使整集失败，即使加密器成功。捕获前后仍保留完整 cold fence。内部 copy-config 子进程仅输出原 tar 字节、不打印诊断，docker cp 留在 producer 进程组供原有清理器管理，不落宿主明文。

写入 stop_call_started 后，若 stop 的观察被异常或 TERM 打断，finally 只观察同完整 ID 的 daemon，不能再次 stop，也不能因单次仍 running 就认定终局。对尚未确认停止的源，恢复阶段额外最多观察原优雅停止时限加35秒（Web65秒、DB155秒），包含瞬时 inspect 失败；观察到 exited/PID0 才恢复同 ID。超时仍未知则保留 blocked journal，DB 未确认 ready 绝不启动 Web。此窗口明确接受等待正在进行的原 stop，避免协调器先退出、daemon 随后才停而遗留停机。

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

首次生产 attempt sdk-cold-20261029T042431085647Z-328d1bf5 于2026-10-29 04:24:31 UTC启动，预检首错 source_identity 后停止；status=failed、source_actions.web/db 均为空、blocked=false，没有 stop/start 任何源，原服务无影响。负责人只读定位为 Web 没有 State.Health，旧 Docker Go 模板直接访问该缺失字段导致 formatter 失败，不能据此推断源 ID 变化。修正为 with index .State "Health" 的缺失字段安全访问，存在时仍仅输出 Status；用本机唯一自有、未启动、network none/log-driver none/tmpfs 容器的真实 Docker formatter 做 RED→GREEN 验证并移除该测试容器。原失败记录保留；修复不自动重跑整批，独立审查后由负责人另开新 batch。

第二次生产 attempt sdk-cold-20261029T042923410027Z-70b3fd6c 于2026-10-29 04:29:23 UTC预检首错 source_volume 后停止；source_actions 仍为空，原服务没有停启。负责人只读确认两个卷的 name/local driver/mountpoint 均精确匹配而 options 均为 null。此失败原样保留；后续依上述统一合同和完整只读 preflight 门处理，不宣称前两次成功。

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

真实 Docker formatter 契约测试需显式设置 SDK_COLD_DOCKER_FORMATTER_IMAGE 为本机已有完整 image ID；默认跳过。测试拒绝非 Unix Docker endpoint、使用 pull=never，只创建一个唯一标签且从未启动的隔离容器，最后只移除经标签核实的该完整 ID；不会操作现有容器。
