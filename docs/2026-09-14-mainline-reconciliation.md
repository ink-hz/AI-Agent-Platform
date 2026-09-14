# 主线整合记录（2026-09-14）

用户裁定：先维护干净的主线，再考虑 HR 工作台重新设计。任务 B 仍暂停在方案阶段，本次不改页面设计、不发布生产、不恢复旧执行器。

## 归并范围

开始时根目录 `master` 为 `48fa5860`，远端 `origin/master` 为 `a9d07746`，HR 发布承接分支 `feat/hr-cloud-loop-launch` 为 `e4113ee9`。远端主线与 HR 分支各有 2/186 个对方未包含的提交；本地主线还保留独立发布交接。只同步 HR 文档而未同步运行代码，导致根目录不能作为可靠的 HR 基线。

采用保留历史的普通合并：

1. `095663ef`：纳入远端主线，保留同步复发修复及 `cloud-platform.md` 运维说明。
2. `5069e351`：纳入本地主线，保留岗位工作流、两层情报、源聚合及同步恢复发布记录。
3. 后续测试/规则/记录提交在同一基线上完成，然后根目录 `master` 快进到整合结果，普通推送远端主线。无强推、无重置历史。

三次已上线 HR 提交 `544ec07c`、`7ad14f95`、`65e7fbd1`，当前记录的 UI 发布 `d9c3e8c6`，云端链及同步修复均为整合结果祖先。任务 A 的 `8baf6328` 也包含在内；任务 B 仅包含已交付文档。

## 冲突处理与生产代码

合并冲突仅发生在 HR 两份入口文档、旧交接摘要和两层情报设计记录。保留已实施阶段与最新上线信息；废弃本地主线过时的“仅有 A0 规格”描述。保留 09-12 岗位发布能力，但明确 09-14 云端主入口替换后旧引用不能直接续作。原始资料后续公司聚合说明优先于较早的单岗位列表说明。

`backend/app`、`webui`、`deploy`、`scripts`、`contracts` 相对 `e4113ee9` 无运行代码变化。`forced-import.sh` 及其已有回归测试与两个主线修复 `f6bf68ee` / `e615f9a5` 完全一致。没有用旧主线目录覆盖已发布代码。

既有发布证据与审读记录随历史归并保留；本次不借“干净”之名删除历史数据或重写已有提交。后续临时调试产物应放忽略目录，只提交必要且经过检查的代码、文档和验证证据。

## 发现并修正的旧测试

16 个后端模块首次回归为 **130 通过、8 失败**。8 项均来自 `test_hr_position_api.py`：它仍要求已在 `7b041174` 删除的草案创建/确认/合并/忽略、版本确认和旧会话绑定写接口返回成功。该测试文件内容与 `7b041174` 时逐字节相同。

本次仅调整此测试文件，未改生产接口：

- 六类退役写入口明确断言不可访问，且服务没有被调用；通过实际身份中间件时拒绝请求。
- 保留历史岗位包的 owner 校验、严格序列化、无缓存和错误隐藏；只读账号可读历史，写操作仍拒绝。
- 可写身份、幂等 UUID、请求体约束、CSRF、Origin 与 stale 身份拒绝改用现存材料提升/移除入口验证。
- 原假服务确认旧岗位包的成功断言退出；现行云端标准确认仍由 `test_hr_agent_b_routes.py` 与 `test_hr_agent_standards.py` 验证逐项确认、准确版本、冲突和持久化。

修正后岗位接口 **14 项通过**，这 14 项使用 TestClient、真实中间件及身份/服务替身，不是真实企业身份、网络 HTTP 或数据库验收。相关岗位/云端路由/标准四模块 **33 项通过**。其他首次通过模块未受测试文件业务改动影响。未将旧接口恢复为通过测试的手段。独立复审已确认当前契约与旧接口退出断言，没有生产代码变更。

## 验证范围

