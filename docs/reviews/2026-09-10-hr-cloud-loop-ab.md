# HR Agent A、B 联合评审包

评审对象：自有云端 Loop 运行底座（A0/A1）及岗位校准闭环（B）。这是现有交付的联合评审入口，不新增设计，也不代表用户验收通过。

后续状态：[2026-09-11 修订记录](2026-09-11-hr-cloud-loop-ab-revision.md)，含模型选择变更及尚未解决的 C 前置风险。

## 1. 在哪里评审

- 工作树：`/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-a1`
- 联合分支：`feat/hr-cloud-loop-b`。已经包含 A1 全部提交，不需要先合并两条分支。
- A0 已修订规格基线：`2bddc77`。
- A1 完整交付：`589f5d7`，另保留在 `feat/hr-cloud-loop-a1`。
- A+B 本次联合评审快照：`12a6447`。后续本评审入口的提交仅整理文档。
- 没有推远端、合并 master、部署或停用现行 HR 链路。

```sh
# 在上述工作树执行，只读查看。
git diff --stat 2bddc77..12a6447
git diff 2bddc77..589f5d7 -- backend/app/hr_agent backend/control_migrations/hr_agent
git diff 589f5d7..12a6447 -- backend webui deploy
```

A1 报告是当时快照，其中“B 尚未开放”“只有单发布”“没有业务 UI”等限制已由 B 对应交付更新；不能把这些历史状态当作联合分支现状。未被 B 覆盖的限制继续有效。

## 2. 先读哪些材料

| 顺序 | 材料 | 用途 |
| --- | --- | --- |
| 1 | [总体架构](../../HR总体架构设计.md)、[Agent 工作流](../../HR_Agent工作流.md) | 场景、产品边界、授权、成果与验收定义 |
| 2 | [运行时实施规格](../superpowers/specs/2026-09-09-hr-cloud-loop-runtime-spec.md) | API、五工具、记录、引用、预算、恢复与 B 接口增补 |
| 3 | [总交付计划](../superpowers/plans/2026-09-09-hr-cloud-loop-delivery.md)、[B 实施计划](../superpowers/plans/2026-09-10-hr-cloud-loop-b.md) | 本批范围与 C–E 尚未交付的边界 |
| 4 | [A1 工程记录](2026-09-10-hr-cloud-loop-a1.md)、[B 工程及模型记录](2026-09-10-hr-cloud-loop-b.md) | 实际验证、替身边界、已修复问题、未验分项 |
| 5 | [真实模型证据说明](artifacts/2026-09-10-hr-b-public-model/README.md) | 公开 JD、方法引用、模型成果、用户部分确认及证据缺口 |
| 6 | [全分支独立复审](artifacts/2026-09-10-hr-b-public-model/engineering-review.md)、[前端修订记录](artifacts/2026-09-10-hr-b-public-model/frontend-review.md) | 已发现问题、修复位置与复审结论 |

## 3. A、B 一起交付了什么

| 层次 | A0/A1 | B 增补后的现状 |
| --- | --- | --- |
| 执行 | 独立 API、Worker、自有模型工具循环；不依赖 HR MetaBot/PTY/Claude Code | 同一底座支撑岗位校准界面与公开真实模型旅程；默认关闭，旧链未切换 |
| 工作记录 | 输入修订、完整模型响应、工具回执、成果及来源边持久化 | 方法、资料、提案、确认标准可在连续工作中准确引用 |
| 上下文与权限 | 每轮按当前对象选历史；摘要追溯原条目；当前用户/HR grant/来源权限校验 | 个人来源继承到结果及标准校验；撤权清除页面内容、标题、岗位缓存与草稿 |
| 可靠性 | 租约、心跳、取消、幂等、崩溃恢复、累计预算和阶段收尾 | 解析任务也有持久队列、租约和有界重试；标准确认同事务处理冲突 |
| 材料 | UTF-8 原件/正文分离、准确身份、有界内存读取 | PDF/DOCX 解析与覆盖说明；无 OCR，不将部分解析称为全文 |
| 专业知识 | 受校验的公开目录和按需读取能力 | 七份真实方法、案例与来源台账；保留不可变发布，前端与模型引用同一正文 |
| 成果 | 无岗位也可保存；精确读取；由用户关联岗位 | 确定性 Markdown 下载；对话与岗位找到同一成果；显示结构化基准性质 |
| 标准 | 预留契约与存储，不开放确认 | 用户勾选具体提案条目，通过 HTTP 部分确认；旧基准冲突不覆盖；模型无确认工具 |
| 页面 | 无新业务 UI | 独立 `/hr/agent`：方法阅读、材料状态、工作恢复、成果、确认、预算续作及窄屏滚动 |

