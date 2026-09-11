# C1 材料、草稿与候选人确认契约核查（2026-09-11）

本记录是本地代码核查后的实施建议，不是已实现能力。未运行生产查询、模型或真实简历；不修改迁移及业务代码。主代理保留 `098` 给材料授权证明；下文新增迁移从 `099` 起，实施时先核对全局迁移台账。两份根设计和 C 阶段计划由主代理统一维护。

## 1. 结论与实际复用边界

建议复用新 `MaterialService / MaterialParsingService`、现有五工具 Loop、加密成果及持久操作回执，新增薄的批次/逐文件编排和加密候选人登记。旧候选人服务的人工确认、幂等、逐项失败语义可借鉴；不调用旧草稿写入、旧确认 SQL 或旧 parser coordinator。

| 已核查文件 | 实际行为 | C1 决策 |
| --- | --- | --- |
| `backend/app/hr/candidate_service.py:66`、`candidate_routes.py:280` | 批次 1–100 个附件，按 owner/request/attachment 派生逐项 ID；岗位是必需项 | 复用逐项身份和同键重放语义；新入口允许未选岗位开始，不把岗位基准确认变成建档门槛 |
| `backend/control_migrations/070_hr_candidate_intelligence.sql:20,62,155,296` | `extracted_facts`、`stable_name`、`facts`、mutation 的 canonical_payload/result_snapshot、confirmation 的 confirmed_facts/canonical_payload 都是明文 JSON/text | 不能直接复用写入函数。只加一个 ciphertext 列而继续调用旧函数仍会泄露事件副本 |
| 同迁移 `complete_candidate_draft_v70` | 服务端以同 owner 的原件 hash/规范姓名找身份建议；模型提供的 identity IDs 必须为空 | 保留“建议不是身份确认”。C1 最小版以相同原件身份提供建议，并允许用户浏览自己的候选人明确选取；不做姓名自动合并 |
| 同迁移 `confirm_candidate_draft_v70` | 锁草稿、校验 row_version/ready/附件/已确认岗位基准；同键同载荷重放，异载荷冲突；原子创建 candidate/document/relation 和确认事件 | 保留事务与准确内容确认语义；新事务只写加密新 schema。已有旧合并分支主要追加文档/关系，不更新 candidate.facts，不能误称完整档案合并 |
| `backend/app/hr/candidate_parser_runtime.py:307,416` | coordinator 创建旧 `direct_agent/hr-bot` 会话；runtime 从旧 execution job/turn 读终态助手 JSON | 不能接到新 C1。它不是新的文本解析子进程；复用会重新进入被退出的执行链 |
| `backend/app/hr_agent/material_parsing.py:184`、迁移 `097` | PDF/DOCX 文本解析、不可变来源校验、租约、加密 sealed_content；当前同来源/版本去重，失败后 request 不会重新排队 | 继续用于文本阶段；补显式逐文件 retry generation，不能把再次 POST 当已有重试能力 |
| `backend/app/hr_agent/materials.py:170` | UTF-8 可直接形成 text_ref；PDF/DOCX 需解析。PDF ready 一直带覆盖限制，图片 PDF 没有 OCR | ready 只表示有解析结果；空文本和 coverage_notes 必须保留，不能显示为“简历已完整识别” |
| `backend/app/hr_agent/resources.py:125`、`contracts.schema.json` | candidate 对象校验仍读旧 `platform_hr.candidates`；ExactRef 没有 candidate；save_result 没有 candidate_profile | 新候选登记需要显式接入对象授权，结构化档案引用需新增契约，不能仅新增表后宣称工具已能读档案 |
| `backend/app/hr_agent/proposals.py:16,68` | 当前通过 candidate 对象、read/entry 范围识别个人来源 | 未确认的简历也必须在读取前登记个人材料属性，不能因尚无 candidate ID 混入通用标准 |
| `webui/src/workspaces/hr/HrCandidateWorkspace.tsx:24,279,464` | 旧人工确认要求编辑 JSON textarea | 复用业务语义，不复用 JSON 编辑表单 |

