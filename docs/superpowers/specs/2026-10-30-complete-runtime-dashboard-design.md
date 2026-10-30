# 完整运行数据看板与观测契约修复

日期：2026-10-30。用户在核对错误数量、VOC缺数及旧渠道混算之后明确“开始修复，完整的数据看板”，批准先修数据接入/统计定义、再落实界面的方向。本方案落实该授权，不再次要求用户确认已明确的修复。开发与生产身份分开；页面验收由用户承担。

## 架构复核：为什么旧设计失效

目录条目、实际部署、运行健康、来源可见性和业务轮次被混成同一事实。云端只探测DAQ，旧目录10项被当成已运行Agent，固定7月生命周期被当作发布现状。副本同步成功没有证明受限身份能够读到关联回复；VOC source reader可见66条输入却看不到outbox，仍将空关联发布为完整快照，消费者只数sent便得到0。HR现行schema与旧飞书记录又共用agent id。全局fleet与brief使用不同计数规则并重复解密全量历史，既缺准确性也浪费读取成本。

真实基线：平台代码08c16fd8/image55690e737f4b/API30853edd；同步worker代码c13433f4/image048e90d5bba6/容器8cf7efc0；主线88a37685。VOC源66入站、66 sent回执（60条关联输入），16会话/66轮进入副本却全部未计sent；角色platform_sync_voc_read缺SELECT RLS。HR真实控制库agent_platform_control/platform_control_app已读证：21thread、21work、39input、4result；HR API ready报告01eaed676cfd380b2650180c98c0678f018120f2。这些是时点证据，不写死为产品数字。原始调查保存在主工作区tmp/runtime-data-audit-20261030。

## 目标与明确边界

- 正式业务应用库存7项：FAE、DAQ、HR、VOC、行政5个Agent，加SDK、大制造2个工作台。应用数、Agent数和渠道数分别表达；不是对全公司未知服务作全量证明。
- 正式Agent的当前数据与旧渠道分开。HR当前数据仅来自platform_hr_agent；旧hr-bot/metabot/feishu单列历史来源；5个Marketing仍是已配置的MetaBot/飞书渠道Agent，不凭缺少实时监控宣布停用。渠道用量与独立服务用量分区，保留原权限。
- 会话、用户轮次、出现回复、已确认送达、业务成果各有单位与定义。不把工具调用/流式消息/失败尝试当用户输入，不把保存成果当用户确认或任务整体成功。无法证明完整回答/送达时为null或不适用。
- 每块暴露来源、范围、观测/同步时间、可见性完整性、新鲜度与原因。缺源/未观测用未知；只有完整成功读取的空集才为0。
- 本批不修改Agent Loop、模型/工具/记忆/成果执行协议，不调用真实模型、不生产测试消息、不扩大HR正文权限、不改Nginx/SSH/其他会话工作。

## 方案选择

单改标签会继续掩盖错误数据；直接建设跨域数据仓库扩大写入职责和迁移风险。本批采用专用看板读模型：复用真实只读源，按明确身份分类、一次聚合并缓存，新增统一HTTP合同，旧业务接口和审计保持。现有fleet/operations接口保留给原消费者，新首页不再串行调用旧fleet与brief，也不沿用未经覆盖的“整体正常”结论。

## 读模型与HTTP契约

新增GET /api/dashboard/overview?days=7|30，默认7；需要真实平台owner/admin身份，沿用中央身份/审计/只读限制，不给普通member或仅有局部观察权限的viewer全局聚合。响应private,no-store。路由不得绕过中央授权；认证成功仍须端点内检查管理角色。

返回DashboardOverview，schema_version=1，generated_at为带时区ISO，window含start/end/timezone=Asia/Shanghai/days，另有rolling_24h和active_window_minutes=15。所有时间窗UTC半开区间[start,end)，日趋势按上海自然日。一个快照共享同一now，不让各模块自己算窗口。

顶层inventory含applications/agent_services/business_workspaces/configured_agents/channel_agents/legacy_streams的数量；configured_agents为去重真实目录10（当前基线），其中5独立服务与5渠道配置，不能把独立服务5标成所有Agent总数；legacy_streams保留旧HR来源而不重复Agent。summary含可观测健康/异常/未知、数据源覆盖和当前Agent使用指标，标明完整/部分。applications始终含7项，按kind=agent|workspace分组；channel_agents和legacy_streams独立，不加入“独立服务用量”总数，渠道Agent的已配置事实不因没有Web入口而消失。trend与last_24h来自同一聚合，禁止混用旧brief口径。

