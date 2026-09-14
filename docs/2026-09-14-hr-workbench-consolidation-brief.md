# 任务书：HR 智能工作台代码收敛

- 日期：2026-09-14
- 执行者：Codex
- 授权范围：任务 A 可直接执行；任务 B 必须先交方案，等人确认后再改代码

---

## 0. 执行前必读：这个仓库有三份不同的 HR 代码

**在动任何代码之前先确认你在哪个分支。这一步搞错，后面全部作废。**

| 位置 | 状态 |
|---|---|
| **生产代码** = 分支 `feat/hr-cloud-loop-launch`，worktree 路径 `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release`，tip `859480b9` | **所有工作在这里做** |
| `master`（`48fa5860`，即仓库根目录 `/Users/neo/Developer/work/AI-Agent-Platform` 当前签出的分支） | HR 前端**落后生产三次发布**。不要以它为参照，不要照它改 |

三个已发布生产、但**没有合入 master** 的提交：

- `544ec07c feat(hr): organize intelligence into source and analysis layers`（2026-09-11 情报页两层）
- `7ad14f95 feat(hr): organize source intelligence around company aggregates`（2026-09-11 资料层改为公司聚合）
- `65e7fbd1 feat(hr): restore position workflow and saved result reading`（2026-09-12 岗位工作流）

规模差异：HR 前端 master 3,323 行 / 启动分支 7,303 行；后端 `backend/app/hr` 与 `backend/app/api` 两分支相差 104 个文件、+9,819 / −5,518 行。

**已核实的具体例证**（用来判断你是否在正确分支）：
`backend/app/control_plane/authorization.py` 的 panorama 白名单，master 上写成 `/api/hr/panorama/reports/{publication_id}`，而实际路由模板是 `{bundle_id}`，会导致报告详情、导出、证据下载全部 403 `route_not_authorized`；启动分支该处已是正确的 `{bundle_id}`（`authorization.py:231-235` 对应 `backend/app/hr/panorama_routes.py:192,197,220`）。
**这是 master-only 的问题，不是生产缺陷，不要去"修"它。** 它只用于证明 master 已过时。

进入正确目录：

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release
git rev-parse --abbrev-ref HEAD   # 必须输出 feat/hr-cloud-loop-launch
```

---

## 1. 当前的产品问题（已核实的诊断，不要重新推测）

用户的原话是「HR 智能工作台设计太杂乱，根本不能正常使用」。机制已经查明如下。

### 1.1 两套顶层工作台同时挂在 App.tsx 上

`webui/src/App.tsx:118-127`：

| 路由 | 承载组件 |
|---|---|
| `/hr/`、`/hr/agent`、`/hr/chat` | `HrLoopWorkspace`（新云端链，1,369 行） |
| `/hr/positions` | `HrWorkspacePage cloudPrimary`（legacy） |
| `/hr/positions/:id` | 同上 |
| `/hr/positions/:id/:section` | 同上 |
| `/hr/positions/:id/conversations/:cid` | 同上 |
| `/hr/conversations/:cid` | 同上 |
| `/hr/panorama` | 同上 |
| `/hr/panorama/reports/:id` | 同上 |

### 1.2 legacy 那一半被 `cloudPrimary` 变成了「只读博物馆 + 弹回主对话」

`cloudPrimary` 不是配置开关，而是在 `App.tsx` 里对上述 7 条路由**硬编码为 `true`**。它在 `webui/src/workspaces/hr/HrWorkspacePage.tsx` 里的效果：

- 对话被强制只读：`readOnlyReason="历史对话仅供查看，请从主对话开始新的工作。"`
- `fill()` 在 `cloudPrimary` 下拒绝一切带成果引用、候选人、材料的动作，只报一句
  「历史成果仍可查看。请在主对话中重新选择候选人、材料或成果，再继续工作」，然后调 `openHrWork()` 跳回 `/hr/`
- `HrPositionIndex` 的 `onSelect` 在 `cloudPrimary` 下直接 `openHrWork()` 跳走，点岗位进不去岗位
- 选情报、选方法（`selectIntelligence`、`HrKnowledgePanel`）同样立即跳走

`openHrWork()` 定义在 `webui/src/workspaces/hr/hrCloudLaunch.ts`，作用就是把一份未发送草稿暂存在模块变量里，然后 `navigate` 到 `/hr/`。

**所以用户体验是**：点顶部「岗位」或「HR 情报」进去，看到的全是只读内容，任何想做事的动作都把他弹回主对话。这就是「根本不能正常使用」的确切机制，不是样式问题。

### 1.3 新的 HrLoopWorkspace 已经自带这些能力

`webui/src/workspaces/hr/HrLoopWorkspace.tsx` 的侧栏（`:633` 起）已包含：专业方法（`:657`）、候选人材料（`:669`）、候选人工作（`:674`）、公司与专题情报（`:683`）、岗位选择与「查看岗位资料」（`:1068`）、确认标准（`:1344`）、关联到所选岗位（`:1363`）。

也就是说 legacy 的岗位/情报/候选人界面**在功能上已被新链覆盖**，它们现在的唯一作用是承载历史内容的只读浏览。

---

## 2. 任务 A：删除零引用死代码

**授权：可直接执行。**

以下 4 个组件在启动分支上**没有任何生产代码引用**（只有各自的测试文件引用自己）。连同测试共 1,505 行。

| 文件 | 行数 | 原引用者（已在启动分支被删除） |
|---|---|---|
| `webui/src/workspaces/hr/HrPanoramaReport.tsx` | 507 | 原 `HrPanoramaWorkspace`；现已改为渲染 `HrSourceWorkspace` / `HrResearchWorkspace` / `HrLegacyPanoramaWorkspace` |
| `webui/src/workspaces/hr/HrPanoramaReport.test.tsx` | 502 | — |
| `webui/src/workspaces/hr/HrPositionProposalCard.tsx` | 154 | 原 `HrConversationOutcomePanel`，该文件在启动分支已不存在 |
| `webui/src/workspaces/hr/HrPositionProposalCard.test.tsx` | 150 | — |
| `webui/src/workspaces/hr/HrPositionHeader.tsx` | 29 | 原 `HrPositionWorkspace`，该文件在启动分支已被删除 |
| `webui/src/workspaces/hr/HrPositionHeader.test.tsx` | 75 | — |
| `webui/src/workspaces/hr/HrTaskReferences.tsx` | 15 | 原 `HrPositionWorkspace` 与旧版 `useHrChatPosition`（后者已缩到 51 行） |
| `webui/src/workspaces/hr/HrTaskReferences.test.tsx` | 73 | — |

### 2.1 删除前请自行复核（不要相信本文档，自己验证一遍）

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release/webui/src
for b in HrPanoramaReport HrPositionProposalCard HrPositionHeader HrTaskReferences; do
  echo "--- $b ---"
  grep -rn "from \"./$b\"\|from './$b'\|from \"../hr/$b\"" --include="*.tsx" --include="*.ts" . | grep -v "\.test\."
done
```

