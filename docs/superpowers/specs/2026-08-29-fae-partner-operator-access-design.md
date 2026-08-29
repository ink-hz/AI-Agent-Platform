# FAE 合作方客服统一能力与外部身份设计

**日期：** 2026-08-29

**状态：** 已完成产品设计，等待用户书面评审

**涉及仓库：** `AI-Agent-Platform`、`AI-FAE-Agent`
**不涉及仓库：** `Orbbec-Agent-Team` 的淘宝/天猫消息渠道接入不属于本项目

## 1. 背景

FAE 已同时承担公开客户咨询和 Orbbec 企业员工的专业技术支持。现有实现具备：

- `fae.orbbec.com.cn` 公开客户入口；
- `public_customer` 与 `platform_enterprise` 两种认证模式；
- Platform 钉钉身份经 60 秒单次 Launch Code 进入 FAE；
- 企业 Session、历史、附件和 Feedback 与 `internal_user_id` 绑定；
- `fae` 与 `ecom` 请求渠道字段；
- 同一个 FAE Agent Loop、知识库、模型、工具、图片和附件能力。

新增需求是让外包或合作方客服坐席登录并使用 FAE。已确认的首批使用者是操作淘宝、
天猫客服业务的合作方坐席，不是淘宝或天猫消费者。坐席的最终身份来源尚未确定，可能
来自千牛子账号、合作方 SSO、邮箱或手机号。

本设计解决合作方坐席的安全登录、稳定主体、会话归属、停用和审计。消费者聊天消息
自动接入、自动回复或回传千牛属于后续“电商渠道 Adapter”项目，不在本设计范围内。

## 2. 已确认的产品决策

### 2.1 FAE 能力完全一致

合作方客服与 Orbbec 企业员工使用同一个 FAE Core，不建立“合作方简版”：

- 同一个 Claude 模型及发布配置；
- 同一个 Agent Loop、Planner、Capability 和 Tool；
- 同一个产品知识库、来源、工程资料与诊断数据；
- 同样支持图片、附件、连续对话、来源展示和 Feedback；
- 同样的回答质量、超时策略和错误语义；
- 不因主体类型降低模型、裁剪知识、禁用工具或切换隐藏 Prompt。

身份类型不得隐式决定 `channel`。`fae` 或 `ecom` 是显式业务请求上下文，不是权限
等级；合作方登录不能自动触发能力降级。

### 2.2 数据一致不等于会话互通

“数据完全一致”特指 FAE 的业务知识、产品资料、检索来源和诊断数据。它不授予合作方
读取其他人的会话、附件、反馈、Trace 或 Platform 管理数据的权利。

所有已认证主体只能查看自己的会话和附件。Platform 所有者按既有权限查看、复审和审计
全部 FAE 会话。跨用户读取继续写审计日志。

### 2.3 身份来源延后选择

本设计冻结身份接口和安全不变量，不冻结具体身份 Provider。正式启用合作方入口前，
必须通过真实合作方账号完成 Provider 能力探测并形成 release evidence。运行时不在多个
Provider 之间静默切换。

### 2.4 Platform 提供身份底座

FAE 不自行建立用户名密码库。Agent Platform 负责合作方主体、身份映射、FAE 授权、
停用和审计；FAE 只消费 Platform 签发的短时 Launch Code 与后续 Binding 校验结果。

## 3. 目标与非目标

### 3.1 目标

1. 为每名合作方坐席创建稳定、不可猜测的 Platform `subject_id`。
2. 允许合作方坐席直接从 `fae.orbbec.com.cn` 发起登录并回到同一个 FAE 工作区。
3. 让合作方与企业员工使用完全一致的 FAE 能力和业务知识。
4. 让 Session、历史、附件和 Feedback 绑定通用主体，而不是只支持企业
   `internal_user_id`。
5. 支持按合作方组织和单个坐席启用、停用及审计。
6. 保持现有匿名公开入口和企业钉钉入口行为不变。

### 3.2 非目标

- 不在本项目接入淘宝/天猫消费者聊天消息；
- 不自动把 FAE 回答发送回千牛；
- 不建立通用外部客户账号体系；
- 不允许合作方进入 Agent Platform 首页、Agent 大脑、管理中心或其他专业 Agent；
- 不建立合作方管理员、多级审批、计费或商业化多租户；
- 不建立共享账号或静态公共密码；
- 不改变 FAE 模型、知识、Agent Loop 或生产公开域名。

