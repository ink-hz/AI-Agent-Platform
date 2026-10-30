# 跨仓安全架构初步盘点

日期：2026-10-23。用途：为[整体安全设计](../superpowers/specs/2026-10-23-platform-security-architecture-design.md)提供事实依据。
这是限定范围的架构盘点，不是完整安全审计、漏洞利用报告或生产安全认证。

**2026-10-29 校正**：下文保留10-23工作树和运行观察的历史口径。第二批重新逐字核对生产后，ADMIN 的 routes/session/orchestrator/data_flywheel/postgres_data_flywheel 已是 `048d0c89a92fae1ec56623b134bedb883f8549a9` 主体绑定实现，server 含后续 `095a0da` 改动；旧 master/current checkout 尚未包含该安全修复，不能把旧工作树缺口当作当前线上缺口。首批备份、PHP、续期也已有新结果。最新版本和验证范围见[首批执行](../operations/2026-10-29-security-first-batch-results.md)及[第二批执行](../operations/2026-10-29-security-second-batch.md)，其余旧观察不自动更新为当前事实。

## 1. 方法和版本口径

2026-10-30的DAQ非root生产切换及当前活动数据/密钥目录见[最新发布记录](../operations/2026-10-30-daq-security-release.md)。以下root运行观察保留历史口径；数据库最小权限及其他未验收控制仍独立跟踪。

- 平台起始工作基线 `0f636359`，其父版本为本次问题调查时的 `d0956a3a`；远端主线核对为 `d0956a3a`。
- 子仓只读检查各自当前工作树；有未提交改动的文件只能作为工作树证据，不能直接归为已提交或已部署实现。
- 生产只读核对监听进程的 UID/可执行路径、Docker 运行身份、挂载和网络成员；不打印环境变量、凭据或业务正文。
- 原 FAE 问题保留独立验收，不重新实施修复。其他工作线继续开发，下面的 HEAD 和工作树状态均为本轮观察时点。
- 没有运行跨仓安全测试、浏览器、真实模型或故障注入，没有修改业务代码或生产配置。

| 对象 | 本地基线 | 发布证据和限制 |
| --- | --- | --- |
| AI-Agent-Platform | `master@0f636359` | 实际 API 镜像标签 `a3cac583`；其他常驻组件镜像不能由该标签推定 |
| AI-ADMIN-Agent | `feat/admin-invitations-20260911@55c540e`，159 项已有工作树变化 | 当前生产代码版本未核实；旧交接记录不能代表本轮实际版本 |
| AI-HR-Agent | `master@13848cf`，4 项已有文档变化 | 10-19 发布记录为后端 `01eaed6`、前端 `f845270`、工具修订 42；本轮未重验代码哈希 |
| AI-DAQ-FAE-Agent | `master@9331d7d6`，调查期间有既有工作流变化和 review 文件 | 发布记录及已观察镜像指向 `ab1fa4b`，不能将本地 HEAD 当线上 |
| Orbbec-VOC-Agent | `master@0185869`，干净、未配置 remote | 10-23 发布记录 `ad30f9f6`，与已观察生产镜像标签/镜像 ID 一致；非全部模块哈希证明 |
| AI-Agent-Loop | `business-pruning@f0a8574`，干净 | 通用运行时独立开发仓；未证明是当前所有业务服务的共同生产依赖 |
| AI-SDK-Platform | 顶层为项目文档与来源目录，不是独立 Git 根 | 平台接入代码、部署配置在平台仓；SDK 源码另有子仓，不把目录名视为独立发布服务 |
| AI-FAE-Agent | 前轮观察 `master@a834ebd`，存在其他工作线改动 | 前轮实际制品标识 `978f851c`；后续只按 SEC-FAE-01 接收修复版本与证据 |

同级 `AI-HR-Agent-same-host`、`AI-HR-Agent-stopped-experiment` 和 FAE archive 等目录不因存在而自动成为新的实施入口。
Scanner、其他本地专业 Agent 和第三方服务仍需在首批全量资产登记时确认业务归属；当前没有完成其逐项代码审计。

## 2. 实际基础设施观察

