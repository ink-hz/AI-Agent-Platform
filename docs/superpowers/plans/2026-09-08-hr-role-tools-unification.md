# HR 主对话与角色工具统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 本项目默认使用 executing-plans 在当前会话按依赖执行，不自动启动子代理，不逐任务要求用户批准。用户要求优先于技能模板中的测试或审批流程。

**Goal:** 一次性替换废弃 HR Web 任务产品，在 Hannah 主对话完成按轮选岗位、按需读取、专业分析、结果交付和共同确认。

**Architecture:** Platform 保留 Turn/Attempt、授权、持久化和已确认标准；MetaBot 复用执行器并接入 HR v6 与固定角色包；Team 维护唯一 Hannah 角色、方法和 JD registry。新增业务工具接口，删除五按钮和自动岗位包消费者，同一发布组合一次切换。

**Tech Stack:** Python/FastAPI/Pydantic、PostgreSQL、TypeScript/React、MetaBot Claude PTY、Node.js CLI、JSON Schema、Markdown/YAML。

日期：2026-09-08。状态：T01–T09 已总装，T10 发布准备中；尚未发布 v6。实际检查及限制见 [交付记录](../../reviews/2026-09-08-hr-role-tools-v6-delivery.md)。
依据：[统一设计](../specs/2026-09-08-hr-role-tools-unification-design.md)、[方法库设计](../specs/2026-09-08-hr-scenario-methodology-library-design.md)、[唯一续接摘要](../../runbooks/2026-09-08-hr-web-core-handoff.md)。本计划取代旧计划对本次重新设计的执行顺序，不重做已部署的布局、滚动修复。

## 当前执行记录

用户授权自行完成必要验证与发布，接口优先，不重复无关套件；不另发生产业务消息。三仓均使用 `.worktrees/hr-role-tools-v6`。

- T01–T06：v6 同源契约、按轮范围、授权读取/JD 校验、结构化成果与部分确认、固定角色包、实际 MCP 与恢复已接通。
- T07：飞书真实私聊身份经已登录网页单次关联码关联 owner；同 J 编号按 owner 查询。已关联请求复用同一会话/轮次，确认保存渠道原文与真实确认人。无关联身份的通用分析不获私有标准能力。飞书返回平台对话链接，附件通过网页授权上传。
- T08：主对话按轮选择、候选人/材料、成果定位、部分确认与读取证据完成；保留已认可布局。必要组件检查和构建通过，页面与真实质量留给用户。
- T09：正式迁移 `hr_web/094_hr_role_tools_v6.sql`，撤销旧写函数；旧按钮/API/罐头信封及后台消费者删除，历史标准保留。
- T10：生产只读核对 confirmed/superseded 0 行、当前指针 0、HR 在途 0；不将零记录推导为无人使用。发布前再确认在途与磁盘。发布尚未执行。

## Global Constraints

- 一次性替换，不逐入口上线，不先发布旧契约补丁，不保留旧任务兼容消费者。
- 只改 HR；其他 Bot、模型、渠道配置、Nginx 和全景生产体系不随之重构。
- 复用 `position_task_records` 与 Turn/Attempt，不新增 HR 任务主表、执行器或第二套调度状态机。
- 平台当前已确认标准原位复用，保留完整 modules、确认人、时间、来源和版本链；CLI 无归属卡片不自动导入，未确认草稿不提升为标准。
- 发送采用用户可见的最终正文；建议动作只填入可编辑输入框。切换岗位只影响后续轮次。
- 角色包按 Attempt 固定 commit/清单；重试不得静默采用最新版，不得绕过已撤销的数据权限。
- 新业务结果经显式 schema 校验、幂等持久化后返回引用；不从 Markdown 注释提取。
- JD 使用前校验 24 小时；36 小时是健康降级，90 天是来源陈旧提示。读取目标 2 秒，刷新前台等待预算 60 秒，均尚未验证。
- 知识索引不超过 4 KiB，hr_reference_knowledge 不超过 8 KiB；方法正文通过原生 Read 按需读取，不设置第二套方法编译预算；符合设计口径的完整初始命令字节降幅目标 50%，不能据此宣称总模型消耗下降。
- 必要验证接口优先、页面最后由用户验收；不追加无关全量套件、不重复已完成检查。模型提供方替代必须明确标注，不能称为真实业务质量验收。
- 每包记录相关变更及必要检查结果；不把模板中的“每步测试/每步审批”变成实际工作流程。代码检查、发布门禁与用户质量验收分别报告。

