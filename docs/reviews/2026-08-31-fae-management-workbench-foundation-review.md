# FAE 管理工作台 Foundation Release Review

**日期：** 2026-08-31
**复审范围：** `19703dfbc9b5a60c6f5d4302bcffa1fe5bc9efe8..796433a26589a531ee945d7453242d917c53055e`（28 个 Tasks 1–9 实施提交）
**Task 10 基线：** `796433a26589a531ee945d7453242d917c53055e`
**结论：** Foundation 的代码、自动化回归和静态上线契约满足进入部署流程的条件；本次复审没有部署、没有调用生产环境，也不构成生产验收。

## 自动化结果

| Gate | 最终结果 |
|---|---|
| Task 10 focused backend | `456 passed` |
| Canonical full backend | `3485 passed, 2 skipped, 180 warnings`，284.38 秒 |
| Cloud focused backend | `32 passed, 1 warning` |
| 权限、审计、新鲜度、路由证据矩阵 | `20 passed, 1 warning` |
| Full frontend | `69 files passed; 587 tests passed` |
| Frontend 路由/工作流矩阵 | `8 files passed; 89 tests passed` |
| Production frontend build | PASS；TypeScript 与 Vite 完成，3511 modules transformed |
| Python compile | `.venv/bin/python -m compileall -q app tests` PASS |
| Shell syntax | `bash -n deploy/cloud/accept.sh` PASS |
| Embedded report-check JavaScript syntax | `/opt/homebrew/bin/node --check -` PASS |
| Diff whitespace | `git diff --check` PASS |

两个 backend skip 均为显式 opt-in 的本机依赖，而不是产品测试失败：

- `test_full_migration_chain_preserves_least_privilege`：未配置隔离数据库和 Flywheel migrations；
- `test_opt_in_source_provenance_matches_manifest`：未设置 `AI_NOTES_SOURCE_ROOT`。

第一次 canonical backend 运行得到 `3482 passed, 2 skipped, 1 failed`。唯一失败是既有
OAuth rate-limit 测试跨越日历分钟边界：诊断探针记录到第一条请求发生于
`23:14:59.855+08:00`，其余 3000 条发生在下一分钟，数据库正确形成 `[1, 3000]` 两个
minute bucket。最终 acceptance test 状态的第一次 canonical run 又在同一既有测试文件的
callback ceiling 断言出现 `[(True, 1201)]`。该 test 单独连续运行 6 次均通过；EXPLAIN 证明
LATERAL correlation 没有被 optimizer 消除。临时探针将原查询精确安排在
`23:44:59.990+08:00..23:45:00.136+08:00`，复现相同 `[(True, 1201)]`，并记录两个
version-43 bucket 的计数为 `26` 和 `1175`（合计 1201，两个 bucket 均未超过 1200）。
相关 FAE commit range 和 Task 10 diff 均没有修改 rate-limit 实现、migration、fixture 或测试；
临时探针已删除，未作越界产品修改。随后在完全相同的最终代码/测试状态运行 canonical full
backend，得到上表的零失败结果。

## 权限与云只读边界

- `test_router_itself_allows_exact_management_contexts`、
  `test_owner_and_admin_can_read_fae_workbench` 覆盖 Owner/Admin allow；
  `test_router_itself_rejects_missing_member_and_viewer_contexts`、
  `test_fae_workbench_rejects_unauthenticated_member_and_viewer` 覆盖未登录 401 与
  member/management_viewer 403。
- `test_hard_stale_owner_and_admin_keep_read_access` 证明 hard-stale 时 Owner/Admin 读仍可用；
  `test_fae_issue_mutation_denials_report_hard_stale_before_cloud_read_only` 证明 hard-stale 写先
  503，fresh cloud replica 写为 403/`cloud_review_read_only`。
- `test_fae_issue_facade_exposes_exact_route_templates` 固定了全部 FAE Issue read/mutation
  route matrix。服务和 API scope 测试证明 Agent/Source 由服务端固定为
  `ai-fae-agent`/`fae`，浏览器值不会进入写入边界。
- `deploy/cloud/accept.sh` 的 active v2 acceptance path 复用既有 mode-0600 Cookie、Origin、
  CSRF config helper，检查 Owner 页面 `/admin/fae`、`/admin/fae/sessions`、
  `/admin/fae/issues`，以及 Owner API overview、bounded Sessions、Issues 均为 200/合法 JSON；
  同时检查 member 直接页面/API 为 403，并用 Owner CSRF 请求证明 FAE mutation 返回精确
  403/`cloud_review_read_only`。每次 curl 都先保留 transport exit status，再比较 HTTP status；
  即使 curl 已打印预期 status，非零 transport exit 也会 fail-closed。脚本不输出 Cookie、
  CSRF 或 token。
