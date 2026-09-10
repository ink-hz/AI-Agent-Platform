# HR B：岗位校准闭环实施计划

基线 `589f5d7`，分支 `feat/hr-cloud-loop-b`；沿用已隔离的 `.worktrees/hr-cloud-loop-a1` 目录，A1 分支保留。用户在 A1 报告后回复“继续”，作为进入 B 的授权；本批连续实现后统一验收，不进入 C。

上位契约仍为根目录两份主文档、`2026-09-09-hr-cloud-loop-runtime-spec.md` 及总交付计划 B1–B4。这里补实施文件和此前延后接口，不另设产品总体设计。

## 全局约束

- 方法由 Agent 自主发现和选择；不写关键词路由、方法计数门槛、HR评分或固定调用顺序。
- API/真实数据库优先，进程失败验证之后才做页面。真实身份、当前 HR grant、Origin/CSRF、owner、对象与来源范围必须保留。
- 用户确认标准只走 HTTP；模型五工具不增加确认、任意 HTTP 或 Bash。来源中有候选范围时，标准提案保存/确认均拒绝；无独立脱敏保留裁决时不能解除继承关系。
- 真实候选人、生产查询/发布、旧链停用、团队共享、官网在线核验、全库重分析、批量简历不在本批。公开静态资料足以开发岗位校准，但必须注明来源时点。
- 本地模型替身只证明工程链路。B4 真实模型配置与专业审读独立列证据；配置缺失不假称通过，不阻止其余开发。
- A1 的096不得修改；B新持久字段使用独立097（提交前确认无占用）。不接入旧 DirectWorker/CLI解析。

## B1：材料与专业目录

### B1a：PDF/DOCX 正文解析

文件：新增 `backend/app/hr_agent/material_parsing.py`、`backend/control_migrations/hr_agent/097_hr_agent_material_parses.sql` 与对应测试；扩展 materials.py；公共装配由主代理集中修改。

接口：`POST /api/hr/agent/materials/{attachment_id}/parse`，空JSON体、真实用户/CSRF/Idempotency-Key；返回包含 parse_id/state 的202回执。GET既有materials路径只读取已存在解析状态/正文身份；不得通过GET启动PDF解析。UTF-8保持原A1确定性只读路径。独立Worker每轮处理至多一个到期/新解析任务，再处理工作，不调用模型。

持久任务以 owner/attachment/source-byte-hash/parser-release 唯一；保存队列、限时租约与尝试次数、当前状态、加密文本及coverage、稳定错误分类。领取后进程死去可在租约过期后重试，不永远卡processing。读取原附件与保存/返回前均重查归属、扫描状态、删除/保留期和immutable身份。

PDF用已有pypdf；DOCX用stdlib zip/XML受控解析（不执行宏、不访问外部关系、不落明文文件）。解析在有界子进程内：限制源大小、解压大小、页/文本总量和墙钟时间；到界不截断后声称全文，保留failed/unsupported或明确的部分coverage。按原文顺序保留段落/表格单元内容；页无可提取文本、图像/嵌入对象等无法证明全文时coverage_complete=false并给可解释说明。没有OCR时不声称扫描PDF已读全文。

MaterialView继续区分原件/正文与parser_release；ResourceText给出coverage_complete和coverage_notes使模型知道解析边界。UTF8摘要沿用A0 canonical JSON；新PDF/DOCX解析正文摘要同时包含coverage_notes，算法变化必须改parser_release。解析正文加密存储，不写普通日志或明文临时目录。

验收：真实上传PDF/DOCX→请求解析→Worker处理→解析引用→读取；多页和表格末尾文字保留；扫描/损坏/受保护/zip膨胀/超时；跨owner、源失效、重复键、进程重启；不是文件名或缩略图冒充正文。

### B1b：专业内容与不可变发布

从本机 Team 仓库经核验的准确提交提取七份方法、案例与角色设计，保留frontmatter、来源说明，不擅自重写HR观点。新增受控内容目录与构建工具，产出A1 manifest；记录源提交和文件hash，前端浏览与Agent读取使用同一发布。

发布按release_id保存不可变子目录，当前目录指针仅供新输入发现。旧输入固定原发布/manifest/方法正文；新发布后原内容仍在则继续原内容，缺失明确blocked。提供只读方法索引/正文API，展示用途/边界/来源并允许“带此方法讨论”，选择作为WorkInput准确引用。

验收：七份真实内容全部可发现/读全文；M1与M2分别存在时旧输入仍读M1；删M1阻塞不偷换；前端选择的正文与模型引用一致；不要求模型每次读方法。

## B2：成果文件与工作台入口

### B2a：不可变 Markdown 文件

