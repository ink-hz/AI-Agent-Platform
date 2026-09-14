# HR 正式入口替换发布记录（2026-09-14）

> 后续已发布页面布局恢复版 `d9c3e8c6`，当前 API 镜像、容器及维护覆盖以[页面恢复记录](2026-09-14-hr-layout-restoration.md)为准。本文下方保留首次入口替换时的状态。

用户确认旧入口无人使用，明确要求直接替换上线。本次已将 `/hr/` 设为云端 Hannah 主对话；`/hr/agent` 保持同页兼容，`/hr/chat` 进入新版。删除“试用”并列导航和标签。历史消息/成果保留只读，旧会话发送、重试和确认入口关闭。岗位快捷入口只带入按用户隔离、未发送的内存草稿。

## 最终生产状态

- 页面/API 发布源码：`523bb95a95d42f664617fcde0d1b43a6ba26eeba`；tree `f1b0fe76f7a739ffe2286bca743b0be834aede5b`。
- API 镜像：`sha256:52a472780ab39f6a82e4489800df038f88ad7f60cfcca14dbb67d64407dd663d`；容器 `f9a24280de733c55e2b83a9868a430ae553b9de133ef735deb0f4d3d631c1a1c`。
- HR 执行器和附件工作器保持兼容107镜像 `sha256:0327cc3a6259994b2546aaf3d3e3ca3fa13911e7d53afe7a78d4a9342a293213`。前端后继版与原运行版的 `backend`、`deploy/cloud` 没有代码差异，因此页面发布只替换 API。
- 三个新服务均 `healthy`、`restart=unless-stopped`。旧 HR web 工作器停止且 `restart=no`。切换门为 `cloud`、epoch3，legacy_nonterminal=0；API与HR执行器实际就绪，允许新任务。
- `current` 指向上述523bb95a源码。共享 `platform.env` 与 generation runtime.env 仍固定0327镜像；新API通过持久化的单服务覆盖文件使用52a472镜像。API 当前及未来旧HR工作器开关均为0。
- 发布 action/deploy 锁与联合发布 failclosed 标记全部确认已释放。

## 后续维护必须使用完整配置

服务器 `/opt/orbbec-agent-platform/private/hr-api-successor-b7f4219073443688fe1068f1742d3141/continuation-static/future-maintenance.json` 保存了准确 `base_argv`、按顺序的 compose_files 及覆盖文件摘要。仅追加明确授权的服务操作，勿仅使用基础 compose；否则会漏掉 API 镜像覆盖或知识/密钥 generation。

API 持久化覆盖文件为 `/opt/orbbec-agent-platform/private/hr-api-successor-b7f4219073443688fe1068f1742d3141/execution/compose.api-successor.json`。原联合发布收据记录的是之前的API ID，不能将新API误当作原联合发布的容器。新API所属操作为 b7f4219073443688fe1068f1742d3141。

## 验证和实际限制

前端组件：相关34文件456测试通过；最后新增超大情报边界修复后，该组件18测试通过。独立审查另运行238项相关测试通过。最终构建和diff检查通过；这些数量有重叠，不合并计数。

生产验证：数据库迁移至107并完成真实门禁切换；新API、HR执行器、附件工作器健康；24个API以外的容器保持已审查基线。通过实际HTTP读取外网 `/hr/` 及其JS/CSS，全部200并与已验证镜像的服务端HTML转换/静态文件摘要匹配。JS中旧“试用”标签不存在。无登录会话访问HR工作接口返回401，未伪造用户会话或成功结果。

旧卡住请求已通过精确进程身份校验停止其Claude子进程，原生签名恢复协议写入停止证明，Turn/Attempt正常cancelled，用户输入保留。不可变v5 job仍显示queued，但准确关联的终止谱系满足104排空条件；没有直接写数据库伪造取消或清空任务。共享MetaBot与relay存活身份保持不变。

API页面替换曾在静态检查处暂停，约04:49–04:52 UTC出现切换中断。原因是发布辅助脚本误将镜像内相对资源路径和经鉴权外壳转换的HTML当成相同字节；应用代码没有因此修改。已核验并启动同一新容器，修复静态验证契约，随后完成持久化和释放。此前一次Compose美元转义显示差异导致的预检失败发生在停服之前。所有失败收据保留，没有把重复执行当成恢复。

受认证的真实账号模型/业务质量验收尚未完成：本次没有可用的既有登录凭据。当前新HR运行时仍完整收集模型回复后提交，逐字/增量文本显示尚未实现；现有轮询不是逐字输出。飞书、浏览器验收及真实个人材料处理不纳入本次完成声明；个人材料处理保持关闭。

## 收据

工作树 `artifacts/2026-09-14-hr-launch/takeover-2/api-successor-2/final-live-readback.json` 保存最终容器、就绪、门禁、维护配置和锁状态；同目录 `public-verified.json` 保存外网文件摘要验证。`takeover-legacy-diagnosis/post-stop-verification/` 保存安全化的原生停止和数据库谱系证据。此前失败、备份、联合发布及构建收据均在同一日期目录保留。
