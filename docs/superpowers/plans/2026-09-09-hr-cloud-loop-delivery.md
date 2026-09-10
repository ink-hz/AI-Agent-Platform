# HR 云端 Loop 分批交付计划

> **执行说明：** 使用 `executing-plans` 按任务推进；需要独立并行工作时按当前任务授权安排。勾选框记录真实交付证据，不自行扩大实施、生产查询或发布范围。

**目标：** 先用岗位需求校准验证自有云端 Agent 的完整工作能力，再覆盖全部 HR 场景并退出旧执行链。

**架构：** 一个 Hannah 自主使用模型与工具，独立 Worker 持久记录工作和成果。先固定接口层实施规格，再编写循环；不把旧远端命令协议或 FAE 的业务规则搬进新系统。

**技术基线：** Platform 本地 master `f6c9730`（代码基线 `6777d22`）；Python 后端、PostgreSQL、React/TypeScript 工作台。`38dfc7a` 只作为较新 HR 资产与另一种历史装载形状的核查基线，不整分支合入。FAE `b49eeed` 用于核查模型适配、直接工具调用的可复用部分。

**状态：** A0 规格及接口样例已编写，自检完成后提交接口层评审；评审通过项尚未勾选。A1 运行代码、P1 现行修复、P2 生产盘点均未执行。P3 已写计量口径与工程候选配置，实测和产品取舍尚未完成。规格入口：[A0 接口层实施规格](../specs/2026-09-09-hr-cloud-loop-runtime-spec.md)。

## 1. 两个选择与全局约束

首批选择**岗位需求校准**。输入可以是公开 JD、虚构业务要求或经确认可用的材料；输出是有依据的岗位理解、JD/JR、待确认要求与用户部分确认的标准。无正式岗位也可讨论，成果先归当前工作，用户建立/选择岗位后再明确关联。

第一项产物是**接口层实施规格**，涵盖工具参数与错误、工作记录与历史范围、API、执行权、恢复和预算。随后实现最小云端 Loop，再接首批场景。不能先写通用 Agent 框架，再让 HR 需求适应它。

- 上位文件：[总体架构](../../../HR总体架构设计.md)、[Agent 工作流](../../../HR_Agent工作流.md)；本计划不成为第三份总体设计。
- 不按关键词或工具次数判定 HR 质量，不要求每次校准必须调用某份方法。案例中的正确流程是业务样例，不能编成路由。
- 所有读取、历史组装、工具写入与确认都服务端校验；私有使用不等于可自动使用同用户的所有候选人资料。
- API/数据库优先，必要进程故障验证，其后真实模型与专业审读，页面最后；各类证据分别记录。
- 首批不处理真实候选人材料。模型服务确认前仍可用虚构数据和提供方替身做工程验证；公开 JD 的真实模型调用也必须使用明确配置的服务。
- D1 修复、只读统计和预算方案可独立推进；不因 A0 尚未完成就延后现行风险处置。
- 首批不切生产受理、不停止 MetaBot、不恢复飞书关联、不启用团队共享/多人确认，也不引入面试转写、日程或全库重分析入口。

## 2. 依赖与批次

| 工作 | 输入依赖 | 可独立审阅的交付 | 结束条件 |
| --- | --- | --- | --- |
| P1：D1 现行处置 | 两个代码基线、虚构候选人 | 复现、处置选择、回归与现行适用范围 | 按总体架构 §10 的“先到者”截止点验证；不等待全迁 |
| P2：数据与部署盘点 | 本地 schema、指定环境和实际授权 | 只读查询方案、公开资产清单；获准后实际计数 | 有可追溯时间/环境/范围；未查项仍标未知 |
| P3：预算方案 | 总体架构 §4.1 的预算单位、公开校准样例；口径先行 | 计量规则、测试初值、生产建议与取舍 | A0 使用统一字段；A1 可测停止/恢复；W11 前固定长任务配置 |
| A0：接口层实施规格 | 两份主文档 | 本计划 §4 的一份具体规格 | 所有接口及错误、状态与故障表可相互对照 |
| A1：最小云端 Loop | A0；P3 的测试配置 | 受认证 API、独立 Worker、直接模型工具循环与持久工作记录 | W9；基础授权、模型端点限制、日志验证；无真实候选人 |
| B：岗位校准闭环 | A1 | 原文/方法读取、成果、跨入口读取、部分确认 | W1、W6、W12；W3 的岗位/对话子项 |
| C：材料与研究 | B；涉及真实个人数据时另需供应商与材料处理条件 | 批量简历、撤权传播、固定情报引用、长研究续作 | W5、W7、W8、W10、W11；候选范围验证 W2 |
| D：其余业务场景 | C 的对应能力 | 搜寻与吸引、候选人评估、面试记录、招聘复盘 | 工作流六类场景均有实际样例；完整 W3、W4，不能仅接快捷按钮 |
| E：数据承接与正式退出 | P2 实际盘点、A–D 完整验收、窗口及渠道决定 | 数据承接证据、在途归属和真实切换方案 | 总体架构 §8 的五时点条件；包含简历解析 |

