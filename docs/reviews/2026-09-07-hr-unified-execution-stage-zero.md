# HR 统一执行：阶段 0 / P01 执行记录

日期：2026-09-07。Owner 在 v0.3 复审修订后指令“开始”。

## 范围与基线

- 仅本地协议、共享样例、校验器和兼容测试；不启用 v5 发送，不迁移生产数据库、不部署、不重放业务消息。
- 工作树：`.worktrees/hr-position-core-availability`，分支 `fix/hr-position-core-availability`。
- 代码基线 `e55806e`；v0.3 文档提交 `8311f97`。
- MetaBot 基线 `fe5ad87`，Team 基线 `a6c028a`；本任务不修改这两个仓库。
- 原有两份未提交旧草案保持原样，不纳入新任务提交；环境目录不提交、不删除。
- O01 的生产目标、时间窗、表/列授权范围尚未明确，因此未执行生产统计。

## 施工前验证

在 Platform 工作树 `backend` 目录，使用根仓库已有 `.venv`，不新装或升级依赖：

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py
```

结果：64 passed in 0.30s。

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_relay_crypto.py tests/test_execution_worker_auth.py tests/test_agent_brain_metabot_collaboration.py
```

结果：63 passed in 1.85s。

以上是已有行为的基线，不计为 P01 新功能验收。

当前 Team 的 `deploy/metabot.runtime-contract.json` 经实际 `MetaBotRuntimeMap.from_contract` 只读解析返回 accepted，HR 端口 9101。此结果不证明生产无配置漂移，不代替 O02 的新合同部署检查。

使用 Python UUIDv5 与独立 Node crypto 算法核对四组人工输入（ASCII、Unicode、两组含分隔符输入），结果一致：

| tenant / app / bot / message | client_request_id |
|---|---|
| t / app / hr-bot / om_123 | 678b10d4-f624-56e5-8282-9491551290a4 |
| 租户 / app / hr-bot / om_中文 | f538a985-3e0c-5f3a-8487-7fbe53000d51 |
| a\|b / c / hr-bot / om_123 | 53534b22-2e73-54a2-be22-33916a884172 |
| a / b\|c / hr-bot / om_123 | 88eac4ac-d307-5bdc-ac56-68357ee3adb0 |

这只是冻结规则的独立核验；实现后的共享样例必须另有自动测试。

## P01 状态

**P01 已完成本地契约冻结。** 首轮候选 `692d262` 的 4 项 Important 已在 `62cdfce` 修订，规格符合性与代码质量复审均 Approved。最终评审范围为 `8311f97..62cdfce` 的 12 个精确文件，不包含旧分支补丁。共享文件见 [v5 合同说明](../../contracts/hr-execution/v5/README.md)。其余 17 项未完成；不以此宣称 HR 运行故障已修复。

### RED / GREEN

首个 RED 在接口已可导入、返回非稳定 UUID 的脚手架上运行：

```text
test_channel_key_is_stable_uuid_and_bot_scoped
1 failed, 64 passed in 0.31s
```

失败点为同一身份两次计算结果不相等，不以 ImportError 代替业务断言。下一轮 tenant/app/分隔符隔离：`1 failed, 65 passed in 0.25s`。随后逐项验证回调范围、权限 scope、typed terminal、ACK 游标、哈希/附件顺序、接收与发送版本分离、共享样例、跨语言计算、桥接身份与快照一致性。首轮 command 脚手架的 AttributeError 不计 RED；补齐占位属性后重新取得业务断言失败。

指定 GREEN（提交后再次运行）：

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_contract_v5.py tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py
```

结果：87 passed in 0.37s（新增 23 项，原有 64 项）。

主会话对同一提交独立运行扩展回归（工作目录 `backend`）：

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_contract_v5.py tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py tests/test_execution_relay_crypto.py tests/test_execution_worker_auth.py tests/test_agent_brain_metabot_collaboration.py
```

结果：150 passed in 2.14s，无跳过或警告。新协议 lint 在同一 `backend` 工作目录运行，`ruff check app/execution_relay/contracts_v5.py tests/test_execution_contract_v5.py` 通过；`git diff --check` 通过。

另一次从仓库根目录启动 Ruff 因源目录推断不同报 I001；按计划要求在 `backend` 运行通过，未为改变检查结果调整代码。后续 CI 也应明确相同工作目录。

