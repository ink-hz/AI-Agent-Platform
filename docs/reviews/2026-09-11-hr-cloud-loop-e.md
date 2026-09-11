# HR E 发布整合评审记录

> 本批用户已授权持续推进上线准备。E2尚未执行；本文件不把工程通过、AI审读或配置存在等同于专业验收和生产验收。

## 发布基线与实际资产

D保留在 `feat/hr-cloud-loop-d` / `6a9cef6`；E开发分支为 `feat/hr-cloud-loop-e`。只读发现生产实际是 `44de9209b77343facf3117fe3ab4d3450eaf2062`，与E分叉于 `6777d22`。另建 `feat/hr-cloud-loop-e-release` 承接该生产提交。兼容旧v6/v7及旧成果读取用于在途排空和历史访问，不恢复旧文档的实施授权，也不改变最终退出HR旧执行器的方向。

生产已应用089–095；整合保留094/095原文件，SHA分别为 `e742c86cb6f0e80b20c3f90e338c11fe4c968653140455bde57032422e3f1784`、`3cbcb95271d068e297e8577431943c44a92175c79a0e30fc6463dfa047016560`，与44de920字节一致。

固定只读盘点及原始失败回执见 [inventory目录](../../artifacts/2026-09-11-hr-e/inventory/)。岗位1332、附件2且均uploading；所查旧候选及成果相关表为0，但不代表所有旧存储均已盘点。HR Relay有3条超过24小时的queued任务、均未请求取消；1个近期在线HR Worker。direct的3条completed不能当作这3条Relay已完成。新链13项表未部署。管理员补充4条固定只读COUNT得到成果投影0、情报包2、跨包岗位6874、current指针1；6874不是当前包去重岗位数。

本次没有生产数据库写入、授权变更、迁移、服务停止或部署。曾启动自动删除的隔离只读盘点容器，失败回执保留；不能表述为“未启动过任何容器”。没有导出业务正文或任务身份。3条排队任务的用途、归属及处置仍未确定，不自动取消或重放。

## 工程改动

- 固定聚合盘点区分缺表、无权、RLS受限、查询错误和零，保留原始失败，不以任意SQL接口扩大用途。
- 未初始化切换状态只兼容旧链，新云端根任务及继续均503；该问题经独立评审后修复。102提供HR专用事务切换控制，受理与切换用同一咨询锁；旧新根任务、已受理继续、幂等重放和排空分别处理。其他Bot保留原入口。切换只能由维护身份操作，模型不能调用。
- 已受理简历批次继续产生解析/研究子任务时，必须核对持久化的批次代次、材料、owner和状态，没有客户端通用绕过开关。
- 云端只读预检核对配置、角色/方法、迁移与权限。研究配置及真实个人材料许可未决仍明确返回未就绪。
- 公共100发布与旧附件Worker停用必须成组，迁移开始后的回滚禁止恢复旧擦除Worker。HR专用迁移助手临时授予迁移角色成员身份，退出时撤销并复查，不先行应用100。
- 合并生产代码曾重开同岗位历史，真实A/B范围的失败测试复现D1；修复后旧HR只用当前输入及独立核实的本轮材料，岗位标签不当作候选人隔离证明。其他Bot历史策略保留。
- 整合保留公司/专题页面、旧成果GET、方法GET及v6/v7在途兼容。关闭旧HR Worker后，历史读取仍经服务端HR授权，旧Worker写入工具不因此重开。

## 本批验证边界

最终整合日志保存在 [integration目录](../../artifacts/2026-09-11-hr-e/integration/)。新建RED/GREEN日志及被新发现中断的回归保留。最初D1测试收集错误日志被后续6项通过日志覆盖，原文件不可复核；此缺口已在merge-resolution.txt披露，后续同岗位D1另有独立RED/GREEN。日志里的pytest堆栈空格不作源码格式错误处理；源码/文档差异单独检查。