- Config schema v2 只有 member 与 Owner 凭据，没有 management-viewer 凭据。因此本次只在
  concrete backend authorization/API tests 中验证 viewer 403；没有配置或执行 production
  live viewer probe，也不声称做过该项生产验证。

## Privileged Session read audit

`test_fae_detail_records_privileged_read_without_content_or_raw_key` 证明 Session Detail 在读取前
写入 requested event，成功后写入 completed event；target 是 canonical key 的 SHA-256，事件中
没有原始 Session key、问题或回答。参数化的
`test_fae_detail_fails_closed_when_required_audit_is_unavailable` 覆盖 requested/completed 两个
audit append 失败点，均返回 503，响应不泄露 Session 内容；requested append 失败时不会读取
Session。

## 每日同步与新鲜度

`test_overview_composes_available_operational_and_review_sections` 固定 Asia/Shanghai 七个完整日的
聚合区间和最新成功同步时间。`test_overview_marks_old_operational_data_stale`、
`test_overview_marks_missing_operational_data_as_unavailable` 与
`test_overview_treats_exactly_36_hour_old_data_as_fresh` 固定 36 小时阈值：超过阈值为 stale，
缺失为 unavailable，边界值仍为 fresh。Frontend tests 断言概览和详情展示数据截止时间，且
页面不宣称“实时”。

## Feedback 与无 Feedback 的同一 Issue 工作流

- 有 Feedback：`FaeSessionDetailPage.test.tsx` 同时载入负 Feedback Turn 与普通 Turn，两个
  Turn 都生成同一 `/admin/fae/issues?session_key=...&turn_key=...` 治理入口；
  `ReviewWorkspace.test.tsx` 用含 `feedback_keys` 的 FAE inbox seed 覆盖 create/link、稳定 Issue
  URL、详情/列表/概览刷新和成功状态。
- 无 Feedback：`FaeIssuesPage.test.tsx` 从真实 scoped Session deep link 建立 seed，并断言
  `source_feedback_keys: []` 仍经过相同 create-then-link workflow；backend 的
  `test_fae_link_accepts_real_turn_without_feedback` 和 service 同名测试证明真实 Turn 存在检查后
  可进入同一 Review state machine，Agent scope 仍由服务端覆盖。

两条路径共享 `ReviewWorkspace`、FAE Issue facade、Review service 和既有状态机，没有第二套
Session、Feedback 或 Issue truth source。

## Generic Sessions / Review 回归

- `SessionsPage.test.tsx` 保留 `/admin/sessions` URL、分页、筛选与 canonical detail link，并把
  FAE-only filter 从 generic URL 中规范化移除。
- `ReviewPage.test.tsx` 保留 `/api/review/*`、legacy actor field、`?issue=`/Agent-scoped URL、
  inbox-to-existing-Issue link、replay 答案和无 force-close 行为。
- Full frontend 的 587 tests 与 Task 10 的 89-test route/workflow matrix 均通过。

## 报告状态与内容扫描

Foundation 仍只展示“分析报告尚未接入”。`FaeReportsPlaceholderPage.test.tsx` 覆盖 collection
和 detail placeholder；cloud acceptance 先要求 `/admin/fae/reports` 为 200/有效 SPA HTML，
再使用 Owner browser Cookie 通过本机 Chrome/CDP 渲染该路由，要求精确 placeholder 文案，
且 workbench content 不含 `article`、`table`、`data-report-id` 或 `sample report`、
`demo report`、`fixture report`。该检查使用本机既有 `/opt/homebrew/bin/node` 绝对路径，先验证
executable，并对内嵌 JavaScript 做了独立 syntax check。精确内容扫描还证明：

- FAE production 页面/组件没有“实时”、fake/sample report 或强制关闭控制；命中的“实时”
  仅为 tests 中的 negative assertions；
- `faeWorkbenchApi.ts` 的 `agent_id` 命中仅来自 response normalization，以及共享 generic
  Review payload 的 defensive strip；API tests 证明该字段不会序列化。`source_kind` 不会由
  FAE client 序列化；
- backend 命中是 immutable scope constant、查询约束、response validation 与 server-side
  override，不是 caller-supplied scope。

**分析报告接入仍待第二个实施计划完成。** Foundation 没有报告 API/read model integration，
没有 FAE producer contract/fixture，也没有用样例报告冒充生产分析结果。

## 未执行事项与已知非阻塞告警

- 没有部署、远程 SSH、生产 curl、生产 mutation、真实 Cookie 使用或合并；cloud acceptance
  仅做了代码 contract test 和 shell syntax verification。
- Vitest/jsdom 输出既有 `Window.scrollTo()` 未实现提示；全部 tests 仍通过。
- Vite 输出既有大 chunk warning；production build 成功。
- FastAPI/Starlette 输出既有 deprecation warnings；最终 backend 为零失败。