期望：四个都无输出。**按 import 路径匹配，不要按标识符名匹配**（原因见 2.2）。

### 2.2 两个陷阱

1. **zsh 会把未加引号的 `--include=*.tsx` 当通配符吃掉**，导致 `grep` 报 `no matches found` 或静默返回错误结果。必须写成 `--include="*.tsx"`。本次调查中这一点造成过一轮完全错误的"零引用"结论。
2. **`webui/src/hrPanoramaTypes.ts:149` 有一个同名的 `interface HrPanoramaReport`，`hrPanoramaApi.ts` 大量使用它。那是类型，不是组件，绝对不能删。** 按标识符名 grep 会命中它，所以 2.1 用 import 路径匹配。

### 2.3 验收

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release/webui
npm run build      # tsc -b && vite build，必须通过（会抓出遗留的孤儿 export/import）
npm test           # vitest run，必须通过
```

如果 `HrPanoramaReport.tsx` 里还导出了别处仍在用的类型（例如 `HrPanoramaComparison`），**不要因此放弃删除**：把该类型移到 `webui/src/hrPanoramaTypes.ts`，再删组件文件。

提交信息用一行祈使句，说明删的是什么、为什么无引用。

---

## 3. 任务 B：legacy 界面收敛

**授权：先交方案，不要直接改代码。** 这一项涉及用户可见行为，必须由人拍板。

### 3.1 要解决的问题

§1.2 描述的「只读博物馆 + 弹回主对话」。7 条 legacy 路由目前既不能用、又不能删得干净（它们承载历史内容浏览）。

### 3.2 请在方案里回答这几个问题

1. 「岗位」和「HR 情报」应该成为 `HrLoopWorkspace` 内的一等界面，还是继续作为独立路由？
   注意既有裁定：**岗位只组织材料，对话是主入口，纯咨询不得要求先建岗位**。
2. 7 条 legacy 路由各自的归属：并入新链、`legacy-redirect` 重定向、还是保留为明确标注的历史归档？
   注意用户既有指令：**「不要逐入口退出，没用的直接干掉」**——倾向一次性替换，不要留长期兼容层。
3. 哪些 legacy 组件因此可以删除？请给出文件清单和行数。当前 legacy 子树：
   `HrWorkspacePage`(116)、`HrPositionWorkflow`(77)、`HrPositionIndex`(248)、`HrPositionDetailsDrawer`(135)、
   `HrCandidateWorkspace`(425)、`HrCandidateAnalysisCard`(147)、`HrPositionContextPanel`(31)、
   `HrPositionResourcesPanel`(79)、`HrOfficialPositionPanel`(95)、`HrPositionActions`(12)、
   `HrPositionPicker`(91，注意 `HrLoopWorkspace` 也在用)、`HrTurnResults`(52)、`useHrChatPosition`(51)、
   `HrPanoramaWorkspace`(58)、`HrLegacyPanoramaWorkspace`(417)
4. 历史内容（已保存成果、已确认标准、历史对话、历史情报版本）在新方案下从哪里进入？
   既有裁定要求**同一成果可从对话、岗位、候选人三个入口进入**，且**深链接打开指定报告不能被默认入口截走**。
5. 现有测试哪些必须迁移、哪些随结构删除？先看 §5。

### 3.3 方案交付形式

一份 Markdown，放 `docs/`，包含：改动前后的路由对照表、要删的文件清单、要迁移的测试清单、以及每一步的「旧界面何时停止承载流量」。最后一项是硬要求——项目有明确裁定，重构必须写清 legacy 退出条件，否则就是叠加。

---

## 4. 不要做的事（项目既有裁定，违反即打回）

这些不是建议，是已经拍过板的约束。改动 HR 界面时逐条对照。

1. **页面按钮只能填入用户可见可编辑的草稿，不能提交隐藏的第二份指令。**
   同时，**只插一句罐头话而没有实际任务行为的入口不许存在**——2026-09-09 就因为「招聘协作」只填预设消息而被整个删掉。
2. **不生成精确录用总分，不做自动排名。** 后端 `candidate_comparison` 的 `ranking` 是硬编码 `None`，不要在前端补一个排序出来。
3. **不要求用户编辑 JSON。** 验收样例 W5 明确把「要求编辑 JSON」列为拒收条件。
   现存违反项：`webui/src/workspaces/hr/HrCandidateWorkspace.tsx` 的「确认后的候选人事实」是一个原始 JSON textarea。若任务 B 决定保留该文件，必须改掉。
4. **界面不暴露 UUID、`contextVersionId`、内部 schema、hash。**
   现存违反项：候选人列表显示「岗位上下文 {id前8位}」，材料列表显示「来源对话 {id前8位} · 轮次 {id前8位}」，身份合并选项显示「合并到 {candidateId}」裸 UUID。
5. **情报页浏览不得触发采集、重算或全库分析**，也不提供任何「立即重算」的普通页面入口。
6. **不恢复飞书关联入口**（已于 2026-09-09 永久删除）。
7. **四种状态必须独立展示**：回答结束 / 成果保存 / 标准确认 / 业务可用。**前三项不能自动证明第四项。**
8. **真实标准确认固定为用户 HTTP 操作，不进入模型工具集。模型不能代用户确认。**
9. **同名候选人不自动合并**，必须让用户明确选择新建还是合并。
10. **失败要如实显示原因，不能一直伪装成正常处理中**；读取失败不能显示成「0 条记录」。

---

## 5. 测试处置

启动分支 HR 前端测试的处置原则：**编码业务行为的迁移，编码已删结构的随之删除。**

必须保住的端到端旅程（内容是业务断言，与界面结构无关的部分逐条迁移）：

- `HrP0Combined.acceptance.test.tsx` — 完整主线：岗位需求 → JD/JR → 材料上传 → 3 份简历批量解析（含 1 份失败）→ 2 位候选人确认 → 匹配分析 → 面试题 PDF 下载 → 全景证据回修 JD/JR
- `HrPositionSpine.acceptance.test.tsx` — 刷新后恢复；只在已加载岗位内创建工作；显式提升用户文件同时保持成果可下载
- `HrPanorama.acceptance.test.tsx` — 使用已发布报告而不触发采集；原始证据可下载

注意 `HrP0Combined` 的用例名是「without losing the mounted recruiting conversation」，它绑定了「挂载后用 `hidden` 隐藏」这个实现。**该实现可以改**，但它保护的用户诉求——切走再切回不丢失正在进行的对话——必须以新形式保住（例如靠路由 + 草稿快照恢复，而不是 DOM 常驻）。

---

## 6. 验收纪律

- **接口优先，页面最后验收。** 顺序是：最小失败用例 → 相关接口/数据库回归 → 必要的真实进程故障测试 → 最后一轮关键页面交互验收。
- 页面只用于接口测试覆盖不到的东西：输入、滚动、布局、文件选择、流式呈现。没有具体前端问题时不要做重复的浏览器巡视。
- 完成报告要分开写清：接口回归 / 前端组件测试 / 浏览器验收 / 生产验收。**有跳过或未验证的，明确说出来。**
- **不要用测试数量、覆盖率或目录 hash 证明业务可用。**
- 如果本地测试里替换了模型提供方，必须单独说明，不能称之为真实模型或业务质量验收。
