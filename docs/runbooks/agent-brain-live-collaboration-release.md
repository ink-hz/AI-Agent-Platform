# Agent Brain 实时多 Agent 协作发布手册

状态：候选版本门禁。只有全部自动化和真实验收通过后才允许扩大授权。

## 不变量

- 发布和回滚不得修改 Nginx；验收证据必须显示 `Nginx SHA256 unchanged`。
- Do not restart AI ADMIN or FAE。`/office/?view=services` 在发布前后必须保持可用。
- FAE container ID/ImageID/StartedAt/RestartCount unchanged，FAE 域名和原 IP 均须保持原状。
- 不输出 Prompt、回答、Thinking、Cookie、钉钉标识或密钥；证据只保存 ID、序号、状态、哈希和评分。
- 不切换 Provider、模型或专业 Agent；失败必须显式呈现。

## 兼容发布顺序

1. 备份控制库，记录 Platform、AI ADMIN、FAE 与 Nginx 的无内容基线证据。
2. 发布 Core Chat v3，同时保留 v2 解析能力。
3. 发布本地 Worker，使其同时接受 v2 和 v3；此时旧 Platform 仍可运行。
4. 对 HR 和 5 个 Marketing Agent 逐个运行真实 Thinking 探针；任何一个不支持即停止。
5. 应用 migration 045 and later migrations，发布 Platform，但保持 Brain 与 Brain V2 intake 关闭。
6. 跑 Reference 全场景，再各跑一个真实 HR 与 Marketing 会话。
7. 原子开启 Brain 与 Brain V2，只授权 owner 验证追问、停止、用户中途补充和 Worker 崩溃恢复。
8. 对 owner 开启工作间 UI，分别在桌面和手机复放同一会话。
9. 独立复审通过后才扩大人员授权。

普通发布必须继承已有的 Brain 与 Brain V2 开关，不得把已启用环境强制写回 0。不存在独立的“协作可用性”开关；人员范围只由后端 Agent 授权控制。

## 自动化门禁

后端、WebUI、三个发布脚本、MetaBot Core Chat v3 和六个本地 Agent 契约必须全部通过。Provider manifest 固定 `claude-opus-5`、adaptive + summarized Thinking、65536 max output，不允许 `fallbacks`。

真实验收证据统一要求：`mock_events=0`、`invariant_failures=0`。以下场景逐项记录 Conversation/Turn/Task/Event ID 与终态，不记录内容：

- `simple_direct_answer`：简单问题不虚构工作间。
- `parallel_hr_marketing`：HR 与 Marketing 真实并行派发。
- `progress_wakeup`：真实进展事件唤醒大脑。
- `agent_followup`：大脑向同一子 Agent 会话发送真实追问。
- `agent_stop`：停止被确认，或显式返回 `cancel_unsupported`。
- `user_intervention`：运行中补充要求进入同一 Turn。
- `partial_failure`：部分失败时明确部分交付。
- `adapter_offline`：Mac 离线只影响本地 Agent。
- `provider_refusal`：HTTP 200 refusal 显式呈现，不重试换模型。
- `worker_crash_recovery`：崩溃恢复无重复任务、消息或答案。
- `thinking_stream_interruption`：断流后序号与摘要不重复。
- `mobile_replay`：手机与桌面事件顺序和交付物一致。

## 真实产品验收

1. “介绍一下你自己”直接回答，不显示虚构协作。
2. “为英文能力、视觉技术和硬件产品经历组合的人才制定搜索与雇主吸引方案”必须建立 HR 与 Marketing 子会话。
3. HR 在终态前产生真实发现，大脑基于结果发送真实追问。
4. 运行中发送“只看深圳，排除管理岗”，同一 Turn 接收并应用。
5. 停止一个任务，显示确认取消或明确不支持。
6. 在规定崩溃点重启 Brain Worker，确认无重复。
7. 断开 Mac，本地 Agent 显示不可用，大脑明确部分交付，云端能力不受影响。
8. 手机重开同一会话，内容和顺序与桌面一致。

独立 Codex 或业务专家对拆解必要性、追问价值、过程真实性、证据质量和最终答案改善评分。只保存评分和事件 ID。

## 回滚

1. 原子关闭 Brain 与 Brain V2 intake，拒绝新协作 Turn；根页面仍是 Agent 大脑，运行请求明确返回不可用，不切换到伪准备页。
2. 排空可完成的 follow-up；其余任务写入明确终态，不删除历史。
3. 隐藏工作间 UI，再回滚 Platform 代码。migration 045 and later migrations remain，不回滚结构、不删除数据。
4. 重新核对 Nginx SHA、`/office/?view=services`、FAE 容器身份、FAE 域名和原 IP。

任何一项不变量变化，立即停止并恢复 Platform；不得通过重启或修改 AI ADMIN、FAE、Nginx 来补救。
