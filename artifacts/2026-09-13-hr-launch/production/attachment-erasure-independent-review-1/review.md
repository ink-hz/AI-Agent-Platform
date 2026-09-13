# 附件擦除 canary 独立审读：961bac7

审查提交 `961bac7502e3f66ed7278ffd208a342591a2c832`。结论：身份、附件绑定、不可重发的不确定写入和擦除证据分离的设计适合已授权的单操作者串行生产合成附件验收；**该提交单独不能提供其宣称的严格墙钟上限，需先补真实有界监督或收窄承诺并采用经验证的外层监督，才按有界维护脚本执行**。没有生产调用，没有模型、S3访问或生产凭据；未修改受审代码。

## 阻断项：时间承诺不成立

`attachment_erasure_canary.py:30–36,67–74,219–229` 每次请求前计算剩余时间，并把它传入 HTTPX timeout。该 timeout 是各阶段/空闲等待超时，不是整个 response 下载的墙钟期限。持续小片段传输可在 deadline 以后返回，observe 的“aggregate20-second”也不成立。四项测试把 timeout 传到 TestClient，原日志明确保留其弃用警告，不能据此证明网络截止。

独立本地实证命令：

```
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/attachment-erasure-independent-review-1/check.py > artifacts/2026-09-13-hr-launch/production/attachment-erasure-independent-review-1/local-slow-stream.log 2>&1
```

退出 0 表示成功复现受审缺陷，不是 canary 合格：exact commit 快照真实 HTTPX、localhost socket 慢流、0.1 秒剩余期限、每 0.025 秒一个字节，完整请求耗时与返回值见原日志，返回时已越过 deadline。只用合成 header，直接构造 loopback config 绕过 HTTPS loader 以隔离 timeout 机制；不代表真实凭据/TLS验证。`check.py` 固定输入源码，所有快照在 `fingerprints.json`。

最小修复：调用阶段在真实主进程墙钟监督之下运行（可用适用平台的信号截止，或独立外层监督进程）；prepare/erase 上限按已持久 ledger 剩余 300 秒计算，observe 单独 20 秒，不重置 mutation deadline。超时后的发送中操作保留 uncertain/sending，恢复不能重发。若使用外层终止，必须证明不留下继续发送的子线程/子进程。需真实慢流负例和超时后未知写不重发验证，不能只 monkeypatch time 或调用 TestClient。作者正在另行处理；本报告不预先批准未来版本。

## 身份与范围

- 94–107 行通过真实 account 检查准确 owner UUID、platform_owner、非 hard-stale；每个 prepare/erase/observe 都核验。不是客户端伪造成功身份。
- 51–55 行送 session cookie、同源 Origin、CSRF；CLI 使用 `verify=True, trust_env=False, follow_redirects=False`。继承 loader 要求绝对普通 0600 JSON、HTTPS 同源、无 URL credentials/query/path，不签发会话。日志只保存安全 account 子集、不记录 Cookie/CSRF；测试实际确认令牌不在 ledger。
- 上传返回 UUID 持久化；metadata 校验 id、包含 run UUID 的合成文件名、长度、MIME；erase 还重读当前 ready metadata。附件 API repository 自身以真实 owner 约束查询/删除。客户端没有枚举或任意 attachment-id CLI；不是靠 operator 填一个待删 ID。
- prepared hash 是发送字节和 metadata 绑定，不能证明服务器存储字节；README 已准确声明由 root 在删除前收集真实对象图、字节与 ref 哈希。脚本本身不负责 SQL/S3。必须在同一未篡改 ledger、同一账号/源、单操作者串行运行；没有进程间锁，不允许两个 resume 并发运行。可作为操作限制，不把“单次发送”泛化为多进程并发保证。

## 不确定重放

继承 `api_canary.py:195–241` 在网络前写 operation sending，先持久化后请求；仅 prepared 可发送，received 直接返回已有收据，其他状态阻止重发。没有以本地 Idempotency-Key 冒充上传服务端幂等。响应丢失、响应无效、崩溃在 sending 后均保守停住；这可能留下待人工核对的合成孤立上传，但比自动重建安全。erase 194–198 行也不会重复 DELETE。收据文件采用 0600 临时文件+fsync+replace；这里是进程中断保护，不宣称断电下文件系统目录持久性或抵抗操作者篡改。

## 404 与物理擦除

219–241 行只记录 metadata/404，`physical_erasure_verified=false` 固定保留。DELETE204 只使状态成为 awaiting_external_erasure_evidence。原成功测试真实 PG+access service 验证 GET404 时 MemoryStore 对象还存在，然后真实 maintenance-role erasure service 执行后才检查对象消失及 job completed。这个反例有承重意义，没有以404假证擦除。

本次 production 必须先由 root 锁定准确 attachment ID 对应的原始/派生/write-attempt 对象图并证明对象存在，再 DELETE；之后分别记录 job、清理计数和各准确 S3 对象/适用版本的实际不存在。权限错误、查错版本、无法读取都不是物理擦除。脚本正常退出或 observe404 均不替代该证据。

## 旧 root schema 与四项测试覆盖

四测试完整读取：①延迟 validating→ready、204、404仍存对象、真实维护擦除；②已过期禁止DELETE但能GET观察；③DELETE响应丢失后恢复不重发；④上传初始化响应丢失不重建、owner不匹配不写。原证据是 4 passed / 6.43s、4 条 TestClient warning；未重跑重复套件。

`hr_agent_database(migrate_hr=False)` 只是省略 hr_web/hr_agent 目录，仍执行全部 root SQL（含100及102–105）。所以这些测试不等于已在旧 ledger95精确布局验过。只读比较65e7fbd→961bac7：上传、附件路由、metadata/download repository 等模块没有变；account/auth相关差异是HR入口/健康路由，不改变所用 account 契约。HTTP工具本身没有新HR依赖，因而支持其旧API使用判断。

但 `backend/app/attachments/erasure.py:66` 的 claim SQL 从复合展开改为 FROM函数，且 migration100 给 maintenance 增加 uploads/write-attempt 读取列权限，均与实际物理擦除有关；旧95处理器不能借当前全root测试被宣布合格。若在root105迁移后执行，应明确记录真实ledger与运行处理器版本；若尚在旧95，只能观察真实结果、保留可能失败，不能预设 job会成功。受审脚本不会伪造成功或修生产记录。

四测试用真实本地会话仓储、身份授权、中间件、PG、加密、附件处理和擦除服务；外部登录交换、对象存储是替身。没有独立的缺CSRF/错Origin/过期会话反例，也没有对错误metadata和并发resume专项测试，不能从这4项宣传这些负例已实测；源码 fail-closed 与既有服务边界可见。

## 固定证据

`snapshot/` 中7份源码/原测试/原日志/README来自确切commit，不从现在工作区复制未知变更。原 green 记录运行开始 HEAD404862b、source_unchanged=true；3个受审源码 SHA 与提交快照逐字节可核，不把原运行误称发生于961bac7提交之后。依赖 api_canary.py 也固定并审读了被继承的config/journal/mutate/sleep完整路径；未使用其HR work入口。
