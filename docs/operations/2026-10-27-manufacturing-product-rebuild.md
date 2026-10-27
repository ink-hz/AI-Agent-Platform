# 大制造工作台项目管理版上线

后续已发布[架构产品化 v0.4](2026-10-27-manufacturing-architecture-product.md)，当前运行版本和完整 41 项维护链以新记录为准。下文为历史发布事实。

后续已部署[手册连续阅读修正](../../../AI-Manufacturing-Platform/docs/release-handbook-continuous-2026-10-27.md)：手册整篇展示，目录仅作页内定位。当前运行平台源码 `59f97fee18dd04155810890a7fe930124979e78d`、制造源码 `161292be7c01038cc7c25b5f36aea04577c19823`，镜像 `sha256:4d98690675b1e8623a04be44f1e950735d05ef69c598bd8f68c02133f20fb381`；维护入口已更新为 `/opt/orbbec-agent-platform/staging/manufacturing-handbook-continuous-20261027/future-maintenance.json`，完整 Compose 链为 40 项。77 项制造前端测试、构建、隔离候选及生产边界检查通过，浏览器验收仍由用户完成。以下保留原项目管理版的历史发布事实。

2026-10-27，[大制造工作台](https://agent.orbbec.com.cn/manufacturing)已按项目和事项重构上线。刷新页面后，以平台 owner/admin 登录。浏览器页面验收由用户完成。

首页为实际项目、待确认与阻塞事项和动态；支持项目列表/看板、交付目标与负责人、阶段开始/受阻/完成/重开、任务/问题/确认、资料协作及归档恢复。旧流程记录保留为参考。制造手册复用平台现有 ArticleMarkdown/MermaidDiagram，包含最新配色和16个图块；正文与更新时间由制造仓库准确提交生成。

| 发布项目 | 实际值 |
| --- | --- |
| 平台源码 | `73cde12661dca8f15cff7b57fa8aa2ec4bc8afeb` |
| 制造源码 | `0918bb19054b4f58a1b940b0e36666cdc2910e0c` |
| API 镜像 | `sha256:fcc02c82050f2fda8628397be02c3c2e1180e0ba779ab0ccbce8a13180c35d6c` |
| 维护入口 | `/opt/orbbec-agent-platform/staging/manufacturing-product-v2-20261027/future-maintenance.json` |

镜像从实际旧bce8f412派生，仅 COPY 一项后端权限路由文件和平台静态产物；保留其他线上补丁。原38份Compose保持并追加overlay，总39份；15项挂载中仅替换制造代码source，原数据和另外13项挂载、网络、安全配置及其他29个容器保持。Nginx未修改/重载。

切换前以UID10001在线备份制造SQLite（涵盖WAL，0700目录/0600文件，quick_check通过），再初始化新增项目表，没有创建生产演示记录。应用回滚保留数据库，不用旧备份覆盖后续用户写入。实际维护以当前有序清单和运行标签为准，不能使用过时current链接或全量deploy脚本。

代码审查通过。发布制造源码合并后175项Python、72项业务前端通过；平台776项相关后端、32项相关前端和构建通过，归并后102项桥接及21项包装器/最新版手册解析通过。真实隔离镜像使用合成owner/admin/member主体、实际应用工厂和真实权限/CSRF，验证项目/阶段/事项/文档/幂等/冲突/归档与另一进程持久读取；正向验证没有冒用员工Session。生产healthy、匿名新API401/no-store、完整内容摘要、原入口资源及容器保持检查通过。

真实账号页面验收尚未执行；ERP/MRP/MES仍为关系登记，实际接口尚未验证。完整设计、手册维护、版本、备份及证据见 [制造仓库发布记录](../../../AI-Manufacturing-Platform/docs/release-product-v2-2026-10-27.md)。后续文档与发布工具提交不改变上表实际运行版本。
