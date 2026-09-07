# HR 统一执行 M03a 本地验收记录

状态：M03a 本地实现与独立规格/质量复审通过，唯一 Important 已关闭。未完成 M03 整体，不代表 v5 或业务已可用。

## 候选范围

- MetaBot：`5506629..672cc9f`，`feat(hr-runtime): persist v5 events and unique terminal atomically`，13 个文件。
- Platform：`f7de940..40ed7a9`，包含 `2592327` callback 原始输入校验和 `40ed7a9` 三条 v5 日期入口的正向格式校验；仅两个 Python 文件。
- Team 保持 `a6c028a`，原未跟踪文件未动。Platform 两份旧的未提交草案未动。
- 无生产访问、迁移应用、模型执行、飞书发送、依赖安装或推送。仅测试自有临时 PG、角色和子进程。

## 实现边界

事件和唯一终态存入同一 `hr_runtime` PG：原始事件正文为不可变 JSON TEXT，命令保存对应事件引用/连续游标。应用角色对事件仅 SELECT/INSERT，无 UPDATE/DELETE。终态写入与命令终态同事务；内容相同返回原事件/序号/时间，内容冲突不再生成第二个终态。

执行领取时原子保存启动租约代次，回调地址/凭据之后轮换不改写原事件。缺失启动证据的旧 claimed 行不被猜测恢复为可执行。有效结果入库不等于执行器已停止：无论 payload 中 stopped 的值如何，执行占位仍保留，等待 M04 的核验。

`append` 处理完整非终态事件的精确重复、冲突和缺序；`commitTerminal` 在锁内分配序号/首次时间；两者保持 Session → command → intent 锁序。共享 ACK 无法确认最大的 safe integer 序号，生产者在该边界前明确 sequence_exhausted，不修改 wire schema。

Platform 修复了 Pydantic JSON 转换接受数字时间字符串以及非 plain UUID 别名的问题；有效大小写 UUID 保留。UUID 格式依据 [JSON Schema 2020-12 §7.3.5](https://json-schema.org/draft/2020-12/json-schema-validation#section-7.3.5)，不扩大为所有 callback UUID 必须小写；小写规则仍只用于原有命令/入站哈希身份。

## TDD 与实际进程证据

首个持久化 RED：真实命令接受/领取后，终态操作与 store 重开得到 0 条事件，期望 1；最小实现后 GREEN。后续并发终态最初触发 unique violation，经锁内语义裁决后只返回既存事件或 conflict。回调结构/字节/源字段、租约和缺序均有逐步 RED/GREEN。

主会话在最终候选提交复验：

```sh
node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/local-runtime-config.test.ts tests/local-runtime-store.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts tests/core-chat-v5-http.test.ts tests/http-server-cross-verify.test.ts tests/core-chat-v5-event-contract.test.ts tests/core-chat-event-outbox.test.ts tests/core-chat-event-outbox-process.test.ts
node /Users/neo/Developer/work/metabot-dev/node_modules/typescript/bin/tsc -p tsconfig.bridge.json --noEmit --composite false --incremental false
```

**11 文件，266 tests passed，2.20s**，其中原 M02 回归 185 项、新 callback 58、outbox 21、实际进程 2。十处源码/测试文件的 ESLint 及 diff-check 通过，无警告；输出有两行明确的合成进程故障证据。

实际自有子进程 PID 27908 在 IPC 确认事务内已经写入终态/事件后、COMMIT 前被 SIGKILL；重开确认事件 0、无终态、序号仍 1。PID 27922 在确认 COMMIT 后被 SIGKILL；重开确认唯一 result 1、序号 2、后续 error 冲突。两种情况均保留 claimed/会话占位，不能重新执行。这是事务写入进程的实际故障验证，不是 Claude/飞书进程验收。

Platform 工作树 backend 内复验：

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q tests/test_execution_contract_v5.py tests/test_metabot_collaboration_v4.py tests/test_metabot_relay_client.py
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m ruff check app/execution_relay/contracts_v5.py tests/test_execution_contract_v5.py
```

初始候选 **135 passed，0.51s**，Ruff 通过。日期源校验先出现 6 个 DID NOT RAISE；UUID 源校验先出现 15 个 DID NOT RAISE、1 个正向通过；修订后对应负向与正向均通过。最终修订后的新验证见下节，不能用该初始计数代替最终证据。

实际 Node 导入候选编译产物、6 类事件/4 类 ACK、outbox 导出及 schema 深相等均通过，进程自然退出 0。源/导出 callback SHA 为 `11c81cee2b51526d277d7695c2760e5863727812a1413e375d5f76ca132db9e7`；编译后 JSON 因格式化 SHA 为 `9e9e6fc27b87047c1614119923b5ed5385f0350eb2e0b23dbfb9e2495eccb776`。schema 仍来自 `62cdfce`，与本次 Python 校验修复提交区分。

## 复审修订与闭环

独立复审发现数值时间黑名单仍接受前导加号、尾随小数点；主会话进一步验证科学计数法及输入附件 `expiresAt` 存在同根强制转换。`40ed7a9` 将三条入口统一为正向 ASCII 日期格式校验，随后仍由既有 Pydantic 检查日历和时区。明确保留已批准的大小写 T/Z、空格分隔及 basic-offset 兼容形式；拒绝数值别名、逗号小数、缺秒和无时区，不修改共享 schema、旧协议或 MetaBot。

修订先通过公共解析器复现 **15 个 DID NOT RAISE**，再得到 **54 个 focused / 104 个契约文件 / 168 个指定回归通过**。主会话在 `40ed7a9` 重跑上述相同三文件命令：**168 passed，0.38s**，Ruff 与 diff-check 通过。测试分别证明原始模型可强制转换和 wire 入口必须拒绝，避免以无效命令哈希代替日期断言。此前 P01 的 expiry 修订只挡住部分数值形式，本次才关闭这些已复现的变体。

同一独立 reviewer 对完整 MetaBot `5506629..672cc9f` 与 Platform `f7de940..40ed7a9` 复审：规格符合、质量 **Approved**，无遗留 Critical/Important/Minor。MetaBot 精确提交未变，266 项和进程证据仍对应最终代码。

## 未完成事项

M03b 的实际 Relay 接收/ACK/缺序补送/持久退避、M03c 的 stream 消费和 Flywheel/Trace 重建尚未完成。`readRun` 只是本地读取，不是对端接收或恢复发送证明。M04 必须完成停止核验后释放占位；M03/M05 附件输入与成果上传、P03 发送端协议/序号以及 O02/O03 切换验收仍另行完成。

复审提出的跨任务边界已逐项归入上述任务及 P03/P04 业务上下文、O02/O03 路由/留存/飞书合成验收；它们不由本库测试替代，不勾选整个 M03。