### 共享样例与验证边界

- Native TypeScript verifier 使用 Node 26.5.0：`v5 cross-language fixtures: ok`；Python 测试调用同一 `cases.json`。
- 独立使用 MetaBot 已安装的 Ajv 8.20.0 + formats，以 Draft 2020-12 strict 模式编译全部 5 份 schema：31 个正向样例通过，5 个矛盾终态快照全部拒绝。未安装依赖或修改 MetaBot 仓库。
- Schema 负责有界结构与字段；Python 模型负责跨字段/字节语义和命令 hash。TypeScript 脚本核对 UUID/hash/replay 算法，不冒充尚未实施的 M02 运行时解析器。
- P01 仅有幂等冲突的纯函数裁决，不提供持久 HTTP 409 账本；P07 负责真正入站事务。公开 API、数据库快照、真实权限、凭据文件权限、附件上传和投递持久化均留在所属任务。
- 既有发送器仍仅允许 v3/v4；新增库不代表生产路由已支持或已派发 v5。

### 修订后的 RED / GREEN 与回归

修订提交 `62cdfce`（基于 `692d262`）仍限定原 P01 文件。以下定向测试均先获得业务断言 RED，再最小修改、同一测试 GREEN：

| 修订 | `test_execution_contract_v5.py` 定向选择 | RED → GREEN |
|---|---|---|
| Turn 终态与旧执行核对分离 | `-k snapshot_allows_terminal_turn_while_attempt_reconciles` | 1 failed → 1 passed；snapshot 组 2 passed |
| 真实入站 hash 投影 | `-k turn_intake_content_hash_matches_fixed_golden_vector` | 1 failed → 1 passed；共享/跨语言组 7 passed |
| Markdown 空白不改写 | `-k content_preserves_markdown_indentation_and_line_endings` | 1 failed → 1 passed；字节边界组 2 passed |
| 安全事件解析边界 | `-k parse_v5_event_preserves_all_typed_event_shapes` | 1 failed → 1 passed；事件解析组 3 passed |
| UUID 跨语言字串一致性 | `-k hash_boundaries_reject_noncanonical_uppercase_uuid_wire_values` | 1 failed → 1 passed |

同一已提交代码的结果：P01 单文件 **34 passed in 0.20s**；指定三文件回归 **98 passed in 0.42s**。主会话提交后再次运行上述六文件扩展命令：**161 passed in 2.11s**（新增 34，原有 127），无跳过或警告。TypeScript verifier、backend 工作目录 Ruff、`git diff --check` 通过。

独立 strict Ajv 再次编译 5 份 schema：**32 个正向样例通过、8 个矛盾快照拒绝**。主会话不导入项目 hash 函数，独立按字段组装 JSON 复算入站固定值为 `ac3358e9f43b2ed82141897ac5554f49a5998fdb819c884b81e66d71034cb042`，与共享样例一致。

冻结补充规则：command/intake 的 UUID 要求小写、标准连字符形式，不依赖 Python 隐式规范化；正文保留 Unicode、缩进、LF/CRLF 原样，不 trim；事件从 `parse_v5_event` 安全入口解析，错误不暴露被拒绝的正文或私有进度。入站附件投影保持现有 ID/index/SHA 三字段，文件名/MIME/大小仍由绑定的附件记录维护，P07 核验，不为哈希额外扩展 wire。

### 冻结合同 SHA-256

下列值对应已复审的 `62cdfce`。后续任务若修改共享合同，需记录新 hash 并重新做相关兼容测试，不得静默漂移：

| `contracts/hr-execution/v5/` 文件 | SHA-256 |
|---|---|
| README.md | `e6eef6662d5456d18344536f703b73ae821cfb06542e8b5aac3dd77500eba76a` |
| command.schema.json | `118b0e193f006259a03bc9cfb905ceeee377726a66e3e38e5ee5d61ddb513ba5` |
| callback.schema.json | `11c81cee2b51526d277d7695c2760e5863727812a1413e375d5f76ca132db9e7` |
| snapshot.schema.json | `9feab9dda44051a25931c7b5eb3abee6a0cf32ccaeab527d0969207a027effbc` |
| channel-bridge.schema.json | `19216f616d0b80b4672a99bbe8b8a263359c82877a1c3ab9160fcfcf5e983eef` |
| runtime-config.schema.json | `802094ca27ef00e44a90d0839ea21cfa3752092ae4f81329e1cc7dfa968f3e54` |
| cases.json | `d425f6f801bec8ef98b165c917cc75326c0b77f256e91307e4a27c52b9122dcf` |

