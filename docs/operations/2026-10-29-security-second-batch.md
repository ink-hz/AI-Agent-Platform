# 2026-10-29 安全整改第二批

范围：沿用已确认的整体安全架构B批，先保存已生效的对象授权修复，再逐服务降低运行权限。本记录逐项区分开发、主线整合、部署和验收；FAE应用侧修复仍只收独立验收证据。

## 基线与初查校正

平台起始主线及远端均为01368dea0766d5b3ce16e80ff511c8815fea48aa；在原生隔离工作区实施，不改用户主工作树材料。生产平台API为fb7bc97b…/image b0ac8611…，首批SDK Web为08d59d4b…/image7470cf4d…，15:12仍为准确已知版本。本批不从当前源码重建这些服务。

继续执行时主线已包含另一条已完成的会话同步发布，当前master/origin为1934533e；安全分支已fast-forward保留其全部历史。实际API已为74b9c30d…/image71330ae2…，新增云同步worker为3c3efa75…/同image，详见[同步发布记录](2026-10-29-cloud-agent-sync-rollout.md)。本批DAQ切换须以fresh全机只读清单建立非DAQ保护基线，不能把旧15:12快照当作之后线上身份。该worker从PG只读角色读取DAQ，不跟随宿主trace文件。

行政主工作区为旧特性分支55c540e，master/origin/master为caaeb474…；实际运行/opt/ai-admin-agent自10-26启动的文件与该旧主线不同。逐字核对Git blob后，routes/session/orchestrator/data_flywheel/postgres_data_flywheel五个文件均来自048d0c89a92fae1ec56623b134bedb883f8549a9（10-08的主体绑定修复），server来自含后续运维功能的095a0da。初查旧工作树时推断线上仍缺主体绑定不成立，已纠正；本轮对行政的任务为防止已有安全修复从未来主线发布中丢失，不重复发布或覆盖现代线上代码。

数采FAE当前代码2caa860465820d0c4e6c0f42c5a15eca030389a8，image sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7，容器cff5d7f4…；宿主实际业务进程UID/GID均0，NoNewPrivs=0，根文件系统可写。主线b763cca为后续文档，不能冒充线上代码。当前两个bind是专属data RW及secrets RO，尚未降权。

## 行政主体绑定主线保存

实施内容：从caaeb474建立独立安全分支，移植048d0c8的完整生产者—消费者修复：可信主体传到会话、Redis持久化、模型编排的turn记录、PG归属、反馈查询及031迁移。server仅移植必要的两行身份传递，保留主线其他实现；不引入其他分支的会议/工作台/运维新功能。

主线移植 `6f88af1cc7bb36ca445f64ef323a4f1800a6c241`，测试fixture修正 `e42f5295b9af77a674058353be699cdd4c4f5801`，收尾文档 `140e6cc`，已fast-forward归并并推送ADMIN master。五个相关生产文件、迁移及原测试与048逐字相同；server仅应用原两行身份传递。独立spec/code审查均Approved，0 Critical/Important；唯一Minor（fixture应固定001–029而非仅排除031）已修正、增量复核通过。

原master两项安全RED分别证明旧session复用和跨用户feedback实200应403。移植后不同用例 **115通过**：87接口安全、25会话/台账/编排单元、3一次性真实PG集成；fixture最后修改后只复核新增2项，2通过/0失败，3.32秒，未重复无关API组。真实HTTP、Redis JSON、跨轮上下文、普通低权限PG登录、001–029→031及重放均覆盖；模型和平台身份边界替身、Redis传输替身分别声明。生产员工身份、浏览器和真实模型业务质量未验收。

原master既有基线177通过/2失败仍保留：全写路由清单旧断言、prefixed feedback-admin旧路径预期与实际404不符。最终定向组未选它们，未改断言、未宣称全库全绿。新增集成开发中两次测试假设错误也有独立失败日志；源修复未随测试假设改变。此次**没有重新部署行政服务**，线上代码未被旧主线覆盖；将来部署仍需保存其余现代线上功能。用户原分支、未提交文档与未跟踪素材均保留。

