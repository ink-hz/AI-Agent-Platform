# Orbbec AI Agent Platform — v1 设计文档

- 日期: 2026-06-24
- 状态: 待评审 (Draft, pending user review)
- 仓库: `/home/ink/Orbbec/AI-Agent-Platform`(新建独立仓库,与 AI-FAE-Agent / AI-ADMIN-Agent / Jarvis 平级)

---

## 1. 背景与目标

公司内部已有两个**独立、各自生产化**的业务 Agent:

- **AI-FAE-Agent**(技术支持):FAE 技术问答、产品规格、SDK、排障、经验库;有 WebUI、PostgreSQL 数据飞轮、session/trace/feedback、Review Center、阿里云部署。
- **AI-ADMIN-Agent**(行政):行政制度、办公流程、会议室/差旅/办公用品/停车/快递;有独立 WebUI 和后端(in-memory session,无 DB,暂无部署配置)。

问题:两个 Agent 是孤岛,入口分散,配置/部署/数据/治理各自为政。

**目标:** 建立 **Orbbec AI Agent Platform**——一个"门面 + 平台底座",让不同 Agent 被**统一接入、统一展示、统一治理**;但**不粗暴合并**两个仓库的业务逻辑。第一批接入 FAE 与 ADMIN,后续可扩展 Sales / HR / IT / Quality 等。

**核心理念:** 平台采用**联邦(federation)模型**——把各 Agent 当作**独立的外部产品**登记并路由,而不是把它们的逻辑吸收进平台内部。这与 Jarvis 运行时"向内吸收(把 Agent 实现成进程内子 Agent)"的方向**不同**,因此平台独立建仓,不放进 Jarvis,也不放进 FAE 仓库。

---

## 2. 范围(v1)

### 2.1 v1 交付物(做)

1. **Agent Registry**:`registry.yaml` 作为唯一真相源,登记每个 Agent 的名称、图标、业务域、入口 URL、负责人、健康检查地址、状态等。
2. **Portal**:一个 React/Vite 卡片式门户页,展示所有已接入 Agent,点击进入对应 Agent。
3. **健康检查**:`platform-api`(FastAPI)在**服务端**轮询各 Agent 的 `/health`,归一化 + 缓存,前端展示在线/离线/指标。
4. **进入方式**:点击卡片 → 浏览器直接跳转到该 Agent 在注册表中登记的**外部入口地址**(v1 不做反向代理)。
5. **Gateway 规划**:输出统一路由方案 + Nginx 样例配置(`deploy/nginx.platform.conf.example`),**只出规划样例,不真正接入**。

### 2.2 v1 不做(明确边界)

- 不做真正打通的统一网关反向代理(仅出规划)。
- 不合并知识库、不合并 session、不合并业务工作流。
- 不做跨 Agent 自动编排 / Coordinator。
- **不修改 AI-FAE-Agent 与 AI-ADMIN-Agent 的任何代码**(健康检查走服务端,绕开 CORS;无需两个 Agent 配合)。
- 不做配置中心、数据飞轮、Review/Eval 的实现(仅在仓库结构中留好挂载点)。
- 不做鉴权/SSO(留待后续阶段)。

### 2.3 设计原则

- **YAGNI**:v1 只写 `registry` 与 `health` 两块真实代码;后续阶段目录(`_reserved/`)只放 README 占位,不写未用代码。
- **为平台演进留位**:`platform-api` 即未来 Config Center / Data Flywheel / Review / Eval / Gateway 的种子,每个后续阶段 = 新增一个 router/模块。
- **接口先于实现**:注册表 schema、Agent 健康/状态契约先定清楚,实现可演进(文件 → DB)。

---

## 3. 两个 Agent 现状(集成事实)

| 维度 | AI-FAE-Agent | AI-ADMIN-Agent |
|---|---|---|
| 框架 | FastAPI(:8000)+ React/Vite | FastAPI(:8000)+ React/Vite |
| WebUI 路径 | `/app/`(Vite `base=/app/`) | `/app/`(Vite `base=/app/`) |
| 健康端点 | `GET /health` → `{status, qa_indexed, products_loaded}` | `GET /health` → `{status, llm_provider, llm_model, chunks_loaded, documents_loaded, ...}` |
| 聊天 | `POST /chat`(SSE)、`/feedback`、`/history` | 同左 |
| 数据层 | PostgreSQL 数据飞轮 + Review Center | in-memory session,无 DB |
| 部署 | docker-compose + nginx + 阿里云指南 | 暂无 Docker/nginx/部署配置 |
| 版本 | App version 硬编码 `0.1.0`(未在 /health 暴露) | App version `0.1.0`(未在 /health 暴露) |

**两个关键约束:**