A/B/C 是前三个工程批次，不代表全部 HR 已完成。P2 在最初启动，数据承接的执行在 E；不得到 E 才发现旧数据数量与引用缺口。C 的公开研究可在真实候选人闸门未解除时推进，个人材料工程验证可使用虚构样本。

## 3. 三项独立前置工作

### P1：复现 D1，选择现行处置并验证

**责任：** 工程负责复现、修复与验证；若采用临时功能限制，产品负责人确认用户影响。截止点沿用总体架构 §10，不另起更晚的迁移期限。

**核查文件：** `backend/app/agent_brain/conversation_context.py`、`backend/app/hr/task_context.py`、`backend/app/hr/task_service.py`、`backend/app/hr/task_repository.py`、`webui/src/workspaces/hr/HrCandidateWorkspace.tsx`。现有测试入口为 `backend/tests/test_agent_brain_conversation_context.py`；复现与处置证据保存到 `docs/reviews/2026-09-09-hr-history-scope-remediation.md`。

- [ ] 在本地一次性 PostgreSQL 中通过真实任务/会话服务建立同用户、同岗位的 A/B 任务，A 提问和助手回答加入唯一虚构事实。观察 B 实际模型输入，分别检查直接历史与已压缩摘要；附件原文绑定另测，不混成同一结论。
- [ ] 在隔离 checkout 对照 `38dfc7a` 的 v6 形状：历史下界为 0、按岗位筛选；验证较早 A 消息仍可进入同岗位 B 输入。保留 master 与 v6 两份输入证据，不用一个基线的结果代表另一个。
- [ ] 比较“按对象分会话”与“消息/笔记带工作对象后筛选”：逐项说明候选任务入口、普通对话、明确多人比较、已有混合摘要和未标记旧消息如何处理。选定现行补救；新链只继承对象隔离语义，不强制继承旧表或入口。
- [ ] 按选定补救新增候选人级失败测试，再作最小修复；无范围证明的个人历史不自动注入，混合摘要重建或省略，明确比较可读取双方。实现前在处置记录固定具体修改/新增文件，避免只给 SQL 加条件而遗留摘要路径。
- [ ] 验证同用户 A→B、滚动摘要、较早历史、明确 A/B 比较、跨用户拒绝、当前输入与合法岗位基准仍可用；分别报告当前 master 修复与 v6 分支是否获得适配，未适配的旧分支不得按旧计划继续上线。

复现必须在改代码前失败，修复后同一回归通过。运行入口：

```sh
cd backend
.venv/bin/python -m pytest tests/test_agent_brain_conversation_context.py -q
```

新增回归的精确测试名和环境准备命令随 P1 处置记录提交；本条现有命令不是“已有候选人隔离测试”的证明。没有 PostgreSQL、测试被跳过或只验证了附件绑定，都不算 P1 完成。

### P2：准备只读盘点范围，再取得实际数据

**责任：** 工程准备范围和查询；环境/数据负责人确认可访问环境与权限。产物：`docs/reviews/2026-09-09-hr-assets-inventory.md`。

