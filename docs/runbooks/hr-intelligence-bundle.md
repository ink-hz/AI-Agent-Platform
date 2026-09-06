# HR 招聘情报本地 Bundle 运行手册

## 不可突破的边界

- `LOCAL_ONLY_COLLECTION=true`
- `LOCAL_ONLY_ANALYSIS=true`
- `PRODUCTION_COLLECTION=false`
- `PRODUCTION_PANORAMA_MODEL=false`
- `PRODUCTION_CONSUMPTION_ONLY=true`

采集、清洗、聚合、GPT 分析以及 Markdown、PDF、Excel 生成只能在本地完成。生产环境只校验、导入、发布和读取已经完成的不可变 Bundle。页面、API、定时器和生产容器均不得提供更新、采集、分析、重试、运行或恢复入口。

## 本地生产

本地持续数据根目录为：

```text
/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/
```

原始证据、标准化岗位、确定性聚合、AI 分析、token/成本记录和报告必须分层保存。采集失败必须标记为 `failed`、`partial` 或 `not_observed`，不得显示成零岗位。

使用 `backend/tools/hr_intelligence/cli.py` 创建 Bundle、恢复未完成分析、构建并严格校验。真实 Bundle 完成后，向 Owner 提交 Bundle ID、完整路径、来源覆盖、限制、报告哈希以及最终本地 `master` SHA。

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

验收必须核对 Bundle ID、数据截至时间、12 家公司来源覆盖、原始岗位、AI 分析、未知项、证据 URL/SHA-256，以及 PDF/Excel 与本地文件哈希完全一致。同时确认生产不存在 Producer、模型客户端、调度器、`run` 或 `resume` 能力。
