# 联合附件 fence 执行器：先行差异设计与阻点

旧848行 attachment_hotfix.py 不适合通过继承 Hotfix 或修改两三个常量实现此次要求。按根“旧脚本不适合安全最小改写时先给具体差异与阻点”的分支，本轮先交此设计，**没有生成可运行执行器，没有进行SSH、生产、模型或mock测试**。最终镜像尚未绑定，任何后续草案必须默认拒绝 execute。原脚本不改，读取字节已封存。

## 具体不兼容

| 原位置 | 必须改变的行为 |
|---|---|
| globals17–72 | 固定旧release/image/100–105和单worker，不可复用为此次受审配置；command的ENV也含旧PLATFORM_IMAGE，直接导入调用会隐式污染新cohort。 |
| Hotfix.peers224 | API和MinIO被视为不可变peer；此次应准确排除三个目标ID，但保留其余每个peer身份/镜像/状态/启动时间/重启策略的相等校验。 |
| stop_old482/recreate606 | 只控制一个old/new_id；不能证明API和worker都已退出后才动MinIO，也没有旧MinIO PID退出和同数据挂载证明。 |
| migrate/root_verified | 只验证100权限与旧固定ledger；必须绑定106精确checksum及四列授权/逻辑删除claim函数，并用已有受审root supervisor，不重新实现GRANT生命周期。 |
| recreate659–699 | 健康即设置unless-stopped、释放action/input锁，最后installed_awaiting_erasure_canary。新流程在awaiting_canary必须保留维护锁/marker、明确未完成，并由显式finalize验真实回执后才开放。 |
| fail_closed700 | 只能收敛旧worker/单新worker；此次必须收敛准确旧新API+worker，不重启barePUT。异常不能把未知daemon状态当已停；MinIO独立恢复策略不等于恢复写者。 |
| preflight | 没有停机前owner会话配置和真实身份回执前置；文件语法有效不等于当前真实owner授权有效。 |

可复用的仅是有限command进程组回收、四signal处理、protected/digest/atomic等小原语的审读经验。建议在新三目标专用模块明确复制必要原语并去掉全局旧image依赖，绑定新模块SHA；不继承Hotfix.run/recreate/fail_closed，不建立泛化fleet发布框架。复制的有限原语由针对性的真实本地子进程信号测试再验证。

## 新模块边界

目标文件 production/attachment_fence_release.py；固定三服务名platform-api、platform-attachments、platform-minio，拒绝配置第四目标。CLI仅prepare、execute、finalize；不带SSH、owner登录创建、模型、cookie获取或自动canary。依赖注入只在测试中用于command/clock/filesystem边界；生产入口不允许任意Python adapter路径或任意shell命令。

受审绑定文件需包含：schema版本、维护run UUID；准确公共origin；最终release40hex/API和worker镜像sha256；三个旧容器64hex ID/image和MinIO PID/StartedAt、期望数据挂载（source/destination/rw/type）及网络；准确compose/env/config/helper源码SHA；106文件与预期账本SHA；迁移supervisor源码SHA；owner私有配置绝对路径与SHA、预检回执路径/SHA及发行器SHA；备份receipt SHA；完整peer快照；各阶段有限timeout和总体deadline；finalize回执schema/发行器SHA。拒绝空值、占位hash、未知字段、不规范路径或重复目标、相对路径/symlink、非root-owned0600敏感文件。私有配置cookie/CSRF只能内存验证与散列，不输出到日志。

脚本自身常量 REVIEWED_BINDING_SHA 默认未绑定：prepare可诊断不完整输入并退出非零；execute/finalize在未固定binding SHA、self SHA、最终镜像时必须在任何写操作前拒绝。不能让操作员自填任意“reviewed=true”越过这个门。无生产有效配置样例和默认镜像。

## owner与finalize回执的两个当前真实缺口

现有 api_canary.load_config79 只校验owner UUID、cookie/CSRF字串和同源HTTPS。attachment_erasure_canary.identity114 会真实GET account，核owner/platform_owner/hard_stale=false并写ledger，但现有ledger不是绑定维护run、源文件SHA、有效时限和新cohort的专用预检回执。**不接受有人手写passed=true的JSON。**

最小补充契约：根独立运行受审只读owner预检，输出root-owned0600回执，绑定run UUID、config SHA、发行器SHA、真实HTTP requestID/status、owner UUID/origin、hard_stale=false、observed_at/expires_at；监督器停机前检查完整契约、文件不可替换身份和freshness。它不能离线证明一个已在服务端撤销的会话仍有效：若要“停机当时有效”保证，应由监督器在停机前调用固定SHA只读预检入口重新验证（不创建session），然后核其回执。需根明确采用该实时预检入口，或接受已验证回执的有限freshness边界，不能混称。

