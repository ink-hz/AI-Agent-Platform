# HR v6 交付记录

2026-09-08 22:14 CST：三仓 v6 已上线，工程切换完成。尚无真实模型业务质量结论。

## 最终行为

一个主对话，岗位/候选人/材料按轮固定。建议动作只填入用户可编辑正文。Hannah 从固定 Team 角色包运行，通过三个受授权业务工具读取、提交成果、确认用户明确选择的标准；原生 Read 关联真实工具事件与已固定知识版本。

旧五按钮、独立岗位工作页控制器、隐藏罐头消息、Markdown 信封、任务与岗位包自动投影消费者退出；旧 SQL 保留为历史迁移，094 撤销运行调用权限。历史消息、文件、已确认标准和版本链保留。CLI 本地无归属卡片停止写入，不自动导入。

飞书私聊通过网页生成的 10 分钟关联指令绑定真实平台用户。岗位编号只在本人范围内解析；`/标准 J编号` 读取同一确认版本，`J编号 要求` 受理到平台同一持久执行，返回对话链接。`/建议 结果编号` 展示具体条目，`/确认 结果编号 条目编号,条目编号` 保存原用户消息，通过同一确认工具处理。缺失身份的通用分析不取得平台私有工具能力；附件仍经网页上传。网页可解除关联。

## 有效检查与范围

- 接口/数据库：真实 Web 身份、CSRF、授权，Worker Ed25519 签名、原 lease/业务 capability、持久幂等、同键异内容拒绝、过期/跨 owner/错范围拒绝。部分确认继承完整旧模块并保留真实确认人；飞书关联、身份不匹配、同 J 跨 owner、重复消息与部分确认通过。候选人从另一自有对话的文件经授权 grant 读取，不改绑文件或会话。
- 故障恢复：MetaBot `tests/hr-v6-execution-loop.test.ts` 与 Platform `tests/test_hr_v6_execution_loop.py`，一次性真实 PostgreSQL、独立 HTTP 进程、Worker、MCP stdio 子进程；实际终止并重启协调进程后继续原执行，旋转 lease 后工具恢复，结果和知识证据持久化；同会话 A→B 不带 A 的私有历史。最新通过耗时 86.48 秒；不重复运行。
- 明确替代：上述跨仓链路的 PTY/模型提供方边界被替换，文件实际读取并发出原生形状 Read 事件。不是实际 Claude Read 观察或真实招聘质量证据。JD 接口检查使用本地官网提供方替代，不称为实时官网验收。
- 前端：TypeScript 与 Vite 构建通过。新成果卡组件检查验证精确 resultId、仅选中条目进入可见草稿、未直接提交。没有重复浏览器巡视或已上线布局/滚动套件。
- CLI：公开 JD 版本/hash、刷新取消预算、可信能力文件/跨 J 拒绝已有定向检查；旧本地存储已删除。方法库既有参考内容质量检查沿用，不重做。
- 测试夹具：合并运行时发现旧测试留下可重新领取的 attempt/ready worker；已按自有测试数据范围撤销 worker、取消残留测试轮次。该清理不作为真实执行恢复证据。

## 生产只读观察与未验收项

初次只读检查：`position_context_versions` 所有状态 0 行，当前确认指针 0，HR 非终态轮次 0。新读取保留真实标准的实现仍必须存在；零行不等于无人使用。未查旧草稿数量、旧信封错误分布或无归属 CLI 卡片统计。

尚未提交生产模型消息，未做用户业务质量验收。没有同输入保留基线，未证实初始命令字节下降 50%；不能以工程机制或新提示词较短替代该数据。JD 刷新预算 60 秒、工具总等待 70 秒；真实官网首次耗时待观察。

## 实际发布结果

- Platform API / 独立 HR Worker / 本地 Signed Worker：`7bc7b042e18d47c180053eab307990a08baecde7`。发布前合并并保留已上线的公司情报版 `beeae7875eee47d29e82f4187cd10414d8398762`；新选材仍能带入按轮范围请求，账号切换、返回公司阅读、发送期间新增引用保留均经合并点组件检查。
- HR MetaBot：`6b1ff78952ff90d83f82c737eaabaf05fcf77ea4`，运行 `dist/index.js`；MCP 编译入口在正式安装目录完成 initialize。Team：`36b932b01f11e893dcca16ac8162b392b84fbb8c`，role manifest hash `92786276c247fe4ee4c1224cfb3232455a0c126cf51b9e54138775c4a46462f7`。三仓已推送 master。
- 正式 094 已应用，checksum `e742c86cb6f0e80b20c3f90e338c11fe4c968653140455bde57032422e3f1784`；临时 migrator owner 成员资格已撤销。旧任务/岗位包领取与草稿生产函数对 app 和 brain worker 均不可执行。Local Worker 原表增加三列，仅应用一次。
- 最终本地认证 readiness HTTP 200；云端签名观测 `ready=true`、`core_chat_collaboration_v6`、三个工具、准确 Team commit/hash。API health `ok`，HR Worker 运行，发布前后 HR 在途均 0，平台确认版本仍 0 行；没有伪造终态或重放历史任务。
- 公网页面资源：`index-DJaUEm9z.js` / `index-D3IZxH9U.css`；HTML 匹配，JS HTTP 200。没有另做浏览器业务验收。
- 切换修正：完整配置不在 Git 中，已原样保留上一 HR release 的私有 `.env` 与 `runtime-contract.json`，依赖链接使用上一实际 release 的依赖根；云端补齐 knowledge 两个 root 配置。首次启动被配置门禁拒绝，修正后才记录上线成功；未禁用归档/Flywheel，未变更模型、飞书 app 或其他 Bot。
- 云端除 API/HR Worker 外容器 ID 与镜像均未改变；非 HR PM2 进程 PID/重启次数一致，Nginx hash 一致。共享 Signed Worker 按本次 v6 必要范围重启并保持原持久状态。
- 磁盘记录：根盘最终可用 `39,912,714,240` 字节、61%；与发布元数据中的 preflight 比净增 `1,208,320` 字节。/data 最终可用 `71,781,330,944` 字节，29%；包含 `236,185,805` 字节 control 数据库备份。保留当前＋`beeae78`、`6777d22` 两版回滚源；旧源归档遵守 10 版/30 天。没有删除其他服务仍引用的镜像或业务数据。
- 发布锁释放，`/data/staging/orbbec-agent-platform/3e7d86c1df1143c78a4dd72575c4c2e7` 已清理。完整证据在 `/data/orbbec-agent-platform/release-metadata/7bc7b042e18d47c180053eab307990a08baecde7/`；本地 HR 配置和回退记录在 `/Users/agentops/AgentRuntime/instances/hr-bot/hr-web-v6/`。

文档提交不需要再次部署上述版本。用户直接使用主对话验证岗位相关性、证据和建议质量。