方法选择没有关键词路由、固定调用顺序或“必须读几份方法”的在线门槛。服务端强制的是身份、范围、准确引用、保存、确认和预算等客观契约。

## 4. 代码核查入口

| 核查面 | 主要路径 |
| --- | --- |
| API 与服务端身份 | `backend/app/hr_agent/routes.py`、`service.py`、`access.py`；`backend/app/control_plane/auth.py`、`authorization.py`、`middleware.py` |
| 循环、模型、预算与恢复 | `backend/app/hr_agent/runtime.py`、`model.py`、`worker.py`、`repository.py`、`context.py` |
| 材料与专业发布 | `backend/app/hr_agent/materials.py`、`material_parsing.py`、`resources.py`、`knowledge.py`；`backend/tools/hr_agent/build_knowledge_release.py`；`backend/hr_agent_knowledge/` |
| 成果与标准 | `backend/app/hr_agent/results.py`、`repository_views.py`、`proposals.py`、`standards.py` |
| 数据库 | `backend/control_migrations/hr_agent/096_hr_agent_runtime.sql`、`097_hr_agent_material_parses.sql`；`backend/app/hr_agent/config.py` 的就绪检查 |
| 界面 | `webui/src/hrLoopApi.ts`、`webui/src/workspaces/hr/HrLoopWorkspace.tsx`、`HrLoopMethodPreview.tsx`、`HrLoopWorkspace.css` |
| 装配 | `backend/app/main.py`；`deploy/cloud/compose.yaml`、`compose.hr-agent.yaml`；[本地运行说明](../runbooks/hr-agent-local-runtime.md) |

## 5. 现有证据与可信边界

以下数字来自各阶段实际运行记录，本次整理评审包没有重新跑模型或全量测试。A1 与 B 的测试有继承和重叠，不能相加当作独立覆盖数。

| 类别 | 已留存结果 | 边界 |
| --- | --- | --- |
| A1 阶段回归 | 168 通过；现行相关接口/启动回归另 15 通过 | 一次性数据库、真实进程；模型为替身 |
| A+B 后端回归 | 216 通过、1 跳过 | 跳过的是需显式配置的真实模型测试；已另行实跑公开模型旅程 |
| 身份/回跳回归 | 272 通过 | 不等于企业 SSO 或云端 TLS 已演练 |
| 前端 | 2026-09-11 原样重跑报告中的 10 文件命令：225 项通过（其中工作界面 24 项）；`npm test` 全量一次为 1,145 通过、3 失败 | 组合命令是组件/fetch 合约测试；全量失败均为既存 `src/styles.test.ts` 断言，见下方，不是本批 Loop CSS 或测试修复 |
| Schema/文档 | 55 定义、133 例、18 条件规则、6 正文证据 ID | “18 条件规则”是 selfcheck 覆盖 ID，不是 schema 构造数量；不证明权限实现或招聘判断正确 |
| 真实进程 | Worker SIGKILL/恢复、提交前后故障、取消、摘要、预算及材料读取崩溃等 | 不承诺模型供应商请求恰好一次；已外发材料不能撤回 |
| 浏览器 | 工作恢复、方法阅读/选择、部分确认、关联、下载、继续输入、390px 双向滚动 | 本地测试身份、脚本模型、HTTPS Origin 传输适配；不是生产验收 |
| 独立审查 | 原交付时后端/前端审查曾闭合；9月11日外部审计新增的问题见[修订记录](2026-09-11-hr-cloud-loop-ab-revision.md)，不可继续称当前无未关闭项 | 仍保留下面的未验事项，不能代替用户验收 |

此前会话曾观察到浏览器下载为 125 字节，且与授权 `/file-info` 的 SHA-256 一致；本次评审包未保留该文件或 file-info 响应，故此观察不可复现、不可作为响应捕获证据。仅保留当时助手记录的 SHA-256 `8d9cbc31f733fb8920c81f92077939319b21c9e0fb12be13082e0171dd5c4523`，不把它表述为重新核验。截图见 B 报告，截图内容来自本地工程夹具，不是真实模型成果。

