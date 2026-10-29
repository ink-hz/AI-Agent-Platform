# MinIO 离机恢复验证（2026-10-29）

本任务只验证已成功集合 `20261029T032929866671Z-4b220d42` 的同一份附件密文能否在隔离本机 MinIO 中恢复并经认证枚举。原 `backup-restore-actual-attempt1.json`、8 分钟前采集的全目录基线、原 manifest/三件密文全部保留。原先的 `attachments_baseline` 失败不能改绿或视为同一时点对账通过。

实现限于 `deploy/cloud/restore_minio_backup.py`、`backend/tests/test_restore_minio_backup.py` 和本文，不修改 backup_runtime、SDK、主计划或生产服务。父 agent 已批准本设计；代码提交后先独立审查，再执行真实恢复。

## 设计与执行计划

- [x] 写 RED：真实合成 AEAD/tar、输入失败不创建目标、分页/权限拒绝、隔离边界、唯一目标清理及信号；实现后 GREEN。
- [x] 再补 RED：真实 SIGTERM 在认证/枚举过程中保留 interrupted；curl XML 桶分页和签名只经 stdin。修后与既有恢复测试共同 GREEN。
- [ ] 独立审查本提交，确认实际执行命令、输入集合和目标隔离。
- [ ] 新建唯一自有 MinIO，真实启动、错误凭据拒绝、正确凭据签名 GET、所有可见桶与对象/版本/删除标记/未完成 multipart 分页枚举，确认删除目标。
- [ ] 现有生产 API 内以原已挂载专用 S3 凭据只读枚举，只输出桶/count/hash；与父 agent 的恢复 DB 引用证据并列报告。

脚本复用**同 checkout** `restore_backup_set.py` 的 manifest、私钥权限/公钥指纹、固定密文 FD/hash/version、AEAD 和安全 tar 校验函数，不改变加密协议。只恢复附件，不重跑数据库恢复。附件先完整认证/验证 tar，第二遍流式解密逐文件写入目标 tmpfs；不在宿主写明文。读取目标文件的 SHA-256，确认提取后的文件数/字节数/完整路径与内容聚合等于 archive，再允许 MinIO 启动。tar 链接、绝对路径、`..`、特殊文件和重复项按既有规则拒绝。

Docker 固定显式 `--host unix:///Users/neo/.colima/default/docker.sock`，忽略默认 context/DOCKER_HOST；拒绝其他 endpoint。精确来源镜像为 `sha256:d249d1fb6966de4d8ad26c04754b545205ff15a62e4fd19ebd0f26fa5baacbc0`（父 agent 只读 image save/gzip 导入，无 pull）。目标 `--pull=never --network none`、无发布端口、只读根、uid/gid10001、cap-drop ALL/no-new-privileges、log-driver none；`/data` 2GiB、`/tmp`32MiB 均为 tmpfs，内存768MiB。创建后 selective inspect 核验实际边界，不读取 Config.Env。仅使用唯一 attempt label/name/full ID，清理不触碰其他容器。

根预审要求补齐明文资源保密边界：`--memory-swap=768m` 与 memory 完全相等以禁用 swap，`--ulimit core=0` 禁 core dump，`--cpus=1` 限制CPU，两个 tmpfs 均 noexec/nosuid/nodev。创建后必须实际 inspect `Memory=MemorySwap=805306368`、`NanoCpus=1000000000`、core Soft/Hard均0，及两个 Tmpfs均noexec且无exec；仅声明CLI参数不算通过。原d921未创建实际目标，无已有明文/资源需迁移。新增5个资源边界 RED 后实现，独立复审前仍不运行真实目标。

随机本机临时 root 用户/密码只经 stdin 写到目标 0600 tmpfs 文件，由容器内 shell 读取并 export；凭据值不进入 host argv、Docker Config.Env、日志或报告，私钥从不进容器。使用临时 root **不验收原生产 IAM/权限**。准备期目标只运行等待 shell，逐文件提取不依赖镜像 tar。镜像须有 sh/cat/mkdir/curl/sha256sum；缺少依赖明确失败，不换镜像/协议。随后 MinIO 仅监听自身 loopback；30 秒有界健康轮询仅用于启动就绪，签名 S3 请求无重试。一次错误 secret 的签名 GET 必须403，正确临时身份 ListBuckets 必须200。

本机请求经 botocore SigV4 签名后用 `docker exec curl --config -` 的 stdin 发送到同一容器 loopback，无端口转发或共享网络。清单范围为全部可见桶，包含版本配置、current objects、全部 versions、delete markers 和 unfinished multipart uploads；每类最多128页，总记录最多100000，整体120秒限额。输出只含桶名、数量、字节与排序元数据 SHA-256，不输出 keys、version IDs、upload IDs、内容或服务端错误正文。分页重复游标、权限不足和协议错误均首错失败，不修改 IAM。

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

本文当前仅记录设计与工程验证；真实 MinIO 恢复、原生产 IAM、同一时点跨DB/对象原子性和完整业务灾备未宣称通过。实际收据、首错及后续独立复验分别追加，原失败不覆盖。
