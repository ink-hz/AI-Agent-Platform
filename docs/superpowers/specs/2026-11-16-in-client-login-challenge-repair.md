# 钉钉内免登凭证初始化修复

## 证据与责任边界

交接来源：制造仓库 `docs/integrations/2026-11-16-dingtalk-in-client-login-issues.md`。开发基线为主线 `08ec59d77a497e8779492a0db8db6b01b5065ec7`，在隔离工作树修复，根工作区与制造仓库的用户材料保持原状。

2026-11-16 只读生产核对：API 容器 `8ec16084d0f943429cea1bc264ab17e5a9e01012edc073c0a4919abfd147a5fb`，镜像 `sha256:0c4ee83340b0843e6cf369a70bf2970e66b3ddb3da4af35ff711d6f5c5006bfe`，引用 `orbbec-agent-platform:manufacturing-v2-08ec59d7-ae511add`，healthy；实际 `PLATFORM_RELEASE_SHA=08ec59d77a497e8779492a0db8db6b01b5065ec7`。该捕获不代表本次修复已经发布。

匿名 HTTPS `/manufacturing` 返回 200 且不签发登录 challenge；`/login?return_path=%2Fmanufacturing` 返回 200 并签发。制造 config 选择 `app_id=platform`，VOC config 选择 `app_id=voc`。未进行用户免登交换或修改生产配置。

## 同类问题架构复核

扫码与客户端内登录都依赖服务器签发的 HttpOnly challenge。SPA 的 history 导航无法履行签发职责，页面长期停留也不能证明凭证仍有效。此前仅在扫码入口加初始化，遗漏了另一个登录生产者，共同契约没有被共享表达和测试。

两种流程统一调用同一个私有初始化函数：每次登录先使用 `credentials: include`、`cache: no-store` 请求前缀内 `/login`，成功后才继续。钉钉内流程必须在获取配置和一次性 code 前完成此步骤。初始化失败直接返回错误，不申请 code、不交换、不重试；cookie 继续由服务器管理，前端不读取、不缓存或伪造。保留现有账号 CSRF、后端 challenge 校验、限流、应用路由和权限边界。新增一次同源 HTTP GET，与已上线扫码流程一致；无模型调用或 token 成本。

独立审查继续追踪根路径与预览路径的消费者契约，确认预览另有两处既有缺口：CSP 仅允许前缀内 `/api/`，阻断前缀内 `/login`；config 查询发送无前缀的返回路径，真实服务端校验会拒绝。共同原因是前端只替换了请求 URL，未完整表达环境内登录契约，mock 也没有执行 CSP 与真实路径校验。修复在预览 CSP 中仅增加完整的 `/login` 地址（不扩大到根站或整个前缀），config 返回路径与扫码一致使用 `platformPath`。服务端补真实 `DingTalkWebAuth` 的配置路由用例验证应用选择、拒绝跨环境路径，并单独测试公共登录页和制造页的 CSP。

## 测试与验收

- 先用缺失 challenge 的根路径及预览路径测试复现失败，再实现共同初始化。
- 覆盖制造、office、VOC、平台首页的顺序和应用选择；失败及慢响应期间不得申请 code；超过十分钟再次尝试必须重新初始化；已有会话仍发送 CSRF。
- 现有扫码测试基线 4 项中 3 项失败：fixture 遗漏当前 `loginMutationHeaders` 的账号 GET，导致错误断言。只更新 fixture 以保留该安全边界，再验证扫码回归。
- 后端接口与凭证/应用选择回归、前端登录回归及生产构建分别记录结果。提供方 SDK 在组件测试中替换；不称为真实钉钉登录验收。
- 钉钉 PC、移动端的制造直达免登及其他入口回归由用户验收，浏览器不由开发 Agent 操作。

## 独立待核项

制造平台当前按平台主应用处理，实际钉钉入口归属仍需确认。独立新应用只有取得可信配置后才能登记，不根据名称猜测或复制其他应用凭据。

VOC 的提供方 errcode 88 与缺失 challenge 是不同故障。交接日志证明已到达钉钉并被拒绝，但不能仅据错误码断定具体是发布状态、可见范围还是权限；这三项及错误 request_id 需在 VOC 应用后台核实。本次不改 token 重试策略、不把提供方拒绝视为成功。制造角色开放也不在范围内。

## 开发验证结果（2026-11-16）

| 项目 | 结果与边界 |
| --- | --- |
| 新内免登最小复现 | 初版 18 例均 RED；实现共享刷新后转 GREEN。预览真实路径纠正后另外复现 4 例 RED，再完成路径修复 |
| 预览 CSP | 新契约用例先 RED，证实缺失 `/login`；精确放行后 GREEN |
| 前端最终回归 | `authInClient` 18、`authQr` 4、`auth` 136、`LoginPage` 20，共 **178 passed，0 failed** |
| 后端初始回归 | Auth API、web session security、in-client registry、identity rate limits：**317 passed，56 deselected，0 failed** |
| 后端最终定向回归 | 修改 CSP 并补 9 例后 Auth API **216 passed，0 failed**；其余 110 例的源码未变。合计 **326 个不同后端用例通过** |
| 构建 | 最终 `npm run build` 成功（TypeScript + Vite）；保留既有 >500 kB chunk 提示 |
| 独立审查 | 首轮 2 项 Important 已修复，最终 Ready to merge: Yes；Critical / Important / Minor 均 0 |
| 未执行 | 56 项需隔离 PostgreSQL 的测试、真实钉钉提供方登录、PC/移动端浏览器及发布后生产正向验收。无模型调用或业务消息发送 |

后端保留 TestClient 的 per-request cookies 弃用提示，未绕过授权或签名。测试中的钉钉 SDK/提供方与存储替身不代表真实业务验收。完整日志位于根仓库私有目录 `tmp/dingtalk-in-client-challenge-20261116/`：`frontend-red.log`、`frontend-preview-config-red.log`、`backend-csp-red.log`、`frontend-final.log`、`backend-regression.log`、`backend-auth-api-final.log`、`frontend-build-final.log`、`production-baseline.json`。

代码验证通过，按仓库要求归并主线；本次尚未部署，生产保持上述旧镜像。制造应用归属与 VOC 后台配置仍为独立待核项。
