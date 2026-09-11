# D 候选人、面试与复盘评审包

状态：D 本地工程已完成并通过独立工程复审，可交付评审；专业验收尚未全部闭合。本稿随实际证据更新；不作为用户或人类专业签收。分支 `feat/hr-cloud-loop-d`，C 分支保留于 `95d33c3`；工作树目录仍叫 `hr-cloud-loop-c`。未推送、部署、执行生产迁移或处理真实候选人。

## 1. 本批行为

保存成果时，同事务关联服务端核验过的岗位与候选人。对话、岗位、候选人读取同一份结果；通用列表显示最新修订，已确认的候选草稿保留准确旧修订。继续准确成果时，可回查它的已保存来源；每个结果节点仍受当前工作对象限制，叶材料仍受归属、准确身份、删除与撤权检查。

独立审查发现“读取成功但下一模型请求丢掉正文”的问题，`66b60ff` 已修复。新增测试直接断言下一请求含准确材料正文；仅有读取回执不再能让该测试通过。

用户面试原文通过普通 UTF-8 附件上传后登记。新迁移 `hr_agent/101_hr_agent_interview_records.sql` 保存加密元数据、准确引用和个人来源标记；登记与标记同事务。原文、未来面试方案、AI 整理和复盘分别保存与展示；无方案也能登记。模型工具仍为五个，用户 HTTP 才能确认，角色内容不按关键词路由方法。

真实个人材料仍需专门处理授权。新增原文及派生来源同样受模型发送、通用标准和删除传播约束。生产装配缺少该授权时拒绝发送，不因本次虚构样例启用而放行真实材料。

## 2. 验证记录

| 检查 | 实际结果 | 证据与限制 |
| --- | --- | --- |
| 最终后端集成 | 425 passed、6 skipped，238.27s | [最终日志](../../artifacts/2026-09-11-hr-d-validation/backend-delivery.log)；真实一次性 PostgreSQL/API 和原进程故障用例。候选业务正常测试替换模型边界；覆盖最终POST撤权修复与历史AI副本复验分支 |
| D完整/无方案工程旅程和参数边界 | 4 passed、1 skipped，42.68s | [专项日志](../../artifacts/2026-09-11-hr-d-validation/journey-engineering-final.log)；已包含在最终集成，不相加。首次无方案专项1passed另留原日志 |
| 准确来源续作 | 39 passed，12.80s | continuation/context/research_result/repository_views；新增下一模型请求原文断言，独立复审通过 |
| 文档契约自检 | 55定义、134例、18条件覆盖ID、6正文证据ID | [日志](../../artifacts/2026-09-11-hr-d-validation/selfcheck.log)；18不是Schema构造数，不替代权限或专业测试 |
| 相关前端与TypeScript | 5文件56项通过，编译退出0 | [前端日志](../../artifacts/2026-09-11-hr-d-validation/frontend-final.log)、[编译日志](../../artifacts/2026-09-11-hr-d-validation/typescript.log) |
| 已知前端基线 | styles.test.ts：31通过、3失败 | [单独复跑](../../artifacts/2026-09-11-hr-d-validation/frontend-known-baseline.log)；styles.css与styles.test.ts均与master逐字节相同，[blob对照](../../artifacts/2026-09-11-hr-d-validation/frontend-baseline-blobs.txt)。不是仓库前端全绿 |
| 浏览器 | 未验 | 浏览器运行时旧连接失效，重新发现列表为空；未绕过身份使用生产，不把组件测试称浏览器通过 |
| 独立整批工程审查 | 复审通过 | [原审查报告](2026-09-11-hr-d-engineering-review.md)；POST期间候选资料撤权的真实复现及修复、重放拒绝均留证 |
| 原C证据 | 97份hash/字节数匹配且与原提交相同 | [复核记录](../../artifacts/2026-09-11-hr-d-validation/prior-evidence-check.json)；C收尾证据亦未修改 |

后端命令：

```sh
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py backend/tests/test_hr_metering_validation.py backend/tests/test_agent_brain_hr_history_isolation.py backend/tests/test_conversation_attachment_migration.py backend/tests/test_attachment_erasure_hotfix_database.py backend/tests/test_attachment_erasure_readonly_runbook.py -q -rs
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_d_journey.py::test_fictional_record_without_plan_can_be_saved -q --tb=short
backend/.venv/bin/python docs/superpowers/specs/hr-cloud-loop/selfcheck.py
cd webui
npm test -- --run src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrLoopCandidateWorkspace.test.tsx src/workspaces/hr/HrLoopCandidatesPanel.test.tsx src/hrLoopCandidatesApi.test.ts src/hrLoopApi.test.ts
npx tsc -b
# 已知基线单独运行（预期仍有3项既有失败）：
npm test -- --run src/styles.test.ts
```

6项skip为B真实模型、C真实归档、C真实模型、D真实模型和两项真实情报包条件。D真实模型单独运行；普通集成不会调用外部服务。原仓库前端3项 `styles.test.ts` 失败仍是已知基线，须在前端验证中分别披露，不能当本批新发现或隐去。

## 3. W1–W12 验收地图

