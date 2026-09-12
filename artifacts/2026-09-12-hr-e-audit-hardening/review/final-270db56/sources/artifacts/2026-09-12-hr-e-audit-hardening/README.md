# E 第二轮审计证据入口

基线：`5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a`。本轮工程代码先冻结为`3504c20`（运维）及`6eaefc2`（恢复/部署/诊断）；整组回归运行期间不改源码。最终关联回归为567 passed / 1 skipped / 23 warnings，421.98秒，退出码0；[原始日志](runs/final-integration/output.log)与[命令](runs/final-integration/command.json)绑定准确代码提交。条件跳过为缺自有MetaBot进程夹具；正式上线前置仍不满足。

事实说明见[本轮交接正文](../../docs/reviews/2026-09-12-hr-e-audit-hardening.md)。最终被审提交与每份源文件的准确身份以`manifest.json`中的`reviewed_commit`和`sources`为准；该正文先提交再审，最终审阅放在独立文件，不修改被审正文加入结案段落。

- `runs/`：root每次运行的命令、起始HEAD、退出码、原始日志、工作树patch和未提交新测试原字节。夹具调试失败与产品负例分开说明；不是每次失败都是产品缺陷。
- `operations/`：运维原始日志、20个外部工具因果mock场景、真实PG持锁慢count回执、准确源码/fence/patch快照。早期运行缺同期准确源码的限制见`task1-report.md`，其“未提交”描述是该报告写作时状态，最终三份源码已在`3504c20`提交。
- `forensics/`：三份明确标注的历史精确重建与两份存活临时patch复制；`aeff5307…`原文仍缺失，没有伪造原件保存或当前提交身份。
- `cosmetic-before/`：本轮导入排序前的实际文件字节及哈希回执。
- `lint/baseline-comparison-final.json`：本轮12个变更Python文件相对基线没有新增Ruff问题；保留两条既有问题，不声称全仓静态检查通过。
- `final/historical-identity.json`：357份历史artifact及102/103/104三个迁移文件逐字节保持不变。
- `review/`：准确Git提交的独立审核、保留在仓库内的patch及指纹。

本地真实数据库、HTTP身份/签名/授权和自有进程只证明列出的工程性质；模型/角色冻结输入、Docker/PM2等替换边界按正文与各测试明示。没有生产、真实模型、浏览器、消息发送或部署操作。自动HR编排探针、旧链安全恢复、生产计数与正式窗口验证仍未完成；既有专业缺陷和历史缺失证据没有被测试数量消除。

复算（从任意目录均可调用此脚本）：

```bash
python3 artifacts/2026-09-12-hr-e-audit-hardening/verify_evidence.py
```

manifest覆盖本目录全部普通文件，排除它自己及`final/manifest-verification.json`自引用回执；同时核对被审Git源文件、当前源码、历史原件和恢复文件。新增或修改证据都会报错。
