# P1/P2 岗位页云端读取前端报告

## 实现结果

- 岗位详情通过后，用按 CSRF token 记忆化的 `createHrLoopApi` 读取云端当前标准和岗位范围成果；成果目录读取至全部 cursor 页面，再逐条按准确 ref 读取正文。
- 当前标准只来自云端 `standards/current`。只有 `404 not_found` 表示尚无当前标准；确认时间显示 `confirmed_at`，UUID revision 不展示。
- 当前成果按五阶段 kind 分组；`research` 不进入五阶段。正文以 Markdown 展示，并使用同一准确 ref 下载。后来关联、正文 `objects` 不含当前岗位的合法成果不会被拒绝。
- 旧 context 的 current 与 superseded、旧 results 都进入明确的只读历史区域；旧成果使用 `readOnly + referenceOnly`，不提供确认或执行。候选人旧组件继续使用旧 contextVersionId。
- 云端标准、云端成果和旧历史分别显示错误。401/403 关闭岗位受保护内容；普通失败不显示 0 或空态。owner、CSRF、position 和 refresh 都会建立新读取 epoch，晚到读取与下载不会恢复旧范围内容。
- 保留五阶段、官网原文、候选人和材料组件以及普通“在主对话中推进”草稿入口。未新增跨路由 exact-ref 交接状态。

## TDD 与验证

- RED：`npm test -- --run src/workspaces/hr/HrPositionWorkflow.test.tsx`，4 项中 2 项按预期失败：页面仍只显示旧成果；云端准确正文/下载入口不存在。
- GREEN（最终聚焦）：`npm test -- --run src/workspaces/hr/HrPositionWorkflow.test.tsx src/hrLoopApi.test.ts`，2 个文件、14 项全部通过。
- 构建：`npm run build`，TypeScript 与 Vite 构建通过。仅有既有的大 chunk 提示。
- 完整前端回归：`npm test -- --run`，132 个文件通过、2 个条件跳过；1197 项通过、2 项跳过。输出仅含既有 jsdom `scrollTo` / localStorage 提示。

## 范围与限制

- 浏览器合成边界验收确认：总览可访问历史，旧 current/history 去重，主容器可滚动且无横向溢出；准确成果下载生成 1586 字节 Markdown。该夹具不是受认证生产验收。
- 本提交不含后端、产品文档、预览夹具、导航、候选人行为或 P3/P4 改造。

## 2026-09-14 最终审查修订

本节替代上文关于旧数据只读历史和旧候选人组件的描述。用户最新决定旧 UI 直接退出岗位页：页面不再请求或展示旧 context、旧 results、旧候选人组件；候选人阶段只显示当前 `candidate_assessment` 成果，并保留进入主对话的普通草稿入口。没有删除数据库数据。

- 组件清理会同时 abort 旧请求并使统一 epoch 失效，卸载、owner/CSRF/position/refresh 变化后的晚到下载不能创建 Blob URL 或点击下载。
- 任一云端分页、标准、成果正文或下载请求返回 401/403 都会立即使统一 epoch 失效并清空受保护视图。即使同批另一个正文先以 503 结束聚合，后来到达的 401/403 仍会被逐请求观察。
- 移除成果 basis 展示及专用样式；该能力属于后续阶段。
- 新增完整 kind 映射断言：requirements 四类、sourcing、candidate_assessment、interview 两类、retrospective，且 research 不归入五阶段。

验证证据：

- 新增测试第一次运行：13 项中 11 项通过、2 项失败；失败原因是测试在岗位总览断言仅在 JD/JR 阶段展示的标准正文，修正夹具交互后 13/13 通过。生命周期实现缺陷来自独立审查的确定性代码路径分析；未把夹具错误冒充功能 RED。
- 最终聚焦：`npm test -- --run src/workspaces/hr/HrPositionWorkflow.test.tsx src/hrLoopApi.test.ts`，2 个文件、20 项全部通过。
- 最终构建：`npm run build`，TypeScript 与 Vite 构建通过；仅有既有的大 chunk 提示。
