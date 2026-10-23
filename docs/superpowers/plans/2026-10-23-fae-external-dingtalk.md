# FAE 外部钉钉实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 个人钉钉验证后自动获得可追溯的 FAE-only 身份，企业认证边界保持完整。

**Architecture:** 独立个人 OAuth Provider；Platform partner 身份底座内原子开户；带浏览器
绑定的共享回调只签发 FAE launch；权威owner关联经加密副本支撑管理员受审计查询。

**Tech Stack:** Python/httpx/FastAPI, PostgreSQL security-definer functions, pytest。

## Global Constraints

- 企业 corpId 和通讯录校验不放松。
- 外部身份只可使用 FAE，不签发 Platform web session，不访问内部管理数据。
- 凭钉钉验证自动开户，无人工审批；停用和撤销不得被再次登录恢复。
- 不用 computer use；生产不运行 Agent 评测；真实账号证据不得伪造。
- 机密只通过受控 secret 文件和加密存储，不打印凭据或在普通响应暴露 provider ID。

### Task 1: 独立个人 OAuth Provider

Files: create `backend/app/control_plane/dingtalk_external_provider.py`,
`backend/tests/test_dingtalk_external_provider.py`。

Interface: `DingTalkExternalIdentityProvider(*, client_id, client_secret, redirect_uri,
verified_at_for_subject, now, transport=None)`，kind=`dingtalk_external`，
`begin_auth(state)->str`、`async finish_auth(callback)->VerifiedProviderSubject`、
`async check_subject(provider_subject)->Literal['active','inactive']`、`async aclose()`。
`verified_at_for_subject(subject)` 为同步可调用，返回 datetime 或 None；now 为可注入
UTC 时钟，默认当前时间。回调内短时证明最多1024条、60秒，持久证明最大8小时。

- [x] 先写失败测试：authorization URL 只含 openid、不含 corpid；回调只进行一次 token
  POST 和一次 profile GET，没有企业查询；无 corpId 也可解析 unionId/nick。
- [x] 使用 respx/httpx transport 验证 RED，然后实现固定钉钉 API、HTTPS 回调白名单和
  字段严格校验。provider_subject 为 sha256(client_id)+':'+unionId。
- [x] 验证空/畸形 unionId、昵称、token、authCode，冲突参数、Provider 错误、超时，
  异常输出无 secrets/token/原始身份；token兑换不自动重试或换协议。
- [x] 验证无证明/过期/未来/无时区证明不是 active；即时一分钟证明和数据库八小时
  租约可用；换 client_id 隔离；关闭 HTTP client。
- [x] 执行 provider 相关测试并提交这两个文件，保存 RED/GREEN 报告。

### Task 2: 原子开户、OAuth attempt、factory 和回调

Files: additive next available `backend/control_migrations/*_fae_external_dingtalk.sql`,
partner repository/service/provider、routes_auth/routes_partner、main/config 和相关 tests。

- [x] 创建失败 PG/HTTP 测试，验证带浏览器nonce的一次性state、authCode参数和
  `fae-dt-v1.` 分派；未知身份从首次真实Provider响应得到FAE-only launch。
- [x] 在单个受限事务内验证 consumed attempt，锁住所有HMAC transition候选，复用或
  创建外部realm/subject/operator/provider身份/FAE grant，写严格系统审计。
- [x] 为旧身份保留停用/撤销/错误realm拒绝；更新已验证昵称和verified_at但不恢复权限。
- [x] factory 加载 purpose-separated 新secret keyrings和已有content/control DSN，
  注入持久租约查询。共享回调在现有Platform Cookie处理之前分派外部namespace。
- [x] 管理员受审计identity详情由稳定subject查询provider原始ID及昵称；普通外部响应
  不包含这些字段，不签发Platform Cookie。覆盖真实PG并发、回滚、权限和防重放。
- [x] 所有定向测试通过并独立审查后提交；更新部署配置和secret准备工具。

### Task 3: FAE 身份追溯和联合验收

- [x] 复用FAE已有权威owner列；导入时校验owner/type/认证模式/user_id，替换metadata自称引用。
- [x] 仅FAE会话携带typed opaque UUID/type；reader兼容旧新形状，writer默认关闭，明确opt-in后才输出新字段。
- [x] 经加密副本到达受保护详情；保持HMAC分组；所有者受审计查询解析Provider昵称/unionId，普通响应无原始ID。
- [x] 独立审查两仓库差异。Task3 167项通过（59新增）；全平台组合复审无Critical/Important。
- [ ] 按官方应用实际允许范围执行两个外部真实账号验证；确认重复授权主体稳定、账号隔离、撤销与重放。
- [ ] 生成真实证据摘要及固定digest release文件，再启用生产外部入口。不得用工程测试替代真实授权证据。

## 实施记录

- Provider: f12b6772，234项相关测试通过；独立复审通过。
- Canonical launch: d5134e9c，402项相关测试通过；独立复审通过。
- 原子开户/factory/受审计查询: 10d5351a，904项定向测试通过，0失败/0跳过；独立安全复审通过。
- 加密副本引用: c62b7a29，167项定向测试通过；独立复审通过。
- 2026-10-23最终组合复审通过，未核验真实外部AppKey权限、未执行真人扫码或生产外部激活。
- 已采用中央受审计身份解析，不增加FAE昵称快照。先部署reader再opt-in writer，再按正常同步重导旧记录。

### Task 4: 独立Development真实授权验证入口

- [x] 仅preview DSN、独立keyrings的窄接口进程，复用真实Provider/Broker/Repository/Service；无问答、launch或管理页面。
- [x] 共用已登记回调URL，edge仅将external state命名空间导向Dev；企业callback保持当前API。禁止生产独立Nginx配置测试，先隔离容器验证，再受控完整配置检查。
- [ ] 两个不在企业通讯录的真实钉钉账号重复授权；仅保存加密身份和脱敏验收摘要，验证重放、状态撤销和账号隔离。
- [ ] 证据完成后关闭Dev入口/路由，按准确主线制品和固定真实证据启用生产外部身份。

选择理由：现有DingTalk应用回调已固定，直接开启生产才能收集证据会违反既有Dev门禁。单独Dev进程与preview库可验证实际授权和自动开户，不向生产主体授予测试权限。
