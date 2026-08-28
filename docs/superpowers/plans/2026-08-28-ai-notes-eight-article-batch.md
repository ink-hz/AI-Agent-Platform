# AI 工程笔记剩余八篇批量迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将个人博客存货中的八个 AI 工程主题逐篇精读、重新写作并配制可读的 Mermaid 图，保持为草稿直至整批通过门禁，再统一发布到 AI 工程笔记，使生产目录从 6 篇增长到 14 篇。

**Architecture:** 在隔离分支中建立批次研究台账和草稿发布合同，然后严格按顺序一次完成一篇。每篇从空白正文重写，旧稿只作素材，一手资料用于核验会变化的事实；批次专属测试直接检查草稿并把 frontmatter 的副本临时视为正式文章，证明草稿始终可发布。八篇全部完成后，统一切换为正式文章，由现有正式文章自动发现机制和真实 Mermaid 渲染测试接管，再合并、推送和受控部署。

**Tech Stack:** Python 3.12、FastAPI 内容仓库、PyYAML、markdown-it-py、pytest、React 19、TypeScript、Vitest、Mermaid、Vite、Docker Compose、Bash 受控发布脚本。

## 全局约束

- 源仓库 `/Users/neo/Developer/personal/starship-blog-source` 只读；不得修改、格式化、提交或建立运行时链接。
- 正式内容只写入 `backend/app/ai_notes/content/`，研究证据写入 `docs/reviews/ai-notes-eight-article-source-review.md`。
- 作者固定为 `苍渊`，座右铭固定为 `博观而约取，厚积而薄发。`；正文不重复署名。
- 八篇制作期一律 `draft: true`。只有 Task 11 可以同时将八篇改为 `draft: false`。
- `publishedAt` 和 `updatedAt` 使用 Task 11 实际发布当天日期，且不得早于 `2026-05-25`；不得提前写未来日期。
- 每篇先全文精读、后查一手资料、再从空白正文写作；禁止用品牌词替换或规则匹配冒充清洗。
- 会变化的产品能力、版本、架构与标准状态，只能引用执行当天的官方文档、规范、论文或官方仓库；无法确认则删除、降级为工程推断或标明作者建议。
- 每篇恰好使用 3 张主题清晰的小型 Mermaid 图。单图控制在 12 个主要节点以内，优先使用纵向或近方形布局；不得用一张超大总览图塞满整篇。
- 每张图必须包含全局唯一的 `accTitle` 和 `accDescr`、语义 `classDef` 或 `style`，大分组保持白底；图前解释观察重点，图后解释关键路径、权衡或失败分支。
- Markdown 正文从 H2 开始，不允许 H1、原始 HTML、旧组织印记、危险协议或灰色大分组背景。
- 每篇必须与既有 6 篇及本批次已完成草稿复读去重；站内已有主文章负责完整解释，当前文章只写边界说明和相对链接。
- 用户负责上线后的浏览器视觉验收；本计划不声称代理完成桌面或手机视觉检查，也不因缺少浏览器检查阻塞部署。
- 保留根目录已有用户文件，不添加、不修改、不删除 `.claude/`、`docs/2026-06-29-platform-flywheel-review-design.md`、`registry.local.yaml`、`webui/public/ai-admin-logo.svg`、`webui/public/ai-fae-logo.svg`。

## 固定批次清单

| 顺序 | 目标文件 | slug | 标题 |
| --- | --- | --- | --- |
| 1 | `backend/app/ai_notes/content/02-agent-architecture/02-agent-identity-access-control.md` | `agent-identity-access-control` | Agent 身份与最小权限：代表谁、能做什么、如何审计 |
| 2 | `backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md` | `llm-inference-serving-engineering` | LLM 推理服务工程：吞吐、延迟、缓存、路由与成本 |
| 3 | `backend/app/ai_notes/content/04-ai-engineering/03-ai-cloud-native-runtime.md` | `ai-cloud-native-runtime` | AI × 云原生运行时：调度、弹性、发布与故障恢复 |
| 4 | `backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md` | `llm-agent-observability` | LLM / Agent 可观测性：从调用链到质量闭环 |
| 5 | `backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md` | `open-source-agent-runtime` | Hermes 与 OpenClaw：开源 Agent 运行时的设计边界 |
| 6 | `backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md` | `metabot-agent-control-bus` | MetaBot 架构：Agent 的多渠道远程控制总线 |
| 7 | `backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md` | `agent-framework-selection` | 主流 Agent 框架选型：从开发工具到生产运行时 |
| 8 | `backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md` | `intent-driven-ai-business-platform` | 意图驱动的 AI 业务平台：从固定旅程到受控执行 |

---

### Task 1: 建立隔离执行环境、研究台账和批次草稿合同

**Files:**
- Create: `docs/reviews/ai-notes-eight-article-source-review.md`
- Create: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Interfaces:**
- Consumes: `AiNotesRepository`、`validate_publication()`、现有 Mermaid 生产渲染辅助函数。
- Produces: 八篇固定目录、已完成草稿清单、发布候选副本校验器、草稿 Mermaid 真实渲染辅助函数和精读证据格式。

- [ ] **Step 1: 使用 `using-git-worktrees` 创建隔离分支**

