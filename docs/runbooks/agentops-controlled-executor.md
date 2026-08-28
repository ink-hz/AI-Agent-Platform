# AgentOps 完整免密执行运行手册

本手册维护 `neo` → `agentops` 的完整免密执行能力。它不保存 macOS 密码，允许
`neo` 以 `agentops` 身份运行任意命令，但不授予 `root` 或其他系统账户权限，
也不改变 AI ADMIN、FAE 或 Nginx。

## 1. 前置检查

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
git status --short
git rev-parse HEAD
git rev-parse origin/master
/usr/sbin/lsof -nP -iTCP:9110 -sTCP:LISTEN
/usr/sbin/lsof -nP -iTCP:9120 -sTCP:LISTEN
curl -fsS -o /dev/null -w '%{http_code}\n' 'https://agent.orbbec.com.cn/office/?view=services'
curl -fsS -o /dev/null -w '%{http_code}\n' https://fae.orbbec.com.cn/
```

要求工作树无本任务变更、9110/9120 仅回环监听、行政门户与 FAE 均返回 `200`。

## 2. 首次安装

先生成独立 AgentOps 云端密钥，并从 `neo` 的受信任主机记录中提取唯一的
`47.106.112.69 ssh-ed25519` 公钥：

```bash
deploy/cloud/provision-agentops-acceptance-key.sh
```

精确成功标记：

```text
AGENTOPS_ACCEPTANCE_KEY_STAGED_OK
```

然后触发唯一一次 macOS 管理员授权：

```bash
/usr/bin/osascript -e 'do shell script "/Users/neo/Developer/work/AI-Agent-Platform/deploy/local-execution-worker/install-agentops-control.sh" with administrator privileges'
```

安装成功标记：

```text
AGENTOPS_CONTROL_INSTALL_OK
```

安装器把正式规则安装为：

```sudoers
neo ALL=(agentops) NOPASSWD: ALL
```

同时识别并事务化移除唯一允许的历史重复规则
`/etc/sudoers.d/agentops-management`（其内容必须逐字等于
`neo ALL=(agentops) NOPASSWD: ALL`）；文件内容、所有权或权限不符时失败关闭，
不会删除未知 sudoers 配置。正式规则只保留在
`/etc/sudoers.d/orbbec-agentops-control`。

安装器还会把该单条云端主机公钥以 `0600 agentops:staff` 安装到
`/Users/agentops/AgentRuntime/private/cloud-known-hosts`。所有验收 SSH 请求
都同时启用 `StrictHostKeyChecking=yes` 和固定的 `UserKnownHostsFile`；不得
退回 `accept-new`、关闭主机校验或复制 `neo` 的完整 `known_hosts`。

验证此后的任意 `agentops` 命令不再询问密码：

```bash
sudo -n -H -u agentops /usr/bin/id -un
sudo -n -H -u agentops /bin/sh -c 'cd /Users/agentops && pwd'
sudo -n -H -u agentops \
  /Library/PrivilegedHelperTools/orbbec-agentops-control status
```

预期：

```text
agentops
/Users/agentops
AGENTOPS_CONTROL_OK commands=6
```

`sudo -n -l -U neo` 的结果中必须且只能出现一条
`(agentops) NOPASSWD: ALL`；不得出现 `ALL=(ALL)`、`(root)` 或其他免密目标。
原有执行器继续保留为稳定的 Canary/启停快捷入口，但不再是权限白名单边界。

## 3. Relay Canary

```bash
sudo -n -H -u agentops \
  /Library/PrivilegedHelperTools/orbbec-agentops-control relay-canary
```

精确成功标记：

```text
AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 accepted_job_kinds=direct_agent,metabot_local public_ports_added=0 duplicate_dispatches=0
```

失败时可直接以 `agentops` 身份执行只读诊断，不需要密码：

```bash
sudo -n -H -u agentops /bin/sh -c \
  'cd /Users/agentops/AgentRuntime/platform/backend && exec .venv/bin/python -m app.execution_relay.acceptance_orchestrator /Users/agentops/AgentRuntime/private/acceptance-config.json'
```

保持 Brain 关闭，检查 Worker 9120 和云端任务状态。

## 4. Agent Brain 正式验收

确认 Cookie、Prompt、Provider 证据和独立质量评审文件均为 `0600` 后运行：

```bash
deploy/cloud/accept.sh \
  /Users/neo/.orbbec-agent-platform/brain-v2-2f61471/acceptance-config.json \
  release
```

精确成功标记：

```text
AGENT_BRAIN_V2_ACCEPTANCE_OK
```

验收脚本失败关闭：任一阶段失败都会恢复 Brain 关闭状态，并恢复被中断的 Worker。

## 5. 密钥轮换

轮换前确认没有发布或 Agent Brain action lock。再次运行 provision 脚本会用远端 prepare/commit/rollback 事务替换受限公钥，并在本地生成新的 pending 私钥。随后再次以管理员权限运行安装器，原子替换 `agentops` 私钥。

轮换完成后必须重新运行 `status` 与 Relay Canary。AgentOps 公钥限制为 SSH 实际观察到的来源 IP，并带 `restrict` 选项；公司出口地址变化时必须重新轮换，不得移除来源限制。

## 6. 密钥撤销

```bash
deploy/cloud/revoke-agentops-acceptance-key.sh
```

精确成功标记：

```text
AGENTOPS_ACCEPTANCE_KEY_REVOKED_OK
```

脚本只删除 `BEGIN/END ORBBEC AGENTOPS ACCEPTANCE KEY` 管理块，并验证指纹，不修改其他 `authorized_keys` 内容。

## 7. 执行器卸载

卸载属于权限边界变更，需要管理员授权：

```bash
/usr/bin/osascript -e 'do shell script "/Users/neo/Developer/work/AI-Agent-Platform/deploy/local-execution-worker/remove-agentops-control.sh" with administrator privileges'
```

预期：`AGENTOPS_CONTROL_REMOVE_OK`。卸载后 `sudo -n -H -u agentops ... status` 必须失败。

## 8. 回滚与不变性

安装失败时安装器自动恢复原执行器、sudoers、AgentOps 云端私钥、固定主机公钥
和验收配置。远端密钥 prepare 后任何本地失败都会恢复旧 `authorized_keys`；若
哈希被外部并发修改，事务失败并保留证据，不覆盖并发变更。

每次安装、轮换、验收和回滚后都执行：

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' 'https://agent.orbbec.com.cn/office/?view=services'
curl -fsS -o /dev/null -w '%{http_code}\n' https://fae.orbbec.com.cn/
curl -fsS -o /dev/null -w '%{http_code}\n' http://47.106.112.69/
```

三项必须均为 `200`。同时比较 FAE container ID、image ID、StartedAt、RestartCount、Config hash 和 Mounts hash；任何变化都视为失败。

## 9. 权限边界

- 不保存或提取管理员密码。
- 不使用 `sudo -S`、Keychain 密码导出或密码环境变量。
- 允许 `neo` 免密执行任意 `agentops` 命令。
- 不允许 `neo` 免密成为 `root`，也不允许切换到其他系统账户。
- 不在日志、报告或命令输出中打印 Cookie、Token、私钥、钉钉身份或业务正文。
