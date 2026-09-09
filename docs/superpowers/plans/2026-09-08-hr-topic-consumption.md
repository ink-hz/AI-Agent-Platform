# HR 专题展示与使用实施计划

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded producer and review tasks; root implements and integrates the consumption path. Track each item below.

**Goal:** 将已核验的专题正文、研究范围、公司关系和明确状态接入专题阅读与选材讨论。

**Architecture:** 延续已批准的公司／专题设计。专题目录由生产内容声明，持久化在 source-catalog.json 的 topics 数组；生成、导入验证、API 和 Agent Markdown 共用此目录。旧内容包不原地修改；通过可追溯的衍生包添加经过内容复核的目录，保留原分析、来源和时间。

**Tech Stack:** Python / FastAPI / PostgreSQL / React / TypeScript。

## Global Constraints

- 不以关键词、公司名称字符串匹配、证据输入全集或数组位置生成业务关系。
- scope.company_keys 是声明的统计样本范围；discussed_companies 是正文实际讨论对象，必须带明确结论身份与说明。界面分开表达。
- 不伪造趋势、比较结论、模型调用或更新后的分析时间。原始内容可以 limited 状态阅读，边界必须显示。
- 页面和筛选不调用分析模型。带入材料作为用户输入，继承 12 KiB 材料预算和 32 KiB 消息预算，不自动发送。
- 所有 API 进入真实中央授权和 HR Agent grant 验证。新接口从一开始登记授权模板。
- 使用现有独立 worktree，不改主目录或其他会话工作。

## Contract

```json
{"topic_id":"campus-recruiting","title":"校招布局","question":"当前校招岗位分布在哪里？","scope":{"description":"当前采集范围内的校招岗位","company_keys":["agibot"],"tracks":["campus"]},"analysis_state":"limited","unit_ids":["UUID"],"discussed_companies":[{"company_key":"agibot","unit_id":"UUID","claim_ids":["I-track-campus-1"],"explanation":"正文列明该公司的校招样本数量。"}],"limitations":["公开岗位不等于编制；未形成完整能力比较。"]}
```

analysis_state 为 available / limited / insufficient_evidence / missing；missing 不得附单元，其他状态必须引用真实 topic 或 track 单元。目录 ID、公司 ID、单元和结论 ID 校验，拒绝重复和悬空关联；缺少目录的旧包返回 metadata_missing，不由技术文件名恢复业务元数据。

GET /api/hr/panorama/topics 返回 {bundle_id, generated_at, state, items}；items 为上述目录加 summary。GET /api/hr/panorama/topics/{topic_id}?bundle_id= 返回 {bundle_id,generated_at,topic,units,companies}；units 保留七段 response 和全部稳定结论 ID；companies 用明确关系返回 canonical_name。公司详情的 related_topics 只由 discussed_companies 产生，统计范围不会产生实质关系回链。

## Tasks

- [x] 1. 生产契约与产物：新增 app/hr/topic_catalog.py 校验器；生成器从目录渲染业务标题、问题、状态和范围；导入前验证；新增经过内容复核的样本目录及衍生包工具。先测重复／悬空关系／missing 不可伪装为 ready；再用真实 bundle 核查输出哈希及分析内容未变。
- [x] 2. 后端读取：新增 topic_intelligence.py 与窄查询；接入两个 GET 接口及中央授权；测试目录、固定包读取、失效／缺失、关系范围与生产身份授权。真实 bundle HTTP 验收从生产构建产物导入，禁止手填不存在的关系冒充产物。
- [x] 3. 前端：独立专题列表和详情组件，先判断、后证据和公司；明确 limited 等状态；搜索、返回、异步请求取消、失效账号清理；公司／专题双向导航；通用选材支持 topic 身份，保留已有公司行为。组件测试覆盖实际响应、迟到响应与预算。
- [x] 4. 联合验收与发布：重点 API、生产契约与组件回归通过后构建；必要的一轮浏览器验收；独立审查；固定代码与产物，沿用发布锁按顺序先安装／导入已验证内容，再切换 API。记录生产验收范围，不使用真实业务消息测试。

## Validation commands

从 backend 使用主仓 .venv/bin/python：
```sh
python -m pytest tests/test_hr_topic_catalog.py tests/test_hr_topic_intelligence.py -q
HR_COMPANY_TEST_BUNDLE=/absolute/path/to/verified/bundle python -m pytest tests/test_hr_company_intelligence_http.py -q
```
从 webui：
```sh
npm test -- src/workspaces/hr/HrTopicWorkspace.test.tsx src/workspaces/hr/hrIntelligenceReference.test.ts
npm run build
```

内容验收重点是结论原样保留、范围含义清楚、关系明确引用原文，不以测试数证明比较质量。暂时缺少跨期观察的 trends 不进入本次发布目录。