比较的三个方案：直接桥接旧服务代码量少，但违反加密和执行边界，排除；全面改造旧 v70 字段/SQL/事件/所有调用方会牵涉现行维护与资产迁移，超出 C1；采用新加密记录并复用新 Loop/成果，变更较小且与 E 的旧资产承接分开，因此推荐第三种。

## 2. 四种状态分别表达

| 层级 | 状态与含义 | 不能自动推出 |
| --- | --- | --- |
| 材料文本 | `not_started/queued/processing/ready/failed/unsupported`；ready 返回准确 text_ref、coverage_complete、coverage_notes | 已提取人名/经历、全文视觉覆盖、材料事实属实 |
| 模型档案草稿 | `not_started/queued/running/waiting_user/waiting_budget/ready/failed/cancelled`；ready 必须有经校验且实际保存的 candidate_profile 精确成果引用 | 真实身份已确认、候选人已建档 |
| 人工身份决定 | `unreviewed/confirmed/dismissed`；confirmed 必须记录用户看到的准确草稿、修订后的字段以及 `create_new` 或 `merge_existing` | AI 提取内容均属实、招聘评价或录用决定 |
| 候选人登记 | 只有确认事务提交后产生 candidate_id、不可变 profile_revision_id、document_id，及可选 position relation | 模型响应结束或文本 ready 不能伪造这些 ID/成功状态 |

批次响应逐文件给出这些独立状态；不定义一个会掩盖局部失败的“全部成功”字段。批次汇总可计数 `awaiting_review / confirmed / failed / still_processing`。unsupported、空扫描件、失效材料各有固定错误码；一个文件失败不回滚其他文件已保存结果。

## 3. 拟议最小 API

所有路径位于现有新链前缀 `/api/hr/agent`，沿用当前 AuthContext、`HrAccess.authorize_user`、CSRF 和私有 no-store 响应。owner 只由服务端取得。以下为新增契约，不是现有 API。

| 方法与路径 | 请求 | 返回／行为 |
| --- | --- | --- |
| `POST /candidate-draft-batches` | `Idempotency-Key`；`{attachment_ids:[UUID], position_id:UUID|null, text:"提取并准备人工核对…", budget_profile:"configured-id"}` | 202，`batch_id` 与有序 items；原子登记全部 item 身份与个人材料属性，后台独立推进。不向旧 draft 表排队 |
| `GET /candidate-draft-batches/{id}` | 当前 owner；可分页 items | 每项 attachment_id、original/text refs、parse 状态、extraction_work_id、profile_ref、review_state、row_version、错误码；文件名经附件服务解密授权返回，不另写明文列 |
| `GET /candidate-drafts/{id}` | 当前 owner | 上述 item 状态、解密后的 typed profile、准确 profile_ref、原件查看入口、覆盖警告、可核对的身份建议 |
| `POST /candidate-drafts/{id}/retry` | key；`{expected_row_version, stage:"text"|"profile"}` | 202；仅失败阶段创建新 generation。text ready 不重解析；profile ready 不重跑；work waiting_budget 使用已有追加预算操作，不以 retry 重置预算 |
| `POST /candidate-drafts/{id}/confirm` | key；`{expected_row_version, profile_ref, decision:{kind:"create_new"}|{kind:"merge_existing",candidate_id,expected_profile_revision}, profile:CandidateProfile, reviewed_coverage_notes:true|false}` | 201；确认精确草稿与用户修订，原子返回 candidate/profile/document/可选 relation 的真实回执。存在覆盖警告时必须明确已阅读警告，不能把此布尔值解释为用户证明全文解析完整 |
| `POST /candidate-drafts/{id}/dismiss` | key；`{expected_row_version}` | 未确认项忽略，停止/取消其本次 work；不删除已确认 candidate |
| `GET /candidates?cursor=...&limit=50`、`GET /candidates/{id}` | 当前 owner，最大100/页 | 授权分页候选列表／当前档案；姓名从密文读取。最小版不承诺姓名全文搜索；相同原件 hash 提示及人工选取足以防止自动错合并 |
| `GET /candidates/{id}/profiles/{revision}` | 当前 owner、准确 revision | 不可变已确认档案和 source refs；工具以新增 `ExactRef.kind=candidate_profile` 读取同一内容 |