- [ ] 对 `backend/control_migrations/`、`backend/control_migrations/hr_web/` 和当前 repository 逐项核对对象存放位置，写出只读查询正文、涉及表、预计扫描范围及最大输出量。先给可审阅查询，再申请具体环境访问；不查找或输出连接凭据。
- [ ] 统计项覆盖岗位、候选人、有效附件、各类成果、确认标准、执行/等待/恢复工作、解析草稿、失效引用和已发布情报包；查询输出只含聚合值与必要诊断身份，不含个人正文。
- [ ] 同时完成不依赖生产授权的本地公开资产清单：情报包 manifest/原文/语义报告/导入接口；Team `7757ca4` 的七份方法、README、来源台账与 JD 案例。记录实际来源提交和内容身份，不把本地包当当前生产包。
- [ ] 获准环境明确后，在只读事务和限时查询中取得实际数值；记录时间、基线和未覆盖项。确认云端受理配置、在途执行者及简历解析归属，仅报告必要开关状态。

没有生产权限时提交本地清单与可执行查询方案，生产计数仍未完成；A0/A1 的隔离开发不受阻，E 的数据承接设计不能据此假定零数据。

### P3：形成预算配置与测量方案

**责任：** 工程提出默认值与测量；产品负责人确定可接受时长/成本。方案写入 A0 规格的预算章节，不新增一份竞争性总设计。

- [x] 定义模型调用次数、累计输入/输出 token、活动执行秒数、费用估算/币种的计量口径；等待用户与排队不计活动时长，重试与恢复不清零。用量缺失、缓存 token 和断流请求写出明确处理办法。证据：A0 §9，仅规格完成。
- [x] 候选配置改为32次、900秒、600,000累计输入加输出token，内含收尾2次/60秒/40,000 token；A0 §9提供上下文增长算例与分维度测试配置。多上限先到者停止，不要求同一工作同时触发；本项只完成方案，不代表实测或生产默认已获确认。
- [ ] 按 A0 §9.3 分别独立触发调用次数、token、活动时长三个上限，再记录一次显式追加额度并继续；验证用量累计、已保存成果可找回、未读清单保留，未获得追加不自动开新工作绕过限制。
- [ ] 在公开校准和长阅读样例上记录活动时长、用量、未读量与成果可用性；有供应商价格来源时才给费用估算，并标明与账单的差别。校准与研究分别提出配置，不能以读不完就抽样的方式通过 W11。

## 4. A0：先交付接口层实施规格

**产物：** [实施规格](../specs/2026-09-09-hr-cloud-loop-runtime-spec.md)、[请求/返回 Schema](../specs/hr-cloud-loop/contracts.schema.json)、[接口正反例](../specs/hr-cloud-loop/contract-examples.json)。该文件只细化接口与实现，不重新定义产品边界。以下五个任务依次补入同一文件，避免两名实施者各自发明数据结构。

### A0.1：固定首批工作请求与成果关系

**输入：** 总体架构 §3–5、岗位校准场景、W1/W3/W6。

- [x] 写出“无岗位上传公开 JD → 校准 → 保存 → 关联岗位 → 选两条确认 → 同用户另一会话修改基准”的完整请求与响应样例；明确哪个动作是用户授权，哪个仅为模型建议。
- [x] 固定工作、输入修订、对象集合、材料引用、成果修订、确认基准的字段类型和关系；给出合法样例、跨用户引用、错对象引用和旧基准确认冲突样例。
- [x] 明确无岗位成果如何列出和后来关联；同一成果从对话/岗位读取返回同一身份与正文，关联不复制一份内容。候选人入口后续复用同一语义。

**可拒收点：** 必须先建完整岗位才能讨论；用会话 ID 代替业务范围；确认接口只接布尔值、不能绑定用户看过的准确条目。

### A0.2：固定工具契约与 API 面

**输入：** A0.1 的对象/引用语义；总体架构 §4.2。

- [x] 为资料发现、全文读取、方法目录/正文、历史成果发现/读取、成果保存、澄清与确认定义精确工具/API 名称、参数 JSON Schema、成功返回和错误。可以合并工具，不能漏掉能力。
- [x] 逐工具列出服务端身份注入、对象范围检查、权限检查时点、正文身份、是否产生副作用与幂等作用域。确认标准由真实用户 API 完成；模型可以生成待确认提案，不能代填确认者。
- [x] API 至少覆盖提交工作、读取状态/事件、取消、回答澄清、预算续作、按对象列出成果、读取精确正文、部分确认；写出请求去重、相同去重身份改内容、分页/重连与错误响应的完整实例。
- [x] 固定成功、确认为空、缺数据、暂不可用、无权访问、冲突的不同结果；说明工具错误如何变成可理解的用户进度，而非统一“暂无结果”。