### 独立复核

首轮两项结论均为 Needs fixes，无 Critical；以下 4 项 Important 经主会话核对后纳入同一批修订：

1. 快照强制 Turn/Attempt 同为相同终态，与设计 3.2.1 允许的终态 Turn + reconciling 旧执行矛盾。
2. 入站冲突只比较任意 hash 字符串，缺少 `contentHash` 的精确业务投影与跨语言计算向量。
3. 共用文本校验器拒绝 Markdown 首尾空白，包括正常的末尾换行。
4. 事件解析缺少与命令解析相同的脱敏异常边界，Pydantic 错误可带入被拒绝正文。

首轮审查员只运行两个有明确疑点的内存探针（终态快照、Markdown/异常内容），未重复整套测试或修改代码。修订后审查完整的 `8311f97..62cdfce` 包与 RED/GREEN 记录，确认四项及 UUID 跨语言边界修复；两项结论均 **Approved**，无 Critical、Important 或需行动的 Minor。最终验证使用修订后的 161 项结果，不挪用首轮 150 项。

主会话已逐项处理复审的不可验证项：持久幂等/真正 HTTP 409 与附件元数据权限属于 P07，平台 Attempt/Result 持久化属于 P02/P04，本地存储与命令/回调恢复属于 M02/M03，能力握手属于 P03/O02，渠道投递与发布属于 P08/M06/O02/O03。它们在 P01 的排除范围内，不记为已经实现。

## 额外发现：Team 既有合同快照测试失败

在未修改的 Team HEAD `a6c028a` 运行 `node --test scripts/reliability/tests/runtime-contract.test.mjs`：16 passed，1 failed（测试第333行，deepEqual断言第339行）。实际 `loadRuntimeContract` 解析成功；失败是 `productionBots` 固定快照缺少合同新增的 `brainDelegatable`、`collaborationContract`、`thinkingSummary` 和三个 supports 能力字段。

合同最近变更来自 `94e1c12 feat(agents): require live collaboration contract`，测试最近变更 `437cc7a fix(metabot): harden Agent Brain isolation`。这是独立仓库的既有测试基线问题，不是新P01契约回归；本轮不修改Team，也不把这次运行记为全绿。O02实施时必须修复并重跑，不能据此通过Team发布验收。

## 未执行

生产事件统计、真实飞书 API 去重/重推、进程故障注入、数据库迁移、部署与业务流量切换均未执行。

## M02b 联调发现的后续修复：expiresAt 原始输入校验

后续提交 `4ed7765 fix(hr): reject numeric expiry coercion in v5 commands` 修复一个具体输入差异：Python 的共用 AwareDatetime 会把数字字符串当作 Unix 时间戳，但冻结 v5 schema 的 date-time 与实际 Ajv+formats 不接受这种输入。仅在 v5 原始 wire 入口增加非字符串/数字字符串拒绝；未修改共用 v4 模型、冻结 schema/cases、哈希、依赖或部署配置，因此 `62cdfce` 资产来源与 SHA 不变。

真实 TDD：新增 15 个参数化场景，修改前 3 个数字字符串用例 `DID NOT RAISE`，其余 12 个通过；修改后 15 个全部通过。保留标准、空格分隔、小写 t/z、冒号/无冒号时区、+23:59、带小数秒输入；无时区和非法日期仍拒绝。实际数字 JSON 输入也拒绝，错误表面固定且不包含 grant/token。

主会话在 `4ed7765` 重新执行 `test_execution_contract_v5.py`、`test_metabot_collaboration_v4.py`、`test_metabot_relay_client.py`：**113 passed，2.85s**；backend 目录 Ruff 和 diff-check 通过。另用固定样例独立复验 8 个时间边界值，全部符合预期。不能用未启用 date-time checker 的 Python jsonschema.FormatChecker 冒充格式校验；此次对照使用已安装的 Ajv2020+formats，未安装包。

独立规格/质量评审范围 `35ad357..4ed7765`，两项 Approved，无 Critical/Important/Minor。测试执行证据由主会话当前提交复验闭环；这项窄修复不代表 M02c HTTP、M03/M04 恢复或生产已经完成。
