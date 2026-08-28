## AI 工程笔记八篇迁移：源稿研究台账

隔离 worktree：`/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/ai-notes-eight-article-batch`

本台账仅记录迁移前的源稿清单与研究状态。源稿位于只读目录
`/Users/neo/Developer/personal/starship-blog-source/src/content/blog/`；行数与
SHA-256 已于 2026-08-28 使用 `wc -l` 和 `shasum -a 256` 复核。

| 源文件（绝对路径） | 行数 | SHA-256 | 目标文章 | 完整阅读 | 保留 | 删除 | 事实核验 | 去重 |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-深度理论知识.md` | 2625 | `9df1b094bf6064974314e93985e65332803c500061a495f8fffc9f0408662ea5` | agent-identity-access-control | 已精读：1-2625 | 第1-5章：主体、令牌、授权、用户委托 | 第3、6-7章：网关样例、旧日期、SSO与厂商清单 | 已核验：RFC 8693、NIST、SPIFFE、OWASP（2026-08-28） | 全景与信任层留在主文章；本篇深化委托、授权、审计 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-理论架构设计.md` | 2140 | `c693c2e5ca30a47019191e001f0a8708beaed585dc03e7cdf67413e253efac01` | agent-identity-access-control | 已精读：1-2140 | 第1、4-5、7.3章：身份、权限交集、最小权限、零信任 | 第2-3、6、7.1-7.2及7.4-7.5章：登录网关、旧组织案例、产品横评 | 已核验：RFC 8693、NIST、SPIFFE、OWASP（2026-08-28） | 状态机与信任分级留在主文章；本篇深化凭证与证据 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构深度指南.md` | 2483 | `f21f1316a7c66c5a5c920efe8c0f352dc2532af15d82265d35b58dfbab7dc784` | llm-inference-serving-engineering；辅助 llm-agent-observability | 已精读：1-2483 | 第2、4章：KV Cache、连续批处理、投机解码、路由问题框架 | 第1、3、4.3章及总结：Agent、RAG、MCP、旧 API 样例与固定性能数字 | 已核验：PagedAttention、vLLM、TensorRT-LLM、SGLang（2026-08-28） | 应用请求链与 RAG 留在既有文章；本篇只写模型服务内部数据路径 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/AI-LLM系统架构理论指南.md` | 1550 | `c897f77ba9511c48358164c2c50b8f144703c983c0aa1dcedde9b20a6b145d8f` | llm-inference-serving-engineering；辅助 llm-agent-observability | 已精读：1-1550 | 第2、4.1-4.2章：Prefill/Decode、分页缓存、批调度、容量与路由取舍 | 第1、3、4.3章及总结：Agent、RAG、MCP、厂商层级、固定价格与倍率 | 已核验：PagedAttention、vLLM、TensorRT-LLM、SGLang（2026-08-28） | 不重述 Prompt/工具/输出验证与检索链；重写排队、缓存、路由与单位成本 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/ai-cloud-native-opportunity.md` | 124 | `9c04bf78390c08bbe3c89e685bf4f407434c6f96c0ec63828662db75239c0a07` | ai-cloud-native-runtime | 已精读：1-124 | 问题与原则：异构资源、弹性、发布、容错 | 架构样例、厂商选型、固定倍率与 HPA 万能化表述 | 已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28） | 推理算法留在既有篇；本篇只写运行时资源与运维闭环 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排深度指南.md` | 250 | `4908263dc8ebdbe69106f50b8aa8f2b5030dd0a7e9fc5827945784e02dd31df4` | 辅助 ai-cloud-native-runtime | 已精读：1-250（通用概念辅助） | 调度、隔离、声明式发布、故障恢复概念 | 安装命令、对象清单、旧版本行为与厂商 GPU 功能 | 已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28） | 不迁移 Kubernetes 百科；只辅助 AI 运行时边界 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排理论指南.md` | 1705 | `12361a569b863b0de58f43366420c822f5b8568b42f243c38aab16aa0eef53ff` | 辅助 ai-cloud-native-runtime | 已精读：1-1705（通用概念辅助） | 一致性、队列调度、隔离、渐进发布与恢复概念 | 组件百科、Helm/GitOps 教程、厂商设备与固定性能数字 | 已核验：Kubernetes、Kueue、KServe、Device Plugin（2026-08-28） | 不复述通用编排；重写为制品、调度和恢复链 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/可观测性与监控-深度理论知识.md` | 1857 | `546ea435a32227f0ec75e73e946e45e4cb5a6e092ee55deaf7d456de3c43b8ed` | llm-agent-observability | 已精读：1-1857 | 上下文传播、结构化 trace、采样、高基数、SLO 与信号关联 | 通用三支柱教材、产品栈与配置、固定阈值、采样率和成本数字 | 已核验：OpenTelemetry GenAI、W3C Trace Context、NIST（2026-08-28） | RAG 算法与 Agent 状态机留在主文章；本篇只记录证据与质量信号 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Hermes-Agent架构分析与思考.md` | 390 | `aac8a4c575a11ae1a129c3e03d49e6217d2c02a92363b0689d5c5306c70227ad` | open-source-agent-runtime | 已精读：1-390 | Agent loop、context/session、skills/tools、memory、sandbox 与 recovery 问题框架 | 动态版本、数量、排行、营销结论与无法复核的生产效果 | 已核验：Hermes 与 OpenClaw 官方仓库快照（2026-08-28） | Claude Code 使用体验留在既有文章；本篇只比较运行时责任边界 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Clawdbot架构理论指南.md` | 284 | `838ae6a89bfeca305bb70bc016f975d7b12f34abef9fe73dd4638c22dedb6961` | open-source-agent-runtime | 已精读：1-284 | Gateway、channel routing、session、tools/skills、sandbox 与 recovery 问题框架 | 旧项目名当现名、动态渠道数、成熟度结论与安全泛化承诺 | 已核验：Hermes 与 OpenClaw 官方仓库快照（2026-08-28） | 企业 Agent 全景留在主文章；本篇抽象 provider/model/runtime/channel 和 ownership |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/MetaBot架构设计理论分析.md` | 515 | `f526d770501328c0aa12bb926ae379c640bebb3cd9540fe4b569a581caebded5` | metabot-agent-control-bus | 已精读：1-515 | 远程控制、渠道适配、消息桥、持久执行与恢复问题框架 | 旧组织、旧拓扑、代码规模、固定数量、端口、版本与营销结论 | 已核验：两个当前代码仓与 Platform relay 边界（2026-08-28） | 通用运行循环留在既有篇；本篇只写远程控制、可靠投递与状态所有权 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/主流Agent框架深度分析-从架构本质到生产可用性.md` | 323 | `4edd175b19b9ac82be0ac9a92ed10d69ddfe14cac7611fa7b3df9f0a5866054b` | agent-framework-selection | 已精读：1-323 | 产品形态、生产责任维度与选型问题框架 | 旧候选集、功能表、宣传语、成熟度判断与永久排名 | 已逐页核验：8 个官方入口（2026-08-28） | 三篇同类文章留下具体架构；本篇只给出选型方法与退出条件 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/干掉用户旅程-意图驱动的业务平台架构设计.md` | 379 | `3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b` | intent-driven-ai-business-platform | 已精读：1-379 | 第1-4、10章：固定旅程边界、能力原子化、受控编排与渐进迁移问题框架 | 标题与第2-9章：唯一对话入口、UI消失、完全自治、自演进取代发布及营销结论 | 已核验：Anthropic、NIST、OpenAI PDF 与 Agents 文档（2026-08-28） | 信任全景留在企业 Agent；AI 协作方法留在 AI Native；本篇只写受控意图执行 |

## 精读证据格式

后续精读时，每条保留的事实或观点按以下格式记录，不将来源中的品牌、宣传或未经核验的主张直接迁入草稿：

```text
- 目标文章：<slug>
  源文件：<绝对路径>
  定位：<标题 / 行范围>
  事实或观点：<简要转述>
  保留决策：<保留 / 删除>
  事实核验：<待核验 / 已核验，依据>
  去重关系：<与何处重合，处理方式>
