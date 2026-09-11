# HR C 阶段交付与评审（2026-09-11）

分支 `feat/hr-cloud-loop-c`，基线 `347594c`；A+B 分支保留。本轮已实现 C0/C1/C2 与 C3 研究运行能力，C3 专业验收尚未通过。真实模型试验的失败、已保存但判断不合格的成果一并留证。未推送、部署、访问生产业务库或使用真实候选人材料；不据此进入 D/E 或宣称旧链已经退出。

## 1. 本次改动及理由

- **长历史与取消**：取历史时仅短暂锁定工作快照；当前一次读取按唯一对象/引用批量验权，再解密。取消不等待对象存储；交付上下文前重新检查输入、执行权、来源及摘要祖先。原件实际读取仍校验字节，权限证明不冒充内容审计。
- **候选材料**：批次内各文件独立上传、解析、研究草稿与人工确认。失败项单独重试；姓名和摘要由用户审阅填写，明确新建或关联已有本人候选人，不按姓名自动合并。新档案与回执加密，不调用旧 MetaBot parser，不新增模型确认工具。
- **个人材料边界**：入批即登记个人来源。普通 work 引用该材料或派生 research 也在发送前检查处理许可；默认无许可则拒绝。回调异常只使该项失败，兄弟项继续。已知个人来源不能借丢弃 candidate 对象进入通用标准。这是来源传播，不是任意文本自动识别/DLP。
- **情报引用**：严格适配真实 bundle，当前目录和选定旧正文分开。前端保留准确引用；旧正文清除或篡改时报不可用，不换 current。86篇旧模板仅验证格式，不认证其专业质量；历史38篇语义分析尚未发布到新库。
- **删除维护**：真实维护角色暴露两处旧缺陷：领取函数被逐列重复执行可配错任务/附件，维护角色缺上传版本读取权限。修复领取 SQL，新增公共迁移100的最小列权限；HR readiness 同时检查迁移与实际权限。只是本地修复，未应用生产。
- **研究运行**：单请求原默认120秒不足以覆盖已观察到的持续输出。支持显式1–600秒，默认120不变，实际截止取配置与工作剩余活动预算的较小值；本机试验副本设300秒，不改生产或默认预算。`official_original` 不可用错误明确指向 basis，不再让模型误以为 research 不支持；普通研究用 source_refs，basis 可空。

接口与范围：[C1契约](2026-09-11-hr-c1-material-contract.md)、[C1页面](2026-09-11-hr-c1-frontend.md)、[删除维护审计](2026-09-11-hr-c1-erasure-audit.md)、[C2](2026-09-11-hr-c2-intelligence.md)、[C0独立审查](2026-09-11-hr-c0-history-review.md)、[资产盘点](2026-09-11-hr-c-assets.md)。候选人页面只做材料核对/建档，完整评估和面试续作仍属于D。

## 2. 可复现验证

以下命令从 C 工作树执行，Python 使用 `backend/.venv/bin/python`。数字重叠，不相加。

| 命令/边界 | 实际结果 | 说明 |
| --- | --- | --- |
| `backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py -q -rs` | **311 passed, 5 skipped，149.36s** | 每模块一次性PostgreSQL、真实迁移/HTTP/应用权限、真实Worker进程；模型替身用于确定性故障。skip为B真实模型、C原始包、C真实模型、C2两项实际包，没有因数据库缺失跳过 |
| `HR_TEST_INTELLIGENCE_BUNDLE='<本机包目录>' backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_intelligence_build.py backend/tests/test_hr_agent_intelligence_releases.py -q -rs` | **6 passed，5.86s** | 上一行C2的两个跳过已单独用真实12公司/3437岗位/86分析单元包实跑 |
| `backend/.venv/bin/python -m pytest backend/tests/test_r1_authorization.py backend/tests/test_agent_use_authorization.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_hr_task_context.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py backend/tests/test_hr_agent_candidate_routes.py -q` | **655 passed，10.29s** | 真实身份/CSRF/第二个owner及旧HR上下文回归；不声称线上D1已经处理 |
| `cd webui && npm exec -- vitest run src/hrLoopApi.test.ts src/hrLoopCandidatesApi.test.ts src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrLoopIntelligencePicker.test.tsx src/workspaces/hr/HrLoopCandidatesPanel.test.tsx` | **53 passed，10.74s，5文件** | HTTP模拟边界的组件交互，非真实浏览器上传；`npm exec -- tsc -b`通过 |
| `backend/.venv/bin/python docs/superpowers/specs/hr-cloud-loop/selfcheck.py` | **55定义、133例、18条件覆盖ID、6正文证据ID** | 不是专业质量规则；18不等于schema构造总数 |

