# Web/API 切换与独立飞书 MetaBot 范围核对

结论：不应把停掉独立 `metabot-hr` 飞书进程作为本次网页/API发布的无条件前置，更不能执行不存在的 `ENABLED=0` 方案。用户排除飞书；根设计第197/248行明确独立渠道去向未定不关闭。网页/API同一任务只能交给云端Loop，与另一个独立渠道保留其原执行器不是同一事项。

这不表示所有旧执行资产都可忽略：必须停止旧 `platform-hr-web-worker` 新认领并持久配置关闭，确认本地签名worker/MetaBot中没有可继续恢复的**平台HR在途任务**，并确认新API不绕过gate直连旧MetaBot。不能用“业务终态/计数0”替代最后一项进程/本地队列核验；若发现同一平台工作已交给旧本地执行器且仍可继续，先对那个工作完成/取消并确认停止，而非借此自动扩大为停独立飞书渠道。

## 承重路径

- `hr_agent/cutover.py` 用共享advisory lock，cloud只接云端，旧legacy仅可在legacy/draining_legacy继续；缺表仅兼容旧链，新云端fail-closed。
- `agent_brain/conversation_repository.py` 的新输入/旧对话入口检查legacy lane；`direct_command_binding.py:154–158` 每次offer重新串行检查，发现候选不等于允许派发；`execution_relay/recovery_v5.py:103–114`恢复也要求旧lane。
- `execution_relay/repository.py:269–275` 在cloud/draining_cloud从旧租约allowed agents去掉hr-bot。
- 102/104数的是平台conversation/turn/attempt/jobs/direct binding、candidate draft及新work/parse/intake；不枚举飞书本地session。104精准终态lineage只是避免把已终止运输信封误当活工作；未ack stop/活turn/不明lineage仍阻挡。105增加同cloud lane恢复，不新增MetaBot执行路径。
- 根设计第35行说明新runtime没有接DirectWorker/Relay/MetaBot；旧签名Worker→MetaBot是历史独立路径，停止它领取平台HR任务与关闭飞书会话可分开。

## 本机只读核对

实际agentops ecosystem中 `metabot-hr` 的script为src/index.ts、cwd为 `/Users/agentops/AgentRuntime/metabot`，`METABOT_ONLY_BOTS=hr-bot`，独立bots/state目录。未输出真实bots.json或任何凭据。该磁盘src/index.ts的飞书事件经event dispatcher进入MessageBridge（约116–134行）；本地API也存在core-chat/task执行入口，所以**不能仅凭进程名宣称飞书专用或网络隔离已验证**。发布必须以实际平台路由/gate和旧平台本地任务盘点证明隔离；此次只读源码不代表已检查所有运行连接或加载模块。`metabot-releases/current`另指向6ddbdefffede245d48ec20b43ee487c7ff2c732c，不能把该symlink误当ecosystem所列cwd源码身份；实际PM2窗口快照由根另外核对。

直接普通用户读取agentops目录被权限拒绝，后续使用现有 `sudo -n -u agentops` 仅read/ls/grep/hash成功；没有执行或修改机器人、PM2、fleet生成器，没有发送业务消息。指纹见fingerprints.json，agentops配置只记SHA，不复制原配置或秘密。

## 假开关与持久生成

Team源码 `scripts/reliability/runtime-contract.mjs:40–43` 的allRuntimeBots直接返回production bots，只对testBot检查enabled；`generate-ecosystem.mjs:54`映射全部runtime bots，`generate-instance-configs.mjs`同样使用这个集合。ecosystem加ENABLED=0既没有机器人启动读取语义，也无法避免下次重新生成覆盖。不能把testBot.enabled规则移植成生产bot已有能力。

当前不建议改Team fleet。若将来明确授权关闭独立HR渠道，真正最小持久排除需在权威production contract的bots集合/明确受支持的生成筛选中排除hr-bot，并让ecosystem、instance config、health/restore期望共同使用同一结果，再定点PM2 delete+save；仅删输出ecosystem某app或PM2条目会被生成/恢复复活。若contract验证要求固定全套bot，则“移除一条”也不是已验证可执行方案，需要局部设计/测试后另行审查，不能在本次维护现场假定支持。实际启动源码存在METABOT_EXCLUDE_BOTS，但对单bot进程排空不等于PM2恢复/健康契约支持持久停用，亦不作为本次替代开关。

## 本次建议

删去deployment-plan第233/253行ENABLED持久停机条件及“未关闭独立飞书即不满足发布”的推论。保留网页/API旧worker关闭、新cloud受理/旧受理拒绝、精确平台任务排空、本地平台残留执行核验。若这些成立，独立飞书渠道保持原样，明确未验收也未迁移；不宣称全部HR渠道已退出MetaBot。本报告不批准停机或修改生成器。
