# Orbbec MetaBot Cluster Monitor

本机 MetaBot / Agent Bot 集群的只读状态仪表盘。Platform 从 `Orbbec-Agent-Team` 的运行契约自动发现实例，定时探测各实例公开的 `/api/health`，不提供 Agent 入口，也不执行重启或其他控制操作。

## 当前能力

- 自动读取 `deploy/metabot.runtime-contract.json` 中的业务 Bot 和 test-bot。
- 每 10 秒并发探测全部 MetaBot 实例。
- 展示健康、异常、离线、检测中四种状态。
- 展示运行时长、响应延迟、API 端口和最后检测时间。
- 契约读取失败时保留最后一次有效实例列表。
- Platform API 暂时失败时，页面保留最后一次成功快照并提示数据可能过期。

首版只使用无需鉴权的 `GET /api/health`。不会读取 MetaBot API 密钥、访问 `/api/status`、读取 PM2 或控制任何 Bot。

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

## API

### `GET /api/cluster/status`

返回集群摘要、运行契约状态和实例快照。实例固定按离线、异常、检测中、健康排序，同状态按端口升序。

### `GET /api/health`

仅表示 Platform 自身进程可用，不代表全部 MetaBot 健康。

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