前端复核使用报告的准确命令和文件清单：

```sh
cd webui
npm test -- src/hrLoopApi.test.ts src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrWorkspaceShell.test.tsx src/router.test.ts src/workspaces/hr/HrPositionIndex.test.tsx src/documentTitle.test.tsx src/accessEventReporter.test.tsx src/AppShell.brain.test.tsx src/App.hrPositionSection.test.tsx src/auth.test.ts
```

它在 2026-09-11 得到 **10 files / 225 tests passed**。随后一次 `npm test` 得到 **126 files / 1,145 tests passed，`src/styles.test.ts` 3 failed**：最小可见字号 `11 < 11.5`、`.hr-workspace-shell` 期待的 `background: #eef1f4`、以及职位聊天栅格期待的字面 `248px`。这三个断言与本批未触碰的 `webui/src/styles.css` 有关；该文件 blob 为 `ceeb217b73162287e638ebc33560cf67901c4fb7`，与本次文档基线 `ac965b9` 相同。未改 CSS 或这些无关测试。`Window.scrollTo` 的 jsdom 提示及 Vite chunk warning 仍只是提示。

## 5.1 对照工作流 W1–W12 的状态

状态以 [HR Agent 工作流](../../HR_Agent工作流.md) 的 W1–W12 为准；“工程通过”只指所列本地接口、数据库、进程或组件证据，绝不替代其要求的真实模型与独立专业审读。

| 工作流 | 当前状态 | 已有证据位置 | 仍缺什么 |
| --- | --- | --- | --- |
| W1 | 工程通过；专业审读待定 | `backend/tests/test_hr_agent_b_journey.py`；公开模型 `evidence.json` / `result-1.md` | 独立专业审读与用户验收 |
| W2 | 新链工程通过；C 未实施；旧 D1 未修复 | `backend/tests/test_hr_agent_context.py::test_candidate_b_excludes_a_message_and_summary`、`::test_explicit_comparison_allows_both` | C 的候选人工作流验收；这些虚构对象回归不修复现行 D1 |
| W3 | 部分工程通过 | `backend/tests/test_hr_agent_b_journey.py`（对话/岗位读取） | 候选人入口、面试续作及专业验收，完整 W3 在 D02 |
| W4 | C/D 未实施 | 无 | 面试方案与真实记录场景 |
| W5 | C 未实施 | 无 | 批量材料、损坏/歧义与专业审读 |
| W6 | 工程通过；专业审读待定 | `backend/tests/test_hr_agent_b_journey.py`、标准并发/来源回归 | 公开模型产出的独立专业审读与用户验收 |
| W7 | C 未实施 | 无 | 固定情报包、更新与清除后的读取 |
| W8 | 部分工程基础；C 未实施 | A1/B 授权与撤权回归 | C 的历史/摘要/下载/模型发送完整联动 |
| W9 | 工程通过 | `backend/tests/test_hr_agent_worker_process.py::test_kill_restart_same_database_preserves_attempt_and_result`、`::test_real_process_reply_projection_and_silent_sending_recovery` | 无专业审读要求；仍非生产演练 |
| W10 | 部分工程通过；C 未实施 | `backend/tests/test_hr_agent_runtime.py::test_first_request_logs_exclude_sensitive_payloads`、文件隔离/诊断回归 | 真实个人材料前的 C 扩展验证 |
| W11 | C 未实施 | A1 预算机制回归仅作基础 | 公开研究、承重引用、部分交付与专业审读 |
| W12 | 工程通过；专业审读待定 | 发布身份/恢复回归；公开模型方法证据 | M1→M2/丢失的完整业务样例与独立专业审读 |

## 6. 专业质量怎么评审

本次真实模型沿用 AI-FAE-Agent `.env` 指定的 Anthropic Messages 网关配置，模型别名 `claude-opus-4-8`；未据此宣称官方型号或实际上下文窗口。只发送公开 JD 和专业方法，未发送真实候选人材料。