本设计对早期“Platform 不支持外部客户账号”的非目标作窄范围修订：仅增加受邀请、
可撤销、可审计的 `partner_operator` 主体，不增加消费者账号或开放注册。

## 4. 方案比较

### 4.1 采用：Platform 合作方身份 Broker

Platform 建立独立的合作方主体与 Provider 映射。Provider 完成认证后，Platform 直接签发
只可进入 FAE 的 60 秒单次 Launch Code。合作方不取得正常 Platform Web Session，因而
不能进入 Platform 页面。

优点是复用现有 FAE Launch/Binding 信任边界、集中停用和审计，且未来更换千牛、SSO、
邮箱或手机号 Provider 时不改 FAE。

### 4.2 不采用：把合作方加入 Orbbec 钉钉组织

该方案会污染企业通讯录、部门授权和离职语义，并让外部人员与企业成员边界混淆。

### 4.3 不采用：FAE 自建账号或共享访问链接

FAE 自建账号会复制 Platform 身份、密钥、停用和审计逻辑。匿名链接或共享账号无法追责
单个坐席、隔离历史或撤销个人访问，不满足正式客服使用要求。

## 5. 总体架构

```text
未来身份来源
千牛 / 合作方 SSO / 邀请制邮箱或手机号
                    │
                    ▼
        Agent Platform Partner Identity Broker
        ├── partner organization
        ├── partner operator
        ├── provider identity mapping
        ├── FAE-only grant
        └── audit / revoke
                    │
                    │ 60 秒、单次、FAE audience Launch Code
                    ▼
             fae.orbbec.com.cn
                    │
                    ▼
                 FAE Core
      同模型 · 同 Loop · 同知识 · 同工具 · 同附件
```

企业成员继续沿用：

```text
DingTalk -> Platform enterprise session -> FAE Launch Code -> FAE Core
```

两条链路在 Platform 的主体类型不同，在 FAE 的能力配置完全相同。

## 6. Platform 主体与账号模型

### 6.1 不把合作方塞入企业目录

现有 `platform_control.internal_users`、`directory_members`、`web_sessions` 和部门授权都
依赖钉钉企业目录新鲜度。合作方不得伪装成 `internal_users`，否则目录同步、离职事件和
硬过期策略会产生错误语义。

Platform 新增通用 Agent 访问主体投影，保留既有企业表不动：

```text
agent_access_subjects
  subject_id uuid primary key
  subject_type enterprise_member | partner_operator
  status active | suspended | disabled
  display_name_ciphertext
  display_name_key_version
  created_at / updated_at / invalidated_at

enterprise_subject_links
  subject_id -> agent_access_subjects
  internal_user_id -> internal_users
  unique(subject_id)
  unique(internal_user_id)

partner_organizations
  partner_organization_id
  status active | suspended | disabled
  name_ciphertext / key_version
  created_at / updated_at / invalidated_at

partner_operators
  partner_operator_id
  subject_id -> agent_access_subjects
  partner_organization_id -> partner_organizations
  status active | suspended | disabled
  created_at / updated_at / invalidated_at

partner_provider_identities
  provider_identity_id
  partner_operator_id
  provider_kind
  provider_subject_lookup_hmac / lookup_key_version
  provider_subject_ciphertext / encryption_key_version
  verified_at / revoked_at

partner_agent_grants
  grant_id
  subject_id
  agent_id = ai-fae-agent
  created_by_internal_user_id
  created_at / revoked_at / revoked_by_internal_user_id
```

企业主体投影通过一次性迁移回填，但现有 Platform 认证、授权和目录代码继续以
`internal_user_id` 工作。通用 `subject_id` 首期只进入跨 Agent Launch、Binding 和 FAE
会话所有权，避免无关重写 Platform 全站权限模型。

### 6.2 Provider 适配接口

Provider 必须实现统一服务端合同：

```text
begin_auth(return_path) -> provider_redirect
finish_auth(callback) -> verified_provider_subject
check_subject(provider_subject) -> active | inactive | unavailable
revoke_local_binding(provider_identity_id)
```

`verified_provider_subject` 至少包含稳定 Provider 主体、认证时间和可选展示名。Provider
Token、Secret 和原始主体值只存在于 Platform 服务端；数据库查找使用版本化 HMAC，原值
使用版本化加密。不得把手机号、邮箱、千牛账号或 Provider Token 写入 Launch Code、URL、
访问日志或 FAE。

### 6.3 Provider 正式启用门槛

候选 Provider 必须同时满足：

