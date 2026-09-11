# HR D7 同网关合成计量验证

## 结论

本次在本地使用根目录 `.hr-agent/provider.json` 指向的同一 Messages 网关和固定 `claude-opus-5`，执行了严格有界的 6 次合成生成调用。六次均返回 usage 和 provider 自报 model；没有重试、改模型或改端点。此验证只检验传输与计量行为，不是专业 HR 质量验收，也不证明 provider 背后的模型身份。

`conservative_utf8` 运行时 floor 保持不变。本批次发现工具 schema 样本的实报输入量 822 高于冻结估算 654（1.257 倍），因此不能用这 6 个样本声称当前估算对所有请求均为上界，更不能据此降低 floor。

## 边界与方法

- 调用预算：最多 6 次，实际 6 次；每个预先定义的样本一次，失败不重试。
- 请求约束：`max_output_tokens=128`，deadline 60 秒，单样本输入最多 12,000 Unicode 字符。
- 数据：英语、中文、混合 JSON、工具 schema、多轮历史、较长中文，均为公开合成文本。
- 路径：`ConfiguredHttpModelPort.from_mapping` 构建现有 `ProviderProfile`，通过现有 `ModelPort.stream` 发起请求。
- 固定性：完整 request manifest 在首次调用前写入；每个 messages/tools payload 使用规范 JSON 的 SHA-256 标识。
- 安全记录：结果仅保留请求哈希、字符数、冻结估算、provider 自报 model、stop reason 和 usage 数值字段。未记录响应正文、凭据或 endpoint；endpoint 也没有写入报告。

## 观测

| 样本 | Unicode 字符 | 冻结输入估算 | 实报 input | 实报/估算 | 实报 output | stop |
|---|---:|---:|---:|---:|---:|---|
| english | 63 | 405 | 52 | 0.128 | 42 | end_turn |
| chinese | 18 | 396 | 54 | 0.136 | 131 | max_tokens |
| mixed_json | 75 | 435 | 82 | 0.189 | 132 | max_tokens |
| tool_schema | 54 | 654 | 822 | 1.257 | 130 | max_tokens |
| multi_turn | 100 | 569 | 94 | 0.165 | 6 | end_turn |
| long_chinese | 6,489 | 19,808 | 12,990 | 0.656 | 6 | end_turn |

所有响应的 `response_model` 均为 `claude-opus-5`，这只是 provider 自报。三个请求虽设置输出上限 128，provider usage 实报为 130、131 和 132，并以 `max_tokens` 停止；本证据不能区分可见输出与隐藏推理或网关计量开销。

六次的 `cache_creation_input_tokens` 和 `cache_read_input_tokens` 均为 0。样本没有设计重复稳定前缀的成对请求，因而缓存命中、缓存创建计费和跨请求稳定性均未验证。

## 后续拟合建议

这 6 个样本只作为探索性拟合集，不能兼作验证集。下一轮应预先冻结一个更大的分层语料，并在任何拟合前按 case family 留出整组验证样本，至少区分纯文本语言、多轮数量、Anthropic 转换后的工具 schema 字节数及工具数量。候选模型可用非负截距加 UTF-8 payload bytes、消息数、转换后 tool bytes 等特征；选择参数时优先控制留出集的低估率和最大低估幅度，而非只优化平均误差。

在独立留出集通过前，运行时继续使用现有 floor，并对含工具请求单独保守处理；本报告不提出或授权任何运行时参数修改。缓存需要另建固定前缀的预先哈希成对样本，并单独设置调用预算，不能从本批次的全零缓存字段推断生产缓存行为。

## 证据

- `artifacts/2026-09-11-hr-d-metering/request-manifest.json`：调用前冻结的完整合成请求和哈希。
- `artifacts/2026-09-11-hr-d-metering/attempts.json`：逐 attempt 的 model、原始数值 usage、冻结估算与状态。
- `artifacts/2026-09-11-hr-c-followup/token-count-probe.json`：同网关标准 count_tokens 路径 404 的前置证据。
- `backend/tests/test_hr_metering_validation.py`：runner 的边界、清洗与缺失 usage 失败行为。

未执行生产调用、业务消息发送、数据库写入、部署或页面验收。

## Runner 复审修订

独立复审指出配置和字符边界未被完全强制：现已在创建模型端口前拒绝非 claude-opus-5 / anthropic_messages_sse 配置，并按 messages + tools 的规范 JSON 总字符限制输入。原始六样本证据不回填；原 unicode_characters 字段只计算消息正文，新 runner 另记 payload_unicode_characters。原样本的完整 payload 也均小于 12,000，修改未追加任何真实调用。三条失败用例先复现，修复后六条测试通过。

## 准入风险补充

工具样本实报822高于估算654不仅否定估算上界：请求按估算预留，可能在剩余额度不足以覆盖真实消耗时仍被准入。结算max(reported,floor)保留了不低于实报的扣记，但只能事后补账，不能撤回请求。当前计量不构成严格事前费用上限；与缓存/中文校准及供应商计数接口一起留在D7待决，本次不改floor。
