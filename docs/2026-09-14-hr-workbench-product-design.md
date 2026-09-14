# AI-HR 智能工作台 · 产品设计

- 日期：2026-09-14
- 代码基线：`master` @ `21091b13`（已核对 `git rev-parse`，与 `origin/master` 一致）
- 状态：草案第二版。§5 权威选择**已获用户认可**；其余按 2026-09-14 用户代码核对结论修正
- 前置：承接 `HR总体架构设计.md` 与 `HR_Agent工作流.md` 的既有裁定，不重新发明方向

本文按四层分开陈述：**业务要求 / 目标设计 / 当前代码 / 验收证据**。静态分析得出但未在运行时验证的，明确标注。**本文未改代码，也未把静态分析当作运行时验收。**

---

## 1. 结论：这次要修的是"看得到、找得到"，不是导航

信息架构基本是对的。四个门类（主对话 / 岗位库 / 岗位工作流 / HR 情报）与既有裁定吻合；`HrPositionWorkflow` 的五阶段已正确区分"官网原文是来源事实"与"已确认标准是团队认可"，读取失败也如实显示而非伪装成 0 条。

真正坏掉的是**新旧两条链之间的读取断层**，而且有两处，不是一处：

| 断层 | 主对话写入 | 岗位页读取 |
|---|---|---|
| **标准** | `platform_hr_agent.standards` | `platform_hr.position_context_versions` |
| **成果** | `/api/hr/agent/results` | `/api/v1/hr/positions/{id}/results` |

所以**下一步的目标就是这个闭环：确认后看得到，保存后找得到。** 重复界面的收敛排在这之后。

---

## 2. 诊断（已核对代码）

### 2.1 标准：主对话确认的标准，岗位页看不到

`backend/app/main.py` 同时挂载两套：

| | 主对话 | 岗位工作流页 |
|---|---|---|
| 路由 | `POST/GET /api/hr/agent/positions/{id}/standards/{confirm,current}`（`main.py:1620` → `hr_agent/routes.py:307,317`） | `GET /api/hr/positions/{id}/context`（`main.py:1653` → `hr/position_intelligence_routes.py:326`） |
| 存储 | `platform_hr_agent.standards` + `standard_revisions`（`hr_agent/standards.py:18-21`） | `platform_hr.position_context_versions` |
| 形态 | 扁平 `items[]`（`item_id` + `text`）+ `revision` | 8 个固定模块 + `displayVersion` |

前端：`HrLoopWorkspace.tsx:1007-1022` 调 `api.confirm(...)`；`HrPositionWorkflow.tsx:45,70` 调 `r12.context(positionId)` 并用 `HrContextVersionView` 渲染「已确认岗位标准」。

核对结果：**岗位页从不调用新标准接口**（grep `agent/positions`、`standards/current`、`hrLoopApi` 无命中）；`backend/app/hr_agent/` 下无任何文件写 `position_context_versions`。

#### 相位闸门不能解决这个问题

两条链在**写入侧**由 `platform_control.hr_execution_cutover(phase ∈ legacy|draining_legacy|cloud|draining_cloud)` 互斥（`control_migrations/102_hr_execution_cutover.sql`，`hr_agent/cutover.py:43-68`；单例行缺失时只允许 `legacy`）。

但 `require_lane` 只出现在 `backend/app/hr_agent/` 内部（`candidates.py:149,516`、`repository.py:450,601,684`、`material_parsing.py:231`）——**它只把关云端链准入，对旧链读取不设限**。`hr/position_intelligence_routes.py` 中 grep `require_lane`/`cutover`/`lane` 无命中。

因此 `phase = cloud` 下的实际后果：旧接口**未被拦截、继续正常返回**，但读的是已冻结的表，岗位页会显示「尚无已确认标准」或一份陈旧标准。**这条路径不报错，只是安静地给错答案。**

*静态分析结论，未运行时验证；无法从源码判断已部署环境的 `phase` 取值。*

### 2.2 成果：同一个病，第二处

