# HR 独立仓库拆分评估（2026-09-15）

基线：已发布主线 `570ea625`。目标目录 `/Users/neo/Developer/work/AI-HR-Agent` 已存在，核实时为空，尚未初始化 Git、复制代码或配置远端。本文为实际依赖核查与边界建议，不把仓库拆分写成已经完成的服务拆分。

## 结论

适合独立维护：HR 已有自己的运行时、工作状态、岗位标准、成果与候选人模型。建议目标是 HR 独立构建、接口测试和发布，平台继续提供企业身份与共享基础能力。当前直接搬目录不能达到该目标。

## 已核实的依赖

| 部分 | 当前实现 | 拆分影响 |
| --- | --- | --- |
| HR API 装配 | `backend/app/main.py:1537` 起在平台进程中创建 HrAgentService、数据库连接、权限与材料服务 | 需要独立应用入口和明确的配置/注入边界 |
| 身份与权限 | `hr_agent/access.py` 依赖 AuthContext、AgentUseAuthorization，检查目录失效及 hr-bot 授权 | 需要可验证的身份与当前授权契约；不能只信前端传入的 user ID |
| 岗位身份 | `hr_agent/resources.py` 对岗位查询 platform_hr；新标准/成果位于 platform_hr_agent | 当前岗位与资源能力必须随 HR 一起划归，不能只搬 hr_agent |
| 附件读取与撤权 | `materials.py`、`material_authority.py` 直接查询 platform_attachments；main 传入附件服务的内部 repository、codec 和 store | 独立服务需要材料读取/当前授权契约，保留撤权、擦除与发送前复核语义 |
| 加密与密钥 | config 依赖 control_plane.crypto.IdentityKeyring、execution_relay.content_crypto.ContentCodec；repository 等使用 SealedContent | 加密格式和密钥归属必须明确，不能因来自 execution_relay 目录就删除或另造格式 |
| 页面 | workspaces/hr 之外还依赖 hrLoopApi、hrLoopCandidatesApi、hrApi、hrR12Api、auth、router、attachmentApi 和共享组件 | 迁移需要完整入口、客户端与必要组件；只复制 HR 页面目录无法构建 |
| 数据库与迁移 | HR 新表、岗位/资源旧 schema、身份/附件依赖及切换迁移跨编号存在 | 按表与权限归属划分，保留已应用迁移校验和；独立仓库不必同时改物理数据库 |
| 发布 | HR Worker 已是独立进程，但当前 API 仍与平台共用镜像及服务 | 独立镜像、API 路由、Worker 配置和维护记录需要配套，不能直接删平台装配 |

源码入口：[HR 访问控制](../backend/app/hr_agent/access.py)、[资源范围](../backend/app/hr_agent/resources.py)、[材料读取](../backend/app/hr_agent/materials.py)、[材料授权](../backend/app/hr_agent/material_authority.py)、[服务装配](../backend/app/main.py)、[当前实现全景](2026-09-15-hr-current-implementation.md)。

## 两种拆法及建议

1. **仅独立代码仓库，仍由平台统一装配发布。** 搬迁成本相对低，但每次 HR 变更仍受平台发布约束，还会新增依赖版本管理。适合作为短暂过渡，不能称为独立产品交付。
2. **独立 HR 应用，通过明确契约使用平台能力。** 需要先处理身份、材料和发布边界；完成后才能独立构建、测试和上线。建议采用这一目标，按依赖逐步完成，不引入跨仓库源码路径引用。

建议随 HR 归属的内容：当前云端运行时、岗位和资源业务、标准/成果/候选人、情报业务、HR 前端、相关测试与现行文档。平台保留企业登录和通用身份授权、共享附件存储与擦除、其他产品及通用运维能力。知识发布资源需要明确输入路径和版本，不把同级 HR-Agent-Knowledge 目录自动认定为本次迁移资产。

## 切换与验收边界

新仓库独立安装依赖、构建并通过真实 HTTP/数据库回归后，才具备切换条件。重点验证身份/授权、岗位当前标准与成果、材料撤权、幂等与恢复；必要时跑自有 Worker 进程故障测试。浏览器与页面验收按用户要求由用户承担。

部署时同一 URL 只能有一个明确的当前 HR 服务。独立 HR 服务承接流量并完成接口核验后，平台 HR 路由装配、旧实现及其专用依赖退出；共享身份、附件和其他产品仍保留。历史废代码不因为拆仓重新引入新仓库。数据表处理与代码删除分开记录，不能把删源代码当成删数据库授权。

仍需在实施方案中落实：身份/材料契约的准确端点与认证方式、数据库角色及表归属、HR 独立构建入口、平台停载时点，以及新 Git 远端地址。当前未创建远端仓库，也未更改生产路由。
