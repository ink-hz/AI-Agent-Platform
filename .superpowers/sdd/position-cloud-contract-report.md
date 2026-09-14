# HR 岗位云端读取契约报告

## 结果

`backend/tests/test_hr_position_cloud_reading.py` 在显式 `cloud` 阶段的一次性 PostgreSQL 上验证新云端标准/成果与旧 context/results 读取边界。`backend/tests/test_hr_position_intelligence_api.py` 同步记录旧 context POST 写路由已经退休，两个 POST 均为 404 且不会调用 create/confirm 服务。

## 真实边界与替代项

- 身份、session cookie、CSRF digest、目录当前性、HR grant、授权仓储和岗位 owner 校验均使用生产类及真实数据库记录。
- 外部钉钉 code exchange 由本地 owner UUID 替代；会话仍由 `DingTalkWebAuth` 与 `WebSessionRepository` 正式签发。
- 模型提供方由直接构造 `ModelReply/ToolCall` 替代。`save_result`、标准确认、revision CAS、owner/岗位过滤和数据库持久化使用生产代码；未向成果/标准表伪造成功行。
- HTTP 使用 FastAPI `TestClient` 的进程内 ASGI 请求，不是 TCP。未运行生产调用、真实模型、Worker 故障或浏览器验收。

## 契约证据

- 先确认两项标准为基线，再提交 replace/remove 两项建议且只选择 replace：已存在的第二项保持不变，current 精确等于新 revision；再次使用陈旧提案返回 409 和当前 revision。此处是顺序 stale CAS；并发序列化由既有 standards 回归覆盖。
- 新成果正常保存并在同一 work 内精确修订；同 owner 的另一岗位成果作为干扰项，被目标岗位过滤列表排除。列表返回同 result id 的最新 revision，准确旧 revision 仍返回第一版正文。
- 云端新成果已非空时，旧 `/api/v1/hr/positions/{id}/results` 通过真实 `HrToolService(ExecutionRelayRepository)` 返回 200 空列表，证明旧、新结果来自独立存储。旧 context GET 在 cloud 阶段继续返回正式 repository 保存并确认的历史 context。
- 其他 owner 岗位的 current standard 与新成果列表均为 404；缺 CSRF 的标准确认是 403。

## 验证命令

```text
.venv/bin/python -m pytest -q \
  tests/test_hr_position_cloud_reading.py \
  tests/test_hr_position_intelligence_api.py \
  tests/test_hr_agent_standards.py \
  tests/test_hr_agent_b_routes.py
```

最终结果：`17 passed in 4.69s`。未覆盖 later-link 使结果正文 `objects` 不含岗位但岗位目录仍合法包含该结果的情形；前端需按岗位列表成员关系与 ExactRef 判断，不应额外要求正文包含岗位。
