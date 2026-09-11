# C0 历史短锁与取消独立评审

范围为root待提交的`repository.py`历史读取/取消差异及新增性能测试；未修改运行实现。测试提交`50a1318`包含root原有10个用例和本次补充1个删除来源回归。

评审未发现阻断问题。`read_selected_entries`在短工作锁下取得输入和不可变密文快照，锁外校验权限再解密历史；权限决策表仅活于单次读取。摘要递归检查每个原始条目的存在性、顺序、输入修订与权限；真实`commit_summary`汇总来源引用。返回前再次检查当前scope和工作fence，并要求已选择条目及摘要覆盖的全部来源仍存在。输入变化、取消与租约变化拒绝旧上下文；当前来源权限检查不能解释为重读原始对象字节。

`context_input`最后复核fence，后续模型上下文构建还调用`read_selected_entries`，发送入口再次校验依赖。当前判断依赖这条完整调用路径，不主张单独`context_input`提供跨多事务的原子权限快照。

取消只返回控制视图，不清空数据库检查点；状态变化及lease_epoch阻断旧worker，已取消工作仍拒绝追加输入/额度。前端接收回执后刷新完整工作与成果，空checkpoint不会写回服务端。展示限制：刷新完成前成果计数可能短暂为0；幂等重放的控制回执仍为精简内容，不能解释为删除既有成果。

新增回归使用真实本地HTTP上传、材料凭据、隔离PostgreSQL及真实加密条目写入器。两层摘要正文为合成历史夹具，不伪造模型成功；在外层摘要正文解密后的最终scope复查窗口，通过单独提交的数据库事务删除其传递原始来源，要求抛出`ContextRebuildRequired`，并确认下一次重建排除两层摘要。仅在独立测试进程内将最终required集合临时去掉covered来源后，该用例按预期以`DID NOT RAISE ContextRebuildRequired`失败；仓库实现未改动。

2026-09-11验证：

- `.venv/bin/python -m pytest -q tests/test_hr_agent_history_performance.py tests/test_hr_agent_context.py`：21 passed in 5.36s。
- 先前独立检查`.venv/bin/python -m pytest -q tests/test_hr_agent_repository.py -k 'cancel or resume'`：4 passed、11 deselected。
- 新测试文件Ruff check及format check通过。
- C1/C2前端提交顺序整合无冲突，53个相关测试已包含C2回归，不应再与此前37个结果相加。

未扩展无关测试；未调用模型、生产或外部服务，未进行新的浏览器或进程故障验收。所有删除动作仅作用于一次性测试数据库。