**可拒收点：** 成果只能按 resultId 猜读、没有发现接口；工具开放任意 owner 字段；正文文件替模型扩权限；将模型自报保存视为成功。

### A0.3：固定持久工作记录与历史选取

**输入：** A0.1/A0.2；P1 已核查的历史形状；P3 先行计量口径。新链采用对象标记后筛选，不等待 P1 修复上线；P3 不等待本节的数据库字段，A0.3 只将口径落实为字段和事务。

- [x] 给出数据模型与迁移设计：工作输入修订、执行认领、模型步骤、完整工具调用及结果、操作回执、研究笔记、成果、用户确认、预算配置/用量；列明唯一键、事务边界与字段加密/保留责任。
- [x] 固定对象/来源标记及每轮选取算法，写出 A→B、A/B 比较、混合摘要、未标记个人历史、引用撤权的输入与输出记录。先过滤再压缩；不能把未经筛选的混合摘要带回模型。
- [x] 明确角色/方法的目录与正文身份、任务继续时固定内容、缺失旧内容的暂停语义。工作记录保存必要依据与阶段判断，不要求储存或展示模型私密推理全文。
- [x] 固定预算扣记、追加、取消、等待、部分交付状态及恢复位置；说明无法获知供应商用量时保留的未知状态与保守处理。

**可拒收点：** 仅记录最新对话字符串；进程恢复预算归零；同用户默认整会话可用；研究覆盖仅靠模型说“都读了”。

### A0.4：固定执行权与故障语义

**输入：** A0.2 的操作身份、A0.3 的持久模型。

- [x] 写出认领/续租/失效代次、租约过期与取消的状态转换表；同一工作唯一有效执行者，迟到 Worker 不能写入。模型网络等待不占数据库事务，保活不依赖模型输出。
- [x] 对“受理后未认领、模型断流、工具参数半截、写成功回执丢失、取消与写入竞争、旧 Worker 迟到、浏览器重连”逐行规定恢复依据、允许动作、禁止动作和用户状态。
- [x] 把 W9 变成真实进程测试步骤：成功写入后阻断回执，终止 Worker，重启并查询已存在回执；校验只有一个副作用，另测失效执行权与取消后新操作被拒绝。不能用纯 mock 循环代替进程恢复。
- [x] 指定模型端点限制、超时/错误分类、普通日志字段、文件隔离和受限诊断路径；从首条模型请求就执行。W10 在 C 扩展个人材料验证，不到 C 才补基础边界。

**可拒收点：** 以“重发完整提示词”代替恢复；回执不明就重复写；把供应商模型调用宣传为恰好一次。

### A0.5：固定文件边界与 A1 编码任务

**现有核查入口：** Platform `backend/app/agent_brain/loop_runtime.py`、`direct_worker.py`、`conversation_context.py`、`backend/app/hr/resource_service.py`、`backend/app/main.py`、`deploy/cloud/compose.yaml`；FAE `src/agent/loop/adapters.py`、`runtime.py`、`tools.py`。核查结论须标具体提交；不把现有 Platform 的委派循环直接称为 Hannah Loop。

- [x] 将新功能划为请求与授权、工作记录与历史选择、模型适配、工具执行、Worker、成果与确认六个责任边界；固定各自新增/修改文件、公共签名和调用关系。优先独立 HR 包，避免给旧 DirectWorker 叠第二套执行语义。
- [x] 逐项记录 FAE 的实际可复用代码与 HR 专属替代部分；不继承 FAE 业务门控、固定取证要求或只靠内存保存运行状态。
- [x] 明确迁移目录为 `backend/control_migrations/hr_agent/`、拟用096且提交前再查占用；根目录与子目录共用编号账本，094/095已占用。固定一次性数据库初始化、测试提供方与 Worker 启动/终止命令；所有命令可在隔离环境执行，不修改现行受理默认值。
- [x] 将 A1 拆成带准确函数签名、失败测试、最小实现与验证命令的编码任务，直接写入本计划的 A1 段；API、模型记录和恢复任务必须引用 A0 同一份定义。
- [ ] 接口层评审：工程负责人组织技术审阅，产品负责人（用户或指定负责人）确认场景/权限边界；记录两方结论后进入 A1。编写者已完成 W1–W12 映射自检，但不能自行勾选评审通过。2026-09-10评审补项见规格§2–6/§8.4/§9；修订不等于评审已通过，A1仍未启动。

