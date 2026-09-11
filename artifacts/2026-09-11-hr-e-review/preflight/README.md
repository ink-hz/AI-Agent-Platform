# 103 preflight 验证

新增版本列与缺失/错误 checksum 的发布阻断测试。`red.log` 保留初始 3 项失败；`green.log` 名字虽含 green，实际是 2 failed/10 passed，不作为通过证据：最初把仅核对 HR schema 的 schema_ready 子字段当作全部迁移身份结论。后改为准确发布契约：ok=false、migration_identity_mismatch、103.match=false。最终以 final-green.log 为准。
