# 2026-10-30 DAQ 安全权限发布

用户明确授权上线，并要求保留其他仓库和会话的工作。本批仅发布DAQ运行身份和文件权限边界。**2026-10-30 10:18（北京时间）已完成生产切换和只读范围验收，发布锁已释放。** 首次自动验收因检查路径错误失败，原失败保留；经独立复核及单独只读补验确认当前实例符合要求，没有重放发布或回滚健康实例。

## 版本与保全

平台主线保留另一会话的questioner身份、界面状态及发布记录；DAQ的497303b只有测试/文档，不改变实际应用字节。发布动作工具4665d52与该主线合并为c5fbbf8并推送，没有覆盖其他工作区。ADMIN仍保留用户feature分支及未提交材料，本批不重发ADMIN、FAE或平台API。

准确生产源为cff5d7f4…、原镜像5ed10ead…、PID3095768、2026-10-28原启动时间。候选7360ead7…仅派生不可变代码权限及USER/标签，业务、依赖、模型和知识字节不变；沿用第二批11项适用原生证据（最新8项、未变继承3项）。最终主线、工具提交、镜像和容器身份另记，不能彼此代替。

## 安静维护窗口的证据与边界

使用固定PyPI py-spy0.4.2制品，先以自有子进程验证JSON/nonblocking及不输出locals，再读取准确源PID的函数、不可变代码路径和行号。实际三个Python线程为AnyIO queue.get等待、附件GC Event.wait、Uvicorn主循环；此前未知的晚建线程已识别为空闲worker，没有观测到pump或解析执行帧。

正常DSN只读事务确认准确DB/schema/OID/marker，running/live/expired为0，completed26、interrupted1。实际trace为50729字节、27条唯一认证root，全部有终态；末条09:39:00.185641在ledger最后09:39:00.184486之后。还须fresh socket、子进程、附件临时项、外部写者及DB-trace关联检查。独立复核允许对这个具体安静状态做权限维护发布，统一生命周期端点不是硬前置。

现有断连后daemon pump未join仍是通用生命周期缺口。观察到实际活动就不满足安静门；停后出现新running/interrupted或未解释trace缺口则验收失败。观察到SIGTERM之间的新断连竞态没有被本批证明消除，不称通用无竞态排空。只使用无限grace的正常SIGTERM，不发SIGKILL，不扩大为Agent行为修复。

首个在线准备在socket门被拒绝，未发源操作。固定诊断定位到身份投影客户端保留的CLOSE_WAIT，而非请求执行线程：目的地与准确身份代理容器、源配置地址一致。只读pidfd_getfd复制准确inode的描述符，以MSG_PEEK|MSG_DONTWAIT得到EOF0；不消费数据、不改原FD flags。RX队列中的1是FIN序列，不能当正文积压；TX为0。后续仅允许这个已绑定身份目的地、队列边界和EOF条件的退役连接，其他外连仍拒绝，原失败保留。

后续PG元数据路径首次因JSON OID类型被拒绝，原失败保留。诊断确认准确DB/角色、关系和安装标记均匹配，但PostgreSQL将oid编码成JSON字符串。生产者固定显式转bigint，继续以严格数字校验原52890/52925；不放宽到任意类型转换。该路径通过准确现有PG容器、原ai_fae角色的Unix socket，以完全限定的daq_fae关系、只读事务及OID/marker绑定观察，与原正常DSN基线交叉核对；不冒称保留了DSN的SSL/search_path连接协议，也不修改业务数据或角色。

## 控制面验证与失败保留

旧57KB控制器审查C0/I3，未使用。新security_action.py仅执行父控制器审过的字面argv，耐久记录intent、may-have-occurred、claim及单次真实CLI终态ACK；父控制器负责全局锁、A/B和物理材料证明。22项本地工程测试通过，独立C0/I0/M0；Linux-root/Docker证据另计。

首次隔离fixture的create/start/rm三动作均ACK0，但末尾缺席检查因通用docker inspect的错误文本差异FAIL。原失败保留；固定诊断后统一使用显式docker container inspect，仅只读收尾原对象，不重放三动作刷绿。原fixture已确认缺席。