入批拒绝混入其他用户或不存在附件，404 且不创建批次；重复 attachment ID 为422。用户已拥有但格式不支持的文件可登记为 unsupported，其他文件正常继续；被删除/撤权的文件不成为可读 item。请求正文不接模型服务、owner、candidate_id（create_new）、work_id 或任意 SQL/路径。

原件预览/下载沿用现有附件 ticket 服务，真实 ownership、时效、不可变版本、删除检查继续生效；不得把 object_ref 或存储凭据直接交给前端。

## 4. 新 Loop 中的档案提取

每个 item 创建一个独立的新 Loop work，明确引用该份简历 text_ref，不把整批附件送入每个 work。position_id 可作为用户选定对象，未建档时绝不借用猜测的 candidate ID。用户可见 text 就是本次提取目标；后台阶段编排不替代模型对内容的判断。

继续使用五工具：`list_resources / read_resource / save_note / save_result / ask_user`，不新增另一套模型调用器。给 `SaveResultInput.kind` 增加 `candidate_profile`，增加只在此 kind 下必需的 `profile:CandidateProfile`；其他 kind 禁止该字段。保留可读 body。提取 schema 如下：

- `display_name:string|null`；`name_evidence:[{ref:ExactRef,start:int,end:int}]`；`summary:string`；`facts:[{field_id:UUID,section:education|experiences|projects|skills|certifications|languages|awards|publications,text:string,evidence:[{ref:ExactRef,start:int,end:int}]}]`；`unknowns:[string]`。非空姓名必须有 evidence；生成 field_id 只作条目身份，不是候选人身份。确认 API 的 profile 使用相同展示形状，服务端将相较原草稿修改的字段另标为 `user_correction` 并关联加密确认操作，不要求用户为其亲自补充的信息伪造原文证据。
- 字段只收材料明确陈述和未知；不允许身份 ID、排名、保护性个人属性或任意扩展键。联系方式不在能力提取 profile 中；以后有明确联系用途时另设授权字段，不在 C1 自动发送无关联系方式。
- `source_refs` 必须包含该 item 当前 text_ref；保存时校验 evidence 属于这个精确来源、区间有效且已被实际 read_records 覆盖。读取区间合并后是否覆盖全部可提取文本由服务端计算并与 parser coverage_notes 一同展示；不能让模型声称“全文读完”替代记录。
- candidate_profile 保存成功时，同事务登记 item.profile_ref / row_version，才能呈现 ready；模型只发自然语言结束没有草稿成果时为 `profile_missing`。半截 JSON、未知字段、编造 source span 不能变为 ready。模型等待用户或预算时呈现 work 的真实状态。
- 已保存而后续回答失败仍保留可审阅草稿。人确认后封住该 item 的后续草稿更新；旧 lease、旧 generation、迟到 save_result 都不能覆盖确认结果。历史成果不可变，当前指针可在确认前按 CAS 更新。

现有 `_save_result` 的加密 sealed_document、operations sealed_arguments/receipt、model_attempts 加密请求/回复和 reference_edges 可直接承接模型草稿。不把模型结果先放入明文 staging 表。

**首个个人材料标记必须早于第一次模型读取。**新 `personal_materials` 绑定 owner/attachment/source_identity，草稿已确认与否不影响个人性。standards/proposals 的递归来源检查直接查询这个标记，阻止档案、附件及衍生成果进入通用岗位标准；不能仅检查当前 objects 是否出现 candidate。

模型处理条件须由服务端配置裁决并写入冻结配置身份，区分本地虚构 fixture 与获准真实个人材料；客户端提交 `synthetic=true` 不构成可信授权。当前没有已落实的真实个人材料服务配置，C1 工程验证只允许测试进程使用本地 fixture/model port，不因创建这些 API 自动打开真实材料生产处理。

