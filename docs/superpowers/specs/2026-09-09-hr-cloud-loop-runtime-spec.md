# HR 云端 Loop 接口层实施规格（A0）

日期：2026-09-09；修订：2026-09-10。状态：规格已编写，待接口层评审；没有创建运行模块、迁移 SQL 或生产服务。上位：[总体架构](../../../HR总体架构设计.md)、[工作流](../../../HR_Agent工作流.md)；执行跟踪：[交付计划](../plans/2026-09-09-hr-cloud-loop-delivery.md)。

本规格固定首批岗位校准的请求、工具、工作记录、授权与恢复接口。模型服务商尚未确定不阻止这些契约成立；A1 先用提供方替身，真实模型验收另使用获准配置。首批完成不代表整个 HR 已迁移。

配套 [JSON Schema](hr-cloud-loop/contracts.schema.json) 定义请求/返回形状，[接口样例](hr-cloud-loop/contract-examples.json) 提供正反例。它们都是文档资产，不能被生产导入后冒充已经实现的服务。Schema 验证字段形状及可由单个文档判断的条件：提案 action、kind 与 payload、修订成对、预算追加非零、引用种类和错误返回。权限、引用真实性、跨记录关系、数值间比较、并发与运行状态必须另有接口/数据库测试；Schema 通过不代表这些已实现。

## 1. 固定选择与现有事实

| 决定 | A0 固定内容 | 理由/边界 |
| --- | --- | --- |
| 执行单元 | 新 `app.hr_agent` 包，一个云端 Worker 直接循环模型与工具 | 不改 `DirectWorker` 增加第二套语义，不经 MetaBot/Relay/CLI |
| 工作与对话 | `thread_id` 组织交互；`work_id` 表示一个可继续的用户目标；每次新输入生成递增输入修订 | 线程不授予整段历史的使用权；普通提问不要求先建岗位 |
| 历史范围 | 消息、工具结果、笔记带对象/来源标记，每轮先筛选再压缩 | 支持单人、多人比较与无岗位工作；不按候选人永久分裂产品对话 |
| 成果 | 一份稳定结果身份、多份不可变正文修订、多处对象链接 | 关联岗位不复制正文；改写才创建修订；旧引用仍定位旧正文 |
| 工具 | 首批五个通用工具，参数与返回见 §4 | 方法/成果发现与正文读取共用资源协议；没有关键词场景路由 |
| 确认 | 用户 HTTP 操作，不作为模型可调用工具 | 精确选中条目 + 预期基准 + 真实身份；保留未选条目 |
| 并发 | 每工作串行模型步骤及工具操作；不同工作可并行 | 第一批降低副作用恢复复杂度；并非固定 HR 思考流程 |
| 迁移 | 独立 `hr_agent/`，占用统一账本中的 096，显式执行 | 094/095 已被旧链使用；运行开关不做 DDL |

代码核查：master `ba979db` 的代码仍基于 `6777d22`；较新 HR 路径以 `38dfc7a` 核查。FAE 历史设计有事件适配、工具调用和来源协议，但同时含产品门控、`submit_answer` 强制终稿和内存循环状态；不整体复制。选择“对象标记 + 筛选”是新链的明确设计，不宣称 P1 的现行修复已经选型或上线。

依赖无环：总体架构 §4.1 给出预算单位 → 本文 §9 给出 P3 口径 → §6 落持久字段。P1 提供旧历史形状与处置比较；新链不等待旧链修复上线，也不继承旧 SQL。P2 生产计数影响承接与切换，不影响 A0。

## 2. 身份、对象、引用与返回约定

### 2.1 公共类型

精确字段见 Schema `$defs`。本节同时说明 Schema 条件和需数据库验证的约束。

| 类型 | 字段与规则 |
| --- | --- |
| `ObjectRef` | `{kind,id}`；kind 为 position/candidate/company/topic。岗位与候选人 id 必须是现有 UUID，公司/专题用稳定键；同种对象去重、排序仅用于规范化，不表达业务重要性 |
| `ExactRef` | `{kind,id,revision,sha256}`；kind 为 material/method/result/intelligence/standard。全部不可省略；sha256 为正文规范化表示的摘要。revision 大小写不敏感地拒绝 current/latest。result/standard 的 id 与 revision 均为 UUID；material 的规范稳定 id 为 `{attachment UUID}:original` 或 `{attachment UUID}:text`，原文 revision 为字节 hash，解析文为 `hash:parser_release`；method/intelligence 保留稳定键 id 和固定发布 release revision |
| `WorkInput` | `thread_id:null\|UUID,text,objects[],references[],budget_profile`；owner、模型端点、角色包、工具权限均由服务端配置与校验，客户端不能注入 |
| `AppendInput` | `expected_input_revision,text,objects[],references[],question_id:null\|UUID`；objects/references 是本轮完整选择，不是隐式追加。客户端可先呈现仍有效的选择供用户继续，服务端每次重新校验 |
| `ResultView` | 精确 ref、kind/title/body、objects、source_refs、preceding_refs、base_standard_ref、changes、basis、access_state；basis 区分已确认标准、官网原文、用户临时要求，kind 不强制正文模板 |
| `Change` / `ProposedChange` | 输出Change包含服务端生成的change_id；输入ProposedChange仅含action、target_item_id、text；add 要求 target=null 且 text 非空；replace 要求 target 存在且 text 非空；remove 要求 target 存在且 text=null |
| `Problem` | code、可展示 message、retryable、有限 details；不回传凭据、SQL、未获准对象名或整份模型响应 |

ID 由服务端生成。客户端 Idempotency-Key 是 UUID；模型工具不填写该键，工具操作身份由已落盘模型步骤和调用槽位派生。SHA 的规范化固定为 UTF-8 JSON、键按字典序、无额外空白、保留数组业务顺序；资源各 kind 的正文表示由资源适配器固定并在 read 返回前复算。单纯关联对象不改变正文摘要，关联元数据有单独审计事件。

传输防护初值：每个 JSON 请求最多 256 KiB、用户文本最多 64 KiB（UTF-8），原附件通过附件接口传输；不是限制库规模或 HR 场景对象数量。超限返回 413，并指向文件输入方式；不静默裁剪。列表每页 50 条，游标必须绑定查询、owner、目录身份与过滤条件，失效返回冲突，不能混页。

`BudgetAmounts` 是可全零的非负额度（供 reserve 使用）；`BudgetAddition` 至少一项大于零，不能把独立闸门测试的全零 reserve 一并拒绝。`SaveResultInput` 的 result_id/expected_revision 必须同时为空或同时非空。standard_proposal 要求至少一个 change；其余 kind 的 changes 必须为空且 base_standard_ref=null。base_standard_ref 非空时只能是 standard 引用。

`ConfirmError` 是确认端点显式错误联合：修订冲突只能走 `StandardConflict`，普通认证、授权、输入和资源错误走不含 `revision_conflict` 的 `Problem`。`StandardConflict` 的 details.conflict_kind 必为 standard_revision 或 proposal_revision，current_revision 字段必须存在，值为当前可见 UUID（当前无标准才可为 null）；裸 `Problem(code=revision_conflict)` 不能绕过该字段。输入和预算修订冲突使用普通 Problem，current_revision 是正整数；其他错误不伪造标准修订。返回当前身份不代表客户端可直接替换旧提案基准。

### 2.2 最小授权算法

1. 中央身份中间件取得真实 `AuthContext`，检查 HR 入口资格和写请求的 Origin/CSRF；再由新 `HrAccess` 检查业务对象/材料。Worker 仅持工作身份与执行权，不携带浏览器 Cookie。
2. 接受输入时，检查当前用户可用对象、附件保留/删除状态和所有准确引用。选中一个成果并不自动授权其全部祖先；祖先仍逐一按当前用途验证。
3. 无岗位咨询使用当前工作选择的材料与公开方法/情报。自然语言可以直接开始工作；明确使用已知对象时客户端提交其对象引用。尚未解析的自然语言对象名由 Agent 澄清或使用当前可见选择确认，不能把模型猜到的名字/ID视为授权。页面不要求完整建档才能提问。
4. 每次读、组装模型输入、保存、继续、确认和下载重复校验：用户权限 ∩ 当前明确对象/本工作材料 ∩ 当前有效来源权限 ∩ 获准动作。
5. 跨用户对象查询先在数据库按 owner 过滤，后解密；对外返回 404 `not_found`，不泄露其存在。对本工作已知但超出当前用途的引用，工具返回 forbidden / scope_denied；删除旧正文返回 missing / reference_unavailable；技术读取失败返回 unavailable，不能伪装为空。

A0 不把自然语言意图识别改成关键词表。用户新输入改变对象后产生新输入修订，不直接放宽旧修订的 scope；关联一个结果到岗位也不解除其候选人来源约束。

## 3. 用户 HTTP API

前缀固定 `/api/hr/agent`。沿用平台身份中间件，并在中央路由授权集合登记全部精确路径。GET 只读；所有 POST 需真实用户、Origin/CSRF 和 `Idempotency-Key`。写操作去重键作用域为 `(owner_id, HTTP方法, 规范资源路径, key)`，请求哈希包含完整正文；同键改内容 409，重试先重验当前权限再返回已提交回执。