首批根FD探针的PID1没有安装SIGTERM handler，等待自然退出超过提交等待，原失败保留。实际stop最终ACK0；一次清理输入因0644被工具在操作分配前拒绝，保留该输入，新私有0600清理动作确认原fixture已移除。第二批先确认安装与Uvicorn一致的SIGTERM handler，实际验证：准确原镜像的根FD在容器退出后仍可读，固定代码摘要一致，FD在rm前关闭，四动作ACK0，fixture缺席。没有生产源操作。

## 发布顺序与回滚

1. 持有现有DAQ .deploy.lock，保存fresh实际版本、原完整恢复模型/私有env身份；准确Compose2.40比较原、候选和root回滚语义，owner标签是单独核对的增量。
2. fresh安静证明通过后单次stop --time -1准确源CID，保持源根FD用于停后层材料审计。确认正常退出、无写者、DB无新增中断后，冷捕获到全新RW目标并render/verify。预启动前失败仅在相关动作闭合后按A恢复，不在停机窗口调参。
3. 材料证明后仅rm准确已停源，不-f/-v；create唯一服务且no-build/pull never。创建态核对配置IPAM地址，运行态核对实际IP。紧接启动前再次helper verify，记录may-start后启动。
4. 候选任何may-start之后均为B：先闭合并停止准确写者，原root镜像5ed挂CURRENTNEW附件/trace和原root密钥，不还原PG或旧整块data。
5. 只读核对实际UID/caps/NNP/rootRO/五leaf、知识身份、完整静态字节、未授权拒绝和错误日志；其他服务以fresh版本为保护基线。真实员工SSO、浏览器、自然trace新增、通用生命周期及数据库最小角色分别登记。

停后层审计按预先固定的可丢弃缓存合同逐项核对：仅六个不可变代码根和Python标准库中的正规、单链接、root拥有的Python3.11 bytecode，magic及header须绑定对应源文件；相关目录须为缓存目录/祖先或原秘密bind挂载点。保留停前/停后摘要，关闭时新增合法缓存不等于业务材料变化；任何其他文件、类型、链接、删除或未解释成员都阻断rm。原数据、秘密和已发布知识不在此可丢弃范围。

## 实际发布结果

| 项目 | 实际值 |
| --- | --- |
| 当前容器 | `9557de1b646fd73073fbd14acbfd56ba30dbf6ad078a841149629cf580d2fbed` |
| 当前镜像 | `sha256:7360ead746ecef1303f27cc85b560ee13807af4a2245c8963f3eef94468586c7` |
| 应用代码 / runtime release | `2caa860465820d0c4e6c0f42c5a15eca030389a8` / `daq-062f0a7ae744`，业务代码字节未变 |
| 权限派生recipe | `9439b21b95ded7530e2f4205bf19a53b667b52ac` |
| 当前启动时间 / 宿主PID | `2026-10-30T02:10:39.542517528Z` / `768002` |
| 实际运行权限 | UID/GID/唯一附加组21081，CapEff/CapPrm/CapBnd为0，NNP=1，根文件系统RO |
| 文件边界 | 附件目录和trace文件两个RW leaf；知识两leaf和秘密leaf为RO；`/tmp`为256MiB、0700、noexec/nosuid/nodev tmpfs |
| 网络 | 原两网络，edge `172.31.0.9`，主机仅 `127.0.0.1:8081`；未改Nginx |

原实例单次优雅SIGTERM，10:10:33.742正常退出0、未OOM，无旧PID生命周期或递归cgroup成员。停后DB/trace、附件和外部写者门通过；冷复制附件0个、trace50729字节，SHA256 `3bc2d5a2f14e3c36f4257a99d4ac5a59b700beb4b2019beac363354fd291c478`。冷态材料/render及启动前verify通过后，仅移除准确已停实例，不-f/-v。stop、rm、create、start各一次，四个耐久ACK均0；候选重启数0，未恢复PG或旧业务数据。

首次apply健康恢复后检查了错误根路径。实际固定代码在`/daq/`提供页面、`/daq/assets/`提供资源，并向index的head注入base和两个meta；原始文件hash不能直接替代HTTP页面hash。该失败属于验收器的URL和表示契约错误，旧 `release-failed.json`、`rollback-b-required.json` 原样保留。B记录表示若回滚须使用CURRENTNEW数据，并非要求无条件停止健康实例。