从已提交本计划的 `master` 创建分支 `feat/ai-notes-eight-article-batch-20260828`。执行 worktree 技能的目录优先级与安全检查，记录绝对 worktree 路径；后续所有任务在该 worktree 中执行。确认根仓库用户自有未跟踪文件未进入 worktree 和提交。

- [ ] **Step 2: 运行基线门禁**

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_ai_notes_production_content.py -q
.venv/bin/python -m app.ai_notes.validate
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 现有 5 个分类、6 篇正式文章通过，全部既有正式 Mermaid 可真实渲染和清洗。若基线失败，先诊断现有问题，不把失败归因于尚未创建的八篇。

- [ ] **Step 3: 写批次测试辅助和第一个失败合同**

在 `backend/tests/test_ai_notes_eight_article_batch.py` 固定八篇相对路径、slug、标题、分类和顺序，同时维护一个随单篇提交增长的 `COMPLETED_BATCH_ARTICLES`：

```python
BATCH_ARTICLES = (
    ("02-agent-architecture/02-agent-identity-access-control.md", "agent-identity-access-control"),
    ("04-ai-engineering/02-llm-inference-serving-engineering.md", "llm-inference-serving-engineering"),
    ("04-ai-engineering/03-ai-cloud-native-runtime.md", "ai-cloud-native-runtime"),
    ("04-ai-engineering/04-llm-agent-observability.md", "llm-agent-observability"),
    ("03-tools-and-frameworks/02-open-source-agent-runtime.md", "open-source-agent-runtime"),
    ("03-tools-and-frameworks/03-metabot-agent-control-bus.md", "metabot-agent-control-bus"),
    ("03-tools-and-frameworks/04-agent-framework-selection.md", "agent-framework-selection"),
    ("05-thinking-and-methods/02-intent-driven-ai-business-platform.md", "intent-driven-ai-business-platform"),
)

COMPLETED_BATCH_ARTICLES: tuple[str, ...] = ()
```

测试辅助必须能：解析草稿 frontmatter 和正文；对 `COMPLETED_BATCH_ARTICLES` 逐篇断言 `draft is True`、作者、座右铭、标题、slug、分类、标签和日期合法；把已完成草稿的内存副本改为 `draft: false` 并与既有正式文章一起调用 `validate_publication()`。本任务只加入研究台账清单测试，要求 13 个源文件的绝对路径、行数和 SHA-256 精确存在；不提前加入“八篇必须都存在”的断言。

在前端集成测试中增加空的 `BATCH_DRAFT_RELATIVE_PATHS: string[]` 和 `draftBatchArticleFiles()`，再增加两条测试：列入清单但不存在的文件必须报错；清单中的实际草稿必须复用 `expectProductionDiagramsToRender()` 真实渲染。清单为空时没有待渲染草稿，因此通过；Task 2–9 每完成一篇就同时把路径加入清单，绝不把缺失文件静默过滤掉。

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 后端研究台账测试 RED，原因是台账尚不存在；前端辅助测试和既有正式文章测试为绿。

- [ ] **Step 4: 创建研究台账骨架并保持真实状态**

台账写入以下 13 个源文件的绝对路径、实际行数、SHA-256 和目标文章映射；“完整阅读”“保留”“删除”“事实核验”“去重”栏目初始统一写 `未开始`，不能提前勾选：

```text
2625  9df1b094bf6064974314e93985e65332803c500061a495f8fffc9f0408662ea5  身份认证与访问控制-深度理论知识.md
2140  c693c2e5ca30a47019191e001f0a8708beaed585dc03e7cdf67413e253efac01  身份认证与访问控制-理论架构设计.md
2483  f21f1316a7c66c5a5c920efe8c0f352dc2532af15d82265d35b58dfbab7dc784  AI-LLM系统架构深度指南.md
1550  c897f77ba9511c48358164c2c50b8f144703c983c0aa1dcedde9b20a6b145d8f  AI-LLM系统架构理论指南.md
124   9c04bf78390c08bbe3c89e685bf4f407434c6f96c0ec63828662db75239c0a07  ai-cloud-native-opportunity.md
250   4908263dc8ebdbe69106f50b8aa8f2b5030dd0a7e9fc5827945784e02dd31df4  Kubernetes与容器编排深度指南.md
1705  12361a569b863b0de58f43366420c822f5b8568b42f243c38aab16aa0eef53ff  Kubernetes与容器编排理论指南.md
1857  546ea435a32227f0ec75e73e946e45e4cb5a6e092ee55deaf7d456de3c43b8ed  可观测性与监控-深度理论知识.md
390   aac8a4c575a11ae1a129c3e03d49e6217d2c02a92363b0689d5c5306c70227ad  Hermes-Agent架构分析与思考.md
284   838ae6a89bfeca305bb70bc016f975d7b12f34abef9fe73dd4638c22dedb6961  Clawdbot架构理论指南.md
515   f526d770501328c0aa12bb926ae379c640bebb3cd9540fe4b569a581caebded5  MetaBot架构设计理论分析.md
323   4edd175b19b9ac82be0ac9a92ed10d69ddfe14cac7611fa7b3df9f0a5866054b  主流Agent框架深度分析-从架构本质到生产可用性.md
379   3a165ec7de6b712d9cbbc999ee6d7752b9954691f904f41a80d289bc0585d52b  干掉用户旅程-意图驱动的业务平台架构设计.md
```

