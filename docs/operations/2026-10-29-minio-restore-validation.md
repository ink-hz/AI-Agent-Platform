# MinIO 离机恢复验证（2026-10-29）

本任务只验证已成功集合 `20261029T032929866671Z-4b220d42` 的同一份附件密文能否在隔离本机 MinIO 中恢复并经认证枚举。原 `backup-restore-actual-attempt1.json`、8 分钟前采集的全目录基线、原 manifest/三件密文全部保留。原先的 `attachments_baseline` 失败不能改绿或视为同一时点对账通过。

实现限于 `deploy/cloud/restore_minio_backup.py`、`backend/tests/test_restore_minio_backup.py` 和本文，不修改 backup_runtime、SDK、主计划或生产服务。父 agent 已批准本设计；d43正式独立审查无 Critical/Important 后执行，实际结果分别记录在下文。

## 设计与执行计划

- [x] 写 RED：真实合成 AEAD/tar、输入失败不创建目标、分页/权限拒绝、隔离边界、唯一目标清理及信号；实现后 GREEN。
- [x] 再补 RED：真实 SIGTERM 在认证/枚举过程中保留 interrupted；curl XML 桶分页和签名只经 stdin。修后与既有恢复测试共同 GREEN。
- [x] 独立审查d43，无 Critical/Important；已归并主线 `1a4e6525`，合并后65项测试通过。
- [ ] 新建唯一自有 MinIO，真实启动、错误凭据拒绝、正确凭据签名 GET、所有可见桶与对象/版本/删除标记/未完成 multipart 分页枚举，确认删除目标。
- [x] 现有生产 API 内以原已挂载专用 S3 凭据只读枚举，只输出桶/count/hash；与父 agent 的恢复 DB 引用证据并列报告。

脚本复用**同 checkout** `restore_backup_set.py` 的 manifest、私钥权限/公钥指纹、固定密文 FD/hash/version、AEAD 和安全 tar 校验函数，不改变加密协议。只恢复附件，不重跑数据库恢复。附件先完整认证/验证 tar，第二遍流式解密逐文件写入目标 tmpfs；不在宿主写明文。读取目标文件的 SHA-256，确认提取后的文件数/字节数/完整路径与内容聚合等于 archive，再允许 MinIO 启动。tar 链接、绝对路径、`..`、特殊文件和重复项按既有规则拒绝。

Docker 固定显式 `--host unix:///Users/neo/.colima/default/docker.sock`，忽略默认 context/DOCKER_HOST；拒绝其他 endpoint。精确来源镜像为 `sha256:d249d1fb6966de4d8ad26c04754b545205ff15a62e4fd19ebd0f26fa5baacbc0`（父 agent 只读 image save/gzip 导入，无 pull）。目标 `--pull=never --network none`、无发布端口、只读根、uid/gid10001、cap-drop ALL/no-new-privileges、log-driver none；`/data` 2GiB、`/tmp`32MiB 均为 tmpfs，内存768MiB。创建后 selective inspect 核验实际边界，不读取 Config.Env。仅使用唯一 attempt label/name/full ID，清理不触碰其他容器。

根预审要求补齐明文资源保密边界：`--memory-swap=768m` 与 memory 完全相等以禁用 swap，`--ulimit core=0` 禁 core dump，`--cpus=1` 限制CPU，两个 tmpfs 均 noexec/nosuid/nodev。创建后必须实际 inspect `Memory=MemorySwap=805306368`、`NanoCpus=1000000000`、core Soft/Hard均0，及两个 Tmpfs均noexec且无exec；仅声明CLI参数不算通过。原d921未创建实际目标，无已有明文/资源需迁移。新增5个资源边界 RED 后实现，独立复审前仍不运行真实目标。