| 方法与路径 | 输入类型 | 成功返回 | 必须处理的错误 |
| --- | --- | --- | --- |
| GET `/materials/{attachment_id}` | 无；先走 §3.2 的现有上传入口 | 200 MaterialView：状态、原文件引用、解析正文引用 | 非本人404；解析未完成返回状态，不造正文；附件删除/过期410 |
| POST `/works` | WorkInput | 201 WorkView；重复同请求 200 | thread 非本人404；无效引用422/410；配置不就绪503 |
| GET `/threads?cursor=…` | 无 | 200 ThreadPage，仅当前用户线程 | 未登录401，游标错误409 |
| GET `/threads/{thread_id}/works?cursor=…` | 无 | 200 WorkPage，仅当前用户工作 | 非本人404；状态与单项WorkView一致 |
| GET `/works/{work_id}` | 无 | 200 WorkView | 非本人404；不能返回私有其他工作的 pending question |
| GET `/works/{work_id}/messages?after=N&limit=L` | N为entry seq；默认100条，最多200 | 200 MessagePage：用户/助手/提问正文及options，受限项为占位 | 非本人404；撤权正文不返回；不输出model_request或工具内部密文 |
| GET `/works/{work_id}/events?after=N&limit=L` | N≥0；1≤L≤200，默认100 | 200 EventPage，按 seq 递增 | 错 owner404；过期游标410，返回可重新读取状态的地址，不重启工作 |
| POST `/works/{work_id}/inputs` | AppendInput | 202 WorkView | 旧 input_revision409；question 不属于当前等待409；权限撤销403/410 |
| POST `/works/{work_id}/cancel` | CancelInput | 200 WorkView | 已终止重复取消返回现状态；不得回滚此前保存内容 |
| POST `/works/{work_id}/budget-extensions` | ExtendBudgetInput | 200 WorkView | 预算基准改变409；追加全零422；未批准超服务上限422 |
| GET `/results?thread_id=…` 或 `?object_kind=…&object_id=…` | 两种范围恰选一种；可选 cursor/kind | 200 ResultPage；空为 items=[] | 不接无范围全库查询；对象无权404 |
| GET `/results/{result_id}/revisions/{revision}` | revision 为准确 UUID | 200 ResultView | 非本人404；已知正文被删除410；来源失效403且无正文 |
| POST `/results/{result_id}/links` | LinkResultInput | 200 LinkReceipt | 预期正文修订非当前409；错 owner404；对象范围不相容422 |
| POST `/positions/{position_id}/standards/confirm` | ConfirmInput | 201 StandardView；重复200 | 409 ConfirmError：基准/提案正文改变走 StandardConflict；普通未登录/未授权/输入错误走不含 revision_conflict 的 Problem；选择不存在/重复/相冲突条目422 |
| GET `/positions/{position_id}/standards/current` | 无 | 200 StandardView | 当前无标准404；不自动回退到官网或未确认草稿 |

messages接口只投影user/assistant/question条目，按entry seq稳定分页，question带question_id和所属input_revision。受当前范围或来源限制的条目保留visibility=restricted、body=null、options=[]，不暴露原文。浏览器重开先读WorkView，再读messages显示已提交回答或待答问题；问题正文不能只保存在内存里。

`WorkView` 中的 result_refs 只包含调用者当时可读的结果引用；被限制的结果以数量/状态提示说明，不能返回已撤销正文摘要。列表 `ResourceItem.description` 同样在授权后生成。未完成模型的流式文字不作为持久回答；A1 使用事件轮询，后续 SSE 复用相同 seq，不发明第二个完成信号。

用户从 events 读取 `tool_error` 的 tool_status、error.code/retryable 和模板说明，以区分 unavailable/forbidden/conflict；`recovery_started` 表示新执行者已进入实际恢复。二者不伪装成助手正文，也不新增 work state。前端重开同时补读事件；普通日志不复用整个 Event 或 Problem 对象。

### 3.1 连续请求样例及确认事务

配套 JSON 中 UUID 和摘要是虚构样例身份，不是实际资料；每个请求需通过本节语义检验，结构正例不代表数据库里已存在对应对象。

1. 按 §3.2 上传公开 JD，轮询附件/解析状态并取得 text_ref 作为 material ref M；POST works 使用 `thread_id:null,objects:[],references:[M]`，返回工作 W、线程 T、输入修订 1、queued。无需新建岗位。
2. Agent 读取 M，自主发现/选用方法，`save_result(kind=role_calibration,result_id=null,expected_revision=null,objects=[])` 返回 R1；用户从线程成果列表可找到同一 R1。
3. 用户通过现有岗位建档/选择入口得到岗位 P；POST works/W/inputs 带 `expected_input_revision:1,objects:[P],references:[M,R1]`，返回修订2。POST results/R1.id/links 绑定 P，正文仍是 R1；岗位与线程读同一 result_id/revision。
4. Agent 保存 `standard_proposal` P1：base_standard_ref=null（此岗位无已确认标准）、changes 为两条 add。用户界面逐条展示准确 P1 正文，提交 `proposal_ref:P1,selected_change_ids:[c1,c2],expected_standard_revision:null`。服务端确认基准与提案一致，原子生成 S1，用户身份写入 confirmed_by。提案本身仍是待确认内容，不被重写。
5. 另一次提案 P2 基于 S1。确认前同用户另一会话已创建 S2，POST confirm 携 `expected_standard_revision:S1.revision` 必须返回409 `revision_conflict`、当前 S2 revision，不应用任何 c1/c2。用户重新阅读差异并取得基于 S2 的提案后，用新去重键确认；不能把旧提案的基准字段在请求里改成 S2 绕过冲突。

confirm 事务同时锁岗位当前标准指针、写操作回执和标准新修订；检查 proposal kind/准确摘要/base/目标岗位/当前权限。只应用 selected_change_ids，其他条目保持原值。多个选中 change 修改同一 target 拒绝422；add 由服务器分配正式 item_id 并记录 change→item 映射。模型不能调用该 HTTP 路径；它的可见工具不包含确认工具或任意 HTTP/Bash。

### 3.2 无岗位材料上传、查询与精确引用

现有 `backend/app/attachments/conversation_routes.py` 的 `/api/v1` 路由可复用。实际 `BeginUploadRequest.conversation_id` 可为 null；不用创建旧 conversation，也不触发旧 HR 简历解析任务。以下沿用附件自身身份/Origin/CSRF与上传协议，不套用新 Loop 所有 POST 的幂等键约定：

| 已有方法与路径 | 顺序与结果 |
| --- | --- |
| POST `/api/v1/attachments/uploads` | conversation_id=null、original_name、declared_mime、declared_size；返回 upload_id/attachment_id |
| PUT `/api/v1/attachments/uploads/{upload_id}/content` | 上传真实字节；沿用长度/类型/校验边界 |
| POST `/api/v1/attachments/uploads/{upload_id}/complete` | 返回 AttachmentResponse；ready 只表示附件可用，不证明正文已解析 |
| GET `/api/v1/attachments/{attachment_id}` | 查询 uploading/validating/scanning/ready/quarantined/rejected/deleted 及 retained_until |
| POST `/api/v1/attachments/{attachment_id}/ticket` → GET `/api/v1/attachments/content/{ticket}` | 原文件预览/下载现有通道；这些旧入口当前不知道新 work scope，不能称为 W8 新成果下载已通过 |

**新增** `MaterialService.resolve(owner_id, attachment_id) -> MaterialView` 接到 `/api/hr/agent/materials/{attachment_id}`。适配现有 UploadService/DownloadService 的 owner、保留期及受控读取，服务器生成私有可见主体，不由模型填写。GET 只查询现有附件/解析产物，不触发模型、旧 CLI 或重新上传；尚无正文时 text_ref=null、parse_state=not_started/processing/failed/unsupported；页面根据parse_state展示处理状态，读取接口再以Problem区分实际错误。删除/过期不返回可用引用。

A1 的首个无岗位接口样例使用真实上传的 UTF-8 text/plain JD：解析适配器直接严格解码原字节，不启动模型，解析版本固定 `utf8-v1`；只读 resolve 可确定性计算该文本视图。B1 在真实 PDF/DOCX JD 验收前补齐受控解析产物及 coverage；不把当前附件缩略图服务当作已有全文解析器。PDF/DOCX 没有解析正文时返回 unsupported/processing，不能让 `read_resource` 返回文件名冒充全文。批量简历字段提取与建档仍属于 C1。

原文件与解析正文分别定引用：原文件 id=`{attachment_id}:original`，revision=原字节 sha256；规范正文为 `{byte_sha256, detected_mime, size_bytes}`。解析正文 id=`{attachment_id}:text`，revision=`{byte_sha256}:{parser_release}`；规范正文为 `{original_ref, parser_release, text, coverage_complete}`。ExactRef.sha256 对这些规范 JSON 求摘要，原二进制字节摘要单独保留，不混用。同一个 parser_release 必须确定性产出；正文/解析方法变化产生新 revision，禁止就地替换。读取返回 text 的码点区间，并重验原附件权限；不暴露对象存储路径。

`ResourceItem` 统一补 state、visibility、representation、original_ref；私有 visibility 的 subject_id 为当前获准主体，公开资料为 public/null。parsed_text 必须回链 original_ref；方法/成果使用 authored_text。上传 JD 仍是用户私有文件，内容公开不意味着上传副本向全平台公开。state 是材料访问/处理状态，不冒充官网新鲜度状态。

## 4. 模型工具契约

### 4.0 能力分区

| 分区 | 首批能力 | 调用边界 |
| --- | --- | --- |
| 模型可见的五个工具 | `list_resources`、`read_resource`、`save_note`、`save_result`、`ask_user` | 只由已提交模型步骤建立稳定槽位执行 |
| 仅用户 HTTP | `/standards/confirm`、`/cancel`、`/budget-extensions`、`/inputs`、`/links` | 真实登录身份、CSRF、权限与 HTTP 去重键 |
| 明确延后或禁用 | 外部写工具、文件交付、任意 HTTP、Bash | A1 不注册；后续按 §4.1 与 §6 重新评审 |

下表仅固定 A1/B 岗位校准阶段的五个模型工具，不是全部 HR 能力的最终集合。每个参数对象和输出对象均使用 Schema 对应 `$defs`，拒绝未声明字段。工具权限由当前工作状态与材料范围决定，方法选择由模型推理；不按场景关键词改变知识集合。