1. 能取得稳定的个人坐席标识；
2. 能区分至少两名真实坐席；
3. 能判断账号是否仍有效或支持 Platform 主动停用；
4. 不依赖共享密码；
5. Provider 回调支持服务端 state、授权码或等价防重放校验；
6. 能在 Dev 使用真实合作方账号完成登录、撤销和重复回调测试。

淘宝开放平台的商家授权、消息订阅和千牛 API 受应用类型、AppKey、卖家授权和权限包
约束。公开 API 列表不能作为坐席身份能力已开放的证据；必须用真实店铺和应用完成探测。

## 7. 登录、Launch 与 Binding

### 7.1 合作方登录流程

```text
GET fae.orbbec.com.cn/partner/login
  -> 跳 Platform Partner Identity Broker
  -> Platform 创建短时 state
  -> Provider 认证
  -> Platform callback 服务端验证
  -> 查找或创建 partner_operator + subject_id
  -> 校验组织、坐席与 FAE grant 均 active
  -> 签发 60 秒单次 Launch Code
  -> 302 到 fae.orbbec.com.cn/app/#partner_launch=<code>
  -> FAE 服务端经私有 back-channel exchange
  -> FAE 创建自己的认证 Session Cookie
```

合作方不取得 `agent.orbbec.com.cn` 的正常 Platform Web Session。Platform callback 只允许
回到精确的 FAE 路径，不接受任意 return URL。

### 7.2 Launch Code

Launch Code 必须：

- 使用密码学随机值，数据库只存版本化摘要；
- 60 秒过期且只能消费一次；
- 绑定 `subject_id`、`subject_type=partner_operator`、`agent_id=ai-fae-agent`、
  Partner grant 和 Binding ID；
- 不携带原始 Provider 身份、合作方名称或个人资料；
- 只能由 FAE 私有 back-channel 交换；
- 重放、过期、错误 audience、已停用主体或已撤销 grant 一律返回 401/403。

企业 Launch 也逐步投影为同一通用合同，但必须保留 `internal_user_id` 兼容字段供现有
Platform/Flywheel 使用。合作方 Launch 不伪造 `internal_user_id`。

### 7.3 Binding 校验

FAE 对已认证主体最多缓存 60 秒 Binding 状态。Platform 的有效判定为：

```text
subject active
AND partner organization active
AND partner operator active
AND provider identity not locally revoked
AND FAE grant active
AND binding active
```

Platform 不可用时返回明确 503。已认证合作方不得静默降级成 `public_customer`。停用组织、
坐席或 grant 后，相关 Binding 与 FAE Session 最迟在 60 秒内失效。

## 8. FAE 通用会话主体

### 8.1 认证模式

FAE 将认证模式扩展为：

```text
public_customer
platform_enterprise
platform_partner
```

会话身份字段统一为：

```text
owner_subject_id uuid null
owner_subject_type anonymous_customer | enterprise_member | partner_operator
internal_user_id uuid null
identity_binding_id uuid null
```

约束：

- `public_customer` 没有 `owner_subject_id`、`internal_user_id` 或 Binding；
- `platform_enterprise` 必须有 `owner_subject_id`、`internal_user_id` 和 Binding；
- `platform_partner` 必须有 `owner_subject_id` 和 Binding，且 `internal_user_id` 必须为空；
- 已认证 Session 的查询、继续对话、附件、Feedback 和历史均以
  `owner_subject_id` 为权威所有权键；
- `internal_user_id` 仅作为企业主体兼容投影，不再是 FAE 唯一所有权表达。

### 8.2 登录 Cookie 与 CSRF

FAE 为企业成员与合作方签发 FAE 自己的 HttpOnly、Secure、SameSite=Lax Cookie。Cookie
不包含主体、角色或 Provider 信息。所有非安全方法继续校验精确 Origin 和独立 CSRF
Token。若浏览器同时存在冲突的已认证模式，FAE 必须拒绝歧义状态并要求明确退出后重新
登录，不能自行选择权限更高的主体。

### 8.3 历史、附件和 Feedback

- 已认证会话的事实源必须是 FAE PostgreSQL，不是进程内 `SessionStore`。现有
  `chat_sessions`/`chat_turns` 增加通用主体所有权；完整用户/助手消息按 Turn 持久化，
  需要继续对话的结构化 `SessionContext` 使用版本化加密 checkpoint 保存；
- 进程内 Session 只作为可丢弃缓存。缓存缺失、进程重启或另一台设备打开历史时，FAE
  必须从持久消息和 checkpoint 恢复，不得创建同 ID 的空会话或把旧历史只作为只读展示；
