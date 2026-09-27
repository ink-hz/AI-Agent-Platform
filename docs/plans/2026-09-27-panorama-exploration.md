# 全景 AI 探索实施计划

依据已确认方向及 docs/design/2026-09-27-panorama-exploration.md，当前会话逐项实施。

目标：让探索思考直接进入业务全景，保持首页简洁，保留真实事实和候选判断的边界。

架构：React 本地内容模块定义八个独立主题；PanoramaView 控制视角与主题，PanoramaCanvas 突出关联节点并复用详情位置。无新依赖、后端协议或持久化变更。

约束：不改正式图关系、草稿或组织数据；不推断责任与已实施状态；不访问浏览器、调用模型或发送业务消息；用户原工作区未跟踪文件保持。

- [x] 先扩充 webui/src/panorama/PanoramaView.test.tsx：默认无探索正文，切换客户/产品/市场等主题，节点改名和删除、编辑关闭、原入口可用；运行确认缺失功能失败。
- [x] 新增 webui/src/panorama/panoramaExplorations.ts：逐项写明八个主题的判断、方向与验证问题；相关节点稳定 ID 独立维护。
- [x] 新增 webui/src/panorama/PanoramaExploration.tsx：主题导航及精简详情，使用当前节点名称；在 PanoramaView.tsx 管理开关，PanoramaCanvas.tsx 标注并复用详情；panorama.css 添加限定样式。
- [x] 运行 npm test -- src/panorama/PanoramaView.test.tsx src/panorama/panoramaApi.test.ts src/AppShell.panorama.test.tsx src/PanoramaRecovery.test.tsx src/PanoramaLayoutHost.test.tsx src/panoramaNavigation.test.ts src/panoramaRouting.test.ts，再运行 npm run build。
- [x] 独立审查内容与工程边界，修正有效问题，归并主线；使用准确候选版本更新页面并核对资源、原授权与发布状态。
- [x] 同步工程仓库维护设计，记录完成范围和未做的真实业务验证。

完成记录：相关前端 46 项、构建、发布事务 15 项及线上 35 项检查通过。应用版本 5d05a4ba，发布/草稿/上一版及修订号保持；未做真实业务场景或浏览器视觉验收。见 ../operations/2026-09-27-panorama-exploration-release.md。
