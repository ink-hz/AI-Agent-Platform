# DAQ 提问人身份接入设计

用户已授权端到端修复、测试和发布。本任务只改变下游身份投影和展示，不改变回答、模型、知识或相机服务。

## 已核实基线

2026-10-30 生产 DAQ 为 `2caa860465820d0c4e6c0f42c5a15eca030389a8`，镜像 `sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7`。正常 DSN 只读查询：17 个会话均为 enterprise_member/platform_enterprise，user_id 与 owner_subject_id 一致。DAQ 写入端从验证后的 PlatformSubject 构造 Session 和持久化记录，拒绝跨主体会话。

生产平台 API 和 cloud-sync 共用镜像 `sha256:71330ae264007f8e5b683083c17710ce13bc50547147dd0f35420afa809fef6d`，实际应用增量为 `b4c258b4`；镜像继承的 revision 标签 `28272ec4` 不是完整运行代码身份。现有云同步直接读取源库，每来源独立约 10 秒轮询。

## 选择与边界

复用云同步的 opaque verified_subject 和控制库授权解析。相对于传姓名或新增目录同步，这种做法避免昵称成为归属依据，并保留现有加密、来源校验和会话权限。相对于仅扩展 UI，补齐投影、导出/导入校验、授权函数、解析及展示才能完成闭环。

DAQ 仅从规范 owner_subject_id/owner_subject_type、匹配 user_id 和 platform_enterprise 模式产生 enterprise_member 引用。空、部分、冲突归属不从 metadata 昵称或引用补猜。旧 importer 也清除 metadata 自报引用。普通导出仍默认不含引用；受控云同步显式 opt-in。

VerifiedSubject 只接受 source_kind=fae 且 agent 为相机或 DAQ；DAQ 仅允许 enterprise_member。DAQ session/turn/user HMAC 输入保持原样，补引用不迁移任何业务键。正文、反馈、复审、时刻和来源不变。

解析器逐 Agent 分组，以 (agent, subject, type) 保存本次结果，禁止跨 Agent 复用授权或名字。新加法控制迁移 127 提供带 allowlisted agent_id 的受限函数，验证活跃管理会话和该 Agent 的 observation scope。旧 126 函数保留。无引用清除副本自报姓名；有引用无 profile 保留引用并显示姓名不可用。列表、详情、每轮均使用同一可信归属。

## 验证与发布

先复现新增 DAQ 正例失败，再通过投影、加密副本、真实 PostgreSQL 授权和 HTTP 消费者测试。覆盖账号切换、跨用户/跨 Agent、混合列表、缺身份、伪造 metadata、撤销管理会话及相机既有行为。DAQ HTTP→真实持久化验证继续使用验证提供方测试边界，不调用模型。

只从精确合并版本构建平台增量，保留现役基础镜像、Compose 输入、静态旧资源、环境、Nginx 和无关服务。加法迁移备份后应用；API 先能读取新引用，之后更新已有 cloud-sync 镜像，沿正常快照补齐历史。只读前后核对 session/turn/question/answer/feedback/review 稳定。无法证明的历史继续未知。生产不发模型题，不伪造会话或管理登录。

回滚恢复旧 API/worker 镜像和 Compose 选择；保留加法函数与现有业务数据，不恢复共享数据库覆盖新记录。若旧 worker 会移除新身份引用，先停止本次 worker 再按记录回退。实际发布身份、回填和可核验会话另写回执。
