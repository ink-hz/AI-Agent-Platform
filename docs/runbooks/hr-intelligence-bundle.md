# HR 招聘情报本地 Bundle 运行手册

## 不可突破的边界

- `LOCAL_ONLY_COLLECTION=true`
- `LOCAL_ONLY_ANALYSIS=true`
- `PRODUCTION_COLLECTION=false`
- `PRODUCTION_PANORAMA_MODEL=false`
- `PRODUCTION_CONSUMPTION_ONLY=true`

采集、清洗、聚合、GPT 分析以及 Markdown、PDF、Excel 生成只能在本地完成。生产环境只校验、导入、发布和读取已经完成的不可变 Bundle。页面、API、定时器和生产容器均不得提供更新、采集、分析、重试、运行或恢复入口。

## Bundle v2 文件职责

Bundle v2 保留结构化审计底座，并新增只供 Agent 检索的 Markdown 语义层：

```text
<bundle-id>/
├── manifest.json                 # Bundle 身份、版本及文档哈希
├── checksums.sha256              # 全文件校验和
├── source-catalog.json           # 已批准来源和公司别名
├── source-coverage.json          # 每个渠道的五态覆盖结果
├── raw-evidence-index.json       # 原始证据 URL、时间、SHA-256 和定位
├── evidence/sha256/              # 按内容哈希归档的原始响应
├── normalized-jobs.jsonl         # 规范化岗位事实
├── aggregates.json               # 确定性统计、技术树和公司比较
├── analysis.json                 # 已接受分析、完整输入和证据引用
├── analysis-usage.json           # Provider、模型、token/成本可用性
├── report.md                     # 人工可读报告，不含全量岗位正文
├── report.pdf                    # 人工审阅版
├── report.xlsx                   # 原始岗位和审计明细
└── agent/
    ├── index.md                  # 口径、边界和文档索引
    ├── executive-brief.md
    ├── companies/*.md
    ├── directions/*.md
    ├── topics/*.md
    ├── tasks/*.md
    └── chunk-index.json          # 确定性路由标签、字节范围和内容哈希
```

`analysis.json` 保留完整请求是为了让 Bundle 脱离本机 work 目录后仍可复核和重算；它不是运行时上下文。HR Agent 只按任务读取 `agent/*.md` 的相关片段，单轮不会注入整份报告或全量岗位。

Bundle schema v2 是当前生产候选格式。严格校验器继续接受已经完成且校验和正确的 schema v1 Bundle；v1 没有 Agent Markdown，运行时应受控降级到结构化事实检索并记录原因，不能静默使用未校验文本。

## 本地生产

本地持续数据根目录为：

```text
/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/
```

原始证据、标准化岗位、确定性聚合、AI 分析、token/成本记录和报告必须分层保存。采集失败必须标记为 `failed`、`partial` 或 `not_observed`，不得显示成零岗位。

使用 `backend/tools/hr_intelligence/cli.py` 创建 Bundle、恢复未完成分析、构建并严格校验。`collect` 同时处理招聘渠道和 catalog 中批准的公司公开材料；当前没有独立的生产采集命令。

```bash
cd backend
NEW_BUNDLE_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"

./.venv/bin/python -m tools.hr_intelligence.cli init \
  --bundle-id "$NEW_BUNDLE_ID" \
  --catalog "$PWD/tools/hr_intelligence/source_catalog.v2.json"
./.venv/bin/python -m tools.hr_intelligence.cli collect \
  --bundle-id "$NEW_BUNDLE_ID" --resume
./.venv/bin/python -m tools.hr_intelligence.cli validate \
  --bundle-id "$NEW_BUNDLE_ID" \
  --require-company-count 12 --require-provenance
./.venv/bin/python -m tools.hr_intelligence.cli prepare-analysis \
  --bundle-id "$NEW_BUNDLE_ID"
```

本地分析器必须为 `analysis/requests/*.json` 逐单元生成 schema v2 响应和使用量记录。不能在公司之间复用通用结论；无法取得按单元 token 或费用遥测时必须记录 `unavailable` 及原因，禁止估算。