每个应用含稳定id、中文name、kind、正式entry_path、runtime、source、usage、business_metrics、deployment；链接保留原业务入口与管理深链权限，不提供旧MetaBot Web使用入口。SDK/制造的会话指标为not_applicable；业务指标不加入统一用户轮次。

Metric精确定义：value为非负整数或null，status=observed|partial|unavailable|not_applicable，unit=session|turn|delivery|work|result|document|project|task|application|agent，definition为稳定中文可读说明，reason可空。Usage含retained和window下的sessions/turns/answered_turns/deliveries、last_activity_at及原始outcome_counts。answered_turns只代表出现回复记录，不代表完整业务完成；无可靠完整送达证据的来源deliveries=null。

SourceEvidence含source_id/source_kind/channel、status=fresh|stale|unavailable|not_applicable、observed_at/published_at/checked_at、complete、error稳定码。RuntimeEvidence含coverage=observed|not_configured|not_observed、state=healthy|degraded|offline|unknown、observed_at、stale、method、error。每应用独立时间，DAQ更新不能刷新其他对象；同步成功不是服务健康。Deployment含version/observed_at/evidence；只采用实际ready/已验证发布证据，不把目录yaml日期或最早会话冒充上线与更新时间。没有证据明确未知。

## 来源接入与职责

### VOC真实权限与关联合同

修正平台provision-agent-sync.py所拥有的专用consumer角色，在同一事务幂等协调outbox FOR SELECT TO platform_sync_voc_read USING(source_inbound_message_id IS NOT NULL)，只保留既有4列授权。禁止BYPASSRLS、关RLS、public授权、读取recipient标识或改员工业务策略。不需要发布VOC业务服务。

VOC使用专用READ COMMITTED只读事务，先以已授予列的零行读取持有全部ACCESS SHARE，再用新鲜目录核对角色/列权限、适用RLS策略、关联UNIQUE/FK；单条MATERIALIZED SQL同时测量count/bytes并读取同一MVCC业务快照；其他源保留原RR合同。有阻止完整可见性的restrictive策略或缺策略必须失败并保留旧副本/记录source failure，不能发布空回复当正常。未关联回执不伪造对话；delivery pending/failed和job done等原始状态如实保留。暂未实现全平台数据仓，不能写造成功数据。

### 正式使用、历史渠道与增量读取

副本四个direct源匹配精确(agent_id,source_kind,channel)，按原始turn记录计输入轮次、按session计来源会话，回复与送达分别计。历史HR与Marketing分区。旧副本条目即使没有source_states也不能默认为实时；完整性不明返回partial/stale。

新增有界增量数字聚合：先读取session_key/payload_sha256/agent_id/source_kind/channel/时间等轻量元数据，只对新增或hash变化的行取密文并解密一次，立即转为无正文的数值摘要，不缓存员工正文。删除的session在确认完整快照后移除；缓存有明确上限（默认10000项）与single-flight，禁止每个请求/摘要重复解密历史。超过上限明确unavailable/limit错误，不静默截断后声称完整。共享快照TTL30秒，单源失败独立，保留上次成功值须标stale和来源时点，无隐藏立即重试。

### 现行HR

平台已持有同一control app DSN；新固定SQL reader强制repeatable-read/read-only/5秒statement timeout，只读threads/works/inputs/entries/result元数据，不读sealed_*、objects或密钥。用户轮次以inputs唯一键计；多assistant流片按(work_id,input_revision)去重，question另明确归类；state/answer_state/results分别给业务指标。完整答案判定没有元数据证据时不伪造。不要把当前work状态覆盖历史每轮结果。旧HR飞书369留历史，当前HR不回落旧链；Marketing渠道为真实已配置Agent，单独展示可追溯用量与观测状态。

### 七应用健康、版本与业务指标

为正式服务配置显式可信健康端点，优先复用现有safe health客户端，HTTP仅固定路径、禁止重定向/凭证外泄、短超时、无业务调用。每应用独立探测、缓存与失败；HR ready采用真实release_sha。缺配置显示未观测，不能用本机localhost猜容器网络。主控发布前核对真实可达地址；不恢复云端SSH/MetaBot poller。