用 `shasum -a 256` 和 `wc -l` 重新核对源文件；任一值变化时，更新台账和测试为执行时真实值并在提交说明变化，不使用旧值强行通过。

重新运行本任务测试。Expected: 后端台账合同和前端草稿发现辅助均 GREEN，完整测试套件不保留已知失败。

- [ ] **Step 5: 提交批次脚手架**

```bash
git add docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "test(ai-notes): define eight-article migration batch"
```

Expected: 提交后批次脚手架、既有生产内容和前端 Mermaid 测试全部为绿。

---

### Task 2: 迁移 Agent 身份与最小权限

**Files:**
- Create: `backend/app/ai_notes/content/02-agent-architecture/02-agent-identity-access-control.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-深度理论知识.md`：按 `1-400`、`401-800`、`801-1200`、`1201-1600`、`1601-2000`、`2001-2400`、`2401-2625` 行读到 EOF。
- `/Users/neo/Developer/personal/starship-blog-source/src/content/blog/身份认证与访问控制-理论架构设计.md`：按 `1-400`、`401-800`、`801-1200`、`1201-1600`、`1601-2000`、`2001-2140` 行读到 EOF。
- 复读：`backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`。

**Primary sources to verify on execution day:**
- RFC 8693 OAuth 2.0 Token Exchange: `https://www.rfc-editor.org/rfc/rfc8693`
- NIST SP 800-207 Zero Trust Architecture: `https://csrc.nist.gov/pubs/sp/800/207/final`
- SPIFFE overview: `https://spiffe.io/docs/latest/spiffe-about/overview/`
- OWASP LLM Excessive Agency: `https://genai.owasp.org/llmrisk/llm062025-excessive-agency/`

- [ ] **Step 1: 完成精读台账**

逐段记录两份旧稿哪些概念可保留、哪些传统登录/SSO/厂商 IAM 清单删除、哪些旧组织案例和日期删除、哪些内容已由企业级 Agent 架构主文章承担。记录四个一手来源的访问日期和它们分别支持的论点；不复制长引文。

- [ ] **Step 2: 先写文章内容合同并验证 RED**

把本篇加入后端 `COMPLETED_BATCH_ARTICLES` 和前端 `BATCH_DRAFT_RELATIVE_PATHS`，再新增断言：标题和 slug 精确匹配；标签为 `("Agent", "身份与权限", "安全治理")`；恰好 3 张 Mermaid；正文必须覆盖“用户委托”“工作负载身份”“Token Exchange”“最小权限”“审批绑定”“审计证据”；必须链接同分类的 `enterprise-agent-system-architecture`；不得出现 SSO 产品横评、旧组织标记和云厂商 IAM 功能清单。三张图的唯一标题固定为：

- `Agent 身份与委托链`
- `Agent 行动授权决策`
- `高风险操作审批闭环`

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k identity
```

Expected: RED，缺少目标 Markdown。

- [ ] **Step 3: 从空白正文完成文章**

正文按以下结构写作：问题与边界；三类主体（用户、Agent、工作负载）；委托链和短时凭证；资源/动作/条件授权；风险分级与审批绑定；工具凭证隔离；审计证据；落地清单。三张小图分别只画身份链、决策输入与结果、审批状态变化，单图不超过 10 个主要节点。

- [ ] **Step 4: 运行单篇 GREEN 与真实图渲染**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k identity
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 身份篇合同和 3 张图通过；其他七篇尚未加入已完成清单，不造成测试失败。

- [ ] **Step 5: 复读去重并提交**

逐节对比企业级 Agent 架构文章，将全景、状态机、信任层的完整解释留在旧文章；身份篇只深入委托、授权与审计。

```bash
git add backend/app/ai_notes/content/02-agent-architecture/02-agent-identity-access-control.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add agent identity and least privilege"
```

---

### Task 3: 迁移 LLM 推理服务工程

**Files:**
- Create: `backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `AI-LLM系统架构深度指南.md`：`1-400`、`401-800`、`801-1200`、`1201-1600`、`1601-2000`、`2001-2400`、`2401-2483`。
- `AI-LLM系统架构理论指南.md`：`1-400`、`401-800`、`801-1200`、`1201-1550`。
- 复读：`01-foundations/02-llm-application-system-architecture.md` 和 `04-ai-engineering/01-rag-retrieval-engineering.md`。

**Primary sources to verify:**
- PagedAttention paper: `https://arxiv.org/abs/2309.06180`
- vLLM official docs: `https://docs.vllm.ai/`
- TensorRT-LLM architecture: `https://nvidia.github.io/TensorRT-LLM/architecture/overview.html`
- SGLang official docs: `https://docs.sglang.ai/`

- [ ] **Step 1: 全文精读并记录边界**

台账区分应用系统请求链与模型服务内部数据路径。旧稿中的框架版本、吞吐倍数、GPU 型号结论和未来预测必须重新核验或删除；记录哪些段落与已上线 LLM 应用系统架构、RAG 文章重复。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签固定为 `("LLM", "推理服务", "AI 工程")`；关键概念包括 Prefill、Decode、连续批处理、KV Cache、量化、投机解码、路由、排队、容量与单位成本；恰好 3 张图，唯一标题：