## 数采运行权限设计边界

本批固定现有代码、上游pin、知识版本、模型和数据库/网络关系，只调整运行身份及文件访问边界。会话与内容密钥消费代码要求owner等于运行UID且mode0600，不能仅将Docker user改成非root或把root密钥变成组可读。候选使用独立UID/GID、同字节的新专属秘密目录、当前两份准确知识的只读快照、必要附件/日志写目录，保留原目录和权限用于回滚。候选启动会清理附件临时文件和过期项，因此隔离验收不得挂载生产附件。

资产/helper、双镜像配置、原生探针及只读观察器已经分别审查、fast-forward归并并推送DAQ master，当前工具提交 `bd875cace924ff72d83a92d5fe70ca2a3bf44c86`。资产helper来自 `cfc1510e5a4a9b49d870195909cd560cbf32010c`，SHA256 `3d94c1ac3b395cfb551ec31005df8cdae2b42312ba0533f8de9fef7057553eea`；只读复制原语不变。保留原主线领先远端的11个有效提交、全部失败批和原始文件，没有用旧目录覆盖主线。业务源码、prompts、依赖和知识字节不变。

真实Linux只读资产准备通过：主知识2文件/9,412,265字节、SDK15文件/1,329,624字节、四个必要秘密文件同字节复制，用时0.149秒；实际新session/content副本owner21081、mode0600，entitlements及公钥root:21081、mode0640。原目录及权限不变。此项只证明资产准备，生产RW附件/trace仍须停机后重新计量、冷复制。

现场Compose 2.40.3源有效语义检查通过，24个环境变量摘要与当前进程匹配。首次候选草案因env_file引用丢失失败；`1270caf`适配后第二次因long bind的false序列化差异失败。按设计第11节区分作者显式限制与类型化有效模型，`15be5c8`第三批0.988秒通过；双镜像集成`e0aa155`第四批1.019秒通过源/候选/回滚完整语义、环境引用及执行策略。四次均无服务操作，不把解析通过当作daemon创建成功。全部原失败保留。

原生探针首审发现2 Critical/6 Important/2 Minor；后续持久生命周期、真实内容消费者、启动维护观察及持续journal故障均经工程负例和独立复审。早期工程74项、后续30项/24项等定向组重叠，不累加声称当前全量。

### 准确镜像与制品权限

准确旧镜像的首批V01在17:41因`src.agent.session`无法导入失败：文件0644但父目录0700。六个不可变代码根共20个私有目录，原root执行掩盖了打包问题。设计第12节先复核，再实施固定旧镜像的离线权限派生；旧“只换UID、完全同镜像”的取舍被明确替代，没有加cap或移除功能。

原Docker29删除回执未被旧探针识别，原批cleanup保持BLOCKED。`9f70004`协议修复及独立审查后，仅恢复原workroot清理：全部已登记对象absent、lease released、原FAIL报告hash不变；未手删租约或重跑失败批。

离线构建前两批分别因预期OverlayFS祖先ctime、目录nlink传播被拒绝，17.592秒及13.8秒；均无候选tag，中间容器已移除，生产不变。第一份微探针未记录nlink，“nlink不变”推断已在设计中撤回；第二份完整字段探针证明仅已知chmod链的目录>1→1变化，文件硬链接检查仍严格。独立审查后，准确主线配方 `9439b21b95ded7530e2f4205bf19a53b667b52ac` 第三次构建 **PASS，13.871秒**：

- 候选镜像：`sha256:7360ead746ecef1303f27cc85b560ee13807af4a2245c8963f3eef94468586c7`。
- 原镜像/生产源/root回滚镜像仍为 `sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7`。
- 295条目、3,667,034字节前后完整内容摘要相同：`517c980be1d178f7a354e975bf69d3d313422db0da353dfcb3a75f32e870cc1d`；20权限变化、40个ctime/24个目录nlink效应逐项登记。
- 基础RootFS层完整保留加两层；Config仅USER21081及三个已登记label改变。context仅两个已审文件，无秘密、安装、依赖升级或registry pull，RUN network none。

