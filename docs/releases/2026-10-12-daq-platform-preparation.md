# DAQ FAE Platform 接入准备（2026-10-12）

本变更准备 `ai-daq-fae-agent` 的企业成员身份签发、Agent Catalog 和同域 `/daq/` 路由。数采服务仍独立运行，预定回环端口为 `127.0.0.1:8081`；浏览器 `/daq/api/*` 去掉 `/daq/api` 前缀后进入数采后端，`/daq/*` 保持原路径。`/daq/health` 和 `/daq/api/health` 不对公网开放。

数据库迁移 `113_daq_agent_launch.sql` 只增加两个明确的 Agent ID，不改相机身份和合作方发放范围。数采目前仅允许企业成员，并须有数采自己的 `agent_use_grant`。跨 Agent 绑定验证继续按精确 ID 拒绝。未创建任何数采授权、未加入公开 `registry.yaml`，也未部署网关或数采服务。

正式启用次序：先完成数采知识 D3/D4 正式发布和独立数据库/身份配置，再部署数采后端并验证回环健康；随后执行 Platform 迁移与服务发布，配置受控用户授权，最后发布 Nginx 路由和 Registry 入口，检查数采与相机两个入口的真实身份和只读健康。每一步保留可回退版本；代码归并不代表正式上线。
