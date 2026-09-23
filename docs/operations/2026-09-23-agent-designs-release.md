# Agent 设计阅读页发布

2026-09-23 已上线：[Agent 设计](https://agent.orbbec.com.cn/admin/agent-designs)，入口「AI 工作 → Agent 设计」。默认打开 HR 总体架构设计，保留全文、表格与 Mermaid，支持章节跳转、图表放大及页面内文档切换。仅平台管理员与所有者可读。

- 应用提交：`4fadbdbb174db288ab7f1c1530c4a5d00413298d`；前一应用：`b3cdfd38587686467120b67ddfba413363469524`。
- 镜像：`sha256:db1fe7456abcef02602ce9485bdd7e8398cef72ffd34552dd1f5e4ce46623256`；API 容器：`da86547f22e36fada78bcb905e257b7c172f1a6e634a11eab5edf46c64bfd584`。
- 源码树：`bd21eef9d32bb5ad4071fe7c29364208039cc868`；归档 SHA-256：`1cea6867da9b4fbe9d1dda607b1eaac92b2fb91d74a28ed24b1cb1460f79a2c3`；清单 SHA-256：`3808e0f0c5c3a07b098bf81549f4ca5fef87c0e4dcc304473b5d9c392e0e0811`。
- 后续维护：`/opt/orbbec-agent-platform/private/panorama-81a506731ab946d6a42bfb4079f6eb56/future-maintenance.json`；沿用 27 份 Compose 输入，仅追加 API 镜像/版本覆盖，共 28 份。

HR 源文件 HEAD 为 `063876aec2e22e432e175c486582448f846080ec`，展示快照包含当时的未提交修改，SHA-256 为 `390a2f5915c08253d63563f8fe04e56660fa24eec4760c2f2b35f6d8908b8a7f`。源与快照字节一致；没有改写或提交 HR 仓库。更新命令：`python3 scripts/sync-agent-design.py --source-repo /path/to/AI-HR-Agent`，同步后随平台发布，不是实时共享文件。原文“目标架构，未全部实施”保持。

验证：后端授权/会话回归 762 项通过，随后新增登录返回路径验证通过（文档专项 6 项通过）；前端组件、路由、Markdown 共 89 项通过，含当前原文 16 张 Mermaid 的实际渲染；生产构建与 15 项发布事务测试通过；独立审查无阻断项。

线上 34 项检查通过：新页面与索引/正文 API 匿名返回 401/no-store，正文 API 为 private；静态资源哈希与镜像一致，设计正文未打包进入前端 JS。容器内正文 SHA-256 与源记录一致，16 张图完整；登录发起接受新页面返回路径。API healthy、重启 0、锁释放。24 个其他容器、Nginx、systemd、持久挂载未改变。历史任务、组织目录与管理员查询的既有只读探查通过。

未执行真实模型调用、业务消息、数据库迁移或授权变更；不改变 HR 设计与执行能力。线上校验未替代完整管理员登录 HTTP 和浏览器阅读验收，页面视觉及实际点击由用户验收。

证据：`/opt/orbbec-agent-platform/private/panorama-81a506731ab946d6a42bfb4079f6eb56`；本地回执：`/tmp/agent-designs-release`。本记录提交不改变上述应用版本。