随机本机临时 root 用户/密码只经 stdin 写到目标 0600 tmpfs 文件，由容器内 shell 读取并 export；凭据值不进入 host argv、Docker Config.Env、日志或报告，私钥从不进容器。使用临时 root **不验收原生产 IAM/权限**。准备期目标只运行等待 shell，逐文件提取不依赖镜像 tar。服务镜像须有 sh/cat/mkdir/sha256sum；首轮d43使用镜像内curl，后续客户端适配见下文。缺少依赖明确失败，不换服务镜像/协议。随后 MinIO 仅监听自身 loopback；30 秒有界健康轮询仅用于启动就绪，签名 S3 请求无重试。一次错误 secret 的签名 GET 必须403，正确临时身份 ListBuckets 必须200。

本机请求保持 botocore SigV4 签名，HTTP桥首轮为 `docker exec curl --config -`，后续已批准采用原生VM客户端，仅进入同一容器网络namespace；无端口转发或共享网络。清单范围为全部可见桶，包含版本配置、current objects、全部 versions、delete markers 和 unfinished multipart uploads；初始桶发现最多128页/128桶；桶内每类最多128页，每桶全部四类合计最多100000记录。桶内页面请求前检查从inventory开始计时的120秒deadline，初始桶发现循环本身尚无该deadline检查。输出只含桶名、数量、字节与排序元数据 SHA-256，不输出 keys、version IDs、upload IDs、内容或服务端错误正文。分页重复游标、权限不足和协议错误均首错失败，不修改 IAM。

退出、超时、SIGINT/SIGTERM 都先终止 host CLI，再按精确 owned identity 停止并移除唯一目标，成功 scoped ps 确认不存在后才报告 cleanup_confirmed。daemon 无法确认时报告 cleanup 失败，保留 attempt/name/ID，禁止把未知当已清理；不自动重跑。SIGKILL 无法执行 finally，须按原 label/ID人工核对清理后再验证。原密文/旧基线和失败记录不删除。

## 已完成的工程验证

首轮 **19 RED**（工具不存在），实现后19通过；自查新增 **3 RED**（两处 SIGTERM 被阶段包装、桶分页字段丢失），修正后新22项与原恢复38项合计 **60通过、0失败，0.51秒**。测试使用真实合成 AEAD、tar、真实本进程 SIGTERM，Docker/MinIO边界为合成；不是实际服务恢复或生产验证。

根预审资源边界新增 **5 RED**（swap/core/CPU/两个可执行tmpfs未拒绝），修正后新27项与原38项合计 **65通过、0失败**；实际资源值仍须真实创建后的 inspect 验证。

```bash
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest \
  backend/tests/test_restore_minio_backup.py backend/tests/test_restore_backup_set.py -q --tb=short
```

实际执行命令（必须在独立审查后，以新私有收据记录）：

```bash
backend/.venv/bin/python deploy/cloud/restore_minio_backup.py \
  --manifest "$RECOVERY_SET/20261029T032929866671Z-4b220d42.json" \
  --artifacts-dir "$RECOVERY_SET" \
  --private-key "$CLOUD_REPLICA/backup-recovery-x25519.key" \
  --docker-endpoint unix:///Users/neo/.colima/default/docker.sock
```

`RECOVERY_SET` 为本机 Application Support/OrbbecAI-Agent-Platform/cloud-replica/recovery-sets 下的上述集合目录。生产只读程序由 `production_inventory_program()` 生成，经 stdin 在已运行的精确 API ID 内执行；只使用现有 S3 文件凭据、固定path寻址/us-east-1、`total_max_attempts=1` 和有界连接/读取超时。配置桶必须在可见清单中，任何权限缺失均失败。不会发送业务消息、写对象或修改服务。

## 证据口径

当前原 archive 的42个普通文件/42277字节均在 `.minio.sys` 下，这是附加物理证据，**不能独立推出业务对象为空**。父 agent 已恢复两库：replica8表/19818行，control209表/1846752行。对象引用证据有2条 uploading 附件/上传意图，size合计1595518，已提交sha256/immutable_locator均为空；另1条 abandoned write_attempt size0/hash0，artifact_versions/derivatives0。上传意图数量和请求字节不等于已归档对象；仍须与认证 S3 的 current/version/delete/multipart 清单核对。