## 仓库、文件归属与依赖

路径前缀：`P` = `/Users/neo/Developer/work/AI-Agent-Platform`；`M` = `/Users/neo/Developer/work/metabot-dev`；`T` = `/Users/neo/Developer/work/Orbbec-Agent-Team`。下文文件均相对这些根目录。

Platform 基线为 `6777d227a28c1548c6cdee480913b6f72818dde2`；MetaBot HR 已部署基线为 `6ddbdefffede245d48ec20b43ee487c7ff2c732c`，不能误用 M 根目录的旧 HEAD 覆盖已上线恢复逻辑。执行前记录三仓当前 HEAD 和脏文件，使用隔离工作树，保留已有未提交成果；文档阶段不创建实施分支。

| 任务 | 负责范围 | 依赖 | 可核对交付物 |
|---|---|---|---|
| T01 | Platform 主持、三仓共用契约 | 无 | v6 wire schema、业务工具 schema、错误及引用约定 |
| T02 | Platform 按轮归属 | T01 | 11 处 Python 引用与 SQL 门禁逐项落实，候选人解析可按轮关联 |
| T03 | Platform + Team 授权读取/JD | T01、T02 | 官网凭据、标准与材料读取、读取记录、deadline/降级 |
| T04 | Platform 结果与确认 | T01、T02 | 精确结果引用、幂等提交、原已确认标准继续可用 |
| T05 | Team 方法与角色包 | T01 | 复用七份参考知识、唯一资源身份、不可变角色发布清单 |
| T06 | MetaBot + Platform 运行接线 | T02、T03、T04、T05 | v6 实际工具调用、固定角色 cwd、流式结果与恢复 |
| T07 | Team + Platform + MetaBot 渠道校准 | T04、T06 | CLI/飞书真实用户映射、同服务读写，停本地双写 |
| T08 | Platform 主对话和方法展示 | T02、T04、T05、T06 | 一个发送入口、按轮岗位、精确结果、方法使用证据 |
| T09 | 三仓删旧与总装 | T03–T08 | 废弃入口、提示词、领取、消费者和本地写入退出 |
| T10 | 三仓一次发布与用户验收 | T09 | 版本组合一致、已确认成果可见、用户业务质量记录 |

T03/T04 的服务代码和 T05 内容可以在接口固定后独立推进；这只是依赖关系，不是自动委派或多次发布。主路径为 T01 → T02 → T03/T04/T05 → T06 → T07/T08 → T09 → T10。

共有文件的归并顺序：T01 负责契约；T02 负责按轮 schema；T03/T04 将读取、提交所需数据库变更归并到同一 HR 迁移；T09 最后处理 `main.py` 装配和旧函数撤销。不得各自重写公共文件后直接覆盖。

## T01：固定 v6 和业务工具契约

**文件：**

- 新增 P `contracts/hr-execution/v6/command.schema.json`、`callback.schema.json`、`business-tools.schema.json`、`manifest.json`。
- 新增 P `backend/app/execution_relay/contracts_v6.py`。
- 新增 M `src/api/routes/core-chat-v6-contract.ts`；发布镜像位于 M `src/runtime/contracts/hr-execution-v6/`，由 P 契约生成/复制，不独立编辑。
- 参考 P `contracts/hr-execution/v5/` 与 M `src/api/routes/core-chat-v5-contract.ts`，不直接放宽 v5 校验。

**输入/输出：** 保留现有 run/command/turn/attempt/lease/签名等身份字段；新增角色包、按轮 scope、能力描述和已登记结果引用。下面固定的是新增 wire 名称，不是已实现 API：

