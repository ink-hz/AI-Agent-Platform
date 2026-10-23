# Office 运营统计发布

2026-10-23。用户授权“做好了就上线”。

- 源码已归并推送 master；API 生产版本 `81d553b17752940d60be5c06b682093eb4088f85`，基于原 `72d636e3559dffbb53850fda677ae170e2c53e5d` 镜像只覆盖本次文件；loopback 以原镜像制作单文件 overlay。现有 FAE 123 及外部身份配置、SDK/平台前端保持；新增迁移 **124_office_operations.sql**。
- 内部接口采用精确 loopback 本地来源及 Office bearer；Office 再验证行政管理员。新增日底账、账号排除、固定模块目录、首页入口点击、日/月汇总及 CSV，原始访问保留 90 天。历史主部门用可信通讯录一次回填，新事件保存发生时主部门；历史混合页面不推算。部门/日期/服务过滤及周期人数去重在数据库完成，接口不返回个人身份。
- 已通过 267 项合并后定向测试（含 PostgreSQL、身份来源/CSRF、排除、幂等、历史/live 分离），以及 4 项真实 loopback 代理测试。Office 85 前端单测、9 浏览器交互及3后端代理单测、构建预算通过。完整代码评审无 Critical/Important 遗留。
- 两次发布因 backfill 执行环境而自动恢复原 API/代理：只读根文件系统禁止 docker cp；无 DAC override 的 root 无权读取应用用户0600源码。随后用候选镜像/应用用户只读验证391位目录映射及377位主部门解密，通过 stdin 私密传参执行回填。复用了相同镜像，未更改验证源码；失败阶段未回填。
- 成功切换时已确认旧 API 容器删除，再回填4052条历史日底账。员工页面且排除指定开发账号的真实原始/汇总对账为8651PV、390UV；9月4500PV/302UV，10月至发布时4151PV/256UV。过滤/CSV/员工管理分区对账通过；生产未造订单、未发通知。若未来回退旧writer，再上线前必须补齐回退窗口内缺失汇总。
- 生产API/loopback健康，其余容器ID、服务挂载及配置保持。最新完整维护清单：`/opt/orbbec-agent-platform/private/office-operations-81d553b17752-1adeb632/future-maintenance.json`，保留32份Compose输入。成功报告与旧配置同目录；不能从基础Compose重建丢失当前身份/业务覆盖。
- Office生产前后端版本 `58bbf39d684c860519634ab41f1081906275243b`，新入口 `/office/?view=operations`。官方资源/MIME/摘要门禁及25路由×4视口共100项运行门禁通过，0错误/资源失败/溢出。公网浏览器均到登录边界，已登录业务展示仍待用户验收；手机业务验证为本地模拟身份交互。

Office仓库证据：`outputs/2026-10-23/office-operations-{build,platform-release,backend-release,webui-release}.json`。私有账号UUID、凭据、目录个人资料不入库。