真实网络核验补充：平台容器可达FAE edge8000、DAQ配置health、VOC配置health；FAE/HR/行政公开health为404，HR/行政仅监听127.0.0.1:8012/8011。因此不能靠公开探针、host-gateway或开放监听来补健康。

新增独立宿主回环观测源：标准库collector仅探测固定HR ready和行政health，不执行Agent/SSH/Docker，也不读业务库或秘密。systemd oneshot+30秒timer，DynamicUser与专用StateDirectory，单次限时、无隐藏重试；输出schema_version1、两应用逐条checked_at/state/稳定error/release_sha(仅ready真实报告)，不存健康响应正文或身份字段。原子写入/var/lib/orbbec-platform-runtime/snapshot.json，应用通过专用只读bind目录/run/platform-runtime消费，90秒TTL，未来时间/损坏/缺失逐对象unknown。URL不可来自网页请求，采集器不能访问非回环地址。失败写当次失败观测，不沿用旧healthy假正常。

本源作为独立交付Task3并经真实本地HTTP/文件/过期测试；不会给API Docker socket、宿主SSH凭据或写观测目录的权限。新增只读挂载属于明确发布配置增量，Task5必须对完整原策略做精确白名单比较：仅允许这一条bind新增，所有原挂载/环境/用户/能力/网络/其他服务不变；回滚恢复原完整输入链和原HostConfig。不能用放松通用parity/hash检查来发布。新systemd单元为本任务自有，行政/HR既有单元不改；发布前先核对collector真实输出与目录权限。

SDK复用已校验的SdkAdminRepository catalog与manifest，显示知识文档量/发布证据、说明文档工作台不是聊天服务；不逐页拉禅道、不触发SSO provision。大制造沿用既有access与workflow/dashboard读合同，保留真实账号授权、project_ids和dataset(演示/测试/正式)标记；不得内部伪造owner、跨用户全局缓存项目数据或把就绪当生产验收。能读取的现有业务指标以原单位展示；不适用/缺源/无权限分别表达，不能发明字段或将这两项删出库存。

## Agent目录：最新用户补充（2026-10-30）

用户明确要求目录展示MetaBot+飞书Agent，以反映已开发/配置数量；覆盖之前只显示Web条目的页面裁定，但没有恢复平台内使用入口。目录展示完整且经过授权的注册元数据，独立Web与MetaBot/飞书分组、分项计数；注册/可网页使用/渠道配置与运行监控覆盖分开。HR以现行Web投影优先，不因保留的旧MetaBot适配字段被错误分回旧执行链，同一agent_id不重复计数。

管理者(owner/admin)需要完整目录元数据视图；若现有permitted_catalog仅提供可使用条目，则新增管理目录GET接口，返回非秘密目录元数据及按原use授权计算的can_use，独立Web链接仍需真实use授权。普通成员沿用原按use授权过滤的目录，禁止公开管理库存或越权使用。WebCatalogProvider保留full catalog与filtered applications两个字段，sidebar/panorama仍只消费已授权正式Web入口；MetaBot条目为说明卡，无旧Brain/聊天启动链接。列表数量来自实际去重元数据，不能为显得多而写死或重复计数。

## 页面

运行中心保留现有路由，顶部为正式应用/Agent数量、实际健康覆盖、当前用户轮次及数据覆盖；提供7/30天切换。近24小时活动、趋势和排名采用同一窗口定义；排名只基于当前Agent，缺数据不参加排序为0。应用区域区分Agent与业务工作台，卡片展示健康/数据状态、源时间、会话/输入/回复/送达单位、业务成果与实际版本证据。MetaBot/飞书渠道Agent单独分区，旧HR来源另标历史；明确两者不计独立服务使用总数，不把已配置渠道误标停用。数据故障在本源卡片明确呈现，移除未经证据的“暂无严重问题/所有检查正常”。不展示HR原始输入/个人材料。保留现有工程笔记、账号权限、安全记录及会话详情入口。

## Task1独立审查发现的快照边界（实施前纠正）