| 名称 | 参数 → 返回定义 | 实际语义与检查 |
| --- | --- | --- |
| `list_resources` | ListResourcesInput → ListResourcesOutput | kinds 必填；method 给用途/边界，result 支持历史发现，其余给材料/情报/标准目录。objects 省略时取当前范围，显式值必须为其子集；query 只作搜索定位。成功无条目为 empty |
| `read_resource` | ReadResourceInput → ReadResourceOutput | 准确 ref；offset 默认0、limit 默认8000个 Unicode 码点，最多20000。返回 offset/end/total/next_offset 与持久 read_id；不支持的二进制返回 invalid/unsupported_kind，不能称全文已读 |
| `save_note` | SaveNoteInput → SaveNoteOutput | 保存问题、证据、反证与下一步的自由正文；open_questions 为待解问题，reading_targets 为已发现但计划继续阅读的准确引用。只进工作记录，不形成岗位标准；系统补齐输入/来源限制 |
| `save_result` | SaveResultInput → SaveResultOutput | 新建 result_id/expected_revision 均null；修改均非null且基准为当前修订。objects 仅选当前合法对象；返回真实保存 ref。standard_proposal 要求非空 changes，base 可为空；其他 kind 要求 changes=[]、base=null；basis 按下述基准语义校验 |
| `ask_user` | AskUserInput → AskUserOutput | options 可以为空；服务端创建 question_id，持久提问并把工作转 waiting_user、释放租约。回答进入新的输入修订；不把普通模型问句当已经保存的等待状态 |

发现方法用 `list_resources({kinds:["method"]})`，读正文仍调用 read_resource；不单独强建“方法执行器”。模型可读零份、若干份，或按用户指定讨论。全文阅读记录使用每个 ref 返回区间的并集；收到全部区间只能证明内容可见，不能证明正确理解，专业质量另审。

source_refs/preceding_refs 是模型声明的引用；服务器检查真实可读、已返回的 ref 和依赖关系。**访问依赖由当前模型输入、此前已返回材料及前序成果的依赖并集保守生成**，不以模型少填 source_refs 来去除个人限制。显示引用可以是子集，不声称每个已读文档都支撑该结论。新输出引用已失效材料时拒绝保存，保留受限工作记录供解释。

工具统一 envelope：ok/empty 的 error=null；missing/unavailable/forbidden/conflict/invalid 的 data=null、error 为 Problem。只有列表允许 empty；读取失败不伪装空字符串。HTTP 错误采用 Problem，并映射400语法、401未登录、403当前范围拒绝、404隐匿对象、409冲突、410旧正文清除、413过大、422结构/语义无效、503配置或暂时不可用。

每个工具执行前检查取消、执行权与活动deadline；超时后不再启动新的研究读取，未执行读取槽位保留为aborted及未读记录。保存最后有效检查点仍由系统执行，不伪造新研究结果。

所有工具操作从完整、已提交的模型响应建立稳定槽位后执行。参数解码失败不能按 `{}` 继续；未知工具返回 invalid；一次响应含多个工具时按槽位顺序执行。ask_user 执行后其余未执行槽位标记 aborted，回答后由新模型步骤继续，不携带旧授权偷偷执行。

`basis[]` 在保存与展示时保持一致：confirmed_standard 必须引用实际已确认且本次可读的 standard；official_original 必须引用可核验的官方材料，用户自写的标题不构成官方证明；user_temporary 必须指向本工作真实 input_revision，可同时带本次上传的参考 ref。服务器核实来源类型，不因模型填写 kind 就赋予权威。岗位校准/JD/要求/标准提案须有 basis；普通研究确实没有岗位基准时可以为空。用户口述与上传未核实要求展示“临时要求”，不能因为成果已保存就显示“已确认”。提案非空 base_standard_ref 必须出现在 confirmed_standard basis 中，二者精确一致。

B3 保存 standard_proposal 以及 HTTP confirm 都检查 reference_edges 的传递来源：解析到候选人范围或已标记个人材料即422 personal_source_not_allowed，确认事务无标准写入。少填引用、改名或换对象链接不能解除限制。首批没有自动脱敏例外；用户可先保存私有 retrospective 等成果。未来人工脱敏需独立审阅产物/身份和发布契约，不能给当前模型一个“已脱敏”布尔值。来源未知文本中的个人原话仍需 W6 人工审读，图检查只证明已知个人来源被阻止，不宣称可识别所有自然语言个人信息。

### 4.1 明确延后的能力与验收

| 能力 | 归属与开放条件 | 当前不可判为通过 |
| --- | --- | --- |
| 官网事实核验 | B1 在在线读取前另补核验接口和 checked_at/freshness 状态；保留健康降级、来源陈旧、疑似下线、确认下线，24小时只是待产品确认的可配置初值 | A1 静态材料读取不证明官网校验；observed_at 不替代这些字段 |
| 公开信息调查 | C3 固定网络授权/公开来源工具后开放；五工具只读已发布或已授权材料 | W11 的开放调查部分；不把阅读现有包称为已联网调查 |
| 文件成果交付与下载 | B2 定成果文件身份与下载入口，C1 接个人来源撤权验证；每次下载校验当前身份和来源 | W3 文件支腿及 W8 下载支腿；已有附件 ticket 不自动继承新成果权限 |
| 批量简历与人工核对 | C1 补批次/单文件解析、歧义与人工更正接口 | W5 整条；单份公开 JD 上传不能替代它 |
| 个人材料模型出站与隔离 | A1 从首条请求执行 §8.4 基础日志/隔离；C1 在获准材料配置下扩展个人样例 | W10 真实候选材料部分；日志与隔离基础支腿必须在 A1 判定，不能整体延后 |

## 5. 上下文与模型适配

每次调用输入依次组装：固定角色/工具定义、当前用户目标和对象、已选准确引用、获准历史/笔记、已经返回的工具内容与必要目录。工具调用与返回配对，不能只保留返回而丢掉其请求；允许并列不同证据和反例，不以统一答案模板覆盖专业表达。

历史条目保存 `object_refs, local_work_id, source_refs, input_revision`。当前候选人 A/B 的例子：

| 历史条目 | 当前工作对象 | 选择结果 |
| --- | --- | --- |
| scope={P,A} 的提问/助手分析 | {P,B} | 不选；同岗位/同owner不足以带入 |
| scope={P,A} | 明确 {P,A,B} 比较 | 来源仍有效时可选 |
| scope={P} 且无候选人来源的岗位要求 | {P,B} | 可选，基准身份仍准确 |
| 摘要 scope={P,A,B} | {P,B} | 不直接用；从可读原条目重建 B 范围笔记或省略 |
| 未标记的旧个人文本/摘要 | 任意新候选任务 | 不导入新链；不能靠名字字符串过滤重获授权 |
| work_local 的上传 JD | 同一 work 后来绑定 P | 用户本轮仍选择该材料且有效时可选；不能因此授予其他工作 |
| 已读材料撤权后保存的摘要 | 任意工作 | 摘要依赖同步失效，缓存不绕过校验 |

新链只接受自己带范围标记的历史和用户明确选中的有效旧成果；不导入旧 conversation.summary 作为全局记忆。对象标记由服务端从本轮输入和已读依赖保守并集产生，模型不能把 scope 改成空来获得通用性。无关历史被排除后保留当前请求；无法充分恢复时明确询问，不编造省略内容。

### 5.1 摘要的来源与替换契约

entries 增加 `kind=summary`，以及 nullable `summary_provenance`（SummaryProvenance，密文保存）。summary 必须有 derived_from 非空数组，每项为本 work 不可变 entry_id/seq/input_revision；其他 kind 此字段必须为空。序号只用于顺序和检查，源身份以 entry_id 为准，不能用一个连续区间假定中间每条均被覆盖。只允许指向更早条目，拒绝环和跨 work；摘要的对象/来源是被覆盖条目的保守并集。摘要覆盖笔记或摘要时递归保留源身份并去重。

只有运行时的 `commit_summary(fence, attempt_id, provenance)` 能生成 summary：正文取已完整提交的摘要模型响应，provenance 取实际送入该次请求的原条目，模型不能改它。没有模型压缩时不造 summary；save_note 仍是研究笔记，不能用来隐式删除/替换历史。摘要记录与 context_compacted 事件同事务提交，原条目不删除。压缩模型请求 purpose=summary、tools=[]，其正文提交摘要，不能被终答投影为用户回答。

`read_selected_entries` 先判定每条的对象与传递来源；混合摘要不直接解密回送模型，从仍存在且获准的原条目重建，不能只改 scope。原条目不可得则省略并说明恢复限制。普通 note 同样继承本次输入/读取的个人范围，改成 note 不可绕过隔离。A1.1b 用可信 repository fixture 写 A/B 原条目及一条覆盖二者的 summary，再观察 B 的实际模型请求；另测原条目删除和明确 A/B 比较。fixture 构造的是持久来源关系，不是伪造业务完成。

角色/目录发布使用一个 `knowledge_release_id` 与 manifest sha；每个方法正文有 ExactRef。新工作检查配置、manifest 与正文一致；恢复读取原发布内容。原内容缺失转 blocked，显式更新内容须新输入修订；不会改 current 让旧任务继续。

模型接口 `ModelPort.stream(request: ModelRequest) -> Iterator[ModelEvent]`：事件为 text_delta、tool_delta、usage、stop；最终构建 `ModelReply(text, tool_calls, stop_reason, usage)`。只有完整 stop、有效参数和提供方完成标记俱全才提交步骤、执行工具。正文可作为正常回答，不要求通过 FAE 的 submit_answer 工具。中间 delta 只是临时进度，不能形成“回答结束”。