| 编号 | 已观察事实 | 能得出的结论与限制 |
| --- | --- | --- |
| INF-01 | ADMIN API `127.0.0.1:8011`、HR API `127.0.0.1:8012` 的监听进程 UID 都为 root | 回环监听限制了直接网络入口，但不能证明文件、凭据及跨服务进程权限已隔离 |
| INF-02 | FAE 容器进程 UID 为 0；DAQ 配置用户为 root；两者根文件系统可写，未见平台容器同等的 cap_drop/no-new-privileges 配置 | 容器加固不统一；不等于模型拥有 Shell，也不等于已经获得宿主 root |
| INF-03 | 平台 API 和部分常驻容器为 10001，根文件系统只读、cap_drop=ALL、no-new-privileges；VOC 常驻服务同样有加固 | 已有可复用基线；仍需逐服务核对可写挂载、数据库权限、网络和实际子进程 |
| INF-04 | `orbbec-agent-platform-edge` 同时含 FAE、DAQ、平台 API、Brain 等；`deploy_default` 含 FAE、DAQ 和数据库组件；FAE 与观测数据库同网 | 网络成员共享信任面；尚未做逐路径连接/授权测试，不能宣称已经验证横向利用 |
| INF-05 | 主机 SSH 默认有效配置允许 root 和密码认证；22 监听全地址；UFW inactive，INPUT 默认 ACCEPT，DOCKER-USER 无额外规则 | 需要核对 Match 条件、云安全组和实际来源限制；未核验云安全组，不能单凭此称任意来源都可登录 |
| INF-06 | 同机另有非本项目 demo 在全地址监听 18080/18081，进程为 root；MetaBot 进程在回环 9100/9200，以 root 运行 | 共享主机风险不止业务仓；登记维护归属和需要的访问路径，不自动停服或修改无关项目 |
| INF-07 | SDK 禅道宿主映射为回环 18131；数据库不发布宿主端口；禅道和数据库根文件系统可写，未设置平台同等加固 | 保留第三方程序必要写目录和启动要求，需验证应用进程用户；不能仅按镜像 User 空值判定所有子进程为 root |

这些事实来自本会话的只读 SSH /proc、ss 和 docker inspect/network inspect，不包含配置变更或连接攻击测试。
共享 Nginx 配置只作文件读取，未执行独立配置测试或重载。

## 3. 各仓边界与关键问题

### 平台公共底座

- 已有路由鉴权、CSRF、来源解析与部分强制审计失败关闭：`backend/app/control_plane/middleware.py:454`、`:593`、`:606`、`:631`。
- 控制库按 app/audit/migrator/maintenance/directory/stream/brain 校验准确角色：`backend/app/control_plane/dsn.py:13`、`:32`。这不代表每个业务仓或生产连接都已独立分权。
- Brain 工具注册为委派、观察、追问和交付等有限集合：`backend/app/agent_brain/tool_protocol.py:326`。模型工具集合与服务进程自身可访问资源分别验收。
- 附件有 owner/任务授权、票据、撤销及留存实现：`backend/app/attachments/grant_service.py`、`download_service.py`、`retention.py`；生产清理调度和跨服务消费者仍需验证。
- 扫描器存在 ClamAV 和仅完整性检查的 TrustedInternal 两种实现：`backend/app/attachments/scanner.py:39`、`:101`，选择在 `worker_runtime.py:525`。本轮未核实实际装载模式，不能把所有 ready 附件都称已通过恶意软件扫描。
- 备份和恢复已有脚本：`deploy/cloud/backup.sh`、`restore-drill.sh`；本轮未核实全部业务库/对象、异地副本、恢复身份及最新恢复结果。
- 本地 AgentOps 手册允许 neo 以 agentops 任意执行，而非命令白名单：`docs/runbooks/agentops-controlled-executor.md:3`、`:49`。这是运维关系，不能推导为业务 Agent 权限；实际消费者、凭据与任务入口需单独登记。

### ADMIN

- 平台身份客户端固定账户接口，关闭环境代理和重定向；附件已有 owner、大小、类型和路径限制：`../AI-ADMIN-Agent/src/service_portal/platform_identity.py:74`、`src/service_portal/attachments.py:80`。
- **当前工作树已证实**：聊天入口认证后丢弃主体，chat 按客户端 session_id 获取会话；Session 没有 owner，反馈未绑定当前主体。构成需要修复的对象授权缺口，但本轮未验证生产利用：`src/api/server.py:1221`、`src/api/routes.py:268`、`src/admin_agent/session.py:14`。
- 邀请函普通管理权限使用 display_name 名单；平台 admin/owner 与行政业务管理权限的文档和实现存在差异：`src/api/invitation_routes.py:40`、`src/service_portal/office_admin.py:8`、`docs/runbooks/ai-admin-domain-management-access.md:5`。
- 会话 TTL 不等于飞轮问答和 JSONL 的删除策略；生产保存周期、访问和备份未知：`src/storage/data_flywheel.py:60`、`:276`。

### HR

