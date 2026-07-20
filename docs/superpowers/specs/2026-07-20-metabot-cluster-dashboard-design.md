# MetaBot / Agent Bot 集群监控仪表盘设计

## 背景

AI Agent Platform 当前按“业务 Agent 入口门户”建模，registry 只登记 FAE 和 ADMIN，并在前端提供外部入口链接。实际需求是监控本机正在运行的 MetaBot / Agent Bot 集群，不提供任何 Agent 入口或控制能力。

本机的集群真相源是：

`/Users/neo/Developer/work/Orbbec-Agent-Team/deploy/metabot.runtime-contract.json`

该契约当前包含 8 个业务 Bot 和 1 个 test-bot。每个实例在独立端口提供无需鉴权的 `GET /api/health`，响应仅包含 `status` 和 `uptime`。

## 目标

构建一个本机常驻、只读的集群监控仪表盘，准确回答：

- 集群当前包含多少个实例；
- 哪些实例健康、异常、离线或仍在检测；
- 每个实例运行了多久；
- 每个实例的探测延迟和最后检测时间；
- 仪表盘数据是否仍然新鲜。

## 非目标

首版不包含：

- Agent 工作台或其他入口链接；
- MetaBot 对话、任务下发、重启或任何控制操作；
- 需要密钥的 `/api/status`；
- PM2 进程控制或跨用户权限；
- 历史趋势、持久化、告警通知；
- CPU、内存、任务、执行器等详细运行指标。

## 架构

### 运行契约加载器

后端新增独立的运行契约加载器。契约路径由环境变量 `PLATFORM_METABOT_CONTRACT_PATH` 指定，默认使用上述本机路径。

加载器从 `bots` 和启用的 `testBot` 生成统一监控目标：

- `id`：Bot name；
- `name`：面向页面的 Bot name；
- `pm2_name`：`instance.pm2Name`；
- `port`：`instance.apiPort`；
- `health_url`：`http://127.0.0.1:{port}/api/health`；
- `workdir`：仅供后端日志诊断使用，不通过监控 API 返回，也不做页面入口。

后端每轮轮询前检查契约文件的内容。契约变化时同步新增、删除或更新监控目标，无需重启 Platform。

若契约临时不存在或解析失败，后端保留最后一次成功加载的目标列表，并将 `source_healthy` 设为 `false`、记录安全的错误摘要。错误摘要不得包含密钥或完整环境变量。

### 健康轮询器

后端默认每 10 秒并发请求全部目标的 `/api/health`，单实例超时 3 秒。每个实例独立失败，不阻塞其他实例。

状态规则：

- `healthy`：HTTP 200、JSON 为对象、`status == "ok"`；
- `degraded`：目标可达，但状态码非 200、JSON 无效或 `status` 非 `ok`；
- `offline`：连接失败或超时；
- `checking`：尚未完成第一次探测。

每次探测保存：

- 状态；
- uptime 秒数；
- 响应延迟毫秒数；
- UTC 检测时间；
- 简短错误码或错误摘要。

运行数据只保存在内存中。Platform 重启后重新进入 `checking`，不伪造历史。

### API

新增 `GET /api/cluster/status`，返回一个完整快照：

```json
{
  "summary": {
    "total": 9,
    "healthy": 9,
    "degraded": 0,
    "offline": 0,
    "checking": 0
  },
  "source": {
    "healthy": true,
    "checked_at": "2026-07-20T10:00:00Z",
    "error": null
  },
  "instances": [
    {
      "id": "hr-bot",
      "name": "hr-bot",
      "pm2_name": "metabot-hr",
      "port": 9101,
      "status": "healthy",
      "uptime_seconds": 280512,
      "latency_ms": 2,
      "checked_at": "2026-07-20T10:00:00Z",
      "error": null
    }
  ]
}
```

实例排序由后端固定为 `offline`、`degraded`、`checking`、`healthy`，同状态按端口升序，保证所有客户端一致。

保留 `GET /api/health` 作为 Platform 自身健康检查。旧 Agent portal API 不再由新页面使用，但首版不强制删除，以减少迁移风险。

## 前端

页面定位改为“MetaBot Cluster Monitor”。

### 顶部摘要

顶部显示：

- 实例总数；
- 健康数；
- 异常数；
- 离线数；
- 最后刷新时间。

只要存在 `degraded` 或 `offline`，摘要区域进入告警视觉状态。

### 实例卡片

每个实例卡片只显示运维状态：

- Bot 名称；
- MetaBot 进程名；
- API 端口；
- 状态徽标；
- 格式化运行时长；
- 响应延迟；
- 最后检测时间。

卡片不是链接，不含“进入工作台”或外部跳转。颜色不是唯一状态信号，所有状态同时提供文字标签。

### 刷新与过期

前端每 10 秒获取一次 `/api/cluster/status`。请求失败时保留最后一次成功数据，并显示“监控接口不可用，状态可能已过期”。

任一实例的 `checked_at` 距当前时间超过 30 秒时，页面将其标记为“数据过期”，但不擅自将其改判为离线。

## 错误处理

- 一个 MetaBot 超时只影响该实例；
- 契约加载失败时保留最后一次有效列表；
- Platform API 失败时前端保留最后一次成功快照；
- 没有成功快照时展示明确的空状态；
- 后端错误信息只返回可操作的简短摘要，不返回堆栈或秘密；
- 仪表盘不执行自动恢复或重启。

## 本机部署

生产构建由 FastAPI 同源提供，避免同时维护 Vite 开发服务：

1. 使用 `npm run build` 生成 `webui/dist`；
2. 后端设置 `PLATFORM_STATIC_DIR` 指向该目录；
3. 后端监听 `127.0.0.1:8000`；
4. 通过当前 macOS 用户的 LaunchAgent 常驻运行并在登录后自动启动；
5. 日志写入用户 Library Logs 下的 Platform 专用文件；
6. 仪表盘地址为 `http://127.0.0.1:8000/`。

部署不得停止、重启或修改任何 MetaBot 进程。

## 测试

### 后端

- 契约解析覆盖 8 个 `bots` 和启用的 `testBot`；
- 禁用的 `testBot` 不进入目标列表；
- 契约变化能够增删实例；
- 契约错误保留最后有效快照；
- 四种实例状态判定正确；
- 单实例失败不影响其他实例；
- 汇总统计与排序正确；
- API 不泄漏 `workdir`、其他主机路径或秘密。

### 前端

- 状态标签与视觉 tone 映射正确；
- uptime 格式化正确；
- 汇总数字正确显示；
- 实例顺序与数据一致；
- API 失败保留旧数据并显示过期提示；
- 页面不包含 Agent 入口链接。

### 部署验收

- 后端测试全部通过；
- 前端测试和生产构建通过；
- `GET /api/cluster/status` 返回当前 9 个实例；
- 当前 9 个实例均能通过 `/api/health` 独立探测；
- 页面从 `http://127.0.0.1:8000/` 返回 HTTP 200；
- LaunchAgent 状态正常；
- 重启 Platform 服务后仪表盘能够恢复；
- MetaBot 进程 PID 和运行状态在部署前后不被改变。

## 成功标准

用户打开 `http://127.0.0.1:8000/` 后，可以在一个页面内看到由运行契约自动发现的全部 MetaBot / Agent Bot 实例及其实时健康状态。页面没有入口或控制功能，Platform 重启和契约更新不会影响正在运行的 MetaBot 集群。