- `LLM 推理请求数据路径`
- `连续批处理与 KV Cache 生命周期`
- `推理路由与容量决策`

要求链接 `../foundations/llm-application-system-architecture`，不得重复 RAG、工具调用、Prompt 编排或输出验证正文。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k inference`

Expected: RED，文件不存在。

- [ ] **Step 3: 写作与作图**

结构：服务目标与指标；Prefill/Decode 的资源差异；批处理与排队；KV Cache；模型压缩与投机执行；路由和多模型；容量测算；成本/延迟/质量权衡；上线清单。图 1 画请求到 token 流，图 2 画批次与缓存状态，图 3 画基于 SLO、队列、缓存和成本的路由决策。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k inference
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/04-ai-engineering/02-llm-inference-serving-engineering.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add LLM inference serving engineering"
```

---

### Task 4: 迁移 AI × 云原生运行时

**Files:**
- Create: `backend/app/ai_notes/content/04-ai-engineering/03-ai-cloud-native-runtime.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `ai-cloud-native-opportunity.md`：`1-124`。
- `Kubernetes与容器编排深度指南.md`：`1-250`，仅作通用概念辅助。
- `Kubernetes与容器编排理论指南.md`：`1-400`、`401-800`、`801-1200`、`1201-1600`、`1601-1705`，仅作通用概念辅助。
- 复读本批次推理服务篇，不复制其算法机制。

**Primary sources to verify:**
- Kubernetes scheduling: `https://kubernetes.io/docs/concepts/scheduling-eviction/`
- Kueue overview: `https://kueue.sigs.k8s.io/docs/overview/`
- KServe docs: `https://kserve.github.io/website/`
- Kubernetes device plugins: `https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/`

- [ ] **Step 1: 精读并删除 Kubernetes 百科倾向**

台账明确保留的只是一致性、调度、隔离、声明式发布和恢复概念；kubectl 命令百科、对象清单、厂商功能、旧版本行为不进入新文。核验 AI 工作负载的队列、设备资源和模型服务边界。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("AI 基础设施", "云原生", "运行时")`；关键概念包含 GPU/CPU 资源差异、调度、排队、模型制品、缓存、弹性、灰度、检查点、故障恢复；3 张图标题：

- `AI 云原生运行时分层`
- `模型制品到灰度发布链`
- `AI 工作负载故障恢复流程`

必须链接同分类的 `llm-inference-serving-engineering`；不得出现通用 Kubernetes 安装教程或把 HPA 描述成所有推理服务的万能策略。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k cloud_native`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：AI 工作负载为何不同；计算/显存/网络；队列与调度；镜像和模型制品；节点缓存；服务弹性；灰度与回滚；检查点与恢复；平台团队落地清单。三图分别覆盖分层、制品发布、失败恢复，不在一图混画。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k cloud_native
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/04-ai-engineering/03-ai-cloud-native-runtime.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add AI cloud native runtime"
```

---

### Task 5: 迁移 LLM / Agent 可观测性

**Files:**
- Create: `backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `可观测性与监控-深度理论知识.md`：`1-400`、`401-800`、`801-1200`、`1201-1600`、`1601-1857`。
- 对 Task 3 两份 LLM 源稿只复查与监控、评估、成本相关的已记录章节，不重新把系统架构搬入正文。
- 复读 LLM 应用系统架构、RAG、企业 Agent 架构三篇。

**Primary sources to verify:**
- OpenTelemetry GenAI semantic conventions: `https://opentelemetry.io/docs/specs/semconv/gen-ai/`
- OpenTelemetry GenAI attribute registry: `https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/`
- W3C Trace Context: `https://www.w3.org/TR/trace-context/`
- NIST Generative AI Profile: `https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence`

- [ ] **Step 1: 精读并建立 AI 证据链边界**

台账删除通用三支柱教材和具体监控产品配置，只保留与一次 AI 结果可追溯、可评估、可改进相关的部分。明确 OpenTelemetry GenAI 语义约定执行当天的稳定性状态；实验性属性不得写成永久字段合同。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("LLM", "Agent", "可观测性")`；必须覆盖 trace、模型/Prompt 版本、token、延迟、成本、检索证据、工具调用、质量评估、反馈集；3 张图标题：

- `LLM Agent 端到端证据链`
- `AI 可观测性分层信号模型`
- `线上反馈到离线评估闭环`

必须链接 `../foundations/llm-application-system-architecture`、同分类的 `rag-retrieval-engineering` 和 `../agent-architecture/enterprise-agent-system-architecture`；不得重复解释向量检索或 Agent 状态机。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k observability`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：为何传统服务监控不够；单次结果证据链；结构化 trace；模型、Prompt 和工具版本；质量与安全信号；成本和 SLO；评估集；人工反馈闭环；最小落地方案。图 1 只画一次调用，图 2 只画信号层级，图 3 只画改进闭环。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k observability
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/04-ai-engineering/04-llm-agent-observability.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add LLM and agent observability"
```

---

### Task 6: 迁移 Hermes 与 OpenClaw 开源 Agent 运行时

**Files:**
- Create: `backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `Hermes-Agent架构分析与思考.md`：`1-390`。
- `Clawdbot架构理论指南.md`：`1-284`。
- 复读 Claude Code 架构文章；以当前项目名 OpenClaw 重新核验旧稿中的 Clawdbot 表述。