```typescript
type HrTurnScope = {
  positionId: string | null;
  positionCandidateIds: string[];
  attachmentIds: string[];
};
type HrRolePackageRef = {
  teamCommit: string;
  catalogRelease: string;
  manifestSha256: string;
};
type HrMethodSelection = {
  resources: { source_commit: string; id: string; revision: number; sha256: string }[];
  catalogRelease: string;
};
type HrResultRef = { resultId: string; schemaId: string; contentSha256: string };
// Command: contractVersion = "core_chat_collaboration_v6"
// contextMode = "frozen_intent_with_tools"
// scope: HrTurnScope; rolePackage: HrRolePackageRef
// methodSelection: HrMethodSelection | null
// Terminal result payload: resultRefs: HrResultRef[]
```

- [x] 定义固定初始意图的 canonical hash、scope/角色包哈希覆盖范围；保留现有签名及附件/输出 grant 规则。模型正文和公开日志不能包含工具 bearer 凭据。
- [x] 固定查询工具 `hr.read_context`、提交工具 `hr.submit_result`、确认工具 `hr.confirm_standard` 的请求/响应 schema。查询必须声明资源类型及读取/重新核验意图；写入必须有稳定 operationId。owner 不作为模型可自行决定的参数；网页所选方法用 `methodSelection` 记录，模型后续选择作为事件追加。无岗位解析用附件范围，有岗位候选人分析使用 positionCandidateIds。
- [x] 定义明确错误：权限失效、范围不符、版本冲突、同键异内容、来源不可用、未登记结果引用；不得统一包装成“模型失败”。
- [x] 规定业务提交与终态分离：持久结果允许在执行过程中展示；回调引用须属于该轮。提交成功而终态回调丢失时，通过原 operationId/结果引用恢复，不能重造结果。
- [x] 固定共享 schema 发布哈希及三仓消费方式。必要契约检查只覆盖合法命令、未知字段拒绝、身份/哈希不一致；不启动模型。

**完成条件：** T02–T08 使用同一组字段、错误和 schema 版本；不存在各仓自定同名协议或暗改 v5。

## T02：落地每轮岗位范围与候选人关联

**文件：** 修改 P `backend/app/agent_brain/conversation_models.py`、`conversation_service.py`、`conversation_repository.py`、`conversation_context.py`；P `backend/app/hr/task_context.py`、`context.py`、`repository.py`、`candidate_parser_runtime.py`。新增 P `backend/app/hr/turn_scope.py`。

**迁移：** 以 P `backend/control_migrations/pending/hr_role_tools_v6.sql` 汇集本次 schema 及函数变更；T09 集成时按实际迁移头分配正式编号并移动到运行目录，不能发布 pending 和编号版两份。复用 069/093 的现有记录，新增角色包/方法目录/事件引用需要的列，不改写历史 prompt/hash。

**输入/输出：** 统一对话发送增加 T01 的 `scope`；受理事务保存用户正文、材料引用、轮次范围。`turn_scope.py` 提供 `load_authorized_turn_scope(owner_id, conversation_id, turn_id)`，所有新读取/写入使用其返回的可信范围。

- [x] 有岗位轮次扩展 `position_task_records`；无岗位轮次使用既有 Turn/Attempt 输入记录，不伪造 position。受理前验证会话/岗位/材料权限，正文、附件和 scope 同一幂等请求提交。
- [x] 按设计 §2 清单修改 `conversation_context.py`、`task_context.py`、`repository.py` 三处、`context.py`；历史读取明确隔离。新任务不调用旧 CTE，T09 删除旧入口。
- [x] 修改候选人解析三处 Python 检查和 070 的关联/恢复/结果/碰撞函数；以 parser operation/attempt 与来源 turn 替代“整个会话未绑定”和 `started_by_client_request_id` 前置，保留精确附件与租约校验。
- [x] 修改 069/078/093 按轮记录、069 标准来源、079/086 情报引用门禁；不动全景生产调度。`import_cli.py` 只保留历史范围读取。
- [x] 按来源 turn 关联文件和结果；岗位详情的会话计数/列表对按轮记录去重，不从当前选择反推历史归属。
- [x] 必要接口检查集中覆盖：同会话 A→B→通用；运行 A 时准备 B；同键同正文但不同 scope 拒绝；B 不读 A 的附件；候选人解析恢复仍定位原附件。

**完成条件：** 设计列明的每处门禁都有实际改动或删除去向；改变下一轮岗位不改变当前执行和历史归属。

