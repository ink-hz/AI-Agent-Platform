# 手册独立复核

只读核对本轮三处修订，未修改代码或操作文档；未执行任何真实 Docker/PM2 stop、delete、save、权限操作或生产请求。测试仅把文档 Bash 片段中的全部运行控制命令替换为本地纯 mock，再交给 Bash 执行。

- 宿主 Compose `runtime.env` 的 root-owned/0600 与容器 preflight 两份快照的 uid10001-owned/0600 已明确区分，与 preflight 所有者检查一致。
- 云端停止和 PM2 退出的完整片段都已包入独立子 shell，并启用 `set -euo pipefail`。7 个纯模拟场景通过：PM2 状态检查失败、快照 cmp 失败均非零退出且不调用 save；正常场景才调用 save。云端缺少容器不调用 stop，stop 失败不继续 inspect，仍在运行则非零退出；正常场景通过。
- 28 条 interrupted 的描述改为有 mission run/Turn 引用且关联 Turn/Attempt 已终结，不再声称读取了 mission run 的终态。3 条 queued 命令记录与旧记录的技术终态仍未被扩称为业务成功或实际 Worker 已停止。

本轮指出的三处问题在上述范围内闭合。103 部署顺序、迁移助手保证与外部恢复边界、PM2 delete-one 而非 restore-one 的选择、共享 Relay 保留及各项未验前置仍保持一致。最终套件数字不属于本次核对范围。

复现程序：[runbook_mock_check.py](runbook_mock_check.py)；原始结果：[runbook-mock-results.json](runbook-mock-results.json)。从工作树根执行 `backend/.venv/bin/python artifacts/2026-09-11-hr-e-review/final/runbook_mock_check.py`，仅操作临时 mock 文件。

核对时手册 SHA-256：`6357a6202c9cd4626a920f871c0b8edb91ab2ce85b1fc664d0e9352625b9e082`；收尾记录 SHA-256：`aeff5307660b06c1446ff8e91234e23d6f9040d36cbd614c104ac9381ae400c7`。这些是本次文档快照指纹；之后的测试数字或文字更新不追溯冒充该快照。