`HrPositionWorkflow.tsx:38` 读的是旧成果接口：

```
GET /api/v1/hr/positions/{positionId}/results?offset=&limit=50
```

这条路由来自 `hr/tool_routes.py::build_hr_result_router`（`main.py:1645-1646`），服务的是旧链 v6/v7 工具写下的成果。`main.py:1643-1644` 的注释写明它的定位是「历史成果读取，比旧执行器活得久」。

主对话侧读的是 `GET /api/hr/agent/results` 与 `/api/hr/agent/results/{id}/revisions/{rev}`（`hrLoopApi.ts`）。

**所以只统一标准是不够的**：主对话保存的 JD、人才画像、面试方案、候选人分析等成果，回到岗位页同样找不回来。这与 §2.1 必须一起修，否则「保存后找得到」不成立，而"三入口同一成果"裁定也无法兑现。

### 2.3 岗位库有四个按钮接在已删除的接口上

`7b041174`（2026-09-08）退役六类岗位草案写入口。`hr/routes.py` 现在只剩 6 条路由，写操作只有材料提升/移除：

```
GET    /api/hr/positions
GET    /api/hr/positions/{position_id}
GET    /api/hr/position-drafts
GET    /api/hr/conversations/{conversation_id}/position-package
POST   /api/hr/positions/{position_id}/materials/{attachment_id}
DELETE /api/hr/positions/{position_id}/materials/{attachment_id}
```

但 `HrPositionIndex.tsx` 仍接着：确认新建（`:120` `confirmDraft`）、合并到岗位（`:124` `mergeDraft`）、忽略（`:126` `dismissDraft`）、用对话新建岗位（`:148` `proposeDraft`）。

失败形态：白名单（`control_plane/authorization.py:174-185`）仍列着这些退役路由，但白名单是**允许的超集**，所以是 **404 而非 403**。另外 `App.tsx:121-127` 硬编码 `cloudPrimary`，`HrPositionIndex.tsx:64` 会在部分动作发出前把用户弹回 `/hr/`。**六个中还有几个在浏览器里点得到属未验证。** 但「待确认工作流已不成立」是确定的。

同类白名单僵尸条目另有 7 条：`POST .../context/drafts`（`:193`）、`.../context/drafts/{id}/confirm`（`:194`）、`POST /api/hr/position-candidates/{pcid}/analyses`（`:212`）、`.../candidate-comparisons`（`:215`）、`.../tasks`（`:216`）、`GET .../tasks`（`:217`）、`GET .../tasks/{task_id}`（`:218`）。

### 2.4 候选人管理有两套实现

| 实现 | 行数 | 入口 |
|---|---|---|
| `HrCandidateWorkspace` | 425 | 岗位工作流「候选人」阶段（`HrPositionWorkflow.tsx:72`） |
| `HrLoopCandidatesPanel` + `HrLoopCandidateReview` + `HrLoopCandidateWorkspace` | 615 + 230 + 108 | 主对话侧栏（`HrLoopWorkspace.tsx:669-673`） |

两者能力**不对等**，这一点很重要，不能简单"合并"（见 §7.3）。

### 2.5 情报有两套实现

新的 `HrSourceWorkspace`(136) / `HrCompanyDetail`(706) / `HrResearchWorkspace`(139) / `HrTopicWorkspace`(616) 与 legacy 的 `HrLegacyPanoramaWorkspace`(417) 并存，`HrPanoramaWorkspace`(58) 按 query 参数分流；主对话侧栏另有 `HrLoopIntelligencePicker`(241)。

`webui/src/hrPanoramaApi.ts`（144 行）**生产代码零引用**，只有自己的测试导入它；`GET /api/hr/panorama/reports/{bundle_id}/export` 无任何前端调用者。实际在用的证据下载是组件内裸 `fetch`（`HrCompanyDetail.tsx:411,691`、`HrTopicWorkspace.tsx:518,555`），绕过了该客户端。

### 2.6 导航概念重复

