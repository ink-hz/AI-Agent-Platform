# VOC 专业 Agent 目录接入设计

日期：2026-08-27
状态：已获用户原则确认，等待书面审阅

## 1. 目标

把已经上线的 `/agents/voc/workspace` 作为一个可点击的专业 Agent 卡片加入
`/agents`，并向当前企业内全部有效成员开放。此次只接入入口，不修改 VOC
草稿、提交、补充、数据权限或模型协议，也不修改行政 `/office`。

## 2. 现状

- VOC 工作区、Platform 身份透传、后端扩展和生产容器已经上线。
- `registry.yaml` 已登记 `id: voc`，但 `/agents` 不读取 Registry；它读取
  `backend/app/agent_catalog/catalog.yaml`。
- 规范 Catalog、后端授权允许列表以及前端外部工作区白名单均只有现有八个
  Agent，因此 VOC 不会出现在 `/agents`。

## 3. 方案选择

采用“规范 Catalog 接入”方案：沿用稳定 ID `voc`，在唯一专业 Agent Catalog
中加入外部工作区卡片，并同步后端授权允许列表及前端安全白名单。

未采用：

- 前端硬编码一张 VOC 卡片：会绕过后端授权，形成第二份目录。
- 立即让 `/agents` 改为读取 `registry.yaml`：会扩大为 Catalog/Registry
  合并重构，不适合本次快速接入。

## 4. 产品表现

专业 Agent 排序固定为：

1. AI FAE Agent
2. HR Agent
3. VOC 洞察助手
4. 五个 Marketing Agent
5. AI 行政 Agent

VOC 卡片显示：

- 名称：`VOC 洞察助手`
- 分组：`客户洞察`
- 交互模式：`external_workspace`
- 安全入口：`/agents/voc/workspace`
- 操作文案：`打开工作区`
- 卡片点击后保持同域 Platform 登录态，进入现有 VOC 工作区

## 5. 授权与安全

- `voc` 进入后端规范 Agent ID 允许列表。
- 生产发布后，通过现有受审计的 `grant_agent_use_scope_v29` 为 `voc` 建立
  `all_members` 授权；不直接插入无审计授权行。
- 目录 API 仍以后端授权结果过滤，未登录或失效成员不能通过卡片或直接 URL
  读取 VOC 数据。
- 前端只接受精确同源路径 `/agents/voc/workspace`，任意其他 VOC URL 均显示
  “入口暂不可用”。
- `voc` 是外部工作区卡片，不注册 Brain Adapter，不进入 Agent 大脑派发范围。

## 6. 实施边界

需要修改：

- 规范 Catalog 与 Catalog 校验测试；
- Catalog 授权函数的规范 ID 允许列表，使用新控制库迁移；
- 专业 Agent 页的排序、类型与入口白名单；
- 前端目录测试、迁移测试和生产验收断言；
- 发布后的全员授权操作及审计证据。

不修改：VOC 服务代码、VOC 数据库、行政 Agent、FAE、MetaBot、Brain Adapter
和现有 VOC 工作区业务流程。

## 7. 测试与验收

实施采用测试驱动开发，至少证明：

1. Catalog 精确包含九个产品 Agent，`voc` 只能是外部工作区。
2. 后端对有效全员授权返回 VOC，对无授权用户不返回。
3. `/agents` 中 VOC 位于 HR 后、Marketing 前，链接精确为
   `/agents/voc/workspace`，卡片类型为 `voc`。
4. 恶意或漂移的 VOC 工作区 URL 不会渲染成可点击链接。
5. 既有 FAE、HR、Marketing、行政顺序和入口不回归。
6. 生产真实企业账号能从 `/agents` 进入 VOC，刷新后仍保持登录态。
7. `/office`、FAE 域名和现有 Agent 大脑链路不发生变化。

## 8. 回滚

应用回滚恢复发布前镜像；若已创建 `voc` 全员授权，则使用现有受审计撤销函数
撤销该授权。回滚不删除 VOC 数据，也不停止独立 VOC 服务，原工作区可继续通过
既有直接路径访问。