- `chat_sessions.user_id` 继续作为企业 `internal_user_id` 兼容投影，新增
  `owner_subject_id`/`owner_subject_type` 承担所有已认证主体的权威归属；不得把合作方
  Provider 原始 ID 塞进 `external_user_id`；
- 登录后历史按 `owner_subject_id` 分页，支持跨设备继续对话；
- Session 不得在创建后改绑主体或主体类型；
- 附件上传、绑定、读取、派生物和擦除均校验 `owner_subject_id`；
- Feedback 必须绑定当前主体拥有的 Turn；
- 合作方 A、合作方 B、同一合作方的不同坐席均不能互读；
- Platform 所有者的跨主体读取继续通过管理投影并写审计，不给 FAE 浏览器伪造 owner
  参数的能力。

### 8.4 能力与知识一致性不变量

认证主体类型不得进入以下选择逻辑：

- 模型 ID、thinking/effort、输出 token 或 Loop 预算；
- Capability Catalog、Tool 白名单或检索索引；
- 产品事实、SDK、来源、工程诊断数据；
- 图片、OCR、文档解析和附件大小策略；
- Fallback、拒答和错误处理策略。

如未来确有数据源限制，应另立资源级授权设计并由用户重新批准，不能通过主体类型条件
分支偷偷实现。

## 9. 页面体验

FAE 公网域名和主问答页面不变。公开页面增加清晰但不抢占主入口的“合作方客服登录”。

- 匿名客户继续直接咨询，不强制登录；
- Orbbec 员工继续从 Platform 专业 Agent 目录单点进入；
- 合作方完成身份验证后返回同一个 FAE 工作区；
- 登录后账号菜单显示坐席展示名和所属合作方，不显示 Provider 原始标识；
- 合作方能使用自己的会话侧栏、附件和 Feedback；
- 合作方页面不出现 Agent Platform、Agent 大脑或管理中心导航；
- 身份服务异常显示明确重试/退出，不出现“已降级为公开客户”的假成功。

首期合作方人员由 Platform 所有者管理，不建设合作方自助邀请或管理员页面。

## 10. 管理与审计

只有 `platform_owner` 可以：

- 创建、启用、暂停和停用合作方组织；
- 创建或绑定合作方坐席；
- 授予或撤销 FAE 使用权；
- 查看合作方主体状态和最近登录，不读取 Provider Token；
- 按既有 Review 权限查看合作方 FAE 会话。

以下操作必须写不可变审计事件，写失败则管理操作失败：

- 合作方组织和坐席的创建、启用、暂停、停用；
- Provider 身份绑定、重新绑定和撤销；
- FAE grant 授予与撤销；
- Launch 拒绝、异常重放和 Binding 撤销；
- Platform 所有者查看合作方会话或附件。

审计记录只保存内部 UUID、事件类型、结果、原因码和必要的脱敏前后状态，不保存原始
Provider ID、手机号、邮箱、Cookie、Token、问题全文或附件名。

## 11. 错误与失败语义

| 情况 | 行为 |
|---|---|
| Provider 登录取消 | 返回合作方登录页，明确 `authentication_cancelled` |
| state/授权码重放 | 401，审计 `partner_auth_replay_rejected` |
| 未绑定坐席 | 不自动创建开放账号；进入 owner 可处理的待绑定状态 |
| 组织或坐席停用 | 403，撤销 Binding，不转匿名 |
| 无 FAE grant | 403 `fae_access_denied` |
| Platform 暂不可用 | 503 `partner_identity_unavailable` |
| Launch Code 过期或重放 | 401 `launch_code_invalid` |
| FAE Binding 校验失败 | 清除本地认证 Cookie并要求重新登录 |
| 跨主体 Session/附件访问 | 404 或 403 的固定策略，不能泄露资源是否存在 |
| Provider 主体冲突 | 409，禁止按展示名、手机号近似值自动合并 |

## 12. 发布阶段

### 阶段 1：通用主体与所有权

- Platform 增加通用 Agent access subject 与企业主体投影；
- FAE 增加 `owner_subject_id` 与三种认证模式；
- 回填现有企业会话的通用主体；
- 保持合作方登录路由不可达；
- 完成公开客户和企业员工全量回归。

### 阶段 2：Partner Identity Broker

- 实现合作方组织、坐席、Provider 映射、FAE grant 和审计；
- 实现 Provider 抽象接口、state、callback、Launch 和 Binding；
- 使用 Reference Provider 在 Dev 验证合同，不对生产提供虚假登录。