适配层不自动重试网络请求：一次实际 HTTP 请求对应一个持久 model_attempt，循环决定是否重试并重新扣预算。超时120秒上限且不超过剩余活动预算；新工作测试模型配置只有一个指定端点，不自动 fallback。错误分类为 rate_limited、transport_error、incomplete_response、provider_refused、invalid_response；无完整工具参数不执行任何副作用。

上下文窗口以配置模型 profile 的 context_window_tokens 为准，预留本次 max_output_tokens；首批另设主动压缩 input_trigger_tokens=12000、压缩后输入目标 input_target_tokens=8000（按 tokenizer 测量，不按字符数）。筛选并纳入新工具内容后的预计普通输入超过12000即尝试压缩已处理的历史，不等撑满模型窗口。优先保留当前问题、准确基准、未完成位置和待处理工具配对；替换正文使用 §5.1 的有来源 summary。仍不足则暂停说明，不能裁剪成假完整。压缩调用同样经过 ModelPort、持久步骤与预算，不开预算外的“摘要模型”。单次压缩输入必须小于模型窗口减输出预留；若待压缩集合过大，按完整条目/工具对分组，每组单独记账。无法在剩余额度内压到目标则保留检查点，转收尾/等待预算，不无限重压，也不把截断称完整读取。阶段笔记正文是否保留了承重证据仍需业务审读。

## 6. 持久模型与事务

全部新表位于 `platform_hr_agent`，不复用旧 turn 的 v6/v7 命令列。下表是迁移 096 的字段契约；UUID 均非空，除标 `?` 的字段外非空。公共时间为 timestamptz；jsonb 的枚举/对象形状由对应 Schema 与服务层校验。敏感正文不明文写入 jsonb。

每个私有实体含 `owner_id uuid, created_at timestamptz`。`sealed_*` 统一为 `bytea + key_version integer` 两列；subject 固定 `hr-agent:{实体类型}:{实体ID}:{修订或记录ID}` 作为 AAD。先 owner 过滤后解密，不宣称每人一把密钥。

| 表 | 主键与核心列 | 约束/索引 |
| --- | --- | --- |
| threads | thread_id；sealed_title | owner、created_at/thread_id 分页索引 |
| works | work_id；thread_id；input_revision bigint；state text；phase text；answer_state text；lease_epoch bigint；lease_owner text?；lease_until?；sealed_budget；sealed_checkpoint；budget_revision bigint；last_event_seq bigint；last_active_at? | owner/thread FK配对；认领索引(state,lease_until,created_at)；owner/work唯一；预算更新与认领锁 work 行 |
| inputs | (work_id,revision)；input_id；sealed_input；objects jsonb；role_release text；role_manifest_sha text；knowledge_release text；configuration_revision text | 每输入不可变；owner/work/revision 外键；revision 连续由行锁递增 |
| entries | entry_id；work_id；input_revision；seq bigint；kind text；objects jsonb；local_work_id?；sealed_body；source_refs jsonb；sealed_summary_provenance?；model_attempt_id?；operation_id? | unique(work_id,seq)；assistant终答与summary各按(kind,model_attempt_id)唯一投影；kind=user/assistant/tool/note/question/summary；仅summary必须有来源覆盖，其他kind为空；记录不被全局 summary 覆盖 |
| model_attempts | attempt_id；work_id；input_revision；ordinal bigint；purpose text；logical_step_id uuid；retry_no integer；status text；usage_observation_id uuid?；reported_usage_hash text?；sealed_request；sealed_reply?；provider_profile text；provider_request_id?；reserved_tokens bigint；charged_tokens bigint；raw_usage_cipher?；usage_quality text；started_at；ended_at? | unique(work_id,ordinal)及unique(work_id,input_revision,logical_step_id,retry_no)；status=prepared/sending/committed/interrupted/failed/superseded；每次实际网络尝试一条记录；purpose=work/summary，summary不进入终答投影 |
| operations | operation_id；work_id?；input_revision?；attempt_id?；slot integer?；namespace text；request_key text；request_hash text；status text；sealed_arguments；sealed_receipt?；lease_epoch? | unique(owner_id,namespace,request_key)；工具另 unique(attempt_id,slot)；status=prepared/committed/aborted；持久回执与本地业务写同事务 |
| read_records | read_id；work_id；input_revision；operation_id；ref jsonb；start_offset bigint；end_offset bigint；total_characters bigint；objects jsonb | unique(operation_id)；ref/区间只证明返回覆盖；区间必须落在正文内 |
| events | (work_id,seq)；input_revision；type text；state text；phase text；error_code?；error_retryable?；tool_status?；result_ref?；created_at | seq 在 work 锁内分配；message 由允许的进度模板产生，不存用户原文、模型推理或未经筛选的标题 |
| results | result_id；origin_work_id；current_revision uuid；kind text | 只维护指针；owner/result唯一；同一结果修订更新需 compare-and-swap |
| result_revisions | revision_id；result_id；sealed_document；sha256 text；objects jsonb；created_by_operation uuid | unique(created_by_operation)；正文、提案变更和基准不可变；owner/result/revision复合关系 |
| result_links | (result_id,object_kind,object_id)；linked_by_operation uuid | 只有用户链接操作增加跨工作业务关联；链接不修改已有修订、来源限制或 owner |
| standards | (owner_id,position_id)；current_revision uuid | 空标准用无行表示；确认前锁相应岗位行或事务级(owner,position) advisory lock，防首次确认并发插入 |
| standard_revisions | revision_id；position_id；previous_revision?；sealed_items；proposal_ref jsonb；selected_change_ids jsonb；confirmed_by uuid；created_by_operation uuid | unique(created_by_operation)；confirmed_by来自真实用户；非模型；items包含 change→正式item映射 |
| reference_edges | (dependent_kind,dependent_id,dependent_revision,source_kind,source_id,source_revision)；owner_id；source_sha256 | 维护 entries/results/standards 的保守来源依赖；不能因UI隐藏引用而删边；首次写检查环 |
| budget_extensions | extension_id；work_id；budget_revision bigint；addition jsonb；reason_cipher；created_by_operation uuid | unique(work_id,budget_revision)及operation唯一；追加大于零，累计原上限，不修改既有用量 |

`works.sealed_checkpoint` 存不可变修订的当前 `WorkCheckpoint` 投影；旧版保留为带工作范围的 note/事件引用，更新随相关工具提交/状态变更在 work 锁内完成。字段含 revision/input_revision、discovery_state、catalog_refs、readings、open_questions、pending_operation_ids、last_note_entry_id。新输入重新选取允许的 checkpoint，不将 A 的未完成问题原样带入 B。

readings 的每项按准确 ref 保存 total_characters、remaining_ranges 和 unread/partial/returned/unavailable。新选中的主材料、已开始读取资源以及 save_note.reading_targets 登记；reading_targets 必须是已授权选择/目录可发现的 ref，可尚未读正文，不当作承重引用。服务器按 read_records 区间并集计算剩余，0≤start<end≤total 是服务验证，total 未知时只能 unread/unavailable 且不造数字。只有全部区间已返回才为 returned，这不代表理解已完成。没有可枚举完整目录时 discovery_state=open；closed 必须有固定 catalog_refs 和完整枚举证据，模型说“找完了”不能改变它。open_questions 保存模型声明的待答问题，不以空列表证明专业任务完整。

暂停、取消、失败、进程恢复和 waiting_budget 均保留上述字段；未执行 operation 的稳定身份纳入 pending_operation_ids。预算已耗尽也由系统保存最后已知位置，不依赖额外模型调用才记录剩余。展示按当前权限投影：受限材料不返回身份/剩余正文，必要时只提示有受限未完成内容；sealed checkpoint 不能直接透传。

新模型状态不读取旧 `execution_jobs` 当运行事实。外部资源由适配器解析 ExactRef：附件服务查真实保留/绑定，方法读取固定发布目录，结果/标准查本规格表，情报解析固定 publication/body 身份。新表不为每份方法再复制一个业务对象；读取记录保留当时 ref。

事务边界：

- 接受输入：去重 → 验权 → 锁 work/thread → 保存不可变输入与 user entry → 更新 queued → 写 accepted/input_changed 事件与回执。新输入使旧 lease_epoch 失效，旧prepared/sending记录在同事务标superseded：确定未sending的prepared释放预留且不扣调用数，sending保留保守扣记；旧调用可完成网络但不得提交业务结果。
- 提交模型响应：核对 owner/work/input/lease_epoch/未取消 → 保存完整响应与 usage → 分配每个工具槽位的 operation_id → 提交。之后才执行工具，不能先写业务后补模型记录。
- 执行本地写工具：锁 work → 验执行权及当前依赖 → 锁 operation → 若 committed查回执 → 保存结果/笔记和回执、事件同事务提交。连接不跨模型网络等待。
- 读取工具：事务外取得正文，返回给模型前再验权限与执行权；保存 read_record、tool entry 和回执。如果二次验权失败，不提交明文给模型；重试可重读只读源，但 ref必须仍一致。
- 撤权与保存的交界：新库来源锁与工作保存串行化。旧附件/外部权限通过适配器在提交前重验，并在读取成果、组装下一次模型输入和下载时再验；不能承诺跨多个系统的原子撤回，更不能声称撤回已经发送的数据。
- 初期外部写工具不开放。后续需要跨服务副作用时，必须有对端幂等键/状态查询协议；仅有本库 operation 去重不足以证明外部恰好一次。

依赖遍历按准确 ref 去重，批量查询每组最多100项，总计最多10000个不同节点；超过返回 unavailable 并阻止受影响读取，不跳过验证。环是 invalid；这是工程保护，不限制 HR 方法数量。缓存键包含 owner、用途对象集合与授权状态身份；没有权威状态可校验时缓存只用于加速读取，不能替代权限判断。

## 7. 状态、执行权与故障表

