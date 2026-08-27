# 云端运行摘要与本地口径一致性设计

日期：2026-08-27
状态：已确认，等待实施

## 1. 问题与证据

同一个近 24 小时时间窗口内，本地运行摘要显示 AI FAE Agent 新增 44 次对话，云端只显示 15 次；本地还能显示 AI ADMIN 同步失败、FAE 小时用量和 flywheel 恢复事件，云端却显示“关注状态暂不可用”和“暂无重要变化”。

只读生产核查确认数据没有丢失：云端脱敏副本包含 15 个 AI FAE Session，并在这些 Session 内完整保存了 44 个已回答 Turn。云端事件投影也已经包含 AI ADMIN 告警和 FAE 小时事件。

根因有两个：

1. 本地按近 24 小时已回答 Turn 计数，云端 `ReplicaOperationsRepository.usage_leaders()` 却按新建 Session 计数。
2. 云端事件投影没有保留 `event_family`、`status`、`title` 和 `source_kind`；读取时又把所有事件固定重建为 `execution`、`historical` 和 `cloud-replica`，导致活动告警和 usage/recovery 事件被摘要逻辑过滤。无 `agent_id` 的平台级恢复事件也没有进入投影。

## 2. 目标与非目标

目标：

- 云端与本地使用相同的“已回答 Turn”口径统计近 24 小时用量。
- 云端正确恢复安全的事件分类、状态、标题和来源。
- AI ADMIN 活动告警进入“需要关注”。
- FAE 小时用量和 flywheel 恢复事件进入“近 24 小时重要变化”。
- 保持云端只读、脱敏和现有一周年保留策略。
- 兼容升级前已经写入的旧事件投影。

非目标：

- 不同步原始未脱敏事件内容。
- 不把本地整份摘要作为不可解释的快照复制到云端。
- 不新增云端写入口。
- 不修改 FAE、AI ADMIN、Nginx 或其数据库。

## 3. 设计

### 3.1 用量统计

`ReplicaOperationsRepository` 通过受限的 Session 记录读取接口获得已经解密且完成脱敏校验的 Session 投影。它只检查：

- Agent 是否属于业务可见目录；
- Turn 的 `created_at` 是否处于请求窗口；
- Turn 的脱敏回答文本是否非空。

每个满足条件的 Turn 计为一次对话。Session 的 `created_at` 不再作为运行摘要用量口径。计数过程不输出问题、回答或身份字段。

为了避免复制解密和完整性校验逻辑，Session 读取仍由 `ReplicaObservabilityRepository` 负责；Operations Repository 只消费一个窄化的 usage reader 接口。

### 3.2 事件投影

`OperationEventProjection` 增加以下安全字段：

- `event_family`
- `status`
- `title`
- `source_kind`

`agent_id` 在加密投影载荷中改为可空，以支持 flywheel 等平台级恢复事件。现有数据库索引列保持 `NOT NULL`，此类事件只在索引列使用固定哨兵值 `platform`；读取时必须以验签、解密后的载荷为准并恢复为 `None`，不得把哨兵值冒充真实 Agent ID。`title` 和 `summary` 继续经过脱敏字典；其余枚举或来源字段只接受安全标识符。不得投影 `facts`、目标路径、指纹或原始正文。

本地导出器每次都会重新导出管理投影，因此新版本上线后的下一次成功同步会原位更新历史事件投影，不需要数据库迁移或手工回填。

### 3.3 向后兼容

云端读取器必须兼容旧投影：

- 缺少 `event_family` 时，根据已知 `event_type` 做受限映射，未知类型回退 `execution`；
- 缺少 `status` 时回退 `historical`；
- 缺少 `title` 时回退 `event_type`；
- 缺少 `source_kind` 时回退 `cloud-replica`。

这保证先部署云端或先更新本地导出器都不会造成读取失败。新字段同步到达后，页面自动恢复完整语义。

## 4. 数据流

```text
本地 platform_read.turns ──> 本地运维统计（44 个已回答 Turn）
            │
            └─脱敏 Session 投影──> 云端 replica──> 按 Turn 重新统计（44）

本地 operational_events ──> 安全事件投影
            │                  family/status/title/source
            └────────────────> 云端运行摘要与事件列表
```

## 5. 失败行为

- Session 投影解密、完整性或数据库读取失败：摘要 API 返回明确的 503，不使用 Session 数静默代替 Turn 数。
- 旧事件投影字段不完整：使用明确的兼容默认值，不拒绝整份摘要。
- replica 超过新鲜度阈值：继续使用现有 stale/unavailable 语义，不宣称整体健康。

## 6. 测试与验收

测试先行，至少覆盖：

1. 15 个 Session 内 44 个已回答 Turn 时，云端返回 44。
2. 空回答 Turn 不计数，窗口外 Turn 不计数，排除名单 Agent 不计数。
3. 活动 attention 事件在云端保持 `active` 并显示在“需要关注”。
4. usage 和 recovery 事件保持原 family，并进入重要变化。
5. `agent_id=null` 的平台级恢复事件能够读取。
6. 旧格式投影仍能读取。
7. 投影不包含 facts、身份标识、目标路径或未脱敏正文。

生产验收必须在同一分钟窗口比较本地与云端：用量总数、活跃 Agent、活动告警和前五条重要变化一致；同时确认 FAE、AI ADMIN 与 Nginx 不变。

## 7. 发布与回滚

发布顺序：先发布兼容读取器，再更新本地导出器；等待一次成功同步后验收。若两个组件随同一 Platform 版本发布，仍保留旧投影兼容测试。

回滚只回滚 Agent Platform 版本。事件投影中的新增字段保留在加密载荷内，旧读取器忽略它们；不回滚数据库、不重新同步全部数据、不重启 FAE 或 AI ADMIN。