## 5. A1 以后各批的交付边界

以下表格规定交付边界，A1 的具体步骤见 §5.1；接口与文件名以 A0 §10 为准。尚未实施，不因规格出现函数签名就视为代码存在。

| 任务 | 消费前项 | 交付与必须验证的失败路径 |
| --- | --- | --- |
| A1.1 受理与记录 | A0 请求/持久契约 | 隔离 API 接受并持久保存工作；重复提交返回同一工作，改内容冲突；跨主体引用拒绝；服务重启记录仍在 |
| A1.2 Worker 与直接循环 | A1.1、A0 执行契约 | Worker 直接调用模型与测试工具；步骤可回查；半截工具参数不执行；端点限制与日志约束生效 |
| A1.3 恢复/取消/预算 | A1.2、P3 | W9 全部故障动作有真实进程证据；等待释放执行资源；有限预算停止，显式续作累计用量 |
| B1 资料与专业内容 | A1、A0 引用契约 | 补PDF/DOCX解析状态/全文覆盖； 发现/全文读取公开 JD 与七份方法；目录不算正文；自主选用，不强制方法命中；W1/W12。可先用带时间的静态公开材料，接入在线官网校验前固定新鲜度阈值 |
| B2 成果发现与保存 | B1、统一成果模型 | 补文件交付与下载契约、准确基准性质； 对话和岗位读取同一内容；无岗位工作也可保存；正文、文件和对象关联可找回；先完成 W3 的两入口部分 |
| B3 部分确认与并发 | B2、确认基准语义 | 标准提案/确认均测个人来源拒绝，冲突必须返回当前标准修订； 用户选中准确条目，同用户另一会话先更新则冲突；不覆盖未选条目、不改变官网；W6 |
| B4 校准连续旅程 | B1–B3 | 公开 JD → 澄清 → 理解/建议 → 保存 → 部分确认 → 另一会话继续；工程提供真实模型证据，指定 HR/用人经理审读，产品负责人决定业务通过；最后验证页面输入与跨入口操作 |
| C1 个人材料与候选范围 | B、P1、材料处理配置 | 虚构样例先做 W2/W5/W8/W10：逐文件状态、歧义人工核对、历史隔离、撤权传播、受限存储与日志；真实材料验收须另满足总体架构 §6 |
| C2 固定情报引用 | B1/B2、P2 公开资产 | 目录当前包与已选旧正文分开；包更新/清除返回正确内容或不可用；W7，保留期在对应验收前确认 |
| C3 研究与预算续作 | A1.3、B1/B2、P3 | 连续阅读公开原文，实体范围准确，承重引用与反证保留；受限预算后继续；W11；不替换公共情报发布 |
| D01 业务场景扩展 | C 的相应能力 | 搜寻草稿、候选人评估、针对性面试方案、真实反馈整理、招聘复盘；逐场景实际样例，不自动联系或作录用决定 |
| D02 全入口与连续招聘 | D01 业务扩展 | 完整 W3/W4：候选人入口可找回同一成果并继续面试；真实记录不编回答；复盘提案再次人工确认，不把个人原话沉淀为标准 |
| E1 承接与切换演练 | P2 实际计数、A–D 证据 | 旧数据身份/归属/引用映射与清单对账，聊天和简历在途分别演练；历史包缺失明确返回不可用 |
| E2 正式切换 | E1、具体窗口/渠道/执行授权 | 验证旧链停止认领、排空/取消、唯一新受理和回滚不重复执行；只停 HR 的旧执行职责 |

### 5.1 A1 编码任务（A0评审通过后执行）