当前 MaterialService 的 read_resource 返回原解析文本，尚无按用途去除联系方式的表示；仅让模型“不输出联系信息”不等于发送前最小化。真实个人材料入口仍须保持关闭，直到获准处理配置及有来源映射的最小化阅读表示另有验证；本次虚构样例闭环不能替代这项前置。

## 5. 最小新增存储与一致性

建议 `backend/control_migrations/hr_agent/099_hr_agent_candidate_materials.sql`：

| 表／扩展 | 明文允许字段 | 私人正文与约束 |
| --- | --- | --- |
| `candidate_draft_batches` | owner_id、batch_id、可空 position_id、created_at、created_by_operation | 目标 text/请求列表可复用 operations.sealed_arguments；唯一 owner+batch，准确同键幂等，不存姓名 |
| `candidate_draft_items` | owner_id、item_id、batch_id、attachment_id、source hash/identity、ordinal、text/profile generation、parse_id、extraction_work_id、profile_ref、review_state、row_version、固定 error_code | 原件身份与来源引用不含正文；每批每附件唯一；所有外键带 owner；不复制 profile JSON。创建 work 后以唯一 owner/item/profile_generation 绑定 |
| `personal_materials` | owner_id、attachment_id、source_identity、reason=`candidate_draft`、created_by_operation | 唯一 owner+附件版本；撤权不能靠删除此分类而让已有衍生资料变成公共资料 |
| `candidates`（新 schema） | owner_id、candidate_id、current_profile_revision、status、created_by_operation、timestamps | 不含明文姓名/事实。与旧 platform_hr.candidates 不双写；旧资产迁移属于 E |
| `candidate_profile_revisions` | owner_id、candidate_id、revision_id、sha256、source refs、previous_revision、confirmed_by、created_by_operation | `sealed_profile + key_version` 储存姓名、修订事实、人工身份决定、已阅读覆盖说明及准确原草稿 ref；版本不可更新 |
| `candidate_documents`（新 schema） | owner/candidate/document/item/attachment IDs、source hash、profile revision、status | 一项只能确认一次；合并把新文档追加到明确目标，不覆盖旧文档 |
| `candidate_positions`（新 schema） | owner/candidate/position IDs、status、created_by_operation | 可选关系；不强求旧 context_version_id，岗位标准在评估时按准确 standard ref 选择 |
| 扩展 `material_parses` | `generation`、`generation_attempts`、`row_version`；保留单调递增 attempts 用作 late-write fence | ready 版本不可覆盖；failed 重试增加 generation，重置本代尝试数，累计 attempts 不清零。每代最多3次崩溃恢复，显式再重试才新开一代 |

复用 `Repository._seal/_unseal` 的 AAD `hr-agent:<table>:<row-id>:<field>`、现有 ContentCodec/keyring。所有请求、确认理由/修订、回执若含私人正文均加密，操作命名空间如 `candidate_batch`、`candidate_confirm` 只包含固定名称，日志和 events 只记录 ID、状态、固定码。不得沿用旧明文 canonical_payload/result_snapshot；源文件字节维持已有私有附件存储规则。

确认事务顺序：验证当前用户及源证明 → 以 owner/key 查已有操作 → 同键载荷对比 → 锁 item 与合并目标 candidate → 校验 item.row_version、准确 profile_ref、source 当前有效性、target 归属及 expected_profile_revision → 加密 profile revision → 创建/更新 candidate 指针、追加 document、可选 relation → item confirmed → 加密原始回执同事务提交。不同 key 并发确认同一 item 只能一个成功，另一个409；同 key 重放返回原回执但仍先做当前权限检查。合并采用用户本次确认的整份新档案 revision，旧 revision 保留，不能静默猜测字段冲突如何合并。

批次到 work 的衔接不能跨连接假定原子：item 先持久化，协调器用稳定 `candidate-profile:<item-id>:<generation>` 提交新 work，再 CAS 回写绑定。若 work 已提交而回写前崩溃，重放同 key 找回同一 work。不是每次 worker tick 创建新 work。模型恢复使用现有 Loop lease、预算与操作幂等；用户 retry 是新 generation，旧工作显式终止/失效，新预算计费行为可见，不用 process crash 自动补满预算。

