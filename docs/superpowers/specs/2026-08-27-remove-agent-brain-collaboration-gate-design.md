# 删除 Agent Brain Collaboration 发布门禁

日期：2026-08-27  
状态：设计已确认，等待实施计划

## 1. 背景

生产已经启用 Agent Brain V2，Brain Worker、Provider、数据库迁移与对话链路均已部署，但系统又增加了 `PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED`。当 Brain 和 Brain V2 均为开启、该额外开关为关闭时，后端会把正常的产品入口降级为“Agent 大脑正在准备”。

这造成两个问题：

- 已经部署且健康的 Agent 大脑被第三个发布开关隐藏；
- 用户看到的是假的产品状态，无法区分发布门禁与真实故障。

产品决策是彻底删除该机制，不保留兼容开关或同义替代项。

## 2. 最终行为

Agent 大脑的产品可用性只由核心 Brain 配置与运行状态决定：

- `PLATFORM_AGENT_BRAIN_ENABLED=1` 且 V2 配置合法时，登录后的根路径始终显示可持续对话的 Agent 大脑；
- 不再存在 Collaboration 额外门禁；
- 不再显示“Agent 大脑正在准备”或“顶层调度能力尚未正式启用”；
- 核心 Brain 被明确关闭或运行链路不可用时，创建对话接口返回稳定的显式错误，例如 `503 agent_brain_unavailable`；
- 前端保留用户输入并显示真实不可用状态和重试入口，不跳转专业 Agent、不伪装成准备中、不静默降级。

## 3. 删除范围

### 3.1 后端

- 从配置模型和环境解析中删除 `agent_brain_collaboration_enabled`；
- 删除 `brain_use_enabled = brain && (!v2 || collaboration)` 计算；
- Auth Shell、Conversation Router 与根路径直接使用核心 Brain 可用性；
- 删除用于注入 `platform-agent-brain-mode` 的 HTML 元数据；
- 核心 Brain 不可用时使用明确的服务端错误，不返回准备页状态头。

### 3.2 WebUI

- 删除 `BrainPreparingPage`；
- 删除 `agentBrainShellEnabled()` 及其 DOM 元数据读取；
- 登录用户访问 `/` 时始终进入 `BrainWorkspacePage`；
- 对明确的 Brain 503 错误显示“Agent 大脑暂不可用”，保留草稿并允许原请求安全重试。

### 3.3 部署与回滚

- 从 Compose、远程发布、回滚、验收脚本中删除 `PLATFORM_AGENT_BRAIN_COLLABORATION_ENABLED`；
- 普通发布生成的新 `platform.env` 不再包含该变量；
- 保留核心 `PLATFORM_AGENT_BRAIN_ENABLED` 与 `PLATFORM_AGENT_BRAIN_V2_ENABLED`，用于真正的灾难停用和版本兼容；
- 回滚验收不再断言 `brain-preparing`，而是断言页面不出现准备文案、关闭核心 Brain 后写接口明确返回不可用；
- 不修改 Nginx，不修改或重启 AI ADMIN、FAE、VOC。

### 3.4 文档

- 删除发布手册中“先保持 Collaboration=0、验收后再打开”的步骤；
- 历史设计文档可以保留事实记录，但必须标注该门禁已经废除，不能再作为运行指导。

## 4. 不采用的方案

- **只删准备页：** 后端仍会拒绝 Brain 请求，形成可打开但不可使用的假首页。
- **把开关永久固定为 1：** 仍保留误关和部署漂移风险。
- **换一个新的发布开关：** 只是重命名同一问题。

## 5. 测试与验收

实施必须按测试驱动完成，至少覆盖：

1. 配置模型不再接受或读取 Collaboration 开关；
2. Brain=1、V2=1 时根路径显示 Agent 大脑，与任何遗留 Collaboration 环境变量无关；
3. WebUI 构建产物不包含“Agent 大脑正在准备”或“顶层调度能力尚未正式启用”；
4. 核心 Brain 关闭时，对话写接口返回稳定的显式不可用错误；
5. 真实 Provider 或 Worker 故障不会切换到准备页；
6. 发布生成的 `platform.env` 不含 Collaboration 变量；
7. 发布和回滚脚本不再读取、写入或断言该变量；
8. `/office/?view=services`、FAE 域名、VOC 工作区、Nginx 配置和相关进程身份保持不变；
9. 生产发布后刷新 `/` 直接进入 Agent 大脑，能够创建并继续一轮真实会话。

## 6. 发布方式

先发布兼容代码和脚本，再原子重建 Platform API 与入口容器。Brain Worker 只有在镜像或运行协议确实变化时才重建。发布前后记录 AI ADMIN、FAE、VOC 与 Nginx 不变性证据。

若新版本验收失败，回滚 Platform release；不得通过恢复 Collaboration 开关回滚，因为该开关从本版本开始不再存在。