各步骤使用A0 §10固定的类型与函数；本节不给生产写入授权。测试目录下的fixture可写虚构数据；不在A0创建占位运行模块。每个子项先形成失败用例，再作最小实现和独立提交。

#### A1.1a：类型、配置、迁移与独立加密装配

**文件：** 新增 `backend/app/hr_agent/{__init__,types,config,access}.py`、`backend/control_migrations/hr_agent/096_hr_agent_runtime.sql` 与README；修改 `backend/app/config.py`。测试新增 `backend/tests/hr_agent_support.py`、`test_hr_agent_contracts.py`、`test_hr_agent_repository.py`。

- [ ] 写失败测试 `test_rejects_model_owner_field`、`test_disabled_relay_can_load_hr_codec`、`test_missing_schema_does_not_create_tables`、`test_proposal_conditions_and_positive_budget_addition`；从A0 Schema精确派生字段，未知字段/缺失ref摘要拒绝。
- [ ] 按A0 §8复用migrator和ContentCodec接口，独立装配HR密钥；096前再次核对所有已用编号。fixture用一次性PostgreSQL，角色名/环境与现有migrator校验一致，显式跑根迁移及新目录，不执行hr_web 089–095。
- [ ] 实现已声明的类型、配置/权限入口、表与约束；数据库就绪检查是只读，不因开关启用而迁移。密钥/数据库秘密不出现在repr或错误中。
- [ ] 运行下列对应测试，要求首次失败可复现、修复后通过且数据库测试无skip；只提交本项文件与结果记录。

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_contracts.py tests/test_hr_agent_repository.py -q
```

#### A1.1b：受理、查询与对象范围历史

**文件：** 新增 `backend/app/hr_agent/{repository,service,context,routes,results,materials}.py`；修改 `backend/app/main.py`、`backend/app/control_plane/authorization.py`；测试新增 `test_hr_agent_routes.py`、`test_hr_agent_context.py`，扩展repository测试。

- [ ] 写失败测试 `test_submit_without_position_and_replay`、`test_same_key_changed_payload_conflicts`、`test_owner_filter_precedes_decryption`；使用真实认证/CSRF链，不单独绕开中间件调用owner函数。
- [ ] 实现A0 §3的work/thread/messages/events读取、受理和继续接口，以及repository同名方法。未开放的B能力明确503；GET messages必须在重开后返回已提交回答/问题正文，受限原文返回占位。
- [ ] 写并实现 `test_candidate_b_excludes_a_message_and_summary`、`test_explicit_comparison_allows_both`、`test_unscoped_old_summary_is_not_imported`；观察build_model_context的实际messages和dependencies，不只检查附件元数据。
- [ ] 写并实现 `test_upload_text_jd_without_conversation_resolves_material_ref`：真实附件begin/content/complete→materials查询→WorkInput；分别验processing/unsupported/删除和原文/解析身份，A1先UTF-8文本，B1补PDF/DOCX，不用假ref掩盖入口缺失。
- [ ] 实现输入修订、来源范围标记、先筛后压缩和原方法身份校验；新输入失效旧执行权。测试通过后独立提交，不导入旧全局summary；summary必须记录derived_from，普通note无替换历史权限。增加 `test_mixed_summary_missing_originals_is_omitted` 和 `test_note_cannot_erase_personal_scope`。

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_routes.py tests/test_hr_agent_context.py tests/test_hr_agent_repository.py -q
```

#### A1.2：完整模型步骤和五工具循环

**文件：** 新增 `backend/app/hr_agent/{model,resources,tools,runtime,observability,work_files,diagnostics}.py`；扩展repository；测试新增 `test_hr_agent_runtime.py`，扩展hr_agent_support中的本地脚本提供方。

