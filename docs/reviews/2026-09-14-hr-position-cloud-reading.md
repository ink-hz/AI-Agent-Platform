# 当前 HR 工作台与旧界面退出验收

## 当前范围

基线 master 21091b13。用户最新裁定旧界面和专用代码直接删除，不维护兼容、迁移或历史阅读层。岗位当前标准/成果改读云端；当前岗位资料、候选人工作、情报资料/研究继续使用，岗位库采用列表。

已删除 31 个旧实现与专用测试文件，并删旧路由、宿主分支、API 方法及类型；本轮总差异净减少约 8600 行。岗位库为单列列表，候选人入口接当前现有面板，不自动发起工作。

## 接口验证

- `test_hr_position_cloud_reading.py`、`test_hr_agent_standards.py`、`test_hr_agent_b_routes.py`：12 passed（4.56s）。新用例保留真实一次性 PostgreSQL、正式会话签发/校验、CSRF、目录/授权仓储、岗位 owner 与标准/成果事务。已删除为旧 context/results 兼容额外建立的夹具及断言。
- 验证仅修改所选标准、未选原项保留、current 同准确 revision、陈旧确认 409；同 owner 其他岗位成果排除、准确旧修订仍可读。准确旧修订属于当前成果的版本能力，不是旧系统阅读区。
- `test_hr_agent_repository_views.py`、`test_hr_agent_result_files.py`：13 passed，覆盖准确文件与撤权；这些既有测试有身份/范围替身，不代替新增真实会话用例。
- `PYTHONPATH=backend:backend/tests backend/.venv/bin/python backend/tests/helpers/hr_position_cloud_reading_tcp.py`：本机实际 TCP HTTP 复验两条贯通用例。使用 127.0.0.1 随机端口和正式签发的测试会话，不输出令牌。外部钉钉 code exchange 和模型提供方为本地替身；不是独立 Worker 故障或真实模型业务验收。

## 前端验证

岗位读取与异步权限修复：聚焦 20/20，构建通过。已覆盖卸载/切换后的晚到下载、并发请求先 503 后 401/403、五阶段类型以及旧接口无调用。旧界面退出后的全量前端测试有 1074 项通过，两个旧断言失败已通过删除旧专用 P0 用例和已删除 CSS 的断言处理；随后 API/资源/官网/样式 53 项及岗位工作流/列表 17 项通过，最终 TypeScript 与 Vite 构建通过。未重复运行无关全量。

最后浏览器验收使用真实组件与本地 API 夹具：导航仅对话/岗位/HR 情报，当前成果可见；24 岗位单列纵向排列，1728px 宽度无横向溢出，滚动容器内容 6176px、高度 843px。此前已验证岗位工作流实际滚动和 Markdown 下载。此项不冒充真实后端或生产验收。

## 生产边界

2026-09-14 只读核实：生产 current 为 d9c3e8c6e908d5f1da8365df36c92a804eea659f；platform-api 容器 f5d70e692a62；`platform_control.hr_execution_cutover.phase = cloud`。本轮未发布、未发送业务消息、未进行生产业务写入或数据库数据删除。

## 主线

独立最终审查未发现合并阻塞，报告 `.superpowers/sdd/legacy-exit-final-review.md`。临时工作树从同步 master 创建，验证后回并主线；用户原始设计文件已备份，根工作区其他文件不动。
