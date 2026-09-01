# FAE 反馈与修复闭环云端投影设计

**日期：** 2026-09-01

**状态：** 产品方向已确认，等待书面规格复审

**涉及仓库：** `AI-Agent-Platform`

**依赖设计：**

- `docs/superpowers/specs/2026-08-31-fae-management-workbench-design.md`
- `docs/superpowers/specs/2026-08-03-feedback-fix-closure-design.md`

## 1. 背景与问题定义

FAE 管理工作台已经提供 `/admin/fae/issues`，并复用了 Platform 现有 Review
Workspace。该组件在完整数据模式下已经能够展示：

- 原始问题、原始答案与来源 Turn；
- 根因、影响范围与负责人；
- 修复合并和生产部署证据；
- 逐题真实复跑答案与独立语义复审；
- 由硬门自动计算的最终闭环状态。

生产环境采用云端加密只读副本。当前 `review_issue_projection` 只复制 Issue
标题、优先级、失败层、负责人、关联 Turn 数量和处置类型；
`ReplicaReviewRepository.get_issue_detail()` 明确把 `links`、`evidence`、
`replays` 和 `events` 返回为 `unavailable`。前端又在只读模式下主动清空
`root_cause`、`impact_scope` 和 lifecycle progress。

因此，生产页面只能看到 Issue 数量和标题，无法看到用户已经确认的“原始会话 →
根因 → 修复证据 → 真实复跑 → 修复结论”。这不是 FAE 没有做过闭环，也不是页面
组件缺失，而是云端投影合同丢失了闭环明细。

## 2. 事实来源与领域边界

本项目不新建第二套反馈或修复状态机。事实来源固定为：

```text
FAE 原始事实
  platform_read.feedback
  platform_read.turns
  platform_read.sessions

Platform 修复闭环事实
  platform_review.feedback_issues
  platform_review.feedback_issue_links
  platform_review.feedback_fix_evidence
  platform_review.feedback_replay_runs
  platform_review.feedback_issue_events

              ↓ 只读、结构化、脱敏投影

Cloud platform_replica.management_projections

              ↓

/admin/fae/issues
```

`platform_read` 只提供不可修改的原始反馈、Session 和问答事实；
`platform_review` 是根因、修复证据、复跑和最终闭环状态的唯一事实源。云端副本
只负责读取和展示，不成为新的修复事实源。

## 3. 目标与非目标

### 3.1 目标

1. 生产 `/admin/fae/issues` 能查看完整的反馈修复证据链。
2. 每个 Issue 能下钻到原始 FAE Session 和具体 Turn。
3. 根因、工程证据、真实复跑与最终结论由结构化数据驱动，不使用 Mock 或硬编码。
4. 云端继续只读；任何治理写操作仍只发生在具备 writer 的受控环境。
5. 混入错误 Agent 作用域的历史记录不显示、不参与统计，也不能拖垮全部合法记录。
6. 新旧投影在滚动发布与回滚期间保持向后兼容。

### 3.2 非目标

- 不嵌入 FAE 原生 `/app/review` 页面；
- 不新增 FAE 专属 Review 数据库或状态机；
- 不把原始客户身份、原始 Session ID、附件原件或完整审计事件复制到云端；
- 不在云端执行复跑、语义评测或 Issue 写操作；
- 不修改 FAE 应用、FAE 数据库、FAE 容器或 `fae.orbbec.com.cn`；
- 不在本项目同时实现分析报告页；
- 不把修复闭环改造成新的 BI 报表。

## 4. 方案选择

### 4.1 采用：扩展现有结构化只读投影

扩展 `ReviewIssueProjection`，在同一个签名同步批次中复制经过作用域校验和文本
清洗的 Issue、原始事实、工程证据、复跑结果与 progress。云端继续通过现有
`ReplicaReviewRepository` 和 Review Workspace 呈现。

该方案保留现有权限、路由、组件和证据语义，同时避免云端对本地数据库的实时依赖。

### 4.2 不采用：把 FAE Review Center 嵌入 Platform

iframe 或外链会形成第二套身份、导航和权限边界，也无法显示 Platform 后续形成的
合并、部署和独立复审证据。

### 4.3 不采用：生产请求实时回源本地 Platform/FAE