`HrWorkspaceShell` 顶栏是「对话 / 岗位 / HR 情报 / 方法与模型」；`HrLoopWorkspace` 左侧栏是「最近工作 / 专业方法 / 候选人材料 / 公开研究」。**专业方法**与**情报**在两处各有入口，含义不完全相同。

### 2.7 岗位库当前是多列卡片网格，不是列表

`hrPositionWorkflow.css:1`：

```css
.hr-pw-position-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:22px}
```

宽屏下是 3–4 列卡片，每张卡片还带一条「JD/JR · 候选人 · 面试 · 复盘」阶段条（`HrPositionIndex.tsx:241`）。**用户要求改为列表**（见 §7.1）。

### 2.8 仍在违反既有裁定的两处

| 裁定 | 违反位置 |
|---|---|
| 验收样例 W5 明确把「要求编辑 JSON」列为拒收 | `HrCandidateWorkspace.tsx` 的「确认后的候选人事实」是原始 JSON textarea（旧链） |
| 「用户无需管理 hash 或内部 schema」 | 候选人列表显示「岗位上下文 {id 前 8 位}」；材料列表显示「来源对话 {id} · 轮次 {id}」 |

---

## 3. 产品定位（承接既有裁定，不改）

**Hannah** 服务招聘团队、HRBP、用人经理与面试官，把模糊需求转化为可搜索、可判断、可验证的人才标准。

六类场景：岗位需求校准、人才搜寻与吸引、候选人评估、面试准备与记录、招聘复盘、公司与专题研究（第六类可独立发起）。

**用户可从自然语言、岗位、简历、公司研究或面试反馈任意开始，不以完整建档作为咨询前置。**

首批单用户私有：面试官意见作为当前用户提供的材料进入，不提供面试官独立登录或多人协作。

| | 工作 | 操作对象 | 节奏 |
|---|---|---|---|
| **J1** | 把这个岗位招到合适的人 | 岗位、标准、候选人、材料、成果 | 每天，长周期 |
| **J2** | 了解同行在招什么 | 关注公司、专题、已发布情报版本 | 每周，阅读为主 |
| **J3** | 问一件事 / 做一次研究 | 无固定对象 | 随时，一次性 |

---

## 4. 设计原则（全部来自既有裁定，逐条可追溯）

1. **一个主对话。** 岗位、候选人、材料按轮固定。对话是主入口，岗位只组织材料，**纯咨询不得要求先建岗位**。
2. **页面动作只填入用户可见可编辑的草稿，发送后才执行。** 不存在隐藏的第二份指令。推论：**只插一句罐头话而无实际任务行为的入口不许存在**。
3. **AI 起草，人逐项确认。** 确认固定为用户 HTTP 操作，**不进入模型工具集，模型不能代用户确认**。
4. **四种状态独立展示**：回答结束 / 成果保存 / 标准确认 / 业务可用。**前三项不能自动证明第四项。**
5. **事实与推断分离。** 官网原文是来源事实，确认标准是团队认可，AI 推断必须列出事实依据。规则归类不冒充官网字段。
6. **同一成果可从对话、岗位、候选人三个入口进入**，且深链接不被默认入口截走。
7. **不评分、不排名。**
8. **只说工作语言。** 不出现 UUID、`contextVersionId`、hash、JSON 编辑框、内部 schema。
9. **失败如实说明。** 读取失败不显示成「0 条」；不假装仍在处理。
10. **情报浏览不触发采集、重算或全库分析。**
11. **不为了统一而收回已有能力。** 收敛重复实现时，若两套能力不对等，先补齐差距或明确说明局限，**不能用"统一"作为倒退的理由**。

---

## 5. 已认可的决策：一份标准，一个权威

**用户已认可（2026-09-14）：云端标准作为当前唯一权威，旧标准保留为只读历史。岗位页接入现有标准接口，这一步先做。**

权威 = `platform_hr_agent.standards` / `standard_revisions`。依据：

