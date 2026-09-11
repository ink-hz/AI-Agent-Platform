# C3 公开研究证据

本目录仅包含已声明的公开岗位材料。`public-requests.json` 是测试专用完整请求捕获，不是允许生产记录完整提示词的日志方案；不包含私有网关地址、鉴权头或思考正文。模型名称是网关自报。

- run1–8：保留失败及未通过审读的原稿，不以最后成功覆盖。
- run9：Revopoint 全部19个唯一岗位身份的职责与要求；预算续作与两轮显式反馈，同一成果新修订。最终正文为 `run-9/stage-4-result-1.md`。
- run10：定向7岗实体对照，8192输出额度下截断失败；夹具预检字段命名错误另存，预检没有调用模型。
- run11：与run10相同7岗完整原文，16384输出额度，预算续作及一次反馈修订。最终正文为 `run-11/stage-3-result-1.md`。不是智元1539岗全量。
- `diagnosis/`：准确失败请求对照与较早人工构造探针；参数完整返回不证明实际保存或专业通过。
- `runner-sources/`：run9–11首次发送前加载的六份源码字节，按同目录evidence记录逐一核对；不能用当前源码解释早期运行。

运行从C工作树执行，需本机公开bundle和私有HR配置，不把秘密提交到仓库。run9执行完整测试文件（2 passed），run11执行下列单项。审读交接文件由独立审读者读完真实保存稿后生成，不能预先放入“通过”决定来代替审读。

```sh
HR_C_REAL_PROFILE_FILE=/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json \
HR_C_RESEARCH_BUNDLE='/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c' \
HR_C_PUBLIC_SCENARIO_FILE=.superpowers/sdd/c3-subsidiary-scenario.json \
HR_C_REAL_MAX_OUTPUT_TOKENS=16384 \
HR_C_REAL_TIMEOUT_SECONDS=300 \
HR_C_REAL_OUTPUT_DIR=.superpowers/sdd/c3-opus5-subsidiary-run11 \
HR_C_REVIEW_QUEUE_DIR=.superpowers/sdd/c3-opus5-subsidiary-run11-review \
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_c_public_research.py::test_public_full_research_budget_resume -q -rs
```

复跑须使用全新的输出/交接目录，先将已导出的 `subsidiary-scenario.json` 放到场景路径；旧决定绑定原work与input revision，不能复用。run9不设置场景和输出额度变量，使用默认8192，执行整个测试文件。两次均使用临时profile的300秒请求超时，不修改源私有profile。

每次work预算为初始2调用/600000累计token/900活动秒，显式追加20/600000/900，服务上限24/1200000/1800。run11收尾预留32768 token，run9为16000。累计token为保守估算混合实报，不是供应商账单；pytest墙钟时间含独立审读等待，各run数字不代表所有试验与诊断成本。

各文件字节与SHA见 `sha256-manifest.json`（清单不哈希自身）。专业结论在目录外对应独立审读报告；AI审读不冒充人类HR或用户验收。生产、真实候选人和3437岗压力验证均未发生。

原始失败日志的空白行和模型原稿末尾空行按字节保留，因此全量 `git diff --check` 会在run9/run10原始证据报告空白警告；源码及维护文档单独检查通过，不为消除警告改写证据哈希。
