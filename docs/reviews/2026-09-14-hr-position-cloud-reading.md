# 岗位标准与成果闭环验收记录

## 基线与范围

master 21091b13 开始，短期分支 fix/hr-position-cloud-reading。范围仅产品设计 P1+P2。

## 生产前置核实（只读）

2026-09-14，通过现有 SSH 身份读取 current 链接及 platform-api 容器中的只读数据库事务：

- 实际发布：d9c3e8c6e908d5f1da8365df36c92a804eea659f。
- 容器：f5d70e692a62，镜像短 ID 8a61c21b9778。
- `SELECT phase::text FROM platform_control.hr_execution_cutover` → `cloud`。
- 无生产写入、模型调用、消息发送或本轮发布。此项只确认执行相位，不代替受认证业务验收。

产品设计 §12 问题 5 已由此解决：当前不需要先切换执行器。文档中“legacy 时主对话整体 503”是过宽描述；本轮仅按已核实的 cloud 环境验证，不据该措辞推断全部 GET 行为。

## 验证

### 接口与数据库

- 新增贯通用例与相关回归：`test_hr_position_cloud_reading.py`、`test_hr_position_intelligence_api.py`、`test_hr_agent_standards.py`、`test_hr_agent_b_routes.py`，17 passed（4.69s）。
- 新用例保留真实一次性 PostgreSQL、正式会话签发与 cookie/CSRF 校验、目录/HR grant/授权仓储、岗位 owner 范围及成果/标准事务。外部钉钉 code exchange 和模型提供方被本地替代。HTTP 为 TestClient 进程内 ASGI，未称为 TCP 网络验收。
- 证明两项已确认标准中仅修改所选项，未选删除项保持不变；current 与确认回执同准确 revision；顺序陈旧确认返回 409。真实数据库并发序列化由既有 standards 测试覆盖。
- 证明同 owner 不同岗位成果被排除，岗位目录返回最新准确修订，旧修订正文仍可读；旧 context 返回非空历史，旧成果接口在新成果非空时仍返回自身独立存储的空列表。前端另测旧接口非空时只展示为历史。
- 扩大回归发现旧 context POST 预期已过期，已改为验证两条写路由退役且未调用服务写入；保留现行 GET/范围测试。相关回归最终全通过。
- 独立任务审查：修复过滤干扰项与部分修改保留证据后，spec/quality 均通过。

- 补充准确下载与后来关联语义回归：`test_hr_agent_repository_views.py`、`test_hr_agent_result_files.py`，13 passed（2.90s）。验证准确修订文件、撤权拒绝、跨 owner 隐藏和显式岗位关联；这些既有测试使用身份/范围替身，不替代上面新增的真实会话与岗位归属贯通用例。

### 本机 TCP HTTP 复验

- 用 `backend/tests/helpers/hr_position_cloud_reading_tcp.py` 复用已提交的两条贯通用例，通过绑定 127.0.0.1 随机端口的 uvicorn 和 httpx 实际发出 HTTP 请求；两个用例均通过。
- 命令：`PYTHONPATH=backend:backend/tests backend/.venv/bin/python backend/tests/helpers/hr_position_cloud_reading_tcp.py`。
- 复用一次性数据库与正式签发的测试 session/CSRF，不输出令牌；外部登录交换和模型提供方仍为本地替身。服务器为测试线程内的真实监听服务，未运行独立 Worker 或注入进程故障；此项不称为生产或真实模型业务验收。

### 其余验收

- 前端最终聚焦：`HrPositionWorkflow.test.tsx` + `hrLoopApi.test.ts`，14/14 通过；TypeScript/Vite 构建通过。
- 一次完整前端回归：1197 通过、2 条件跳过（随后新增晚到响应与历史去重断言，由最终聚焦回归覆盖，未因此重复整套测试）。既有大 chunk、jsdom scrollTo/localStorage 提示保留说明。
- 浏览器：Chrome 中使用真实组件与 CSS、合成 fetch 边界的本地预览。确认新标准正文与确认时间、旧历史独立入口、current/history 相同版本去重、历史成果只读正文、当前成果展开。主容器 scrollTop 从 0 到 639，scrollHeight 1852 > clientHeight 899，宽度 1728 下无横向溢出。
- 点击准确成果下载后，实际生成 1586 字节合成 Markdown 文件，内容与夹具相符。浏览器自动化的 download 事件等待超时，改以本次新生成文件确认结果；未把事件等待称为通过。
- 此浏览器验收不是受认证整站或生产验收；未另做移动端尺寸检查。未改 Worker/恢复路径，不新增进程故障注入；本轮未发布或进行生产业务写入验收。

## 审查与主线

Task 1 已独立审查通过。前端及最终分支审查、主线归并待收尾更新。