## 实际只读源清单（独立通过）

代码来源为 `d43a224d0841edca46eab583ef52f71192ccf935`，2026-10-29 03:56:11–03:56:12 UTC 在既有 API 容器内通过已挂载凭据执行批准的只读程序。fresh installed resolver 得到 API `a95da88456840af72de6565ad46e5a292e59ad8174b07c36b6e9567351c1fa9e`、image `sha256:cd3fb491ca9a99cc11154a747b48ca821f7f4adfee963586cd329a4d41acdac6`；46个有序输入配置聚合 `3b2ed6609e61d824639d610d24a92233a4d77289d1a96851901f2a6485d0bb34`，完整前后 resolve 相等。MinIO exact image仍为上述d249。没有秘密值、keys或body离开容器，没有写对象、消息、IAM或生产停启。

全部可见桶1个：`orbbec-agent-attachments`，versioning=Enabled；current objects=0、versions=0、delete markers=0、unfinished multipart uploads=0、object_bytes=0，元数据聚合 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。配置桶存在，全部四类只读清单均成功，非权限失败冒充空清单。该观察晚于03:29备份捕获，不宣称同一时点跨资源原子对账。

## 实际本机恢复首轮（失败保留，目标已清理）

使用未修改的d43代码、原set与原私钥、精确colima endpoint和d249镜像，耗时13.073秒。原附件 ciphertext SHA-256为 `06b58658f147286f5230662dde3624c888372517056a60e7fbcf6a302380ee47`；AEAD/tar通过，42文件/42277字节，路径与内容聚合 `33a55b5bec7bce2ed1f57ff6749d40cf1e0ff25a18c086f2f7739f0ff0c088e8`。按批准函数的执行顺序，提取后目标逐文件哈希与该聚合相等才执行launch；该条件已走过。

准备后只读观察到的真实边界为：uid/gid10001、network none、无端口/持久mount、lognone、readonly、memory=swap=805306368、NanoCpus=1000000000、core Soft/Hard=0、两个tmpfs均noexec/nosuid/nodev且对应uid/gid10001。临时随机root凭据只经stdin/tmpfs，未使用原生产IAM身份。

首错是稳定 `stage=docker`，不是完整 MinIO 逻辑恢复通过。只读daemon events进一步保留：health curl从连接未就绪(exit7)转exit0，随后3个signed-config-stdin curl exit0，第4个exit139。结合固定代码调用次序，可推断就绪、错误secret拒绝、正确ListBuckets及版本配置前置已走过，首个ListObjectsV2的curl进程异常退出；没有保存每步HTTP独立收据，不将这些事件补写成完整认证/逻辑清单验收。daemon为aarch64，精确源image为linuxamd64；架构差异是环境事实，**未证明它是exit139根因**。没有重建目标、自动重试失败请求、替换client/身份、换archive或改旧baseline。

唯一目标 `f2d0bae5774780846ec09ec2d4ef296ebdd7247feb2ade3f5f4add55413bed11`（attempt `48eeb83d153a49b7906388cae4765e92`）已stop/rm，独立成功scoped ps确认remaining=0、absence=true。失败与原错误均保留，后续新批次/客户端适配须另行审查；原生产IAM、完整MinIO四类逻辑恢复、跨DB/对象原子性和业务灾备均未宣称通过。

主树 `tmp/security-first-batch-20261029/` 的本次收据均为0600（不提交）：`minio-source-logical-inventory-attempt1.json`、`minio-local-restore-attempt1.json`、`minio-local-restore-boundary-attempt1.json`、`minio-local-restore-cleanup-attempt1.json`、`minio-local-restore-events-attempt1.json`、`minio-local-curl-exit-diagnostic-attempt1.json`。服务端stderr、凭据和文件内容未写入这些收据。

