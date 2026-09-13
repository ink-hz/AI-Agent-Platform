# HR provision 只读残留核对清单

固定本次执行：

```bash
RELEASE=ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7
DEPLOYMENT=4cd463bbaf897639b891b04555bd0c78
IMAGE=sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59
INPUT=/opt/orbbec-agent-platform/private/hr-provision-inputs/$RELEASE-$DEPLOYMENT
METADATA=/data/orbbec-agent-platform/release-metadata/$RELEASE/hr-provision-$DEPLOYMENT
STAGE=/data/orbbec-agent-platform/provision-staging/$DEPLOYMENT
KNOWLEDGE=/data/orbbec-agent-platform/hr-knowledge
```

先用冻结脚本 `poll --wait` 取得终态并拉回 `stdout.log`、`stderr.log`、`result.json`、`knowledge-before.json` 和 `preflight.json`。随后只读核验：

1. `$METADATA/exit_code` 是 root:root、0600、非 symlink；值为单个整数。只有 `0` 进入成功分支，其他值进入失败分支并现场保留证据。
2. `$INPUT`、`$STAGE`、`$KNOWLEDGE/.current.$DEPLOYMENT`、`/opt/orbbec-agent-platform/private/agent-brain-action.lock` 均不存在。
3. 活动 `/opt/orbbec-agent-platform/private/deploy-input.lock` 不存在；本次准确的 `deploy-input.completed-$RELEASE-$DEPLOYMENT` 可作为 release helper 的完成标记保留。它只能包含 root:root 0600 的 `owner.json`，目录为 root:root 0700；只比较其 release/deployment 身份，不输出正文。
4. `docker ps -a` 中没有名称含本次 `$DEPLOYMENT` 的容器；准确现行 API 容器 ID 仍为 `3347e61ede7d3ab0bd96c0cdc25cff1d6808c89ab2b8a31f24953bebccd4fcf7`、仍运行，且本脚本没有创建或启动 API、HR Worker、附件 Worker。

成功分支再核验：

5. `$METADATA` 中 `exit_code`、`pid`、`stdout.log`、`stderr.log`、`knowledge-before.json`、`preflight.json`、`result.json` 均为 root:root 0600 普通文件；JSON 可解析。只输出 `status/ok/blockers/runtime_match/release/image/configuration/knowledge/services_changed/migrations_run` 等去敏字段。
6. `preflight.json` 必须为 `ok=false`、`blockers=["database_not_checked"]`、`runtime_match=true`、`database.checked=false`、`limitations.model_or_network_called=false`；API 与 Worker 的 configuration、knowledge、provider、budget、diagnostic、release-policy、content-keyring 和 attachment identity 相等，且两端 `d7_product_approved=true`、attachments enabled/wiring present。
7. `/opt/orbbec-agent-platform/private/hr-agent` 为 root:root 0700，只含 9 个 root:root 0600 普通文件：6 个固定 HR 配置、`runtime.env`、`api-runtime.env`、`worker-runtime.env`。不读取这些文件正文，不打印 credential 或任一 env 值。
8. `orbbec-agent-platform-hr-agent-secrets` 卷标签 `hr.provision.deployment=$DEPLOYMENT`；卷根为 uid/gid 10001、0700，只含 12 个 uid/gid 10001、0600 普通文件：4 个现有 API secrets、6 个 HR 配置、`api-runtime.env`、`worker-runtime.env`。只用卷 mountpoint 做 `lstat`/文件名集合检查，不 `cat`、不计算或输出 credential hash。
9. `/data/orbbec-agent-platform/hr-work` 为 uid/gid 10001、0700 空目录。`$KNOWLEDGE/current.json` 为 uid/gid 10001、0644 普通文件，SHA256 为 `c2df4a8fdf5a4b59acdfe4a97a24c3e8be07ff48de6f0c972f87d5473847e467`；它指向 `hr-intelligence-57d25a1702f4718737fb7779`。
10. `$KNOWLEDGE/releases/hr-intelligence-57d25a1702f4718737fb7779` 存在且归 uid/gid 10001；manifest 与资源逐项校验通过，资源为 85 个 intelligence Markdown 加 9 个公开方法资源。以 `knowledge-before.json` 重算旧树：旧目录只比较 type/uid/gid/mode，旧文件比较 type/uid/gid/mode/size/SHA256；所有旧条目必须相同。
11. `result.json` 必须为 `status=completed`、准确 release/image/configuration/knowledge、`knowledge_preserved=true`、`services_changed=false`、`migrations_run=false`、`preflight_blockers=["database_not_checked"]`。

失败分支核验：

12. 除任务 metadata 和 deploy helper 的本次完成标记外，本次新建的 HR private 目录、HR secrets 卷、work 目录、知识 release、`current.json` 和临时 current 均应不存在；旧知识树按 `knowledge-before.json` 保持不变。若任一残留存在，保持现场，不人工泛化删除，按准确 deployment/owner/label/字节身份单独审读 cleanup。

所有检查只读。不要运行 `docker run`、迁移、服务启停、lock helper 的 acquire/release、`cat` secrets/env 或任何清理命令。
