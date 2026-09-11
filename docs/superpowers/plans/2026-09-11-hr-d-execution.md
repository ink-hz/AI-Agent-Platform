# HR D 本地实施记录

状态：2026-09-11 用户“还不能开始吗？你继续呗”授权后，已在 `feat/hr-cloud-loop-d` 开始；C 分支保留在 `95d33c3`。本计划落实总体架构的既有场景，不另建总体设计。生产擦除发布、真实候选人模型服务与研究计量配置分别保留自己的验收门槛，不阻止无关的本地开发。

## 全局约束

- 接口、真实一次性 PostgreSQL 与授权回归优先；页面最后验收。
- 单用户私有；用户身份、当前对象、准确引用、撤权、删除传播不可弱化。模型工具仍为五个，确认仅走用户 HTTP。
- 场景到方法由 Hannah 自主推理；不增加关键词路由、固定业务步骤、总分或录用决策。
- 原始面试记录由用户提供，模型整理另存派生成果；方案不能充当已发生的回答。
- 不改已发布迁移或 C 原始证据。新迁移进入 `hr_agent/101`；公共 100 未发布，其安全发布顺序不变。
- 模型试验只用公开或明确虚构材料与 HR 指定 Opus5；不部署，不触碰真实候选人。

## Task 1: 候选人成果可发现与准确续作

实现保存成果时自动关联其服务端核验的对象，使对话、岗位、候选人读取同一成果。保存事务写 result_links；不可接受超出本次对象的模型关联。

用户确认候选草稿时，把该准确草稿关联到新建/明确选定的候选人。一般 link_result 不允许把任意成果挂到另一候选人；确认路径具有已经核对源、草稿与用户选择的独立语义。不得改写已有 result_revision 正文、objects 或 hash。对已经确认的 candidate_documents，读取端也能发现其准确 result_ref；不能以 current 替换确认的旧修订。通用成果列表继续显示该成果最新修订；read_candidate.documents 单独保留每次确认的准确 ref，候选入口必须将“已确认草稿”与“关联成果”区分，不能任挑某次确认覆盖通用列表。

read_candidate 返回已授权 position_ids，供选定候选人后构造明确岗位关系；不凭同名推断。

主要文件：repository.py、repository_views.py、candidates.py；按实际需要抽辅助函数，避免复制隐私校验。测试覆盖：保存后跨三入口准确读取；新建与已有候选人确认的草稿可发现；后来修订不偷换已确认 ref；跨 owner/跨候选人任意关联拒绝；相同幂等请求不重复；撤权仍拒绝。

任务先写最小失败测试，执行相关结果/候选人/HTTP 数据库回归。报告需给真实命令、结果、变更边界和未完成项。

## Task 2: 用户面试原文登记

复用普通附件上传：UTF-8 text/plain 原文由 user_input 附件生成准确 material ref，无需模型解析。新增用户 POST/GET `/api/hr/agent/candidates/{candidate_id}/interview-records`，以及准确 GET `.../interview-records/{record_id}` 返回该用户记录与原文。

POST 仅接 material_ref（必须原生 UTF-8 text ref）、title、occurred_at（可空）、position_id（可空）、interview_plan_ref（可空）；Idempotency-Key。拒绝额外 authorship/source_kind/正文参数。响应 authorship 固定 user_supplied。原文留在附件链，登记只保存加密标题/时间与准确引用。首批文本记录上限32,000 Unicode码点；准确GET读取附件而非AI结果，返回text与material_ref并在返回前重验当前权限，列表不携带正文。

101 新增 candidate_interview_records；personal_materials 增加 registered_by_record，与 registered_by_item 恰有一种来源；使现有模型出站与标准提案个人来源检查覆盖用户记录。普通上传 source_kind 在数据库固定，登记锁必须重新检查 user_input，不能只信 MaterialService 或客户端。原文非空白、严格 UTF-8、准确身份、同 owner、可读/未过期/未删除；可选岗位必须已关联候选人；可选方案必须准确 interview_plan 且对象含当前候选人/所选岗位。登记与个人标记同事务。

幂等和 owner 隔离、错误时零落盘、agent_output 不能冒充用户记录、撤权传播、派生标准仍受个人来源保护须有真实数据库/API 测试。正文通过已有 material 工具读取，模型整理沿用 kind=interview_record 与 source_refs，不新增工具或 ExactRef kind。

## Task 3: 候选人工作入口

新 Loop 候选人入口可选已确认候选人、查看原始材料/准确草稿/成果、带选中准确引用继续工作。面试原文文本框经普通附件上传再登记；用户原文与 AI 整理分别标识，不能静默混用。请求/对象切换清除前一个候选人的选择与展示。快捷意图可编辑且不构成固定路由。

组件与 API 测试覆盖候选 A→B 清空、原文上传登记、准确成果引用续作、失败不伪装已保存。不接旧 DirectWorker 候选人页面作为新链验收。

## Task 4: 场景与方法验证

在角色内容中明确方案、用户原始记录、AI 整理及待核验事实的区别，保留自主选方法。新增不可变知识发布，旧工作继续原发布。使用虚构材料通过真实新链 API 验证搜寻→评估→针对性面试→原始记录→复盘；工程替身与真实模型审读分开报告。对 W3/W4 分支列出证据与尚不可判项，不按工具次数、措辞或 AI 赞成票冒充业务通过。

## Task 5: D7 计量与最终交付

已提交探索样本 `965a790`：6 次合成请求，报告见 `docs/reviews/2026-09-11-hr-d-metering.md`。这不是运行时扣记校准通过，缓存仍未验证，不调整 floor 或部署配置。

逐任务独立代码评审；最终相关回归、自检与前端编译后提交 D 评审包。W1–W12 映射同步两份主文档。D 本地实施完成与生产/真实候选人/专业验收分别陈述。

## 进度

- [x] D 分支保留与只读缺口盘点。
- [x] D7 六样本探索与留证（965a790）；校准决策未通过。
- [ ] Task 1。
- [ ] Task 2。
- [ ] Task 3。
- [ ] Task 4。
- [ ] 最终评审、回归与交付包。