- 它是**主对话**（产品主入口）写入的那一份，符合「一个主对话」裁定
- **确认是纯用户 HTTP 动作**：`POST /api/hr/agent/positions/{id}/standards/confirm`，且 `confirm` **不在云端工具集里**（工具集仅 5 个：`list_resources`、`read_resource`、`save_note`、`save_result`、`ask_user`，`hr_agent/types.py:129-135`）。这正是原则 3 的正确实现
- 确认语义（逐项 `selected_change_ids` + `expected_standard_revision` 的 CAS，冲突返 409 并带 `current_revision`）正确表达「用户选择认可部分并确认；内容或基准变化时重新核对」
- **旧模型的确认根本没有 HTTP 路由**：`CreateContextDraftBody`（`position_intelligence_routes.py:50`）与 `ConfirmContextModulesBody`（`:98`）定义了却未接路由；旧的 8 模块确认只能经模型工具 `hr.confirm_standard` 触发，再由 `hr/standard_consent.py` + `hr/calibration_service.py:16-49` 校验用户同意
- 云端链只需 1 个 worker；旧链简历解析需 3 个进程

两者形态**不可互换**：

| | 旧（`position_context_versions`） | 新（`platform_hr_agent.standards`） |
|---|---|---|
| 结构 | 8 个固定模块 | **扁平 `items[]`，无模块概念** |
| 唯一性 | DB 唯一索引保证每岗位仅一条 `confirmed` | 每 `(owner, position)` 仅一个 `current_revision` |
| 继承 | 确认 `jd` 不丢已确认的 `mission`（`069:653` 做 `||` 合并） | 由 `changes[]` 的 `add/replace/remove` 显式表达 |

因此：

1. 岗位页「已确认岗位标准」改读 `GET /api/hr/agent/positions/{id}/standards/current`
2. `position_context_versions` 降为**只读历史**，界面标为「历史确认记录」，不与当前标准并列
3. **不做 8 模块 ↔ items 字段映射把历史伪装成当前**

### 5.1 必须同时修：确认标准不会自动创建岗位

**这是本版的重要修正。** 确认流程要求岗位**已经存在**：`hr_agent/resources.py:150-162` 在对象范围校验时执行

```sql
SELECT 1 FROM platform_hr.positions WHERE owner_internal_user_id=%s AND position_id=%s
```

不存在则 `raise problem("not_found", http_status=404)`。

同时 `hr/routes.py` 已无任何建岗写接口（§2.3）。两者相加的结论是：

> **「确认标准后即进入岗位库」目前无法兑现。**

注意一个架构细节：**岗位本身仍住在 `platform_hr.positions`**，只有标准迁到了 `platform_hr_agent`。所以云端链是"用新库存标准、用旧库认岗位"。

两条出路，需要决策（§12 问题 1）：

- **补建岗能力**：在云端链新增建岗契约，使"说清要招什么 → 确认 → 成为正式岗位"真正成立
- **调整承诺**：明确岗位只能来自官网同步，主对话只能在**已存在的岗位**上工作；界面文案据此改写，不承诺建岗

**不要在设计里保留无法兑现的承诺。** 在决策之前，§7.1 按"调整承诺"的保守写法落地。

### 5.2 顺带确认：云端链已在结构上强制"事实与推断分离"

值得保住，界面不要绕过。`save_result` 契约（`hr_agent/contracts.schema.json`）规定：

- `kind ∈ {role_calibration, jd, requirements, standard_proposal}` ⇒ `basis[]` 必须非空
- `ResultBasis.kind` 只能是 `confirmed_standard` / `official_original` / `user_temporary`
- `kind == standard_proposal` ⇒ `changes[]` 非空；其他 kind ⇒ `changes` 必须为空且 `base_standard_ref` 为 null

即任何标准类成果都必须声明建立在哪一类事实之上。**界面应把 `basis` 显式展示**——这是"推断必须列出事实依据"裁定的落地点，现在后端有、界面没用。

`save_result.kind` 共 10 种：`role_calibration, jd, requirements, standard_proposal, sourcing, candidate_assessment, interview_plan, interview_record, retrospective, research`。

---

## 6. 信息架构（沿用，明确职责）