| 当前状态 | 事件 | 下一状态与动作 |
| --- | --- | --- |
| queued | 有效 Worker 认领 | running；epoch+1，记录租约与活动开始 |
| running | ask_user提交 | waiting_user；question落盘，释放租约 |
| running/research | 本次回答完整结束、无待执行工具 | completed；answer_state=ended；保存成果与回答状态分别呈现 |
| running/research | 普通额度不能准入下一调用 | 持久phase=finalizing，保留触发原因，尝试总剩余额度内的收尾；若不足直接waiting_budget |
| running/finalizing | 收尾终答提交或总额度不足 | waiting_budget，保存实际局部回答与未完成范围；不宣称整个目标完成 |
| running | 角色/旧引用缺失、凭据配置不匹配 | blocked；保留原因，不切current或旧CLI |
| running | 可重试传输失败且预算允许 | 新model_attempt，受同一执行权/预算约束；初期每逻辑步骤最多重试2次 |
| running | 已达重试上限或拒绝/协议错误无法继续 | failed；已有成果保留，说明实际失败范围 |
| waiting_user | 对应question回答且输入基准一致 | 新输入修订，queued |
| waiting_budget | 合法预算追加 | queued且phase=research；用量不变，上限增加 |
| completed/failed/blocked | 用户显式补充新输入 | 校验依赖/配置后新修订queued；原成果和失败历史保留 |
| queued/running/waiting_* /blocked | 用户取消 | cancelled；epoch+1，停止新操作；不能反向撤销既有成果 |
| cancelled | 普通预算追加 | 409；用户要重做须新work，不自动复活取消任务 |

测试运行配置：租约60秒、每15秒续租，心跳线程/任务独立于模型流；超时认领使用数据库时间。租约失效后迟到 Worker 的业务提交必须被 epoch + input_revision + state 三重拒绝；不是只检查 worker_id。受限用量结算按下述独立接口处理，不放开业务提交。模型或工具外部调用期间不持行锁；取消操作能及时取得 work 锁。

| 故障点 | 持久事实 | 恢复动作 | 禁止的假恢复 |
| --- | --- | --- | --- |
| 接受成功、未认领 | queued+input | 任一有效 Worker 认领 | 浏览器重发一份新工作 |
| prepared但尚未标sending时退出 | 原attempt已准备、调用数尚未扣 | 新执行权重验原输入/来源后沿用原attempt；只发送一次 | 新建attempt或无故重复预扣 |
| 发模型后断流/进程被杀 | sending，尚无完整reply | 原attempt标interrupted，保留预算扣记；相同logical_step_id递增retry_no另开attempt | 解释半截JSON为有效工具调用 |
| 完整终答已提交、尚未finish_work时退出 | purpose=work的committed reply且无tool_calls | 从原reply幂等投影回答和终态，普通阶段completed、收尾阶段waiting_budget；不再发模型 | 重新问模型生成第二份答案 |
| 完整摘要响应已提交、未保存summary时退出 | purpose=summary的committed reply及请求来源覆盖 | 按attempt唯一投影summary/context_compacted；之后重建普通上下文 | 把摘要当终答或再发一次摘要请求 |
| 完整模型响应已提交、工具未执行 | committed attempt+prepared operations | 按原槽位继续工具，保持operation_id | 再问模型发明另一批操作 |
| 结果写成功、回执响应丢失 | operation与结果同事务committed | 返回原回执并继续下一槽位 | 重建第二份结果或再次确认标准 |
| 写事务未提交进程退出 | operation prepared且无结果 | 新执行权重验后执行一次 | 把未知写入当已成功 |
| 取消与写竞争 | work锁确定先后 | 写先提交则结果保留；取消先提交则该写被拒绝 | 声称能抹去取消前已完成动作 |
| 新输入到达旧模型还在运行 | input_revision增加、epoch失效 | 旧响应标superseded；新输入重新组装历史 | 旧响应覆盖新目标或用旧scope写入 |
| 浏览器关闭/重开 | work/events/results在库 | GET状态，按last seq恢复进度 | 关闭浏览器即销毁工作 |
| 进程停在等待用户 | waiting_user+question | 仅回答该question才继续 | 定时重新认领浪费模型调用 |

phase独立于state，仅为research/finalizing；running重认领保留phase，不从收尾重新开始研究。finalizing工具列表只包含save_note/save_result/ask_user，所有副作用仍验权；收尾模型不得调用read_resource/list_resources。没有足够模型额度时系统直接保存最后有效记录并转waiting_budget，收尾不成为额外无限循环。

每个逻辑模型步骤有logical_step_id；第一次retry_no=0，重试上限2表示最多三次发送。prepared未sending沿用原attempt；sending后结果不明保守记一次并创建相同logical_step_id下retry_no+1。完整工具批次处理完才开下一逻辑步骤；进程重启不重置retry_no。用户新输入产生新逻辑步骤，但累计预算保留。

计量与业务提交分离：`settle_usage(worker: WorkerIdentity, attempt_id: UUID, observation_id: UUID, usage: Usage) -> None`只允许可信配置的Worker/恢复处理器在内部调用，不注册用户/模型HTTP端点，不接受lease写入或工具参数。根据attempt记录的profile/实际传输关联校验usage，锁work与attempt，仅更新usage/charged_tokens；不得创建工具、改变输入、续租或把waiting工作自动启动。第一次完整reported usage记录observation_id与hash，重复同值无动作、不同值冲突；旧attempt已superseded仍可结算。没有晚到usage则保留estimated扣记，不能伪造精确账单。旧epoch的commit_model仍一律拒绝。

文件交付到 B/C 再开放；A1 的副作用故障测试使用真实新库 result/note写入，不造业务成功行。外部对象存储若随后接入，需要内容寻址/上传意图与提交回执，不能把 DB事务假想成能回滚已上传文件。

## 8. API 装配、加密、配置与迁移

### 8.1 复用与新增边界

| 实际现有位置 | 可复用部分 | 必须新增/调整 |
| --- | --- | --- |
| `control_plane/middleware.py` IdentitySecurityMiddleware；`routes_manage.py` authenticated_context/csrf_protection | 真实会话/Origin/CSRF执行与依赖钩子 | 新路由登记到 `control_plane/authorization.py`；不能单独拿csrf_verified布尔值当完整验证 |
| `hr/routes.py` 的 owner闭包与 AgentUseAuthorization | HR资格/只读用户判定的语义 | 拟建 `hr_agent/access.py`；现有owner是闭包，不存在可直接导入的公共require_hr_owner |
| `execution_relay/content_crypto.py` ContentCodec | seal_json/unseal_json、AAD与密钥版本 | 新HR独立装配与subject；不继承Relay开启条件 |
| `control_plane/crypto.py` IdentityKeyring.from_file | purpose=`platform-content-encryption`、32字节keyring读取 | 独立HR配置路径、schema readiness和keyring失败行为 |
| FAE `loop/adapters.py` | 事件归一化、完整工具调用缓冲、usage缺失标记的经验 | 禁用内部隐式重试，参数失败不得返回{}，适配本规格ModelPort；不直接跨仓运行时import |
| FAE `loop/runtime.py`、`tools.py` | 循环与工具结果设计参考 | 不复制产品选择门控、答案填表、submit_answer、所有错误折成tool_error |
| `attachments/conversation_routes.py`、`upload_service.py`、`download_service.py` | 无会话上传、当前owner/保留期检查、原字节受控读取 | 新 materials 适配器生成精确引用及原文/解析分离；现有缩略图不等于正文解析；详见§3.2 |
| Platform `agent_brain/loop_runtime.py` | 持久步骤与有效执行权的可读案例 | 旧委派/任务编排协议不作为Hannah直接Loop |

当前 `main.py` 在 Relay 开启时才初始化 ContentCodec，`config.py` 的 Relay配置关闭时会清空该路径。因此新HR配置独立读取内容密钥，即使导入同一个加密类型，也不代表保留Relay执行依赖。现有加密不等于新库全部字段已受保护；A1用解密探针验证跨owner先过滤。

### 8.2 拟新增运行配置

| 配置 | 初始值/必需条件 |
| --- | --- |
| `PLATFORM_HR_AGENT_ENABLED` | 默认0；为1才挂新路由与初始化HR依赖；未匹配schema/配置返回明确不就绪 |
| `PLATFORM_HR_AGENT_CONTENT_KEYRING_FILE` | 启用时必填；独立于Relay keyring开关，文件内容不进日志 |
| `PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE` | 启用时必填；配置id/revision、唯一endpoint、model、credential_file、tokenizer/context_window、usage归一化口径；A1可指定本地脚本提供方 |
| `PLATFORM_HR_AGENT_KNOWLEDGE_DIR` | B1启用真实资源时指向只读发布目录；A1指定独立测试目录 |
| `PLATFORM_HR_AGENT_BUDGET_PROFILE_FILE` | 启用时必填，明确限额/收尾预留/单步输出上限及12000/8000压缩触发/目标；不得空配置无限循环 |
| `PLATFORM_HR_AGENT_WORK_DIR` | 启用时必填专用根目录，§8.4的私有隔离与保留设置缺失则不受理 |
| `PLATFORM_HR_AGENT_DIAGNOSTIC_PROFILE_FILE` | 启用时必填；诊断默认disabled，可配置授权角色/独立密文目录/保留秒数；不得沿用普通日志目录 |
| `PLATFORM_HR_AGENT_LEASE_SECONDS` / `PLATFORM_HR_AGENT_HEARTBEAT_SECONDS` | 测试初值60/15；heartbeat < lease/2，启动校验 |

数据库采用现有平台 connection factory；不在模型参数中暴露 DSN。Worker是独立入口 `python -m app.hr_agent.worker`，从同一套只读配置和受限数据库凭据初始化；服务运行账户无DDL权限。`deploy/cloud/compose.yaml` 拟增 `hr-agent` profile 的服务定义，默认不启动，不改变旧HR接单配置；schema存在与运行启用是两个独立步骤。

