# C1 材料、草稿与候选人确认契约核查（2026-09-11）

本记录先于实现固定复用边界和文件计划，随后补充本地实现及回归证据。未运行生产查询、真实模型或真实简历。材料授权证明使用 `098`，本次独立迁移为 `099`；两份根设计和 C 阶段计划由主代理统一维护。

## 1. 结论与实际复用边界

建议复用新 `MaterialService / MaterialParsingService`、现有五工具 Loop、加密成果及持久操作回执，新增薄的批次/逐文件编排和加密候选人登记。旧候选人服务的人工确认、幂等、逐项失败语义可借鉴；不调用旧草稿写入、旧确认 SQL 或旧 parser coordinator。

| 已核查文件 | 实际行为 | C1 决策 |
| --- | --- | --- |
| `backend/app/hr/candidate_service.py:66`、`candidate_routes.py:280` | 批次 1–100 个附件，按 owner/request/attachment 派生逐项 ID；岗位是必需项 | 复用逐项身份和同键重放语义；新入口允许未选岗位开始，不把岗位基准确认变成建档门槛 |
| `backend/control_migrations/070_hr_candidate_intelligence.sql:20,62,155,296` | `extracted_facts`、`stable_name`、`facts`、mutation 的 canonical_payload/result_snapshot、confirmation 的 confirmed_facts/canonical_payload 都是明文 JSON/text | 不能直接复用写入函数。只加一个 ciphertext 列而继续调用旧函数仍会泄露事件副本 |
| 同迁移 `complete_candidate_draft_v70` | 服务端以同 owner 的原件 hash/规范姓名找身份建议；模型提供的 identity IDs 必须为空 | C1 最小版允许用户浏览自己的候选人明确选取；不做原件或姓名自动匹配建议 |
| 同迁移 `confirm_candidate_draft_v70` | 锁草稿、校验 row_version/ready/附件/已确认岗位基准；同键同载荷重放，异载荷冲突；原子创建 candidate/document/relation 和确认事件 | 保留事务与准确内容确认语义；新事务只写加密新 schema。已有旧合并分支主要追加文档/关系，不更新 candidate.facts，不能误称完整档案合并 |
| `backend/app/hr/candidate_parser_runtime.py:307,416` | coordinator 创建旧 `direct_agent/hr-bot` 会话；runtime 从旧 execution job/turn 读终态助手 JSON | 不能接到新 C1。它不是新的文本解析子进程；复用会重新进入被退出的执行链 |
| `backend/app/hr_agent/material_parsing.py:184`、迁移 `097` | PDF/DOCX 文本解析、不可变来源校验、租约、加密 sealed_content；当前同来源/版本去重，失败后 request 不会重新排队 | 继续用于文本阶段；补显式逐文件 retry generation，不能把再次 POST 当已有重试能力 |
| `backend/app/hr_agent/materials.py:170` | UTF-8 可直接形成 text_ref；PDF/DOCX 需解析。PDF ready 一直带覆盖限制，图片 PDF 没有 OCR | ready 只表示有解析结果；空文本和 coverage_notes 必须保留，不能显示为“简历已完整识别” |
| `backend/app/hr_agent/resources.py:125`、`contracts.schema.json` | candidate 对象校验仍读旧 `platform_hr.candidates`；ExactRef 没有 candidate；save_result 没有 candidate_profile | 新候选登记需接入对象授权；本版使用 research 精确引用及 HTTP 人工档案读取，不扩展结构化档案工具契约 |
| `backend/app/hr_agent/proposals.py:16,68` | 当前通过 candidate 对象、read/entry 范围识别个人来源 | 未确认的简历也必须在读取前登记个人材料属性，不能因尚无 candidate ID 混入通用标准 |
| `webui/src/workspaces/hr/HrCandidateWorkspace.tsx:24,279,464` | 旧人工确认要求编辑 JSON textarea | 复用业务语义，不复用 JSON 编辑表单 |

比较的三个方案：直接桥接旧服务代码量少，但违反加密和执行边界，排除；全面改造旧 v70 字段/SQL/事件/所有调用方会牵涉现行维护与资产迁移，超出 C1；采用新加密记录并复用新 Loop/成果，变更较小且与 E 的旧资产承接分开，因此推荐第三种。

## 2. 本次已授权的最小实现（替代此前结构化档案扩展建议）

仅使用现有 `save_result(kind=research)` 的加密叙述成果作为未确认草稿；不新增 model/tool/schema。用户通过普通姓名、已审阅摘要表单以及明确 create/link_existing 选择建档。真实资料配置默认关闭，只在注入获准处理回调的本地 fixture 测试中推进。文本解析 ready、草稿成果已保存、人工确认、candidate/document创建分别呈现。

