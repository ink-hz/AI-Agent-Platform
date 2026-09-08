# HR 知识内容发布与接入

代码默认为关闭知识接入。启用前，两端应具有同一 source_commit 的内容包；此页是操作说明，不表示已执行生产发布。

## 来源与构建

唯一可编辑来源是 `Orbbec-Agent-Team/bots/hr/knowledge/`。首批内容提交为 `6cea3c8877c3c66e5e3310e22f10b72032cbeaab`，目前在该仓库 `feat/hr-reference-knowledge` 分支。正式分发前先按现有流程合入来源和 Platform 实现。

从 Platform 的 `backend/` 目录运行，替换路径为实际检出位置：

```sh
python -m app.hr.reference_knowledge_release \
  --source-repo /path/to/Orbbec-Agent-Team \
  --source-commit 6cea3c8877c3c66e5e3310e22f10b72032cbeaab \
  --releases-root /path/to/staged-releases
```

构建只读取该 Git 提交的内容，不读取未提交草稿。输出为 `staged-releases/<source_commit>/`，含 manifest、索引、来源台账和资源。新领域按目录及 Markdown/frontmatter 发现，不需要修改资源 ID 白名单。README 是短入口，`sources/` 下是出处材料。

把**同一个构建产物**复制到本地 Agent 的 `<hr_bot_cwd>/.knowledge-releases/<source_commit>/`，以及云端 `<knowledge_root>/releases/<source_commit>/`。按现有发布流程准备只读内容，不覆盖任何已发布版本；内容变化必须新建来源提交。首版不自动清理历史包。

首包共七份资源、九个内容文件；这是首批清单，不是资源数量上限。其 manifest SHA-256 为 `d909e1b954b42cd3c95e14fd0f030cc1fc168deceedf9d1b68f6164a3f41c7ee`。部署时比较两端 manifest 与各文件哈希，记录本地 Agent 可访问的实际目录。Platform 无法仅根据云端副本证明本地包已到位。

## 配置

向网页 API 与独立 HR DirectWorker 提供相同配置，目录均不包含最后一层 source_commit：

```text
PLATFORM_HR_WEB_WORKER_ENABLED=1
PLATFORM_HR_KNOWLEDGE_ROOT=<cloud knowledge_root>/releases
PLATFORM_HR_KNOWLEDGE_AGENT_ROOT=<actual hr_bot_cwd>/.knowledge-releases
PLATFORM_HR_KNOWLEDGE_COMMIT=6cea3c8877c3c66e5e3310e22f10b72032cbeaab
```

三项知识配置要么全部省略，要么同时提供。旧执行路径不启用知识，避免把 v5 冻结恢复保证套到旧路径。现有 HR worker/Relay/direct Agent 前置配置保持原要求。

发布新版本时先分发内容并核对两端，再更新新任务使用的 commit。用户显式选择历史版本时使用该版本；技术重试沿用原冻结提示词与固定路径。旧内容须保留到相关任务恢复和复盘保留期结束，不通过删除旧包实现回退。

## 使用与验证

HR 用户通过受原有登录和 Agent 权限保护的 `/api/hr/knowledge` 浏览；详情路径为 `/api/hr/knowledge/<source_commit>/<resource_id>`。网页读取云端副本，Agent 在本地 Read，二者不互相回源。

“带着这个方法讨论”提交 `user_selected_resources` 中的 source_commit、id、revision、sha256；它与用户文本一起加密持久化，参与幂等比较并进入冻结上下文。用户指定表达偏好，Agent 可以说明局限或补充资源。

知识索引上限 4 KiB，知识字段上限 8 KiB，并纳入现有 96 KiB 上下文限制。较大知识库应使用短领域入口和按需读取的详细索引，不按关键词截取内容。内容以专业参考身份进入会话，不形成工具操作授权。

先确认已保存的[受控试验](../reviews/2026-09-08-hr-reference-knowledge-trial.md)，再用一条测试业务请求验证真实发布链路。核对任务冻结入口、实际 Read 和结果保存；回答自报的引用只作复盘，不作质量或任务完成判定。当前代码的默认展示不提供生产工具遥测。