完整旅程是：竞对公开 JD 解读 → 用户补充本公司机器人岗位条件并选择要求校准方法 → 修正校准、保存分条提案 → 本地测试用户通过 HTTP 只确认第一条 → 独立会话读取正式标准。三个阶段完成，测试耗时161.78秒。

建议直接并读：

1. [公开输入、工具回执与完整结构化结果](artifacts/2026-09-10-hr-b-public-model/evidence.json)。其中 `results[].changes` 是提案条目，不能只看第三份 Markdown 的简短正文。
2. [第一轮竞对解读](artifacts/2026-09-10-hr-b-public-model/result-1.md)。检查公司/实体、执行载体、任务和要求强度的推断是否被材料支持。
3. [用户澄清后的岗位校准](artifacts/2026-09-10-hr-b-public-model/result-2.md)。检查“当前没有线上 A/B”“真机经历允许培养”“三个月后独立验证”是否真正改变判断，是否仍把待核实条件升级为必备。
4. [提案正文](artifacts/2026-09-10-hr-b-public-model/result-3.md)，与 JSON 的条目和 `confirmed_standard` 对照。检查未选条目是否保持未确认、临时要求是否与正式标准区分。

前两阶段记录6次模型调用、85,665个已扣记token，不是三阶段总账。第三阶段只导出了完成状态，未导出其逐步回执和回答；它的专业内容不能凭这个包独立核验。单次耗时也不是性能基线。

第一阶段允许模型自主决定是否读方法；第二阶段是用户明确选择方法。不能据此宣称任意问题下的自主选材和方法运用都已验证。

## 7. 必须保留的未验/未交付项

- **浏览器原生文件上传待补验。**选择器能打开，但扩展拒绝 `setFiles`；真实上传/扫描/解析 API 已验，不能代替“用户选文件后完成上传”的浏览器证据。
- **专业审读及用户 A+B 联合验收尚未完成。**已有模型产出供审读，不自行判定 HR 质量通过。
- **没有生产验收。**未验证云端凭据/TLS/对象存储、镜像构建、实际 Compose 启动与容量；原 A1 记录注明本机缺 Docker CLI。
- **现行 D1 没有修复。**新链候选范围隔离的测试不等于 P1 旧会话链修复；P2 生产数据盘点也未执行。
- **C–E 未执行。**批量候选人、真实个人材料、情报包适配、开放研究、其余招聘场景和正式切换不在本批交付。
- **真实候选人服务许可仍待定。**使用同一模型做公开测试不授权其处理候选人材料。
- **解析与引用限制保留。**无 OCR；macOS 没有与 Linux 同等的子进程地址空间强制限制；正文读取次数不是理解质量。旧基准的删除/替换提案如果无法展示原标准内容，界面要求修订提案。

## 8. 可直接交给 Claude 的评审任务

> 请在 `feat/hr-cloud-loop-b` 对 A0/A1+B 做一次联合评审，以本文件的准确基线和根目录两份主文档为准。先检查契约是否落到真实 API、持久化、模型循环、权限、材料、成果与页面，而不是只核对测试数量。
>
> 先按本报告 §5.1 将发现映射到 W1–W12，并保留“工程通过 / 部分 / C 未实施 / 专业审读待定”的边界。特别区分 W2 新链虚构对象回归已通过与现行 D1 未修复。再检查：同一输入与准确引用在重试/恢复/新发布后是否稳定；候选范围和摘要是否会污染后续工作；当前撤权能否阻断读取、下载及确认；提案部分确认、并发冲突和来源继承是否一致；模型能否发现/读取方法且不被固定步骤控制；页面是否表达真实保存、临时基准和正式确认的区别。
>
> 对真实公开模型产出另作专业审读，核验判断、反证、适用边界及用户澄清后的修正，不以调用成功或方法读取作为专业通过。缺失的第三阶段回答和原生文件上传证据请保留为未验证。前端报告所列 10 文件命令现为 225 通过；全量 `npm test` 的既存 `src/styles.test.ts` 三项失败须如实报告，不修改无关 CSS/测试来取得全绿。
>
> 请给出 A 运行底座、B 工程闭环、B 专业质量三个分别的结论；问题按阻塞/应修/建议列出准确文件位置、触发方式和影响，区分代码缺陷与尚未实施的 C–E 范围。本轮只读评审，不修改代码、运行生产命令或接触真实候选人材料。