1. **端口/根路径冲突**:两个 Agent 默认都想占 80 / `/app/`,同机直接冲突——所以统一路由必须由平台/网关掌控。
2. **`/app/` base 写死进构建**:Vite 把 `base:'/app/'` 编进产物,naive 的 `/fae/` 前缀代理无法自动改写静态资源路径——这是 Gateway 阶段必须处理的点(见 §8)。

---

## 4. 总体架构(v1)

```
                    ┌─────────────────────────────────────┐
                    │   Orbbec AI Agent Platform (新仓库)   │
                    │                                       │
   浏览器  ───────► │  Portal 前端 (React/Vite, base=/)     │
                    │       │ GET /api/agents                │
                    │       │ GET /api/agents/health          │
                    │       ▼                                │
                    │  platform-api (FastAPI)               │
                    │   ├─ registry  ← registry.yaml         │
                    │   └─ health poller(服务端轮询+缓存)    │
                    └───────────┬──────────────┬────────────┘
                       /health  │              │ /health
                  (服务端调,非浏览器,绕开 CORS)│
                    ┌───────────▼──┐     ┌─────▼─────────┐
                    │ AI-FAE-Agent │     │ AI-ADMIN-Agent│   ← 完全不改动
                    │ :8000 /app/  │     │ :8000 /app/   │
                    └──────────────┘     └───────────────┘
                         ▲                      ▲
   浏览器点击卡片 ───────┴──────────────────────┘
   (v1 直接跳各 Agent 注册表登记的外部入口地址)
```

数据流:
1. 浏览器加载 Portal 前端 → 调 `platform-api` 的 `GET /api/agents` 拿卡片数据。
2. 前端每 ~30s 调 `GET /api/agents/health` 刷新状态徽标。
3. `platform-api` 后台任务每 ~30s 在服务端轮询各 Agent 的 `health.url`,归一化后缓存;health 路由只读缓存(快、稳、不阻塞)。
4. 用户点"进入" → 浏览器新开标签跳到该 Agent 的 `entry_url`。

---

## 5. 仓库结构

```
AI-Agent-Platform/
├── README.md
├── registry.yaml                  # ★ v1 唯一真相源
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口,挂载 router + 启动 health poller
│   │   ├── config.py              # 平台配置(端口、轮询间隔、registry 路径)
│   │   ├── registry/              # ★ 阶段1:Agent Registry
│   │   │   ├── models.py          #   AgentEntry (pydantic)
│   │   │   ├── repository.py      #   Repository 接口 + YamlRepository(以后可换 DB)
│   │   │   └── routes.py          #   GET /api/agents, GET /api/agents/{id}
│   │   ├── health/                # ★ 阶段3 雏形:健康状态
│   │   │   ├── poller.py          #   asyncio 后台轮询 + 内存缓存
│   │   │   ├── normalizer.py      #   各 Agent 异构 /health 归一化(按 type)
│   │   │   └── routes.py          #   GET /api/agents/health, /api/agents/{id}/health
│   │   ├── _reserved/             # 后续阶段占位(仅 README,不写代码)
│   │   │   ├── config_center/README.md
│   │   │   ├── flywheel/README.md
│   │   │   ├── review/README.md
│   │   │   └── gateway/README.md
│   │   └── static/               # 前端构建产物(由部署步骤拷入)
│   ├── tests/
│   └── requirements.txt
├── webui/                         # React/Vite,与 FAE/ADMIN 同款
│   ├── src/
│   └── vite.config.ts             # base='/'(Portal 在根路径)
└── deploy/
    ├── nginx.platform.conf.example # ★ 阶段2 路由规划样例
    └── docker-compose.example.yml
```

---

## 6. 注册表 Schema —— 平台核心契约 `registry.yaml`

```yaml
version: 1
agents:
  - id: fae                          # 稳定标识;后续所有数据按它隔离(agent_id)
    name: "AI FAE Agent"
    domain: "技术支持"
    description: "FAE 技术问答 / 产品规格 / SDK / 排障 / 经验库"
    icon: "🛠️"                        # v1 用 emoji 或图标 URL
    owner: "<负责人>"
    env: "prod"                      # dev | prod(同一份可登记不同环境实例)
    status: "active"                 # active | maintenance | offline(人工开关)
    entry_url: "http://<fae-host>/app/"   # ★ 用户点击跳转地址(外部可达)
    health:
      url: "http://<fae-host>/health"     # 服务端轮询地址(可内网)
      type: "fae"                         # 决定用哪个 normalizer
    # —— 后续阶段预留,v1 可留空 ——
    api_base: "http://<fae-host>"         # 阶段2 Gateway / 阶段4 飞轮用
    version: ""                           # 可手填;未来由 /status 自动获取
    tags: ["pg-flywheel", "review-center"]

  - id: admin
    name: "AI ADMIN Agent"
    domain: "行政"
    description: "行政制度 / 办公流程 / 会议室 / 差旅 / 办公用品 / 停车 / 快递"
    icon: "🏢"
    owner: "<负责人>"
    env: "prod"
    status: "active"
    entry_url: "http://<admin-host>/app/"
    health:
      url: "http://<admin-host>/health"
      type: "admin"
    api_base: "http://<admin-host>"
    version: ""
    tags: ["in-memory-session"]
```