实时回源会让云端页面依赖本地 Mac、SSH tunnel 和本地数据库可用性，违背现有
云端只读副本的可用性与隔离目标。

## 5. 结构化投影合同

### 5.1 Issue 摘要

`review_issue_projection` 保留现有字段，并新增：

- `origin_turn_key`：稳定哈希后的来源 Turn key；
- `secondary_layers`：经过标识符白名单校验的次要失败层；
- `root_cause`：经过既有文本清洗后的根因；
- `impact_scope`：经过既有文本清洗后的影响范围；
- `fix_ready`：是否已经记录修复准备事实；
- `progress`：使用既有 Review 硬门算法计算的 lifecycle 状态、缺失门、复跑通过数、
  应复跑数和 reopened 标记；
- `links`、`evidence`、`replays`：下述只读结构化明细；
- `detail_schema_version`：固定整数 `1`，用于区分旧摘要投影与完整明细投影。

Issue UUID 继续作为现有详情路由的稳定 ID。它不是 Session 或客户身份，不重新生成。

### 5.2 原始事实 links

每个 active 或历史 link 投影以下字段：

- 稳定哈希后的 `id`、`source_turn_key`、`source_session_key`、
  `source_feedback_keys` 和 `source_trace_key`；
- `link_role`、`active`、`source_turn_index`、`source_created_at`；
- 经过文本清洗的 `source_question` 与 `source_answer`；
- `source_outcome` 与 `source_fallback_used`。

不复制 `source_details`、完整前序 `source_context` 或附件内容。需要上下文时，Owner
通过 `source_session_key` 打开 `/admin/fae/sessions/:session_key`，继续使用现有
特权读取和审计边界。

### 5.3 工程证据 evidence

复制所有与当前 Issue 关联的修复证据，字段限定为：

- 稳定哈希后的 evidence `id`；
- `evidence_type`、`repository`、`reference`、`version`；
- `commit_sha`、`release_manifest_ref`、`environment`；
- `verification_status`、`observed_at`、`observed_by`。

`verification_details` 只参与本地 progress 计算，不原样复制。云端返回空对象，避免把
验证器输出、路径或内部运行细节扩大到云端数据面。
现有 `FixEvidence` 响应契约要求的 `url` 字段固定返回空字符串；不会为了兼容
响应类型而复制内部仓库、CI 或部署系统的可点击 URL。

### 5.4 真实复跑 replays

复制每次复跑的最小可读结果：

- 稳定哈希后的 replay `id`、`issue_link_id` 和 `trace_id`；
- `attempt_no`、期望/实际版本和 Git SHA；
- 配置模型、实际模型、耗时与执行时间；
- 经过文本清洗的真实 `answer`；
- `execution_status`、`runtime_gate`、`runtime_failure_reason`；
- `semantic_verdict`、`review_method`、`reviewer`、`review_reason`；
- `started_at` 与 `completed_at`；
- `sources` 只保留经清洗的 `title`、`name` 或 `reference` 展示字段，不复制未知嵌套字段、
  URL 查询参数或原始 Provider payload。

`done`、`context_snapshot`、`attachment_manifest` 和上游原始事件不进入云端投影。

### 5.5 审计事件

首版不复制 `feedback_issue_events` 的完整内容。事件只在源端用于恢复 previous status
并计算 `reopened`；云端详情继续标记审计时间线为 `unavailable`。这是明确的数据最小化
选择，不影响根因、修复证据、复跑和最终结论的展示。

## 6. 状态计算

云端不得根据字段存在与否猜测 lifecycle。源端读取同一 repeatable-read 快照后，调用
与 `PsycopgReviewRepository` 相同的纯函数计算 progress。

为避免两套状态算法漂移，将“原始 Issue + links + evidence + replays + previous status
→ `IssueProgress`”抽成 Review 领域内的纯函数：

- 本地 writer/read repository 继续调用该函数；
- cloud exporter 调用同一函数；
- 云端 repository 只消费已经计算好的 progress，不重新推导。

任何硬门规则变更必须先修改这个唯一函数及其测试，再提高投影 schema version。

## 7. 作用域隔离与历史异常记录