| W | D 本地状态 | 证据与边界 |
| --- | --- | --- |
| W1 | 沿用B部分通过 | 原上传/临时基准/无岗位工程覆盖；D不重写B公开JD专业验收 |
| W2 | 新链工程回归 | 对象、历史及摘要隔离沿用并回归；D补准确成果来源的当前对象检查。旧链D1生产补丁未发布 |
| W3 | API与组件通过，run-3定向方案AI审读有范围限定地通过 | result_links事务、候选确认准确草稿、跨入口准确读取、来源正文进入下一模型请求；浏览器尚未验 |
| W4 | 原始记录工程通过；无方案分支局部通过，有方案及复盘专业未全闭合 | 用户原文单独登记，模型派生成果不改原文；run-3完整链与run-4专项均实际完成，但记录/复盘仍有实质语义问题，整体未通过专业验收 |
| W5 | 沿用C虚构工程覆盖 | 本次真实模型的候选初始化仍是脚本模型+真实人工确认HTTP，不宣称简历抽取质量通过 |
| W6 | 工程回归并扩展个人原文保护 | 新原始记录及派生来源不能写通用标准；用户部分确认/并发沿用B |
| W7 | 沿用C准确情报引用工程覆盖 | D不改变bundle保留策略，不重复声明真实包导入；生产目录仍未验 |
| W8 | 本地来源失效回归 | 新增原文读取期间撤权、个人来源标记、准确成果源图校验；公共擦除修复仍未部署 |
| W9 | 原进程故障套件回归 | 同操作保存、kill/恢复、取消；新增登记真实事务原子性与幂等，不冒充新增独立进程故障实验 |
| W10 | 虚构工程边界通过 | 真实个人处理授权缺失零发送；只使用指定Opus5，无备用网关；真实候选人承诺尚未确定 |
| W11 | C公开样例证据保留，长任务不判 | D7六样本探索不是token校准通过，300秒/16384研究配置未定，默认120秒不能称C3已验证 |
| W12 | 发布身份工程回归，方法运用另审 | D角色文件参与不可变知识发布；不以方法调用数量或读取记录当专业通过 |

## 4. 模型与计量

真实验证仅发送[明确虚构材料](../../backend/tests/fixtures/hr_agent_d/scenario.md)与已发布专业资源，通过本机HR私有profile中的 `claude-opus-5`。网关响应的模型标识只是自报，不认证底层官方型号。材料中的审读问题不交给执行模型。运行目录独立创建，不覆盖失败；候选初始化使用脚本模型，三个后续工作分别记录输入、消息、工具回执与准确成果。

每次独立D试验最多24次模型请求，默认4096输出/120秒；显式 `HR_D_EXTENDED_RESPONSE=1` 的本地试验采用16384输出/300秒、32768收尾token预留，每工作仍受原600000 token/32calls/900活动秒总预算控制。此覆盖不改私有profile或生产默认，不替代D7决策。run-1默认设置在面试方案阶段失败；run-2无方案整理在默认设置成功。这两者不能合称完整D默认配置已通过。

运行、不可变输入、完整输出与限制见[模型证据说明](../../artifacts/2026-09-11-hr-d-journey/README.md)及[独立AI专业审读](2026-09-11-hr-d-professional-review.md)。首次输出将培养条件扩展到另一项必需能力，并把最终根因当可复核记录的必要条件；原稿保留。后续仅以通用逻辑说明修订角色，不按样例编写自动合格判断。

run-3 在扩展配置下以23次请求完成三个工作、保存五项成果（645.72秒），没有中断；最大单请求137.14秒、最大输出7302 token，不能据此宣称120秒/4096默认配置已获验证。run-4 使用run-3评估与方案的准确正文副本，在新的隔离工作中专项生成记录与复盘：9次请求、541.98秒，其中一次300秒请求中断后恢复，两个成果均保存。它不是原结果修订、同工作恢复或完整三阶段重跑。四次运行及全部失败、输出、审读原样留存。

专业审读确认培养边界、自述与真实性、多种核验方式有所改进；最新run-4仍存在以下拒收点：将记录能力称为“唯一完全无证据的硬性项”，遗漏未核验的示波器基础；将明确未做的工作样本同时列为未知；将本份记录两问扩大为当日仅两问。另有局部责任与经验/能力措辞偏强。不能以工程完成或反复运行替代这些问题的闭合。W3方案的局部可接受也不自动使W4或整个D通过。

计量6次探索见[单独报告](2026-09-11-hr-d-metering.md)：工具Schema样本中估算低于实报，缓存样本未证明命中，不降扣记下限，不用探索系数报实际费用。

## 5. 评审与交付边界

请先核W映射，并注意上表已披露的3项既有前端失败，再审原文与实际结果，并区分原始记录、AI整理和未来方案。不能把AI审读算成人类通过票数；审读者是否参与材料准备、是否被执行者编辑反馈需明示。

公共附件迁移100仍必须遵守“停旧Worker → 应用迁移 → 只启动修复镜像”的独立发布手册。D新增101不自动发布100；本地API可用不代表旧HR已退出。E数据承接、生产在途、真实候选人授权与正式切换未开始。
