# 全景首页实现计划
依据：Orbbec-AI-Engineering/docs/design/2026-09-20-ai-engineering-homepage-final-design.md（803d890）。基线 66b69ff71328be458671b7e612c23f1483365b35，与最近核实发布一致。本轮开发，不执行生产发布。

## 全局约束
首页为可操作全景，不默认展示文档目录。React/CSS/必要 SVG，不引入图引擎。事实及导出只经管理员/owner 保护接口提供。比例用原金额计算。历史部署、健康、效果分开，未知不填零。保留原业务授权、只读限制、员工子入口。不改 frame 防护、不用 iframe。接口/组件/构建由开发验证；浏览器/手机由用户验收。

## Task 1 — 数据与导出
GET /api/v1/ai-engineering/panorama；精确字段合同 webui/src/panoramaTypes.ts。原金额整数分。内容基于已核实底稿，不臆造新事实。GET /api/v1/ai-engineering/export.svg 和 export.png 同一结构化内容生成 1920×1080 总览，版本/生成时间/数据时间/来源编号，不含工作区/身份。固定 action ID，不执行任意 URL。后端授权、比例、导出回归先行。仅改 backend/。

## Task 2 — 全景视图
新增 PanoramaView（data, onAction(actionId), onEvidence(slug)），类型/API client/CSS/tests。领域展开、搜索定位、同尺度比例、展示模式、来源与明确动作。纯视图不加载业务组件。仅新增 panorama 相关文件。

## Task 3 — 业务集成
修改 AiEngineeringLanding/App/AppShell/router，保护检查不弱化，证据按需。复用原生平台业务组件真实接口，输入/执行/结果在图内。关闭保留当前组件，任务不取消；切换可能丢失输入时确认。角色与云只读不绕过。独立专业应用明确入口标识，不伪称集成。

## Task 4 — 验证审查收尾
相关 API/组件回归、构建、公开包内容检查；审查导航/恢复/身份失效/导出一致性。完成后按仓库规定提交归并主线。记录实际能力和浏览器/生产未验收项。

## 进度
工作树已建；数据、视图、集成进行中；验证和审查待完成。