现有 `REVIEW_ISSUE_SQL` 已校验 origin turn、active links、反馈 keys、replay links、
canonical issue、历史移动事件和 canonical cycle。该校验继续是明细能否导出的硬门。

云端读取时按记录隔离：

1. `scope_valid=true` 的记录进入列表、统计、详情和 Turn governance；
2. `scope_valid=false` 或缺少 marker 的记录完全隔离，不返回任何标题或明细；
3. 同一 Agent 同时存在合法和异常记录时，合法记录继续可读；
4. 某 Agent 只有异常记录时，相关 Review 读取失败关闭；
5. 某 Agent 没有 Issue 时属于正常空集，不视为作用域失败；
6. 直接请求被隔离 Issue UUID 返回 404，不能通过错误差异探测其内容。

投影健康信息只返回异常记录数量，不返回异常记录 ID、标题或来源。FAE 页面显示：
“部分历史异常记录已安全隔离，未计入当前结果”，避免静默少算。

## 8. 隐私、加密与权限

- 源端查询继续使用 read-only、repeatable-read 事务和 30 秒 statement timeout；
- 所有自由文本继续通过现有 `sanitize_text` 归一化并剥离凭据；不在本项目中
  暗中改变已有管理员副本的业务正文保真策略；
- Session、Turn、Feedback、Link、Evidence、Replay 和 Trace 标识使用现有 32 字节
  identity key 派生稳定 ID；
- 不复制 `user_identity`、provider user ID、native Session ID 或其他结构化客户身份字段。
  原始问答正文可能包含业务上下文中的人名、联系方式或客户描述，它们沿用当前
  Owner/Admin 限定的加密副本边界；如需进一步 PII 脱敏，必须作为独立策略变更评审，
  不得在 exporter 中临时增加不可见的信息损失；
- 投影 JSON 继续由 `FieldCipher` 字段级加密，并由签名批次传输；
- `platform_replica.management_projections` 仍是云端唯一保存位置；
- `/api/admin/fae/*` 继续执行 Platform Owner/Admin 后端权限门禁；
- 云端 `write_available=false`，前端不渲染创建、编辑、验证、复跑、关闭或合并按钮；
- 只有 `/admin/fae/sessions/:session_key` 的既有特权接口可以返回完整 Session，并继续
  写读取审计。

前端隐藏不是授权边界；所有 API 仍由后端固定 `agent_id=ai-fae-agent` 和
`source_kind=fae`。

## 9. 查询与同步实现

源端在一个 repeatable-read 事务中执行批量查询：

1. Issues 与现有 `scope_valid`；
2. Issue links + `platform_read.turns`；
3. Fix evidence；
4. Replay runs；
5. 每个 Issue 最近一次记录 lifecycle status 的事件。

查询按 `issue_id` 分组后构建 `ReviewIssueProjection`。禁止按 Issue 逐条查询，避免
N+1 和同步时长随 Issue 数量线性放大数据库往返。

同步顺序固定为：

```text
先部署能接收新字段的云端代码
  -> 暂停一次定时同步窗口
  -> 推送 detail_schema_version=1 的新批次
  -> 验证新鲜度与详情
  -> 恢复定时同步
```

旧云端读取器会忽略已存 JSON 的新增字段；旧 importer 不接受新字段。因此回滚后必须暂停
新版 exporter，恢复兼容 exporter 后再继续同步。

## 10. API 与前端行为

### 10.1 API

`GET /api/admin/fae/issues/:issue_id` 在完整投影下返回：

- `issue.root_cause`、`issue.impact_scope`；
- 非空或真实空数组的 `links`、`evidence`、`replays`；
- `availability.links/evidence/replays = resolved`；
- `availability.events = unavailable`；
- 可用的 lifecycle `progress`；
- `replica_read_only = true`。

旧摘要投影仍按原合同返回字段不可用，不伪造空证据。

### 10.2 前端

- FAE 工作台标签由“问题治理”改为“反馈与修复”；
- 页面主标题继续使用“反馈修复闭环”；
- 只读副本不再因为 `replica_read_only` 无条件清空 root cause 和 progress；
- 字段真实缺失时仍显示“暂不可用”，不能用空字符串伪装成未填写；
- “原始事实”增加“打开原始 Session”链接；
- 工程证据、真实复跑答案、语义复审结论继续复用现有组件；
- 审计时间线明确显示“源端保留，云端未复制”；
- 被隔离记录数量大于零时显示安全隔离提示。