材料授权使用主代理 `098` 的准确 text-ref proof 接口；历史筛选/确认优先批量校验 owner、附件状态、失效/擦除、immutable identity 与 proof，真正读取正文仍验证字节。C1 不另写一个每条历史都重新下载附件的范围校验器。删除 source 后新读取、草稿确认、候选档案读取/下载、历史与结果递归均拒绝；已发送模型的数据不能声称撤回。

## 6. 人工核对页面与实施文件

新 C1 页面每份文件独立显示“文本提取／AI 草稿／人工核对／建档”状态。审阅页左侧原件/准确正文与覆盖说明，右侧姓名、摘要、经历/项目/技能条目及来源跳转。用户用普通文本输入、添加/删除条目与新建/合并选择修改内容；UI 负责序列化 JSON，用户不编辑 JSON。不预选有歧义的合并目标；确认成功显示服务端返回的候选人入口。

具体文件计划（实施前再校对是否已由并行工作新增）：

- 新建 `backend/app/hr_agent/candidates.py`：batch、逐项状态、人工确认、准确候选档案读取与事务；新建 `candidate_profiles.py`：有限字段/证据区间校验、save_result 特例；新建 `candidate_coordinator.py`：逐项 parser/Loop 提交及恢复，不调用模型供应商。
- 修改 `material_parsing.py`：显式失败重试 generation；修改 `worker.py`：有界协调器 tick；修改 `resources.py`：新 candidate owner 查询/准确 profile 读取；修改 `proposals.py`：未确认个人材料标记与递归拒绝。
- 修改 `contracts.schema.json`、`types.py`、`service.py`、`routes.py`、`repository.py`、`repository_views.py`、`config.py`：新增契约、服务入口、候选成果分支、只读视图和 schema-ready 检查。实际 DDL 只进099，不改已应用096/097。
- 新建 `backend/tests/test_hr_agent_candidate_materials.py`、`test_hr_agent_candidate_profiles.py`、`test_hr_agent_candidate_materials_http.py`、`test_hr_agent_candidate_materials_process.py`：分别覆盖加密/持久化、来源校验、真实身份 HTTP、提交/回写间崩溃和迟到 worker。
- UI 新建 `webui/src/workspaces/hr/CandidateDraftReview.tsx` 及组件测试，修改已存在的 `HrLoopWorkspace.tsx`、`HrLoopWorkspace.css`、`webui/src/hrLoopApi.ts`（类型当前同文件）；不能只改旧 `HrCandidateWorkspace.tsx` 就宣称新链可用。
- 同步正式 `docs/superpowers/specs/hr-cloud-loop/contracts.schema.json` 和 `contract-examples.json`，运行版契约保持一致；两份根 HR 文档与 C1 执行计划由主代理处理。

## 7. 验收切片与未闭环项

顺序：最小失败用例 → 真 HTTP/一次性 PostgreSQL → 必要进程故障 → 最后一轮文件选择/多文件状态/来源预览/表单人工确认 UI。

必测虚构批次：UTF-8、带覆盖警告PDF、DOCX、扫描空文本、不支持格式、超时各一份；只有获准可读文本进新 Loop。模型替身必须实际经工具保存准确草稿，不能向数据库手工写“成功”代替闭环。检查原表/事件/普通日志无姓名/独有经历/私人正文。

W5 至少有一份完整走完上传→text ready→Loop profile saved→用户修改→明确身份选择→真实 candidate/document/relation 回执→候选页读同一修订；另有部分失败只重试失败项、同键重放/异载荷冲突、并发确认、跨用户拒绝和内容修订409。原件hash相同/姓名相同不能自动合并。

W2/W8 补充未确认草稿A→B、混合摘要、明确双方材料、撤权后 profile及标准提案拒绝；删除发生在模型调用和保存之间也不得有效保存。W10 区分生产材料处理配置与本地 fixture；配置缺失不得回退旧 parser 或任意供应商。

目前仅有代码阅读和方案自检，无上述实现或新测试证据。OCR、真实简历专业质量、旧候选资产迁移、真实供应商审批与生产切换不在本建议中冒充已完成。
