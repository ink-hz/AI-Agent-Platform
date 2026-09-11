# 旧 HR D1 本地临时补救（2026-09-11）

状态：实施前决策已固定，最小补救与本地回归已完成；仅本地一次性数据。未发布，未查询生产，产品影响与发布验收仍待确认。本记录不关闭 D1 生产处置，也不代表新 `hr_agent` 的完整 W2/W8 验收。

## 基线及根因

当前 `347594c` 的 `backend/app/agent_brain/conversation_context.py::_load` 以 `conversation_id` 和 `summary_through_seq < seq <= user_seq` 选择历史，未标注个人范围；共享摘要直接进入上下文。`build_direct` 的 64 条/96 KiB 上限仅限制大小，不证明候选人范围。当前任务提供方已有经过校验的单候选人 envelope，不能据此授权整段历史。

对照 `git show 38dfc7a:backend/app/agent_brain/conversation_context.py` 的 192–207 行、286 行：v6 使用 `hr_input_context.scope.positionId` 过滤，历史下界为 0，省略共享摘要。同一岗位 A/B 仍共享历史；与当前基线机制不同。这是静态机制核查，未执行独立 v6 checkout 的模型输入复现，不能算 P1 所要求的 v6 运行证据。本补救不引入 v6 字段/架构，不修改或适配 v6 分支；该分支仍不得按历史计划继续上线。

## 实施前选择与文件范围

选择有界临时限制：所有旧 `mode=direct_agent, direct_agent_id=hr-bot` 会话只加载本轮用户消息，省略会话摘要，不向压缩模型提供旧片段。覆盖候选任务、未绑定岗位的普通 HR 对话、解析入口和已有混合摘要；不能靠当前任务种类或关键词推测旧消息不含个人事实。非 HR 保持原历史、摘要与压缩行为。

保留服务端已验证的当前岗位/候选人 envelope、工作流约束、当前附件绑定与解析附件授权。当前合法岗位基准仍可用，不改任务创建或结构化候选人成果协议。

两种长期方案均需比本补救更大的契约：按候选对象分会话必须处理普通对话、多人比较及既有会话迁移；逐消息/摘要标注对象必须回填可证明来源、递归校验摘要并处理未标记旧消息。旧消息/混合摘要没有范围证明，本次不尝试自动回填或基于文本猜测。

产品限制：旧 HR 自动多轮续聊暂时失去历史，包括同一候选人的继续追问及普通岗位讨论。用户需在本轮重新提供必要问题和材料；明确“比较 A/B”不解锁旧历史，只保留本轮明确提供的双方材料和当前已验证上下文。旧 `HrPositionTaskService.STARTABLE_TASK_KINDS` 不包含 `candidate_comparison`，本补救不新增结构化多人任务支持。

已有 `CandidateService.compare` / `candidate-comparisons` 接口独立按用户、岗位、基准和明确选择的候选关系生成证据视图，本补救不修改该能力，也不拿它授权共享聊天历史。`DirectMissionAdapter.prepare` 会优先重放已冻结命令，跳过上下文重建；补救仅覆盖新组装上下文。已冻结/在途输入、旧执行端可能持有的历史及正式发布恢复安排需另行验收，不能声称已撤回先前发送的内容。

具体允许修改文件（先于实现固定）：

- `backend/app/agent_brain/conversation_context.py`：历史读取下界与摘要选择；现有提供方不变。
- `backend/tests/test_agent_brain_conversation_context.py`：已有非 HR 容量用例定位。
- `backend/tests/test_agent_brain_hr_history_isolation.py`：真实临时 PostgreSQL/任务服务、A→B、摘要、普通 HR、明确比较和非 HR 回归。
- 本记录。根目录两份 HR 文档与交付计划由主代理同步，本子任务不修改。

## 验证记录

环境：`backend/.venv` 使用现有本地 Python 环境；`initdb` / `pg_ctl` 来自 `/opt/homebrew/opt/postgresql@17/bin`。测试的 `control_database` fixture 在 `/tmp/control-pg-*` 创建 PostgreSQL，仅监听随机本地端口，应用现有迁移后清理。fixture 中的 `environments['production']` 只是该一次性库的角色隔离测试标签，不是生产连接。

失败复现命令（实现前）：

```sh
cd backend
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH .venv/bin/python -m pytest tests/test_agent_brain_hr_history_isolation.py -q --tb=line
```

结果：`11 failed, 1 passed`。候选任务四种组合（build/build_direct × 原历史/混合摘要）均在 B 上下文中读到 `虚构甲独有经历：紫铜海鸥项目七次冷启动` 或甲的助手分析；普通 HR / 显式比较六种组合也混入旧消息或摘要；长历史产生包含甲的压缩候选。首轮测试中助手 fixture 超过原有事件上限，先修正为 2,000 个汉字再确认上述 11 项均为预期行为失败，未将 fixture 错误计为复现证据。

修复后的相关回归命令：

```sh
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH .venv/bin/python -m pytest tests/test_agent_brain_hr_history_isolation.py tests/test_agent_brain_conversation_context.py tests/test_hr_task_context.py tests/test_agent_brain_attachment_delivery.py -q --tb=short
```

结果：`50 passed in 8.12s`，无跳过。新增用例精确名称：

- `test_same_position_candidate_switch_omits_a_and_keeps_verified_b`：真实任务服务与持久上下文提供方建立同用户/岗位 A、普通追问、B；跨用户任务拒绝；B 的当前正文、已确认基准、候选 ID 和唯一 B 文档附件保持。
- `test_unbound_hr_and_explicit_comparison_never_unlock_unproven_history`：未绑定岗位的 B 评估、通用岗位问题、明确比较双方本轮材料；原历史/混合摘要均省略，完整当前正文保持。
- `test_hr_does_not_send_large_old_history_to_compaction`：不向压缩步骤提供甲的历史。
- `test_non_hr_preserves_shared_summary_and_owner_rejection`：FAE 历史与摘要、真实用户边界不变。既有 120 条容量回归改为 FAE，以继续验证非 HR 的 64 条/96 KiB 行为。

另跑 `.venv/bin/python -m pytest tests/test_hr_candidate_service.py -q -k comparison --tb=short`，结果 `1 passed, 13 deselected`，保留原比较服务的同岗位/基准证据视图与不排名行为。

测试通过真实任务/会话服务、当前候选人 envelope 校验、PostgreSQL 加密持久化和 `ConversationProjection`；人物/候选档案由本地 fixture 建立，模型完成事件为本地 fixture，未调用模型。断言对象是上下文构建器返回的待发送数据，未捕获真实 HTTP/执行端最终模型输入，不称为模型发送边界或业务质量验收。

分类：本轮完成服务/数据库回归，以及既有附件授予与交付相关回归；没有新增 HTTP 验收，没有 D1 专项附件原文字节传输/撤权回归，没有进程故障测试、前端组件测试、浏览器验收或生产验收。未做独立 v6 动态复现。P1 的这些剩余证据不能因本地补救测试通过而关闭。