- [ ] 写失败测试 `test_incomplete_arguments_execute_nothing`、`test_unknown_tool_returns_invalid`、`test_asked_question_is_durable`；脚本提供方走实际HTTP，分别返回完整响应、半截参数与断流。
- [ ] 实现ModelPort事件与完整ModelReply解析；每次网络尝试持久记录logical_step_id/retry_no，不允许适配器内部隐式重试。资源适配器在A1使用固定虚构文本和方法发布目录，B1再接真实内容。
- [ ] 按原operation槽位分派list_resources/read_resource/save_note/save_result/ask_user，引用和写入由服务端验权；save_result在真实测试库落盘，不能只返回假的saved=true。
- [ ] 写并实现 `test_first_request_logs_exclude_sensitive_payloads`、`test_work_files_cannot_escape_or_cross_scope`、`test_diagnostics_disabled_and_expired_unreadable`，观察Worker及HTTP适配器输出；W10基础边界在首条模型调用即生效。
- [ ] 写并实现 `test_tool_error_and_recovery_are_visible_in_events`；事件输出安全分类，messages不伪造助手失败答案。
- [ ] 实现终答投影与逐工具继续。调用顺序来自模型返回，不能把“先方法再保存”写成业务管道；固定终答可直接保存普通回答，不增加submit_answer门控。

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_runtime.py tests/test_hr_agent_context.py tests/test_hr_agent_contracts.py -q
```

#### A1.3：独立Worker、恢复与预算

**文件：** 新增 `backend/app/hr_agent/worker.py`、`backend/tests/test_hr_agent_worker_process.py`；扩展repository/runtime/config及测试support；修改 `deploy/cloud/compose.yaml`新增默认不启动的hr-agent profile。

- [ ] 写失败测试 `test_committed_save_survives_lost_reply_and_kill`、`test_committed_final_answer_projects_without_new_model_call`、`test_prepared_attempt_is_not_double_charged`。故障钩子阻断回执或终答投影；kill真实子进程后重启同一测试库，核对模型请求数/操作ID/结果修订数。
- [ ] 实现claim/renew/失效epoch、独立心跳与信号退出；先恢复prepared或committed步骤，不重发整份用户工作。终答从原reply投影，业务副作用只读取原回执。
- [ ] 写并实现 `test_cancel_wins_before_new_side_effect`、`test_old_epoch_cannot_commit_after_new_input`、`test_late_usage_only_settles_budget`；取消/新输入使旧业务提交失效，迟到usage只能走受限结算。
- [ ] 按A0 §9.3独立测试calls/token/time、usage缺失、重试累计；再测试research→finalizing→waiting_budget重启与显式追加。assert重点是没有下一次请求/重复副作用，而非最终回答中出现“预算”字样。
- [ ] 用GET work/messages/events重开工作，验证已保存回答、问题、阶段与成果可找回；运行下面两组回归，记录模型替身/进程故障范围后独立提交。此时不启动生产profile。

- [ ] 增加 `test_growing_context_exhausts_tokens_before_call_cap`、`test_proactive_summary_preserves_sources_and_checkpoint`、`test_summary_reply_never_completes_work`：测试增长轨迹与12000/8000主动压缩，压缩/重试均累计；摘要来源、未读范围和待答问题在kill/restart后仍在。固定200/100样例仅证明扣记，不能作为长文预算校准。

```sh
cd backend
.venv/bin/python -m pytest tests/test_hr_agent_worker_process.py -q
.venv/bin/python -m pytest tests/test_hr_agent_contracts.py tests/test_hr_agent_repository.py tests/test_hr_agent_context.py tests/test_hr_agent_routes.py tests/test_hr_agent_runtime.py -q
```

A1验收由工程负责人基于上述实际证据组织技术审阅；A1通过只证明工程契约与恢复能力，B4的真实模型/专业质量仍由指定业务审读者及产品负责人判定。

## 6. 交付记录与维护

每项完成记录提交、基线、输入身份、运行命令与实际结果；涉及故障的项给出操作回执和状态证据；涉及智能质量的项给出材料、输出与审读理由。缺少该项证据就不勾选。代码任务遵循失败用例先复现、最小实现、相关回归、独立提交，不重跑无关全量测试。

总体架构维护决策与边界，工作流维护行为及 W1–W12，本计划维护批次依赖与完成证据，A0 规格维护准确接口。首批验收只覆盖首批，不把 W3 的岗位/对话部分通过写成完整 W3 通过，也不把 B 批结束写成整个 HR 已迁移。

本次未运行模型、未查询生产业务库、未修复 D1、未部署或推送远端。A0 文档与结构样例已交付，下一步是接口层评审；P1/P2 的执行与 P3 实测保持各自条件。