77939d1b的首次实现214工程测试通过，但独立审查的真实PG交错发现：RR快照在identity查询建立，表锁稍后取得，期间policy DDL可提交；pg_policy旧快照校验与RLS当前syscache应用不同策略，可错误发布complete空关联。原after-proof锁测试未覆盖这一窗口，证据保留，不能把Task1标完成。修复必须使DDL边界与权限证据/实际数据查询一致；可采用在业务快照之前稳定锁边界的能力设计，或VOC单一有界SQL业务快照配合锁后新鲜权限证明，保留独立数据一致性，不要求为了沿用RR实现而扩大列权限。不能靠重试/吞错/伪造回执通过。需要确切复现该前置交错及同次读取中源数据变化的回归，源错误仍先于发布，其他源读契约不退化。

## 验收与成本

接口优先：最小红测→临时真实PG受限角色→源查询/投影/持久化/看板HTTP合同→组件与生产构建→必要故障/缓存验证。真实模型0。VOC必须真实RLS/真实专用角色，不用管理DSN冒充reader；确认员工隔离和只读禁止写仍成立。HR临时真实控制表验证聚合与权限，不伪造完整业务成功，源端完整任务质量本次未改变。

缓存验收证明重复快照不重新解密、变化仅重算对应session、删除生效、上限显式拒绝、并发single-flight、单源失败不把其他源清零；报告耗时和读取/解密次数。HTTP验证匿名/member/viewer/管理角色、撤权、no-store、无正文/凭证泄漏。浏览器由用户；生产只读核对与开发替身清楚分开，首次失败保留，不重试到通过。

本批发布仅从审查后已归并主线的准确提交构建，保留实际线上基底和所有其他会话层。涉及VOC角色策略的受控DDL单独记录，先备份目录权限/策略定义与检查，不改业务数据。最终报告必须分别给工程、接口、真实PG、前端、生产及用户验收通过/失败/未执行数量和仍未解决项。

### 最新目录裁定下的命名校正

“渠道Agent”与“历史来源”不能混同。Marketing五项是现有配置，用户要展示开发/配置成果；没有Web按钮不代表停用。只有旧HR飞书统计明确是现行独立HR之前/并行的旧链来源。后端应使用channels与legacy_streams分别表达；页面可以折叠次要区域，但不能省略真实配置数量。

### 宿主观测文件跨任务准确合同

Task2在runtime_dashboard/health.py实现HostObservationReader，Task3实现collector并复用该reader做端到端测试，避免双实现。文件JSON为{schema_version:1,generated_at:ISO8601,observations:[{application_id:"hr-bot"|"ai-admin-agent",state:"healthy"|"degraded"|"offline"|"unknown",observed_at:ISO8601,error:null|稳定码,version:null|40位hex}]}。仅这两项ID，重复ID/非法结构拒绝，单条过期/未来时间不能污染其他条。App只读/run/platform-runtime/snapshot.json，TTL90秒，源码不写健康文件。collector固定两回环目标，不接收用户URL。

Task1修正落地fdace0ed：前置竞态红测、单语句内真实并发写、锁后DDL/大小/超时及失败保旧回归220通过（53真实PG），独立复审C0/I0/M0。原214测试通过但I1阻断的记录保留，不把旧版本改记成功；生产验证/DDL尚未执行。

## Task2独立审查的完整性与资源边界复核

2fb44d6c本地56项通过后，独立真实PG/隔离进程诊断发现两项Important，未部署：I1热缓存仅按session_key+payload_sha256复用，明文channel/agent/family/时间等身份元数据改变但hash不变时绕过原decryptor一致性证明；冷读拒绝、热读却fresh complete。I2宿主观测path.open可在FIFO坏源上无界阻塞，热/冷分支都会持全局health锁等待读取。原复现和失败不能改记成功。

共同原因是缓存的表示依赖和文件消费的执行边界没有完整继承源契约。修复须将原decryptor用于比对的明文身份/时间元数据指纹也纳入复用判定，变化则仅重读对应行并使用既有验证，不能全量重解密或扩大缓存/时限；错误来源保旧数字并标stale，不将损坏元数据改写为正常。宿主reader要在打开和读取之前证明是有界regular file（nonblocking/nofollow+fstat等），缺失/不安全类型/符号链接返回逐源unknown，描述符正确关闭，不可卡住共用锁；不能仅用外层to_thread声称IO有超时。精确PG暖/冷对照、未变/变化解密次数和隔离FIFO/普通/损坏/符号链接/过期/描述符与并发读取用例必须覆盖；其他域授权和API合同保持。