## T03：授权上下文读取与 JD 凭据

**文件：** 新增 P `backend/app/hr/tool_routes.py`、`tool_service.py`；修改 P `backend/app/execution_relay/routes.py`、P `backend/app/hr/importers.py`、`task_context.py`、`panorama_context.py`。修改 T `services/hr-jd-sync/cli.mjs`、`registry.mjs`、`sync.mjs`、`official-client.mjs`、`state-store.mjs`、`constants.mjs`、`bots/hr/.claude/skills/jd-registry/SKILL.md`。

**输入/输出：** `hr.read_context` 通过新的 `POST /api/v1/execution-worker/hr/v6/query` 访问。固定资源类型：`official_position`、`confirmed_standard`、`material`、`intelligence`；返回来源引用、版本/哈希、校验状态和内容，记录关联 turn/attempt 的读取事件。

- [x] 校验签发给当前 worker/attempt 的能力凭据，再校验 T02 范围、实时权限及材料保留状态。续租/恢复重新签发权限，不修改不可变初始意图。
- [x] 复用现有标准、材料和情报业务查询；云端数据不通过任意 SQL/文件路径工具开放。材料使用已有授权下载，查询只返回该轮允许的引用或内容。
- [x] 增强 `verify-job <query> --json`：同次可信读取公开 `registryVersion`、`jobContentHash`、`lastSuccessfulSyncAt`、`verification`，成功与最后有效版本回退都使用同一凭据格式。哈希复用 registry/importer 算法。
- [x] JD 超过 24 小时或疑似下线才刷新；60 秒 deadline 传入同步和 HTTP 调用，取消后不发布半成品 registry。超时读最后有效版本并标降级；无快照明确不可用。
- [x] 冻结初始命令只保留资源引用；首次读取记录实际版本，重读复用记录版本，显式重新核验追加新记录。JD 是 Team registry 的权威事实，Platform 仅投影，不双向覆盖。
- [x] 必要接口检查覆盖：有效/过期/超时/无快照四种返回语义、撤权或过期附件拒绝、来源哈希可对应。网络边界替代时注明非官网真实核验。

**完成条件：** JD、标准、材料和情报存在真正可调用的授权查询面；首轮不继续完整前推这些数据。

## T04：结构化结果、确认与现存标准

**文件：** 新增 P `backend/app/hr/result_submission.py`、`calibration_service.py`；修改 P `backend/app/hr/tool_routes.py`、`repository.py`、`candidate_repository.py`、`resource_routes.py`、P `backend/app/agent_brain/turn_result_projection.py`。数据库变更归并 T02 的 HR 迁移。

**输入/输出：** `hr.submit_result` 对应 `POST /api/v1/execution-worker/hr/v6/results`，返回 T01 `HrResultRef`；`hr.confirm_standard` 对应 `POST /api/v1/execution-worker/hr/v6/confirmations`，返回实际已确认版本引用。用户读取结果使用带既有登录授权的 `GET /api/v1/hr/results/{resultId}`。

- [x] 为分析交付、内部建议、候选人分析、面试/比较和方法步骤证据绑定明确 schema；复用现有业务结果存储。必要 operation→结果回执只承担幂等提交，不能长成第二套任务主表。
- [x] 同一轮可多次提交独立结果；同 operationId/同内容返回原引用，异内容拒绝。业务结果与回执原子写入；不能有回执成功、业务结果缺失。
- [x] 新标准服务原位读取 `positions.current_context_version_id` 和 `position_context_versions`，保留完整继承 modules、原确认信息、来源链与旧记录可见性；指针不一致报具体错误。
- [x] 保存共同确认时绑定真实认证用户消息、已展示的结果版本、明确条目及当前基线。原消息和展示记录由服务器核对；模型传入 `confirmed=true` 不构成人工确认。部分确认只合并明确条目；歧义或版本冲突继续澄清。
- [x] 终态仅引用已提交且属于该轮的结果；文件交付复用附件产物授权，普通结果不能冒充文件上传成功。
- [x] 必要接口检查覆盖：提交后断连重试、跨轮引用拒绝、旧完整确认模块仍可见、部分确认/并发冲突、伪造确认拒绝、2–10 个同岗位候选人比较范围。