现有 attachment_erasure_canary.observe 永远写 physical_erasure_verified=false，正确地不把404当物理删除。因此finalize不能使用旧canary状态installed/404。需要根的真实version-aware observer输出独立回执：维护run、新API/worker IDs/images、同一owner/origin/配置SHA、canary attachmentID与准确key集合摘要、源hash、真实upload/delete操作receipt关联、106 ledger摘要、每key零payload/零delete-marker/至少一个版本级HEAD验证的零字节专用metadata fence、观察时间、所有unknown/timeout/失败为空。监督器只读核验已固定发行器/receipt SHA和交叉绑定，再复核当前daemon/peer/at-rest状态。密钥原文不进入公开receipt。缺失任一项留awaiting，不能finalize。

## 固定状态机及持久化

prepare（只读身份/绑定/备份/owner预检，写本地私有准备receipt）→ acquired（准确inode+token锁）→ old_writers_stop_pending（先持久目标）→ 两旧写者restart=no且停止证明 → minio_stop_pending → 同ID旧PID退出证明 → 同ID/image/mount重启证明 → migration106_pending/verified → create_new_cohort_pending → 两新ID先持久后restart=no → startup_capability_verified → start_cohort_pending → awaiting_canary。

每个daemon mutation前后核输入/peers，记录意图与准确目标后再调用；创建结果响应未知时按本run固定labels+服务名+期望image枚举，不能按宽泛name-prefix停止别人。新容器创建或receipt磁盘写失败不得启动；MinIO旧进程未退出不得启动cohort。awaiting退出不表示完成，所有锁/marker保留，cohort保持restart=no；是否开放公共ingress须由同一外层维护窗口明确约束，不因容器healthy擅自放行全部Bot。

finalize重新加载本run持久状态并验证锁归属和真实外部回执；验证通过后才设置新cohort生产restart策略，复核、持久finalize_verified，再释放本run锁/marker并记completed。任意中间失败，包括释放锁/写final状态失败，要停止准确新写者并保留失败证据，不能继续对外声称完成。不得用finally无条件解锁。

四信号或总体deadline：屏蔽重复中断仅限有限cleanup，TERM→有限等待→KILL精确owned subprocess group并回收；另行inspect daemon，不将客户端退出视为容器停止。cleanup停止新旧写者，逐个核restart=no/running=false。若Docker不可用，记录unverified、保留锁/marker，不虚报收敛。迁移helper为本run有receipt可证所有权时才按其既有撤权/停止路径回收，最后独立核membership/session零。不能杀其他peer会话。

MinIO停止/重启对共享附件读取有影响；API停止影响所有Bot HTTP/回调/成果上传。Feishu、本机signed worker、其他24容器中的无关服务不被此执行器停机。回退只能同fence协议/106兼容的新旧cohort，不能恢复旧barePUT API或worker。

## 因果测试（先RED，再新模块；当前未跑）

- 未绑定镜像/self/config/owner receipt、过期或错误owner、旧404-only验收：command mutation trace必须为空；完整真实形状模拟回执才能进入下一阶段。
- 旧API停止返回成功但inspect仍running；workerrestart仍unless-stopped；任何一个失败：MinIO stop trace不存在。
- MinIO旧PID未退出/同ID换image或data mount/重启失败：migration/start新cohort trace不存在，旧新写者保持停。
- migration106 checksum/permission失败：无新cohortstart；helper清理失败时“不verified”。
- daemon创建成功但响应丢失、或记录新ID磁盘失败：精确run标签回收新容器且不start；无关peer不被动。
- 第二新写者启动失败：第一新写者也被停，old不会start；awaiting marker与action锁保留。
- finalize缺真receipt、错run/cohort/config/key集合或payload残留：不启restart策略、不解锁；合格receipt→精确finalize链。
- 任意阶段peer/input漂移：失败收敛；callback把错误注入到实际下一状态而非只断言方法名出现。
- 真实本地sleep子进程对INT/TERM/HUP/QUIT、超时与二次signal的owned回收；mock Docker证明daemon状态核验，二者不冒充真实Docker生产测试。

这是准备进入新模块实现的最小差异设计。两个receipt的生产发行契约和最终镜像尚未具备，因此不应现在交付一个把空配置或任意JSON接受为授权的“可执行”脚本。根审阅差异/选择预检契约后，可实现未绑定即拒绝的草案及上述本地tests，后续仍须独立安全审读。