```
/hr/                      主工作台 —— 干活的地方（三栏：工作与方法 | 对话 | 成果与标准）
/hr/positions             岗位库 —— 找到岗位（列表）
/hr/positions/:id         岗位工作流 —— 汇集与回看，动作交回主对话
/hr/panorama              HR 情报 —— 只读两层：原始资料 / AI 分析报告
```

| 界面 | 职责 | 明确不做 |
|---|---|---|
| 主工作台 | 一切**执行**：提要求、选对象、看进度、确认标准、保存成果 | 不做跨岗位报表 |
| 岗位库 | **找到**一个岗位 | 不做岗位增删改（写入口已退役） |
| 岗位工作流 | **汇集与回看**一个岗位的全部记录；每阶段提供「带此岗位回主对话」 | 不在此页发起模型调用 |
| HR 情报 | **阅读**已发布内容 | 不触发采集或分析 |

导航去重（解 §2.6）：顶栏保留「对话 / 岗位 / HR 情报」；**「方法与模型」从顶栏移除**，只留在主工作台侧栏——它是选材料的工具，不是门类，且既有裁定说明它「供选择和阅读，不提供业务授权」。

---

## 7. 关键交互设计

### 7.1 岗位库：列表形态，不做建岗承诺

**采用列表**（解 §2.7）。当前的多列卡片网格改为单列列表，每行一条岗位，横向排列可扫读的字段：岗位名称、部门与地点、来源（官网/内部）、状态、最近活动时间。理由：岗位库的任务是**找到**一个岗位，列表的纵向扫读和横向对齐比卡片网格更快，也更容易容纳搜索与筛选结果。卡片上那条「JD/JR · 候选人 · 面试 · 复盘」阶段条移入岗位工作流页，列表行不承载它。

**删除「待确认」整块与「用对话新建岗位」弹窗**（接口已不存在，§2.3）。按 §5.1 未决前的保守写法，替换为一句如实说明：

> 岗位来自官网同步。主对话可以在已有岗位上推进工作。

若 §12 问题 1 决定补建岗能力，此处文案与入口再按新契约调整。

前端一并删除 `hrApi.ts` 中六个无后端的方法；白名单清掉 `authorization.py:174-185` 及 §2.3 列出的 7 条僵尸条目。`GET /api/hr/position-drafts` 与 `/conversations/{id}/position-package` 随之无消费者（§12 问题 2 已核实）。

### 7.2 确认标准（产品的核心动作）

唯一必须做到舒展的界面：

- **先展示准确正文**再让用户勾选，不能只给摘要就要求确认
- **逐项勾选**，未勾选的项保持原样，**不静默覆盖**
- **基准变化时重新核对**：`expected_standard_revision` 冲突时显示「这条要求在你确认期间变过」并列出变化前后正文，让用户按新基准重新决定。不用「基线已变化，请按新基线重试」这类机制语言
- 确认后明确显示**标准确认**状态，且**不因此声称业务可用**（原则 4）
- 不出现 `revision`、`sha256`、`item_id`

### 7.3 候选人（本版重点修正）

§2.4 的两套实现**能力不对等**，按原则 11 处理：先说清差距，不用"统一"做倒退的理由。

#### 7.3.1 结构化字段编辑不是现有云端能力

云端 `confirm_item` 只接受 6 个字段（`hr_agent/candidates.py:571-585`）：

```
expected_row_version, result_ref, display_name(≤500),
summary(≤16000), decision({kind:create} | merge), reviewed_limitations(bool)
```

**没有任何结构化事实字段。** 而旧链的 `confirmDraft` 有 `confirmedFacts`，但呈现为原始 JSON textarea（§2.8）。

所以现状是：**能力在错的链路上、以错的形态存在。**

- 短期：云端保持 `display_name` + 审阅摘要，界面把它设计成一个体面的审阅表单（姓名 + 摘要 + 明确的合并/新建决策 + 局限确认），**不假装有字段级编辑**
- 中期：技能、经历等结构化编辑**需要新增云端契约**，属独立决策与排期，不在本次闭环范围内

