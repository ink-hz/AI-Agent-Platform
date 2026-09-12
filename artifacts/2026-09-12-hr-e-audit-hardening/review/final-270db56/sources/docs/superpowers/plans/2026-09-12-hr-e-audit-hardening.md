# E 第二轮审计保留项处理

基线 `5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a`，既有隔离工作树与review分支。采用既有设计修复，不创建新切换边，不发布或运行生产操作。

## 共同约束

- 接口优先；本地真实身份、签名、权限、幂等、PostgreSQL与自有进程。无真实模型、生产、浏览器或消息发送。
- 上轮全部artifact及102/103/104保持原字节。新证据独立放在 `artifacts/2026-09-12-hr-e-audit-hardening/`，失败记录不覆盖。
- 每项意见必须有处理或明确延期条目。旧517及30项不合并、不冒充本轮结果。
- 最终代码与审核正文先提交并冻结，再独立审阅准确提交；审阅报告单独留存，不再改被审正文去添加其自身审阅链接。
- 当审核对象为未提交快照，留存原始字节或可校验的准确patch，不能只有临时路径和哈希。历史重建须标明重建方法，不冒充当时直接保存。

## Task 1：运维闸门与手册（子代理负责）

范围：`docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md`、`backend/tests/test_hr_cutover_runbook.py`及确有必要的独立运维测试/helper；不修改102/103/104。

- [x] 复现maintenance已持有排他锁时慢count阻塞其他Bot追加；把持锁语句限制为显著低于应用10秒阈值，显式事务避免autocommit使SET LOCAL失效。用真实双连接、实际transition与非HR repository追加验证超时回滚、epoch/回执不变及追加成功。说明数据库超时不保证网络/主机挂起的墙钟上限。
- [x] 修复§6.2失败路线死角；不增加状态机边。当前唯一承诺的恢复终点是保持draining_cloud修复，legacy恢复只能作为另行设计、验证恢复失败方案后的延期步骤，不能暗示四边状态机能随意撤销。
- [x] 把附件停旧→inspect→100→只启动修复镜像做成可执行受控步骤并纳入顺序/失败/错镜像负例；仅mock外部工具，完整说明不是真实Docker/生产验收。
- [x] 新手册mock回执绑定准确runbook SHA；记录所覆盖与未覆盖的fenced块，不复用旧SHA。
- [x] PM2 mock状态须因果依赖实际delete/restore；§6.1持久保存并校验退出前身份/配置前置，§6.2不得凭未读取的hr-before文件恢复；指出共享API重启影响或避免在现行恢复路径重启。
- [x] 运维Docker命令按现有基线加入cap-drop及no-new-privileges；30秒旧承诺同步纠正，旧operations-final-1作为历史中间运行披露。

## Task 2：遗漏的恢复与部署边界（root负责）

- [x] 先复现search recovery三项失败，再仅修完整迁移夹具；resume_search_turn创建新轮次，补draining_legacy/cloud/draining_cloud拒绝且无副作用的回归，不把用户重新搜索误当旧执行续作。
- [x] 检查/v5/recovery及recovery_work是否继续授权旧HR执行；根据真实协议语义补闸门与签名HTTP负例，保留非HR及必要停止/结果回执。
- [x] SIGQUIT加入迁移助手清理路径，用真实本地信号/数据库验证；迁移容器加入既有cap-drop/no-new-privileges限制。
- [x] preflight逐迁移诊断使用评审常量并识别镜像文件篡改，避免match与schema_ready矛盾。
- [x] 明确并处理或延期编排探针不可见的HR readiness、CandidateUnavailable吞数据库错误。不能把API启动成功当成HR可用或发布成功。

## Task 3：证据与交接（root，独立只读协作）

- [x] 核实并保存cosmetic之前两份源码、旧closure正文与临时review patch。精确重建时验证旧哈希，无法恢复就明确缺失。
- [x] 对aeff5307旧审阅对象漂移补纠正说明；不修改历史原件。
- [x] 两份根HR文档同步运维行为；新报告逐项映射所有意见与延期理由。
- [x] 精准相关回归567通过/1条件跳过、静态新增0、历史360份哈希核对完成；代码独立审核已完成。
- [ ] 本文与交接正文冻结提交后独立复审准确commit/patch及最终证据，最后只新增审阅回执与manifest（此复选框保留冻结时状态，完成结果以单独审阅回执为准，不再修改被审正文）。
