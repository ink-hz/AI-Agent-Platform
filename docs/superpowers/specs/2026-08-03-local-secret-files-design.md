# 本机凭据文件替代 macOS Keychain 设计

**日期：** 2026-08-03  
**状态：** 已批准  
**范围：** AI Agent Platform 本机运行凭据

## 背景与目标

平台由单一 macOS 用户维护。现有 Keychain 方案要求后台 LaunchAgent 读取四个独立条目，更新访问控制或重启验证时会触发多次交互授权，运维成本超过本机部署的收益。

本批次彻底移除平台对 `/usr/bin/security` 和 macOS Keychain 的依赖，改为仓库外的本机凭据文件。目标是：退出登录或重启后自动恢复、永不弹出钥匙串授权、凭据不进入 Git、LaunchAgent plist、数据库、日志或页面，并继续保持三种 PostgreSQL 角色的最小权限边界。

## 方案

凭据存放在当前用户专属目录：

`~/Library/Application Support/OrbbecAI-Agent-Platform/secrets/`

目录权限必须为 `0700`，每个凭据文件权限必须为 `0600`，文件归当前用户所有且不得为符号链接。四个文件分别保存 analyst DSN、review writer DSN、sync writer DSN 和 FAE dev replay token。

平台新增统一的本地秘密文件读取器。读取器只接受绝对路径、普通文件、当前用户所有权和不允许 group/other 访问的权限；内容为空、文件过大、权限不合格或路径异常时均显式失败，不回退到其他身份或更高权限凭据。

数据库配置继续允许进程环境变量作为临时运维覆盖；默认值改为上述文件路径。Replay 的 registry 引用从 `env:` 改为 `file:`。CredentialResolver 仅支持 `env:` 与 `file:`，删除 `keychain:` 分支。同步 CLI、Fleet 和 Review 数据库解析器统一使用秘密文件读取器，不再启动子进程。

## 迁移与清理

迁移工具从当前已验证的运行环境取值，原子写入四个本机文件并设置权限。验证新文件与当前值一致后：

1. 清除四个用户 launchd 临时环境变量；
2. 重启 Platform LaunchAgent；
3. 触发一次同步 LaunchAgent；
4. 验证 Platform、Review、Sessions、数据同步和 replay 凭据解析；
5. 删除四个旧 Keychain 条目。

旧条目只在所有冒烟通过后删除。删除后凭据仍保存在受权限保护的本机文件中，不发生凭据丢失。

## 测试与验收

- 单元测试覆盖文件存在、权限、所有权、符号链接、空值、过大文件和环境变量覆盖。
- 回归测试断言代码、配置、registry 和活动运维文档不再包含 Keychain 读取机制。
- 全量后端测试通过。
- 清除 launchd 临时变量后，Platform 重启并且 `/api/health`、`/review`、`/api/review/overview`、Sessions API 返回成功。
- 同步 LaunchAgent 在无 Keychain、无临时环境变量时 exit 0，Review backfill 成功。
- Replay 凭据能够从 `file:` 引用解析；不对生产运行评测或问答。
- 精确删除四个旧 Keychain 条目，随后再次重启冒烟，确认不出现授权弹窗。

## 非目标

本批次不改变数据库密码、角色授权、反馈闭环状态、FAE 生产版本或生产回答路径，也不把秘密写入 LaunchAgent plist。
