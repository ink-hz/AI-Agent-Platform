# Office 收件人目录生产基线实施计划

## 任务 1：冻结生产契约

- 先修改部署测试，要求 base Compose 固定启用、固定只读秘密挂载，且独立 overlay 不存在。
- 增加发布脚本测试，要求切流前秘密校验与启动后容器校验同时存在。
- 运行定向测试并确认失败。

## 任务 2：实现唯一正式部署路径

- 修改 `deploy/cloud/compose.yaml` 的 API 与 loopback 配置。
- 删除 `deploy/cloud/compose.office-recipient-directory.yaml`。
- 修改 `deploy/cloud/remote-stage.sh`，加入发布前和启动后的 fail-closed 门禁。
- 保留 `deploy.sh` 对普通发布输入中 Office 秘密和开关的拒绝。

## 任务 3：更新运维契约

- 将 runbook 的 scoped release 改为标准生产发布保证。
- 删除人工 overlay、临时 env 和“回退为关闭”的步骤。
- 明确正式 bearer 的创建、轮换与失败行为。

## 任务 4：验证、集成与上线

- 运行定向部署测试、完整相关后端测试和 shell 语法检查。
- 检查 diff，提交本地分支；同步最新 `origin/master`，只推 master，不创建远端功能分支。
- 执行正式部署。
- 验证 Platform 服务、正式收件人解析、未授权拒绝与 AI ADMIN 不变性。
- 删除生产机上旧的 `platform-office-recipient.env`，保留正式 bearer。