```

## agent-identity-access-control 精读结论

### `身份认证与访问控制-深度理论知识.md`

- `1-120`：身份、认证、授权的概念边界只作背景；传统用户登录、MFA、LDAP、SAML 和 SSO 不进入 Agent 专题。
- `121-760`：JWT、Opaque Token、网关认证和示例代码不迁移；只保留“凭证有受众、范围、期限与撤销窗口”的设计问题，具体协议事实重新向一手资料核验。
- `761-856`：保留用户委托、Agent 独立身份和 Token Exchange 的问题框架；不沿用旧稿自定义 claim 作为标准要求，也不复制示例密钥或固定 TTL。
- `857-1480`：大段网关中间件、配置、curl 和故障处理示例删除；新文改为资源、动作、条件、风险和审批绑定的授权输入。
- `1481-2400`：云厂商 IAM、IDaaS、开源产品功能清单及方案横评全部删除，避免把会变化的产品能力当作通用架构。
- `2401-2625`：产品价格、用户规模、公司阶段、多云厂商映射和旧日期案例删除；只保留工作负载不共享长期静态凭证这一原则，并以 SPIFFE 官方说明核验。

### `身份认证与访问控制-理论架构设计.md`

- `1-400`：保留“身份—认证—授权—审计”四问作为阅读框架；令牌类型百科、算法对比和固定生命周期不迁移。
- `401-800`：单层网关、BFF、分层网关、Header 透传等传统入口架构删除，它们不回答 Agent 代表谁和为何获准行动。
- `801-1200`：RBAC、ABAC、ReBAC 教程与产品模型删除；保留主体、资源、动作、环境条件共同形成授权决策的抽象。
- `1201-1450`：保留 Agent 自身身份、用户委托身份及二者权限取交集的核心；将旧稿把用户 Token 直接交给 Agent 的流程改为受控交换和短时凭证。
- `1451-1820`：云厂商、IDaaS、开源 IAM 的功能、价格、应用数量与选型树全部删除。
- `1821-2140`：保留零信任的资源中心视角、最小权限和审计原则；通用 Token 生命周期图、微服务身份透传和公司规模选型表删除。
- 两份源稿中的 `2024-12-31` 条件、固定价格、员工/用户规模、特定组织阶段和厂商生态判断均视为旧案例，不进入正文。

### 与已发布主文章的去重

`enterprise-agent-system-architecture` 已经承担 Agent 全景分层、运行循环、持久化任务状态机、四级信任模型、Hook、子 Agent、观测评估和存量系统接入。本篇不重画这些全景，只回答三条窄链路：用户如何把有限权利委托给 Agent、Agent 运行实例如何用工作负载身份取得短时凭证、每个行动如何由资源级授权与审批生成审计证据。

### 一手来源核验

访问日期：2026-08-28

| 一手来源 | 本文采用的受支持论点 |
| --- | --- |
| [RFC 8693 OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693) | Token Exchange 区分主体与行动者并表达委托链；`subject_token`、可选 `actor_token`、目标资源与 scope 为交换请求提供结构，但具体信任模型仍由部署策略决定。 |
| [NIST SP 800-207 Zero Trust Architecture](https://csrc.nist.gov/pubs/sp/800/207/final) | 零信任不因网络位置或资产归属授予隐式信任；保护重点是资源，主体与设备需在建立资源会话前分别完成认证和授权。 |
| [SPIFFE Overview](https://spiffe.io/docs/latest/spiffe-about/overview/) | 工作负载可取得短时密码学身份文档并相互认证；SVID 可用于建立 TLS 或签名、验证 JWT，Workload API 支持自动轮换。 |
| [OWASP LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) | 过度功能、权限与自主性需要最小化能力和人工审批；下游系统应在用户上下文中实施最小权限并逐请求执行授权。 |

## llm-inference-serving-engineering 精读结论

### `AI-LLM系统架构深度指南.md`

- `1-800`：Agent、ReAct、Plan-and-Execute 与 Multi-Agent 的说明、代码和示例全部删除；它们属于应用控制层，不是模型服务内部。
- `801-1200`：保留 KV Cache 动态增长、分页管理、连续批处理、前缀复用的问题框架；代码、CLI 参数、模型名、GPU 型号、内存利用率、吞吐和延迟数字删除。
- `1201-1600`：保留“提议多 token、目标模型验证”的投机解码思路；固定毫秒、加速比和简化采样代码删除。RAG 架构与实现不迁移。
- `1601-2000`：检索、重排、分块和 Self-RAG 全部与既有检索专题重复；只从模型服务角度重写路由需要的 SLO、队列、缓存与成本信号。
- `2001-2400`：厂商模型名、API 价格、RPM、实例数、任务关键词分类器和降级代码删除；MCP 实现不属于本篇。
- `2401-2483`：MCP client 与旧总结删除。总结中所有固定节省比例、加速倍率和厂商适用性口号不进入新文。

### `AI-LLM系统架构理论指南.md`

- `1-400`：Agent 组件、循环、计划和多 Agent 协作全部删除；仅记录第2部分开始的模型内部边界。
- `401-800`：保留 KV Cache 避免重算、逻辑块到物理块映射、前缀共享、迭代级批调度和投机验证的概念；固定模型尺寸、块大小、利用率、精度损失与加速倍率删除。
- `801-1200`：投机解码的接受率与额外工作取舍重写；RAG 概念、索引、融合、重排和上下文构建全部删除。旧稿的价格、延迟和节省百分比不采用。
- `1201-1550`：保留多实例负载、速率限制和成本约束的问题意识，改写为先质量资格、后排队/KV/缓存/成本打分；MCP、模型层级与静态任务分类表删除。

### 与既有文章的去重

`llm-application-system-architecture` 已承担请求信封、上下文、应用编排、外部能力、模型网关和交付门禁；`rag-retrieval-engineering` 已承担数据接入、分块、Embedding、ANN、混合检索、重排和证据化生成。本篇不重画两条链，只追踪 token 进入模型服务后的 Prefill/Decode、迭代调度、KV 生命周期、执行优化、实例路由、容量与单位成本。

### 一手来源核验

访问日期：2026-08-28

| 一手来源 | 本文采用的受支持论点 |
| --- | --- |
| [PagedAttention paper](https://arxiv.org/abs/2309.06180) | 分页块让动态增长的 KV Cache 按需分配并支持请求内与请求间共享；论文基准倍率不外推到当前模型与硬件。 |
| [vLLM official docs](https://docs.vllm.ai/) | 前缀缓存只跳过共享前缀的 Prefill 计算，不缩短新 token 的 Decode；量化和投机方法的真实收益需按模型、流量、硬件与采样设置复测。 |
| [TensorRT-LLM architecture](https://nvidia.github.io/TensorRT-LLM/architecture/overview.html) | 调度器逐步选择活动请求，KV Cache 管理器负责分配、释放与维护缓存；官方架构将调度、缓存和模型执行分为独立责任。 |
| [SGLang official docs](https://docs.sglang.ai/) | Prefill 偏计算密集，Decode 偏内存密集；路由可连接分离的两类实例。分离会新增 KV 传输与故障边界，因此本文仅把它作为需实测的容量选项。 |

## ai-cloud-native-runtime 精读结论

### `ai-cloud-native-opportunity.md`

- `1-37`（前言、为什么是 AI × 云原生）：保留 AI 工作负载资源密集、负载变化、模型迭代和运行形态多样的问题框架；“必须深度结合”“完美匹配”等结论性口号改写为资源、制品、就绪和恢复的可验收条件。
- `38-75`（痛点、设计原则、推理服务架构）：保留弹性、资源隔离、灰度、可观测与容错作为运行时目标；删除单一 Ingress—GPU Pod—PV 架构图、对象名堆叠和“模型热加载即可避免重启”的泛化结论。
- `76-97`（成本优化、技术选型）：厂商 GPU 产品、推理引擎和监控栈选型全部删除；固定利用率、成本降低和延迟降低比例没有可迁移证据，不进入正文。
- `98-124`（性能、安全、下期预告）：KV Cache、PagedAttention、Flash Attention、连续批处理等算法机制留在既有推理服务篇；通用鉴权和脱敏只作隔离背景，不扩写为安全专题。
- 旧稿的 `HPA/VPA 实现自动扩缩容` 被删除。新文把弹性重写为队列年龄、实际就绪容量、冷启动、设备压力与 SLO 的多信号控制，不把 HPA 当作所有推理服务的万能策略。

### `Kubernetes与容器编排深度指南.md`（通用概念辅助）

- `1-61`（架构与术语）：只保留声明式期望状态、控制循环和职责分层；control plane 组件、网络与存储链路清单不进入 AI 专题。
- `62-89`（容器运行时）：镜像构建与节点运行解耦这一原则用于区分运行时镜像和模型制品；CRI、containerd、cgroup 与版本迁移细节删除。
- `90-138`（调度、网络、存储）：保留资源请求影响放置、异构节点需要显式约束、存储恢复需要演练；CNI/CSI 选型表和故障百科删除，队列与准入行为改由 Kueue 官方资料核验。
- `139-166`（安全）：只保留运行时权限、网络、存储和租户配额需要共同形成隔离边界；PSP/PSA 版本史、配置模式和通用安全清单不迁移。
- `167-250`（GPU、升级、恢复、排障）：Device Plugin 暴露特殊硬件这一概念向 Kubernetes 官方页重核；GPU Operator、MIG、time-slicing、MPS、CDI、厂商指标、升级命令、etcd 细节和通用症状表全部删除。只重写 AI 工作负载的设备故障、检查点与结果验证。

### `Kubernetes与容器编排理论指南.md`（通用概念辅助）

- `1-400`（架构、控制面、调度、控制器）：保留声明式收敛、资源约束、优先级和滚动替换的问题意识；组件职责百科、过时术语、固定时间阈值、对象配置和大图全部删除。调度当前定义以 Kubernetes 官方入口为准。
- `401-800`（节点、网络、GPU 调度）：健康检查用于区分进程存活与模型真正就绪；kube-proxy 模式、固定复杂度、厂商设备拓扑、GPU 共享方式和性能结论删除。Device Plugin 只保留官方支持的注册、上报和整数扩展资源边界。
- `801-1200`（隔离、配额、RBAC、Helm）：保留配额、运行时、网络、存储与缓存共同隔离的抽象；Namespace/RBAC/NetworkPolicy/Helm 教程、角色样例和对象清单全部删除。
- `1201-1600`（Helm、GitOps、渐进式交付、CRD/Operator）：只保留不可变声明、版本化、持续调谐、灰度和回滚的通用原则；命令、模板、ArgoCD 行为、固定流量比例与产品能力不迁移。模型发布边界由 KServe 官方资料重新核验。
- `1601-1705`（Operator、调度器扩展、总结）：保留控制循环和过滤—放置的职责意识；调度扩展点百科、厂商拓扑打分和固定分数删除。新文不教授定制调度器，而是聚焦队列、准入、制品和恢复证据。

### 与既有文章的去重

`llm-inference-serving-engineering` 已承担 Prefill/Decode、连续批处理、KV Cache、量化、投机解码、路由、容量与单位成本。本篇仅用一句边界链接指向该文，不复述算法和引擎内部状态；模型在本篇是被打包、调度、缓存、扩展、发布和恢复的运行单元。两份 Kubernetes 长文仅提供声明式、一致性、调度、隔离、渐进发布与恢复的通用概念，不迁移成 Kubernetes 安装、对象或组件百科。

### 一手来源核验

访问日期：2026-08-28

| 一手来源 | 本文采用的受支持论点 |
| --- | --- |
| [Kubernetes Scheduling, Preemption and Eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/) | 调度把 Pod 匹配到节点；抢占与驱逐分别处理优先级和中断。本文据此区分节点放置、优先级抢占与故障恢复，不把它们混成一个调度动作。 |
| [Kueue Overview](https://kueue.sigs.k8s.io/docs/overview/) | Kueue 管理配额消费并决定工作负载等待、准入或抢占；Pod 到节点调度与任务生命周期仍由既有 Kubernetes 组件负责。 |
| [KServe official docs](https://kserve.github.io/website/) | KServe 控制面覆盖模型生命周期、版本跟踪与灰度发布；数据面承接预测和生成模型的请求接口。本文只采用这一发布责任边界，不复制快速安装和对象清单。 |
| [Kubernetes Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | Device Plugin 向 kubelet 暴露 GPU 等设备资源供工作负载请求；扩展资源按整数计数、不能超配，且不会自动表达显存、拓扑和模型兼容性。 |

## llm-agent-observability 精读结论

### `可观测性与监控-深度理论知识.md`

- `1-400`（可观测性、OpenTelemetry、上下文传播）：保留“输出要能反推内部状态”的问题意识、Trace/Span 数据模型、日志与追踪关联以及 W3C Trace Context 的跨组件相关性；控制论推导、通用三支柱对照、成熟度模型、Collector 产品管道和旧时间线不进入 AI 专题。
- `401-800`（采样、分布式追踪）：保留头部/尾部采样取舍、父子步骤、事件、异步因果关联和关键路径；固定 QPS、固定采样率、支付系统示例、存储产品横评与通用追踪可视化教材删除。
- `801-1200`（日志）：只保留结构化事件、Trace ID 关联、敏感内容最小采集和保留分层；日志级别教程、Agent/Sidecar 采集方式、压缩算法、查询语法和具体日志产品配置全部删除。
- `1201-1600`（指标、告警、SLO）：保留高基数 ID 不进入时序标签、面向用户结果的 SLI、告警可操作性、错误预算和分母定义；PromQL 教程、固定阈值、固定响应级别和通用资源监控清单删除。
- `1601-1857`（架构与最佳实践）：保留信号关联、分层存储和采样需要由用途驱动的原则；固定周期、固定压缩比例、产品选型表、部署路线和生态清单删除。
- 原稿中的 `2024-01-21`、`2025-01-21`、固定保留天数、固定采样比例、固定成本节省比例和具体厂商推荐均视为旧示例，不进入正文。

### 两份 LLM 源稿的相关范围复查

- `AI-LLM系统架构深度指南.md` 的 `2001-2400` 仅复查用量、延迟、费用、限流和模型路由的记录需求；示例代码、厂商模型、API 价格、静态复杂度分类器和 MCP 教程不迁移。
- `AI-LLM系统架构理论指南.md` 的 `1201-1550` 仅复查多实例负载、请求速率、模型层级和成本约束如何形成观测信号；固定 RPM、延迟、价格、实例数、加速倍率和任务关键词规则全部删除。
- 上述模型服务内容已经由 `llm-inference-serving-engineering` 承担。本篇只关联实际模型、token、延迟、路由、成本和发布批次，不重复 Prefill/Decode、KV Cache 或调度机制。

### 与既有文章的去重

`llm-application-system-architecture` 已承担请求契约、上下文装配、工具编排、输出验证和可靠交付；`rag-retrieval-engineering` 已承担数据接入、分块、召回、重排、证据化生成和检索评估；`enterprise-agent-system-architecture` 已承担运行循环、持久化状态、工具授权、审批和子任务。本篇不重画这些流程，只规定它们为一次执行留下的版本、检索证据、工具调用、质量、安全、成本与反馈信号。

### 一手来源核验

访问日期：2026-08-28

| 一手来源 | 本文采用的受支持论点 |
| --- | --- |
| [OpenTelemetry Generative AI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | GenAI 约定已迁至独立官方仓库，当前整体状态为 Development；独立仓库的 Schema URL 仍是待完成项，因此本文把外部字段作为带版本的可演进映射。 |
| [OpenTelemetry GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) | 原属性注册表中的 GenAI 字段已标为 Deprecated 并指向独立仓库；本文不把历史 `gen_ai.*` 名称写成永久字段合同。 |
| [W3C Trace Context](https://www.w3.org/TR/trace-context/) | traceparent 提供跨组件关联所需的 trace-id、parent-id 与 trace-flags；tracestate 用于可选厂商信息，传播上下文不等同于接受上游身份或采样决策。 |
| [NIST Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | 持续监测、内容来源、结构化反馈和独立评估共同支撑生成式 AI 风险管理；本文据此把线上证据、人工诊断、版本化反馈集和发布门禁闭合为改进循环。 |

## open-source-agent-runtime 精读结论

### `Hermes-Agent架构分析与思考.md`

- `1-120`（定位、全景）：保留从入口、Agent loop、工具、执行环境、会话与记忆观察运行时的问题框架；版本、测试量、提交量、平台数、模型数和“早期生产”结论全部删除。
- `121-220`（学习、Skills、Tools、执行后端）：保留“记住什么”、“知道怎么做”和“被允许做什么”应分层的问题；任何固定功能数量、平台枚举和云环境成本结论不进入新文。
- `221-310`（循环、依赖、亮点与不足）：保留模型—工具—结果的反馈循环、上下文压缩和中断问题；“同步核心”、自动技能质量、配置数量等主张不沿用，改为向当前代码核验。
- `311-390`（横向对比与总结）：功能象限、优劣排名、“唯一”和未经核验的用户规模删除；只保留“持续状态需要可预测、可追溯、可恢复”这一设计问题。
- 原稿的分析时间、版本号、功能数、提交数、PR 数、性能结论和“已证明生产可用”均不进入正文。

### `Clawdbot架构理论指南.md`

- `1-65`（名称、Gateway）：保留项目已使用 OpenClaw 现名、Gateway 是长期运行连接与会话入口的核心问题；旧名不再当成当前产品名，动态渠道清单不迁移。
- `66-137`（Tools、Skills、Plugins、安全）：保留指令、行动接口与运行时扩展分层，以及工具可见性应在模型调用前收窄的原则；“有安全选项即生产安全”类泛化不采用。
- `138-218`（责任分层与设计借鉴）：保留入口、控制面、运行循环、能力和本地状态的评估问题；具体内部调度、压缩、重试和恢复行为改为官方当前文档与代码证据。
- `219-284`（评估与结论）：保留最小闭环、安全默认值、状态位置、工具副作用与项目限制的评审方法；通道覆盖、平台计数和成熟度判断删除。
- 旧稿基于更早日期的文档描述；新文不直接继承任何路径、存储格式或安全默认值，全部以执行日仓库快照重新核验。

### 与既有文章的去重

- 已复读 `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md` 全文 `1-361`，SHA-256 为 `c04ed6a9c02fe35397949197f9fd63bea9fae6076d7846002043df235faac21d`。新文不重述 Claude Code 的开发者体验、Hooks、MCP、子 Agent 或自研 Agent 全景，只复用“公开事实与工程抽象分开”的证据方法。
- `enterprise-agent-system-architecture` 已承担企业 Agent 全景、运行循环、持久化状态、信任与子任务；本篇不重画全景，只比较两个开源运行时的 ownership boundary。
- 身份与授权细节链接到 `agent-identity-access-control`；运行证据链接到 `llm-agent-observability`。本篇只说明运行时应暴露的接口，不重述两个专题。
- 不提前给出框架选型结论，不建立功能排行榜；本篇只提供会话、工具、远程执行与恢复的责任检查项。

### 官方仓库快照与实际读取路径

访问日期：2026-08-28

| 项目 | Remote | 默认分支 | 完整 HEAD SHA |
| --- | --- | --- | --- |
| Hermes Agent | `https://github.com/NousResearch/hermes-agent.git` | `main` | `35328345d5e3b5badc47271bdb8828e1fd2d25f4` |
| OpenClaw | `https://github.com/openclaw/openclaw.git` | `main` | `468054f93c431bfe192327f439efe325be52f2b4` |