应用runtime_release仍为`daq-062f0a7ae744`，代码身份`062f0a7ae744c10ed3aace2d39f48f22e4793d08603c4f9cc49b29afdcde4ab4`，上游revision`6bad94591dc4c40c29ad054b526c0ec526478607`。主知识release`11fc32a02a1692d44dde1a3753a0406e1b6b33c2148ea1ea192b7b211d00c5b9`，SDK release`2a43d71308f278a919edd422c65276831caf5160a4fd28725a00dbd2286b14e5`。应用、工具提交、镜像和知识身份分别登记。

### 原生验收证据

以下是隔离PG、实际Linux容器/HTTP/文件系统/crypto/parser/Loop的工程验收；identity/provider为明确的合成边界，使用真实已签知识及公钥只读副本。没有挂载生产可写数据或凭据，没有真实模型调用。

| 批次与工具提交 | 通过 | 失败 | 未执行 | 用时/清理 |
| --- | ---: | ---: | ---: | --- |
| 20261029T094125Z-5a59af / b40a6e8 / 旧镜像 | 0 | 1(V01) | 10 | 1.031秒；独立cleanup恢复后released |
| 20261029T104000Z-017f8a / e0aa155 / 派生镜像 | 0 | 1(V01) | 10 | 14.615秒；released |
| 20261029T105436Z-6ff12a / 01a1f8b | 4(V01/03/04/05) | 1(V06) | 6 | 27.398秒；released |
| 20261029T111122Z-9a65e2 / b8b248f | 8(V01/02/06/07/08/09/10/11) | 0 | 3 | 97.578秒；released |

第二批派生镜像APP仅持久ValueError，不能事后伪造首错帧；另行源码及本地最小复现确认原探针无效Origin，正例改用产品已批准的agent域名字符串，实际HTTP仍私有loopback，原负例保留。第三批V06实际安全帧确认httpx.Client在factory认证后被消费者二次enter；`b8b248f`统一十处调用方的单一contextmanager所有权，真实httpx/loopback负例证明修复，未绕过认证或替换业务能力。

**11个适用门项现均有PASS证据：8项为最新批实际通过，V03/V04/V05继承前一批。** 继承前后同kernel boot、三准确image、helper SHA、22段上下文AST均已核对不变，两次独立审查认可，不能称最新批“11全绿”。V07六格式18项归属检查、V10真实HTTP+PG跨轮/幂等、V11旧root镜像继续消费新数据并再前进，均在最新同批闭环。全部自有对象确认不存在、lease released，原生操作没有改动生产容器。

V02实际观察主进程、spawn和Tesseract均UID/GID21081、capabilities全零、NNP1。5ms采样RSS最高494,688KiB、tmp最高3,866,624字节，仅是采样下界，不称绝对峰值；/tmp仍256MiB。archive=false的归档保护分支、事务故障原子性、真实模型质量、员工SSO、浏览器、生产附件写验收均未执行。合成正常提交不能替代事务故障证明。

独立代码、整体增量及实际原生操作复核均Approved，0C/I/M。新增只读观察器`bd875ca`的36项本地真实PG17.10/SCRAM普通角色、文件IPC和子进程测试通过，8.13秒；本地UID/路径适配声明，不冒充Docker或生产验收。它逐phase保留正常DSN和search_path、绑定原源DB/关系OID/安装marker、RR READ ONLY、3秒statement/500ms lock timeout，结果只含身份及计数。OBSERVED及running=0均不单独证明服务静默。

### 生产切换状态

截至19:46只读复核，DAQ源仍为cff5d7f4…、旧image5ed10ead…、PID3095768，10-28原启动时间；**尚未停止或切换生产DAQ，V12未执行**。本轮实际范围收敛为readiness-only：准备及审查独占生命周期账本、观测和恢复材料，但当前缺少可信后台执行完成证据，标记execution_completion_unproven。停前/停后必须联合ledger、上传/解析、PID/cgroup/socket/可写FD与稳定冷态清单；无完成证明时应ABORTED_PRESTOP，保留原服务运行，不尝试停机。控制器及实际checkpoint仍在验证，不能提前写已退出或已发布；任何未知均停止推进。