**Primary sources to verify:**
- Hermes Agent official repository: `https://github.com/NousResearch/hermes-agent`
- OpenClaw official repository: `https://github.com/openclaw/openclaw`
- OpenClaw agent runtime concepts: `https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-runtimes.md`
- OpenClaw runtime architecture: `https://github.com/openclaw/openclaw/blob/main/docs/agent-runtime-architecture.md`

- [ ] **Step 1: 精读旧稿并检出官方源码快照**

记录两个官方仓库执行当天 HEAD、默认分支和读取的文档/代码路径。只对公开代码与文档作事实陈述；架构推断必须明确写“从公开结构可以推断”。旧名称、Star 数、版本、营销描述、无法复核的生产效果不进入文章。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("Agent", "开源运行时", "架构分析")`；必须覆盖 Agent loop、context、skills、tools、memory、sandbox、recovery 和 ownership boundary；3 张图标题：

- `开源 Agent 运行时共同循环`
- `Hermes 与 OpenClaw 能力责任映射`
- `Agent 运行时能力边界`

要求明确区分 provider、model、runtime、channel；不得写功能数量排行榜，不得把推断冒充官方实现。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k open_source_runtime`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：比较方法；共同运行循环；上下文与会话；Skills/Tools；记忆与学习；渠道与远程执行；沙箱/权限/恢复；两项目责任映射；选择边界。共同问题做主线，差异只写可核验的设计选择。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k open_source_runtime
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/03-tools-and-frameworks/02-open-source-agent-runtime.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): compare open source agent runtimes"
```

---

### Task 7: 迁移 MetaBot 多渠道远程控制总线

**Files:**
- Create: `backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `MetaBot架构设计理论分析.md`：`1-400`、`401-515`。
- 当前代码库 `/Users/neo/Developer/work/Orbbec-Agent-Team`：先读其 `AGENTS.md`/README/架构文档，再用 `rg --files` 定位渠道适配、消息归一化、会话绑定、命令状态、恢复、审计相关实现；记录实际 commit SHA 和所读文件。
- 复读当前平台与 MetaBot 集成相关代码和文档：`rg -n "MetaBot|metabot|relay|message bridge" backend webui docs`。

- [ ] **Step 1: 精读并建立事实分级**

台账把事实分为：当前代码直接证明、公开文档证明、作者工程推断。旧稿中的代码规模、旧组织部署拓扑、旧组件名和不能复核的实现细节全部删除。若当前代码与旧稿冲突，以当前代码为准并记录差异。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("Agent", "MetaBot", "远程控制")`；必须覆盖 channel adapter、message normalization、identity/session binding、persistent executor、command lifecycle、idempotency、reconnect、audit 和 remote-control risk；3 张图标题：

- `MetaBot 多渠道 Agent 控制平面`
- `远程命令持久状态机`
- `断线恢复与幂等闭环`

禁止出现旧组织名称、代码行数、部署规模或不在当前代码中的具体能力。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k metabot`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：远程控制问题；渠道边界；消息规范化；身份和会话绑定；控制总线；持久执行器；命令生命周期；断线恢复与幂等；审计和风险；适用/不适用边界。架构图只画责任层，状态图只画命令状态，恢复图只画重试和去重。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k metabot
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/03-tools-and-frameworks/03-metabot-agent-control-bus.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add MetaBot control bus architecture"
```

---

### Task 8: 迁移主流 Agent 框架选型

**Files:**
- Create: `backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `主流Agent框架深度分析-从架构本质到生产可用性.md`：`1-323`。
- 复读本批次 Hermes/OpenClaw、MetaBot 和既有 Claude Code 三篇，将它们作为不同产品形态的事实样本。

**Primary sources to verify:**
- LangGraph: `https://docs.langchain.com/oss/python/langgraph/overview`
- CrewAI: `https://docs.crewai.com/`
- Microsoft Agent Framework: `https://learn.microsoft.com/en-us/agent-framework/overview/agent-framework-overview`
- OpenAI Agents SDK: `https://openai.github.io/openai-agents-python/`
- Google ADK: `https://google.github.io/adk-docs/`
- Dify: `https://docs.dify.ai/`
- Coze Studio: `https://github.com/coze-dev/coze-studio`
- Claude Agent SDK: `https://platform.claude.com/docs/en/agent-sdk/overview`

- [ ] **Step 1: 精读并在执行当天冻结候选集**