```bash
./.venv/bin/python -m tools.hr_intelligence.cli accept-analysis \
  --bundle-id "$NEW_BUNDLE_ID" --all-ready
./.venv/bin/python -m tools.hr_intelligence.cli analysis-status \
  --bundle-id "$NEW_BUNDLE_ID" --require-complete
./.venv/bin/python -m tools.hr_intelligence.cli quality-check \
  --bundle-id "$NEW_BUNDLE_ID" --strict
./.venv/bin/python -m tools.hr_intelligence.cli build \
  --bundle-id "$NEW_BUNDLE_ID"

BUNDLE_PATH="/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/$NEW_BUNDLE_ID"
./.venv/bin/python -m tools.hr_intelligence.cli verify \
  --bundle "$BUNDLE_PATH" --strict
./.venv/bin/python -m tools.hr_intelligence.cli evaluate-retrieval \
  --bundle "$BUNDLE_PATH" \
  --cases "$PWD/tools/hr_intelligence/retrieval_eval_cases.v1.json"
```

检索门禁要求至少 40 个固定案例、上下文相关性不低于 0.90、来源覆盖等于 1.00、伪造来源为 0、回放不一致为 0。真实 Bundle 完成后，向 Owner 提交 Bundle ID、完整路径、来源覆盖、限制、报告哈希以及最终本地 `master` SHA。

## 2026-09-06 本地验收记录

- Producer code SHA：`3d1306589df399f1029218683cefabdbf7719a63`
- Bundle ID：`2b49ecc4-42fe-45ae-80ac-26891f42ac6c`
- 生成时间：`2026-09-06T14:37:42.467393+00:00`
- Manifest SHA-256：`5d446fe136a3fe2d3d1bb06873f9584e4a357f9546e9f66e686e14950dab98a3`
- 规范化岗位：3,437 条；已接受分析：86 个；Agent Markdown：86 份；检索片段：598 个
- 分析质量：410 条事实、104 条研判、86 条建议、通用重复公司结论 0 对
- 检索质量：40/40；相关性 1.00；来源覆盖 1.00；伪造来源 0；回放不一致 0
- 人工报告 SHA-256：Markdown `a27ae57f3fec7897479590dc53750de0ae08b70ad2dee4f2be44e85a66697cf1`；PDF `f2e738cfe82e3cc8b49df3bce267061de64cba54a88a895868800b606b0ca9a0`；XLSX `2d528541298e15648ef2d153492e5f1b76cba9febf430136c861cb090c763c67`
- Agent chunk index SHA-256：`88037e20fee19ba4f4bea6e3b3dafa485545037151cc92147fa174b746e3afd6`

| 公司 | 招聘覆盖 | 岗位数 | 公司公开材料 |
| --- | --- | ---: | --- |
| 联合光电 | partial | 8 | failed |
| 速腾聚创 | succeeded | 291 | succeeded |
| 禾赛科技 | succeeded | 364 | failed |
| 拓竹 | succeeded | 558 | failed |
| 创想三维 | succeeded | 55 | succeeded |
| 智能派（ELEGOO） | succeeded | 39 | succeeded |
| 知象光电 | succeeded | 19 | succeeded |
| 先临三维 | succeeded | 173 | succeeded |
| 思看科技 | partial | 0 | succeeded |
| 智元机器人 | succeeded | 1,539 | failed |
| 影石创新 | succeeded | 375 | succeeded |
| 华为 | succeeded | 16 | succeeded |

联合光电和思看科技保留 `partial`，失败渠道不解释为“没有招聘”。禾赛、拓竹、智元的非招聘官网材料本轮未通过采集，但招聘渠道成功；产品路线判断因此维持中低置信度。当前只有一个时间点，所有月度增长、收缩和资源迁移结论均为未知。

## Owner 审批门禁

生产导入前必须收到以下两行精确批准：

```text
APPROVE_RELEASE_SHA=<40 位小写 Git SHA>
APPROVE_HR_BUNDLE_ID=<已审阅 Bundle UUID>
```

缩写 SHA、不同 Bundle、不同措辞或审批后发生的代码/数据变化均使审批失效。

## 唯一一次导入

应用版本验证和部署完成后，执行：

```bash
deploy/cloud/import-hr-intelligence.sh "<绝对 deploy.env 路径>" "<绝对 Bundle 目录>"
```

脚本只使用 `/data/staging/orbbec-agent-platform/<deployment_id>/` 范围内的本次 staging，使用 `trap` 精确清理；不使用 `/tmp`，不携带模型密钥，不连接公网。导入事务失败时，当前已发布 Bundle 保持不变。

## 验收

验收必须核对 Bundle ID、数据截至时间、12 家公司来源覆盖、原始岗位、AI 分析、未知项、证据 URL/SHA-256，以及 Markdown/PDF/Excel 与本地文件哈希完全一致。还必须抽检每家公司 Markdown 和 5 份任务手册均包含可引用事实、研判、建议、未知项、替代解释及证据；确认生产不存在 Producer、模型客户端、调度器、`run` 或 `resume` 能力。
