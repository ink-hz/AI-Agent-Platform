# 独立停机前 owner / CSRF 预检

新增 production/owner_preflight.py，不创建session、不SSH、不执行生产或模型、不增加产品接口。只使用现有owner会话，执行真实GET account、错误CSRF空body POST、正确CSRF空body POST。默认HTTP三请求整体10秒POSIX墙钟上限，最大30秒；不是把HTTPX阶段timeout当整体上限。配置/源码读取和最后私有receipt落盘不宣称受HTTPdeadline控制。

## 无业务写入的承重路径

当前 middleware.py 先取得真实session/授权、检查origin，再对非safe请求验证X-CSRF-Token；错误token固定403“CSRF verification failed”。现有POST /api/v1/attachments/uploads的BeginUploadRequest要求original_name、declared_mime、declared_size；发送{}必然在FastAPI参数校验时失败，begin_upload函数及_upload_service.begin尚未调用。ConversationAttachmentRoute把RequestValidationError统一为422及固定“attachment request invalid”，不返回字段数组。预检精确核这两个响应，不接受任意422或JSON passed=true。

真实本地HTTP/PG测试同时检查5张业务表attachments/uploads/upload_write_attempts/processing_jobs/erasure_jobs计数不变，并以会被真实handler调用的_upload_service入口spy证明未进入上传服务。正常认证/授权可能写审计或会话bookkeeping，因此不称数据库绝对零写。GET200只证明owner/platform_owner/hard_stale_read_only=false，CSRF由后两次POST负/正例共同验证。

无副作用证明依赖已审服务端路由/中间件契约。receipt记录expected_server_contract两份源码SHA，但HTTP不能自行证明远端加载的是这些字节；联合host监督器须先绑定并核实实际运行API源码/镜像契约，不能拿本入口对任意未知新路由版本盲探。主体是合法已有会话，不支持登录或session制作。

## 回执与安全边界

私有配置仅允许五字段owner_id/session_cookie/csrf/public_origin/api_base_url，绝对普通0600文件、当前uid所有、NOFOLLOW及NONBLOCK打开后fstat；UUID与cookie语法校验，HTTPS同源，不信任环境代理、不跟随重定向。入口未输出cookie/CSRF/响应正文；测试断言receipt不含实际会话token。依赖api_canary.py的已审整体deadline函数，import前核固定SHA13fcb02d28132afbe84268b0b5576946f2bc32dbaea8f1c2f74abaf702e0abd5，漂移拒绝。

新receipt以O_EXCL/0600创建，逐请求先写requestID/method/path/sending并fsync，返回后写准确status/time；未知响应记transport_unknown且不重发。receipt绑定run UUID、config原字节SHA、owner/origin、script自身SHA、helperSHA与预期服务端源码SHA。三步都验证并复核config未变后才status=verified、csrf_verified=true，给观察时间及60秒消费freshness窗口。失败无成功回执；部分写/落盘失败不能供监督器消费。尚未包含联合cohort/106/finalize状态，这些由后续host监督器交叉绑定并实时再验。

60秒不是会话租约：服务端随时可能撤销身份；即使预检通过，canary第一次真实mutation仍必须接受实际认证/CSRF检查。首次生产预检现在仍缺用户真实凭据，未执行。

## 验证证据（均本地）

- RED：入口不存在，真实HTTP/PGfixture已建立，1failed1.38s，red-test.py/命令/日志保留。
- 初实现：1failed3passed3.17s，实际返回200/403/422但初版期待FastAPI字段数组，拒绝成功。诊断最小用例1failed1.17s证明真实路由统一响应。没有改API行为或降低为只看422。
- 修正后：6passed4.46s；随后加任意422拒绝、NONBLOCK配置文件保护与服务端预期SHA绑定，并格式化。
- 最终：**7passed5.54s**，含真实owner/CSRF/无业务写入、错误CSRF、错误owner提前止步、私有文件权限拒绝、现有422契约、真实慢流socket整体0.15s打断且单次请求/持久unknown、任意422拒绝。既有fixture仅替外部登录兑换，session通过正规DingTalkWebAuth→WebSessionRepository生成并真实持久；该测试setup不等于入口会生产创建session。S3为既有MemoryStore，未发生写入；慢流测试单独用无凭据loopback HTTP传输，不称业务或生产身份验收。
- Ruff最终exit0。4项Starlette TestClient timeout弃用警告如实保留，无新增skip。

final-source绑定最终脚本/test/真实路由/中间件/fixture/deadline helper字节；首次缺失入口RED与诊断/初实现各有保存源码。green-1中间版本未单独保存完整源快照，不把它伪称准确冻结版本；最终green源码完整可复核。最初lint3项诊断在工具输出（未单独保存raw文件），修正后最终Ruff命令/日志已保存。无需重复全套无关回归。此报告不称预检已生产通过，也不称联合发布执行器完成。