仓库级前端的3个 `styles.test.ts` 既有失败沿用A+B记录，两个相关blob与master一致；本轮没有将相关53项写成全仓前端全绿。候选前端不要求用户编辑JSON。单文件解析不含OCR，批次恢复不恢复尚未上传的浏览器File对象。

证据入口：历史性能 `test_hr_agent_history_performance.py`（1/30/300条重复来源、取消/新输入/撤权/摘要祖先）；逐文件 `test_hr_agent_candidate_intake.py`；真实身份 `test_hr_agent_candidate_routes.py`；真实维护 `test_hr_agent_candidate_erasure.py`；预算长响应 `test_hr_agent_research_timeout.py`；保存错误恢复 `test_hr_agent_research_result.py`。原件删除的测试实际调用维护角色和对象存储删除，不只改数据库状态。

## 3. W1–W12 映射

“工程通过”仅限指定支腿；业务审读和用户验收不由测试数量替代。

| W | 本次状态 | 证据与尚未关闭部分 |
| --- | --- | --- |
| W1 | 沿用B部分通过 | 上传/原文身份/无岗位work和临时基准工程覆盖；B公开JD模型样例仍独立评审，C未重做该业务质量验收 |
| W2 | 新链工程通过；旧链发布未完成 | context、history_performance、candidate_routes 实际输入/摘要来源隔离，先验权再解密；旧D1保守补丁不清除已冻结命令或旧CLI会话 |
| W3 | 部分通过 | B统一成果+ C候选原件/草稿准确读取；完整候选入口到针对性面试续作属D，不以建档代替 |
| W4 | 属D，不判 | 本轮未实现/验证真实面试记录与方案对应 |
| W5 | 虚构材料工程通过；专业/浏览器待验 | 独立失败重试、人工新建/关联、版本冲突、上传组件与真实候选HTTP；模型抽取忠实性/身份歧义质量未通过真实模型审读，真实个人资料闸门未开 |
| W6 | B工程延续，C扩展来源保护 | proposals/standards与candidate_routes拒绝未建档个人材料及派生来源；部分确认/并发沿用B；不声称任意自由文本都已脱敏 |
| W7 | 本机真实包工程通过 | C2真实发布+HTTP准确旧正文、清除/篡改拒绝；前端目录更新仍发B1。生产保留期/发布目录未配置 |
| W8 | 本地工程通过 | 来源撤权在历史/发送/保存/下载重验，真实维护删除后档案/派生成果不可读；新增100待部署；不撤回已送给外部模型的内容 |
| W9 | 工程通过 | 真实Worker SIGKILL/恢复/取消原测试，C补长历史取消及候选提交后回写异常恢复。候选协调专项为异常注入，非新增候选进程kill证明 |
| W10 | 工程部分通过 | 基础出站端点/日志/临时目录+候选处理许可缺失或异常零发送；不新增fallback。真实个人最小化、获准服务及保留承诺尚未确定 |
| W11 | **专业未通过，工程部分通过** | 19岗完整归档已返回，预算暂停/追加累计可查；Opus试验出现空回答失败、越界判断，不能凭已保存或方法已读验收。不是3437岗压力证明；子公司歧义另样例未验证 |
| W12 | B工程延续 | 方法准确revision/丢失拒绝测试，C真实模型自主读方法仍出现判断问题；方法读取不作为正确应用证明 |

## 4. Opus5公开研究记录

