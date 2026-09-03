# HR Agent Web 工作台发布验收记录

日期：2026-09-03  
状态：代码验收中，尚未发布生产

## 发布范围

本次只交付 HR Agent 网页工作台的会话、附件、生成结果、反馈与恢复能力，以及其
Platform 侧安全存储和运维基础。岗位生命周期、候选人智能和人才与组织情报属于下一阶段，
不混入本次发布。

冻结边界：不修改共享 Nginx、`/office/`、FAE、VOC、行政 Bot、Marketing Bot 或其他
应用目录；不向业务用户自动补发历史消息。

## 十项核心场景

1. HR 对话支持图片、PDF 和 Office 文件，单次最多五个新附件。
2. 同一会话已存在的有效材料不占本次五个新附件名额。
3. 旧附件接口保持关闭，新会话附件接口独立开启。
4. 附件处理 Worker、ClamAV 与 MinIO 只在私有 Docker 网络协作。
5. 长任务和检索失败可从原 Turn 恢复，未读状态由服务端持久化。
6. 失败的生成版本保留为证据，但绝不晋升为当前成功结果。
7. 删除、到期、授权撤销与部分失败重试遵循一年保留策略。
8. 输入框允许纯文本或已就绪附件发起请求，并保留原生 `✨ 发送` 按钮。
9. 回答支持复制；倒赞必须先选择原因，并可填写自由文本说明。
10. 当前结果可单独下载或批量下载，历史失败版本仍可审计。

对应自动化验收：

- `backend/tests/test_hr_workspace_acceptance.py`：7 项。
- `webui/src/pages/HrWorkspace.acceptance.test.tsx`：3 项。

## 数据与发布纪律

- 持久数据：`/data/orbbec-agent-platform/`。
- 发布 staging：`/data/staging/orbbec-agent-platform/<deployment_id>/`，成功或失败均由
  `trap` 精确清理本次目录。
- 根盘只保留当前版本和两个回滚版本；历史版本归档到
  `/data/archive/orbbec-agent-platform/releases/`，最多 10 个且不超过 30 天。
- 发布包排除数据、上传、日志、索引、数据库、虚拟环境、`node_modules` 与模型缓存。
- 发布前检查 `df -B1 / /data`；根盘不足 25 GiB、预计不足 20 GiB 或发布后超过 75%
  均拒绝发布。
- Docker 仅清理本应用无容器引用的旧镜像，保留当前和两个回滚镜像，不执行
  `docker system prune -a`。

## 本地验证证据

已通过：

- HR 工作台后端核心场景：`7 passed`。
- HR 工作台网页核心场景：`3 passed`。
- Task 15 附件与部署聚焦回归：`233 passed`。
- Cloud 部署测试：`85 passed`。
- DingTalk 部署测试：`30 passed`。
- Execution Worker cloud deploy 回归：`36 passed`。
- Shell 语法与 `git diff --check`。
- WebUI 全量：`91` 个测试文件、`801 passed`，production build 通过。
- MetaBot v4 根测试：`1060 passed, 1 skipped`；CLI `36`、MetaMemory `42`、
  Skill Hub `6`、Server `359`，bridge TypeScript build 通过。

Platform 全量后端正在本次最终提交前重新执行，最终结果补入发布报告。

## 视觉改版

HR 工作台采用全高三栏结构：252px 会话历史、弹性对话阅读区和 296px 会话材料栏。
视觉语言由通用后台蓝切换为克制的鼠尾草绿，收紧标题、边框和阴影；输入区固定在阅读流底部，
桌面端保持稳定阅读宽度，窄屏按 1260px 和 720px 两级折叠。改版没有改变会话、附件、反馈
或下载的数据契约。

## 生产发布报告

以下项目必须在实际发布后填写；未取得证据前不得标记完成：

| 项目 | 结果 |
|---|---|
| 发布前 `df -B1 / /data` | 待执行 |
| 发布后 `df -B1 / /data` | 待执行 |
| 新增文件与目录大小 | 待执行 |
| 当前版本 | 待发布 |
| 两个回滚版本 | 待发布 |
| 删除或归档的历史版本 | 待发布 |
| staging 已清空 | 待验证 |
| 当前及回滚 Docker 镜像 | 待验证 |
| HR 页面与附件 API HTTP 验收 | 待验证 |
| 其他应用或共享 Nginx 修改 | 必须为“否” |

## 回滚

回滚只针对 Agent Platform 当前应用版本及本应用镜像。附件数据库迁移为增量迁移，回滚
应用时不删除持久数据。回滚前后必须再次检查 HR 页面、附件 API、`/office/` 与 FAE，且不
重启无关服务。
