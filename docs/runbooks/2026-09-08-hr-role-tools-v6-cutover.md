# HR v6 一次切换

2026-09-08 22:14 CST 已完成一次切换，正式版本与证据见交付记录。以下保留实际操作和后续发布注意事项，不是要求再次执行。

1. 固定并推送三仓提交。Team `36b932b01f11e893dcca16ac8162b392b84fbb8c`，完整 role 与知识投影同一 commit。角色包 hash `92786276c247fe4ee4c1224cfb3232455a0c126cf51b9e54138775c4a46462f7`。MetaBot 发布编译的 `dist/index.js` 与 `dist/runtime/hr-tool-mcp.js`，不使用源码入口启动缺失的 MCP JS 文件。运行配置 `.env`、`runtime-contract.json` 不在 Git 中，必须从上一实际 HR release 原样保留；依赖链接复用上一 HR release 已验证的依赖根，不能假定全局 metabot/node_modules 等价。
2. 通过既有 SSH、发布锁和 `/data/staging/orbbec-agent-platform/<deployment_id>` 准备 release、镜像与包。复用当前镜像依赖，仅替换应用、正式迁移和前端产物。阶段目录 trap 清理；完整源从固定 Git commit 归档，不带数据/密钥/依赖。
3. 记录 `df -B1 / /data`：根盘可用至少 25 GiB、预计至少 20 GiB、发布后不超过 75%；净增长超过 1 GiB 解释。保留 current + 2 回滚，归档最多 10 版或 30 天，不动其他服务镜像和数据。
4. 暂停 HR 新消息入口，确认既有 HR 轮次真实终态；API 切换窗口停止受理后再核对在途。存在在途则恢复旧服务完成/取消，不改冻结 v5 命令、不伪造终态、不重跑用户消息。
5. 用正式迁移器应用 `backend/control_migrations/hr_web/094_hr_role_tools_v6.sql`，检查已应用 checksum。新增 scoped grant、结果、身份关联与读取证明；撤销旧消费者权限。Local Worker 对原有 callback 表应用 `pending/worker_v6_tools.sql` 的 3 列扩展（worker 独立库未采用云端编号目录），先查列/事务处理，已具备时仅核对，不重复 ALTER。
6. HR MetaBot、既有本地 Signed Worker、云端 API 与独立 HR Worker 同一切换。完整角色固定 cwd，JD CLI 指向同一 Team 包的 services/hr-jd-sync/cli.mjs 与既有 registry 状态目录。Cloud API/HR Worker 配置 role root/commit、knowledge root/agent root/commit；保留其他 Bot、模型、飞书 app 配置和 Nginx。
7. 核对 signed readiness 为 `core_chat_collaboration_v6`、固定 Team commit/hash 和三个工具能力。版本不匹配时不运行 HR 新轮次，不回退 v5 或双信封消费者。检查页面资源、API health、HR Worker 运行和既有标准读取；不另发生产业务消息。
8. 记录前后容器/非 HR Bot 身份和 Nginx hash。回滚需先停止新受理并处理原执行；094 的授权撤销不能靠换旧镜像掩盖，不能重新启用旧生产链处理 v6 消息。失败保持 HR 停止并修复具体组合。

工程检查与用户质量验收见 [交付记录](../reviews/2026-09-08-hr-role-tools-v6-delivery.md)。不重复已完成的跨仓恢复、CLI 或布局测试。