列表筛选首版保持现有云端 disposition 口径，列表与详情可以展示 lifecycle progress；
不在本次同时重做双维度筛选器。

## 11. 错误与兼容行为

- 投影旧版本：摘要仍可读，明细明确 unavailable；
- 单条明细文本清洗失败：整条 Issue 不进入导出批次，不输出半份证据；
- 单条作用域失败：仅隔离该记录；
- 全部记录作用域失败：Review 分区失败关闭，Sessions 分区继续可读；
- 同步失败：保留上一成功代际，并显示既有数据截止时间；
- 记录引用不存在的 Session：不生成 Session 深链，原始问答仍按投影可读；
- evidence/replay 与 Issue 或 Link 归属不一致：Issue `scope_valid=false`；
- 新字段超出 importer allowlist：整个批次拒绝，不替换上一代数据。

## 12. 测试与验收

### 12.1 后端 TDD

- SQL 在同一快照批量读取 Issue、Link、Evidence、Replay 和 previous status；
- progress 与现有 Review repository 对相同 fixture 逐字段一致；
- sanitizer 清洗全部自由文本并稳定哈希所有来源标识；
- store 只接受精确的新字段集合，未知嵌套字段不会进入云端；
- repository 返回完整详情并正确设置 section availability；
- 旧摘要投影仍兼容；
- 合法与异常记录混合时只返回合法记录；
- 全异常、空集和直接访问异常 UUID 的行为分别符合第 7 节；
- 云端 repository 不暴露任何 mutation 方法。

### 12.2 前端 TDD

- 完整只读投影展示根因、影响范围、原始问答、工程证据和真实复跑；
- 原始 Session 链接使用投影后的 `source_session_key`；
- 只读模式不出现任何治理写按钮；
- 旧摘要投影显示 unavailable，不显示伪造的“尚未填写”；
- 安全隔离提示只在数量大于零时出现；
- 导航、标题和概览入口统一使用“反馈与修复”。

### 12.3 生产验收

1. 所有 `platform-*` 容器镜像 SHA 与 `/opt/orbbec-agent-platform/current` 一致；
2. 云端同步代际小于新鲜度阈值；
3. `/admin/fae/issues` 能打开任一合法事项；
4. 详情能看到原始问题、原答案、根因、至少一类工程证据或明确空集、复跑结果或明确空集；
5. 原始 Session 深链进入正确 FAE Session；
6. 只读页面没有创建、编辑、复跑、验证、合并或关闭入口；
7. 被隔离记录无法通过列表、搜索、详情 UUID 或 Turn summary 探测；
8. `ai-fae-backend` Container ID、Image ID、StartedAt、RestartCount、Config hash 和
   Mounts hash 全部不变；
9. `https://fae.orbbec.com.cn/` 和 `/office/` 保持原状态；
10. 回滚到上一 Platform release 后，旧摘要读取仍可用且新版 exporter 已暂停。

## 13. 发布与回滚

发布只修改 Agent Platform。顺序为：

1. 记录当前 Platform、FAE、Office、Nginx 和同步代际事实；
2. 部署包含兼容 importer/reader 的完整 Platform release；
3. 确认所有 Platform 容器运行同一 release SHA；
4. 运行一次新版同步；
5. 使用真实 Owner 身份完成第 12.3 节页面验收；
6. 恢复定时同步。

任一门禁失败时，暂停新版 exporter，恢复上一完整 Platform release 与匹配的环境文件，
保留数据库上一成功代际。回滚不得修改或重启 FAE，也不得修改行政业务数据。

## 14. 完成定义

只有同时满足以下条件，本项目才算完成：

- FAE 工作台展示的不是 Issue 摘要，而是可追溯的反馈修复闭环；
- 页面使用真实结构化投影，无 Mock、无硬编码、无 iframe；
- 原始事实与 Platform 修复事实各自保持唯一事实源；
- 云端只读、作用域隔离、稳定标识和加密边界全部通过自动化测试；
- 真实生产数据同步、权限、页面、FAE 不变性和回滚均完成验收。
