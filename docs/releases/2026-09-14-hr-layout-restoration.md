# HR 页面恢复与分支核对（2026-09-14）

用户反馈云端入口替换后页面布局混乱，旧版更清晰，岗位页无法滚动，并要求检查分支是否漏合入。已 fetch origin，核对相关 HR 分支提交和所有 HR 工作树的跟踪文件改动。

旧版导航、渐变、磨砂及岗位工作流提交 `723c81e7`、`4905faf7`、`5fb4d548`、`65e7fbd1` 均在发布分支历史中。合并 `a7d9b5c4` 的第二父提交带有 `hr-position-directory` 和岗位卡片，但合并结果保留了另一侧 `hr-position-index`。该结构只设 min-height，外层 `.hr-workspace-body` 为 overflow:hidden，因此岗位内容超出后不可滚动。主对话变化来自 `523bb95a` 把此前独立试用的 HrLoopWorkspace 提升为默认入口。

相关 HR 工作树已跟踪文件中没有未提交的前端改动；未提交跟踪改动均为历史报告/设计文档，保留原样。补查未跟踪文件发现 `.worktrees/hr-agent-web-workspace/webui/src/workspaces/hr/HrPositionTaskActivity.tsx` 及同名测试，内容为旧 R1.2 任务进度/重试展示，尚未提交，也未被页面导入；保留原样，不自动恢复旧执行方向。此前“没有未提交前端代码”的口头结论仅覆盖跟踪文件，已向用户更正。部分历史分支仍有独立提交（运行时、下载、发布记录或测试），未将提交不在祖先链直接等同于功能缺失，也未据此整分支合并。远端 master 是当前发布分支祖先。

本次从已发布的岗位工作流样式恢复卡片、页面宽度、层级及独立滚动，保留当前岗位来源、草稿决策和主对话操作。云端对话复用原 HR 渐变磨砂配色和宽正文布局；正文独立滚动，输入框位于底部，成果与标准在正文内。岗位搜索弹层固定于视口，避免矮窗裁切。

## 当前生产版本

- 源码 `d9c3e8c6e908d5f1da8365df36c92a804eea659f`，tree `7659a3f939399b84b49694e253bb7560d2925e90`。
- API 镜像 `sha256:8a61c21b9778c14d4b82b58999885a5b722ddd71629a5915fca9f9d78cb6fbce`。
- API 容器 `f5d70e692a62ae2de6bd884543f52aa267aa3eaabd16d4923638892d7907401a`，healthy、unless-stopped；current 指向本次源码。
- 24 个非 API 容器的运行身份保持部署基线。backend 和 deploy 相对上一版523bb没有差异；云端门禁、HR worker、附件 worker 和全局0327镜像保持现状。
- 后续维护以服务器 `/opt/orbbec-agent-platform/private/hr-ui-restore-4e4c2d67bf049c00a4b7d28c35002377/execution/future-maintenance.json` 的 base_argv 和完整有序覆盖文件为准。它保留前次 API 覆盖，再追加本次覆盖。勿只使用基础 Compose。
- action/deploy 锁均释放，无联合发布 failclosed 标记。

## 验证范围

前端组件：相关5文件67项通过；本地及镜像生产构建通过。独立静态审查通过。jsdom 存在 Window.scrollTo 未实现提示，组件通过不代表实际滚动验收。

发布辅助脚本：适配已有第5个 API 覆盖文件，新增测试在修复前失败，修复后11项通过，独立审查和只读生产预检通过。

生产 HTTP：`/hr/` 与主 JS/CSS 均200，字节摘要与新镜像和原生服务端外壳转换一致；匿名访问岗位/情报子路由返回401，符合鉴权约束。API readiness 为 ready 且 new_admission_enabled，数据库仍为 schema107/cloud。首次外网验证误将受保护子路由也预期为200而遇到401，修正的是验收脚本预期，没有更改应用授权。

浏览器：扩展能列出/新建标签，claim/goto 后读取超时，尚未完成真实滚动、窄屏和视觉验收。没有把静态审查、CSS 代码或匿名外网资源核对称为浏览器通过。

本次纯页面改动，未重复执行无关业务 API/数据库全量回归、进程故障或真实模型业务验收。前次发布记录中的真实账号、逐字输出等未完成项继续保留。

本地证据在 `artifacts/2026-09-14-hr-launch/ui-restoration/`，其中 `branch-audit.json` 为分支溯源、`final-verification.json` 为最终生产与外网检查。
