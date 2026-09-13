# HR 上线接续审核交接（2026-09-13）

**当前尚未切换生产接单。** 已完成镜像构建、生产备份、HR 配置与知识包安装及隔离预检；数据库仍是迁移前状态，API、附件和旧网页 Worker 仍运行原镜像。用户已授权上线，排除飞书并明确走真实 HTTP 验收，浏览器不作为本次前置。新增上线阻断：生产对象桶已只读确认版本控制为 Enabled，而现有擦除适配器未指定 VersionId，会留下旧数据版本。已构建 ce6 镜像及已安装的维护输入仅保留为历史准备，不得执行原切换方案；先修复、重新构建并绑定新镜像，同时取得既有有效接口登录凭据，避免附件停机后无法完成实际验收。

工作树：`.worktrees/hr-cloud-loop-e-release`，分支 `feat/hr-cloud-loop-launch`。不得在根目录用户工作树恢复旧方案或覆盖用户修改。已构建但未发布的运行时镜像源为 `ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7`；后续提交中的 host 工具、验收脚本和证据不冒充已进入该镜像。

| 项目 | 已有实际证据 | 尚未证明 |
| --- | --- | --- |
| 镜像 | ce6 源在生产构建成功；镜像 `sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59` | 该镜像的生产服务切换与业务接单 |
| 附件版本擦除 | 生产 GetBucketVersioning HTTP 200 / Enabled；源码裸 delete 未指定版本 | 全版本实际删除、失败保留可重试引用、修复镜像与生产擦除闭环 |
| 数据库 | 生产 001–095 每项 SHA 匹配；job kind 基线预检通过 | 100/102–105 及独立 HR 迁移实际执行；104 准确计数现场耗时 |
| 备份 | 291124812 字节 custom dump、schema/ledger/globals 私有备份；归档目录可读，备份会话归零、两锁释放 | 恢复演练；未 dump 或修改 preview |
| 配置/知识 | 9 个宿主私有文件、12 个 HR 卷文件；94 个知识资源；API/Worker 指纹一致；旧知识原字节保留 | 网络、数据库、实际进程及模型运行验收；当前 preflight 的唯一 blocker 是 database_not_checked |
| 旧链 | 本地 HR legacy 13 completed/3 interrupted、outbox 0；3 条平台 v5 command 有终态/退出/停止并精确匹配终态上传 | 切换窗口中的再核验、云端旧网页 Worker 持久关闭及实际派发拒绝 |
| 历史模型 | 63 会话/242 条用户消息只读提取，22 个合成适配场景；一轮选定四例实际执行六轮、23 次模型调用 | 22 例完整回放；H03 补跑尚在进行；专业质量整体通过 |
| D 模型 | 三轮真实提供方工程试验及独立输出审读，第三轮 1 passed/590.16 秒 | 专业整体通过；仍有单因排除和归因偏移 |
| 页面 | 合并保留岗位/成果能力并修复路由；定向组件/style 工程回归留证 | 本次未做浏览器验收，按用户指令不作为上线前置 |
| 生产接口 | 受认证 canary 脚本及真实本地 HTTP/PG 工程测试；慢流墙钟中断补验 | 有效生产 owner Cookie/CSRF、生产真实擦除、受理/续作/成果及自有进程重启验收 |

实际操作入口为[发布计划](../../artifacts/2026-09-13-hr-launch/production/deployment-plan-review.md)和[当前实施计划](../superpowers/plans/2026-09-13-hr-launch.md)。本次使用提交 `404862b93c57e6782016182f089b67766ab569ff` 的 production-only 监督迁移工具，不能替换为通用 bootstrap 或同时处理 preview。须先停止准确旧附件 Worker、应用 100、启动修复镜像并完成真实物理擦除，再进入 HR 迁移与切换。

独立飞书 MetaBot 不纳入本次停机。平台同一工作仍须由新云端链唯一执行；共享 Relay 和其他 Bot 保留。实际 PM2 加载版本与磁盘 ecosystem 不同，应以[本地平台在途核对](../../artifacts/2026-09-13-hr-launch/production/local-platform-inflight-review/)为准。三条 `reconciliation_required=true` 原样保留；实际加载代码拒绝已完成任务重放，不等于全部外部效果已对账，不得清标志造零。

真实个人材料供应商处理授权仍未落实，personal authorizer 关闭。现有 public-only 配置用于公开/审阅合成材料；它不是任意正文的个人信息分类或自动拦截器。生产预算是 600k/900 秒，历史本地回放使用 1.2M/1800 秒，两者不能互相证明。真实模型工程通过不作为录用、淘汰或专业判断的自动批准。

历史回放原 pytest 为 1 failed/620.12 秒，原 RED 保留。独立审读指出 H03 强制两个 kind、H13 强制保存超出产品语义；修订测试契约不能消除 H01 要求强度、H13“未提供→未采集”等真实内容问题。详见[输出审读](../../artifacts/2026-09-13-hr-launch/history-real-review-1/review.md)。

证据封存分别见 `model-evidence-seal.json`、`engineering-evidence-seal.json` 和 `production/build-and-provision-seal.json`。两个已披露的操作文书/权限问题：旧指纹表的 companion SHA 少一字符，实际发布计划按准确 Git blob 修正，旧表保留；provision 外层曾把两个日志创建为 0644（父目录 0700），已按准确 SHA 定点改 0600，原脚本与补救回执分开保留。旧缺失日志仍缺失，不补写历史“通过”。

新增物理擦除证据见 `production/s3-versioning-readonly-1/`。只读检查不含任何对象删除；数据库 job completed 会清除引用，所以新 canary 需要在删除前私有加密封存准确对象版本清单，再验证真实 job 状态与版本不存在，不能仅以接口 404 为通过。历史 `production/deployment-plan-review.md` 中 ce6 的执行步骤暂时失效，修复后的新方案另行绑定准确源码与镜像。
