# C2 固定情报引用实施与验证

## 实施前设计（2026-09-11）

授权范围：交付计划 C2/W7；仅本地公开资产、一次性输出和测试数据库，不采集、不重新分析、不连接生产或发布。

实际缺口：`KnowledgeReleases` 已支持当前发布目录和保留的 ExactRef；现有构建器只导入方法，HTTP 目录过滤 method，正文路由也写死 method。无需另建情报库或改旧业务导入表。

选择沿用同一不可变发布格式，新建 `backend/tools/hr_agent/build_intelligence_release.py`：显式指定已完成 Bundle v2，经现有严格校验后将 `agent/**/*.md` 原字节复制到新发布；合并已审阅方法与角色；正文 ID 由原路径确定，revision 为 Bundle UUID，sha256 为原文摘要。索引不预置公司/专题名单，范围来自已校验 Markdown 元数据。保留来源 manifest 和来源身份记录；源 Bundle 永不写入。构建完全成功后才原子更新所指定输出目录的 current 指针，旧发布保留。

不采用运行时连接旧业务库：这会扩大权限和部署面。也不将报告改写成统计摘要：这会丢失证据、未知项和研究正文。工程验收不等于报告专业质量验收，旧 86 单元 Bundle 曾被指出模板化，不能因原字节导入就替代后来语义报告。

接口仅扩展 `routes.py` / `service.py` 的知识目录及精确正文 `kind=method|intelligence`；默认仍为 method。新 HTTP+数据库测试置于 `backend/tests/test_hr_agent_intelligence_releases.py`，覆盖当前目录、旧正文、新工作显式旧引用、清除/篡改不可用、身份与参数边界。构建与真实资产只读验证置于独立新测试文件。方法冻结行为保持既有契约。

## 已实现的接口与发布边界

- `GET /api/hr/agent/knowledge?kind=intelligence` 返回当前发布的情报目录；返回 `objects`，描述包含源报告范围、资料时点和覆盖状态。
- `GET /api/hr/agent/knowledge/{id}/revisions/{revision}?sha256=...&kind=intelligence` 读取完整 ExactRef。kind 为封闭的 method/intelligence 枚举，省略仍为 method；HR 使用授权、登录与禁止缓存继续生效。
- B2 为当前目录时，新工作可以明确选中 B1。工作冻结 B2 方法/角色目录，选中 references 保存 B1 原身份；情报工具目录来自该工作冻结的 B2，正文读取仍是 B1，不以 B2 替代。
- B1 发布目录删除后返回 `reference_unavailable` / HTTP 410；保留目录中正文篡改返回 `configuration_unavailable` / HTTP 503。当前 B2 正文仍可读取。沿用现有失败关闭语义，不将损坏伪装成空结果。
- 构建失败不更新指定输出目录的 current；已存在的同发布内容不允许覆盖。没有自动删除/过期清理功能。复现命令为 `python -m tools.hr_agent.build_intelligence_release --bundle <明确的完整本地Bundle目录> --output <明确的一次性输出目录>`，在 backend 目录使用项目虚拟环境执行；输出目录的上级路径应为真实路径而非符号链接。

## 本地真实资产证据

只读源目录：`/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c`。

| 项目 | 实测值 |
| --- | --- |
| Bundle schema / UUID | v2 / `2b49ecc4-42fe-45ae-80ac-26891f42ac6c` |
| 源 manifest SHA-256 | `5d446fe136a3fe2d3d1bb06873f9584e4a357f9546e9f66e686e14950dab98a3` |
| manifest generated_at | `2026-09-06T14:37:42.467393+00:00` |
| 一次性构建 release | `hr-intelligence-2041182e332c1451c12d1d72` |
| 导入内容 | 86 份 Agent Markdown；另有 9 份既有方法/来源资源 |
| 范围映射 | 12 份 company、72 份 topic（含方向/任务）、2 份总览/索引 |

所有 86 份 Markdown 在临时发布中逐字节与源文件相同，保留正文中的结论、证据链接、未知项、替代解释和 YAML 资料时点。通过 HTTP 另外读取真实公司、专题各一份并逐字节对照。构建可重复，携带原始 manifest 和 `intelligence-provenance.json`；未将原本机路径写入公开目录。测试与独立构建使用临时目录，结束后清理；源 Bundle 未写入，未导入现行系统业务库，未修改生产 current。

源 manifest 不含 producer Git SHA；旧运行手册记载的生产者提交不能冒充本次从 manifest 核验的代码身份。方法来源沿用随发布保留的 `provenance.json`。实际发现默认本地根目录共有三个既有 Bundle，不据日期或 UUID 自动选取所谓最新有效包。

## 验证

测试先出现预期失败：缺少新构建模块；情报精确 HTTP 读取返回 410；非法 kind 仍返回 200。实现后执行：

```bash
HR_TEST_INTELLIGENCE_BUNDLE='/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c' \
  .venv/bin/python -m pytest -q \
  tests/test_hr_agent_intelligence_build.py \
  tests/test_hr_agent_intelligence_releases.py \
  tests/test_hr_agent_knowledge_releases.py \
  tests/test_hr_agent_b_routes.py
```

结果：**12 passed in 7.66s**。新文件及 routes/service 的 Ruff check 与 format check 通过。

- 接口/数据库回归：FastAPI TestClient 经过真实 ASGI HTTP 路由、身份中间件、CSRF/Origin、幂等键、当前 HR 使用授权；底层为自建一次性 PostgreSQL，保存/读取实际输入 references 和发布身份。身份提供方使用既有本地测试替身，不连接真实组织身份服务。选中旧正文、当前目录、删除/篡改不可用、拒绝非法 kind、撤销 HR 使用授权、未登录均覆盖。
- 真实资产：显式设置上述环境变量，执行严格 Bundle 校验、临时构建、全部正文对照及公司/专题 HTTP 对照。未设置时两个实际资产用例明确 skip；夹具不会冒充真实资产。
- 进程故障、前端组件、浏览器、生产验收：本子任务未执行，不能据本组结果宣称这些边界通过。没有调用模型。

## 保留的限制

本次完成固定引用的工程承接。Bundle 校验仅确认完整性，不确认研究专业质量；该 86 单元旧包已有模板化质量问题，本次不改写也不认证其业务可用性。后来公司/专题语义报告尚未与本地实际来源完成核对，P2 的新旧资产衔接仍待完成，不能把本次旧包临时导入称为当前生产内容或取代新版语义研究。

仅复制 Agent Markdown，不把 `analysis.json` 的完整模型请求、原始证据归档、PDF/XLSX 自动开放为模型工具。用户可看到正文中的源链接和哈希；底层证据全文交付不在此子任务完成。

生产发布、保留期/清除规则和页面资料时点/范围验收仍未完成。未进行自动清除；W7 工程证据不关闭这些交付条件。
