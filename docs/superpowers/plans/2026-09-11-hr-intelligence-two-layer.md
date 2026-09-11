# HR 两层情报 Implementation Plan

> 执行方式：本会话持续完成，使用 subagent-driven-development 做独立资料任务与最终审查，主代理负责页面和发布，不逐项等待批准。

**Goal:** `/hr/panorama` 默认展示完整清洗资料，第二层阅读现有 AI 研究，并按公司/固定资料版本衔接。
**Architecture:** 离线从报告同源归档生成服务端资料包与哈希清单，真实 HR 授权只读接口分页提供；React 两层共享公司，保存各自筛选与位置，旧版链接继续兼容。
**Tech Stack:** Python/FastAPI、React/TypeScript、现有私有文件清单。

## Global Constraints

- 原始范围 12 公司/3437 岗位，报告 38 篇/7 公司；不重新抓取或调用模型，不变造岗位身份。
- 资料源 `2ed2262c-63a0-47e5-996c-7d00eb0be1a0`；每个原文来源哈希可追溯，缺失与失败不写零或推断填充。清洗保留段落编号和完整职责要求，明确截断/缺失。
- 权限同现有 research，私有 no-store；固定 edition 不匹配 404、包校验失败 503；正文不进入公开 bundle。
- 旧研究 edition 与原文保持不变，旧 company/topic/bundle 深链接保留。只部署 API，其他容器不重启。
- 只做新增数据路径真实 HTTP/授权/分页和相关组件/构建检查，页面最后；不重跑旧模型或全量测试。

## Task 1：资料清洗包与真实读取接口

Files: `scripts/build_hr_source_manifest.py`, `backend/app/hr/source_library.py`, `backend/app/hr/source_content/`, `backend/app/hr/panorama_routes.py`, `backend/app/control_plane/authorization.py`, `backend/tests/test_hr_source_library.py`, `backend/tests/test_hr_source_http.py`。

- [ ] 先写离线代表样本清洗、固定版本/权限和分页的失败用例；只运行新用例。
- [ ] 从本机确定归档构建资料包，保留 3437 统一身份，将原始职责/要求/公开字段按真实来源关联，提供 12 公司覆盖与官网可读文本。输出哈希清单。
- [ ] 提供下述 snake_case JSON 接口，支持现有登录/中央 HR grant；针对新接口运行一次真实 HTTP 一次性库回归。

Contract:

`GET /api/hr/panorama/sources` → `{edition,source_bundle_id,observed_at,job_count,companies:[Company]}`。
`Company` → `{company_key,name,aliases,job_count,coverage_state,document_state,observed_at}`。

`GET /api/hr/panorama/sources/{company_key}?edition=...&q=...&location=...&channel=...&offset=0&limit=25` → `{edition,company:Company,documents:[{title,text,source_url,observed_at,evidence_sha256}],channels:[{channel,source_url,state,observed_at,job_count,error_code}],limitations:string[],locations:string[],channel_options:string[],items:[JobSummary],total,offset,limit}`。
`JobSummary` → `{job_id,title,location,status,source_url,observed_at,channel}`。

`GET /api/hr/panorama/sources/{company_key}/jobs/{job_id}?edition=...` → JobSummary + `{edition,company_key,duty,requirement,fields:[{label,value}],evidence_sha256,public_job_key,source_kind,content_note}`。字段缺失返回 null 或空数组，content_note 如实说明不能读取的原文。source_kind 为 job/archive，不把入口当直链。

## Task 2：两层界面与来源回溯

Files: `webui/src/workspaces/hr/HrSourceWorkspace.tsx`, `hrSources.css`, `hrSourceTypes.ts`, `HrPanoramaWorkspace.tsx`, `HrResearchWorkspace.tsx`, 对应组件测试。

- [ ] 先写默认资料层、切报告保留公司、详情关闭保留筛选、拒绝响应不显示旧正文测试。
- [ ] wrapper 增加「原始资料」「AI 分析报告」导航，query layer=sources/research；research/edition 老链接优先报告，历史参数仍旧页。共享 research_company；保留 source_q/source_location/source_channel/source_offset/source_job/source_edition。
- [ ] 资料页展示公司目录、渠道覆盖、可读官网资料、岗位分页/搜索/筛选和详情。报告层只复用已有报告，使用其 source_bundle_id 对应固定资料版本，不假定当前新包可替代旧来源。
- [ ] 现有源 URL 能唯一对应来源岗位时链接具体详情，含多个同源岗位时链接公司/搜索范围并标明定位粒度；其他引用保持原行为。
- [ ] 运行新相关组件与 TypeScript/Vite 构建，浏览器可用时做最后一轮关键交互。

## Task 3：审查、定向发布与交付

- [ ] 汇总差异进行一次范围明确的代码审查，修复重要问题，不重跑已通过用例。
- [ ] 核对线上 current/API 仍为 fe10fae，准备源码与 dist。基于确切运行镜像的增量层发布新增 source 包/接口、研究引用扩展与静态资源，不更新其他运行服务。
- [ ] 复用既有发布锁、磁盘阈值、失败回滚、staging 清理与当前加两版保留。匿名页面原有 401 不误判发布故障。
- [ ] 发布后确认实际内容 12/3437、哈希、API 健康、匿名保护、公网 JS/CSS 与非 API 容器 ID 不变，更新续接与验证边界。