**删除 JSON textarea** 这一条仍然成立——它违反 W5 拒收条件——但替换物是"姓名 + 摘要的审阅表单"，不是"按字段的表单"。这是与上一版的差别。

#### 7.3.2 `reviewed_limitations` 是个好机制，要显式化

`candidates.py:630-631`：在需要审阅的情形下，若 `reviewed_limitations` 不为 `True` 则返回 `409 review_required`。这是"不能替用户确认"的结构化落实。界面应把它做成一句明确的、用户必须主动勾选的确认（例如「我已看过上面列出的待核实项」），而不是一个默认勾选的复选框。

#### 7.3.3 不要恢复"必须重算才能比较"的限制

**这是与上一版的重要差别。** 当前代码**已允许**在不同标准版本下比较：`HrCandidateWorkspace.test.tsx:427` 的用例名为「binds feedback to the newest analysis and **uses active relation status for comparison**」，其中 `positionCandidates` 故意返回一个 `contextVersionId` 不同的关系，比较仍然进行。

设计**不得**重新引入「上下文版本不同，需重算后比较」这类硬阻止。正确做法是**说明依据差异与局限**：

> 这两位候选人的分析依据不是同一版岗位标准（张三按 9 月 10 日版，李四按 9 月 12 日版）。
> 下面的比较仍然可用，但涉及标准变动的部分需要你自己判断。

比较仍然**不产出排序**——`hr/candidate_service.py:299` 的 `ranking` 硬编码 `None`，界面不要补一个出来。

#### 7.3.4 身份合并不显示 UUID

显示候选人姓名 + 可区分的事实（公司、岗位、最近经历）让用户判断是否同一人。**同名不自动合并**的约束保留（`decision` 必须显式为 `{kind:create}` 或 merge）。

### 7.4 成果：三个入口，动作按类型区分

同一份成果必须能从对话、岗位、候选人三处进入，且是**同一份**（同一 `result_id` + `revision`，不是重新生成一份冒充）。这依赖 §2.2 的成果读取统一，否则做不到。

成果卡的动作**不是一套通用四连**（这是与上一版的修正）：

| 动作 | 适用范围 |
|---|---|
| 下载 | 全部成果 |
| 带此成果讨论 | 全部成果 |
| 关联到岗位 | 全部成果 |
| **逐项确认为标准** | **仅 `kind == standard_proposal`** |

依据：`save_result` 契约规定只有 `standard_proposal` 的 `changes[]` 非空，其他 kind 的 `changes` 必须为空且 `base_standard_ref` 为 null（§5.2）。**没有 `changes` 就没有可勾选的条目**，把"确认为标准"做成所有成果的通用动作在契约层面就是错的。

文件交付要同时验证登记与字节可下载，不能只给一个看起来像下载的链接。

### 7.5 情报阅读

保留两层「原始资料 → AI 分析报告」，共享公司选择，各自保留筛选与阅读位置。

- 资料层以**公司聚合**为主体，岗位原文与明细默认收起（2026-09-11 裁定：不以岗位列表为主体）
- 清洗后的原始资料**不夹带 AI 判断**；规则归类不冒充官网字段
- 没有报告的公司仍可查阅已取得资料
- 采集失败、缺字段、确认无记录**分别展示**；「本次未取得岗位记录」不等于「已证明零招聘」
- legacy 的 `HrLegacyPanoramaWorkspace` 降为归档：历史版本深链接仍可打开，但不再是并行的第二套浏览界面
- 删除零引用的 `hrPanoramaApi.ts`

---

## 8. 状态与诚实度

| 状态 | 含义 | 界面表达 |
|---|---|---|
| 回答结束 | 本次响应完成 | 对话区 |
| 成果保存 | 内容可找回 | 成果栏出现条目 |
| 标准确认 | 用户认可了具体要求 | 标准栏更新 + 确认时间 |
| 业务可用 | 由实际目标、证据和剩余问题判断 | **不自动点亮**，由用户判断 |