**字段说明:**
- `id`:全局稳定标识,未来数据飞轮/Review 按它隔离(`agent_id`)。
- `entry_url` vs `health.url`:前者给浏览器跳转(必须外部可达),后者给平台服务端轮询(可走内网),两者解耦。
- `health.type`:让归一化器知道如何解读异构字段。新增 Agent 时:`registry.yaml` 加一段 + 选/写一个 normalizer。

**校验:** 启动时用 pydantic 严格校验 `registry.yaml`;字段缺失/格式错误则 **fail fast**,打印清晰错误并拒绝启动。

---

## 7. 平台 API(`platform-api`,FastAPI)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agents` | 列出所有 Agent(对外字段:id/name/domain/description/icon/owner/env/status/entry_url/tags;**不**暴露 `health.url` 等内部地址) |
| GET | `/api/agents/{id}` | 单个 Agent 详情 |
| GET | `/api/agents/health` | 批量返回所有 Agent 的归一化健康(供卡片墙一次拉取) |
| GET | `/api/agents/{id}/health` | 单个 Agent 归一化健康(读缓存) |
| GET | `/api/health` | 平台自身健康(`{status:"ok"}`) |

**归一化健康响应(统一形状):**
```json
{
  "id": "fae",
  "online": true,
  "checked_at": "2026-06-24T10:00:00Z",
  "latency_ms": 42,
  "version": null,
  "metrics": [
    {"label": "QA 索引", "value": "已加载"},
    {"label": "产品数", "value": 42}
  ],
  "raw": { "...": "原始 /health 响应,便于排查" }
}
```
- `online`:服务端轮询在超时内拿到 HTTP 200 即为 true。
- `metrics`:由对应 normalizer 从原始响应提炼的 2~4 个关键指标(FAE: qa_indexed/products_loaded;ADMIN: llm_model/chunks_loaded/documents_loaded)。
- `version`:v1 多数为 null(两个 Agent 未在 /health 暴露版本);可由 `registry.yaml.version` 兜底显示。

---

## 8. 健康轮询与归一化

- **Poller**(`health/poller.py`):FastAPI 启动时拉起一个 asyncio 后台任务,每 `POLL_INTERVAL_SECONDS`(默认 30s)用 httpx 并发轮询所有 `health.url`,单个超时 ~3s,结果(成功/失败 + 时间戳 + 延迟 + 原始 body)写入**进程内缓存**(dict)。失败按 Agent 隔离,不影响其它 Agent,也不会崩溃平台。
- **Normalizer**(`health/normalizer.py`):按 `health.type` 选择解析器,把异构原始响应 → 统一 `metrics` 列表。提供 `fae`、`admin` 两个内置解析器 + 一个 `generic`(只判 online,不提炼指标)兜底。
- **缓存策略**:health 路由只读缓存,永不在请求里现敲 Agent(快、稳)。缓存为空(刚启动)时返回 `online:null` 表示"检测中"。

**前瞻性接口建议(写入 README,非 v1 实现):** 建议各 Agent 后续增加一个标准 `GET /platform/status` 契约,返回统一结构 `{agent_id, version, model, kb_updated_at, status}`,这样平台可统一拿到版本/模型/知识库更新时间,不再依赖逐个 normalizer。v1 先用现有 `/health` + normalizer 兼容,**不强制 Agent 改造**。

---

## 9. 前端 Portal(React/Vite)

- 单页:顶部标题 **"Orbbec AI Agent"** + 副标题;下方 **卡片网格**。
- **卡片**:图标 + 名称 + 业务域标签 + 描述 + 负责人 + **状态徽标**(在线/离线/维护中,综合 `registry.status` 与健康轮询) + 2~4 个指标 chip + **「进入」按钮**(新标签打开 `entry_url`)。
- 前端启动调 `/api/agents` 渲染卡片;每 30s 调 `/api/agents/health` 刷新徽标与指标。
- `/api` 不可用时显示降级横幅,不白屏。
- `vite.config.ts` 设 `base:'/'`(Portal 在根路径);开发态 proxy `/api` → `platform-api`。
- UI 中文,风格简洁,与两个 Agent 的视觉保持基本一致。

