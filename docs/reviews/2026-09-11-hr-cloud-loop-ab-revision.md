# HR A+B 评审修订 — 2026-09-11

本批修复 A+B 外部评审问题，并落实用户指定 Opus 5.0 的本地 HR 配置；未进入 C、未推送或部署。原始交付快照 `12a6447`、评审包 `ac965b9`，记录修正提交 `59456d3`。本记录不将历史测试数或模型输出改标为新版本证据。

## 评审项与处置

| 评审项 | 本次结果 | 证据 / 限制 |
| --- | --- | --- |
| 下载125字节留证缺失 | 原三处声明收紧为人工观察，明确未留存响应和文件 | [A+B 包](2026-09-10-hr-cloud-loop-ab.md) §5；不补造历史产物 |
| 前端225不可复现、全套3红 | 补准确10文件命令；9月11日复跑225通过，完整套件1145通过/3既有样式断言失败 | A+B 包 §5；未改样式以迎合测试 |
| 三个计划测试名不存在、摘要测试名不实 | 对齐实际进程测试名；摘要名改为跨工作隔离 | 交付计划与 `test_hr_agent_context.py`；历史强覆盖未删 |
| W1–W12 缺映射 | 逐条标本批/部分/后续；旧D1与新链W2分开 | A+B 包 §5.1；根架构 D1–D4 已区分新旧链 |
| 18条件规则歧义 | 标明覆盖ID数量 | 不称Schema构造覆盖率 |
| 零报告退尽token预扣 | 结算取报告量与冻结输入保守估算较大值，保留原报告；下限生效标estimated | `test_zero_provider_usage_cannot_refund_input_estimate`；预算策略不是账单计量 |
| UUID大小写幂等 | 锁、查找、存储统一标准UUID文本 | `test_uuid_case_retry_returns_same_work`；工作仍只建一份 |
| 授权入口分叉及空范围放行 | 规格点名实际 `ResourceReader.validate_scope`；缺装配一律503 | `test_missing_scope_validator_rejects_empty_scope`；测试仓库替身显式装配权限边界 |
| ConfirmError 孤儿 | `StandardService._conflict` 发出前实际校验 | `test_standard_conflict_is_validated_on_emission` 与现有标准/HTTP冲突回归 |
| 工具错误历史无范围 | 使用冻结输入对象、用户引用及原调用依赖 | `test_tool_error_inherits_frozen_input_objects`；不使用模型自报范围 |
| 预算追加无服务上限 | profile新增可选service_limits；缺省为初始limits，超限422且事务不保存 | `test_budget_extension_cannot_exceed_service_ceiling`；生产上限仍由部署/产品确定 |
| 模型出处无响应ID | 两种协议采集响应model；网关自报、缺失不回填；公开复测逐阶段保存全部工作轨迹 | `test_response_model_is_optional_gateway_self_report`、`test_stage_export_keeps_new_thread_answer_attempt_and_response_model`；后者为脚本模型工程验证 |
| 历史读取连接/对象I/O放大 | **未修复，列为进入C的前置阻塞** | `read_selected_entries` 持工作锁逐条校验及附件重复读取仍在；须批量权限状态与短事务方案，保留撤权和摘要语义 |

历史9月10日 `evidence.json` 没有响应model与第三阶段完整轨迹；本次增强捕获器不使缺失证据恢复。FAE来源profile指纹只覆盖公开配置字段，不证明秘密端点或底层供应商型号。

## Opus 5.0 配置与实测

用户本轮指定 `claude-opus-5`，已写入项目根目录本机 `.env.hr` 的 `PLATFORM_HR_AGENT_MODEL`，provider JSON同步设为该值。模型选择进入配置指纹；API和Worker共同读取同名变量。配置和凭据均在Git之外，env/provider/credential权限0600、秘密目录0700。FAE原env未修改。

[Opus 5 探针](artifacts/2026-09-11-hr-ab-revision/opus5-probe.json) 仅发送 `Reply with OK.`，返回成功且响应model为 `claude-opus-5`。这是9月11日的新证据，只能证明网关接受该标识并如此自报；不证明底层型号身份、真实上下文窗口或HR专业质量。沿用既有验证窗口配置，未作能力宣称。没有用Opus 5重跑完整公开JD三阶段旅程，更未发送真实候选人材料。

运行时不会自动读取或启用本机env；启动器须装载该文件，并另提供完整数据库/内容密钥/预算等配置。部署仍未执行，详见[运行说明](../runbooks/hr-agent-local-runtime.md)。

## 验证记录

- 首轮最小用例复现预算、幂等、空范围三项失败；补充的确认契约、错误范围、服务上限用例亦先失败后修复。
- 修订中完整HR回归首次发现两项测试夹具缺tokenizer，补齐后上下文10项通过；随后HR加文档检查232通过、1跳过（115.05秒）。此轮发生在新增Opus环境变量测试之前。
- Opus环境覆盖与非法值四项先失败后通过；基础配置/修订用例40通过。
- 最终完整HR与文档回归 **236 passed, 1 skipped（114.01秒）**，跳过完整真实模型旅程；本次另跑Opus 5短探针成功。A0自检55定义/133例/18覆盖ID/6正文证据ID通过；新运行包及新增修订/模型留证测试 Ruff 通过，`git diff --check`通过。
- 本机无Docker，`docker compose config`无法运行；两份YAML均解析并验证API/Worker显式转发同一模型变量，不能当作Compose或生产部署验证。
- 不以模型替身测试或测试数量替代W1–W12业务验收。

```sh
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py -q
backend/.venv/bin/python docs/superpowers/specs/hr-cloud-loop/selfcheck.py
backend/.venv/bin/python -m ruff check backend/app/hr_agent
```

## 尚未闭合的交付闸门

进入C前处理历史读取的锁/连接放大。旧D1仍按原“现行发布或新链候选人验收，先到者”截止处理。真实个人材料服务授权、生产数据/开关盘点、部署演练均未完成。未新增独立人工专业评审；本次含本地回归与工程自审，不能替代用户的A+B评审。