### 8.3 迁移与隔离启动命令

现有 `control_plane/migrate.py` 只遍历指定目录，账本 `platform_control.schema_migrations(version PK,sha256,applied_at)` 在所有目录共用。根目录到088，master的hr_web到093，已用分支存在094/095。拟建：

- `backend/control_migrations/hr_agent/096_hr_agent_runtime.sql`：仅新schema、表、约束、索引及最小权限；不ALTER旧turn、v6/v7表，不引用hr_web表。
- `backend/control_migrations/hr_agent/README.md`：明确根迁移前置、共享编号账本、显式目录、部署/运行分离。提交SQL前再次核对各活跃分支及已发布库存；096若已占用就顺延并同步本规格/测试，不能复用同号不同checksum。

A1测试在本地启动一次性 PostgreSQL，复用现有测试的数据库fixture创建受限owner/migrator。执行前设置测试专用的URL文件；以下命令使用**将由A1测试fixture创建的路径**，不是现有生产配置，也不是本轮已执行命令：

```sh
cd backend
PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/tmp/hr-agent-a1/migrator-url \
PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner \
PLATFORM_CONTROL_MIGRATION_DIR=control_migrations \
.venv/bin/python -m app.control_plane.migrate
PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/tmp/hr-agent-a1/migrator-url \
PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner \
PLATFORM_CONTROL_MIGRATION_DIR=control_migrations/hr_agent \
.venv/bin/python -m app.control_plane.migrate
```

迁移owner名和测试DSN须与fixture创建值一致；不需要先执行089–095。A1的测试工具 `tests/hr_agent_support.py` 负责生成 `/tmp/hr-agent-a1/` 的模型/预算/密钥配置、启动本地提供方和Worker；路径不可复用其他人的任务目录，实际测试运行使用唯一子目录替换示例目录。

Worker子进程以 `sys.executable, '-m', 'app.hr_agent.worker'` 启动，fixture保存PID；故障测试用 `process.kill(); process.wait()`，随后以相同测试库/配置重启。命令与fixture仅在A1编写；A0不会生成空worker文件来让命令看似可用。

### 8.4 从首条模型请求执行的日志与文件边界

普通日志只通过 `emit_log(record: OrdinaryLogRecord)` 发出，Schema 拒绝未知字段，不接受任意 message/extra/exception 对象。允许字段如下；适配器、HTTP客户端、解析器和 Worker 禁用请求正文/响应正文/debug trace 自动输出。

| 允许字段 | 来源与限制 |
| --- | --- |
| at、event、state | 服务器时间与有限事件/状态枚举 |
| work_id、attempt_id、operation_id | 必要内部操作身份；不含人名、联系方式、文件名或原始请求去重键 |
| duration_ms、input_tokens、output_tokens | 数值；未知用量为null，不把凭据 token 混进 token 计量 |
| error_code、profile_revision | 允许错误枚举与配置版本标识；没有 endpoint URL、header、provider 原始错误或配置正文 |

这里存在两个有意分开的词表。`Problem.code` 是用户 HTTP/工具投影词表；日志提供方错误码另含 `rate_limited`、`transport_error`、`incomplete_response`、`provider_refused`、`invalid_response`。适配器把提供方错误保存为内部 attempt 分类并写日志；对用户只映射成适当的 `temporarily_unavailable` 或 `invalid_input` 等 Problem，不能断言两个对象的同名字段或枚举完全相同，也不能把原始提供方正文带入任一对象。

**禁止进入普通日志**：token/签名私钥/完整凭据、Cookie/Authorization、未脱敏简历、联系方式、面试原话、完整提示词/模型响应、原文件名与内容、任意异常字符串。用户 Event 的 message 也用服务端固定模板，不直接写 logger；数据库 sealed_request/sealed_reply 是受限工作记录，并不因此成为可日志化内容。Schema 只能约束结构，字符串字段还须来源于服务器枚举/配置身份；供应商错误内容不能塞进 profile_revision。

文件只写 `${WORK_DIR}/{服务端owner UUID}/{work UUID}/{attempt UUID}/`，根与子目录0700、文件0600，拒绝符号链接、路径穿越和跨任务读；上传名不拼接路径。测试 fixture 使用唯一临时根，Worker不读取宿主Home、平台密钥目录或其他work。材料解析入口禁用宏/主动内容、设置CPU/内存/时限，网络默认禁用；模型仅有§4列出的工具。任务临时文件默认结束后删除，崩溃遗留在租约失效且无运行者后按配置TTL清理；等待所需正文先进入私有持久记录，不能依赖临时文件续作。

受限诊断默认关闭，不提供用户/模型端点。工程诊断需获准运维身份、具体work目的和有效保留秒数，写独立加密存储，以服务器生成诊断ID定位；访问在独立受限审计中记录诊断身份/操作，不将诊断身份或正文加入普通日志白名单。普通日志只允许枚举错误码，不回传诊断正文或原路径。到期清理与主动删除由可信维护入口执行并记录回执；禁止以全量HTTP抓包/print(request)作为默认排错路径。真实候选材料开通前，保留时间、授权角色及模型服务仍须§6上位配置确认。

A1 `test_first_request_logs_exclude_sensitive_payloads` 给本地提供方的输入、工具异常和provider错误分别注入不同虚构敏感哨兵，捕获应用/适配器/HTTP客户端及Worker stdout/stderr，验证均不出现且保留可判别错误码。`test_work_files_cannot_escape_or_cross_scope` 测两个任务、符号链接与删除重启；`test_diagnostics_disabled_and_expired_unreadable` 测关闭、无权、到期和删除。用虚构文本可完成这些基础测试；不能因尚未接真实简历就延期。

## 9. 预算口径、初值与独立验收（P3 的规格部分）

多个上限的语义是“先到者停止准入”，不是要求三个上限在同一工作内同时触达。原120k不是逻辑矛盾，但缺少重发上下文的成本估算，不能拿它证明支持32轮较长调用。累计输入一般为各次实际输入之和；只有上下文持续增长而不压缩时才呈近似二次增长，压缩/筛选会改变曲线。

### 9.1 可审阅的校准测试配置

| 配置 | A1/B 工程初值 | 用途 |
| --- | --- | --- |
| model_calls | 32 | 每次真实网络尝试计1，含重试和压缩 |
| total_tokens | 600000 | 每次输入总量+输出总量累计；缓存输入不因低价格而免计 |
| active_seconds | 900 | running的实际活动时长，不含排队/等待用户 |
| reserve | 2次、40000 token、60秒 | 包含于上述总额，普通研究调用不能挪用；并不保证任何长度下都够两次收尾 |
| 单调用输出上限 | 4096 token | 首批可配置初值，实际请求还受剩余额度/上下文窗口限制 |
| cost_limit | 不启用金额闸门 | 未定供应商/价格，费用记unknown；不得把token换成虚构价格 |

预算不承诺32轮可达。4096是输出**上限**，并非每轮增长下界；8000/20000是读取码点上限，也不等于token。为避免乐观算例，采用不压缩且每轮实际输出4096、全部回送的压力轨迹：`T(n)=6000n+4096*n*(n-1)/2+4096n`，15次581520、16次653056；尚未计入读取正文和收尾reserve，实际会更早停止研究。旧400增长示例删除。

本次选择**量化主动压缩**，保留32次/600k/900秒作为各自独立安全上限，加入§5的12000输入触发/8000目标；压缩自身也消耗这32次和600k。即便每次普通输入≤12000，也不能把省下的输入宣称为固定多出多少轮，因为新阅读与压缩有实际费用。若平均延迟较高，900秒可能先到；这是时长保护，不意味着调用次数保护失效。B4记录实际分布，产品负责人据工程记录确定生产取舍；W11另用实测长阅读配置，不沿用这一组数字承诺通读3437份正文。

### 9.2 扣记、预留与恢复

- `charged_calls` 在每次实际发送前于 prepared→sending 事务中+1；若崩溃不能确定是否已发送，保守保留1，不因重试或取消扣回。适配器不得隐藏多次请求。
- prepared时只记录reserved_tokens，charged_tokens保持0；有效余额同时扣除尚未sending的预留。mark_model_sending将该预留转成charged_tokens，不能重复相加；取消确定未发送的prepared只释放预留。
- 发送前估计当前输入并加上本次最大输出作为 reserved_tokens；普通调用需同时满足 calls/tokens/time 扣除收尾reserve后的余额。普通余额不足先持久转finalizing，再按总剩余额度准入收尾；总额不足转waiting_budget，不把输出上限降成无法解释的截断回答。
- 收到usage：依据提供方profile把未缓存输入、cache write/read规范成不重复的 input_total，把可见/推理输出规范成不重复 output_total；保留原usage供受限核验。用实际总量替换本次预扣。若实际超预估，如实记录超额并停止后续调用，不声称token预算是精确供应商扣费上限。
- usage缺失/断流：charged_tokens保留本次预留值，usage_quality=estimated，不记0；取得可核验usage才调账，并留事件。输入tokenizer不可用时不声称精确估值，profile须提供经过测试的保守估算方法，否则blocked。
- 活动时长用Worker单调时钟计量，心跳定期落累计量和数据库观测时间；崩溃后未落区间最多按剩余旧租约时间保守补记，并标估算。新执行者不会把旧工作用量归零。
- 收尾只允许组织已获准证据、save_note/save_result/ask_user，不准新增研究读取来扩张范围。实际额度仍不够时系统保存最后一个有效笔记、已读区间和待执行位置，明确没有生成新终稿，不能为了“完整交付”超限调用。
- 预算追加是用户操作，increments逐字段非负且至少一项>0，有expected_budget_revision和去重键；增加上限，不重置累计值/原reserve；已取消工作不能由追加复活。新任务重做不是旧任务恢复，界面必须区分。