源正常DSN基线于19:33只读取得：ai_fae数据库/角色、daq_fae schema/search_path、ledger OID52890/marker OID52925、安装身份绑定正确；running/live/expired均0、completed26/interrupted1。该快照不能替代停机时新观察。源writable-layer初查238条中207为pycache/pyc混合路径分类，其余目录祖先及秘密挂载点。新增材料检查原批在最后一次原始docker diff文本比较FAIL；安全帧定位该比较，focused诊断确认两份238条唯一成员相同、仅编码顺序变化。按设计第15节canonical集合合同，新v2在19:57:11实际PASS：238条，183个实际regular pyc（Python3.11 magic及header与对应源文件绑定），其余cache目录/祖先及准确秘密挂载点，无unknown或匿名卷；canonical SHA `7b05f7b890c50f892afd3c7920163031913c19742c024258f0b638cab109ecf2`。原失败保留，207和183是不同统计定义。源身份不变、零生产mutation；该在线检查不是静默证明或source rm授权，删除前仍须fresh冷态核对。

回滚A仅限候选从未可能启动且所有daemon操作闭合；候选任何start已发出或未知均按B，先确认写者退出，旧root镜像挂**当前新附件/trace**及原keys，不恢复旧整块data或共享PG。root回滚后的再次前进另建冷捕获批，不能在线chown。独立救援信息缺失不改变当前SSH。

20:06:57最终只读观察：原DAQ full CID/image/PID/StartedAt仍一致，14TID不能证明业务完成；35个既有容器在该次前后快照中的身份、配置、挂载、网络及进程字段一致。此前围栏原批因Mounts列表编码次序比较失败，原拒绝保留；固定字段诊断后，设计第15节先明确仅对目标唯一的实际Mounts按Destination排列、保留全部字段比较，新v2才通过。此结果仅覆盖该次只读观察区间，不能冒充V12或抹掉此前另一任务的发布。

本轮最终结论为 **BLOCKED_PRESTOP / execution_completion_unproven**：依据已有真实证据停止推进，原服务保持运行；没有调用新控制器、创建observer或新控制器租约，没有发出DAQ stop/rm/create/start，没有冷捕获、切换或恢复尝试。实际只读检查确认新控制器lease不存在，因此没有伪造ABORTED_PRESTOP/释放租约回执。原data及trace宿主路径继续权威生效，已准备RO副本和派生镜像保留，不能当作生产在用版本。

私有切换控制器形成57,584字节工程候选，21项本地状态/合成进程/文件测试通过，0.230秒；源码SHA `af23efa08356294da0b8ab33724186c716ade9f8bff4891f9075217c8b849fc4`。它未上传、未独立审查、未执行Linux host或任何生产stage，留在私有证据中，不纳入已批准运行工具。不能用其parent布尔收据填补可信完成接口缺口。下一项先补统一执行生命周期与排空证明的设计和完整链路验收，再另建发布批；本批DAQ降权上线、V12及真实员工/模型/浏览器均未完成。

另以现有DAQ进程内的正常DSN进行一条限时、只读角色元数据查询，确认实际连接 `ai_fae` 数据库的 `ai_fae` 角色为superuser，且有create database/create role。没有读取业务行或将口令带出容器。此事实证明DB-01尚未满足，需独立最小数据库角色迁移与消费者回归；本次非root配置不修改DSN/共享PG，也不能称数据库隔离完成。

## 运维和范围

- 独立救援通道及最终受控SSH入口信息尚未齐备，不修改SSH、账号、密钥、安全组或防火墙。
- 首批IP证书自然任务14:28:44成功exit0；15:00证书健康及15:01备份健康自然调度均success/exit0，没有强制续签或重复备份恢复。
- 不发送外部告警或业务消息，不调用真实模型，不执行生产业务故障注入，不改共享Nginx、不测试独立Nginx配置。
- 全部服务降权、网络分区、外部通知送达、完整业务DR和员工浏览器仍需分别验收；单项完成不覆盖这些范围。