运行状态沿用目标设计的表达（`HrLoopWorkspace` 已实现其中若干）：已受理可取消、正在处理（不展示私密提示词与推理全文）、正在收尾（不暗示整个研究已完成）、待答问题（「你希望这次解决什么？」）、待额度（「继续前，请指定追加额度」，用户追加才继续）、执行失败（不假装仍在处理）、恢复中（不要求用户重复请求）。**浏览器关闭不取消工作，重新打开按持久记录恢复。**

---

## 9. 术语表：机制 → 工作语言

| 现在（机制） | 改为（工作语言） |
|---|---|
| 岗位上下文 / context version | 岗位标准 |
| 基线已变化，请按新基线重试 | 这条要求在你确认期间变过，请核对后重新决定 |
| 上下文版本不同，需重算后比较 | 这两位的分析依据不是同一版标准，比较仍可用，涉及标准变动处请自行判断 |
| 确认后的候选人事实（JSON） | 候选人审阅（姓名 + 摘要 + 合并决策 + 局限确认） |
| 合并到 `{candidateId}` | 合并到「张三 · 某公司 · 结构工程师」 |
| 来源对话 `{id}` · 轮次 `{id}` | 来自 9 月 12 日的这段对话（可点击回溯） |
| 全景分析 | HR 情报 |
| displayVersion / revision / sha256 | 不显示 |

---

## 10. 分期：先闭环，再收敛

按用户裁定——**先完成"确认后看得到、保存后找得到"的闭环，再收敛重复界面。**

### 第一阶段：闭环（P1–P2）

| 期 | 内容 | 退出条件 | 依赖 |
|---|---|---|---|
| **P1** | 岗位页「已确认岗位标准」改读 `GET /api/hr/agent/positions/{id}/standards/current`；旧 `context` 降为「历史确认记录」区 | 岗位页不再把 `position_context_versions` 当当前标准 | **无**——接口已存在且已在白名单（`authorization.py:152`） |
| **P2** | 岗位页成果改读 `/api/hr/agent/results`（按岗位 object 范围）；旧 `/api/v1/hr/positions/{id}/results` 降为历史成果区 | 岗位页不再把旧成果接口当当前成果 | **无**——已核实 `GET /results?object_kind=position&object_id=&kind=` 支持所需范围（`hr_agent/routes.py:255-275`） |

P2 顺带修掉一个已记录的旧缺陷。当前岗位页按旧 `schemaId` 字符串分类（`HrPositionWorkflow.tsx:56-59`），而旧的 `hr.analysis.v1` **不区分人才搜寻与招聘复盘**，所以两个阶段一直共用同一批成果，页面上还挂着一句「已有岗位分析尚未单独标注搜寻或复盘类型」的说明（`:71`）。云端的 10 种 `kind` 干净地对上五阶段：

| 岗位页阶段 | 云端 `kind` |
|---|---|
| JD / JR | `role_calibration`、`jd`、`requirements`、`standard_proposal` |
| 人才搜寻 | `sourcing` |
| 候选人 | `candidate_assessment` |
| 面试 | `interview_plan`、`interview_record` |
| 招聘复盘 | `retrospective` |

（`research` 不属于岗位阶段，归 J3 独立研究。）

改读之后那句免责说明可以删掉，两个阶段各自显示自己的成果。

P1、P2 共同验收「确认后看得到、保存后找得到」（§11）。

### 第二阶段：收敛与修正（P3–P8）

| 期 | 内容 | 退出条件 |
|---|---|---|
| **P3** | 修岗位库（§7.1）：改列表形态；删「待确认」与「用对话新建岗位」；清 `hrApi.ts` 六个死方法与 13 条白名单僵尸条目 | `HrPositionIndex` 不再调用已删接口 |
| **P4** | 候选人收敛（§7.3）：去 JSON、去 UUID；`reviewed_limitations` 显式化；**保留跨标准版本比较并说明局限** | 两套实现合并为一套，且无能力倒退 |
| **P5** | 情报收敛（§7.5）：legacy 降为归档；删 `hrPanoramaApi.ts` | `HrLegacyPanoramaWorkspace` 仅由深链接进入 |
| **P6** | 导航去重（§6）：顶栏去掉「方法与模型」 | 顶栏三项 |
| **P7** | 展示 `basis`（§5.2）：成果卡显示依据类别 | 「推断必须列出事实依据」界面可见 |
| **P8** | 术语替换（§9） | 对照表清空 |