### 阶段 3：真实 Provider 能力探测

- 使用真实合作方和淘宝/天猫店铺环境核对千牛坐席身份能力；
- 若千牛满足六项门槛，冻结千牛 Provider；
- 若不满足，选择邀请制邮箱、手机号或合作方 SSO；
- 记录 Provider 类型、身份字段来源、撤销方式和真实测试 evidence；
- 不配置多个运行时自动 failover Provider。

### 阶段 4：两名坐席试点

- 先授权同一或两个合作方中的两名真实坐席；
- 验证跨设备历史、附件、图片、Feedback、连续对话和停用；
- 观察错误率、身份校验延迟和会话隔离；
- 试点通过后再批量创建坐席。

未选择并验证 Provider 前，生产不得显示不可用的合作方登录按钮或“正在准备”页面。

## 13. 测试与验收

### 13.1 能力和数据一致性

- 企业主体和合作方主体返回相同的 FAE Capability manifest；
- 相同请求使用相同模型、Loop、Tool 集、知识索引和附件策略；
- 认证模式不得出现在模型或能力选择条件中；
- `fae`/`ecom` 渠道差异只能来自显式请求字段，不能由身份类型隐式设置。

### 13.2 身份与所有权

- 两个 Partner Provider 身份稳定映射到两个不同 `subject_id`；
- 同一 Provider 主体重复登录映射到相同 `subject_id`；
- 不按展示名、手机号后缀或邮箱近似值合并；
- 合作方不能访问任意 Platform 页面或其他 Agent；
- 两名坐席不能读取、继续或反馈彼此会话；
- 企业成员和合作方也不能互读；
- owner 管理投影跨主体读取写审计。

### 13.3 Launch、撤销与故障

- Launch Code 单次、60 秒、错误 audience、过期和重放测试；
- Provider state、callback 重放和 return path 测试；
- 组织、坐席、Provider binding 和 FAE grant 四类撤销测试；
- 撤销最迟 60 秒在 FAE 生效；
- Platform 故障不降级匿名；
- 冲突 Cookie、Session fixation、CSRF 和跨主体附件测试；
- 公开客户和现有钉钉企业入口完整回归。

### 13.4 真实试点门槛

使用至少两名真实合作方坐席完成：

1. 首次登录；
2. 第二台设备再次登录；
3. 各自创建、继续和检索历史会话；
4. 上传图片和文档；
5. 提交 Feedback；
6. 一名坐席停用后 60 秒内失效，另一名不受影响；
7. 相同产品问题与企业员工走相同能力和知识源。

## 14. 运维、回滚与不变项

### 14.1 不变项

- `fae.orbbec.com.cn` 域名和公开客户入口不变；
- 现有 Platform 钉钉登录、管理中心、Agent 大脑和 `/office/*` 不变；
- FAE 模型、知识库、Loop、附件和 Review 能力不变；
- 不新增 FAE 或 Platform 公网端口；
- Provider Secret 不进入前端、仓库或日志。

### 14.2 回滚

回滚只关闭 Partner Identity Broker 的生产入口、拒绝新 Partner Launch，并撤销 Partner
Binding。已有合作方业务数据保留为只读审计事实，不删除或改绑。公开客户和企业员工链路
继续运行，不因合作方 Provider 故障或回滚而重启、降级或切换模型。

## 15. 后续独立项目

当合作方坐席身份和 Web FAE 使用稳定后，再评估淘宝/天猫电商渠道 Adapter：

```text
淘宝/天猫消费者消息
  -> 电商渠道 Adapter
  -> 店铺/会话/消费者匿名化映射
  -> FAE ecom channel
  -> 客服审核建议
  -> 经授权回传千牛
```

该项目必须单独确认淘宝开放平台的聊天消息接收、发送、商家授权、坐席标识和数据合规
能力。第一期应以客服审核建议为默认，不在本设计中承诺自动发送。

## 16. 完成定义

只有满足以下全部条件，才可报告合作方 FAE 已正式开放：

1. 合作方与企业员工能力和业务数据一致性测试通过；
2. 两名真实坐席完成隔离、跨设备、附件和撤销验收；
3. Provider 类型和真实能力 evidence 已冻结；
4. 无共享账号、匿名 Partner grant 或静默身份降级；
5. 公开客户、企业钉钉、Platform、`/office/*` 全量回归通过；
6. 回滚演练证明只关闭合作方入口，不影响其他链路；
7. 交付报告列出代码版本、迁移、Provider evidence、审计证据和剩余限制。