模型配置来源为HR私有env/profile，`claude-opus-5`，网关响应记录同名自报；只表明指定网关返回的标识，不认证底层官方型号。没有改FAEenv、备用模型或生产current。

原始材料取bundle `2b49ecc4-42fe-45ae-80ac-26891f42ac6c` 中Revopoint全部19个唯一岗位身份，按归档index校验原件SHA并匹配公开ID，职责+要求全文13397字符，连同身份/标题/时间组装16969字符。没有使用计数表或摘要代替原文；原始渠道页面残留也保留在公开材料中。

| 运行 | 实际结果 | 结论 |
| --- | --- | --- |
| 1 | 预算暂停后failed，无保存成果 | 未留每次传输耗时，不能事后把所有中断定性为120秒超时 |
| 2 | failed；持续输出至120秒被截断，另有空end_turn | 输出额度改8192仍不能解决硬截止 |
| 3 | 收窄输出篇幅仍failed | 不能以短提示修复传输截止；保留失败 |
| 4 | 300秒配置下133秒工具参数完整；保存因basis错误被拒；最后completed但results为空 | 技术结束不等于成果保存。未保存草稿专业未通过，见独立审读 |
| 5 | 自主读取两份方法，保存1份成果，4calls后waiting_budget；追加后同work为failed（第6次空end_turn） | 已保存成果未丢；专业仍未通过。没有用默认重试或假终答把失败涂绿 |
| 6 | 初始研究第4次摘要请求返回空end_turn，failed，31.14s | 拟执行的显式评审修订尚未到达；没有保存成果或反馈修订通过的证据 |

原始公开trace与SHA清单：[artifacts](artifacts/2026-09-11-hr-c3/sha256-manifest.json)。[run4审读](2026-09-11-hr-c3-run4-semantic-review.md)、[run5审读](2026-09-11-hr-c3-run5-semantic-review.md)独立回查全部原文。主要缺陷：要求OR/优先关系被改窄、招聘职责被当成组织已运行、引用“了解渠道差异”证明经营并存、忽略实操凭证反例。已将通用逻辑边界写入本地角色适配并留来源；没有按公司写判断规则、固定方法路由或机器专业合格门控。

运行1–3的runner_sha256在导出时计算，期间文件有修改，不能当成精确已加载源码版本；4起记录首次发送前的文件指纹。所有记录只含公开材料、工具/状态/用量和非秘密配置摘要，不含网关URL/凭据。原文件身份不可因当前runner修改而回填。

## 5. 待完成与评审顺序

先按上表核查W证据，再评研究正文。C3本次失败不能被311项工程通过冲抵。候选材料专业审读、原生浏览器上传、获准个人处理配置、生产保留/新鲜度/预算值仍需各自验收。P1/D1只有本地保守修复，旧命令快照/在途归属与停机窗口在E单列；P2只有本机公开资产盘点，没有生产计数或数据为空的结论。

部署须先运行公共迁移（含100）及显式HR目录096–099，并验证准确校验和和权限。启用开关不运行DDL；本轮没有部署。新链档案不迁移或双写旧v70资料，其他Bot不受本轮退出计划影响。

### 传输核对补充

公开两句问答探针正常（10.58秒），摘要探针在原历史工具格式、序列化为历史数据两种输入中均由网关返回 `stop_reason=refusal`；省略空工具列表仍拒绝。没有发现本次探针的非空正文被适配器漏掉，但这不能排除其他未捕获响应的问题，也不能把本次refusal与此前空end_turn混为同一原因。摘要格式的试验性代码已撤回：成对探针没有支持它是修复。当前模型/网关还需核查长研究、摘要及空响应兼容性，不能宣称六次研究失败均已解释。没有换模型、备用端点、绕过拒绝或伪造终答。

C3第6轮仅完成初始失败快照，未到显式评审输入；`review-input.md` 是待验证输入，不能当作用户反馈旅程已通过。修订文本/专业通过没有产生，W11仍未勾选。普通短探针正确回答“职责不证明现状”，也不抵消研究长文中同一边界被违反的事实。