具体实现文件先于代码固定：`backend/app/hr_agent/candidates.py`、`backend/control_migrations/hr_agent/099_hr_agent_candidate_intake.sql`、`backend/tests/test_hr_agent_candidate_intake.py`。必要时仅在 `material_parsing.py` 增加失败重试 generation API；不修改其他代理负责的 existing()/材料授权证明。root负责 routes/service/resources/repository/worker/config/rootdocs/frontend 接入。

新 `CandidateIntakeService(repo, materials, processing_authorizer=None)` 接收经过HTTP真实身份授权的 owner UUID；每次业务操作仍通过 repo._scope 检查当前HR使用权限。处理回调默认缺失时不排模型工作，不允许请求字段 self-declare synthetic 放行。接口：

- `create_batch(owner, request, key)`：request 包含 attachment_ids(1–100,无重复)、position_id(可空)、text、budget_profile；原子登记batch、每文件item、personal_materials，源身份为不可变上传身份。全部对象归属先检查，不混入其他人的文件。
- `list_batches(owner, limit=50)`、`get_batch(owner,id)`、`get_item(owner,id)`：逐文件原件/text引用、parse状态、work_id、result_ref、未读区间、覆盖限制、review/state/row_version、固定错误码。
- `advance_one(worker_id)`：有界逐项协调；申请现有parser，解析ready后以稳定UUID幂等键创建独立thread/work，唯一选中该文件准确text_ref；不调用模型。工作保存经来源校验的research成果后进入awaiting_review；无读取证据/无成果的终态明确失败。兄弟项互不回滚。
- `retry_item(owner,id,{expected_row_version,stage:parse|profile},key)`：只重试对应失败阶段；parser失败重试需显式API及CAS；profile失败才增加generation提交独立work；等待预算不允许绕过已有预算追加操作。worker崩溃恢复重用已有work和预算。
- `confirm_item(owner,id,{expected_row_version,result_ref,display_name,summary,decision:{kind:create}|{kind:link_existing,candidate_id},reviewed_limitations:bool},key)`：校验准确成果、源当前有效性、row_version，再原子新建candidate或关联属于当前owner的新链已有candidate，追加document和可选岗位关系并保存加密回执。同键同请求原回执，异载荷或版本冲突409；不按名字猜身份、不改已有candidate的主档摘要；该文件的人工摘要保存在加密document review中。
- `list_candidates(owner,limit=50)`、`read_candidate(owner,id)`：私有新链candidate与加密人工摘要；所有派生材料权限先验证。撤权后不得从姓名列表或回执重放返回旧私人正文。

状态：item `queued/parsing/profiling/awaiting_review/failed/confirmed`；另有 parse_state、work真实state、coverage notes、unread_ranges，文本ready≠草稿ready≠confirmed。纯扫描空文本不创建模型work；文本覆盖不完整或模型未读完可以形成有明确限制的审阅草稿，但用户须明确已看限制。人审不等于证明全文准确。模型自然语言结束但未保存research成果为profile_missing。

迁移099只创建新schema表：candidate_batches（加密请求）、candidate_intake_items（元数据与加密细节）、personal_materials（owner+attachment+不可变来源；无正文）、candidates（sealed_profile）、candidate_documents（sealed_review与来源ref）、candidate_positions（ID关系）。姓名、摘要、用户目标、回执私人字段统一由repo._seal加密；不调用旧candidate表及旧明文事件。所有复合外键带owner，敏感正文不写日志，数据库只有固定错误码。

Root 集成要点：新增candidate对象授权查询新schema，personal_materials早于Loop读取，并在proposals递归来源检查中查询；没有candidate ID的未确认简历同样不能进入通用标准。精确材料授权用098 MaterialService.authorize_refs，一批metadata/proof校验，不重复读附件。真实正文读取仍验证字节。已有原件ticket权限不变。候选材料将来进入真实模型前仍需来源最小化与获准服务配置；仅不输出联系方式不算发送前最小化。

批次→work跨连接用稳定 `uuid5(item_id,"profile:<generation>")` key恢复，提交成功回写前崩溃也找回相同work；CAS防止旧generation覆盖新item。每次确认同事务校验源仍ready、身份未变、未过期/擦除，并锁住附件状态边界；外部读取在事务外完成。显式link仅追加该文件和审阅记录，用户主档原name/summary保持，避免隐式档案合并。