逐个打开官方来源，记录访问日期、当前维护状态、定位和可确认的生产边界。若某项目已改名、弃用或被继任，文章使用当前状态，并把变化写入台账。比较产品形态与责任边界，不比较 Star 数、宣传口号或瞬时排行榜。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("Agent", "框架选型", "工程决策")`；必须覆盖 developer tool、orchestration library、agent runtime、end-to-end platform 四类形态，以及 control flow、state persistence、tool/permission、recovery、evaluation、deployment、observability、team ownership 八个维度；3 张图标题：

- `Agent 产品形态与责任边界`
- `生产级 Agent 能力矩阵`
- `Agent 框架选型决策树`

必须链接同分类的 `claude-code-architecture`、`open-source-agent-runtime`、`metabot-agent-control-bus`；不得给出永久排行榜或“最佳框架”绝对结论。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k framework_selection`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：先判断是否需要框架；四类产品形态；八维能力模型；候选框架按官方边界归类；团队所有权与锁定成本；从原型到生产的淘汰条件；决策树；评审清单。能力矩阵必须写成 Mermaid 或 Markdown 表格的可读组合，避免一张横向超宽图。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k framework_selection
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/03-tools-and-frameworks/04-agent-framework-selection.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add agent framework selection guide"
```

---

### Task 9: 迁移意图驱动的 AI 业务平台

**Files:**
- Create: `backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md`
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Source reading:**
- `干掉用户旅程-意图驱动的业务平台架构设计.md`：`1-379`。
- 复读企业级 Agent 系统架构和 AI Native 辅助架构设计。

**Primary sources to verify:**
- Anthropic, Building effective agents: `https://www.anthropic.com/research/building-effective-agents`
- NIST Generative AI Profile: `https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence`
- OpenAI practical guide to building agents: `https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf`

- [ ] **Step 1: 精读并消除绝对化叙事**

台账明确删除“消灭 UI”“完全自治”“自我进化取代流程”等表达，保留意图入口、能力目录、确定性流程与 Agent 边界、信任等级、人在环和完成证据。与企业 Agent 全景和 AI Native 协作方法重复的章节改为站内引用。

- [ ] **Step 2: 内容合同 RED**

把本篇加入两个已完成清单。标签为 `("AI Native", "业务平台", "意图驱动")`；必须覆盖 intent entry、capability catalog、deterministic workflow、agent boundary、trust tier、human-in-the-loop、completion evidence、progressive migration；3 张图标题：

- `意图驱动 AI 业务平台分层`
- `用户意图到完成证据执行链`
- `业务平台渐进演进路线`

必须链接 `../agent-architecture/enterprise-agent-system-architecture` 和同分类的 `ai-native-architecture-design`；不得宣称 UI、规则或确定性流程会全部消失。

Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k intent_driven`

Expected: RED。

- [ ] **Step 3: 写作与作图**

结构：固定旅程的边界；意图并非一句自然语言；能力目录；规划与确定性编排；信任等级；人在环；完成证据；渐进迁移；反模式；落地清单。图 1 画平台分层，图 2 画受控执行链，图 3 画从建议到有限自治的阶段路线。

- [ ] **Step 4: GREEN、渲染、去重、提交**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py -q -k intent_driven
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git add backend/app/ai_notes/content/05-thinking-and-methods/02-intent-driven-ai-business-platform.md docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "docs(ai-notes): add intent driven business platform"
```

---

### Task 10: 做整批跨文章去重、事实和图示审查

**Files:**
- Modify: 八篇新 Markdown（仅修复审查发现）
- Modify: `docs/reviews/ai-notes-eight-article-source-review.md`
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`

- [ ] **Step 1: 全文复读 14 篇并完成去重矩阵**

按分类顺序从头到尾阅读 14 篇正式/草稿文章，在台账加入“主题—唯一主文章—其他文章保留一句边界/站内链接”的最终矩阵。特别检查：Agent 全景、应用请求链、RAG、身份授权、推理性能、运行时包装、质量反馈、Claude Code、开源运行时事实和框架选型是否各有唯一主文章。

- [ ] **Step 2: 增加批次整体合同并验证 RED/GREEN**

测试八篇：

- `COMPLETED_BATCH_ARTICLES` 和前端 `BATCH_DRAFT_RELATIVE_PATHS` 都与固定八篇清单精确相等；
- 全部仍为 `draft: true`；
- 全部 frontmatter 发布候选副本一起校验后得到 5 分类、14 篇；
- 每篇恰好 3 张图，总计 24 张；
- 24 个 `accTitle` 和 `accDescr` 全局唯一；
- 每图有语义色，且无 `style <group> fill:#F8FAFC`；
- 正文无 H1、原始 HTML、`inkbot.cn`、`Ink Blog`、`STARSHIP`、`星舰`、旧公司名称、旧日期语句和危险链接；
- 八篇互链目标存在，不产生坏链；
- 台账每个源文件从 `未开始` 变成有章节级结论的 `已完成`，且每篇有一手来源访问日期和去重决定。

先引入断言并观察实际失败，再逐项修复，禁止降低断言迁就正文。

