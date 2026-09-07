# HR 统一执行 M02c 本地验收记录

状态：M02c 本地切片完成，完整范围 `9770c0d..5506629` 独立规格/质量复审 Approved，无遗留 Critical/Important/Minor。M02 整体及业务运行尚未完成。

## 范围

MetaBot 隔离工作树 `/Users/neo/Developer/work/metabot-dev/.worktrees/hr-unified-execution`，本切片起点 `9770c0d`。初始提交 `b5fae68 feat(hr-runtime): wire durable v5 HTTP acceptance to isolated PTY entry`，共 6 个文件。

真实 HTTP 入口使用已冻结的 v5 parser、真实 PG 接受/传输轮换/执行意图领取；三者同事务，提交后才进入现有 PTY 的窄适配入口。重复提交恢复既存记录，不能再次启动。生命周期消费者必填且默认不启用，不经过旧 Bridge/Registry 的隐式历史、自动恢复或 SDK 路径。

保留 HR 已配置的模型、工具策略、兼容配置和网关配置；冻结文字及身份不被消费者修改。启动或消费者失败时保留 claimed 状态供核对，不自动改为 interrupted、不重跑。

未启用生产 v5、未声明持久终态能力、未修改 index/模型配置；没有访问生产、应用迁移、推送、发送飞书或调用真实模型。所有数据库和服务器文件来自测试自有临时目录。

## 初轮 TDD 与复验

首个有效 RED 为真实 HTTP 重传用例期望 202、实际返回 400；随后验证一份命令/意图/执行入口。覆盖机器 Bearer、可信 callback、缺失生命周期配置、权限/容量、实际 COMMIT 失败、不支持的附件/空白输入、取消归属保护及真实服务器拥有资源的关闭。

主会话在 `b5fae68` 重新执行：

```sh
node /Users/neo/Developer/work/metabot-dev/node_modules/vitest/vitest.mjs run tests/local-runtime-config.test.ts tests/local-runtime-store.test.ts tests/core-chat-v5-contract.test.ts tests/core-chat-v5-store.test.ts tests/core-chat-session-store.test.ts tests/core-chat-routes.test.ts tests/core-chat-v5-http.test.ts tests/http-server-cross-verify.test.ts
node /Users/neo/Developer/work/metabot-dev/node_modules/typescript/bin/tsc -p tsconfig.bridge.json --noEmit --composite false --incremental false
node /Users/neo/Developer/work/metabot-dev/node_modules/eslint/bin/eslint.js src/api/http-server.ts src/api/routes/types.ts src/api/routes/core-chat-routes.ts src/api/routes/core-chat-v5-routes.ts src/api/routes/core-chat-v5-runtime.ts tests/core-chat-v5-http.test.ts
git diff --check
```

8 文件 **182 tests passed，2.07s**，其中 39 项实际 HTTP 测试；编译器与 scoped lint 通过。仅最末端 PTY/网络执行边界替代，不将输入队列观察或对象重开冒充真实模型、进程退出、飞书验收。

## 评审修订

初审发现终端控制字符会被逐字 PTY 输入当作编辑、提交或中断按键，破坏冻结文字语义。修订 `5506629 fix(hr-runtime): reject unsupported PTY control input` 限定为新命令入场拒绝 C0（保留 LF）及 DEL；正常多行/Unicode 不改写，已存命令重复恢复不受影响。共享 parser/hash 与旧 PTY 不改。

真实 HTTP/PG 的 RED：合法重算 hash 的 ESC 输入期望 503、实际 202/claimed（1 failed，39 个测试被过滤）；加入最小拒绝后同测试 GREEN。最终新增三项测试覆盖 ESC/Ctrl+C/CR/TAB/BS/NUL/DEL 不留命令、序号或意图，无执行/日志泄露；支持的 LF/中文/Markdown 原样到进程边界；已存 claimed 控制字符命令重复恢复不新建意图。

主会话在最终 `5506629` 再跑上述完整命令：**8 文件，185 tests passed，2.03s**，其中实际 HTTP 42 项；编译器、六文件 lint 与 diff-check 无诊断。完整范围复审确认原 Important 已关闭，无新问题。过滤运行中的 skipped 不计为完整回归；最终回归无跳过或警告。

## 构建产物与已知基线行为

实际 TypeScript 构建后的 v5 runtime、route、API server 均可由 Node 导入，导出断言通过。但最初合并导入进程未自然退出，手动中断，退出码 130；不能记录为完整进程生命周期验证通过。

主会话用保存的同一 `b5fae68` 构建产物、独立子进程与 async_hooks 定位：runtime 单独导入自然退出 0；route/API 导入后仅剩 `src/web/ws-server.ts:107` 的模块级 60 秒缓存清理 interval。诊断子进程在 1.8 秒期限由父进程 SIGTERM 结束。基线 `9770c0d` 已有该 interval，API server 也已导入该模块；本切片未修改它。没有启动 API server 或模型。该基线模块寿命单列，未为本次任务扩展修改共享 WebSocket 逻辑。

生成目录已精确、可恢复地移至 `/Users/neo/.Trash/hr-.m02c-verify.1Y1Mf4`，不留工作树发布产物。源码/类型检查通过与自然退出是两种不同证据。

## 尚未完成的集成

- M03：真实 stream 消费、唯一终态和持久 outbox、回调恢复、Flywheel/Trace 投影。
- M04：真实执行器停止证据、重试许可、所有权明确的取消；关闭运行配置前必须对账，当前拒绝行为不是安全回滚证明。
- M03/M05：输入附件传输与成果处理；本切片只接纯文字，新附件命令明确拒绝，不静默丢文件。
- 当前终端不支持的 literal TAB/控制字符仍明确拒绝；不是支持任意冻结字符串的字面传输实现。后续能力声明/验收必须保留该限制，不能默认为已解决。
- P03：发送端鉴权序号协商与完整能力握手；当前内部非消费型序号查询不代表 HTTP 协商已存在。
- O02/O03：运行配置、真实进程故障及受控渠道验收。未获生产统计、切换或业务重放权限。

这些是明确的后续任务，不以本地测试数量替代业务可用性。

复审的四类跨任务核验事项已分别对应上述生命周期、附件/序号、取消/回滚及单列的基线 import interval；当前完成声明仅覆盖已验证的持久接受与受限执行入口。按已批准计划继续本地实施，无需 Owner 逐项确认。
