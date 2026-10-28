# Office 运营看板二级部门汇总

2026-10-28。用户要求默认合并二级部门并保留下级明细；沿用运营看板完成后发布授权。

## 发布范围

- 源码 `fae1e92820d68bd4b7d00f0685afbb5dc4202601` 已归并并推送master。新增迁移125扩展 `read_office_operations_v124`，追加 `department_groups` / `department_hierarchy`，保留现有返回字段、过滤与导出。
- 使用当前完整通讯录，根节点为一级，其直接子节点为二级汇总。同名部门仅按唯一在职人员归属拆分；无法确认时保留未匹配项。汇总及服务格子人数从actor事实独立去重，不能相加子部门人数。
- 只部署SQL函数和迁移登记，事务使用既有advisory lock及所有者角色。原始访问与日底账不改；没有业务写入或通知。
- 实际API镜像为 `orbbec-agent-platform:manufacturing-v2-22f65f19-19b58607`，loopback为 `orbbec-agent-platform:office-proxy-81d553b17752940d60be5c06b682093eb4088f85`。容器及Office服务PID均保持，不重启或覆盖其他业务。

## 验证

- 9项运营PostgreSQL测试通过；2项迁移幂等/历史迁移字节不可变检查通过。覆盖production/preview、跨部门去重、重名归属、未知部门、排除账号、日/月及过滤。
- 生产8组前后对账通过：原JSON所有字段一致，汇总及明细次数一致。发布快照9891次/402人，95个原部门条目合并为30个二级部门和1个未匹配项；未匹配95次均原“未归属部门”。真实私有HTTP返回31组。
- Office UI `95473b179fdbd8ba11153e320f8b9dd5c1dc4016` 已发布，后端仍为 `d5f32fcd303fed78c3da930f5ae2f27c8bdcade8`。20项单测、8项375/1440px浏览器交互、构建预算和官方资源/100项运行门禁通过。公开探针到登录边界；本地手机展开/收起及截图已验证，线上已登录展示待用户验收。
- 首次源函数保护因换行归一化差异中止，在DDL前回滚；修正为字节一致保护后复用同一迁移成功。

## 证据与恢复

- 迁移SHA-256：`af85ecec2e98582dbf5112236a1024f0be9a1ec87cea4068cdd93714740c28d5`。
- 服务器备份 `/data/ai-admin-agent/backups/operations-hierarchy/fae1e92820d6-698e7427`，包含 `previous-function.sql`、`migration125.sql`、`migration-output.log`、`report.json`。
- Office仓库证据 `outputs/2026-10-28/operations-hierarchy-{build,preflight,db-release,webui-release}.json`。无个人记录或凭据入库。
- 此迁移不重写数据；回退须同时核对函数和迁移登记，不删统计底账，不重做124一次性回填。以后维护以实际生产配置为准，10月23日旧Compose清单不可覆盖后续发布。
