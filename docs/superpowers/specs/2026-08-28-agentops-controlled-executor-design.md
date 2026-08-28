# AgentOps 受控执行器设计

日期：2026-08-28  
状态：设计已获用户原则确认，等待书面复核  
范围：本机 `neo` 与 `agentops` 的受控运维边界、Agent Brain 验收调用链

## 1. 目标

`agentops` 是 Orbbec Agent Platform 完全控制的本机运行账号。Platform 的发布和验收不应反复索取 macOS 管理员密码，也不得把管理员密码保存到脚本、文件、环境变量、Keychain 提取命令或标准输入。

本设计用可审计、可撤销的“受控执行能力”替代密码传递：完成一次系统级安装后，`neo` 可以无交互地让 `agentops` 执行一组固定运维动作；不能借此取得任意 `agentops` shell 或 root shell。

## 2. 非目标

- 不保存、提取、转发或记录 macOS 登录密码、管理员密码。
- 不授予 `neo` 通用免密 root 权限。
- 不允许 `sudo -u agentops /usr/bin/env *`、任意 shell、任意命令或调用者自定义路径。
- 不修改 AI ADMIN、FAE、Nginx、业务数据库或 Agent 业务逻辑。
- 不把 Platform Cookie、钉钉身份或云端私钥写入审计日志。

## 3. 方案

安装一个 root 持有且普通用户不可修改的可执行文件：

```text
/Library/PrivilegedHelperTools/orbbec-agentops-control
```

它始终以 `agentops` 身份执行，只接受无自由参数的具名子命令：

```text
relay-canary
worker-stop
worker-restore
metabot-release-sha
agent-team-release-sha
status
```

所有路径、工作目录、环境变量和参数均在执行器内部写死。未知命令、额外参数、符号链接、所有者或权限不符时立即失败。

`/etc/sudoers.d/orbbec-agentops-control` 只允许 `neo` 以 `agentops` 身份调用上述 root-owned 执行器，不允许执行 `/usr/bin/env`、`/bin/sh`、`sudo` 或其他程序。sudoers 文件必须由 root 拥有、权限 `0440`，并在安装前后通过 `visudo -cf`。

## 4. 调用链

```text
neo / Platform acceptance
        |
        | sudo -n -u agentops（只允许固定执行器）
        v
root-owned orbbec-agentops-control
        |
        | 固定 HOME、PATH、cwd、命令与参数
        v
agentops-owned Worker / Canary / read-only Git metadata
```

`deploy/cloud/accept.sh` 不再拼接任意命令。现有五处 `run_agentops` 调用改为具名子命令映射；任何未列入映射的调用在本地失败。

## 5. 云端凭据

长期不复制 Neo 的云端 SSH 私钥。为 `agentops` 生成独立 Ed25519 密钥，私钥由 `agentops` 持有且权限为 `0600`；公钥通过现有受控云端发布通道安装，并使用独立 key comment、来源地址限制和轮换记录。

当前验收中已经产生的 Neo 私钥临时副本，只用于完成本次切换；专用密钥验证通过后立即删除。删除副本不影响 Neo 的原始私钥。

## 6. 安装与撤销

安装器是非交互、幂等和事务式的：

1. 校验执行器与 sudoers 候选文件的 SHA-256、语法和绝对路径。
2. 原子安装 root-owned 执行器与 `0440` sudoers 文件。
3. 对完整 `/etc/sudoers` 执行 `visudo -cf`。
4. 运行拒绝测试和一个只读 `status` 冒烟测试。
5. 任一步失败则恢复安装前状态。

安装只需要一次 macOS 管理员授权。后续正常发布、Canary、Worker 恢复和版本读取不再弹出密码框。

提供独立卸载器，删除 sudoers 条目和执行器；卸载仍需管理员授权，防止普通进程静默改变权限边界。

## 7. 审计与错误处理

- sudo 保留调用者、目标用户、执行器路径和时间记录。
- 执行器额外记录子命令、开始时间、结束时间、退出码和固定目标，不记录 Cookie、Token、私钥、请求正文或业务数据。
- 子命令失败时原样返回非零退出码；Platform 验收失败关闭，不自动切换用户、命令或凭据。
- Worker 停止后的恢复动作必须由验收脚本的 trap 调用 `worker-restore`；恢复失败时明确报告并保持 Brain 关闭。

## 8. 测试与验收

先写失败测试，再实现：

- 执行器拒绝未知命令、额外参数、错误调用用户、错误 HOME 和符号链接目标。
- 每个子命令只执行一组固定路径与参数。
- sudoers 候选与安装结果均通过 `visudo -cf`。
- 治理测试禁止保存密码、`sudo -S`、Keychain 密码提取和 `/usr/bin/env *`。
- `deploy/cloud/accept.sh` 的全部 `agentops` 调用只能映射到允许的子命令。
- 安装、重复安装、部分失败回滚和卸载均可验证。
- Relay Canary 必须输出既有精确成功标记。
- 正式 Agent Brain 验收必须在无密码提示下完成；AI ADMIN `/office/` 与 FAE 前后不变。

## 9. 发布顺序

1. 实现执行器、安装器、卸载器与测试。
2. 修改 Platform 验收脚本为具名子命令。
3. 全量测试并提交到 `master`。
4. 一次管理员授权安装执行器。
5. 创建并验证 `agentops` 专用云端密钥。
6. 删除 Neo 私钥临时副本。
7. 运行 Relay Canary 和 Agent Brain 正式验收。

## 10. 完成条件

只有以下条件全部成立才算完成：

- 后续验收不再要求管理员密码。
- 系统中不存在可提取的管理员密码。
- `neo` 不能借该通道获得任意 `agentops` 或 root shell。
- Relay Canary 与正式验收通过。
- 临时 Neo 私钥副本已删除。
- `/office/`、FAE、端口和现有服务保持不变。

