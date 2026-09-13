# `482a43f` HR 上线合并独立审核

审核对象固定为 `482a43fc34123df73c4c85a46acd63f7139e343f`（tree `f48b7a16d24c25c11f4dab2f50a6893eedd61278`），基线为 `b184b57ec09828033ef127ddf34dd748794325dd`。该对象的父提交为 `a7d9b5c45b063bada711dea43d305736630ad1dc` 与当前 `origin/master` 的 `35ab8d63d58817f0a9235fbb4d650703d00864fc`；`a7d9b5c` 已合入线上能力提交 `65e7fbd14a3cbe99883b0b31e31b5705d183d1f9`。审核只读 Git 对象和已给出的既有测试证据，没有读取移动 HEAD 的源码作为结论依据，没有运行测试、生产、模型或浏览器，也没有修改源码。

## 结论

发现 **1 个 Important 合并回归**，对象不应原样作为上线源码；未发现 Critical。除该前端集成缺口外，线上 `65e7fbd` 的岗位完整工作流、历史成果读取、情报 source/research 两层 API/页面和相关授权边界均在对象中；基线的完整岗位/草稿/幂等实现也保留在 `HrPositionIndex` 组件本身。没有业务测试文件或断言被删除。

## Important：真实岗位目录路由丢失“在主对话中继续”

- `482a43f:webui/src/workspaces/hr/HrWorkspacePage.tsx:98` 以 `<HrPositionIndex account={props.account} api={api}/>` 挂载岗位目录，未传入基线 `b184b57` 原有的 `onSelect={value=>{choose(value);navigate(chatHref);}}`。
- `482a43f:webui/src/workspaces/hr/HrPositionIndex.tsx:244` 仍以 `onSelect && ...` 条件渲染“在主对话中继续”。因此组件直接注入回调时功能存在，经真实 `/hr/positions` 路由挂载时入口消失。
- 影响是用户无法从岗位目录选择 active 岗位并带着未发送草稿回到已知主对话；独立 `/hr/agent?position=...` 链接与岗位详情工作流不能替代这条既有动作。`HrPositionWorkflow` 在 `HrWorkspacePage.tsx:97` 的挂载本身正确，应在保留它和共享 `api` 的同时恢复该回调。
- 原 29 项绿色组件证据没有捕获该问题：`HrPositionIndex.test.tsx` 的“retains production selection into the main conversation”直接传入 `onSelect`，当时 `HrWorkspacePage.test.tsx` 没有覆盖岗位目录选择的集成路径。
- 审核期间主任务已在对象外先补 RED 再修复并得到 30 项 green（`runs/merge-route-red`、`runs/merge-route-green`）；这些是后续修复证据，不改变 `482a43f` 自身含有回归的结论，需绑定新准确提交再复审。

## 其余核对

- 路由与授权：`GET /api/v1/hr/positions/{position_id}/results` 进入 `_HR_POSITION_ROUTES`，HTTP 层依赖 `require_hr_access`；服务查询同时限制 owner、岗位、`hr.submit_result` 和 turn 的冻结 `positionId`，并再次读取授权 turn scope。三个 source 路由同样先执行 HR 授权，响应加 `private, no-store`。未见新增匿名访问、跨 owner 放行或 mutation 只读降级。
- 生产后端承接：`tool_routes.py`、`tool_service.py`、`HrPositionWorkflow.tsx` 及其测试与 `65e7fbd` 的 blob 完全一致；`authorization.py` 在生产路由基础上并集保留云端 Agent 路由和 source 路由，没有删项。
- 岗位组件冲突：合并结果保留基线草稿确认/合并/忽略、明确合并目标、自然语言新建、失败重试和跨 remount 幂等；并增加线上内部状态筛选、归档只读工作流链接、工作流阶段提示、重复分页游标拒绝和取消后的状态保护。归档岗位的“在主对话中继续”仍按 active 条件禁用。
- 测试断言：相对基线，相关测试只有新增行；相对线上源库测试仅在 catalog 精确期望中加入 `overview: null`，与 `SourceLibrary.catalog()` 对缺少 aggregation 的 fixture 返回相符，没有把字段或失败校验删掉。既有证据记录 14 个相关后端测试 green、29 个相关前端组件测试 green 和 TypeScript green；本审核没有重复执行，且前端 green 存在上述集成覆盖缺口。
- 文档：两份根 HR 文档保留基线较新的云端实施、权限、恢复及验收语义，并新增 2026-09-13 直接上线授权、飞书排除、历史只读提取/去标识回放及承接 `65e7fbd` 说明。`docs/runbooks/2026-09-08-hr-web-core-handoff.md` 同时保留两层情报/研究/聚合记录和 `35ab8d6` 的首次发起入口记录；基线至对象无删除文件，`git diff --check` 无输出。

## 固定证据

- `b184b57-to-482a43f.patch` 是 `git diff --binary --full-index --no-ext-diff b184b57... 482a43f...` 的完整输出，SHA-256 为 `9b7e06f81962605691e59d2b340235a38be06372fe5fd84e7b39dc459c7ce452`，7,735,388 bytes / 3,070 lines。
- `source-fingerprints.json` 保存审核对象、tree、父提交、基线、生产来源，以及关键源码/测试/文档的 Git blob 与 SHA-256。所有源码指纹均直接由 `git cat-file blob 482a43f:<path>` 计算，不来自当前工作树字节。

