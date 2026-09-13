# 现有 observer 契约补核（根同意先 design）

补读准确 attachment_erasure_observe.py，字节与SHA另存source/observer-fingerprint，不覆写先前设计或旧审读。该入口比设计初读的旧HTTP canary完善，不能把它误称为只看404：observe416起把owner/run/upload/attachment/content绑定、bucket+endpoint identity、database identity、observer源码SHA、row IDs和原key/version集合封入加密before snapshot；after解密并逐项相等，再做真实S3版本/fence检查及DB completed/请求owner/时间检查。main绑定snapshot和ledger文件SHA，受60秒上限监督，未知失败不通过。它是可以复用的核心证据，不能替换成手写passed字段。

仍未闭合的执行器接口：

- snapshot绑定canary run，并无联合维护run/新API+worker容器IDs与image/MinIO新进程世代/精确106 ledger和helperSHA。执行器需在固定新cohort上单独绑定这些项目，不能用另一场已通过canary。
- before为加密snapshot，after目前只返回汇总receipt、不生成加密after snapshot。若按根要求保留前后私有加密snapshot，最小新增包装验证入口需要封存after复验的准确绑定与结构化DB/S3观测，再对外只输出两个snapshot文件SHA及不含正文/key的摘要。
- after中的source/bucket/DB/row等校验是已有真实保护；外层还需绑定准确config文件SHA、ledger文件SHA、公开synthetic contentSHA及维护run，拒绝文件被换、跨cohort重用或过期。加密保证payload未被随意修改，但不是替代实际最新状态检查。
- 首次停机前真实owner GET及CSRF身份预检是独立入口，不能借after API404完成。现有GET account可证明owner，GET附CSRF头不等于服务端验证CSRF；需沿用已经审阅的真实CSRF identity预检路径及其HTTP回执，不能发新业务副作用探针或以cookie字串检查冒充。

最小新增受监督入口建议为固定SHA的 `verify_fence_release`（新host验证模块，非任意shell hook）：接收私有root-owned binding、既有真实canary ledger、before snapshot和准确新cohort身份；在总体deadline内调用现有observer的受审逻辑进行**当前DB+S3只读复验**，同时核106账本及列权限、固定daemon cohort/MinIO世代、同bucket/config/content/run；写独占0600加密after snapshot及绑定前后SHA的receipt。只读验证失败、未知、超时都返回非零且无可finalize回执。执行器finalize必须调用这个确切受监督入口重新验证，而非只相信此前receipt布尔值；实际上传/delete canary仍由根独立执行，不放入该入口。

尚无实际凭据，首次停机前owner/CSRF预检不可完成；尚无最终镜像/受审binding，不能实现生产有效配置。该缺口不阻止后续写默认未绑定即拒绝的草案与本地mock，但此轮根据根最新指令停留在设计。设计中所有因果测试均为待实现，不虚报已跑。
