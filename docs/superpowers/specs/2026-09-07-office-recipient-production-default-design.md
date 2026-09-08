# Office 收件人目录生产基线设计

日期：2026-09-07

状态：已确认，进入实施

## 目标

Office 收件人目录是已经投入使用的生产能力。标准 Platform 发布成功后，API 与
loopback 必须同时启用该能力；不得再依赖发布后的人工 overlay、临时环境文件或
二次重建容器。

## 生产不变量

1. `deploy/cloud/compose.yaml` 是唯一运行配置，API 与 loopback 均固定启用收件人目录。
2. 正式 bearer 只保存在宿主机
   `/opt/orbbec-agent-platform/private/platform-office-recipient-bearer`，以只读 bind
   mount 提供给两个容器；秘密不得进入仓库、环境文件、argv 或日志。
3. 发布在停止旧服务前验证 bearer 是非符号链接普通文件、UID/GID 为
   `10001:10001`、mode 为 `0600`，并按运行时规则验证 UTF-8、去除首尾空白后的
   32 字节下限、16 KiB 上限和可用于 HTTP bearer 的可见 ASCII 字符。验证失败则
   整次发布失败，旧服务继续运行，错误输出不得包含秘密。
4. 新服务启动后，发布门禁验证两个容器均启用功能、挂载为只读，并验证 loopback
   只接受固定本地 peer `172.31.0.1/32`。任一条件不成立则发布失败并走既有 Platform
   回滚路径。
5. 首次切换若失败，自动与手工回滚都必须识别上一历史 release 内的旧 overlay，
   用它恢复既有启用状态；新的正常发布路径不得读取该 overlay。
6. `deploy/cloud/compose.office-recipient-directory.yaml` 与生产机上的旧
   `platform-office-recipient.env` 不再属于运行机制，应删除。
7. 不修改、不重启 AI ADMIN；发布前后必须比较全部 AI ADMIN systemd 主进程及
   active 时间，证明它们未变化。它继续使用现有正式 bearer 调用 Platform 回环接口。

## 安全边界

能力“默认可用”不等于接口公开。既有 bearer、local-peer、最小字段、`no-store`、
未授权统一 404 等边界保持不变。标准部署脚本仍拒绝从本地 deploy 配置注入 bearer
或功能开关，避免秘密进入普通发布输入。

## 验收

- 仓库中不存在独立 Office overlay。
- base Compose 为 API 与 loopback 提供相同的只读 bearer，并固定启用能力。
- 发布脚本在切流前验证 bearer，在启动后验证运行时配置。
- 合法 AI ADMIN 请求成功；缺 bearer、错误 bearer 和公网请求继续失败关闭。
- AI ADMIN 进程身份在发布前后不变。