Hermes 快照实际读取：

- `README.md`：定位、CLI/Gateway、provider/model 切换、学习、记忆、Skills 与执行后端的官方入口。
- `agent/conversation_loop.py` 与 `run_agent.py`：循环、会话持久化、system prompt 恢复、压缩、重试与结束边界。
- `model_tools.py`、`tools/skills_tool.py`：工具注册与会话范围过滤，Skill 的元数据列表、按需加载和路径安全。
- `tools/terminal_tool.py`、`docs/security/network-egress-isolation.md`：本地、容器、远程执行后端，审批与网络隔离边界。
- `gateway/session_db_recovery.py`、`docs/micro-compaction.md`：SessionDB handle 的单飞打开、失败后续访问继续尝试、封顶指数退避，以及压缩失败的最佳努力语义；前者没有总重试次数上限。

OpenClaw 快照实际读取：

- `README.md`、`docs/concepts/architecture.md`：Gateway、clients、nodes、WebSocket 与 channel 接入边界。
- `docs/concepts/agent-runtimes.md`、`docs/agent-runtime-architecture.md`、`docs/openclaw-agent-runtime.md`：provider/model/runtime/channel 分层；selected runtime 负责 prepared model loop，canonical thread/context/tools/compaction 的所有权随 runtime 合同变化，channel delivery 仍由 OpenClaw 承担。
- `docs/concepts/agent-loop.md`、`src/agents/embedded-agent-runner/run.ts`、`src/agents/agent-tools.ts`：会话串行、上下文装配、模型调用、工具执行、流式事件与持久化边界。
- `docs/concepts/context-engine.md`、`docs/concepts/session.md`、`docs/concepts/memory.md`：上下文引擎、会话路由与存储、长期记忆与压缩关系。
- `docs/tools/skills.md`、`docs/gateway/sandbox-vs-tool-policy-vs-elevated.md`：Skill 加载和快照，sandbox、tool policy 与 elevated exec 的独立责任。
- `docs/gateway/restart-recovery.md`、`docs/channels/channel-routing.md`：重启恢复、重复副作用防护、不恢复状态，以及由 host 决定的消息路由。

