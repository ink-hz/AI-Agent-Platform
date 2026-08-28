# AI 工程笔记八篇迁移：源稿研究台账

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
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/ai-cloud-native-opportunity.md` | 124 | `9c04bf78390c08bbe3c89e685bf4f407434c6f96c0ec63828662db75239c0a07` | ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排深度指南.md` | 250 | `4908263dc8ebdbe69106f50b8aa8f2b5030dd0a7e9fc5827945784e02dd31df4` | 辅助 ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Kubernetes与容器编排理论指南.md` | 1705 | `12361a569b863b0de58f43366420c822f5b8568b42f243c38aab16aa0eef53ff` | 辅助 ai-cloud-native-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/可观测性与监控-深度理论知识.md` | 1857 | `546ea435a32227f0ec75e73e946e45e4cb5a6e092ee55deaf7d456de3c43b8ed` | llm-agent-observability | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Hermes-Agent架构分析与思考.md` | 390 | `aac8a4c575a11ae1a129c3e03d49e6217d2c02a92363b0689d5c5306c70227ad` | open-source-agent-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/Clawdbot架构理论指南.md` | 284 | `838ae6a89bfeca305bb70bc016f975d7b12f34abef9fe73dd4638c22dedb6961` | open-source-agent-runtime | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/MetaBot架构设计理论分析.md` | 515 | `f526d770501328c0aa12bb926ae379c640bebb3cd9540fe4b569a581caebded5` | metabot-agent-control-bus | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/主流Agent框架深度分析-从架构本质到生产可用性.md` | 323 | `4edd175b19b9ac82be0ac9a92ed10d69ddfe14cac7611fa7b3df9f0a5866054b` | agent-framework-selection | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |
| `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/干掉用户旅程-意图驱动的业务平台架构设计.md` | 379 | `3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b` | intent-driven-ai-business-platform | 未开始 | 未开始 | 未开始 | 未开始 | 未开始 |

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
