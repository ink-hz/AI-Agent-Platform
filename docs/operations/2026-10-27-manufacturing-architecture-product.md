# 制造架构产品化 v0.4 上线

2026-10-27，[大制造工作台](https://agent.orbbec.com.cn/manufacturing)第一轮架构产品化已上线，owner/admin 刷新后可用。

项目中增加工程资料包/修订、独立交接回执、生产准备与缺项问题；“系统协同”维护系统/载体、数据归属及相关待交接工作。首页与项目状态来自真实记录。平台人工核对与外部批准分开，ERP/MRP/MES 真实接口尚未接入。手册仍是一份完整文档，来源与内容未改。

| 项目 | 实际运行值 |
| --- | --- |
| 平台源码 | `b38739dc96b2db8ed856d192b1bd8f86e11a3199` |
| 制造源码 | `430fdca3ad94a0d4cc5e93fce66c03d0baac5ff3` |
| API 镜像 | `sha256:dd0ed414f5a34c90c2cf3d7684f4e220777074052c8bdb395d97155cc977e4c2` |
| 当前维护入口 | `/opt/orbbec-agent-platform/staging/manufacturing-architecture-product-20261027/future-maintenance.json` |
| 维护清单 SHA256 | `1125f43e39ee515bef97d8ab46fa616f157888580c7174dc3766c2cf0f4f6233` |

只增补 `manufacturing_paths.py` 的 14 个精确方法/路径，继续用原 owner/admin、CSRF/Origin、hard-stale 只读与 no-store 边界。镜像从实际旧运行镜像 COPY 一项后端文件和 66 项平台静态资源，其他线上补丁保留。41 项有序 Compose 输入、15 项生产挂载及另外 29 个容器的 ID/镜像均已核对；Nginx 未修改或重载。

合并后制造后端/发布工具 260 项、制造前端 127 项、真实桥接 162 项通过，相关授权 674 项通过，构建与 IIFE/CSS 检查通过。独立两仓库复审通过。实际新镜像 network-none、只读、UID10001 候选使用真实应用工厂与合成身份、临时 SQLite 验证闭环与进程读回；额外只读挂载实际旧制造源码，旧代码读写同一临时库再由新代码读取关联/状态通过，未挂生产数据库。

来源关联已独立存表，旧项目 JSON 与普通任务创建回执保持旧格式；回退不覆盖当前库或删除新表/WAL。切换前一致性备份通过；生产 API healthy、重启 0，源/静态/文档摘要一致，公网入口200、新私有 API 匿名401/no-store。

浏览器视觉与真实业务角色验收由用户完成，尚未由 Agent 执行。具体版本、备份、兼容性和维护说明见[制造仓库发布记录](../../../AI-Manufacturing-Platform/docs/release-architecture-product-2026-10-27.md)。本记录是发布后文档提交，实际运行源码保持上表版本。