独立复核后，一次只读补验通过：4份原始静态文件完整集合和字节一致；4资源经loopback和公网TLS共8次完整内容比对通过（含按固定源码转换的HTML，拒绝重定向及非预期编码）；本机/公网三项匿名接口共6次401；公网两项health遮蔽404；健康身份、成功采集的日志、实际安全边界及其他34个容器完整身份/配置/挂载/网络/进程/重启字段一致。记为 **V12 PASS（只读范围）**，不称首次apply成功。补验脚本SHA256 `739b5af11277e8c641e2ba0c6041c74144b102a73254cf842ede8266fe2f0bb4`；最新独立审查C0/I0/M0。

收尾平台主线基线 `c1dd4a73`、DAQ主线 `5f262708`，保留其他会话的身份投影、界面状态和发布历史。实际平台API仍为 `c6ee87f25e4562ee9ce84a4508491a3a089dbd4e` / `sha256:baf2b448feecf41e1e437151c28296f3dc4ee4a443abac31c6c31824a0f2d1bf`；同步worker仍为 `c13433f4580a54629626daf9017b642d44bed009` / `sha256:048e90d5bba6303fcb68f08f1ba4f69796295a171f1066c51be0d6af5acc687a`。镜像以本批fresh fleet为准，版本取实际 `PLATFORM_RELEASE_SHA`，不使用旧发布文档中的15ad镜像或继承的OCI基础层label推定当前身份。本次未重发它们、ADMIN或FAE，未清理其他工作树或未提交材料。

## 当前活动材料与运维入口

以下路径正在生产使用，名称含security-runs不表示可以清理：

| 用途 | 实际宿主路径 |
| --- | --- |
| 发布回执与活动Compose根（R） | `/opt/ai-daq-fae-agent/security-runs/release-20261030T012018Z-03c3d2` |
| 当前权威附件 | R下 `captured-rw/attachments` |
| 当前权威trace | R下 `captured-rw/trace/daq_traces.jsonl` |
| RO材料根（P） | `/opt/ai-daq-fae-agent/security-runs/20261029T084415Z-d1354e/prepared-ro` |
| 主知识 / SDK知识 / 秘密 | P下 `main` / `sdk` / `secrets`，密钥字节相同，所有权适配新运行身份 |
| 活动Compose文件 | R下 `rendered/runtime.json` 加 `candidate-owner.json`；项目 `ai-daq-fae-agent` |

完整project-directory、插值文件、env引用、实际mount及回滚模型见R下 `active-runtime-binding.json`。原 `/opt/ai-daq-fae-agent/data` 和 `/etc/ai-daq-fae-agent/secrets` 保留，原权限未改；旧data不再是活动附件/trace来源。后续发布、备份、知识/授权更新和密钥轮转须先读活动绑定，不能继续将旧data视为唯一生产数据，不能删除R/P。实际回滚先证明当前写者闭合，使用原root镜像5ed、原root密钥和当前新附件/trace，禁止恢复旧快照或PG。

最终状态由R下 `release-final-status.json`、`release-closeout.json` 记录；补验详情为 `production-acceptance-reconciled-v1.json`。10:18:34确认发布锁释放、持锁unit inactive，原锁文件身份保留。46份白名单元数据回执共73645字节下载并校验，未下载sealed env或真实生产密钥。

收尾另核验两份平台运行身份元数据（1618字节），实际版本取 `PLATFORM_RELEASE_SHA` 而非基础镜像继承label。最终独立操作/文档审查C0/I0/M0。私有证据123文件、3564031字节已复制并逐文件校验，归档位于本机 `/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/security-runs/20261030-daq-release-021834Z`；另存最终文档、主线整合回执和总manifest，原10-29归档保留。

## 验证范围与剩余项

本地runner工程22通过、operator修正工程19通过，为独立定向组，不累加为业务质量数量。继承原生11门（最新8项加同镜像/工具合同未变的3项），本次未重跑。生产首次apply验收1失败，明确修正验证契约后的独立只读批次1通过；早期fixture及准备失败保留。

本次真实模型调用0、生产业务测试写入0。原实例正常退出至健康接口首次恢复约18秒。研发耗时和失败记录保留，研发tokens未精确计量，不声称为0。真实员工SSO、浏览器交互、自然业务trace新增及完整模型业务任务验收均未执行。通用断连pump/drain未修复；DB-01角色仍为superuser/createdb/createrole，需独立迁移。FAE仍只保留原验收项。上述事项不因容器降权而标记完成。