**完成条件：** 新结果能按精确引用找到；已有确认成果不因切换消失，模型生成分析不会自动变成长期标准。

## T05：唯一方法目录与不可变 Hannah 角色包

**已发现并复用的实现：** Platform `feat/hr-methodology-library` (`fc4b1b6`) 已合入实施分支；Team `feat/hr-reference-knowledge` (`6cea3c8`) 作为新实施工作树基线。参考 [方法库设计 §2.1、§2.2](../specs/2026-09-08-hr-scenario-methodology-library-design.md)，不再新建 scenarios/models/methodologies 双目录或第二套 ID。

- [x] 复用 Team `bots/hr/knowledge/recruiting/` 七份参考知识及 sources，资源以文件 stem 为稳定 ID，带 revision、domains、knowledge_forms。
- [x] 复用同一 commit 的不可变知识发布、清单哈希、Web 浏览和 user_selected_resources；既有 CLI 读取试验引用原记录，不重复跑。
- [x] 将 v6 methodSelection 收敛到上述实际资源身份，去掉旧设计固定场景和三方法上限，不增加方法执行编译器。
- [x] 增加完整 Hannah 角色包发布；递归包含角色、共享规则、skill 与同一知识目录，并按 Attempt 固定 cwd、commit 与清单。
- [x] 更新 Hannah/skill 的工具入口、按轮范围与共同确认指令；原生实际读取证据与模型自述分别展示。

**完成条件：** 执行器和 Web 引用同一资源修订，完整角色版本可恢复；已有内容实现不能冒充 v6 运行时已接通。

## T06：MetaBot 工具桥、固定会话上下文与恢复

**文件：** 新增 M `src/api/routes/core-chat-v6-runtime.ts`、`core-chat-v6-routes.ts`、`core-chat-v6-service.ts`、`src/runtime/hr-tool-bridge.ts`；修改 M `src/api/routes/core-chat-session-store.ts`、`core-chat-event-outbox.ts`、`src/engines/claude/pty/pty-session.ts`。新增 P `backend/app/execution_relay/routes_v6.py`；修改 P `backend/app/agent_brain/direct_mission_adapter.py`、`direct_command_binding.py` 及对应 relay 装配。

**输入/输出：** 消费 T01 命令、T03/T04 工具接口、T05 角色包；返回既有执行进度加 v6 结果引用。工具桥持有凭据并限制方法/端点，模型只获得业务工具参数。

- [x] 原生运行时注册三个业务工具，工具请求绑定真实 command/turn/attempt/lease；使用授权 HTTP 面，不授予数据库访问。复用既有持久执行与事件 outbox，不复制新 store/worker。
- [x] 执行前校验角色包清单并设置固定 cwd；实际原生文件读取按路径关联版本事件。无原生读取记录时标未证实，不以模型自报替代。
- [x] 同一 Web 会话的岗位切换不能复用夹带其他岗位历史的原生 session。session store 记录所用范围和角色包；范围变化或撤权后以获准历史建立新原生 session，Web conversation 保持不变。恢复同轮使用其原范围和清单。
- [x] 发送冻结意图和必要历史，按需读取资源；流式进度包括 JD 核验，终态引用已登记结果。处理结果已提交、回调未送达和租约过期，过期 worker 不得新增业务写入。
- [x] 复用既有恢复检查，只对新增 v6 工具恢复、范围隔离和角色版本做最小相关验证；已有 v5 测试结果不冒充 v6 通过，也不重跑无关 PTY 套件。

**完成条件：** 真实分派能进入固定角色包、调用授权工具并回传结果引用；仅平台拼好 JSON 不算接通。

## T07：CLI/飞书统一校准服务

**文件：** 修改 T `services/hr-role-calibration/cli.mjs`、`schema.mjs`、`bots/hr/.claude/skills/role-calibration/SKILL.md`；新增 T `services/hr-role-calibration/platform-client.mjs`；新增 P `backend/app/hr/channel_identity.py`，在 T06 工具桥关联可信渠道身份。

**输入/输出：** CLI/飞书使用同一 T04 校准业务服务；认证用户映射到 Platform internal user。岗位以 owner/position 为范围，J 编号仅用于在该用户范围内解析。