新增 `GET /results/{id}/revisions/{revision}/file`（同前缀），从精确、已保存成果确定性导出UTF-8 Markdown；不是模型再次生成。新建文件元数据只读接口 `/file-info`，字段为 result_ref/format/media_type/filename/sha256/size_bytes。filename由服务端结果身份构造，不把私人标题塞入路径/普通日志。无需新增对象存储或复制一份可变成果；每次文件/info读取执行与正文相同的当前主体和来源检查，no-store，拒绝current/latest。下载的是该精确修订，关联岗位不改变字节/hash。

验收：线程与岗位返回同一R；旧revision文件稳定；他人/来源失效403或404且无字节；下载不使用旧会话ticket绕过范围。

### B2b：可使用的独立工作界面

新增明确的 `/hr/agent` 入口接新API，不替换现行HR受理默认值。对话、线程/工作恢复、材料上传与解析状态、明确岗位选择、参考方法、成果正文与下载、逐条标准提案确认都在此闭环。现有岗位入口可进入同一新工作界面的岗位范围视图，那里按对象查同一成果。

模块单独放置，不把旧chat API掺进新任务；共享现有身份/HTTP与上传客户端。无岗位可开始；绑定岗位是用户选择。工作显示回答结束/成果已保存/标准已确认各自状态。不可用/解析不完整/冲突给具体说明，不显示内部lease/hash等实施细节。

组件与API测试先行，再本地浏览器关键旅程。B2b不因缺真实模型API就伪造业务质量结论。

## B3：提案与用户部分确认

文件：新增 standards.py、proposals.py 及测试；复用096已有 standards/standard_revisions/results/operations/reference_edges。repo/tools/routes/service装配由主代理集中接入。

`save_result(kind=standard_proposal)`要求一个明确目标岗位（本次授权集合可包含多个对象，但提案自身目标唯一），服务端生成change_id。校验add/replace/remove、重复target、准确base标准、当前可见来源。初始base=null仅适用于当前无标准；已有标准须给准确标准ref。同一提案修订不可变，更新生成新revision。已知候选范围来源及其传递来源不能进入通用提案。

`POST /positions/{P}/standards/confirm` 和 GET current 使用A0既定契约：真实用户+CSRF+去重键，岗位/提案当前修订/摘要/基准/来源/selected_change_ids逐项校验。事务锁岗位标准槽位（首次也串行），仅应用选中条目；保留未选正式项。add生成正式item_id并保存change映射，replace/remove必须命中基准项；不能静默变成add。操作/标准修订/来源边同事务。

同用户另一会话更新S2后，基于S1的确认返回409 ConfirmError/current_revision=S2，且零写入。不能只改请求expected字段来重定旧提案base。重复同键先重验当前权限，返回原回执；模型没有确认工具。

标准正文作为ExactRef可发现/读取，后续工作能选择正式标准作为basis；精确旧标准不得自动变成current。来源继承保守保留，不能因为仅选部分条目就省略可能有关的候选来源。

验收：真实HTTP部分确认→再次读取标准；未选项保留；空/重复/冲突选择422；初次确认竞争、旧base/旧proposal409并返回当前UUID；真实owner+授权；候选来源直接/传递均拒绝；来源失效后确认与读取阻断；事务回滚、幂等回执；不修改官网。

## B4：端到端验收与交付

1. 本地工程旅程：公开JD材料→自主读取/方法→澄清→校准成果→关联岗位→提案→选中部分→并发冲突→新提案确认→另一会话基于同一正式标准继续；真实上传/数据库/身份链，模型边界本地可控。
2. 使用用户指定的真实模型profile跑公开静态JD与真实方法。单独保留输入材料/方法/结果/工程调用证据；不得使用真实候选人材料或引入自动HR评分。专业质量由用户或其指定HR/用人经理审读，重点是具体判断、证据、边界与方法适用性。
3. 前端关键旅程由本地浏览器验证；记录页面与后端验证区别。
4. 全分支独立审查、定向回归、根目录两份文档和总计划同步。提交本地分支，停在B阶段验收，不部署/推送/进入C。

## 进度

- [x] B1a PDF/DOCX 持久解析与覆盖。
- [x] B1b 专业内容构建、发布固定与浏览/选择。
- [x] B2a 精确文件导出与撤权下载。
- [x] B2b 新工作界面与跨入口使用（原生文件上传浏览器分项见下）。
- [x] B3 提案、部分确认与并发。
- [x] B4 API/数据库/进程工程验证。
- [ ] B4 浏览器全部分项（其余交互已验，原生文件上传待权限开放）。
- [x] B4 真实公开模型运行与证据导出。
- [ ] B4 用户或指定 HR 专业审读。
- [ ] B阶段用户验收。


证据：[B 核验报告](../../reviews/2026-09-10-hr-cloud-loop-b.md)。后端216通过/1默认真实模型跳过，身份回跳272通过；真实模型完整旅程单独通过。原生文件选择器已打开，setFiles被Chrome扩展权限拒绝，因此该浏览器分项继续保留待验，不把API上传代替页面上传。独立评审/最终前端修订与截图以报告最终记录为准。