| 类别 | 本轮执行与边界 |
| --- | --- |
| 接口/数据库 | 云端路由、候选人、标准、成果连续引用和文件、面试记录、岗位 API、情报资料/研究、迁移与同步脚本相关回归；使用本地一次性 PostgreSQL。包含真实 HTTP 传输工程用例及 TestClient 路由测试；身份/提供方边界的夹具与真实企业登录分开，不称为生产业务验收 |
| 进程故障 | `test_hr_agent_worker_process.py` 本轮通过；自有本地 worker/数据库，模型提供方是本地工程替身，不代表真实模型质量 |
| 前端 | 整合后 `npm run build` 退出 0；`npm test` 退出 0，132 文件通过、2 文件条件跳过，1,193 项通过、2 项条件跳过；跳过项需要外部公司/专题 HTTP 证据文件 |
| 浏览器 | 未执行；本轮无页面行为变更 |
| 生产 | 未部署、未调用生产业务、未重复发布验收；Git 主线不等于线上当前提交 |
| 独立审查 | 检查双方祖先、同步修复字节、文档冲突和任务范围；测试修订另行复审。最终快进/推送以 Git 回读确认 |

后端首次命令覆盖以下模块（不是全仓后端套件）：

```text
test_hr_agent_routes.py                 test_hr_agent_b_routes.py
test_hr_agent_candidate_routes.py       test_hr_agent_standards.py
test_hr_agent_result_continuation.py    test_hr_agent_result_files.py
test_hr_agent_interview_records.py      test_hr_agent_runtime.py
test_hr_agent_worker_process.py         test_hr_agent_production_canary.py
test_hr_agent_migration_deployment.py   test_cloud_transport.py
test_cloud_replica_migration.py         test_hr_source_http.py
test_hr_research_http.py                test_hr_position_api.py
```

本地日志：`/tmp/hr-mainline-backend.log`（首次，保留 8 项失败）、`/tmp/hr-mainline-position-green.log`、`/tmp/hr-mainline-position-related.log`、`/tmp/hr-mainline-build.log`、`/tmp/hr-mainline-frontend.log`。最终相关回归消除了已发现失败，不把未运行的其他套件标为通过。

## 根目录材料保护

根目录共有 26 个未跟踪文件，其中 6 个与整合后将被跟踪的文件重名，逐字节比较全部相同：用户任务书及 5 份较早交付记录。更新根目录前将这些原件备份到 `/Users/neo/.codex/backups/ai-agent-platform-mainline-20260914/`，保留 SHA-256 清单；只让已核实相同的文件纳入正常 Git 跟踪，不删除其他未跟踪材料。

已有 HR 发布 worktree 中还有本地发布材料/虚拟环境，因此保留该 worktree，不做强制清理。其他工作树也未批量删除。后续开发默认从同步后的主线开始，历史任务书指定分支只说明当时定位。

## 剩余分支的处置

按整合候选计算，仍有 15 个本地分支 tip 不属于主线祖先；这不是 15 项尚未完成的功能：

| 分类 | 分支/处理 |
| --- | --- |
| 5 个补丁等价 | `feat/fae-independent-access`、`feat/hr-r12-parser-runtime`、`feat/hr-r12-task-result-projection`、`feat/hr-r12-workbench`、`fix/cloud-jcs-dependency`；代码已等价包含，不重复合入 |
| 6 个仅剩文档差异 | `feat/hr-cloud-loop`、`feat/hr-intelligence-experience`、`feat/hr-research-reading`、`feat/hr-role-tools-v6`、`fix/fae-issue-scope-quarantine`、`fix/hr-panorama-read-model`；保留历史，不用旧设计覆盖现行裁定 |
| 1 个后来已有替代实现 | `fix/hr-r12-candidate-document-download`；旧提交不再直接合入，当前代码已有后续下载实现 |
| 3 个旧行为差异 | `feat/hr-r12-resource-import` 的旧全历史导入、`feat/lodging-trusted-gender` 的旧字段处理、`feat/minimal-dingtalk-demo` 的旧演示环境；本次不重新启用，不因主线整理而扩大产品行为 |

分支名清理需要先确认各工作树中的未提交/未跟踪材料，本次不把批量删分支作为主线可靠的证明。

## 今后规则

已写入根 `AGENTS.md`：主线为共同开发基线，实际生产提交另查；完成验证和审查后及时归并，发布从主线中的准确提交构建；紧急修复同一收尾回并，避免后续发布覆盖；不强推抹掉独有工作，不自动恢复旧分支功能。工作台重新设计等待后续用户裁定。
