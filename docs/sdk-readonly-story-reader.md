# SDK 只读需求 reader（B05-P01）

`ReadOnlyStoryReader` 是 R0-b `StoryReader.request` 的专用 HTTP 实现。它绑定调用者声明的 `SnapshotScope`、同一 actor 对象和显式提供的服务 Token，不执行 SSO、登录、凭据文件读取、Token 刷新、重试、账号开通或业务写入。

```python
reader = ReadOnlyStoryReader(
    origin=approved_origin, token=provided_service_token,
    scope=declared_scope, actor=bound_actor,
)
try:
    report = await collect_story_snapshot(reader, bound_actor, scope=reader.scope)
finally:
    await reader.close()
```

调用方负责取得经批准的服务身份、实例版本、产品范围和可见性证据。`reader.scope` 只读；scope 和 actor 引用仍为调用方声明，不能证明来源权限已核定。Token 应由调用方安全提供，不写入日志、报告、异常或代码。

## 传输边界

构造参数为 `origin: str, token: str, scope: SnapshotScope, actor: object, transport: httpx.AsyncBaseTransport | None = None`，无默认线上地址。origin 仅接受有效 HTTP/HTTPS origin（可带根斜杠和有效端口），拒绝用户信息、额外路径、查询、片段和控制字符。Token 非空且不得含控制字符，必须可编码为 UTF-8。不会读取环境、配置、凭据库或文件获得身份；HTTPX 设置 `trust_env=False`、`follow_redirects=False`、10 秒超时。

`async request(actor, method, path, payload=None, params=None, *, api_version=2) -> dict` 在任何 HTTP 前限定：

- actor 必须为构造时的同一个对象；method 仅字面 `GET`，API 版本必须严格为 int `2`。
- path 精确为 `products/{scope.product_id}/stories`，payload 必须为 `None`。
- params 恰为 `browseType=allstory, recPerPage=50, pageID=1..20`；两个数字均为严格 int，不接受 bool、字符串或浮点。

实际 URL 固定 `/api.php/v2/products/{id}/stories`，Token 按注入值编码透传。每次构造实际请求后删除可能由 HTTPX cookie jar 合并的 Cookie 头，响应关闭前清空 cookie jar；源返回 Set-Cookie 不会改变后续页认证。不会跟随重定向或因 401 重新登录、重试或创建账号。`async close()` 释放连接，可重复调用；关闭后请求被拒绝。

仅 HTTP 200 可以成功。流式读取累计最多 1,048,576 个解码响应字节（包括压缩响应解码后大小），超限即停止消费并关闭响应。这是响应正文接受上限，并非 HTTPX 内部解压瞬时内存保证。正文必须为 UTF-8、合法 JSON 对象，拒绝重复键、NaN/Infinity 和数值溢出的非有限浮点；envelope 必须有字符串 status，且成功值为 `success`。stories/pager 的业务结构与分页漂移检查仍由原 R0-b 核心负责。

## 错误与证据

| 来源 | 异常 |
| --- | --- |
| 401 | `ZenTaoAuthenticationError` |
| 400/403/404/409/422 | `ZenTaoRequestRejected` |
| 429、5xx、超时和传输失败 | `ZenTaoUnavailable` |
| 重定向、其他 HTTP 状态、超限、损坏编码、非法 JSON/envelope | `ZenTaoProtocolError` |
| 字符串 status 不为 success | `ZenTaoRejected` |
| 本地构造、scope/request 边界或关闭后请求 | `ValueError`，固定脱敏代码 |

异常只含固定代码，不附带源正文、URL、Token 或底层异常链。未添加 HTTP 观测接口，也不导出 headers/cookies/Token。原 R0-b `raw_response` 是解析后的 JSON，不是原 HTTP 字节；真实接入所需的原 HTTP 字节审计与脱敏由接入方另行负责。

定向验证只使用 `httpx.MockTransport` 和 `asyncio.run`，覆盖零请求拦截、固定 HTTP 目标和 Token、Cookie 后续页隔离、错误单次请求、响应关闭和大小边界、严格 JSON，以及真实 reader 与未修改 R0-b 核心的两页成功、第二页 401/非法 JSON/坏业务页/分页漂移。未启动应用工厂、DB、SSO、live 测试或实际网络。

```shell
# cwd: backend
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -B -m pytest -c /dev/null -o pythonpath=. tests/test_sdk_readonly_story_reader.py tests/test_sdk_story_snapshot.py -q -p no:cacheprovider
```

本包无路由、现有用户入口、配置、数据库或迁移变更。撤销时停止在调用方使用该 reader，恢复已有调用方式须单独检查其认证副作用；本包不批准任何带登录/建号链的真实采集。真实接入仍需当前实例/版本/服务身份/产品授权核验、批准的 GET 校准及外部证据管理；本包合成测试不放行 B-05 父包、导入、部署、业务验收或公开发布。