独立审查Minor口径已明确：代码120秒deadline检查覆盖桶内四类枚举；初始桶发现循环目前只有128页/128桶上限，没有同一个120秒门；100000记录上限按每桶计算。当前1桶无截断场景不受这两点影响，本文的整体文字不作为全局预算已实现证据。后续若增加全局门应单独测试和审查；上述实际首轮保持已审d43代码不变，后续HTTP桥改动见下文。

## 自身HTTP测试适配（待独立复审与一个新本机批次）

首轮的curl139只证明进程异常退出，未证明QEMU根因，也不构成服务恢复失败归因的终点。测试HTTP客户端不应绑定被测vendor镜像的CPU架构；服务端仍使用原精确AMD64镜像和同一份archive、tmpfs隔离、随机本机临时root认证与Host botocore SigV4。没有换身份、改生产IAM或改原失败。父agent已批准单一NativeVMBridge设计，只改原三个文件，先TDD/独立复审，再执行一个新local批次；已过的生产只读清单不重跑。

只读可行性确认：Colima default VM为aarch64，现有`/usr/bin/curl`8.5.0（aarch64-unknown-linux-gnu）、`/usr/bin/nsenter`支持`--net=<file>`、Python3、sudo/timeout均存在，sudo -n可用、cgroup2fs。未安装工具、创建新MinIO目标或重跑S3来验证此设计。

health与signed GET统一走原生桥，无vendor curl回退。每请求前后检查Docker fullID/image/attempt及全部原boundary、Running、State.Pid/StartedAt；VM端检查该PID的cgroup含完整Docker ID组件、Uid四项10001、NSpid末项1、进程starttime和netns inode，后续请求与首个受检指纹完全一致。打开/pin精确netns FD，再使用`sudo`原生Python监督`nsenter --net=/proc/self/fd/N /usr/bin/curl`，只进入net，不进入mount/PID/user/env，不读取目标cmdline/env。FD固定避免路径/PID复用把客户端送入其他namespace；VM主机netns必须不同。

签名配置仅stdin进入原生curl，密码/私钥不进VM参数、环境或元数据；curl清空环境、禁curlrc/proxy，core限制0。curl max-time为health2秒/signed5秒，VM监督7秒，Host Colima控制12秒。保留原30秒显式就绪轮询，仅health连接未就绪(exit7)可返回000继续；VM异常、timeout、身份变化及任何signed请求失败均明确固定`vm_*`阶段退出，不重试或fallback。

每个HTTP request有唯一ID。VM `/run/orbbec-minio-http/<attempt>/<request>.json`只存root0700/0600非敏感scope、controller/client PID/starttime及终止状态，不存headers/config/HTTP正文。预登记后，原生子进程先等待gate，保存精确PID/starttime才放行stdin；VM监督在正常、timeout和TERM时kill/wait。Host CLI中断不被当作VM退出证明：外层忽略后续TERM后只调用一次scoped VM cleanup，按PID+starttime+request marker核对并终止该原生client/controller、确认已停，再清理服务目标。PID已复用时不杀新进程；未知则报告`http_bridge.status=incomplete`并保留VM证据和目标，人工核对后才能新批次，不循环清理。正常已终止请求元数据在确认后删除。

工程证据：新增10项RED后实现；再补2项退出竞态RED（probe后进程消失仍须wait/确认），修后新42项与既有恢复38项合计 **80通过、0失败，0.99秒**。包括真实OS子进程timeout、真实TERM、登记失败不放行gate、netns FD路径替换后仍固定原对象、PID/starttime/Uid/NSpid/cgroup/netns拒绝、VM未知清理阻止target清理、stdin/明确profile及VM程序展开编译。proc和VM控制边界使用合成输入，尚未运行新MinIO目标，不冒充新批次实际通过。
