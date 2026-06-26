# Orbbec AI Agent Platform

企业内部 AI Agent 统一门面与平台底座。平台采用联邦模型：把各业务 Agent（FAE / ADMIN / ...）当作独立外部产品登记、展示、路由和治理，不合并其知识库、session 或业务逻辑。

- 设计文档：`docs/2026-06-24-orbbec-ai-agent-platform-v1-design.md`
- 实现计划：`docs/2026-06-24-orbbec-ai-agent-platform-v1-plan.md`

## v1 能力

- Agent Registry：`registry.yaml` 作为唯一真相源。
- Portal 卡片门户：React / Vite。
- 服务端健康检查：轮询各 Agent `/health`，归一化并缓存。
- 点击卡片跳转各 Agent 外部入口。

## 本地运行

后端：

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PLATFORM_REGISTRY_PATH=../registry.yaml PLATFORM_PORT=8000 \
  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

前端开发态：

```bash
cd webui
npm install
npm run dev
```

前端开发服务器默认在 `http://localhost:5173`，`/api` 代理到 `http://localhost:8000`。

如果本机设置了 `http_proxy` / `https_proxy`，本地 smoke curl 建议加 `--noproxy '*'`，避免请求 `127.0.0.1` 时被代理拦截。

生产部署时，先执行：

```bash
cd webui
npm run build
```

然后把 `webui/dist` 作为 `PLATFORM_STATIC_DIR`，由 platform-api 同时服务 Portal 与 API。

## 接入新 Agent

在 `registry.yaml` 增加一段：

- `id`
- `name`
- `domain`
- `entry_url`
- `health.url`
- `health.type`

`health.type` 可选 `fae`、`admin`、`generic`，也可以在 `backend/app/health/normalizer.py` 增加新的解析器。

## 测试

后端：

```bash
cd backend
. .venv/bin/activate
pytest
```

前端：

```bash
cd webui
npm test
npm run build
```
