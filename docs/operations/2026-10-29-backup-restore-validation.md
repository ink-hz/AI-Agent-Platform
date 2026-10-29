# 2026-10-29 备份集离机恢复验证

范围限定为一份 complete manifest 与 replica/control/attachments 三个 `.orb`。工具不连接 SSH 或生产，不下载文件，不创建/重启 Docker 容器，不覆盖原数据库，不向外部发送消息。实际恢复由发布负责人在取得备份集并完成本地审查后控制执行。

## 数据与执行边界

- `restore_backup_set.py` 直接加载同一 checkout 的 `backend/app/cloud_replica/backup.py`；`--python-root` 也必须指向该 checkout 的 backend，拒绝跨仓/其他 checkout 模块。Python 环境需要既有 cryptography，其他依赖均为标准库。
- 本机恢复私钥只在本机读取：普通文件、无叶符号链接、属主为当前有效 UID、权限精确 0600、原始 32 字节；派生 X25519 公钥 SHA-256 必须匹配 manifest。私钥不进入 Docker、CLI 参数值、报告或云端。
- manifest 必须为 version=1/status=complete，声明 `individual_captures_not_cross_resource_atomic`，set ID 与三个文件名精确匹配。三项范围固定为 `database:agent_platform`、`database:agent_platform_control`、`minio_data_directory`。验证 ciphertext 完整 SHA-256、文件大小、加密体与明文大小；各明文声明上限 4 GiB。
- 打开密文文件后固定使用同一文件描述符，并核对 inode/大小/mtime/ctime。所有三件完成真实 AEAD 解密认证后，才允许角色与数据库创建。数据库预检解密写入丢弃 sink，随后重新流式解密到 pg_restore stdin；不会保存明文 dump。此两遍解密用于防止末尾认证失败发生在数据库写入之后。
- Docker 只使用 local Unix socket endpoint，拒绝 SSH/TCP daemon；完整 expected container ID 与 image ID 必须精确匹配、容器 running、network=none、无发布端口，无非 tmpfs 挂载。强制 log-driver=none、只读根、非 privileged、cap-drop ALL/无 cap-add、no-new-privileges、UID 999，以及 PGDATA 路径 `/var/lib/postgresql/data` 为 tmpfs。不会按模糊名字或短 ID 选择目标。
- 仅创建 `restore_agent_platform`、`restore_agent_platform_control`。任一个已存在则全部拒绝，绝不 DROP/clean/覆盖。pg_restore 使用 `--no-owner --no-privileges --exit-on-error --single-transaction`。失败可能留下该次新建的隔离 DB，后续由负责人明确处置或重新创建隔离容器，工具不自动删除。
- 可通过 `--roles` 提供纯角色名 JSON 数组。缺失角色使用安全 SQL identifier 引用创建为 NOLOGIN；已有角色不改。拒绝包含密码/属性的对象，不复制生产口令、成员授权或登录能力。此步骤仅满足 RLS 等对象对角色名的结构依赖，不证明权限恢复正确。

## schema 与附件验收

`--schema-baseline` 为必填。主工作树 `tmp/security-first-batch-20261029/source-schema-baseline.json` 的当时真实普通表数量为 replica 8、control 209；代码不硬编码这些数字，要求恢复后的普通表完整 `(schema, table)` 集合与调用方提供的基线精确相等。每表执行 count，报告只汇总每个 schema 的表数和行数，不输出业务行、列值或全部表名。该基线是另一个时间点的结构观察，不能代替源数据行数对账。

附件通过有界 OS pipe 与 tar `r|` 流处理：不 extract 到宿主。拒绝绝对路径、`..`、反斜杠、链接、特殊/稀疏文件、重复路径、超量成员、大小越界和结束标记后的非零内容；逐普通文件核对实际大小并计算内容 SHA-256。最多 100000 个目录/文件成员。报告只输出文件总数、内容字节数、聚合摘要。

聚合规则：对 `(规范化 POSIX 路径, 大小, 内容SHA256)` 元组按路径排序，每项使用 ASCII JSON 紧凑编码后加换行，拼接 SHA-256；路径本身不进入报告。可选 `--objects-baseline` 格式为 `{"files":整数,"bytes":整数,"sha256":"64位hex"}`，只有与相同算法生成的源基线完全一致才通过。不提供该参数时明确报告 `attachments_baseline_compared=false`。

最终报告固定声明 `cross_resource_atomic=false` 和 `business_dr_validated=false`。两个数据库与对象目录的单独捕获不构成跨库/对象原子快照；内容认证、结构恢复和行计数不等同于角色权限、业务链路或完整灾备恢复验收。

## 调用方式

下载内容应放入本机私有目录，并保留 manifest 与三个原始文件；不要把私钥放入下载包或 Docker volume。以下变量由调用方指定：RESTORE_MANIFEST、RESTORE_CIPHERTEXT_DIR、RESTORE_LOCAL_KEY、RESTORE_SCHEMA_BASELINE、RESTORE_ROLE_NAMES。不要用 shell xtrace 或打印私钥。

```sh
python3 deploy/cloud/restore_backup_set.py \
  --manifest "$RESTORE_MANIFEST" \
  --artifacts-dir "$RESTORE_CIPHERTEXT_DIR" \
  --private-key "$RESTORE_LOCAL_KEY" \
  --schema-baseline "$RESTORE_SCHEMA_BASELINE" \
  --roles "$RESTORE_ROLE_NAMES" \
  --expected-container-id eca04f41fdaac3a3b0ef4ebe15ea8cc89bc6e53ed151779d2b3bbbca9a9c65f8 \
  --expected-image-id sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3
```

ID 是负责人提供的本次隔离容器身份，不是工具默认值；容器若重新创建必须由负责人明确提供新的完整 ID。若在隔离 worktree 执行，可使用主工作树 backend/.venv 的 Python 提供依赖，但 crypto 源码仍由工具定位到自己的 checkout。

退出码 0 表示本次本地恢复结构/内容检查通过；1 表示失败。JSON 失败报告仅含固定 stage，不转储 PostgreSQL、Docker、解密异常或业务正文。argparse 参数错误使用标准 stderr 与非零退出。

## 本机目标的额外留存边界

最初提供的空容器使用 json-file，PostgreSQL 恢复失败时可能把 SQL/失败行写入宿主 Docker 日志；负责人已在任何真实恢复前移除它并创建上述新 ID。新目标要求 log-driver=none、UID999、只读根/cap-drop ALL/no-new-privileges、network none/无端口，PGDATA 4 GiB、运行目录16 MiB及 /tmp 64 MiB均为 tmpfs，CPU 2核、内存3 GiB、pids 256。工具强制核心隔离条件，拒绝旧日志驱动与可写非tmpfs数据路径；不自行调整权限、日志或资源上限。本子任务仍未执行真实恢复。

## 本地开发与验证

先写边界测试并观察 RED，再实现输入校验、Docker 边界、流式认证与 PG/tar 消费。测试使用真实加密模块生成合成 `.orb`，只 mock Docker/PG 边界；不访问生产、不读取真实私钥、不执行实际数据库恢复。

```sh
backend/.venv/bin/python -m pytest backend/tests/test_restore_backup_set.py -q
```

必须分别报告工程测试、真实密文解密、真实隔离 PG 恢复、源对象基线对账和业务 DR；不存在后两项证据时保持未验收，不以测试数量替代完整闭环。
