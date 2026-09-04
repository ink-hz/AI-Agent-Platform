# HR Position Spine R1.1 发布验收记录

日期：2026-09-04
状态：代码验收通过，尚未发布生产

## 发布范围

本次只交付 HR 招聘智能工作台 R1.1 的岗位主线：

- 将官网岗位快照幂等导入为当前用户可见的 Position；
- 从既有 HR 会话发现明确岗位、待确认岗位和多岗位引用；
- 通过 PositionDraft 显式确认后创建 Position，并原子绑定来源会话；
- 以 Position 为入口限定会话、岗位材料和生成结果的精确作用域；
- 支持岗位内新建对话、历史会话阅读、材料晋升/移除和结果下载；
- 保持所有者隔离、请求幂等、刷新恢复和硬过期只读边界。

本次不是 ATS，不包含候选人流程、招聘阶段、面试排期、Offer、入职、自动联系、自动淘汰、
自动录用，也不建设北森、OA、猎聘或 BOSS 直聘的字段、页面、Adapter 或模拟入口。

冻结边界：不修改共享 Nginx、`/office/`、FAE、VOC、行政 Bot、Marketing Bot、MetaBot 或
其他应用目录；不自动补发或改写历史会话消息。

## 数据与一致性约束

- Position、PositionDraft、会话绑定和岗位材料均存储在 Platform Control PostgreSQL。
- 所有读写按 `owner_internal_user_id` 隔离；越权对象统一表现为不可见。
- 官网导入和历史发现均使用稳定请求 ID，重复执行不创建重复岗位、草稿或绑定。
- 每一条导入投影/绑定/草稿及其来源证据在同一数据库事务提交，证据失败时业务写入回滚。
- 历史发现只建立岗位对象和关系，不重放 Turn，也不伪造历史 Agent 回答。
- 历史导入证据明确区分会话标题与具体消息；标题命中不会伪造为第 1 条消息。
- 多岗位会话按岗位分别形成草稿；未经用户确认不自动绑定。
- 会话创建请求重放必须保持最初的 Position 或 PositionDraft 作用域，跨岗位重放返回冲突。
- Position Detail 返回精确的 `conversation_ids`、`material_attachment_ids` 和
  `artifact_ids`；网页端不以数量或宽泛会话列表推断作用域。
- 只有用户上传附件可被明确设为岗位材料；Agent 生成附件保留为可下载结果。
- 官网同步失败不得清空最后有效岗位快照。

## 一次性导入命令

先执行只读预检；确认计数符合预期后，使用同一个 `run-id` 执行写入。命令只输出计数与
版本，不输出岗位正文、会话正文或密钥：

```bash
cd backend
./.venv/bin/python -m app.hr.import_cli \
  --owner-id <internal-user-uuid> \
  --run-id <stable-run-uuid> \
  --database-url-file <database-url-secret-file> \
  --content-keyring-file <content-keyring-file> \
  --registry-file <published-jobs.json> \
  --dry-run

./.venv/bin/python -m app.hr.import_cli \
  --owner-id <internal-user-uuid> \
  --run-id <same-stable-run-uuid> \
  --database-url-file <database-url-secret-file> \
  --content-keyring-file <content-keyring-file> \
  --registry-file <published-jobs.json> \
  --apply
```

生产运行前仍需单独确认 owner、已发布岗位快照、密钥文件与 `run-id`；本次代码验收不执行
生产导入。

## 自动化验收

R1.1 核心验收：

- `backend/tests/test_hr_position_spine_acceptance.py`
  - 官网岗位导入可安全重放；
  - 明确、歧义和多岗位历史会话处理正确；
  - 草稿确认后原子绑定会话；
  - 所有者隔离生效；
  - 导入不会创建或重放 Turn；
  - 领域模型不存在 ATS 禁止字段。
- `webui/src/workspaces/hr/HrPositionSpine.acceptance.test.tsx`
  - 首次加载失败后可刷新恢复；
  - 只展示 Position Detail 明确返回的会话；
  - 新对话始终携带当前 `positionId`；
  - 用户附件可晋升为岗位材料，生成结果保持可下载；
  - 页面不出现北森、BOSS、猎聘、Offer 或候选人漏斗入口。

本地最终验证：

- Platform 后端全量：`4892 passed, 3 skipped`；另有 246 条既有 Starlette
  TestClient cookie 弃用警告。
- R1.1 后端聚焦验收：`68 passed`；另有 10 条同类既有警告。
- WebUI 全量：`99` 个测试文件、`851 passed`；jsdom 输出既有 `scrollTo`
  未实现提示，不影响测试结果。
- WebUI production build：通过；Vite 仅提示既有大 chunk 优化建议。
- `git diff --check`：通过。

## 生产发布纪律

- 持久数据只进入 `/data/orbbec-agent-platform/`，不得写入 Release 目录。
- staging 使用 `/data/staging/orbbec-agent-platform/<deployment_id>/`，并以 `trap`
  精确清理本次 deployment 目录。
- 发布包排除 `data/`、`uploads/`、`logs/`、`index/`、`answer_reviews/`、知识库数据、
  数据库、`.venv/`、`node_modules/` 和模型缓存。
- 发布前执行 `df -B1 / /data`；根盘可用空间低于 25 GB、预计发布后低于 20 GB，或
  预计使用率超过 75% 时停止发布。
- 根盘仅保留当前版本和两个回滚版本；更早版本归档到
  `/data/archive/orbbec-agent-platform/releases/`，最多 10 个且不超过 30 天。
- Docker 仅清理本应用无容器引用的更旧镜像，保留当前和两个回滚镜像；禁止执行未经
  目标核验的 `docker system prune -a`。
- 发布不得修改 Platform 之外的应用，不修改 `/office/` 路由，不重启无关服务。

## 生产发布报告

以下项目必须在单独授权的实际发布后填写；没有现场证据不得标记完成：

| 项目 | 结果 |
|---|---|
| 发布前 `df -B1 / /data` | 待执行 |
| 发布后 `df -B1 / /data` | 待执行 |
| 新增文件与目录大小 | 待执行 |
| 当前版本 | 待发布 |
| 两个回滚版本 | 待发布 |
| 删除或归档的历史版本 | 待发布 |
| 本次 staging 已清空 | 待验证 |
| 当前及回滚 Docker 镜像 | 待验证 |
| HR 岗位列表、岗位工作区及 API HTTP 验收 | 待验证 |
| 官网岗位最后有效快照仍可读取 | 待验证 |
| 其他应用或共享 Nginx 修改 | 必须为“否” |

## 回滚

应用回滚只切换 Agent Platform 当前 Release 和本应用镜像，不删除 PostgreSQL 持久数据，
也不反向删除已创建的岗位、草稿、材料或会话绑定。回滚前后必须验收登录、HR 岗位列表、
岗位详情、原 HR 会话工作台、附件下载、`/office/` 和 FAE；不得重启或改写无关应用。
迁移 065 暂时保留旧版登录函数 `consume_attempt_and_issue_session_v22` 的应用角色执行权限，
支持数据库先迁移、旧应用节点短时共存和应用回滚；待既定回滚窗口结束后再单独迁移撤权。
