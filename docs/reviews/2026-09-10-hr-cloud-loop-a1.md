# HR 云端 Loop A1 工程交付与验收记录

联合评审请从 [A、B 评审包](2026-09-10-hr-cloud-loop-ab.md) 开始。本文保留 A1 交付时的历史快照；B 已在继承 A1 的分支上交付，其更新与未验项以联合评审包及 B 报告为准。

日期：2026-09-10。分支：`feat/hr-cloud-loop-a1`，实施基线 `2bddc77`。用户最新要求是逐阶段完成并验收：本批只完成 A1，B–E 不自动开始。

## 交付范围

新 `backend/app/hr_agent/` 独立运行，不调用旧 DirectWorker、签名 Relay、MetaBot 或 CLI。HTTP 与 Worker 共用工作/模型步骤/操作/成果数据，使用平台身份、当前 HR 使用授权和来源权限；096 迁移只由显式 migrator 执行。默认关闭，Compose 覆盖文件提供单独启用入口，本次未启动它。

已经验证的工程链路是：真实 HTTP 上传虚构公开 JD → 附件校验/扫描 → 不绑定旧会话的 UTF-8 正文引用 → 新工作 → 本地模型替身自主返回读取/保存工具 → 真实成果修订 → HTTP 精确读取 → 用户建立并选择岗位 → 关联同一成果 → 来源失效后拒绝正文读取。它不是岗位校准的专业质量验收。

| 责任边界 | 主要实现 | 可检查的证据 |
| --- | --- | --- |
| 接口与主体 | routes/service/access，main 中独立装配，中央路由授权 | Cookie 身份边界、Origin/CSRF、HR grant、跨 owner、重复键与变更冲突；无岗位可提交 |
| 持久记录 | repository/repository_views、独立 096 | 输入修订、模型请求/完整响应、工具槽位/回执、成果和来源边；应用角色无 DDL，已存在但缺权限也不就绪 |
| 当前范围与摘要 | context、entries/summary provenance | A→B 剔除 A；显式比较可读二者；普通 note 不能消除范围；递归摘要缺任一原条目便不使用；撤销对象权限也隐藏检查点问题文本 |
| 材料与目录 | materials/resources | 原件/解析身份分开、严格 UTF-8、准确 revision/hash、公开发布 manifest；目录游标绑定 owner/工作/输入，成果发现跨数据库页继续 |
| 模型与工具 | model/tools/runtime | 完整参数才执行，五工具由模型选择；原生 OpenAI/Anthropic 消息与用量归一化；未知工具安全错误；提问持久等待 |
| 执行与恢复 | worker、leases、attempts、operations | 独立心跳，真正 SIGKILL/重启同一库；prepared 未发不扣次，committed 不重问，未提交事务回滚，已提交成果/回执只保留一份 |
| 预算与停止 | research/finalizing/waiting_budget | calls/token/活动时长独立耗尽；增长上下文先触 token 上限；发送前重查预算；收尾阶段重启不回研究；显式追加不清零 |
| 安全与诊断 | model/observability/materials/diagnostics | 单一配置端点、绝对请求截止；普通日志白名单；读取材料只在有界内存中进行，SIGKILL 无明文文件遗留；启用诊断必须有独立审计回调 |

## 自动验证

所有测试数据和进程均在本机一次性环境；PostgreSQL 使用真实迁移、约束和受限应用角色，不以业务仓储替身代替持久化。测试数据库在结束后销毁。

最终全批回归 **168 passed（89.89 秒，无 skip）**；现行相关接口/启动回归另 **15 passed（0.59 秒）**。A0 自检 55 定义、133 例、18 条件规则、6 个正文证据 ID 通过；新运行包 Ruff 与差异检查通过。命令与交付状态同步到交付计划 §6。

```sh
backend/.venv/bin/python docs/superpowers/specs/hr-cloud-loop/selfcheck.py
backend/.venv/bin/python -m pytest backend/tests/test_hr_agent_*.py backend/tests/test_hr_cloud_loop_docs_selfcheck.py -q
backend/.venv/bin/python -m ruff check backend/app/hr_agent
```

现有路径回归还覆盖 `test_hr_position_api.py` 与默认云端应用不启动旧 poller 的相关测试；没有执行无关全量测试或浏览器巡视。新接口没有配套业务 UI 改动。

独立审查发现并修复了：准确引用与确认冲突 Schema 尾项、日期/配置数值校验、就绪权限、旧工具配对、prepared 配置核验、递归摘要、撤权后的回执/检查点、发送前预算、嵌套用量与缓存计量、截断响应、异步请求绝对截止和错误保密、材料崩溃遗留、受限诊断审计。最后的有界审查确认上述阻塞项闭合。

FAE 参考提交 `b49eeed` 在本机 `AI-FAE-Agent` 仓库重新以 `git cat-file -e` 核对存在；A0 文档自检的提交检查范围是当前 Platform 仓库，不能将其误称为跨仓全量检查。

## 验证边界与已知限制

- HTTP 链路保留真实中间件、中央授权、CSRF 和数据库；部分路由测试替换会话验证与 HR grant 服务边界。正式 Worker 入口测试另行建立真实测试身份与 HR 授权记录。
- 模型只用本地 HTTP SSE 或明确注入的脚本替身。部分故障测试注入内部 observer/上下文，以准确停在事务边界；另有正式上下文、目录、材料、五工具和真实 Worker 启动的完整装配测试。不能称作真实模型能力、方法运用或招聘质量通过。
- 长文完整装配使用 `conservative_utf8`：UTF-8 字节数加封装预留的保守估算，测试 profile 的压缩目标/触发为 20,000/24,000，calls/token/time 总额度不因此改变。工具 Schema 去重后仍有固定成本。它没有测出真实模型 tokenizer 下 12,000/8,000 的业务表现，也没有证明 600k 可达 32 轮；真实提供方配置与长任务成本仍需后续校准。缺失 usage 保留预留，不计作 0。
- A1 使用单份不可变的角色/目录发布挂载。配置或发布内容改变会阻塞旧输入，不能静默切 current；真实专业库与跨发布内容承接属于 B1/C2，W12 专业场景尚未通过。
- 取消或输入更新后，旧 HTTP 请求可能继续到本次截止时间（至多 120 秒且受剩余活动预算限制）。旧响应只能结算用量，不能提交旧回答或工具副作用。此处不声称供应商请求恰好一次或已撤回外发内容。
- B3 标准提案/确认尚未开放，相关调用明确不可用；PDF/DOCX、批量候选人、文件交付/下载、官网调查、业务页面、真实候选人材料和生产切换仍未交付。A1 的虚构候选范围测试不等于现行 D1 修复。
- 本机没有 Docker CLI；Compose 的 YAML 结构、默认关闭和挂载一致性有测试，未做 `docker compose config`、镜像构建或云端部署验收。启用方式见 [本地运行说明](../runbooks/hr-agent-local-runtime.md)。

## 阶段结论

A1 的工程实施与验收证据提交给用户审阅；只有用户验收后才进入 B。未推送远端、未合并 master、未部署，也未开启生产或真实候选人材料闸门。
