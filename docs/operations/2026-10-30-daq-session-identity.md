# DAQ 会话提问人身份：实际发布与验收

状态：已发布，生产只读消费路径验证通过。该任务不调用模型，不改 DAQ/相机回答代码或知识。

## 实际版本

- 身份实现主线提交：`2f539f59e2140116057626f3a5267055bcf63f69`。
- 实际平台 API、原 cloud-sync worker 发布提交：`c13433f4580a54629626daf9017b642d44bed009`。
- 实际镜像：`sha256:048e90d5bba6303fcb68f08f1ba4f69796295a171f1066c51be0d6af5acc687a`。
- API：`12918fbe2c5fbef2433842f27c23100a411068a2ed13506e87a49c3308ccc3ae`；worker：`8cf7efc060f3c58cd268a90c4ce073a57bcea4ba555e89a01d32dd4b6cf859ce`；均 running/healthy、0 restart。
- 控制迁移127：`cf2922c4dcca367e17b4c405d1236c98d9ba4d3f781c9d2fa80665b81b107696`。旧126 ledger与函数定义一致，临时migrator→owner授权已撤销。

镜像继承的OCI revision标签不代表完整运行来源；上述release ENV、受审增量清单、实物摘要和收据共同确定身份。72个运行文件逐件匹配发布manifest。

最终复核期间，独立Session展示批次又以本次048e90d5为基底发布了纯静态层。**当前API页面版本为`c6ee87f25e4562ee9ce84a4508491a3a089dbd4e`，镜像`sha256:15ad498db1afce24eb8064b72e64465643141dc6f745a44ebe12398053edc773`；worker仍为上述c134/048e90d5。** 该批次明确继承DAQ身份后端；本任务再只读核对5个身份后端文件逐件摘要不变，仅对改变的列表/回放显示路径运行3项DAQ身份组件检查，全部通过。未重复模型或无关已通过场景。身份功能的实际首发版本仍为c134，不把后续静态发布误记为本任务重新部署回答服务。

## 修复的完整链路

DAQ验证登录上下文 → 原有owner-bound事务持久化 → 现有云端源读取 → enterprise-only verified_subject → 既有加密副本 → 按Agent独立授权解析 → 列表/详情/每轮提问人。

生产源库17个会话的owner_subject_id、enterprise_member类型、user_id和metadata.authentication_mode一致；26轮可信metadata与各自会话无冲突。无需修改DAQ写入端。平台补齐projection、旧importer的metadata去伪、wire scope、导出稳定键、授权函数127、解析和显示；相机仍支持原内部/外部主体，其他Agent不能借用其观察权限。

身份缺失或冲突时不按昵称补猜。可信主体缺姓名档案保留引用并显示姓名不可用，避免整页503；未知归属清除旧副本自报姓名。DAQ原user/session/turn HMAC输入不变。

## 真实数据与验证

- 通过原10秒cloud-direct机制补齐17/17可信引用，无源库owner回写、临时业务记录或第二套同步。列表、详情、全部26轮提问人一致，引用逐条等于源适配器实际编码。
- 每条云端会话去除新增verified_subject后，与发布前规范化完整payload摘要全等；原业务键、问答、反馈和复审未改变。
- DAQ原表逐行摘要前后一致：17 sessions、26 turns、2 feedback、0 reviews。当前DAQ历史均有可信归属；身份未知/部分/冲突场景由Dev负例覆盖，没有伪造生产历史样本。
- 生产相机只读抽查内部、外部身份各一类及1个旧未知样本，姓名和每轮一致，旧未知仍未知；不是相机模型评测或全量历史重审。
- 相关平台304项通过；补profile/阶段发布后的158项、后续helper61项有重叠，不相加。前端5文件23项通过，TypeScript/Vite构建通过。独立Dev真实PG/受控writer/加密副本/登录Cookie HTTP列表详情与授权隔离通过；DAQ真实HTTP+PG账号切换2人2会话3轮通过。提供方验证与回答模型在Dev为明确测试边界，模型调用0。
- 最终发布控制器独立18项通过；Compose replace标签补正后worker16项通过，二者有重叠。代码、准确artifact及后置补验经过独立复审。
- 234项新旧公开构建资源HTTP200且摘要一致；health200，匿名sessions API及admin仍401。33个非目标容器的身份/配置/挂载/进程摘要一致，共享Nginx与行政服务guardrails通过。DAQ和相机回答容器未重启。两个全局发布锁均不存在。

生产身份验收使用既有有效管理DB会话，调用真实副本repository与授权resolver，即HTTP路由消费的同一数据路径。没有伪造Cookie或创建管理会话，不冒称生产登录态HTTP/浏览器验收。登录Cookie、中间件、列表详情的HTTP闭环在Dev完成；浏览器交互按项目约定由用户承担。

可核验DAQ会话：[平台详情](https://agent.orbbec.com.cn/admin/sessions/zxmcvgkeychqpzcdvqjdtluvvgyf2j36ihjugablyjgudaxdyrwa)。源归属、可信姓名及每轮比对保存在受限收据，普通代码仓不写入完整身份资料。

## 并行发布、失败与保全

首候选2f基于71330ae2准备期间，另一任务实际发布了86f5d9a2前端。首次迁移在改库/取锁前因API身份变化拒绝；127不存在、owner membership=false，原服务未切。本次重新保留该发布：其静态来源是b4c258b4源码加739ceb0c的两个组件，非739整仓构建。组合来源、405个跟踪文件差异及64项实物图独立核验；本候选c134为完整合并源码，保留两组件。分别记录backend baseline570587fc、static source-base b4c及739 overlay，不伪造单一基线。API保留现役49份Compose输入。

worker实际重建一次后已健康，但最初后置核对拒绝新增com.docker.compose.replace标签。原失败和锁保留；补正只接受由旧service和container-number确定的cloud-agent-sync-1，不忽略其他字段。独立RED/GREEN、真实runtime及远端重算Compose hash/inputs、Config/Host/Mount/Network/其他容器均通过后，才补写收据并释放own锁；未重复apply或重启worker。

## 备份与回退

现役加密备份集`20261030T011956089325Z-2da42c88`完成，check验证control、replica和MinIO三个密文及保留策略。发布目录`/opt/orbbec-agent-platform/private/daq-session-identity-20261030-c13433f4/`保留准确镜像、manifest、49份原输入引用、API/worker旧新选择、事务及回执；旧API86f5d9a2和旧worker71330ae2保留。

worker未重建前可使用原API事务rollback。worker重建后必须先恢复旧worker、等待正常同步清除DAQ新引用，再基于fresh快照新建并独立审查API回退事务；原API其他容器CID基线已改变，禁止修改旧快照或强跑旧rollback。加法127可保留，禁止恢复共享DB覆盖新业务记录。未执行生产回滚演练。

全部原始失败、独立评审和生产收据保存在DAQ仓Git忽略的`tmp/daq-session-identity-20261030/`，最终证据在`retarget-v2/`；旧v1材料仍保留但不作为实际发布版本。