- [ ] **Step 3: 运行整批机器审查**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py tests/test_ai_notes_production_content.py -q
.venv/bin/python -m app.ai_notes.validate
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
cd ..
git diff --check
rg -n "inkbot\.cn|Ink Blog|STARSHIP|星舰|TODO|TBD|javascript:|data:text/html|^# " backend/app/ai_notes/content docs/reviews/ai-notes-eight-article-source-review.md
```

Expected: 后端批次和既有生产测试全绿；正式校验仍报告 6 篇，因为新文还是草稿；前端同时真实渲染既有正式图和 24 张草稿图；扫描只允许测试中作为禁止项出现的字面量，不允许新正文命中。

- [ ] **Step 4: 内容审查**

对八篇逐篇做事实—来源对应检查、图前图后解释检查、段落重复检查、绝对化措辞检查和中文可读性检查。Critical/Important 问题全部修完；若使用独立 code review，审查者必须同时看文章、台账和测试，不能只看 diff 语法。

- [ ] **Step 5: 提交整批审查修正**

```bash
git add backend/app/ai_notes/content docs/reviews/ai-notes-eight-article-source-review.md backend/tests/test_ai_notes_eight_article_batch.py
git commit -m "docs(ai-notes): refine eight-article publication batch"
```

若审查无修改，不创建空提交；在执行记录中注明门禁结果。

---

### Task 11: 同时解锁八篇正式发布

**Files:**
- Modify: 八篇新 Markdown
- Modify: `backend/tests/test_ai_notes_eight_article_batch.py`
- Modify: `backend/tests/test_ai_notes_production_content.py`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

- [ ] **Step 1: 写最终目录断言并验证 RED**

将生产目录精确期望改为：

```python
{
    "foundations": (
        "agent-engineering-learning-map",
        "llm-application-system-architecture",
    ),
    "agent-architecture": (
        "enterprise-agent-system-architecture",
        "agent-identity-access-control",
    ),
    "tools-and-frameworks": (
        "claude-code-architecture",
        "open-source-agent-runtime",
        "metabot-agent-control-bus",
        "agent-framework-selection",
    ),
    "ai-engineering": (
        "rag-retrieval-engineering",
        "llm-inference-serving-engineering",
        "ai-cloud-native-runtime",
        "llm-agent-observability",
    ),
    "thinking-and-methods": (
        "ai-native-architecture-design",
        "intent-driven-ai-business-platform",
    ),
}
```

执行时运行 `TZ=Asia/Shanghai date +%F`，把输出日期作为字面量固化到测试常量 `BATCH_PUBLISHED_ON`，再断言八篇 `publishedAt == updatedAt == BATCH_PUBLISHED_ON` 且日期不早于 `2026-05-25`。测试不得在后续运行时动态调用 `date.today()`。Run: `cd backend && .venv/bin/python -m pytest tests/test_ai_notes_production_content.py -q`。Expected: RED，正式目录仍只有 6 篇。

- [ ] **Step 2: 一次性切换八篇 frontmatter**

读取执行当天上海时区日期，把八篇 `draft` 同时改为 `false`，并把八篇 `publishedAt`、`updatedAt` 同时设为该日期。不得分多次提交或保留某篇草稿。

- [ ] **Step 3: 移除草稿期专用发现逻辑**

删除前端 `draftBatchArticleFiles()` 及专用草稿测试，让现有 `publishedArticleFiles()` 自动发现 14 篇正式文章和全部 Mermaid。批次后端测试改为断言八篇 `draft is False`，删除“临时改 draft”的迁移期辅助逻辑，保留元数据、内容边界、互链、24 张图和台账合同。

- [ ] **Step 4: 运行正式发布门禁**

```bash
cd backend
.venv/bin/python -m pytest tests/test_ai_notes_eight_article_batch.py tests/test_ai_notes_production_content.py -q
.venv/bin/python -m app.ai_notes.validate
cd ../webui
npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx
```

Expected: 5 分类、14 篇正式文章；八篇同日发布；正式文章自动发现测试真实渲染全部新图，不再依赖显式草稿列表。

- [ ] **Step 5: 提交单一发布开关**

```bash
git add backend/app/ai_notes/content backend/tests/test_ai_notes_eight_article_batch.py backend/tests/test_ai_notes_production_content.py webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "feat(ai-notes): publish eight engineering articles"
```

---

### Task 12: 全量验证、独立审查与合并

**Files:**
- Verify all changed files; modify only to fix traced failures or review findings.

- [ ] **Step 1: 使用 `verification-before-completion` 运行完整本地门禁**

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
.venv/bin/python -m app.ai_notes.validate
cd ../webui
npm test -- --run
npm run build
npm audit --audit-level=high
cd ..
deploy/cloud/acceptance.sh local
git diff --check
```

Expected: 后端和前端零失败、生产构建成功、无 high/critical 审计问题、本地云发布门禁成功、内容验证精确为 5 分类/14 篇。

- [ ] **Step 2: 运行最终内容扫描和提交边界检查**

```bash
rg -n "inkbot\.cn|Ink Blog|STARSHIP|星舰|TODO|TBD|javascript:|data:text/html|^# " backend/app/ai_notes/content docs/reviews/ai-notes-eight-article-source-review.md
git status --short --branch
git log --oneline --decorate master..HEAD
git diff --stat master...HEAD
git diff --check master...HEAD
```

Expected: 正文和台账无旧品牌、占位符、危险协议、H1；工作树只有已知用户未跟踪文件或完全干净；提交序列包含批次脚手架、八篇单篇提交、可选审查修正和单一发布提交。

- [ ] **Step 3: 使用 `requesting-code-review` 做独立审查**