### 一手来源核验

| 一手来源 | 证据级别 | 本文采用的受支持论点 |
| --- | --- | --- |
| [Hermes Agent official repository](https://github.com/NousResearch/hermes-agent) | 官方文档 + 公开代码 | Hermes 公开仓库 main@35328345d5e3b5badc47271bdb8828e1fd2d25f4；README 支持 CLI/Gateway、Skills、memory 与多执行后端的入口事实，循环、工具过滤和局部恢复语义由上述代码路径分别证明。 |
| [OpenClaw official repository](https://github.com/openclaw/openclaw) | 官方文档 + 公开代码 | OpenClaw 公开仓库 main@468054f93c431bfe192327f439efe325be52f2b4；Gateway、session、agent loop、Skills、memory、sandbox 和 recovery 责任只在当前文档与相应代码路径范围内陈述。 |
| [OpenClaw Agent runtimes](https://github.com/openclaw/openclaw/blob/468054f93c431bfe192327f439efe325be52f2b4/docs/concepts/agent-runtimes.md) | 官方文档 | provider、model、agent runtime 与 channel 是四个不同责任层；selected runtime 接收 prepared turn、驱动模型输出、处理 native tool calls 并返回 finished turn。canonical thread/context/tools/compaction 的 ownership 随 runtime 合同变化，channel delivery 仍由 OpenClaw 承担。 |
| [OpenClaw Agent runtime architecture](https://github.com/openclaw/openclaw/blob/468054f93c431bfe192327f439efe325be52f2b4/docs/agent-runtime-architecture.md) | 官方文档 | OpenClaw 官方文档列出 built-in runtime、session、tools、harness 与 model/provider transport 的代码布局；布局本身不证明 selected runtime 拥有 Gateway session、平台策略或恢复编排。 |

### 保留、删除与抽象边界

- **保留**：Agent loop、context/session、Skills/Tools、memory、channel/远程执行、sandbox/permissions、recovery 与 ownership boundary 这些共同工程问题。
- **删除**：旧日期、动态版本、功能或渠道数量、社区热度、产品优劣、无法复核的生产效果和泛化安全承诺。
- **公开代码直接证明**：只在实际读取路径能定位的循环、策略、持久化与恢复语义范围内陈述。
- **从公开结构可以推断**：两个项目都可用入口与会话、上下文与循环、行动与恢复三类问题来评审；这是本文比较模型，不是组件等价或隐藏执行顺序的事实声明。对 OpenClaw，host/Gateway 与 selected runtime 保持相邻边界，不把 Gateway session、channel delivery、平台策略和恢复编排塞入 runtime 内部。

## metabot-agent-control-bus 精读结论

### `MetaBot架构设计理论分析.md`

- `1-112`（定位、全景与规模）：保留“聊天入口连接远程 Agent”这一问题意识，以及 channel、bridge、engine、session 的分层视角；旧组织署名、旧仓库定位、版本、部署拓扑、代码行数、端口、固定组件与引擎数量全部删除。
- `113-270`（引擎、MessageBridge、persistent executor）：保留消息规范化、控制协调、持久执行与跨轮会话的问题框架；具体接口、超时、队列、窗口、成本与实验阶段全部向当前代码重核，不继承旧稿数值和“核心创新”评价。
- `271-400`（memory、skills、session、federation、voice）：仅保留 session linking 需要与身份、授权分开的边界；memory、skill 工厂、语音、联邦和产品功能清单不属于本篇，全部删除。
- `401-515`（模式、对比、亮点与结论）：保留 adapter、persistent execution、恢复与审计的通用工程问题；项目排行、代码规模比较、营销结论、用户场景故事和未经复核的安全承诺全部删除。

### 当前代码仓快照

访问日期：2026-08-28

| 仓库 | 完整 HEAD SHA | 证据用途 |
| --- | --- | --- |
| `/Users/neo/Developer/work/Orbbec-Agent-Team` | `94e1c128f33a153b980ef45b8c002d5bb8d2bac9` | 已提交部署合同、可靠性恢复和 flywheel 存储边界；仓内未跟踪文件未读取为实现，也未修改。 |
| `/Users/neo/Developer/work/metabot-dev` | `73e172192e21621c4bb1d9bf307ab8755ac643cf` | 当前 channel、bridge、session、executor、Core Chat、恢复、回执与 audit/flywheel 运行代码；仓内未跟踪 `.tools/` 未读取为实现，也未修改。 |

Orbbec-Agent-Team 实际读取：

- `CLAUDE.md`、`README.md`、`flywheel/README.md`：当前工作区指导、部署责任和 flywheel 只存储原始执行证据的已提交文档边界。
- `deploy/metabot.runtime-contract.json`：当前部署合同声明 collaboration 能力与隔离方式；其中的动态版本、固定 Bot、端口和规模没有进入正文。
- `scripts/reliability/recovery.mjs`、`scripts/reliability/metabot-client.mjs`：恢复动作会排除真实用户或重复投递风险，重验收要求新 attempt 且不重放原 turn；探针客户端区分运行结果和分阶段 receipt。
- `flywheel/migrations/002_tables.sql`、`flywheel/migrations/003_api.sql`：消息、run、event、evidence 和身份映射的持久事实；event ID 与平台消息约束支持重复检测，但存储提交不等于业务副作用完成。

metabot-dev 实际读取：

- `CLAUDE.md`、`README.md`：当前项目入口、channel 与持久会话的已提交文档；README 中的营销、动态能力清单和数量不作为实现证据。
- `src/types.ts`、`src/feishu/event-handler.ts`、`src/telegram/telegram-bot.ts`、`src/wechat/wechat-bot.ts`：各入口映射 `IncomingMessage`；飞书 sender、Telegram sender 与微信 sender 属于渠道标识，代码没有把它们提升为 Platform 授权身份。
- `src/bridge/prompt-normalizer.ts`、`src/bridge/message-bridge.ts`、`src/bridge/error-classifiers.ts`：引擎命令形式规范化、每 chat 运行与排队、命令处理、输出投递和 activity/audit/flywheel 记录。正常错误分支与 catch 中的 legacy stale-session/context-overflow fallback 会直接 fresh-session 重放原 prompt，尚未统一经过 effect gate，可能在已有副作用后重放原 prompt；`multiple tool_result` 错误也被归入 stale session。
- `src/session/session-registry.ts`、`src/engines/claude/session-manager.ts`：显式 session link、聊天到引擎 session 的映射和本地持久化；会话绑定不等于授权。
- `src/engines/claude/executor-registry.ts`、`src/engines/claude/persistent-executor.ts`：按 chat 管理的持久执行、单活动 turn、释放、异常恢复和 resume 边界；session resume 不证明外部副作用可重放。
- `src/api/routes/core-chat-contract.ts`、`src/api/routes/core-chat-session-store.ts`、`src/api/routes/core-chat-routes.ts`：`core_chat_collaboration_v3`、task session、连续 `messageSeq`、父运行、命令摘要、`active/stopped/failed` journal 和 `accepted/replayed` 接收语义。
- `src/bridge/provider-turn-recovery.ts`、`src/bridge/claude-turn-recovery.ts`、`src/bridge/tool-effect.ts`、`src/engines/claude/pty/turn-recovery.ts`：provider/process-exit 路径经过 tool-effect gate，只在无可用终态、未停止且副作用条件允许等窄条件下做有界恢复；该保证不能外推到上述 legacy fallback。
- `src/bridge/final-delivery.ts`、`src/reliability/probe-receipt-store.ts`、`src/utils/audit-logger.ts`、`src/flywheel/envelope.ts`、`src/flywheel/queue.ts`：最终投递重试、探针阶段回执、结构化审计、运行证据和异步写入失败边界。

Platform 当前 worktree 实际读取：

- `backend/app/agent_brain/adapters/metabot_local.py`：dispatch 接收不等于完成；公共任务通过 relay event 另行 reconcile，重复 enqueue 只在相同 job kind 边界内接受。
- `backend/app/execution_relay/models.py`、`backend/app/execution_relay/repository.py`：加密 job/event、租约、取消意图、事件序列和 terminal 状态属于 Platform relay。
- `backend/app/execution_relay/worker.py`、`backend/app/execution_relay/metabot_client.py`：Worker 在本地持久状态、MetaBot callback 与云端 relay 之间转送；异常响应进入 interrupted 或后续对账，不以一次 HTTP 确认冒充任务完成。
- `backend/app/agent_brain/loop_models.py`、`backend/app/agent_brain/loop_repository.py`：公共 loop、task、delivery、授权快照和结果归一化由 Platform 持有。

### 事实分级与 Platform 边界

- **当前代码直接证明**：上述入口归一化、chat/session 映射、命令 journal、执行器注册表、relay 状态、callback 顺序和审计记录，只在实际读取路径能定位的范围内陈述。恢复判断按路径披露：provider/process-exit 有 effect gate，legacy stale/context fallback 当前没有统一 gate。
- **已提交文档明示**：部署合同、可靠性控制面与 flywheel 运维边界用于说明目标责任；文档中的规划、动态版本、固定部署数量和端口不被写成当前通用能力。
- **作者工程推断**：从当前公开结构可以推断，远程控制需要分别对账 Platform 公共状态、MetaBot 私有状态和目标系统副作用；本文推断所有 replay 应收口到统一证据门禁，无法证明只读或无副作用时停止重放并进入对账。贯通主体、工具幂等查询与未知结果流程也应先于新增渠道。两类推断均在正文逐处显式标注。

双层控制面再加目标系统事实的边界是：Platform 持有**公共任务与 relay 状态**，MetaBot 持有**私有会话与执行状态**，**目标系统真实副作用**由实际业务系统持有。当前 collaboration v3 链路没有贯通 Platform 验证主体：`RequesterSubject` 虽已存在于 Platform relay 模型，但 MetaBotLocalAdapter 没有设置它，MetaBotClient 的 collaboration 请求也没有发送它。渠道消息身份、session key、ack、取消请求和 stop 回执都不能越过这些所有权边界。

### 旧稿冲突、保留与删除

- **冲突**：旧稿把固定渠道、引擎、服务、队列、超时和恢复参数写成系统全景；当前代码已演化为 Core Chat journal、Platform relay、已门禁的 provider/process-exit recovery、尚未统一门禁的 legacy fallback 和多类证据，故以当前提交代码为准。
- **保留**：远程控制总线、channel adapter、message normalization、identity/session binding、persistent executor、command lifecycle、idempotency、reconnect、audit 和 remote-control risk 这些稳定工程问题。
- **删除**：旧组织、旧部署拓扑、动态版本、代码行数、端口、固定 Agent/进程/用户/渠道数量、营销效果、横向排名和无法从当前代码复核的组件能力。
- **去重**：通用 Agent loop、Skills/Tools、memory 与 sandbox 留在 `open-source-agent-runtime`；身份与最小权限留在 `agent-identity-access-control`；证据字段和质量闭环留在 `llm-agent-observability`。本篇只展开远程控制与可靠投递。

## agent-framework-selection 精读结论

### `主流Agent框架深度分析-从架构本质到生产可用性.md`

- `1-46`（口径与分类）：保留“先分产品形态，再问责任边界”的方法；旧候选集、旧访问日期、三类固定分法与“适合生产”判断不直接沿用。
- `47-74`（生产维度）：保留状态、恢复、权限、观测、部署与团队所有权的问题意识；重写为八个可验收维度，不给产品打分。
- `75-137`（开发者工具）：只保留 developer tool 与服务端底座的责任差异；Claude Code、Codex CLI 和 Cursor 的功能枚举及场景表删除，具体工程设计留在已有 Claude Code 文章。
- `138-207`（编排库）：保留控制流、状态、角色协作和消息拓扑是不同抽象的观察；LangGraph、CrewAI 和旧 AutoGen 的旧定位、适用/不适用结论和成熟度评价全部向执行日官方文档重核。
- `208-260`（平台与运行时）：保留可视化平台与代码嵌入式运行时责任不同的问题；Dify、Coze Studio、Hermes 的宣传语、功能表和泛化上线判断不进新文。
- `261-323`（自研与检查清单）：保留企业权限、审计、租户、幂等和回滚不会被框架自动承担的原则；“常需自研”改为先判断是否需要框架，再用退出条件和 PoC 门禁决策。
- 原稿中的 `2026-04-16`、旧 URL、产品列表、功能横评、宣传用语、任何瞬时热度与永久排名均删除。

### 三篇同类文章复读与去重

- 已复读 `01-claude-code-architecture.md` 全文 `1-361`，SHA-256 `c04ed6a9c02fe35397949197f9fd63bea9fae6076d7846002043df235faac21d`。新文只把它作为 developer tool 形态样本，不重述 Hooks、MCP、权限或 Agent loop。
- 已复读 `02-open-source-agent-runtime.md` 全文 `1-240`，SHA-256 `47d887d2b46e6201ae5b4a56b3f946e2bf4d939e7dc4d196c25596b29f2c54f0`。新文只把 Hermes/OpenClaw 作为 agent runtime 与 ownership boundary 样本，不重画 provider/model/runtime/channel 或 recovery。
- 已复读 `03-metabot-agent-control-bus.md` 全文 `1-240`，SHA-256 `c1286562fde608cf014d75ffa63080bc38d9bd15158c89d6f8c6131b12deec95`。新文只用它说明远程控制总线与运行时/平台的边界，不重述渠道、幂等、断线恢复或三层对账。
- 具体产品架构留在上述三篇；本篇只定义四类产品形态、八维责任、锁定/退出成本、淘汰条件和 PoC 门禁。

### 执行日官方来源核验

访问日期：2026-08-28

| 官方来源 | 执行日定位、显式状态与可确认边界 |
| --- | --- |
| [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) | 低层编排框架与运行时，聚焦持久执行、流式输出、人在回路与持久化；页面未给出明确 lifecycle 状态 |
| [CrewAI Documentation](https://docs.crewai.com/index) | 以 Agents、Crews 和 Flows 构建协作与编排，部署和 RBAC 等能力属于 Enterprise journey；页面未给出明确 lifecycle 状态 |
| [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/) | Microsoft Agent Framework 是 AutoGen 与 Semantic Kernel 的直接继任者；Go 版明确为 public preview |
| [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents) | 代码优先的 Agents SDK 运行 agent loop，服务器仍拥有部署、工具实现、状态存储与审批决策；页面未给出明确 lifecycle 状态 |
| [Google ADK](https://adk.dev/) | 旧 Google ADK 入口已重定向 adk.dev；当前定位覆盖多语言开发框架、Agent Runtime、部署、观测与评估，页面未给出统一 lifecycle 状态 |
| [Dify Documentation](https://docs.dify.ai/en/home) | 开源 AI 应用平台，覆盖 Agent、工作流、聊天应用、Web/API 发布与云端或自托管；页面未给出明确 lifecycle 状态 |
| [Coze Studio official repository](https://github.com/coze-dev/coze-studio) | 开源一站式可视化 Agent 开发平台；公网部署需评估安全风险，开源版与商业版存在能力差异 |
| [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) | Python/TypeScript 库在自有进程运行 Claude Code agent loop；托管长任务属于独立 Managed Agents 产品，页面未给出 SDK 明确 lifecycle 状态 |

OpenAI 事实仅采用 OpenAI Docs 的 canonical Agents SDK 页面；没有从原稿旧链接、第三方榜单或搜索摘要补充产品结论。Google 采用重定向后的官方入口；Microsoft 按当前继任关系取代原稿 AutoGen 独立候选定位。页面存在不等于生产准入，官方未说明的维度一律保留为未知，留给 PoC 验收。

### 保留、删除与不确定性

- **保留**：先判断是否需要框架；再按 developer tool、orchestration library / SDK、agent runtime、end-to-end platform 划定责任；最后用八维问题、淘汰条件和 PoC 证据决策。
- **删除**：功能宽表、分数、星级、象限、社区热度、瞬时版本和任何“框架已替团队解决生产责任”的结论。
- **时点限定**：某个候选可跨相邻形态，文中只写截至 2026-08-28 的主要定位和本文用途，不把分类写成永久唯一归属。
- **不确定性**：除 Microsoft Go 版显式 public preview 外，上表所列官方入口若未给出 lifecycle 标签，只记为“官方页面未给出明确状态”；不从文档丰富度或宣传语推导成熟度。

## intent-driven-ai-business-platform 精读结论

### `干掉用户旅程-意图驱动的业务平台架构设计.md`

- 完整阅读：已精读：1-379；执行时使用 `wc -l` 核对为 379 行，SHA-256 为 `3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b`，源目录保持只读。
- `1-82`（传统旅程与入口）：保留固定页面难以覆盖跨模块、开放目标这一问题；删除原题、旧日期、自然语言唯一入口、导航和页面必然退出等绝对结论。新文明确自然语言、表单、页面、API 与事件可以并存。
- `83-198`（能力模型、工具和经验固化）：保留能力原子化、描述与 Schema、风险和副作用、确定性流程与开放判断需要分层的问题；删除固定倍率、固定相似度阈值、零 token、结构化生成错误低一个数量级等未经复核数字，并删除模型自行生成制品后直接生效的叙事。
- `200-237`（信任与自动化分级）：保留风险决定自动化程度、审批和人工接管；删除“完全自主”“全自主运营”和人只做战略决策等终局承诺。完整信任决策链不在本篇重画。
- `239-338`（自演进、跨平台协作与护城河）：只保留候选制品需要验证、评审、灰度和持续监测这一工程问题；删除永不停歇的自演进、无需能力发现或错误语义、所有容错自然发生、网络效应和护城河等宣传性论证。
- `340-379`（迁移路径与结语）：保留不重写业务内核、从低风险链路渐进迁移；删除关闭传统界面、能力自动覆盖所有场景、生态已经成熟以及平台自行生长等泛化结论。

### 两篇站内文章复读与去重

- 已复读 `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md` 全文 `1-578` 至 EOF。主体、委托、完整信任模型、Agent loop、工具副作用、任务状态和审计全景留在该文；新文只引用建议、草拟、审批后执行与有限自治如何绑定具体业务意图。
- 已复读 `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md` 全文 `1-364` 至 EOF。材料治理、方案推演、决策记录和 AI 辅助架构协作方法留在该文；新文只写业务平台从固定旅程到受控意图执行的产品与工程边界。

### 执行日官方来源核验

访问日期：2026-08-28

| 官方来源 | 本文采用的稳定边界 |
| --- | --- |
| [Anthropic, Building effective agents](https://www.anthropic.com/research/building-effective-agents) | workflows 使用预定义代码路径，agents 动态决定过程与工具；从简单方案开始，只在结果证明必要时增加复杂度 |
| [NIST Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | 按组织风险容忍度治理，明确人的监督责任，并持续监测部署后的风险控制 |
| [OpenAI, A practical guide to building agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) | 编排应渐进增加复杂度，guardrails 采用分层防御，失败阈值与高风险行动触发人工介入 |
| [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents) | 代码优先的 Agents SDK 运行 agent loop，服务器仍拥有部署、工具实现、状态存储与审批决策 |

Anthropic 只用于 workflows / agents 的定义边界、简单优先和按需增加复杂度，不外推 Claude 产品能力。NIST 官方出版页及 NIST AI 600-1 原文用于风险容忍度、监督责任、持续监测、可停用和恢复等治理原则，不把跨行业建议写成特定业务的固定等级。

OpenAI PDF 以只读方式下载到临时 `tmp/pdfs/openai-practical-guide-building-agents.pdf`。`pdfinfo` 核验 PDF 共 34 页、无加密、PDF 1.7；文件 SHA-256 为 `1903c2b1837b206d1951d8a3d1124515aee1bebebd8f2448c630c75a6b0aad86`。`pdftotext -layout` 完整提取 1659 行用于定位；`pdftoppm` 渲染并人工核验页面 13、25、31：第 13 页是单 Agent / 多 Agent 编排及渐进复杂度，第 25 页是分层 guardrails 图，第 31 页是失败阈值与高风险动作触发人工介入。PDF 没有被编辑或重新导出。

OpenAI 的时点性产品事实再以当前 `https://developers.openai.com/api/docs/guides/agents` 交叉核验：Agents SDK 是代码优先的 agent loop，服务端继续拥有工具实现、状态存储和审批决策。正文只采用编排、分层 guardrails 和人工介入的稳定原则，不把 2025 PDF 中的 SDK 样例或当前产品行为写成永久合同。

### 绝对叙事清理与新文边界

- **入口**：删除“对话是唯一入口”“导航式 UI 必然退出”；改为自然语言只是入口之一，页面、表单、API 和事件共享结构化意图合同。
- **意图**：删除“用户说一句话即可执行”；改为目标、对象、约束、权限主体、风险、预算、完成条件、证据与补偿边界组成的可验证合同。
- **编排**：删除规则和确定性流程被取代的叙事；改为确定性 workflow 拥有已知顺序和硬约束，Agent 只处理开放判断。
- **自治**：删除完全或无限自治、无需审批治理、所有业务交给 Agent；改为建议、草拟、审批后执行、有限自治四个受风险和责任约束的层级。
- **演进**：删除自我进化取代发布流程；候选规则、workflow 和能力仍经过测试、评审、灰度、监测和可回滚发布。
- **完成**：模型自述、HTTP 200、消息投递或流程末节点都不提升为业务完成；只接受目标系统真实状态、事务回执和可验证交付物。
- **迁移**：从观测与建议开始，经受控执行和验证逐步扩大；每阶段都可停、可降级、可回滚。

本篇最终只负责“固定旅程覆盖不足时，业务平台如何把多入口请求转成受控意图执行”这一问题，不复刻企业 Agent 全景或 AI Native 架构协作方法。

## 最终跨文章去重矩阵

2026-08-28 按分类顺序全文复读当前 14 篇文章后，最终确认每个核心主题只保留一篇主文章。其他文章只保留理解本篇边界所需的一句接口说明，并用站内链接把完整解释交还主文章；不复制主文章的流程、状态机、算法、产品事实或评估方法。

| 主题 | 唯一主文章 | 其他文章保留的边界与站内链接 |
| --- | --- | --- |
| Agent 工程学习次序 | [Agent 工程学习地图](../../backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md) | 学习地图只组织递进实验，并把系统全景、检索和产品样本分别链接到[企业级 Agent 系统架构](../../backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md)、[RAG 检索工程](../../backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md)和[Claude Code 架构分析](../../backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md)。 |
| LLM 应用请求到可靠回答 | [LLM 应用系统架构](../../backend/app/ai_notes/content/01-foundations/02-llm-application-system-architecture.md) | [推理服务工程](../../backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md)只接收应用层已确定的请求合同；[可观测性](../../backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md)只记录请求链留下的版本、步骤和结果证据。 |
| Agent 全景、状态与信任层 | [企业级 Agent 系统架构](../../backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md) | [身份与最小权限](../../backend/app/ai_notes/content/02-agent-architecture/02-agent-identity-access-control.md)只深化委托和行动授权；[意图驱动业务平台](../../backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md)只引用业务信任层级，不重画 Agent 全景。 |
| 身份、委托与行动授权 | [Agent 身份与最小权限](../../backend/app/ai_notes/content/02-agent-architecture/02-agent-identity-access-control.md) | [企业级 Agent 系统架构](../../backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md)只保留身份与策略接口；身份篇承担主体、短时凭证、资源授权、审批绑定和审计细节，并链接回全景文。 |
| Claude Code 公开能力 | [Claude Code 架构分析](../../backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md) | [Agent 框架选型](../../backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md)只把 Claude Code 当作 developer tool 样本，不复制 Hooks、MCP、权限和工作循环。 |
| 开源 Agent 运行时事实 | [Hermes 与 OpenClaw](../../backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md) | [MetaBot 控制总线](../../backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md)只链接通用运行时边界；[框架选型](../../backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md)只比较产品形态和责任，不重述项目代码事实。 |
| MetaBot 远程控制总线 | [MetaBot 架构](../../backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md) | [开源运行时](../../backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md)只给出 channel 与 runtime 的共同边界；[框架选型](../../backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md)只把 MetaBot 当作远程控制样本。 |
| Agent 框架选型方法 | [主流 Agent 框架选型](../../backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md) | 具体产品架构分别留在[Claude Code](../../backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md)、[Hermes 与 OpenClaw](../../backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md)和[MetaBot](../../backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md)；选型文只负责形态、八维责任、退出条件和 PoC 门禁。 |
| RAG 检索与引用 | [RAG 检索工程](../../backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md) | [LLM 应用系统架构](../../backend/app/ai_notes/content/01-foundations/02-llm-application-system-architecture.md)只消费证据包；[可观测性](../../backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md)只记录检索证据引用，不重复索引、召回和重排。 |
| LLM 推理性能机制 | [LLM 推理服务工程](../../backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md) | [AI 云原生运行时](../../backend/app/ai_notes/content/04-ai-engineering/03-ai-cloud-native-runtime.md)只讨论模型服务的资源与运行包装并链接本篇；[可观测性](../../backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md)只关联推理指标定义。 |
| AI 工作负载运行时包装、调度与恢复 | [AI 云原生运行时](../../backend/app/ai_notes/content/04-ai-engineering/03-ai-cloud-native-runtime.md) | [推理服务工程](../../backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md)只负责模型服务内部执行；本篇只在运行包装层处理资源信封、准入、制品、弹性、发布和恢复。 |
| AI 质量信号与反馈闭环 | [LLM / Agent 可观测性](../../backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md) | [LLM 应用系统架构](../../backend/app/ai_notes/content/01-foundations/02-llm-application-system-architecture.md)和[企业级 Agent 系统架构](../../backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md)只定义必须暴露的证据接口；质量信号、反馈集和发布闭环留在本篇。 |
| AI 辅助架构设计方法 | [AI Native 辅助架构设计](../../backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md) | [意图驱动业务平台](../../backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md)只引用材料治理、方案推演和评审方法，不重复 AI 与人的架构协作流程。 |
| 意图驱动业务平台 | [意图驱动的 AI 业务平台](../../backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md) | [企业级 Agent 系统架构](../../backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md)保留 Agent 全景，[AI Native 辅助架构设计](../../backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md)保留设计协作方法；本篇只负责多入口到受控业务执行。 |

矩阵覆盖 14 篇文章的唯一主责，也闭合了 Agent 全景、应用请求链、RAG、身份授权、推理性能、运行时包装、质量反馈、Claude Code、开源运行时、远程控制和框架选型等易重复主题。逐篇复读没有发现需要复制到另一篇的完整段落；保留的重复术语均用于边界声明或站内导航。