- 前端相关118项通过及TypeScript/Vite完整构建通过。该证据是组件/构建，不是浏览器实际交互；旧styles三项失败的历史披露保留，未用局部套件声称仓库全绿。
- 旧成果兼容新增2项：真实create_app和身份中间件下，未登录401、已登录未知成果404。只有成果查找结果被替换为not_found；不是生产数据读取验证。
- E开发基线的212项受理/排空/候选续作等回归，与本发布整合后的测试不是同一基线，不叠加计数。
- 最终发布代码基线 `b5ea32d`：新Loop/盘点/预检/迁移/配置/云部署/切换/读取兼容 **567 passed, 6 skipped，287.23s**；身份/对话/D1 **757 passed，13.54s**。两组各报原始数量，不合并成业务通过数。六项条件跳过是既有真实模型/显式公开bundle测试，本轮未调用真实模型。
- 有界独立审查核对D1与缺gate拒绝cloud，并各有9项和61项回归；见 `integration/independent-review.md` 的文件指纹及限制。不是整批独立人类评审。
- selfcheck **55定义/134正反例/18条件覆盖ID/6正文证据ID**通过。部署shell语法通过。完整C/D artifacts相对D基线6a9cef6为0变动，见 `historical-evidence-check.json`。

最终命令（工作目录为本worktree的backend，前端命令/文件集合见frontend原始日志）：

```bash
.venv/bin/python -m pytest tests/test_hr_agent_*.py tests/test_hr_cloud_loop_docs_selfcheck.py tests/test_config.py tests/test_cloud_deployment.py tests/test_hr_execution_cutover.py tests/test_hr_candidate_cutover_continuation.py tests/test_hr_release_read_compatibility.py -q
.venv/bin/python -m pytest tests/test_agent_use_authorization.py tests/test_r1_authorization.py tests/test_agent_brain_conversation_api.py tests/test_agent_brain_v2_conversation_api.py tests/test_agent_brain_hr_history_isolation.py tests/test_identity_crypto.py tests/test_identity_rate_limits.py -q
```

对应日志分别为 `integration/backend/release-final-verified.log` 和 `integration/backend/identity-verified.log`。首次全组4 failed/560 passed/6 skipped与身份5 failed/752 passed的日志保留：前者是真实hr_web约束与共享gate初始化后的测试夹具不符，后者是通用direct-agent测试使用未配置v6的HR入口。修复只改合法fixture/测试输入，最终组已重新全跑；没有为通过而放宽授权或吞掉异常。

## W1–W12对应状态

| 样例 | 本批作用及证据 | 尚未通过的边界 |
| --- | --- | --- |
| W1 无岗位校准 | 保留B上传/资料/成果接口；新Loop回归 | 浏览器与人类专业签收不新增结论 |
| W2 候选人隔离 | 新链每轮历史/摘要隔离；生产v6/v7兼容回归 | 旧链D1补丁尚未发布，真实候选人未验 |
| W3 跨入口成果 | 新链准确修订读取；新增旧成果关闭Worker后GET兼容 | 完整候选评估专业质量未签收 |
| W4 面试复盘 | D既有接口/来源边界保留 | 三项语义缺陷仍在；不再用角色补丁求通过 |
| W5 批量简历 | 新旧已受理批次在各自drain阶段续作，不接管重放 | 真实候选人许可及专业核对未完成 |
| W6 部分确认/并发 | 标准确认仍由用户HTTP、旧新隔离 | 人类业务审读未新增 |
| W7 准确情报 | 生产公司/专题读取保留；准确旧引用回归 | 53份语义资产未导入，旧模板质量不认证 |
| W8 撤权/删除 | 100修复及回滚防护，源码/真库测试 | 100未发布，生产擦除未验收 |
| W9 中断/取消/续作 | 持久切换锁、排空、幂等、旧新批次归属及进程回归 | 生产非零在途处置和切换演练未完成 |
| W10 出站/日志/隔离 | 装配预检、原有模型边界回归 | 生产个人材料授权回调未配置，不开放真实候选人发送 |
| W11 深读/预算续作 | 既有公开Opus5证据保留 | 默认120秒无研究成功记录；300秒/16384及计量决策未定 |
| W12 方法稳定引用 | 旧方法GET、新发布目录及精确内容身份保留 | 人类专业签收与生产发布身份验收未完成 |

## 正式切换前尚需落定

1. 3条旧HR排队任务的处置及排空证据；不由年龄推断无主。
2. 上线业务范围：D记录的三项语义缺陷未消除，人类尚未签收；真实候选人发送许可与服务端授权装配缺失。
3. 研究超时/输出/预算配置与计量误差边界。Opus5 env保持用户指定，既有试验值不是生产配置审批。
4. 100停Worker顺序、101锁表、102初始化、HR迁移与回滚的具体窗口；本机无Docker，尚无实际发布镜像构建证据。
5. 实際容器预检、浏览器关键交互与生产canary。未完成这些，不声明“已可完整上线”。
