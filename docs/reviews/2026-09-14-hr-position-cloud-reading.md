# 当前 HR 工作台与旧界面退出验收

## 当前范围

基线 master 21091b13。用户最新裁定旧界面和专用代码直接删除，不维护兼容、迁移或历史阅读层。岗位当前标准/成果改读云端；当前岗位资料、候选人工作、情报资料/研究继续使用，岗位库采用列表。

已删除 30 个旧实现与专用测试文件约 5999 行，清单在 `.superpowers/sdd/legacy-deleted-files.txt`。路由与当前组件接线待本次收尾验证；不能把中间删除量当成最终业务验收。

## 接口验证

- `test_hr_position_cloud_reading.py`、`test_hr_agent_standards.py`、`test_hr_agent_b_routes.py`：12 passed（4.56s）。新用例保留真实一次性 PostgreSQL、正式会话签发/校验、CSRF、目录/授权仓储、岗位 owner 与标准/成果事务。已删除为旧 context/results 兼容额外建立的夹具及断言。
- 验证仅修改所选标准、未选原项保留、current 同准确 revision、陈旧确认 409；同 owner 其他岗位成果排除、准确旧修订仍可读。准确旧修订属于当前成果的版本能力，不是旧系统阅读区。
- `test_hr_agent_repository_views.py`、`test_hr_agent_result_files.py`：13 passed，覆盖准确文件与撤权；这些既有测试有身份/范围替身，不代替新增真实会话用例。
- `PYTHONPATH=backend:backend/tests backend/.venv/bin/python backend/tests/helpers/hr_position_cloud_reading_tcp.py`：本机实际 TCP HTTP 复验两条贯通用例。使用 127.0.0.1 随机端口和正式签发的测试会话，不输出令牌。外部钉钉 code exchange 和模型提供方为本地替身；不是独立 Worker 故障或真实模型业务验收。

## 前端验证

岗位读取与异步权限修复：聚焦 20/20，构建通过。已覆盖卸载/切换后的晚到下载、并发请求先 503 后 401/403、五阶段类型以及旧接口无调用。整套旧界面退出后的跨路由回归、完整构建/测试和最终页面验收待更新。

## 生产边界

2026-09-14 只读核实：生产 current 为 d9c3e8c6e908d5f1da8365df36c92a804eea659f；platform-api 容器 f5d70e692a62；`platform_control.hr_execution_cutover.phase = cloud`。本轮未发布、未发送业务消息、未进行生产业务写入或数据库数据删除。

## 主线

独立审查与主线归并待最终退出实现完成。临时工作树从同步 master 创建，验证后回并主线；用户原始设计文件已备份，根工作区其他文件不动。