UI root在HrLoopWorkspace接入文件列表与审阅表单，原件/叙述草稿旁编辑display_name+summary；明确新建或选择私有候选人，用户不编辑JSON。研究叙述不是结构化字段提取；结构化档案字段与自动姓名候选匹配留后续。

## 3. 验证计划与交付记录

先写指定文件失败测试，再最小实现。测试复用真实HTTP上传/处理、一次性PostgreSQL、实际Loop持久操作；模型边界只用本地脚本。不向业务表伪造草稿成功；测试结果必须通过真实read_resource/save_result入库。

必测：逐项独立work/输入引用、unsupported或空文本失败隔离、重试仅失败项、读到末尾/未读范围、已有成果而回答失败、确认新建及显式同owner关联、wrong owner/撤权/源身份变化拒绝、双击与不同载荷幂等、CAS冲突、提交后回写前恢复、磁盘/数据库明文字段不含测试姓名或摘要。

本地实现结果：新增099六表，当前 schema 共24表；就绪检查要求096/097/098/099的准确校验和与锁函数执行权限。099 SHA256 为 `abb6b25c61e9031181f41093958a8ccf535fa9467b47b9f9d4cc5c97f6f2d184`。仅 intake_items 可 UPDATE，其余新增业务记录应用角色不可修改。`lock_candidate_source(uuid,uuid,jsonb)` 是固定 search_path 的受限 SECURITY DEFINER，只向应用角色提供原件来源/状态校验与共享锁，不授予附件 UPDATE、不返回正文。候选主档无 status/deleted 字段；来源撤权或删除通过 read_candidate 的完整来源授权拒绝读取。后续明确授权本子任务同时维护 config.py 的099就绪检查和 test_hr_agent_foundation.py。

已完成最小失败→修复验证：初始缺少 candidates 模块、099就绪检查失败；后续真实测试发现应用角色无附件/候选表 UPDATE 权限而不能直接 FOR SHARE，采用上面的窄函数并保持候选主档只读；独立红例复现改写工作输入后错误接收草稿、源身份改变后的批次重放、处理配置恢复后无法显式重试，均修正后通过。

验证命令：`cd backend && .venv/bin/python -m pytest -q tests/test_hr_agent_candidate_intake.py tests/test_hr_agent_material_parsing.py tests/test_hr_agent_foundation.py`，最终61 passed（29.20s），包含 `test_saved_narrative_survives_final_answer_failure`。覆盖真实HTTP上传及附件处理、一次性PostgreSQL应用权限/约束、真实Loop read_resource/save_result、原件身份变化、同键重放/异载荷409、并发确认CAS、显式关联不覆盖主档、可选本人岗位关联、空文本兄弟隔离、解析重试累计attempt fence、模型未读范围、撤权后的候选列表脱敏、加密数据库字段及操作载荷检查。最终回答被脚本提供方拒绝时，已保存且有读取证据的准确成果仍可人工确认。

恢复测试在 repo.submit 已提交、item 绑定前注入 OSError，再用新协调调用恢复，数据库仅保留同一个 work；这是持久边界异常注入，未执行真实进程kill。模型提供方仅替换为 ScriptModel；本次不是模型质量验收。专属候选 HTTP 路由/真实身份自动化、普通 works 出站门、proposals 拦截与页面交互由 root 集成验证，不计入上述服务层结果。无生产、真实模型、真实个人材料或浏览器验收，发布/产品验收仍待完成。旧v70资产承接仍属于E，不随099迁移或双写。

已知范围：叙述草稿不等同于结构化字段抽取；姓名匹配、候选编辑/删除、分页游标、旧资产迁移尚未提供。每文件专属work若被用户另行修改输入会标记 profile_scope_changed；该旧work生命周期仍由普通work接口管理，重试新建独立work不会自动取消用户改写的旧work。wait_budget/wait_user继续使用原工作交互，不允许retry重置预算。

装配接口：构造 `CandidateIntakeService(repo, materials, processing_authorizer=approved_callback)`；普通模型出站检查需由 root 显式装配相同获准回调为 `repo.personal_processing_authorizer`，默认两处均为 None；本服务构造器不暗中授予出站权限。Worker循环调用 `advance_one(worker_id)`，并保留现有 `materials.parsing.process_one(worker_id)` 和普通Loop worker；本服务不调用模型、不启动额外旧队列。候选对象权限使用 `read_candidate(owner,id)`，该方法先以空对象集合验证HR身份，再授权全部原件/result来源，最后解密姓名/摘要，不递归验证candidate对象。个人标准提案检查通过 `platform_hr_agent.personal_materials(owner_id, attachment_id)` 检查并沿原有 reference_edges 递归；标记在create_batch同事务先于任何Loop工作落库。
