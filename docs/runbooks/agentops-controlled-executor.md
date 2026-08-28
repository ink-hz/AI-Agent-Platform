# AgentOps 受控执行器运行手册

本手册维护 `neo` → `agentops` 的固定运维能力。它不保存 macOS 密码，不提供任意 shell，也不改变 AI ADMIN、FAE 或 Nginx。

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

先生成独立 AgentOps 云端密钥：

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

安装器会识别并事务化移除唯一允许的历史规则
`/etc/sudoers.d/agentops-management`（其内容必须逐字等于
`neo ALL=(agentops) NOPASSWD: ALL`）；文件内容、所有权或权限不符时失败关闭，
不会删除未知 sudoers 配置。

验证此后的调用不再询问密码：

```bash
sudo -n -H -u agentops \
  /Library/PrivilegedHelperTools/orbbec-agentops-control status
```

预期：

```text
AGENTOPS_CONTROL_OK commands=6
```

`sudo -n -l -U neo` 的结果中不得再出现 `(agentops) NOPASSWD: ALL`，且只应有
上述执行器的六条固定命令。

## 3. Relay Canary

```bash
sudo -n -H -u agentops \
  /Library/PrivilegedHelperTools/orbbec-agentops-control relay-canary
```

精确成功标记：

```text
AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 accepted_job_kinds=direct_agent,metabot_local public_ports_added=0 duplicate_dispatches=0
```

失败时不要临时开放 shell 或复制密码；保持 Brain 关闭，检查执行器审计、Worker 9120 和云端任务状态。

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

安装失败时安装器自动恢复原执行器、sudoers、AgentOps 云端私钥和验收配置。远端密钥 prepare 后任何本地失败都会恢复旧 `authorized_keys`；若哈希被外部并发修改，事务失败并保留证据，不覆盖并发变更。

每次安装、轮换、验收和回滚后都执行：

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' 'https://agent.orbbec.com.cn/office/?view=services'
curl -fsS -o /dev/null -w '%{http_code}\n' https://fae.orbbec.com.cn/
curl -fsS -o /dev/null -w '%{http_code}\n' http://47.106.112.69/
```

三项必须均为 `200`。同时比较 FAE container ID、image ID、StartedAt、RestartCount、Config hash 和 Mounts hash；任何变化都视为失败。

## 9. 安全禁令

- 不保存或提取管理员密码。
- 不使用 `sudo -S`、Keychain 密码导出或密码环境变量。
- 不增加通用 NOPASSWD、`/usr/bin/env *`、`/bin/sh` 或任意参数入口。
- 不在日志、报告或命令输出中打印 Cookie、Token、私钥、钉钉身份或业务正文。