### 9.3 每个闸门独立可达的测试

| 样例 | 输入/配置 | 必须观察 |
| --- | --- | --- |
| 调用次数 | calls=3、reserve全0、tokens=100000、seconds=900；本地提供方每次返回小工具请求 | 第4次HTTP请求未发；已发3次；再重启也仍为3 |
| 累计token | calls=100、tokens=1000、seconds=900、reserve全0；提供方固定输入200/输出100、测试max_output_tokens=100，准入预留300 | 3次扣900；第4次准入拒绝，不是模型自己说预算不足 |
| 活动时长 | calls=100、tokens=100000、seconds=2、reserve全0；受控工具耗时超过2秒 | deadline后不发新调用；另起等待用户状态保持5秒，不额外消耗活动时长 |
| 增长轨迹（无压缩） | 固定初始6000、每次实际输出4096全部回送；calls=100、seconds充分、tokens=600k、reserve=0 | 第16次预留超过余额而未发送；与固定200/100扣记测试分开；不是把4096当必然下界 |
| 主动压缩与重启 | 真实 tokenizer 测量累计工具正文，预计普通输入越过12000但仍低于context_window；摘要提供方返回有来源短摘要 | 超阈值触发purpose=summary并扣账；目标≤8000或明确waiting_budget；kill/restart不丢derived_from/未读范围；摘要不被投影成终答 |
| 断流计量 | 第一次sending后断开且不回usage | attempt标interrupted；调用数/预留token保留；重试另计，不为0 |
| 恢复与追加 | 达到任一上限后kill/restart，再用户追加3次/60000 token/120秒 | 原用量保留；新budget_revision；同追加key重复不再增加 |
| 收尾预留 | 正常研究余额不足，剩余额度只够短收尾 | 不再读新文档；有实际保存回执或明确暂无新成果，未读量不消失 |

这些是隔离测试配置，不是按场景硬编码的生产分支。P3计量与候选初值在本规格完成；实测校准、供应商费用与产品确认仍未完成，不把整项P3勾选。

## 10. A1 的文件清单与公共接口

下面全部是**拟新增**文件/签名；当前仓库不存在不构成错误，也不得在A0创建空实现。HTTP wire类型来自配套Schema；Python内部类型集中在 types.py，避免不同任务各自定义同名不同结构。

| 文件 | 固定责任与公共入口 |
| --- | --- |
| `backend/app/hr_agent/types.py` | Schema对应的WorkInput/AppendInput/WorkView/ExactRef/ObjectRef/Problem/各工具类型；以及SummaryProvenance/WorkCheckpoint/MaterialView/ResultBasis与下列内部类型 |
| `backend/app/hr_agent/config.py` | `load_hr_agent_settings(environment: Mapping[str,str]) -> HrAgentSettings`；参数/文件校验与配置身份，不执行迁移；校验0<input_target_tokens<input_trigger_tokens<context_window_tokens-max_output_tokens |
| `backend/app/hr_agent/access.py` | `HrAccess.authorize_user(auth: AuthContext, *, writable: bool) -> UUID`；`authorize_scope(owner_id: UUID, objects: tuple[ObjectRef,...], refs: tuple[ExactRef,...], *, work_id: UUID\|None) -> AuthorizedScope` |
| `backend/app/hr_agent/repository.py` | 持久请求、执行权、步骤、操作、事件、预算；接口见下表；内部持有connection_factory与ContentCodec |
| `backend/app/hr_agent/context.py` | `build_model_context(repository: HrAgentRepository, resources: ResourceReader, fence: LeaseFence) -> ModelContext`；先验权/筛历史，再压缩/组装 |
| `backend/app/hr_agent/model.py` | `ModelPort.stream(request: ModelRequest) -> Iterator[ModelEvent]`；配置提供方适配与完整响应解析；不自动重试 |
| `backend/app/hr_agent/materials.py` | `MaterialService.resolve(owner_id: UUID, attachment_id: UUID) -> MaterialView`；`read_text(owner_id: UUID, ref: ExactRef) -> MaterialText`；A1复用上传与UTF-8正文，B1补解析器 |
| `backend/app/hr_agent/observability.py` | `emit_log(record: OrdinaryLogRecord) -> None`；固定白名单日志与错误映射 |
| `backend/app/hr_agent/work_files.py` | `WorkFiles.open(fence: LeaseFence, file_id: UUID, mode: str) -> BinaryIO`；私有目录与拒绝跨任务/链接；mode只读或独占新建，不从模型接路径 |
| `backend/app/hr_agent/diagnostics.py` | `DiagnosticStore.read(actor: DiagnosticIdentity, diagnostic_id: UUID) -> DiagnosticRecord`；内部受限读取/到期与删除，默认关闭，无模型HTTP入口 |
| `backend/app/hr_agent/resources.py` | `ResourceReader.list(scope: AuthorizedScope, query: ListResourcesInput) -> ResourcePage`；`read(scope: AuthorizedScope, request: ReadResourceInput) -> ResourceText` |
| `backend/app/hr_agent/tools.py` | `execute_tool(repository: HrAgentRepository, resources: ResourceReader, fence: LeaseFence, operation_id: UUID) -> ToolOutcome`；读取已保存参数，不接受调用者重新提供不同参数 |
| `backend/app/hr_agent/runtime.py` | `run_work(repository: HrAgentRepository, model: ModelPort, resources: ResourceReader, fence: LeaseFence) -> WorkView`；执行步骤与恢复，不包含网页/行业规则 |
| `backend/app/hr_agent/worker.py` | `main() -> int`；claim/心跳/退出信号/独立进程；入口只认领新表 |
| `backend/app/hr_agent/results.py` | `ResultService.list(owner_id: UUID, query: ResultQuery) -> ResultPage`、`read(owner_id: UUID, ref: ExactRef) -> ResultView`、`link(owner_id: UUID, result_id: UUID, request: LinkResultInput, key: UUID) -> LinkReceipt` |
| `backend/app/hr_agent/standards.py`（B3实现） | `StandardService.confirm(owner_id: UUID, position_id: UUID, request: ConfirmInput, key: UUID) -> StandardView`；当前指针并发与条目应用 |
| `backend/app/hr_agent/routes.py` | `build_hr_agent_router(service: HrAgentService, results: ResultService, materials: MaterialService, standards: StandardService\|None) -> APIRouter`；B3前确认端点返回503能力未开放，不能伪造标准 |
| `backend/app/hr_agent/service.py` | `HrAgentService`：受理/输入/取消/续作/查询的身份入口；把已授权参数交给repository；未授权不先建work |
| `backend/app/hr_agent/__init__.py` | 包入口，不导入即启动Worker |

`MaterialText(ref, original_ref, parser_release, text, coverage_complete)` 是适配器内部完整文本，read_resource再分页；不带原字节路径。`DiagnosticIdentity(actor_id, roles)`只由可信运维认证装配产生；`DiagnosticRecord(diagnostic_id, work_id, expires_at, sealed_payload)`在单独授权后解密，普通路由和模型均不可取得。

`HrAgentSettings`含DB工厂配置引用、provider profile、ContentCodec配置、知识发布位置、预算profile、租约/心跳秒数；秘密字段repr隐藏。`AuthorizedScope`含owner_id、work_id可空、objects、selected_refs；不是永久授权凭据，每次操作还需重验。`ModelContext`含purpose（work/summary）、角色/工具定义、筛选消息、准确依赖、估算输入token、输入修订；summary另含实际覆盖的SummaryProvenance；不携凭据。

`WorkerIdentity(worker_id: str, profile_revision: str)`只由可信运行装配创建，不从HTTP/工具参数反序列化。`LeaseFence(work_id: UUID, input_revision: int, epoch: int, worker_id: str)`；`ModelRequest(attempt_id: UUID, purpose: str, profile_id: str, messages: tuple[dict,...], tools: tuple[dict,...], max_output_tokens: int, deadline_seconds: float)`；`ModelEvent(type: str, payload: dict)`按§5的四类事件校验；`ToolCall(provider_call_id: str, name: str, arguments: dict)`；`Usage(raw: dict|None, input_total: int|None, output_total: int|None, quality: str)`；`ModelReply(text: str, tool_calls: tuple[ToolCall,...], stop_reason: str, usage: Usage)`；`ToolOutcome`为§4五种工具输出的联合类型。

`RuntimeAction(kind: str, attempt_id: UUID|None, operation_id: UUID|None)`的kind仅为build_context/resume_prepared/execute_tool/project_answer/project_summary/wait/done；后四种动作不会重新问模型。`StoredToolOperation`含operation_id/name/arguments/status/receipt；`ScopedEntry`含entry_id/seq/kind/body/objects/source_refs/input_revision/summary_provenance，只有通过筛选的内容能进入ModelContext。

内部状态控制：普通准入切换finalizing后抛`ContextRebuildRequired`，runtime按新phase重新组装受限工具集合；等待/blocked抛`WorkPaused(view: WorkView)`结束本次认领，不伪装为ModelRequest。业务错误抛`HrAgentProblem(problem: Problem, http_status: int)`由routes/tools映射。没有工具且text为空的完整模型响应是invalid_response，不能finish为成功。

`ResultQuery`含 thread_id或ObjectRef（二选一）、kind可空、cursor可空；`ProgressEvent`直接使用Schema Event。所有UUID/ExactRef由解析层转类型，数据库类型不以任意dict拼SQL。