---

## 10. Gateway 规划(阶段2,仅出样例,不接)

**核心难点:`/app/` base 写死。** 两个 Agent 的 Vite 产物把 `base:'/app/'` 编进 HTML/JS,naive 的 `/fae/` 前缀反代会导致静态资源 404。两条可行路线:

- **路线 A(近期推荐,零改 Agent):基于 host 路由。** `fae.orbbec.internal` → FAE,`admin.orbbec.internal` → ADMIN,`portal.orbbec.internal` / 根 → Portal。各 Agent 仍在自己的 `/app/`,无需重构建。v1 的 `entry_url` 直接填这些 host 即可,平滑过渡到阶段2。
- **路线 B(路径路由,需改 Agent):** 各 Agent 用 `base:/fae/app/`、`/admin/app/` 重新构建 + nginx 前缀代理。打通 `/fae/app/` 形式,但**会动到 Agent 仓库**,留待 Agent 方愿意配合时再做。

`deploy/nginx.platform.conf.example` 同时给出:Portal 挂根路径 + `/api` 反代到 `platform-api` 的**真实可用配置**,以及路线 A/B 的**注释样例**。

---

## 11. 部署

- `platform-api`:**端口 80**,单进程同时服务 Portal 静态页(`/`)和 API(`/api/*`)——与 FAE/ADMIN"FastAPI 一个进程服务 WebUI + 接口"同款。v1 不强制需要 nginx。
- 端口 80 由**平台所在主机**独占;FAE/ADMIN 各在自己的主机上(`entry_url` 是不同 host),互不冲突。
- 前端构建产物拷贝/软链到 `backend/app/static`,由 `platform-api` 在 `/` 直接服务。
- 两个 Agent 维持现状,不动。
- `deploy/nginx.platform.conf.example` + `deploy/docker-compose.example.yml`:作为阶段2网关/容器化样例(v1 可不启用;Agent 不纳入编排,保持独立部署)。

---

## 12. 错误处理

| 场景 | 处理 |
|---|---|
| `registry.yaml` 缺失/格式错 | 启动 fail fast,打印清晰错误,拒绝启动 |
| 某 Agent `/health` 超时/不可达 | 标记该 Agent `online:false`,卡片显示离线徽标;隔离,不影响其它 Agent / 不崩平台 |
| 归一化器遇到非预期字段 | 退回 `generic`(只判 online),`metrics` 为空;原始响应仍存 `raw` 便于排查 |
| 前端 `/api` 不可用 | 显示降级横幅,保留上次数据(若有) |

---

## 13. 测试

**后端(pytest):**
- `registry`:正常 YAML 解析、字段校验、缺字段/坏格式 fail fast。
- `normalizer`:用 FAE / ADMIN 真实样例 `/health` payload 断言归一化输出;未知 type 走 generic。
- `health` 路由:读缓存返回正确形状;缓存空时 `online:null`;未知 id → 404。
- `/api/agents`:返回形状正确,且**不泄露** `health.url` 等内部字段。
- Agent `/health` 用 fake/respx 桩,不依赖真实 Agent。

**前端:** v1 从简——一个渲染冒烟测试(卡片列表渲染、离线徽标显示);不追求覆盖率。

---

## 14. 平台演进路线(后续阶段挂载点)

| 阶段 | 内容 | 落点 |
|---|---|---|
| 1(v1) | 统一 Portal + Agent Registry + 健康 | `registry/`、`health/`、`webui/` |
| 2 | 统一 Gateway(host 或 path 路由) | `_reserved/gateway/` + `deploy/nginx.*` |
| 3 | 统一配置/状态展示(版本/模型/KB 时间) | `_reserved/config_center/` + Agent `/platform/status` 契约 |
| 4 | 统一数据飞轮(按 `agent_id` 隔离的标准 session/QA/feedback/trace 结构) | `_reserved/flywheel/` |
| 5 | 统一 Review / Eval / 发布门禁 | `_reserved/review/` |
| 6 | 跨 Agent 编排(Coordinator) | `_reserved/gateway/` 或独立模块 |

每个后续阶段 = 在 `platform-api` 新增一个 router/模块 + 扩展 `registry.yaml` 字段,**不回头重构 v1 结构**。

---

## 15. 评审决议(已确认)

1. ✅ 前端定为 **React/Vite**(与两个 Agent 同款)。
2. ✅ 注册表加 `env` 与 `version` 字段;`_reserved/` 采用"仅 README 占位"。
3. ✅ `entry_url` / `health.url` 主机地址 **v1 先用占位 `<fae-host>`/`<admin-host>`,部署时填实际地址**。
4. ✅ `platform-api` 入口端口 **80**(单进程服务 Portal + API,详见 §11)。
