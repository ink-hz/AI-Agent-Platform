# HR 场景成果与连续引用实施计划

> 执行方式：executing-plans，在当前三仓隔离工作树内顺序实施，顺序实施；完成前按 requesting-code-review 技能做一次独立审查，不逐任务等待批准。

目标：主对话可以产生有候选人归属的面试成果、显式引用上一份成果继续工作，并在确认岗位标准前复核正文。

架构：保留 Turn/Attempt、授权工具、结果账本、确认链。新增严格 v7 契约与 candidate-analysis.v2；v6 历史数据按原契约读取，不改写冻结命令。Team 继续一份 Hannah 角色文件。

约束：接口优先、页面最后；只用自有 HTTP/数据库测试。模型提供方替换不算质量验收。生产业务消息不发送，已有 v6 部署和测试不重复运行。根工作树脏文件保留。

## T1 契约与版本路由

- [x] 新建 `backend/app/execution_relay/contracts_v7.py`、`frozen_command_v7.py`、`scripts/publish_hr_v7_contracts.py`；生成 `contracts/hr-execution/v7/` 及 MetaBot 对应 schema。
- [x] `inputResultRefs` 必填、去重、纳入 contextHash 和 commandHash。`baselineRefs` 非空、严格三分支；新增候选人面试方案/记录/沟通草稿与候选人分析 v2。
- [x] `backend/tests/test_hr_v7_contracts.py` 先验证缺少基准、遗漏输入引用、篡改哈希被拒绝，再实现。
- [x] Platform core/frozen/source/readiness/worker 与 MetaBot contract/runtime 按版本解析；旧 v6 字段和哈希不动。

## T2 持久输入与业务边界

- [x] `conversation_models.py / conversation_routes.py / turn_scope.py` 接收并冻结输入引用，受理和重放比较完整引用；新增 `095_hr_deliverable_inputs.sql`，数据库验证 owner、岗位、候选人和结果身份。
- [x] `hr/tool_service.py` 读取精确引用、生成本轮读取收据；候选人成果校验非空基准与确切候选人；面试记录校验引用方案在本次输入且已读取。
- [x] `standard_consent.py / calibration_service.py` 用户正文复核声明绑定结果 hash 与选中条目；服务端候选人来源标记随结果持久化。
- [x] `backend/tests/test_hr_tools_v7_http.py` 复用真实 HTTP/session/signature/lease/DB fixture，验证读→保存→新轮引用→再保存、越权/篡改/撤权拒绝，以及确认缺复核拒绝。保留真实授权，不伪造业务成功。

## T3 主对话入口和角色交付

- [x] `conversationTypes.ts / conversationApi.ts`、`DirectAgentWorkspace`、`ConversationWorkspace` 串联输入引用、幂等和清除。
- [x] `HrTurnResults.tsx / HrWorkspacePage.tsx` 展示具体成果，继续分析/准备面试携带确切对象与引用；本次参考可打开、移除。只填可编辑要求，由用户发送。切换岗位清理不适用引用。
- [x] 标准确认界面完整展示正文并绑定复核声明；候选人成果无确认成标准按钮。
- [x] Team `bots/hr/CLAUDE.md` 场景小节补结果类型、基准、复盘去标识化和引用纪律，不拆角色。
- [x] 只运行新增组件用例及 TypeScript 编译/构建。

## T4 集成与交付

- [x] 一次跨仓 v7 工程闭环验证固定输入/工具/终态，提供方边界替换明确记录。
- [x] 内联检查契约生成物一致、迁移权限、输入及确认不可伪造、其他 Bot 不受影响；提交三仓。
- [x] 依据已有发布授权协调 HR 三仓升级，先检查在途与磁盘；保存 current+2、回滚和实际版本证据，不运行生产模型消息。若发现阻塞，保留原服务，明确具体原因。
- [x] 更新续接与交付文档，明确完成能力及未经真实模型质量验收的范围。

验证记录：v7 HTTP / 数据库回归通过；跨仓真实进程与签名、MCP 工具及两轮结果引用闭环通过（替换模型 / PTY 提供方）。新增组件检查、前端与 MetaBot 编译和前端构建通过。审查发现的候选人子集附件范围、祖先重复校验已修复；相关 HTTP 回归再次通过。未重复旧 v6 套件，无生产业务调用，无浏览器或真实模型质量验收。

交付完成：见 [v7 交付记录](../../reviews/2026-09-09-hr-deliverables-v7-delivery.md)。三仓 master 已推送并发布，生产没有模型业务消息。
