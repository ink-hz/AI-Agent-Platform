# Orbbec Agent Platform

面向 11 个 Agent 的只读观测平台：9 个本机 MetaBot，以及阿里云上的 AI FAE Agent 和 AI ADMIN Agent。Platform 展示运行状态、真实使用量、Sessions、逐轮问答、Evidence、Feedback、Review、Trace 和 Flywheel 改进项，不提供 Agent 入口，也不执行重启、Review 或发布操作。

## 当前能力

- 自动读取 `deploy/metabot.runtime-contract.json` 中的业务 Bot 和 test-bot。
- 分钟级探测本机 MetaBot、AI FAE 和后台运行的 AI ADMIN/DingTalk 服务。
- 展示全部 11 个 Agent 的运行状态、运行时长、Sessions 和 Conversations。
- 本机 MetaBot 业务数据直接读取；FAE 与 ADMIN 业务数据每天只读同步一次。
- 统一查看 Session、Question、Answer、Evidence、Feedback、Review 和改进候选。
- FAE 提供脱敏后的 Stage/Span Trace；ADMIN 未采集的工程 Trace 明确标记为 unavailable。
- 契约读取失败时保留最后一次有效实例列表。
- Platform API 暂时失败时，页面保留最后一次成功快照并提示数据可能过期。

健康状态与业务数据的新鲜度相互独立：健康异常不影响上一份业务快照，远端同步失败也不会清空上一份成功数据。

## 数据源

默认运行契约：

```text
/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json
```

可通过 `PLATFORM_METABOT_CONTRACT_PATH` 指定其他契约文件。后端会在轮询时重新读取契约，新增、删除或修改实例不需要重启 Platform。

## 本地开发

后端要求 Python 3.11+：

```bash
cd backend
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PLATFORM_REGISTRY_PATH=../registry.local.yaml \
PLATFORM_METABOT_CONTRACT_PATH=/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json \
PLATFORM_CLUSTER_POLL_INTERVAL=10 \
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

前端：

```bash
cd webui
npm ci
npm run dev
```

开发入口为 `http://127.0.0.1:5173/`，Vite 将 `/api` 代理到 `http://127.0.0.1:8000`。

## 本机生产运行

构建前端：

```bash
cd webui
npm run build
```

LaunchAgent 配置位于：

```text
deploy/com.orbbec.ai-agent-platform.plist
```

安装后，FastAPI 同源提供仪表盘与 API：

- 仪表盘：`http://127.0.0.1:8000/`
- Platform 健康：`http://127.0.0.1:8000/api/health`
- 集群快照：`http://127.0.0.1:8000/api/cluster/status`
- Agent 目录：`http://127.0.0.1:8000/agents`
- Sessions：`http://127.0.0.1:8000/sessions`
- Flywheel：`http://127.0.0.1:8000/flywheel`

查看服务状态：

```bash
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform
```

只重启 Platform：

```bash
launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform
```

日志位置：

```text
/Users/neo/Library/Logs/OrbbecAI-Agent-Platform.stdout.log
/Users/neo/Library/Logs/OrbbecAI-Agent-Platform.stderr.log
```

## 远端每日同步

手动执行一次 FAE 与 ADMIN 同步：

```bash
deploy/sync-remote-agents
```

安装每天 03:20 执行的 LaunchAgent：

```bash
deploy/install-sync-launchagent.sh
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform-sync
```

同步日志：

```text
/Users/neo/Library/Logs/OrbbecAI-Agent-Platform-Sync.stdout.log
/Users/neo/Library/Logs/OrbbecAI-Agent-Platform-Sync.stderr.log
```

同步只通过 SSH 执行只读导出，再原子写入本机隔离镜像。失败时 `/api/sync/status` 会标记 failed，页面继续展示上一份成功快照。

Langfuse 保持独立访问，不嵌入 Platform：

```bash
ssh -i ~/.ssh/orbbec_aliyun_ed25519 -L 3001:127.0.0.1:3001 root@47.106.112.69
```

随后打开 `http://127.0.0.1:3001`。

## API

### `GET /api/cluster/status`

返回集群摘要、运行契约状态和实例快照。实例固定按离线、异常、检测中、健康排序，同状态按端口升序。

### `GET /api/health`

仅表示 Platform 自身进程可用，不代表全部 MetaBot 健康。

### Observability API

- `GET /api/fleet/overview`: 11-Agent 状态与使用量总览。
- `GET /api/agents` / `GET /api/agents/{id}`: Agent 目录与详情。
- `GET /api/sessions` / `GET /api/sessions/{session_key}`: Session 列表与回放。
- `GET /api/turns/{turn_key}/trace`: 脱敏 Trace 详情或明确的 unavailable 状态。
- `GET /api/flywheel/overview` / `GET /api/flywheel/items`: Feedback、Review 和改进队列。
- `GET /api/sync/status`: FAE/ADMIN 最近同步状态。

## 测试

后端：

```bash
cd backend
.venv/bin/python -m pytest
```

前端：

```bash
cd webui
npm test
npm run build
```