### 不在本轮范围

- 候选人结构化字段编辑（需新增云端契约，§7.3.1）
- 建岗能力（待 §12 问题 1 决策）
- 情报独立授权（§12 已决定沿用现有范围）
- 跨岗位人才库（§12 已决定不扩建）

---

## 11. 验收（遵守"接口优先、页面最后"）

顺序固定：最小失败用例 → 接口/数据库回归 → 必要的进程故障测试 → 最后一轮页面交互验收。

**前置：先读出 `platform_control.hr_execution_cutover.phase` 的实际取值并写进报告。** 源码无法判断任何已部署环境的相位，而该取值决定用例怎么写（单例行缺失时只允许 `legacy`）。

P1 + P2 的贯通用例（`phase = cloud`）：

> 在主对话确认一条标准 → `GET /api/hr/agent/positions/{id}/standards/current` 读到它 →
> 岗位页读到同一份（同一 `revision`）→ 未勾选项未被覆盖 →
> 并发确认时 `expected_standard_revision` 冲突被拒并返回 `current_revision`
>
> 在主对话保存一份成果（如 `kind=jd`）→ 岗位页在对应岗位下读到同一份（同一 `result_id` + `revision`）→
> 从对话、岗位两个入口打开的是同一份，不是重新生成的另一份

**反向回归**，专盯"安静给错答案"：

> 旧接口 `GET /api/hr/positions/{id}/context` 与 `GET /api/v1/hr/positions/{id}/results` 在 `phase = cloud` 下
> 仍会正常返回（不受闸门约束）。用例须断言：岗位页**不再**把它们当作当前标准/当前成果展示，
> 只放在「历史确认记录」/「历史成果」区。

报告分开写清：接口回归 / 进程故障 / 前端组件 / 浏览器 / 生产。跳过的明确说出来。**不以测试数量、覆盖率代替业务闭环。** 本地替换模型提供方的测试单独标注，不称为真实模型或业务质量验收。

---

## 12. 决策记录与待决问题

### 已决策

| # | 问题 | 决定 |
|---|---|---|
| — | 标准权威 | **云端标准为当前唯一权威，旧标准保留只读历史；岗位页接入现有标准接口先做。** |
| 3 | 情报是否独立授权 | **先沿用现有范围**，有明确独立使用者后再拆授权 |
| 4 | 是否扩建跨岗位人才库 | **保留现有跨岗位候选人能力，暂不扩建人才库。** 云端已有 `GET /api/hr/agent/candidates` 候选人列表与多岗位关联，并非只能从岗位进入 |
| 2 | `position-drafts` / `position-package` 是否退役 | **已核实无需决策**：`position-package` 前端零调用者；`position-drafts` 唯一调用者是 P3 要删的「待确认」区块。P3 完成后两条 GET 可随之退役。仅需确认历史草案数据本身有无留存价值 |

### 待决

| # | 问题 |
|---|---|
| **1** | **建岗能力：补，还是改承诺？**（§5.1）确认流程要求岗位已存在（`hr_agent/resources.py:150-162` 校验 `platform_hr.positions`，缺失即 404），而建岗写接口已全部退役。要么在云端新增建岗契约，要么明确岗位只来自官网同步并据此改写文案。**在决策前 §7.1 按后者的保守写法落地。** |
| **5** | **生产环境当前的 `hr_execution_cutover.phase` 是什么？** 源码无法判断。若实际仍是 `legacy`，则 `/hr/` 主对话（走 `/api/hr/agent/*`）会整体 503，问题性质完全不同，需先确认再排期。 |
