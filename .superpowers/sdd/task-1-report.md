# Task 1 report: HR 岗位云端读取 API/DB 闭环

## 结论

新增 `backend/tests/test_hr_position_cloud_reading.py`，在 `cloud` 切换阶段以本地一次性 PostgreSQL 验证岗位标准、成果及旧岗位 context 的读取闭环。未改业务后端，未调用生产，未发送业务消息。

## 设施与边界

- 数据库：`hr_agent_database(cutover_phase="cloud")` 启动一次性 PostgreSQL，运行正式 control/HR web/HR agent migrations，并显式进入 `cloud`。
- 身份：使用 `DingTalkWebAuth`、`WebSessionRepository`、`IdentitySecurityMiddleware`、`AuthorizationRepository` 与数据库中的真实 HR grant/session。外部钉钉 code exchange 由本地函数返回测试 owner UUID；会话签发、cookie 校验、CSRF digest、当前目录身份和授权判断未替换。
- 岗位范围：通过 `HrPositionRepository.position_for_owner` 校验真实岗位归属；另建有真实目录外键的其他 owner 岗位，当前 owner 的读取返回 404。
- 成果引用：准确旧 revision 通过 repository 的 owner/对象校验；成果由模型工具生命周期 `prepare_model -> commit_model -> execute_local_tool(save_result)` 保存和修订，未直接向成果/标准表插入成功数据。
- 模型：没有调用真实模型。测试直接构造 `ModelReply/ToolCall`，只替代模型提供方边界；工具事务、权限和持久化仍为生产代码。
- HTTP：FastAPI `TestClient`，属于进程内 ASGI HTTP 请求，不是 TCP 网络验收。没有把它称作真实 TCP canary；现有 TCP canary 面向完整 Worker/模型流程，本任务未重复启动。

## 覆盖

- 标准：缺 CSRF 的确认 403；选择一项后 current 与确认结果同 revision；未选项没有进入 current；相同旧提案用新幂等键再次确认返回 409，并携带准确 current revision。
- 成果：正常保存第一版，再在同一 work 内用准确 expected revision 修订；岗位过滤目录只返回同 result id 的最新 revision；准确读取第一版仍返回原正文和旧 revision。
- 旧读取：`/api/hr/positions/{id}/context` 在 `cloud` 阶段继续返回此前通过正式 repository create/confirm 保存的 context；准确旧成果 revision 同样可读。它们只证明历史读取可用，不代表前端可将其当作新云端授权。
- 范围：其他 owner 岗位的 current 标准和岗位成果列表均返回 404。

## TDD/命令结果

1. `.venv/bin/python -m pytest -q tests/test_hr_position_cloud_reading.py`
   - 首轮：2 errors；其他 owner 未建立真实目录身份，岗位外键拒绝夹具创建。
   - 接入真实身份后：1 failure；成果 kind/basis 合约拒绝无效测试输入。
   - 修订阶段：1 failure；不同 work 修订被真实 origin-work 范围返回 `scope_denied`。
   - 最终：`2 passed in 1.58s`。
2. `.venv/bin/python -m pytest -q tests/test_hr_position_cloud_reading.py tests/test_hr_agent_b_routes.py tests/test_hr_agent_standards.py tests/test_hr_position_intelligence_api.py`
   - `16 passed, 1 failed`。失败为既有 `test_context_api_creates_and_human_confirms_selected_modules` 对已退休 POST context 路由仍期望 200，实际 404；新增测试及标准/成果回归均已通过，本任务未恢复旧写接口。

## 未覆盖

- 未运行真实 TCP HTTP、独立 API 进程、Worker 故障恢复或浏览器验收。
- 未调用真实模型，因此不构成模型业务质量验收。
- 未覆盖生产写入或生产授权业务验收；生产状态只由主任务的只读核对记录。
- 未补测“后来通过 link API 关联到岗位但结果正文 objects 不含岗位”的合法目录语义；本次保存的成果原生绑定岗位。