- [x] 以经过验证的渠道会话身份或登录凭据解析 internal user；映射写入来自可信身份流程，不能根据用户或模型输入的 UUID 直接建立关系，也不以 Bot 账号代确认。
- [x] CLI 读取当前标准、提交建议、共同确认均调用同服务；确认保留原渠道用户消息证据和真实确认人。没有身份映射时分析可继续，私有标准读取/保存明确拒绝。
- [x] 停止把本地 J 编号卡片作为当前标准；原文件封存且不自动导入。删除 `store.mjs` 的运行调用在 T09 完成，不能双写观察一段时间。
- [x] 必要检查覆盖正确/缺失/不匹配身份、跨 owner 同 J 编号、重复确认；不发送飞书业务消息作为验证。

**完成条件：** Web 和 CLI/飞书对同一有权岗位看到同一确认版本；历史本地文件不能盖掉平台真实确认成果。

## T08：主对话与方法展示统一入口

**文件：** 修改 P `webui/src/workspaces/hr/HrWorkspacePage.tsx`、`useHrChatPosition.tsx`、`HrPositionPicker.tsx`、`HrPositionDetailsDrawer.tsx`、`HrConversationOutcomePanel.tsx`、`HrTaskReferences.tsx`、`webui/src/conversationApi.ts`、`conversationTypes.ts`、`hrApi.ts`、`hrR12Api.ts`、`hrR12Types.ts`、`App.tsx`。复用已合入的 `HrKnowledgePanel.tsx`、`reference_knowledge.py` 与 `reference_knowledge_routes.py`。

**输入/输出：** 统一发送 T01 scope 与真实正文；方法浏览读 T05 包的授权投影，结果卡打开 T04 resultId，执行状态来自已有 Turn/Attempt。

- [x] 岗位选择影响下一次发送，不创建额外岗位会话；保留输入草稿、附件及发送幂等键。历史消息展示自己那一轮岗位；运行中切换下一轮不改当前任务。
- [x] 建议动作与方法“用于当前对话”仅填入可编辑正文和方法引用；发送最终输入时才受理。材料在发送时绑定本轮，不靠任务按钮另起请求。
- [x] “本次参考/方法”展示实际读取版本、来源和步骤产物；未证实读取准确标示。结果卡直接定位具体结果、文件或确认条目，支持继续修改与自然语言确认。
- [x] 接统一参考知识浏览与历史修订深链接；不让浏览器加载另一套正文。不发布空壳详情页，不把模型选择规则暴露为用户必须填写的内部字段。
- [x] 移除旧工作页的任务控制依赖，必要资料和候选人能力移到主对话面板；保持已获认可的宽度、渐变毛玻璃、底部输入、滚动及局部切换布局。
- [x] 完成一次必要前端构建；页面验证留给用户。只有出现明确的新交互问题才针对该问题查看页面，不重复巡视已上线布局。

**完成条件：** 用户无需进入独立岗位工作页即可完成输入、分析、查看精确成果、确认与后续复用。

## T09：删除旧产品与三仓总装

**文件：** 修改 P `backend/app/main.py`、`backend/app/hr/structured_output.py`、`task_service.py`、`task_routes.py`、`task_repository.py`、`task_result_projection.py`、`position_package_projection.py`；删除 P `webui/src/workspaces/hr/HrPositionTaskMenu.tsx`、`HrPositionWorkspace.tsx` 及仅为废弃行为服务的测试。修改相关路由引用、T07 本地存储调用、三仓 runtime 装配与 HR 迁移。

**输入/输出：** 接收 T02–T08 的连通能力；输出只运行新 HR 业务路径的同一发布组合。

