# FAE 外部钉钉身份自动接入

用户于 2026-10-23 确认：内部使用企业通讯录；外部任何通过个人钉钉授权、可记录
稳定身份和昵称的用户即可使用 FAE，无人工审批。匿名使用关闭。此前 2026-08-29
设计中保留匿名和仅邀请绑定的条款在这个范围内被本需求取代，其余隔离约束继续有效。

## 架构

沿用 Platform partner_operator 身份底座和 FAE-only launch/binding。企业登录逻辑
及 corpId/通讯录校验原样保留。外部用户不取得 Platform web session。

- 新增独立 DingTalkExternalIdentityProvider，固定 provider kind `dingtalk_external`。
  使用 openid OAuth，仅取个人 unionId/nick；不调用企业通讯录或要求企业 corpId。
  稳定 provider_subject 为 sha256(client_id) + ':' + unionId，避免不同注册间混淆。
- 沿用现有企业回调地址 `/api/v1/auth/dingtalk/callback`，外部 state 采用
  `fae-dt-v1.` 命名空间，整体摘要持久化；回调先分派该命名空间，失败不能进入
  企业分支。支持钉钉返回的 authCode，重复参数和 code/authCode 冲突必须拒绝。
- state 五分钟有效、一次性，并绑定 HttpOnly/Secure/SameSite=Lax 浏览器随机 nonce。
  nonce 不匹配不能兑换 code；登录发起和回调复用既有可信边缘限流。
- 外部首次 OAuth 验证通过后，用单个受限数据库事务创建 partner_operator 主体、
  加密个人标识/昵称、服务端外部用户 realm 和仅 ai-fae-agent 授权。再次登录复用
  同一主体；并发开户不重复，停用和已撤销授权绝不自动恢复。
- 新 SQL 只授予 app 角色，固定 search_path 和 provider/agent；要求刚消费的外部
  OAuth attempt；保留原 owner-only 人工管理函数。系统开户事件用独立严格审计，
  不伪造 owner、不放宽其他审计约束。
- provider 活性基于最近真实 OAuth 的八小时验证租约加本地停用；即时回调只有一分钟
  内存证明供 broker 首次验证，持久化后由数据库 verified_at 续验。无证明不能永久
  返回 active；不存在、过期、未来或损坏的证明均失败关闭。FAE binding 验证缓存
  上限仍为六十秒；租约过期要求重新登录，不静默切换 provider。
- 生产 factory 负责 purpose-separated partner identity keyring、content codec 和
  control DB；不能依赖 Agent Brain 开关。真实账号证据完成前沿用现有发布门禁。

## 可追溯性

FAE 已将服务器验证的 subject_id/type 写入 owner 列；继续使用这些权威列，不增加
FAE 昵称缓存或每轮身份网络查询。同步导入器校验 owner 与认证模式/内部用户一致，
从列推导 typed verified_subject 关联引用，忽略客户端 metadata 自称的身份。引用只含
随机 Platform UUID/type，不含钉钉 ID；它经加密副本到达受保护的管理员会话详情，
配合所有者的受审计身份详情查询解析到 Provider 昵称及 unionId。普通 FAE 账户、
答案与 SSE 不暴露原始 Provider ID。现有 HMAC 用户键继续负责副本分组。

新副本 reader 兼容旧形状和带 typed 引用的新形状；writer 通过明确 opt-in 开关才
发送新引用，以便先部署 reader 再切换同步写入。不默认为旧 reader 发未知字段。

## 验证和上线

先进行 httpx Provider 协议测试、真实一次性 PG 的迁移/权限/并发/撤销/审计测试、
callback HTTP 测试和 FAE 企业/外部两身份会话隔离测试。不会通过浏览器自动点击验收。

实际 AppKey 是否允许外部账号授权必须用真实外部账户确认。复用现有回调可避免新 URL
配置，但凭密钥存在不能推断权限就绪。既有生产门要求两个不同真实外部账号完成稳定
主体、隔离、撤销和重放验证；缺少该证据时不写通过标记或启用生产外部入口。

参考：钉钉官方浏览器 OAuth 文档
https://opensource.dingtalk.com/developerpedia/docs/develop/permission/token/browser/get_user_app_token_browser/

## 最新发布策略裁定

用户随后明确授权直接上线。平台/FAE均支持独立v2所有者授权文档：仅个人钉钉Provider、
授权时间、授权证据摘要和real_account_acceptance=pending；v1真实账号验收契约继续
保持。该变更只影响发布依据，不跳过钉钉验证、权限、加密、隔离、state/nonce、撤销或
审计。具体生产版本、验证结果和未完成真人验收见外部钉钉runbook最新发布记录。
