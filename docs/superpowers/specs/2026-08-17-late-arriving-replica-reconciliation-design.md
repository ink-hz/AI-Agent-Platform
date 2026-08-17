# 迟到数据云副本对账设计

## 背景与根因

本地 `platform_read.sessions` 中 `ai-fae-agent` 有 514 个 Session、1025 个 Turn，云副本只有 484 个 Session、969 个 Turn。五分钟同步任务运行正常，生产序列已推进到 764，但云端 FAE 数据停留在早期 generation。

根因是云导出使用业务时间 `last_active_at` 作为唯一增量游标。FAE/ADMIN 数据先在远端发生，稍后才批量导入本地镜像；当这些历史记录到达本地时，其 `last_active_at` 可能早于已经推进的云导出水位，因而永久失去被选中的机会。

## 目标

- 迟到导入本地的 FAE/ADMIN Session 能进入后续云同步。
- MetaBot 的实时 Session 继续按业务活动时间增量同步。
- 不清空云端数据库，不改变稳定脱敏 Session/Turn ID，不打断签名摘要链。
- 用受保护的一次性回拨重新扫描缺失窗口，将生产云副本补齐至本地当前基线。
- 同步失败时保留队列和原状态，可安全重试。

## 非目标

- 不新增 CDC、消息队列或数据库 Outbox。
- 不改 FAE、ADMIN 或 MetaBot 的业务库。
- 不把云端改成主数据源。
- 不执行全库删除或生产 generation 重置。

## 永久游标语义

`ReplicaSource` 为每个 Session 计算只用于复制的 `replica_updated_at`：

- MetaBot：`last_active_at`，因为其数据直接写入本地库。
- FAE/ADMIN：`greatest(last_active_at, source_synced_at)`，因为 `source_synced_at` 表示历史数据实际进入本地镜像的时间。

分页条件、排序和 checkpoint 全部使用 `(replica_updated_at, session_key)`。展示、保留期和 Session 业务时间仍使用原有 `last_active_at`，不改变用户看到的时间。

`RawSession` 增加 `replica_updated_at`。导出状态中的 `upper_watermark` 从此表示复制更新时间水位；字段名和批协议保持不变，避免云端协议迁移。

## 受控回拨与一次性修复

新增本地 CLI 命令 `rewind-export`，只修改 mode 0600、当前用户所有的导出状态文件。命令必须同时满足：

- 导出队列为空，避免已有 batch 与新水位交错。
- 指定的 `--expected-next-sequence` 与状态完全一致。
- 指定的 `--to` 是合法 UTC 时间、早于当前水位且不早于当前时间 365 天。
- 保留 `source_instance_id`、`next_sequence` 和 `previous_digest`，只更新 `upper_watermark` 并清空 `cursor_session_key`。
- 原子写入状态文件并保持 mode 0600。

生产修复时将水位回拨到最近一次 FAE 本地导入时间之前，再连续执行现有 `push-replica.sh`，直到游标回到当前时间且队列为空。云端继续按原序列和摘要链接收 Upsert；已有脱敏键稳定，新记录补入，旧记录刷新。

## 错误处理

- 回拨前置条件任一不满足时失败关闭，不修改状态。
- 导出或传输失败时保留 batch，由现有任务重试。
- 云端导入继续校验 sequence、previous digest、签名和 record digest。
- 对账过程中若本地源数量发生变化，以同一只读快照记录的上界为验收基线，完成后再跑一次普通增量同步。

## 测试与验收

- 源查询测试证明：业务时间很旧但 `source_synced_at` 新的 FAE Session 会被选中。
- 101 条相同复制更新时间的复合游标测试证明不丢不重循环。
- 回拨命令测试覆盖成功、队列非空、sequence 不匹配、时间范围非法和原子文件权限。
- 现有协议、导入、加密和云部署全量测试必须通过。
- 生产验收必须证明：
  - 本地和云端 `ai-fae-agent` 均为 514 Session、1025 Turn；
  - 最新 Session 时间一致；
  - 云 sequence 连续增长且摘要链有效；
  - 五分钟 LaunchAgent 最后退出码为 0；
  - Platform 5 个服务健康，FAE 容器 ID、StartedAt 和 RestartCount 不变。