审查范围必须覆盖：八篇正文质量、研究台账证据、事实与来源边界、跨文章去重、相对链接、Mermaid 可读性/唯一元数据、草稿到正式的一次性开关、测试是否能防止回归。修复所有 Critical 和 Important 问题并重跑受影响门禁；修复提交使用 `fix(ai-notes): address batch publication review`。

- [ ] **Step 4: 使用 `finishing-a-development-branch` 快进集成**

执行前获取远端并确认 `master` 没有来自其他会话的新提交。若有新提交，先把隔离分支基于最新 `origin/master` 重放或合并并重跑完整门禁。随后在根仓库：

```bash
git fetch origin
git switch master
git pull --ff-only origin master
git merge --ff-only feat/ai-notes-eight-article-batch-20260828
```

Expected: 快进成功；用户自有未跟踪文件保持原样。若不能快进，停止并重新协调分支，不强制覆盖。

---

### Task 13: 推送、受控部署和生产证据

**Files:**
- No product file changes unless a deployment failure traces to repository code.

- [ ] **Step 1: 最终发布前置条件**

推送可在根仓库完成，但部署命令必须回到干净的隔离 worktree 执行；根仓库保留的用户未跟踪文件会触发 `deploy.sh` 的 fail-closed 工作树检查，不能删除这些文件来迎合部署脚本。

```bash
git status --porcelain
git rev-parse HEAD
git rev-parse origin/master
```

Expected: 在隔离 worktree 中输出为空，所有本批次跟踪文件已提交。先在根仓库执行 `git push origin master`，再回到隔离 worktree确认其 HEAD 与 `origin/master` 精确一致；`deploy/cloud/deploy.sh` 自身还会重复此检查。

- [ ] **Step 2: 运行受控部署**

按照 `docs/runbooks/cloud-platform.md` 使用现有 owner-only、权限 `0600` 的生产 `deploy.env`。执行环境把已确认的绝对路径放入任务专用变量 `CLOUD_DEPLOY_ENV`；先只读检查变量非空、文件不是符号链接、所有者为当前用户、权限为 `0600`，再从干净隔离 worktree 执行：

```bash
test -n "${CLOUD_DEPLOY_ENV:-}"
test -f "$CLOUD_DEPLOY_ENV" && test ! -L "$CLOUD_DEPLOY_ENV"
test "$(stat -f '%Lp' "$CLOUD_DEPLOY_ENV")" = 600
deploy/cloud/deploy.sh "$CLOUD_DEPLOY_ENV"
```

Expected exact output: `CLOUD_PLATFORM_DEPLOY_OK release=<HEAD 的 40 位提交> mode=dingtalk`。`CLOUD_DEPLOY_ENV` 在执行时从现有私有发布配置解析，不把秘密路径或内容写入仓库、日志或计划修订。

- [ ] **Step 3: 运行正式验收并收集最小证据**

按 runbook 对同一个 owner-only 配置执行最终验收：

```bash
deploy/cloud/acceptance.sh final "$CLOUD_DEPLOY_ENV"
```

Expected exact aggregate: `CLOUD_PLATFORM_ACCEPTANCE_OK release=<同一 HEAD> criteria=18`。此外只读核验：远端 `current` 指向同一 release；Platform 六个 Compose 服务均 healthy；公开健康接口返回 200；运行容器内内容校验报告 5 分类、14 篇；生产前端资产包含当前 Mermaid 白底与图片交互实现。

- [ ] **Step 4: 处理不确定部署状态**

若部署命令在远端操作后超时或连接中断，不立即重跑。先按 `docs/runbooks/agent-execution-relay.md` 检查 release、部署锁、current symlink、容器和 deploy journal，确定是未切换、已成功还是需要精确回滚。不得绕过发布锁或手工覆盖 `current`。

- [ ] **Step 5: 交付用户验收**

向用户报告：八篇标题和 URL、共同发布日期、最终 release SHA、5 分类/14 篇校验、六服务健康和公开健康结果；明确“浏览器桌面/手机视觉验收由你检查，我没有代替你声明已看过”。上线后若用户发现字号、宽度、图尺寸、颜色或交互问题，作为发布后修正继续处理。

- [ ] **Step 6: 完成目标**

只有 Task 13 的生产证据全部成立后，调用目标状态工具标记 complete；若目标有显式 token budget，向用户报告工具返回的最终 token 用量。随后按 worktree 技能清理已合并的隔离 worktree 和分支，不触碰用户未跟踪文件。

## 完成证据清单

- [ ] 13 个源文件的行数、SHA-256、完整阅读结论和一手资料核验均记录在台账。
- [ ] 八篇正文均由苍渊署名、同一座右铭、同日正式发布。
- [ ] 八篇每篇 3 张小图，共 24 张，真实 Mermaid 渲染和 SVG 清洗通过。
- [ ] 14 篇全文跨文去重完成，相对链接无坏链。
- [ ] 内容校验精确报告 5 分类、14 篇。
- [ ] 后端全量、前端全量、生产构建、依赖审计和本地云门禁通过。
- [ ] Critical/Important 审查问题为零。
- [ ] `master == origin/master == production release`。
- [ ] 六个生产服务 healthy，公开健康接口 200，容器内为 14 篇。
- [ ] 已告知用户浏览器视觉验收由用户本人完成。
