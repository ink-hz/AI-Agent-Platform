# 最终工程回归

留存限制：8份未跟踪的前端中间日志在整理时被误删且不可恢复，见[缺失声明](../evidence-retention-gap.md)。本文只引用实际保留的证据；manifest不是全部试跑总账。原始11项失败、三基线和最终验证没有丢失。

后端运行代码：`96f412fcb05e6d8a770c2ece7968f0a893a8ed8d`，包含独立修复及生产 `fe10fae`。下列命令从发布工作树根运行；前端命令在 webui。Docker 迁移边界为替身，数据库/OS 信号真实；模型、浏览器、部署没有执行。

## 后端

`engineering.log`：**610 passed, 6 skipped in 349.21s**。

```bash
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py backend/tests/test_config.py backend/tests/test_cloud_deployment.py backend/tests/test_hr_execution_cutover.py backend/tests/test_hr_candidate_cutover_continuation.py backend/tests/test_hr_release_read_compatibility.py backend/tests/test_hr_cutover_count_review.py backend/tests/test_hr_cutover_dispatch_review.py backend/tests/test_hr_execution_inventory.py backend/tests/test_hr_research_http.py backend/tests/test_hr_research_library.py -q
```

`identity-transport.log`：**872 passed, 67 warnings in 95.80s**。warnings来自既有Starlette per-request cookies弃用提示。

```bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_use_authorization.py backend/tests/test_r1_authorization.py backend/tests/test_agent_brain_conversation_api.py backend/tests/test_agent_brain_v2_conversation_api.py backend/tests/test_agent_brain_hr_history_isolation.py backend/tests/test_agent_brain_conversation_context.py backend/tests/test_identity_crypto.py backend/tests/test_identity_rate_limits.py backend/tests/test_agent_brain_conversation_repository.py backend/tests/test_hr_direct_command_binding.py backend/tests/test_execution_transport_v5.py backend/tests/test_turn_result_projection.py backend/tests/test_hr_candidate_repository.py -q
```

6个跳过均是原有条件：B真实模型1、C公开研究2、D真实模型1、公开bundle build1/release1。本轮没有新增skip/xfail，具体源码位置及基线比对见最终指纹记录。缺PostgreSQL不会被当作skip。

`production-research.log` 的3 passed只是生产新增研究API/材料的聚焦验证，已包含于610中，不相加。`selfcheck.log` 为55定义/134正反例/18条件覆盖ID/6正文证据ID；条件覆盖ID数不是JSON Schema语法构造数。

## 前端及旧失败

`frontend.log` 是扩大范围首次结果：11 failed/305 passed，不称通过。命令：

```bash
npm test -- --run src/workspaces/hr src/hrAgentApi.test.ts src/auth.test.ts src/documentTitle.test.tsx
```

两个旧候选人测试文件在b974a87、fe10fae和当前树独立重现同样11失败，见`frontend-baseline-review.md`及其原始输出。既有测试仍期待已退出的startTask生命周期，现行页面已改用onDraft与准确scope；测试修订不得删除产品能力或改回旧链求绿。测试调整后的证据另存，原失败日志不覆盖。

`frontend-build.log` 来自`npm run build`，tsc与Vite成功；大于500kB chunk提示保留，不代表构建失败。组件与构建不等于浏览器验收。旧styles三项失败不能由局部通过抵消；仓库级结果另列。

最终测试提交`e93aeb9`只改两个测试文件。`frontend-corrected-broad-final.log`为29文件317通过，`frontend-corrected-related-final.log`为7文件153通过；两者重叠，不相加。`frontend-build-final.log`保留后续fixture引入的ES lib编译失败；等价索引修正后，`frontend-build-verified.log`是实际成功的tsc/Vite输出，`frontend-final-targeted.log`为29项目标通过。

最终全仓命令`npm test -- --run`，`frontend-repository-verified.log`为**135文件、1193 passed / 3 failed / 2 skipped，11.74s**。3失败均为styles.test.ts的既有minimum-font-size、HR grid和position responsive规则，源码与测试未变的证据见`styles-baseline.json`。两项条件跳过为hrCompanyIntelligenceHttp.test.ts和hrTopicIntelligenceHttp.test.ts。本仓前端不是全绿；不能把317项HR通过改写成全仓通过。

前端测试改写经独立复审：`frontend-independent-review.md`保留首审缺口、补回后的指纹，以及最后一行ES兼容修正的独立核对；不是人类业务或浏览器验收。

## 历史与证据身份

`historical-evidence-check.json` 对C/D六目录99个文件逐字节核对D基线6a9cef6；原E目录76个文件逐字节核对b974a87。两者均0变化；094/095/100/101/102迁移也原字节。新103为追加迁移，不回写102回执。

失败日志保留pytest输出中的空格，不为满足源码whitespace检查编辑旧证据。源码/部署/文档单独检查。每个本轮证据文件的字节数和SHA-256见上一级manifest，manifest不包含自身。
