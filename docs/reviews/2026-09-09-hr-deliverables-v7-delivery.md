# HR 场景成果与连续引用 v7 交付

2026-09-09 已发布。用户授权继续实现并自行完成必要验证；本次没有发送生产业务消息。

## 已上线行为

- 保留 Hannah 原有角色与七份参考资源，在原场景小节声明具体成果类型；不新增角色选择器，不恢复“关联飞书”或“招聘协作”短语菜单。
- 候选人分析 `hr.candidate-analysis.v2`、候选人面试方案、面试记录、沟通草稿有严格 schema 和候选人归属。分析/方案使用非空基准：已确认标准、官网版本或本轮用户材料；引用必须来自真实本轮工具读取收据。
- 主对话“继续分析”“准备面试”“整理面试记录”带入具体对象、可编辑要求及成果引用。“本次参考”可打开、移除，由用户明确发送。仅复用结果候选人的有效附件与岗位材料，不把原轮其他候选人附件混入后续草稿。
- 输入侧 `inputResultRefs` 纳入冻结上下文和命令哈希；工具只读本轮选中的确切结果。重复提交比较完整输入。原始成果及所有祖先材料撤权后拒绝使用；祖先按 turn 去重校验，避免重复递归放大。
- 标准确认必须由真实登录用户展示并选择条目、复核所选正文，再发送带复核声明的确认消息。实际冻结版本决定约束，省略输入引用或调用 v6 URL 不能绕过 v7 复核。服务端保存 `candidateDerived`；候选人成果没有确认为岗位标准入口。
- 岗位复盘角色规则要求去标识化，只保留岗位级模式；逐人评价保留在候选人成果。自由 markdown 不是内容防泄漏证明，正文仍依赖用户认真复核。
- 新执行采用 `core_chat_collaboration_v7`；v6 历史结果可引用，已有冻结命令不改写。无匹配场景继续 Hannah 基础角色。

## 实际发布版本

| 部分 | 提交 / 证据 |
| --- | --- |
| Platform API、云端独立 HR Worker、本地 Signed Worker | `1a705ebb4b6598c61bc0d767ff44e2afc038a539` |
| HR MetaBot | `672058ca5d316c9ada2a460e211f8ec5cca1b70a` |
| Team 固定角色包与知识投影 | `a87500e2d0b4d35eb28713fb04b8274964e76d23` |
| 角色清单 SHA-256 | `2645347d161a53c2bd95cdc00529973feea4e00831331e46b434a662c03348d5` |
| 页面 JS / CSS | `index-K0JwF2ER.js` / `index-UreVbVab.css` |
| 正式数据库迁移 | `095_hr_deliverable_inputs.sql`，正式迁移器执行；临时 owner membership 已撤销 |
| 本地 Worker 库 | `pending/worker_v7_tools.sql`，原 callback 版本约束扩展；原数据保留 |

三个代码提交均推送各仓 master。使用三仓 `.worktrees/hr-role-tools-v6`；根工作树及用户原有修改未改变。文档提交晚于运行代码，不因此重发服务。

角色包由 Team `scripts/build_hr_role_package.mjs` 从指定已提交 Git 对象生成 `role-package.json`；知识投影由 Platform `app.hr.reference_knowledge_release` 从同一 Team 提交构建。发布检查清单和每个文件 SHA-256；不是重新补发缺失的七份资源，本次角色修改随 v7 构建新包。

## 验证及明确未验证范围

- 接口 / 数据库：真实 HTTP、用户 session、Worker 签名、grant、lease、幂等与持久化，覆盖候选人结果、跨轮读取、方案到记录、篡改/跨范围/撤权拒绝及真实用户正文复核。审查修复后相关 v7 接口回归通过。
- 跨仓工程闭环：真实 Platform、Worker、MetaBot HTTP / MCP 和两个完整执行轮次，第二轮读取第一轮结果并完成；**PTY / 模型提供方由本地工程 fixture 替换**。未重复旧 v6 进程故障套件，本次没有新增故障注入。
- 契约 / 编译：v7 Python / TypeScript 哈希契约检查通过；两仓生成 schema 与清单逐字一致。MetaBot 编译、前端 TypeScript 与构建通过。
- 前端组件：条目改变使复核失效、确认正文绑定及具体候选人后续草稿检查通过。未做浏览器验收；未重复已有滚动 / 布局检查。
- 生产发布：API health 正常；认证本地 v7 readiness 正常；云端签名 `ready=true`、`core_chat_collaboration_v7`、Team 提交一致；公网页面资源匹配。其他 PM2 进程 PID / 重启次数、云端其他容器和 Nginx 均保持一致。
- **没有真实模型或招聘质量验收**；类型、接口、健康和工程闭环不代表回答质量已改善。

## 发布与恢复证据

云端记录：`/data/orbbec-agent-platform/release-metadata/1a705ebb4b6598c61bc0d767ff44e2afc038a539`，含数据库备份、迁移日志、发布前后容器 / Nginx / 磁盘、签名就绪结果与 `PUBLISHED`。

本地记录：`/Users/agentops/AgentRuntime/instances/hr-bot/deliverables-v7-20260909`，含权限受限的 PM2 原状态及 readiness；本地 Worker 数据库备份在 `/tmp/hr-v7-release/worker-before.dump`，权限 0600。

切换前 HR 非终态轮次为 0。根盘发布后可用 39,438,716,928 字节、61%；当前加两版回滚目录保留，归档执行原限额。云端 staging 与发布锁已清理。

若出现问题，按对应 turn / result / API 定位；不要重复本次测试或部署。v7 受理后的冻结命令不能回写成 v6；恢复需先停止 HR 新受理、核对在途并按既有完成 / 取消流程处理，保留结果和确认数据。岗位全链路页面重新展示不在本次交付范围。
