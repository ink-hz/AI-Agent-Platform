# D 虚构场景模型证据

所有候选人、岗位及面试记录均为明确虚构；没有真实个人资料。使用用户指定的本机HR Opus5 profile；不保存私有端点、凭据或profile正文。`profile_file_sha256` 是本机配置原件的指纹，配置摘要与网关自报模型分别记录，不认证上游官方型号。

执行输入来自 `backend/tests/fixtures/hr_agent_d/scenario.md` 中逐份材料，末尾审读问题未发给执行模型。候选初始化的草稿步骤使用脚本模型，随后经真实HTTP人工确认；此处不宣称真实模型简历抽取通过。后续工作使用真实Opus5、实际API/一次性PostgreSQL、普通上传和准确原文读取。对象库为测试内存存储，不冒充在线对象库。

| 运行 | 输入/配置 | 实际结果 |
| --- | --- | --- |
| run-1 | 完整三阶段任务；120秒/4096输出，原D角色 | 16次请求。搜寻与评估保存成功；面试方案阶段失败、无方案成果；未到记录复盘。独立AI审读发现必需项/培养项与记录可复核性的解释偏移，原文不改 |
| run-2 | 单独无方案记录整理；120秒/4096输出，原D角色 | 1 passed，74.01s；准确原文保持，AI整理另存；W4此分支审读有范围限定地通过，仍有轻微表达保留 |
| run-3 | 完整三阶段；显式300秒/16384输出，澄清逻辑范围后的新D角色 | 1 passed，645.72s；23次请求，三项work完成、五项成果保存；方案局部可接受，记录与复盘专业未全通过 |

| run-4 | run-3评估与方案准确正文副本＋原始记录/JD/过程材料；新隔离work，300秒/16384输出，第三版角色 | 1 passed，541.98s；9次请求，1次300秒中断后恢复；两成果保存，专业问题改善但未全闭合 |

各阶段是新work，以准确成果引用继续上一步，不伪称单work连续旅程。每次独立试验共享最多24次生成请求，每work仍为600000 token/900活动秒/32calls；扩展配置将收尾token预留设32768，不改总上限。默认配置与HR私有env未被这些本地覆盖改写。

run-1首次发送前没有导出runner文件hash；启动时测试文件对应66b60ff，运行中为后续无方案分支扩展了同名测试文件，因此末尾pytest回溯行文本对应后来文件，不能按该回溯定位初始代码。该次标准输出由工具返回后逐字保存到 `../2026-09-11-hr-d-validation/model-run-1-tool-output.log`，不是最初自动落盘日志。没有捕获中断请求的停止原因，不把所有interrupted事后解释成超时或输出截断。run-2起在首次D请求前捕获runner修订和文件hash，并记录归一化流的stop/usage/耗时；流结束不自动等于业务保存。

run-4是引用历史AI副本的新工作：副本正文hash与原result ref映射留存，不能称原结果修订或全流程重跑。最新审读仍发现唯一要求判断、明确未发生与未知、材料覆盖与实际全场范围的矛盾；专业验收不判全通过。

run-1/2旧角色与run-3/run-4新角色以各自知识manifest为准。run-3同时改变本地输出配置和通用角色说明，是修正后的场景验证，不是单变量因果实验；不得归因“仅提高超时就修好专业质量”。原失败与原保存成果保留，后续成功不覆盖。

复跑需显式私有profile和新的空证据目录：

```sh
HR_D_REAL_PROFILE_FILE=/absolute/private/provider.json \
HR_D_EVIDENCE_DIR=/absolute/new/run-directory \
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_d_journey.py::test_fictional_d_real_model_evidence -q --tb=short
# 无方案分支另设 HR_D_WITHOUT_PLAN=1；本地长响应试验另设 HR_D_EXTENDED_RESPONSE=1。
# run-4专项另设 HR_D_REVIEW_HISTORY=/absolute/path/to/run-3（读取保留的历史成果，创建新工作）。
```

专业审读报告由未参与夹具准备或模型执行的独立AI读取原件与输出后编写。主执行者可据问题改进后续角色/代码，但不编辑审读者结论、不替换已保存成果；AI报告不代表人类验收。

交付时为run-*下原始证据生成sha256-manifest.json；该清单是本次封存指纹，不声称每个文件生成瞬间已经签名。运行 `python3 artifacts/2026-09-11-hr-d-journey/verify.py` 可重算文件字节/hash、保存正文对应关系与计量；成功回复实报不能充当包含未知中断用量的全账。
