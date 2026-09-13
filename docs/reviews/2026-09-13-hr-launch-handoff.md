# HR 上线接续审核交接（2026-09-13）

**当前尚未切换生产接单。** 新运行时源 `394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76` 已完成附件版本擦除、防迟到写入、真实SDK流式上传和暂停版本控制兼容修复，并在生产主机完成仅镜像构建。新镜像为 `sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442`；生产仍运行旧服务。真实本地MinIO最终7组通过，四轮合成历史意图/真实模型回放已完成但内容仍有遗留。现有owner Cookie/CSRF私有配置尚未取得，既定路径只读复核仍不存在；迁移、联合维护窗口、受认证生产canary和HR接单切换均未执行。用户已授权上线，飞书排除，浏览器不作为本次前置。

工作树：`.worktrees/hr-cloud-loop-e-release`，分支 `feat/hr-cloud-loop-launch`。不得在根目录用户工作树恢复旧方案或覆盖用户修改。当前已构建但未发布的运行时镜像源为 `394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76`；ce6 为旧准备镜像，不再用于本次发布。后续 host 工具、验收脚本和证据不冒充已进入394镜像。

| 项目 | 已有实际证据 | 尚未证明 |
| --- | --- | --- |
| 镜像 | 394源在生产主机构建成功；镜像cbe282…；独立只读比较24容器的ID/镜像/运行状态/启动时刻/重启策略无变化 | 新镜像实际服务切换、业务接单 |
| 附件版本擦除 | 最终321项附件工程通过；真实MinIO run4七组通过，含1001旧版本跨页、双擦除、迟到PUT、三种版本状态；源码SHA精确绑定 | 兼容API/附件同窗口切换、生产真实身份上传/擦除及版本观察闭环 |
| 数据库 | 生产 001–095 每项 SHA 匹配；job kind 基线预检通过 | 100/102–106 及独立 HR 迁移实际执行；104 准确计数现场耗时 |
| 备份 | 291124812 字节 custom dump、schema/ledger/globals 私有备份；归档目录可读，备份会话归零、两锁释放 | 恢复演练；未 dump 或修改 preview |
| 配置/知识 | 先前ce6配置/知识已安装；新94资源知识包在本地重建并逐文件比对，95文件原字节不变，5文件因role/方法/来源/manifest调整 | 新知识及有效配置指纹安装、网络/数据库/实际进程验收；旧preflight不作为新配置通过证据 |
| 旧链 | 本地 HR legacy 13 completed/3 interrupted、outbox 0；3 条平台 v5 command 有终态/退出/停止并精确匹配终态上传 | 切换窗口中的再核验、云端旧网页 Worker 持久关闭及实际派发拒绝 |
| 历史模型 | 63会话/242用户消息提取；22个合成适配场景；最新H03 real6四轮、17调用、5次成果修订完成 | H03访谈题与替代证据路线仍有1项重要内容问题；22例完整回放及整体专业通过未证明 |
| D 模型 | 三轮真实提供方工程试验及独立输出审读，第三轮 1 passed/590.16 秒 | 专业整体通过；仍有单因排除和归因偏移 |
| 页面 | 合并保留岗位/成果能力并修复路由；定向组件/style 工程回归留证 | 本次未做浏览器验收，按用户指令不作为上线前置 |
| 生产接口 | 受认证 canary 脚本及真实本地 HTTP/PG 工程测试；慢流墙钟中断补验 | 有效生产 owner Cookie/CSRF、生产真实擦除、受理/续作/成果及自有进程重启验收 |

实际操作入口为[发布计划](../../artifacts/2026-09-13-hr-launch/production/deployment-plan-review.md)和[当前实施计划](../superpowers/plans/2026-09-13-hr-launch.md)。`404862b93c57e6782016182f089b67766ab569ff` 的 production-only 监督迁移工具为前一版受审基线；当前新增106源文件前置，需重新测试并冻结准确字节，不能替换为通用 bootstrap 或同时处理 preview。原仅停附件 Worker 的 ce6 方案不适用条件写修复：共享 API 和附件 Worker 都是写入方，须在同窗口停止旧写入方、隔断准确 MinIO 旧请求代际，再启用与106兼容的服务组合并通过真实擦除验收。新监督执行器尚未完成，不执行旧输入。

独立飞书 MetaBot 不纳入本次停机。平台同一工作仍须由新云端链唯一执行；共享 Relay 和其他 Bot 保留。实际 PM2 加载版本与磁盘 ecosystem 不同，应以[本地平台在途核对](../../artifacts/2026-09-13-hr-launch/production/local-platform-inflight-review/)为准。三条 `reconciliation_required=true` 原样保留；实际加载代码拒绝已完成任务重放，不等于全部外部效果已对账，不得清标志造零。

真实个人材料供应商处理授权仍未落实，personal authorizer 关闭。现有 public-only 配置用于公开/审阅合成材料；它不是任意正文的个人信息分类或自动拦截器。生产预算是 600k/900 秒，历史本地回放使用 1.2M/1800 秒，两者不能互相证明。真实模型工程通过不作为录用、淘汰或专业判断的自动批准。

历史回放原 pytest 为 1 failed/620.12 秒，原 RED 保留。独立审读指出 H03 强制两个 kind、H13 强制保存超出产品语义；修订测试契约不能消除 H01 要求强度、H13“未提供→未采集”等真实内容问题。详见[输出审读](../../artifacts/2026-09-13-hr-launch/history-real-review-1/review.md)。

证据封存分别见 `model-evidence-seal.json`、`engineering-evidence-seal.json` 和 `production/build-and-provision-seal.json`。两个已披露的操作文书/权限问题：旧指纹表的 companion SHA 少一字符，实际发布计划按准确 Git blob 修正，旧表保留；provision 外层曾把两个日志创建为 0644（父目录 0700），已按准确 SHA 定点改 0600，原脚本与补救回执分开保留。旧缺失日志仍缺失，不补写历史“通过”。