- 每次请求通过平台 account HTTP 验证，写请求检查 Origin/Host/CSRF；客户端限制回环并禁代理/重定向：`../AI-HR-Agent/backend/app/integrations/platform/guard.py:22`、`session.py:30`。
- 材料和工具已有 owner、准确引用、冻结依赖及提交约束；网页读取已有公网地址检查、逐跳校验和连接绑定；原生文件进程存在拒绝缺失沙箱的实现：`backend/app/hr_agent/material_authority.py:79`、`web_provider.py:185`、`document_process.py:29`。这些是源码证据，本轮未运行其真实链路。
- API/Worker 同数据库角色是现行明确限制，AGENTS 禁止自动恢复旧 mTLS/签名代理/角色迁移/shared SDK：`AGENTS.md:5`、`deploy/README.md:70`。新数据库隔离仅能另提兼容迁移方案。
- 公网地址校验不等于允许敏感内容发送给搜索服务或任意公网接收方；query 与 URL 内容的出域策略需要补齐：`backend/app/hr_agent/web_provider.py:374`、`:398`。
- 个人材料有发送前授权检查；默认真实处理授权未接通，不能宣称已允许真实候选材料外发：`backend/app/hr_agent/personal_processing.py:31`、`resources.py:571`。
- 模型端点传输和供应方政策、业务保留期与诊断 cleanup 的生产接线待核实：`backend/app/hr_agent/config.py:234`、`:339`、`diagnostics.py:95`。现行工具不能按早期“五工具”历史文档推定。

### DAQ

- 企业身份、独立 Cookie、知识角色可见性、流式重放/工具前后权限检查及附件主体绑定已有实现：`../AI-DAQ-FAE-Agent/daq_fae/platform_identity.py:75`、`authenticated_chat.py:87`、`knowledge/reviewed_view.py:484`。
- 运行时锁定 `6bad9459`，但受保护持久引用仍需核验；不能直接改上游或自动跟随 FAE 最新版本：`upstream-source.json:3`、`scripts/verify_upstream_snapshot.py:40`。
- Docker 非 root 和其他加固不足；与相机共享逻辑数据库、模型凭据是现行记录，search_path 不证明最小授权：`deploy/Dockerfile.prod:28`、`deploy/compose.prod.yml:9`、`AI_DAQ_FAE_Agent_工作流设计.md:30`。
- 企业身份有 60 秒缓存；需要定义并测量停权后历史、流式重放和工具执行的最大生效时间，不将知识逐帧复核说成所有身份都即时撤销：`daq_fae/platform_identity.py:142`。

### VOC

- 独立 /voc 和机器人是当前方向，旧 README/运维入口已漂移；发布文档与本地 HEAD 分开处理：`../Orbbec-VOC-Agent/docs/superpowers/specs/2026-09-01-voc-standalone-web-and-dingtalk-bot-design.md:18`、`README.md:17`。
- 私网身份投影带服务 Bearer、写请求校验 Origin/Host/CSRF，私有 API 检查短时令牌，数据库按主体 RLS：`src/orbbec_voc/workspace/platform_subject.py:59`、`browser_auth.py:17`、`identity.py:112`、`migrations/013_platform_workspace.sql:121`。
- 同一 voc_platform 角色可执行管理读取的 SECURITY DEFINER 函数，管理权限隔离依赖应用能力校验：`migrations/017_platform_management_nullable_content.sql:221`、`src/orbbec_voc/workspace/service.py:309`。
- 固定 Bearer 轮换、撤销、短令牌允许的复用和写入幂等策略需要统一说明。jti 没有一次性消费本身不等于漏洞，也不自动要求更换全部协议：`src/orbbec_voc/workspace/identity.py:140`、`platform_subject.py:149`。

### Agent Loop 与 SDK

- Agent Loop 默认可无数据库、身份或凭据启动，按通用工具与附件运行；这是本地默认配置，不是生产开放准入证明：`../AI-Agent-Loop/docs/GENERIC_AGENT_ARCHITECTURE.md`、`AGENTS.md`。后续能力演进遵守同一工具与数据契约，先明确生产依赖归属。
- SDK 规划、源码、连接器和禅道不是一个 Git 根；不能直接把主目录当运行时仓。三类来源与 A/B 工作边界见 `../AI-SDK-Platform/AGENTS.md`。
- SDK 接入代码与 SSO/Nginx 由平台仓承担，禅道运行约束见 `deploy/sdk/compose.yaml:39`。CI/源码处理、发布凭据、第三方程序漏洞和原生权限需要分别登记；不扩张为重写 SDK 项目管理或重复导入业务数据。

## 4. 本轮结论

优先考虑宿主运行身份、跨服务网络和凭据边界、ADMIN 对象授权及公共内部接口，而不是用新的统一 SDK 或更复杂协议掩盖边界问题。
现有应用级校验和加密应保留；所有候选问题都先绑定准确消费者与发布版本，再确定改动。
FAE 修复不占本专项开发范围；其验收不能代替其他服务或整个平台的验收。
