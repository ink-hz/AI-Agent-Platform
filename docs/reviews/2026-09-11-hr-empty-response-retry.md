# HR 空模型响应有限重试记录

日期：2026-09-11。

C3 公开研究出现完整 `end_turn` 但正文为空且无工具调用的供应商响应。协议层原先将其归为通用 `invalid_response` 并立即失败，无法使用已有持久模型步骤重试能力。

本次新增内部分类 `empty_response`，范围严格限于完整普通 `stop`/`end_turn`、正文没有可见字符且没有工具调用。Unicode whitespace 和 `Cf` 格式控制字符（如 U+200B、U+FEFF、U+2060）本身不算可见正文；包含实际文字或 emoji 的 ZWJ 序列仍保留原正文。工具 stop 缺调用、stop 与调用不匹配、未知 stop 仍为 `invalid_response`；截断仍为 `incomplete_response`；拒绝仍为 `provider_refused`。

`empty_response` 使用既有持久重试策略：同一 logical step 最多三次实际发送，每次新建 attempt 并扣预算，预算不足可提前停止，恢复不清零。空响应不提交模型 reply、不创建工具操作、不保存成果，也不投影终答。

验证覆盖协议分类、真实数据库的空后成功与连续三次为空、预算提前停止、拒绝直接终止、不完整工具零业务副作用，以及本地真实 HTTP 模型替身连接真实数据库后的空响应重试。