新增物理擦除证据见 `production/s3-versioning-readonly-1/`。只读检查不含任何对象删除；数据库 job completed 会清除引用，所以新 canary 需要在删除前私有加密封存准确对象版本清单，再验证真实 job 状态与版本不存在，不能仅以接口 404 为通过。历史 `production/deployment-plan-review.md` 中 ce6 的执行步骤暂时失效，修复后的新方案另行绑定准确源码与镜像。

截至新增 S3 修复：`4864abf` 的版本删除有 386 项附件工程回归；`production/s3-real-local-2` 实际本地 MinIO 验证跨两页 1002 个旧数据版本 HEAD 不存在、前缀兄弟保留、Suspended/null 与无版本桶删除通过，不能证明 late PUT 已阻止。后续零字节占位与条件写方案、106 逻辑删除关门和 API/附件同窗口切换正在实施，见 `attachment-write-fence-design-review-2` 与 `attachment-fence-release-plan-review`。原 ce6 运维输入不得执行。

H03 real3 第四轮并非仅预算设置问题：反复以 role_calibration 更新 jd 被拒，另写调试占位，累计20次/1161589估算token后 waiting_budget，无最终答复。`b425b08` 增加授权后的类型字段诊断及禁止占位指导，16项真实PG测试通过且独立审读通过；H03 real5 已以固定 b425 源完成四轮，20次调用/1094115估算token/905.47活跃秒；错误kind反馈后沿原jd保存第四修订，无占位，但 save_note 被拒且如实披露。独立审读仍不接受专业内容：将用户明确指定职责降成待确认、仅补职责却新增相似工艺经历门槛、管理者经历和签核权仍有不当硬化。见[本轮输出审读](../../artifacts/2026-09-13-hr-launch/history-h03-real-review-5/review.md)。真实模型工程与专业判据继续分别登记，旧失败及前一轮内容问题不覆盖。

当前新增物理边界预检 `production/minio-fence-window-readonly-1/` 实际只读确认唯一所见 Docker MinIO 数据挂载、无生命周期/复制/通知配置；未停止该服务，也不证明不存在任意宿主写入方。a608 附件集合316项通过；完整控制平面迁移集合原有2项静态清单失败已在 e8032bc1 修复，当前46项通过。迁移helper定向39项通过，缺106单元测试名称范围经独立审阅收窄；不声称完整入口在所有数据库/容器只读I/O之前拒绝。

新增真实fence运行及诊断已冻结在 `51612089`：本地MinIO跨两页1001个旧数据版本清理且保留占位通过；首个原件流式PUT在SDK计算checksum时失败，原因为 `_DigestingReader` 没有 `tell`。双擦除与半包PUT尚未执行，因此该运行仍为失败。独立审阅另指出 Suspended/null-slot cleanup 可删除并发占位；新修复进行中，旧失败不覆盖。

通用规则修订 `7a7d7d8b` 分开当前草稿授权、来源真实性和长期标准确认，并约束职责修改不得自动提高筛选门槛。知识测试4项通过，两次打包一致；该内容修订尚无专业验收结论。以准确 detached 7a7d7d8 源启动 H03 real6，仍是合成历史意图/真实提供方/本地HTTP+PG、登录交换和对象存储替代，不能作为生产存储或生产预算验收。

## 本次冻结增量

- Runtime：`394c9ecd`，321项附件工程通过；最终S3 helper SHA `42c230ff413fd53402176dd988ff78b511a79a28489130890808d7331cc44933` 与 `production/s3-fence-real-local-4/receipt.json` 精确相同。独立关闭审读见 `review/fence-runtime-closure-b80ea688/`。半包实测的调度为PUT返回200、随后擦除完毕，后续条件PUT412；不声称看见服务器接收前缀或穷尽所有调度。
- 历史模型：`history-h03-real-review-6/review.md` 逐轮审读及原始快照；987148估算token/711.87活跃秒来自1.2M/1800本地实验，不能证明600k/900生产预算能跑完。显式职责/门槛改善；统一面试标准仍不能无条件验收。D轮旧专业遗留仍保留。
- 构建：`production/stage-build-v3-runs/394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76-48218a156bb21b7be9304ddab429d788/receipt.json`，实际exit0；archive SHA `4ac49e614ee0bdf771b2df2ffc1a35cf0c0cb1697444836d5d20c8e8a240126c`，manifest SHA `3e0befec813cd01fb84fd27c9a7982a8792b79ad45c265928151cfe70ddf9720`。构建后只读核验见 `production/post-build-v3-readonly-1/`。
- 新知识包：`hr-intelligence-ad5f3cac253a6a28d3c764db`，94资源；私有归档SHA `b5133cb22f498f51869bdc2518f6f466e8499b0533d287ea80a7e340ca87afd2`，本地生成，尚未安装。旧ce6配置/知识的预检不能移植为新包验收。
- owner预检：`895283b1`，真实本地HTTP/PG证明账户200、错误CSRF403、正确token空body422且不进入上传服务/不建业务对象；原7项和新锚定向1项均保留。固定旧API源码锚与实际只读hash匹配，实际生产owner调用尚未发生；认证/审计可能正常记账，不称数据库绝对零写。
- 新联合执行器目前是三目标状态机/回执契约设计，见 `production/attachment-fence-executor-design-review/`；尚未交付可执行最终绑定，不运行旧ce6维护脚本。发布时需要重新核对生产状态和足够新的备份，再按准确新cohort验证，不能直接照历史命令继续。