| HrAgentRepository 方法 | 语义 |
| --- | --- |
| `submit(owner_id: UUID, request: WorkInput, key: UUID) -> WorkView` | 受理与API回执同事务 |
| `append_input(owner_id: UUID, work_id: UUID, request: AppendInput, key: UUID) -> WorkView` | 新修订、失效旧epoch |
| `get_work(owner_id: UUID, work_id: UUID) -> WorkView` | owner过滤后投影 |
| `list_threads(owner_id: UUID, cursor: str\|None) -> ThreadPage` | 当前用户线程发现 |
| `list_works(owner_id: UUID, thread_id: UUID, cursor: str\|None) -> WorkPage` | 当前用户线程内工作发现 |
| `list_messages(owner_id: UUID, work_id: UUID, after: int, limit: int) -> MessagePage` | 用户/助手/提问投影，旧个人文本按当前权限范围过滤 |
| `list_events(owner_id: UUID, work_id: UUID, after: int, limit: int) -> EventPage` | 稳定seq与安全进度 |
| `cancel(owner_id: UUID, work_id: UUID, reason: str, key: UUID) -> WorkView` | 与写操作竞争在同一work锁上 |
| `extend_budget(owner_id: UUID, work_id: UUID, request: ExtendBudgetInput, key: UUID) -> WorkView` | 追加不清零 |
| `claim(worker_id: str, lease_seconds: int) -> LeaseFence\|None` | FOR UPDATE SKIP LOCKED；过期running可恢复，waiting_*不认领 |
| `renew(fence: LeaseFence, lease_seconds: int) -> bool` | 失效立即false；模型线程观察后停止提交 |
| `prepare_model(fence: LeaseFence, context: ModelContext) -> ModelRequest` | 准入、预留、生成或恢复prepared attempt；普通余额不足转finalizing并要求重建受限context，总额不足waiting_budget |
| `mark_model_sending(fence: LeaseFence, attempt_id: UUID) -> None` | +1真实调用预扣；发送之前调用且唯一 |
| `commit_model(fence: LeaseFence, attempt_id: UUID, reply: ModelReply) -> tuple[UUID,...]` | 完整步骤与工具槽位原子落盘，返回operation_ids；purpose=summary必须无工具调用且保留实际来源覆盖 |
| `interrupt_model(fence: LeaseFence, attempt_id: UUID, reason: str) -> None` | 保守扣记；无工具执行 |
| `next_action(fence: LeaseFence) -> RuntimeAction` | 从持久状态选择重建上下文、恢复prepared、执行原工具、按purpose投影终答/摘要或等待；不直接调用模型 |
| `load_operation(fence: LeaseFence, operation_id: UUID) -> StoredToolOperation` | 读取原name/arguments/状态/回执，不接受替换参数 |
| `execute_local_tool(fence: LeaseFence, operation_id: UUID) -> ToolOutcome` | 原子执行save_note/save_result/ask_user及回执，内部重新验权 |
| `commit_read(fence: LeaseFence, operation_id: UUID, payload: ResourceText \| ResourcePage) -> ToolOutcome` | 二次验权后落读取记录、tool entry与回执 |
| `commit_summary(fence: LeaseFence, attempt_id: UUID, provenance: SummaryProvenance) -> UUID` | 仅purpose=summary且已提交的模型正文；核对实际输入覆盖，写summary与事件，不能投影用户终答 |
| `update_checkpoint(fence: LeaseFence) -> WorkCheckpoint` | 从当前合法输入、读取并集、笔记目标和未执行槽位生成投影；随状态/工具提交原子保存，不需额外模型调用 |
| `read_selected_entries(fence: LeaseFence) -> tuple[ScopedEntry,...]` | owner/对象/来源过滤后解密历史；未标记个人条目排除 |
| `pending_operations(fence: LeaseFence) -> tuple[UUID,...]` | 返回原已保存调用，按slot排序 |
| `finish_work(fence: LeaseFence, attempt_id: UUID) -> WorkView` | 仅purpose=work的已提交终答幂等投影；无pending操作；按phase结束或等待预算，不替用户确认 |
| `settle_usage(worker: WorkerIdentity, attempt_id: UUID, observation_id: UUID, usage: Usage) -> None` | 受限计量结算，不能授权业务写入 |

`HrAgentService`公开 submit/append_input/get_work/list_threads/list_works/list_messages/list_events/cancel/extend_budget，参数同repository；执行前按HrAccess验权并注入owner。没有从模型参数创建AuthContext的方法。

### 10.1 现有文件的修改范围

A1修改 `backend/app/config.py`、`backend/app/main.py` 装配新设置/路由/codec；`backend/app/control_plane/authorization.py`登记新精确路由；`deploy/cloud/compose.yaml`加入关闭状态的profile定义。迁移096按§8。不修改 `direct_worker.py`、旧 `conversation_context.py` 或旧迁移；P1现行修复另提交。

B批再修改 `webui/src/workspaces/hr/HrWorkspacePage.tsx`、`HrPositionResourcesPanel.tsx`，新增 `webui/src/hrAgentApi.ts`及对应组件测试；它们调用本规格API，不通过旧core-chat/MetaBot命令转发。A1不以空页面作为完成证据。

### 10.2 A1 测试文件与命令

新增 `backend/tests/hr_agent_support.py`（一次性库、真实身份fixture、本地脚本提供方、进程故障钩子），及：

- `test_hr_agent_contracts.py`：Schema/语义错误、额外owner字段、未知工具、半截参数。
- `test_hr_agent_repository.py`：受理/操作去重、改内容冲突、epoch过期、预算累计、解密前owner过滤。
- `test_hr_agent_context.py`：对象/依赖筛选、混合摘要和准确方法身份；W2/W8/W12的基础契约。
- `test_hr_agent_routes.py`：真实身份/Origin/CSRF、中央路由可达性、各API返回与错误；无岗位启动。
- `test_hr_agent_runtime.py`：完整模型步骤、五工具分派、提供方错误、预算各维度独立触发。
- `test_hr_agent_worker_process.py`：真实子进程kill/restart、回执丢失、取消竞争、心跳独立与迟到写拒绝。

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_contracts.py tests/test_hr_agent_repository.py tests/test_hr_agent_context.py tests/test_hr_agent_routes.py tests/test_hr_agent_runtime.py -q
.venv/bin/python -m pytest tests/test_hr_agent_worker_process.py -q
```

上述是A1新增文件落地后运行的命令，本轮不运行不存在的测试。数据库测试依赖缺失或skip不可算通过；提供方替身可验证协议和恢复，不证明真实模型岗位校准质量。

## 11. 评审归属、A0完成证据与剩余工作

A0技术评审由工程负责人组织，审阅者检查Schema、API、模型记录和故障语义的一致性；产品负责人（用户或指定负责人）确认没有改变场景与权限边界。两者结论均写入交付计划，才能进入A1。编写者的自检只能标“规格完成，待评审”，不能自行勾选评审通过。

B4的真实模型质量由指定HR/用人经理审读，工程负责人提供完整输入/输出/用量/保存证据；产品负责人据业务审读结论决定首批通过。尚未指派具体人时，不伪造签字；工程测试绿不能代替这一结论。

本轮可验证的只有：文档契约完整、结构正反例符合Schema、引用文件与代码基线可查、A1文件与签名一致、所有待实现内容清楚标记。权限正确、进程可靠、加密装配成功、D1现行修复、生产计数和专业质量均未在A0实现或实跑。

P1现行处置与P2生产盘点保持未完成；P3已有口径/配置候选，实测与产品预算取舍未完成。A0完成后不自动执行A1、生产查询、模型调用、迁移或切换。新库空白不证明旧库没有标准/成果；E批须按实际盘点承接身份、确认基准及引用后才允许生产接单，不能创建一套空标准覆盖已有共识。

### 11.1 本轮文档校验

| 评审补项 | 修订落点 | 仍需运行实现验证 |
| --- | --- | --- |
| 提案/类型/预算条件 | §2及Schema条件、正反例；全零reserve与追加分开 | 当前基准、引用与授权、服务422 |
| 混合摘要与历史筛选 | §5.1、entries来源字段、purpose=summary恢复 | W2实际模型输入与原条目删除/撤权 |
| 材料入口和状态 | §3.2真实附件路径、新MaterialService、原文/解析身份 | 无岗位上传全旅程；PDF/DOCX在B1 |
| 普通日志与隔离 | §8.4字段表、拒绝额外字段样例、A1测试名 | 首条请求/异常输出与跨任务目录 |
| 预算、展示及后续能力 | §4.1、§6检查点、§7事件、§9增长/压缩 | 真实进程/模型用量、专业审读及后续批次 |


本次修订将提案条件、预算非零、准确引用、确认错误联合、材料/摘要/日志字段加入可重复的Schema正反例；读取区间的跨字段大小关系、摘要来源真实性和日志发送行为属于待实现服务测试。`selfcheck-manifest.json` 为不能自动理解的语义建立可审计覆盖 ID，并声明条件矩阵、状态、能力分区、路径/提交与预算算术断言。脚本使用 stdlib RFC 3339 日期解析器覆盖 jsonschema 的 `date-time` 检查，避免可选格式依赖缺失时静默放过非法日期。

下述命令验证55个定义、133个样例（72正例、61反例）、18项条件规则和6项语义证据 ID；执行证据仅限文档，不替代A1故障测试。

可在仓库根目录重复运行以下结构校验；它不调用模型、数据库或网络：

```sh
backend/.venv/bin/python docs/superpowers/specs/hr-cloud-loop/selfcheck.py
backend/.venv/bin/python -m pytest backend/tests/test_hr_cloud_loop_docs_selfcheck.py -q
```

预期第一条输出为 `A0 docs self-check passed: 55 definitions, 133 examples, 18 condition rules, 6 prose evidence IDs`；第二条验证日期、定义覆盖、条件矩阵和预算清单的变异会被检出。这只验证文档结构与声明的覆盖关系，不证明虚构UUID对应真实对象、摘要已从真实原文算出、服务端已经实施授权，或 A0/A1 评审已经通过。
