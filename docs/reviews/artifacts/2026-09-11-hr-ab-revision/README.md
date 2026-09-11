# A+B 修订留证（2026-09-11）

`opus5-probe.json` 来自本次实际原生Messages SSE短请求，保存请求公开文本、安全配置指纹、运行时代码摘要及响应报告的模型名称。请求使用本机HR env中的 `claude-opus-5`，沿用FAE网关凭据；端点、凭据及其摘要均不导出。

响应模型是网关自报，不认证实际底层型号。此次仅验证短请求，不是HR质量验收，也不补全9月10日历史记录。`source_repository_revision` 指FAE代码基线，不包含或版本化其 `.env`；model.py摘要对应本次探针执行时内容。