- [x] 删除五个岗位任务启动 API/罐头正文/轮询 CTE、`HR_WORKFLOW_CONTRACT_V1` 自动注入、旧岗位包与任务信封扫描/领取/完成失败消费者及注册。候选人分析通过新提交面交付后删除其旧入口；全景独立使用的共享 schema/函数按调用关系保留。
- [x] 撤销 `claim_position_package_projection_v76` 等废弃运行函数及草稿自动生产者；不能只改 parser，让空草稿继续写入。通过新迁移撤销，保留历史迁移内容。
- [x] 清除旧岗位页面路由、重复控制器与本地校准写调用；废弃 URL/API 明确不可用，不静默重定向成一次新模型任务。
- [x] 保留已有消息、附件、已确认标准和历史读取；不执行整表清空。空草稿清理不是本次发布依赖，若以后做需精确来源清单和引用核对。
- [x] 检查旧符号残留的用途并归档删除清单；历史 SQL/文档/独立共享能力的命中说明用途，运行装配不得继续引用旧消费者。分配正式迁移编号，仅部署正式迁移。
- [x] 三仓同一包做必要构建与接口连通检查；已完成的相关检查引用结果，不因总装再跑完整套件。

**完成条件：** 删除的是入口及后台生产链，旧消费者不会在新回答到达后创建草稿或失败账本。其他 Bot 继续使用原配置。

## T10：一次切换、数据可见性与用户业务验收

**文件：** 修改 P `deploy/metabot.runtime-contract.json`、部署实际消费的对应 HR 配置与 T `scripts/deploy_bots_to_agentops.sh`；新增 P `docs/runbooks/2026-09-08-hr-role-tools-v6-cutover.md` 和 `docs/reviews/2026-09-08-hr-role-tools-v6-delivery.md`，更新唯一续接摘要。不复制另一套云端发布器。

**输入/输出：** 固定 Platform/MetaBot/Team commit、v6 schema 哈希、role package 与镜像清单；沿用已有发布渠道与磁盘保护。

- [ ] 切换前使用授权只读连接检查 confirmed/superseded 数量、owner/position、当前版本指针，记录账号可见范围；准备新读取结果与原标准 ID/完整 modules/确认元数据对照。零 confirmed 不能推导无人使用。
- [ ] 准备具体发布组合、迁移及恢复记录，检查磁盘：根盘当前可用 ≥25 GiB，预计 ≥20 GiB，发布后使用率 ≤75%；净增 >1 GiB 说明原因。staging 仅在 `/data/staging/<应用>/<deployment_id>/`，遵守既有清理和版本保留规则。
- [ ] 暂停 HR 新受理；实际在途任务使用既有完成/取消流程收尾，不伪造终态或自动重跑。切换三仓 HR 配置后核对协议与角色包一致才恢复受理；版本不匹配时 HR 保持关闭。
- [ ] 核对新标准读取仍返回原确认成果，结果链接/文件授权有效，旧生产者停止。出现数据可见性问题就修复对应读取，不跳过用户成果。
- [ ] 用户验证：同一对话选岗位、提出要求、上传材料、分析和查看精确成果、部分确认、切换岗位再返回复用。使用用户实际业务输出评价岗位相关性、证据、可执行性和未知项；不另发生产业务消息或盲目重跑模型。
- [ ] 记录初始命令字节与实际工具读取量，区分有效快照/刷新耗时；与留存同输入基线比较，不额外调用模型只为测字节。未有质量证据时明确“已替换链路，质量待用户验收”。
- [ ] 汇报分开列接口回归、故障恢复、前端构建、页面与生产质量的实际结果及未验证项；更新版本/资源标识，注明不得重复部署。

**完成条件：** 三仓新 HR 链路一致运行、已确认成果可见、废弃生产者退出；工程交付与用户业务质量结论各有实际证据，不混称通过。

## 覆盖与执行起点

| 设计要求 | 对应任务 |
|---|---|
| 一个主对话、建议动作可编辑、按轮切岗 | T02、T08 |
| 11 处 Python + SQL 门禁、候选人独立会话限制 | T02、T09 |
| JD 唯一事实、凭据、新鲜度、延迟 | T03、T06 |
| 方法来源、唯一 ID、实际读取、固定版本 | T05、T06、T08 |
| 结构化结果、真实确认、现有标准保留 | T04、T07、T10 |
| 不变命令、读取恢复、幂等和授权 | T01、T02、T03、T04、T06 |
| 一次删旧、无兼容消费者、安全切换 | T09、T10 |
| 上下文字节、真实业务质量、不过度测试 | T03、T05、T06、T10 |

T01–T09 的工程总装已完成，继续 T10 一次发布；真实业务质量仍待用户验收。
