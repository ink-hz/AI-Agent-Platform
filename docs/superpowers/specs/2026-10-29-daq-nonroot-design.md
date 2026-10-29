# DAQ 单服务非 root 与文件边界设计报告

日期：2026-10-29。状态：设计及后续架构复核已实施；离线派生制品、工具和11个适用原生门已有证据（最新8项、继承3项），生产切换及V12尚未执行。最新版本、失败与验收范围见[第二批执行记录](../../operations/2026-10-29-security-second-batch.md)。前文方案A同镜像取舍由第12节明确替代，历史检查结果不代表当前阶段。

## 1. 范围、授权和证据基线

本报告落实平台 `docs/superpowers/specs/2026-10-23-platform-security-architecture-design.md:163` HOST-01 与 `:166` KEY-01 的已认可方向。只调整 DAQ 运行 UID/GID、根文件系统、capabilities、特权提升及文件挂载边界。不得改变上游 pin、DAQ 模型/提示/Loop、知识字节及角色、DB、网络、SSH、Nginx、平台或 FAE；不得靠失去附件/OCR/trace 能力换取启动成功。用户已要求继续实施既有安全架构，本设计细化该授权内的单服务降权。生产切换仍由父控制器在以下全部门项通过后单独执行，不表示已部署。

本代理只读取本地源码/文档并写本报告，没有连接生产、修改代码/Git/配置、调用模型、发送业务消息或写业务数据。已读 DAQ AGENTS、完整设计相关运行/发布章节、当前工作流、相邻 FAE AGENTS/工程原则/设计索引与工作流相关职责。旧完整设计中的独立 DB 目标不覆盖已知当前共享 PostgreSQL 的现实，本批不迁数据库。

本地 DAQ 工作树：master `b763cca50d57c680dc7f1e1c56afe845dc6d298b`，干净；本地 origin/master 引用 `9331d7d6d1e0dbfb6af65de9c52fa25c5cab5e9e`，ahead 11。未 fetch，不冒称已在线核对远端；此状态不等于生产版本。源码证据全部使用本地可读的生产提交 `git show 2caa860:路径`，下列文件行号均属于该提交。该提交到本地 HEAD 的 daq_fae/src/deploy 路径无差异。

父控制器已确认的生产事实（本报告不重复盘点）：

- 代码 `2caa860465820d0c4e6c0f42c5a15eca030389a8`；镜像 `sha256:5ed10ead83ee10ad5ef668fad8ee6aa7d28e37bb8d217fd22daa84d6d5db5bf7`；容器 `cff5d7f4fbc70c088ddd2c81c6997fe546b111e34d6407782a794278d110416f`。
- Compose `/opt/ai-daq-fae-agent/releases/2caa860465820d0c4e6c0f42c5a15eca030389a8/deploy/compose.prod.yml`；实际进程 3095768，UID/GID 全 0，CapEff `a80425fb`，NoNewPrivs 0，根文件系统可写；无 cap_drop/nnp。
- 仅 data 整目录 RW 与 secrets RO 两个 bind。回环 `127.0.0.1:8081`，网络 `deploy_default`、`orbbec-agent-platform-edge`，地址 `172.31.0.9`。
- attachments 与知识根 root:root 0700；两个 active.json root 0600；原秘密目录 root 0700，session/content/entitlements root 0600。
- 正在使用的两知识根：`/app/data/knowledge/reviewed-thinking-prefix-20261028`、`/app/data/knowledge/sdk-thinking-prefix-20261028`；审批公钥文件名 `feedback-approval-keys-20261023.json`，容器绝对路径沿用当前配置的准确值。
- UID/GID 21081 此前未被宿主 NSS、运行进程或其他容器 numeric User 使用；其他容器与 DAQ data/secrets 无挂载交叠。切换前复查碰撞，不必新增宿主登录/SSH 账号。

## 2. 最小源码确认：写入与只读职责

| 路径/职责 | 源码证据 | 设计含义 |
| --- | --- | --- |
| 实际附件配置 | `daq_fae/app.py:288`：DAQ_ATTACHMENT_STORAGE_DIR；缺省 `/app/data/daq_attachments`。`deploy/provision_env.py:95` 生成生产值 `/app/data/attachments` | 区分代码缺省与实际生产配置。最终实施按现有 env 的准确 `/app/data/attachments`，不得误迁到缺省目录 |
| 启动附件变更 | `src/attachments/store.py:61` mkdir，`:62` chmod 0700，`:63` 清理；`:487` 删除 .partial-* / .batch-*；`daq_fae/api_attachments.py:17` 构造后立即 gc_expired；`:35` 启动 worker；`store.py:549` worker 首轮立即 GC | 附件根必须 UID21081 自有；仅给组可写仍会 chmod 失败。create_app/import 已有副作用，不能用“仅启动测试”挂生产附件 |
| 后续附件变更 | `store.py:374` 遍历并删除过期，某些归档保护分支还调用 lifecycle；`:442` mkstemp/fchmod 0600/fsync/replace；`:460` 原子复制。`service.py:66` 在附件根创建 .batch-* | 候选隔离附件和 DB；不能只隔离文件却指向生产归档 lifecycle。最终迁移需停止 DAQ 并静默租约/上传，不能运行中复制后声称一致 |
| multipart 与转换 | `src/api/attachment_routes.py:41` 真实 UploadFile HTTP 路径；`parsers.py:118` spawn 子进程；`:185` Pillow 图片归一；`:202` pytesseract OCR；`:237` PyMuPDF；后续 docx/xlsx 解析 | 除附件 RW 需有隔离临时目录，覆盖 multipart spool 与 pytesseract 临时文件；观察 Python worker 和 tesseract 实际 UID/caps。OCR 异常会变为 warning（`:217`），不能以图片上传成功代替 OCR 保留 |
| 主知识加载 | `app.py:171` 配置根；`:203` ReviewedKnowledge.load_active；`knowledge/reviewed_view.py:328` 读取/验证 active、运行身份、签名与内存 manifest | 启动读取已发布快照，不需要知识根 RW，不需要相机 Chroma 索引。生产不启用 allow_local_dev |
| SDK 加载 | `app.py:217` load_active_sdk_release；`knowledge/sdk_release.py:352` active.read_text + _read + runtime/upstream/code/main 知识绑定 + 内存 SdkCatalog | SDK 不写 SQLite/缓存；代码散列及 SDK 绑定不得为了非 root 被重签或绕过 |
| 代码身份 | `knowledge/code_identity.py:9` 读取 daq_fae 的 .py 及三个 prompts 的字节并散列 | 推荐复用准确镜像，部署配置变动不等于改 DAQ 源码身份。不得重建浮动依赖镜像后仍称原 digest |
| SQLite 范围 | `app.py:283` auth_mode 为真时 local_state=None；仅 Dev 使用 DAQ_DEV_STATE_DB SQLite | 正式认证模式不需要 data 父目录、SQLite 或 Chroma RW；`src/api/server.py` 和相机 Orchestrator 的其他写路径不是本 DAQ 启动入口 |
| 生产持久化 | `app.py:826` 装配 authenticated persistence/durable state；`authenticated_persistence.py:360` PostgreSQL repositories；`durable_state.py:414` 配置持久状态 | DB 权限、schema、租约、事务及恢复职责不迁入文件系统；不停止/回滚共享 PG |
| trace | `app.py:279` DAQ_TRACE_LOG_PATH 缺省 `/app/data/daq_traces.jsonl`；`src/agent/tracing.py:121` 每次 emit 重新打开 append；`:137` 创建父目录；`:140` IOError 仅 warning；`:474` JSONL sink 必建 | 启动/health 成功不能证明 trace 写成功。需 synthetic span 真落盘、重启可读及旧文件连续性验收。禁止无声改到容器临时层 |
| session/content keys | `src/platform_identity/service.py:148`、`:160`，`src/storage/authenticated_conversations.py:109`、`:121`：正规文件、owner==runtimeUID、精确 0600 | root:组 0640、ACL 可读不能通过严格 loader；必须新副本归属21081、0600，不放宽代码校验 |
| entitlements | `knowledge/entitlements.py:21` O_NOFOLLOW，owner 可为 0 或 euid，不可 group/other write；`:48` 每请求重读 | 新副本 root:21081 0640 可满足；保留请求时角色检查。不需要进程写权限 |
| 审批公钥 | `knowledge/approval_verifier.py:71` 校验绝对路径/各级无 symlink、正规文件、精确字节 SHA256 | 精确复制并保持配置 pin；不要改 JSON 序列化，不用 symlink 指针替代实际文件 |
| 入口与认证 | `platform_identity.py:135` 严格 key loader，`:103` DB 标记/约束只读校验；`:153` 保护入口鉴权；`app.py:884` 挂载静态 UI | startup 要在认证模式测试，离线无认证启动不能替代；静态字节应可读。未授权 API 拒绝单独验收 |

对受审的 DAQ 装配链没有发现知识缓存/SQLite 的生产写入要求；第三方库、Docker 挂载目标创建及子进程临时需求仍以隔离真实进程测试为准。此结论不等于全仓所有离线知识发布脚本都只读；它们不应在业务容器运行。

## 3. 三个方案及推荐

**A（推荐）：复用准确镜像，UID/GID 21081 + 叶子挂载 + 版本化受限副本。** 保留原 data/secrets 完整不动；仅复制当前两个知识根及公钥、同字节必要 keys 到新版本目录，复制附件与 trace 到新运行数据目录。新容器没有整个 data bind。优势：差异集中、原始材料不被 chown、回滚可指向旧镜像同时继续新数据。成本：复制空间和一次停服一致性窗口；需完整 hash/权限清单、明确发布者与旧 trace 路径。

**B：原目录原位改属主/ACL，只换 UID。** 复制少、切换简单，但 session/content 严格 owner/0600 迫使实际改属主；知识混有历史候选及 501:50 文件，递归 chown 会改用户材料；data 整树暴露也违背最小权限。ACL 不能替代 key 校验，回滚还需要逆向改权限，故本批不采用。

**C：重做镜像封装所有 RO 资产并迁 named volumes/secret delivery。** 可建立长期更强的构建/发布合同，但本次会引入新镜像依赖、知识交付方式与 volume 迁移，扩大版本/恢复验证范围；不能保证既有代码/知识身份不漂移。作为后续独立设计，不用于当前单服务收口，也不以此阻塞 A。

## 4. A 的目标配置与文件合同

容器固定 `user: "21081:21081"`、`read_only: true`、`cap_drop: [ALL]`、`security_opt: [no-new-privileges:true]`。不加 supplementary groups、不 privileged、不挂 docker.sock，不修改现有端口、网络、地址、重启策略或健康探针。实际 UID/GID/CapEff/CapPrm/CapBnd/NoNewPrivs 需用 /proc 验证，不能只读 Config.User。

固定使用上述现有镜像 digest；Compose 不执行 build/pull 漂移版本。镜像 Dockerfile 的 `/app/data`、`/app/data/knowledge` 等父目录本来存在（`deploy/Dockerfile.prod:28`），候选确认它们为 root 持有且服务可遍历，不再覆以原宿主 root0700 data bind。若叶子目标不存在导致运行时失败，先在隔离候选解决明确 mountpoint 方案，不通过把整块 data 重新挂回、赋予 broad RW 或现场改业务源码绕过。

建议宿主新资源分开落盘，例如只用新的唯一批次目录，不覆盖已有路径：

- RO 资产：`/opt/ai-daq-fae-agent/security-assets/nonroot-20261029-<唯一批次>/`，含两个准确知识根和公钥。宿主发布者 root 拥有；对服务组21081赋目录0750、普通文件0640（根据原文件类型逐项建立清单），容器对应 leaf 均 RO。拒绝符号链接、特殊文件与逃逸路径；硬链接亦不得链接回旧文件。不得整棵 knowledge 复制或递归 chown 原树。
- secrets：`/etc/ai-daq-fae-agent/secrets-nonroot-20261029-<唯一批次>/`，根目录 root:21081 0750；session/content 文件 21081:21081 0600；entitlements root:21081 0640；新目录 RO bind 到 `/run/daq-secrets`，保留原内部文件名。只纳入实际 env 引用的必要文件，不把发布/SSH/维护密钥一并复制。原 secrets 及其字节/owner/mode 不变。
- RW 附件：`/opt/ai-daq-fae-agent/runtime-data/nonroot-20261029-<唯一批次>/attachments`，21081:21081 0700，文件0600；bind 到现有 `/app/data/attachments`。迁移所有现存附件的 manifest/chunks/original/normalized 内容及 ID，不能只建空目录。
- RW trace：同一 runtime-data 批次的 `trace/daq_traces.jsonl`，预创建普通文件 21081:21081 0600；优先**单文件 bind 到原 `/app/data/daq_traces.jsonl`**，保持 env 与容器路径不变。完整复制旧历史再续写，保存迁移时 size/hash 与边界偏移。宿主 trace 读取者如存在，必须显式登记新宿主路径，不能继续追旧文件误判停止记录。该单文件 bind 不支持宿主用 rename 替换 inode 的轮转；本批不新增轮转。若发现既有轮转依赖，停止选择单文件方案，改受限目录 bind + DAQ_TRACE_LOG_PATH 并同时审查所有消费者，不静默改路径。
- `/tmp`：独立 tmpfs，uid/gid21081、mode0700、nosuid,nodev,noexec；`TMPDIR=/tmp`，`PYTHONDONTWRITEBYTECODE=1`。大小先按现有上传/并发上限与真实 synthetic 峰值决定并记录，不能无界或盲目增额。建议起测256MiB，仅为验证候选值；若不足，先定位 multipart/OCR峰值，再给依据。保持既有 /dev/shm 限额，是否需要额外调整由 spawn/OCR测试给证据。

keys 复制只在 root 的私有操作中进行，umask077，拒绝 symlink/非正规文件，独占创建目标、核对 inode/link 类型/owner/mode 后发布；禁止 cat/打印值、shell xtrace、将值写命令参数或普通日志。源/目标 SHA256 比较在私有进程内完成，报告只输出 matched=true、文件数量、权限与版本标识，散列清单也按0600保存。无需轮换 key，保留所有 key versions 和 active version；同时按 `_load_content_codec` 验证 session 与 content 材料仍独立（`authenticated_persistence.py:340`）。

## 5. 有限实施顺序与不可越过的门

1. 本地形成可审查的新 Compose/清单及回滚 Compose，不修改原 release 目录。复核完整 effective config 无整块 data 挂载、无 build/pull、无新网络/端口/secret 暴露。配置评审必须脱敏，禁止完整 env 输出到普通日志。
2. 版本化复制 RO 资产/必要 secrets，记录逐文件字节、数量、大小、类型；原树前后 hash/metadata 一致。只读资产不需要暂停业务，但复制后再核对 active 和两根版本未变；漂移即停止重新取一致快照，不拼接版本。
3. 在隔离容器/自有测试进程完成 §6；候选使用独立附件/trace、synthetic keys、独立 PG 与受控身份测试端口，禁止生产 DB/模型/平台身份出站。准确生产知识可同字节只读装载，保持默认真实签名校验；不得用 verifier=True 或 allow_local_dev 冒充生产启动。必要测试注入只允许 provider/identity 外部边界，单独标记。
4. 父控制器确认单 DAQ 切换已授权后，复查镜像/当前容器/UID碰撞/知识版本/挂载交叠；保存准确旧配置与无需回滚 PG 的回滚方案。准备资源可在线完成，但最终附件与 trace 同步必须在 DAQ 已停止且上传、模型 worker、持久请求租约均静默后执行。不能通过清数据库租约制造静默。
5. SourceStop之前先取得可信执行完成证据，并联合新鲜ledger/上传/解析/PID/cgroup/socket/FD观测；缺失则明确execution_completion_unproven，ABORTED_PRESTOP并保留原服务运行，不发stop。前提全部成立才记录最后写入边界、准确优雅停止仅DAQ；确认终态、无遗留写者及冷态稳定后复制附件和trace到新目标。若stop后静默或任何daemon结果未知，保持blocked，不自动重启或开第二写者；恢复仅按第7节及已审切换矩阵的A/B条件：A要求候选从未may-start、相关intent闭合且无未知写者；B先闭合候选生命周期和写者，再用原root镜像挂CURRENTNEW数据。逐文件核对源目标内容，不以初查附件为空豁免；权限变更仅针对新副本。
6. 启动最终非 root DAQ；保留 DB、模型、网络、角色和知识 identity 不变。执行允许的只读生产健康/实际 UID/mount/日志/静态字节检查，不发模型题、不造生产附件或会话。观察正常业务产生的 trace 新增与权限错误，若无业务产生则标为生产 trace 写入未验收，不冒称已覆盖。
7. 完成记录包含原镜像与新容器身份、两知识/代码身份、文件hash匹配结果、准确配置diff、测试分类/数量、未验收项和回滚挂载清单。实施者同步实际运行/部署文档；Agent 行为未改，不虚构完整问答质量已重新验收。

## 6. 最小验收矩阵（设计初稿时未执行；当前逐项结果见执行记录）

每项首错即停，保留候选版本、输入、mount、错误类别与时间；修复后仅重跑受影响项。以下12项是门项，不等于预先声称12个 pytest 用例。测试记录实际断言/用例数，分别记录耗时、RSS/临时空间峰值；真实模型调用数及 input/output tokens 应为0。

| ID | 验证 | 明确通过标准 |
| --- | --- | --- |
| V01 | 准确镜像与生产认证装配启动 | 原镜像digest、源码/上游/知识身份匹配；默认签名装载成功；实际独立PG标记/约束满足；没有创建本地SQLite；启动无 EACCES/EROFS 或重启循环 |
| V02 | 主进程及转换子进程身份 | 实际 UID/GID 全21081；CapEff/Prm/Bnd=0、NoNewPrivs=1；捕获 spawn worker 与 tesseract 同样身份。不能以新 exec 的 id 代替主/子进程证据 |
| V03 | 根FS与RO否定测试 | 在隔离候选内对代码、prompts、两知识root/active、公钥、secrets 写入/删除/rename 均失败；根FS readonly；从进程视角没有历史候选/其他业务秘密。只在隔离副本做写拒绝探针 |
| V04 | RW正例及临时空间 | attachments 真正创建/rename/fsync/chmod成功；文件0600、根0700；/tmp 临时创建成功且根FS其他未声明路径写失败；重启后持久附件/trace仍在，tmp不承载持久数据 |
| V05 | 严格key/entitlements合同 | synthetic 同字节旧root key→新21081 key后摘要/解密结果一致；root-owned0600和21081-owned0640的session/content负例被拒绝；entitlements root:21081 0640接受，group-write负例拒绝；真实新secret仅受限loader/匹配结果核对，不输出内容 |
| V06 | 真HTTP认证边界 | /health字段和完整静态字节hash准确；未登录 /history、/authenticated/conversations、/attachments 拒绝；测试身份按现有SSO客户端边界签发/校验，cookie/CSRF/role合同保留；禁止直接造DB成功状态 |
| V07 | 真multipart转换 | synthetic TXT/CSV、PDF、DOCX、XLSX及大于spool阈值图片各过真实上传；保存原文hash、manifest/chunks、图片normalized结果；清晰OCR文本取得预期chunk，无新增ocr_unavailable。禁止只调parse_direct绕过HTTP/spawn |
| V08 | 附件所有权与生命周期 | A上传/查询成功，B读/删拒绝；未过期/过期/受归档保护synthetic附件覆盖启动GC；只在测试数据演练.partial/.batch清理；非空旧附件迁移后ID、owner、字节、读取和删除合同保持 |
| V09 | Trace真实落盘 | synthetic recorder root/span写真实JsonlTraceSink，核对trace_id和schema、文件owner/mode、旧历史前缀字节一致；重启再次追加；故意只读trace负例暴露warning且门判失败。健康status不是判据 |
| V10 | 认证持久化与跨轮 | 隔离真实PG+synthetic外部provider完成最小认证任务，附件绑定、checkpoint/terminal原子持久化、同幂等键重放，重启后历史解密/上下文与下一轮附件读取连续；模型质量不在此门声称通过 |
| V11 | 数据保全回滚演练 | 非空附件/历史trace启动新UID，写新synthetic附件/trace，然后旧root准确镜像回滚继续挂新数据；新旧附件和trace可见、PG不还原。记录回滚root新建文件属主及再前进的二次迁移要求 |
| V12 | 最终生产只读观察 | 最终实际容器UID/caps/nnp/挂载/网络/知识身份符合配置，健康中archive ready非假阳性，静态内容逐文件hash匹配，日志无权限/OCR/trace错误；非DAQ容器观察字段未变。无生产模型/写测试 |

真实模型质量、真实平台SSO、浏览器交互、生产附件上传/跨轮写入均未在本报告验收；V10 是合成提供方工程验证，V06 测试身份不等于真实平台登录。浏览器验收由用户承担。既有业务质量证据可复用但须保留原版本，不称本批重新验证。没有既有真实进程环境时可先工程验证，但不能把 V01/V02/V07/V11 标为通过。

## 7. 停止条件与无覆盖回滚

遇到以下任一项停止切换：UID冲突、实际版本漂移、知识/secret字节不同或签名/绑定失败、发现未声明必要写路径、OCR/转换退化、key loader不接受、安全位未生效、旧数据无法一致复制、请求/上传不静默、trace不能落盘或读取者失联、其它服务发生变化。先保存失败；不能恢复root长期运行后把本项标“加固完成”，不能靠加capability、给data整树RW、放宽key权限、停用OCR/trace或换模型消除报错。

回滚配置在切换前生成并隔离演练：

1. 停止仅新DAQ、确认所有写者退出，保存失败容器日志/metadata与新数据清单；不删除新数据、不将旧目录同步覆盖新目录。
2. 使用准确旧镜像和旧root身份、旧运行能力基线（包括root访问21081-owned0700目录及chmod所需能力）；不能保留cap_drop ALL后假设root自动绕过DAC。回滚是临时恢复原安全基线，不宣称仍满足非root。
3. **attachments 和 trace 始终挂当前新runtime-data**，继续原容器内部路径；RO知识资产可继续同字节新资产，session/content/entitlements挂回原root-owned秘密目录。绝不直接恢复旧整块data bind，否则切换后新增附件不可见。
4. 保持共享PG当前数据，不降schema、不恢复备份、不清租约。启动原代码后验证已有及切换后附件/历史能访问，trace继续append；真实业务写验证仅在另获明确授权时执行。
5. root回滚运行后可能写入root0600的新附件/trace，不允许随后直接再切21081。再前进要另一次静默窗口，复制当前权威数据至新的唯一批次目录并修正新副本owner、校验全部字节，原目录保留；不能在线递归chown。
6. 附件在业务授权TTL/删除路径正常消失属于当前生命周期；回滚不得从旧快照“复活”已删/过期内容。迁移前快照仅为受限审计恢复材料，恢复业务数据需单独判断，不自动覆盖当前事实。

## 8. 剩余有限输入与结论口径

不再需要无边界生产盘点。实施者仅需用隔离测试闭合：准确镜像父目录及叶子bind创建、/tmp峰值与OCR子进程、真实认证API/PG合成链、root回滚对新数据的访问。切换前固定元数据复核UID碰撞/当前digest/配置路径与秘密文件名；trace是否有宿主轮转或读取者需核对既有部署记录，没有证据不得假设不存在。如需生产只读命令，父控制器先审查限定命令，不由本报告代理自行连接。

设计初稿时结果（历史）：源码/设计核查已完成；执行验收矩阵 **通过0 / 失败0 / 未执行12**。接口回归0、进程故障/回滚测试0、前端组件测试0、浏览器验收0、生产验收0；真实模型调用0。报告可指导实施，但非root修复、上线可用与完整业务验收尚未完成。没有为本任务变更业务行为、增加兜底、增加模型预算或减少能力。

### 设计之后的实际配置补证

控制器已从准确DAQ进程读取非秘密配置：production、identity=true、provider=anthropic、tasks=false、archive=false，运行版本 `daq-062f0a7ae744`；session key路径 `/run/daq-secrets/session-keyring.json`，SDK根使用变量 `DAQ_SDK_RELEASE_ROOT`。审批公钥实际在 `/run/daq-secrets/feedback-approval-keys-20261023.json`，新秘密目录应包含该第四个必要只读文件，保留其既有SHA pin。

正常DSN的限时只读角色元数据查询还确认当前 `ai_fae` 角色为共享数据库超级用户。此项是明确未完成的DB-01，单独安排数据库最小角色迁移；当前运行用户/文件收口不更改DSN或共享PG，不能扩大为数据库隔离验收。


官方配置依据：[Docker Compose服务字段](https://docs.docker.com/reference/compose-file/services/)、[Docker安全边界](https://docs.docker.com/engine/security/)、[tmpfs](https://docs.docker.com/engine/storage/tmpfs/)。本次使用准确已运行镜像及显式运行策略，不重建浮动依赖。

## 9. 首轮工具审查后的控制面契约复核

资产工具首轮 `ae022a9` 独立审查发现三个相关的校验缺口，尚未上云执行。共同原因是把“原始字节摘要一致”扩展成了“消费者实际语义及资源身份一致”：env逐行解析可把多行字符串内部的赋值当成真实变量；Compose输入虽按顺序校验hash，却被硬编码配置替代；目标校验仅比内容/权限，没有保存已发布文件inode和空目录集合。已有测试只检验预期常规表示，未覆盖这些生产者到消费者边界。

整改必须一起收敛：环境使用真实支持的Compose语法/受控插值上下文，最终有效环境在私有进程内比较，不改原env字节；完整有序输入先解析成有效模型，明确保留安全改动以外的设置，或对不能保留的未知设置明确拒绝；目标树在最终权限设置后保存文件和目录的准确身份及完整成员，启动前持有描述符复核。业务写入后的RW清单按原设计失效，回滚使用当前数据，不要求与旧清单相等。

另将输入、输出manifest上限、源数量/大小及实际磁盘余量形成同一个预检合同，避免复制成功后清单无法读取。此次只读计量：主知识2文件/9,412,265字节，SDK15文件/1,329,624字节，附件0文件，trace50,729字节，可用38,614,347,776字节；执行前仍需重新计量。上限应适应该明确范围，不采用无依据的1TiB许可。原34项替身测试保留，修正前分别补多行env、覆盖配置、同字节inode替换和空目录漂移反例，再仅运行受影响回归与独立复审。

本机虽无 `docker compose` 子命令，但有显式 `/opt/homebrew/bin/docker-compose` 5.6.0；生产为2.40.3。开发可直接使用明确的本机前端解析器，生产前仍须在2.40.3做私有有效配置对比，不把不同版本的工程通过冒充生产兼容。相关行为依据[Compose config](https://docs.docker.com/reference/cli/docker/compose/config/)及[env文件语法](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/#env-file-syntax)。

当前源配置archive=false。V08的正常附件归属、非空迁移、启动GC及临时清理仍是本轮必验；归档保护分支按未启用能力单列未执行，不为测试开启生产归档，也不把它记为通过。该适用范围在原生验证前固定，不用于覆盖任何已失败用例。

### 有效模型保留与执行边界的共同约束

复审进一步确认：完整继承有效模型时，`volumes_from` 可绕过仅检查五个显式bind的实现。共同问题是把“保留业务设置”当成“允许继承全部执行能力”。修复须统一验证有效服务模型的权限入口，不能只单补这一字段：显式volumes、继承卷、secrets/configs、API socket、设备/GPU、补充组、capability/privileged、共享PID/IPC/UTS/user namespace、network_mode和额外tmpfs均纳入同一执行边界。非安全改动继续保留；与既定边界冲突或未支持的新能力入口明确拒绝，先补矩阵反例再实施。本批不通过恢复整块data、继承其他容器资源或新增权限让配置通过。

## 10. 原生验收工具首审后的共同契约复核

`4c83c02` 尚未上云的验收工具首审为2 Critical/6 Important/2 Minor。关键共同失效是：客户端结束被当成daemon请求结束，词法路径被当成实际目录边界，以及“调用/返回ready/存在manifest”被当成下游确实消费了正确内容。26项工程测试覆盖了常规形状，却没有覆盖延迟创建、路径别名和保持HTTP成功但内容错误的负例。不得带着这些工具缺口去碰生产，再用生产故障发现问题。

| 共同职责 | 修复后必须成立的合同与验收 |
|---|---|
| 操作生命周期 | 创建结果未知是持久状态，当前名字不存在不能释放全局租约；后来观察到准确自有对象才可清理。SIGTERM/HUP关闭并回收独立客户端进程组，再确认自有容器状态；SIGKILL/重启按明确恢复程序处理，不删lease掩盖未知。覆盖真实进程终止和延迟materialize反例。 |
| 特权文件准备 | 创建前拒绝dot/dotdot/alias重叠，使用受控父目录与描述符保持边界；输入有每文件/总量/数量/深度/磁盘余量上限及准确身份/成员基线。与Task2a复用同仓安全原语或相同严格合同，不能另留弱copytree路径。 |
| 内容消费 | 每格式校验fixture文本、locator/source/hash；normalized图片解码与元数据一致。旧迁移附件须由真实loader/tool读取，覆盖owner/other及可删除样本；root回滚与再前进校验old/nonroot-new/root-new内容和已删除项，不能仅GET manifest。 |
| 合成完整任务 | provider在真实read回执验证成功、来源/定位和唯一fixture内容匹配后继续；跨轮检查恢复上下文/版本/附件绑定，幂等重放的调用计数不增加。正常成功提交与真正原子故障证明分别命名，未测故障不能称原子性已证明。 |
| 证据与适用性 | 每个注入进程启动前记录替身、phase和输入身份；每次停止前保留唯一阶段证据及告警摘要。保存主/子PID、starttime、父/namespace及实际PG/worker/APP/tmpfs/mount源身份。V08按第9节当前archive=false适用范围判断，未启用分支仍单列未执行。 |

所有修复一次性按上述责任收敛后，仅重跑受影响工程负例并独立复审；第一批原生验证仍须首错停止。没有原生执行证据就继续保持V01–V11未执行，更不声称生产完成。

`18859f2`复审又确认两个相邻缺口（0C/2I/0M），因此继续禁止原生执行。共同原因分别是把“不能释放全局租约”错误扩展成“立即放弃其他已知对象清理”，以及把会执行维护的消费者当成纯观察器。统一修复契约如下：未知create持续阻止放行，但清理须逐项遍历全部已知自有对象、尽力停止并确认移除，逐对象保存结果后再汇总失败，不因一个未知或清理异常留下其他已知写者；观察startup临时清理必须在任何会构造AttachmentStore或触发维护的worker之前，以无副作用的受限文件系统快照判定，并立即持久化证据。故意跳过应用启动清理时，即使后续消费者会清掉残留，原门项也必须失败。新的反例只覆盖这两个职责，不重跑已经有结论的无关工程组。

后续`21d0843`差异复审确认启动观察已关闭，但持续journal写失败仍使每项清理在stop前中止，reboot证据写失败也会在遍历前结束。此类失败同属上述生命周期职责：持久化与已确认自有写者的安全停止须有独立错误边界；只对已再次核对归属的既有对象继续stop/确认，不因记录失败创建新资源或扩大归属，持久证据任何不完整均保持blocked和非零结果。补持续save失败、起始恢复证据save失败与多个已知对象的组合测试，禁止只覆盖“下一次save恢复”的短暂失败后声称持久故障已验收。

## 11. 现场Compose生产者协议适配复核

`07b2fc3` 的文件准备在真实Linux以原字节成功复制两知识根和四文件，新key属主21081/0600亦已实际核对；原服务未停止、原权限未改。实际2.40.3解析原发布环境通过，24个有效变量摘要与运行进程相同。但候选配置草案在任何服务操作前因缺少`env_file`元数据被拒绝，原失败保留。

使用纯合成单服务独立复现：现场2.40.3在JSON/YAML、含`--no-normalize`的`--no-env-resolution`输出均仍合并了env文件变量并丢弃引用；`--no-interpolate`路径则保留env_file和仅显式environment。本地5.6的旧测试没有覆盖该生产者差异。[2.40.3官方实现](https://raw.githubusercontent.com/docker/compose/v2.40.3/cmd/compose/config.go)区分ToProject与ToModel输出路径，支持上述机制判断；本链路结论仍以现场复现为依据，不将责任推给工具后留下不可用适配。

后续适配统一保留“原始引用/显式覆盖”与“完整有效语义”两份事实：用明确原始模型协议取得引用，通过同一已固定Compose前端及受控插值上下文解析引用/显式覆盖，恢复不包含env_file展开秘密的可重用模型，并以再次完整解析与原有效模型比较证明等价。不得手工逐行解析env、只注入猜测的引用、把全部展开秘密写进新配置、静默换版本或在遇错后隐式降级。新增2.40生产者特征及5.6的跨协议反例，再独立复审；旧文件准备代码/数据未变时复用其证据，新的解析器适配版本另行记录和验证。

`1270caf`独立审查通过后，首次现场验证已通过引用恢复和runtime/rollback有效语义比较，但在执行策略检查遇到第二个同类协议差异，整次仍记失败，服务操作为零。2.40.3把显式`bind.create_host_path:false`序列化成`bind:{}`，并省略RW的`read_only:false`；原始候选依然明确写false。共同原因是把作者输入约束与类型化有效模型的编码形式当成同一契约。后续统一区分这两层：作者配置仍必须显式禁止自动建目录；有效模型只在同一前端已验证的long bind对象中，按已核对的类型默认值解释省略false，拒绝true、错误类型、未知字段或缺失bind结构，不对未知字段猜默认值。[该版本的compose-go类型](https://raw.githubusercontent.com/compose-spec/compose-go/v2.9.1/types/types.go)以bool+omitempty定义该字段，[实际创建路径](https://raw.githubusercontent.com/docker/compose/v2.40.3/pkg/compose/create.go)将此bool传入CreateMountpoint。新增真实序列化特征的正反例，并在实际daemon创建时继续核对无目录自动创建；不靠删掉安全检查取得通过。

## 12. 首次原生失败后的制品权限与回执协议复核

`b40a6e8`在准确原镜像上的首批原生验证于17:41开始，V01失败（migrate worker为ModuleNotFoundError），其余10门未执行，1.031秒停止。两个测试容器均已停止并移除，但清理确认因CLI错误文本差异保持blocked，原失败/日志/原journal已独立保存；未切换生产。现场仅做只读诊断，原所有生产容器ID/image/PID/启动时间均未变。

ModuleNotFound的准确名字为`src.agent.session`；文件本身root0644，父目录`/app/src/agent`为root0700。对六个固定不可变代码根的只读元数据检查共261文件、26目录，其中20目录为0700（含knowledge、agent、attachments、身份与API、迁移、静态assets）。共同原因是构建继承开发/发布目录私有模式，原root运行掩盖了制品的可读性合同；这不是模型、PG或知识签名故障。旧“原镜像只改运行身份”的实现取舍据此失效，不能加cap、恢复root或另加未声明整块代码挂载让门项通过。

修复收敛到镜像打包边界：以原准确本地镜像5ed10ead…为唯一基础，离线派生仅规范不可变应用资产权限的制品，不安装/升级依赖、不拉基础镜像、不改任何业务/上游/模型/知识字节。仅允许固定代码根`/app/daq_fae`、`/app/src`、`/app/scripts`、`/app/prompts`、`/app/migrations`、`/app/webui/dist`，目录root0755，普通文件保持原内容及必要执行位、只允许root写；link/special/非预期owner或资源上限失败即拒绝。不得扩到data、secrets、系统目录。打包前后独立文件hash/成员/字节数一致，变更清单只含上述权限元数据。未来完整Dockerfile同样强制该合同；本次使用既有精确基础，不重装浮动依赖。

本节显式替代前文/旧任务书对候选“同一镜像、完全不构建”的工程取舍；旧源与root回滚身份保持，只有候选镜像ID分离，DAQ_RUNTIME_RELEASE、应用源码、上游pin、模型、签名知识身份保持原值。六根之外的必要祖先`/app`、`/app/webui`、`/app/data`、`/app/data/knowledge`、`/run`以及`/app/upstream-source.json`、`/app/deploy/provision_env.py`的父目录和文件只做可遍历/可读、owner和内容围栏，失败即拒绝，不扩大chmod范围。最终业务树只改变上述权限；镜像另有显式USER21081、policy/base labels和临时normalizer层及删除，须精确审计。用Python -B运行构建脚本，禁止意外pyc成为新制品内容。

标准发布生产者也须闭环：`deploy/build_release.sh`的显式tar名单目前不含新normalizer；更新完整Dockerfile时同步该名单，并以最小打包合同验证COPY源确实进入发布包。未来完整发布包与本次仅两文件的离线派生context分别校验，不能把整个仓库/env或秘密放进派生构建。

配方首审`dbb7853`发现预算职责仍有生产者—消费者错位：先全量listdir/sort，再在examine检查节点数，不能限制最初分配。修正把限额落实到最早的scandir迭代点，同时计入祖先已列出但尚未访问的成员，后置成员复核也使用同一有界枚举原语；不得让每层重新获得一份完整全局额度，或用已消耗额度错误拒绝正常复核。宽目录、祖先待处理与后置漂移均须在最初超额处拒绝，保留已通过的内容/包合同证据，只重跑受影响枚举/正例/竞态。

### OverlayFS实际元数据传播

`33fb060`配方审查通过、归主线后首次离线构建失败于path identity changed before chmod，17.592秒，无候选tag，失败中间容器已移除，生产身份不变。没有重跑构建。随后使用已审查OwnedDocker生命周期在原准确镜像、network none、无生产挂载/环境的一次性容器上，仅对knowledge和agent两个目录fchmod0755：actual PASS、cleanup released。记录证明目标及其现存祖先`/app`、`/app/daq_fae`或`/app/src`的ctime同步改变；dev/inode、owner/group、size、mtime不变，其他分支ctime不变。故障来自把预期copy-up的祖先ctime传播当成外部替换，而非发生实际字节漂移。

统一修复只允许已知chmod操作的受控元数据效应：操作前持有并核对完整祖先链，操作后逐一确认dev/inode、owner/group、nlink、size、mtime及非目标mode不变，只对该已验证链及目标记录新ctime；目标mode须准确等于既定权限。不得全局忽略ctime、重抓整树基线，或刷新未参与操作的兄弟路径。最终独立扫描仍与原始内容/成员/所有权/mtime及资源身份比较，并与逐操作记录的预期ctime比较；未解释的ctime或其他字段变化继续拒绝。六根外祖先只允许该内核副作用，不能调用chmod或改其mode。局部工程测试模拟已观察到的祖先ctime传播，并保留mtime/身份/成员及无关路径漂移负例；修复独立复审后另建第二批构建，原失败保留。

`3abffbd`第二批构建仍被metadata校验拒绝（13.8秒，无tag、失败中间容器已移除）。完整字段探针补充发现：第一份探针没有记录nlink，因而上文“nlink不变”的推断撤回。新探针保留相同两个目录输入、实际PASS且cleanup released，记录只有ctime、目标mode及**目录nlink**变化：knowledge 2→1，其祖先daq_fae 3→1；agent 6→1，其祖先src 14→1；所有dev/inode、owner/group、size、mtime仍稳定。OverlayFS合并目录的虚拟nlink不能当作原物理目录子项数或文件硬链接证据。

按统一字段职责修订上述操作契约：regular file仍严格nlink1；目录以持有的资源身份、完整成员和路径链证明边界。仅在明确检测到根OverlayFS、已核对的chmod链、dir类型及稳定其他字段条件下，接纳目录nlink从>1到1的已观测copy-up转换，并逐操作记录预期值；其余转换、未参与分支、未知文件系统均拒绝，不作全局nlink忽略。最终仍核对原始成员/内容/mtime/身份和逐操作ctime/nlink，六根外ancestor mode不改。增加完整字段的模拟边界、错误nlink方向/未知FS/regular硬链接负例；失败诊断只记录安全字段名，避免再次以不完整探针假设替代真实契约。第三次构建须先独立复审，不重跑前两次失败输入冒充通过。

## 13. 隔离传输配置与产品身份合同

`9439b21`第三构建actual PASS，20权限变化、295条目/3,667,034字节前后内容摘要一致，基础层保留加两层，除User/三个已登记label外Config相同。双镜像集成`e0aa155`独立审查及现场Compose2.40验证通过后，18:40首批派生镜像原生仍在V01失败，14.615秒首错停止，cleanup released。实际migrate和root seed子阶段均PASS，APP因ValueError退出；其余10门未执行、生产不变。

只读代码链核对又确认一个确定的探针组装错误：其ORIGIN为`https://native-probe.invalid`，原产品`_canonical_browser_origin`只批准fae.orbbec.com.cn、agent.orbbec.com.cn。本地调用准确原函数、相同Origin的最小复现明确返回enterprise_public_origin_invalid。源应用/允许域名不应被为测试放宽；隔离的HTTP地址与合法浏览器Origin是两个职责。后续正例使用现有批准域名字符串，所有实际网络仍只走私有loopback、identity/provider替身，不出站访问该域名。原无效Origin组装失败继续保留，加入精确拒绝负例，不能将修正后的正例冒称原失败批通过。其余fixtures/签名/key/模型/Loop/门标准不改。

当前失败只持久化exception_type，不能定位ValueError的实际首个抛出位置；确定的Origin错误与其是否就是本次首错须分开陈述。运行器补充有界、仅不可变代码路径/函数/行号的私有栈元数据，禁止记录异常message、args、源码行、env或业务正文；原记录不补写伪造栈。新批保留实际失败帧，若仍有其他首错即停止，再按真实证据修复。工程只跑身份配置/私有诊断相关切片，不用关闭验证或替换业务规则取得启动。

## 14. HTTP客户端所有权与剩余门项

`01a1f8b`两项独立审查通过后，新原生批27.398秒取得V01/V03/V04/V05实际PASS，V06首错FAIL，其余6门未执行，cleanup released。安全帧明确落在`runtime_auth`的`with client`；此前`http_client`已对同一httpx.Client发起session POST，随后再进入其上下文。根以自有随机端口HTTP fixture和真实httpx0.28.1最小复现，首次请求200后再次enter必抛Cannot open a client instance more than once，退出后已close。此时不是服务拒绝、文件权限或静态字节错误；原回执不重分类为通过。

共同原因是factory/消费者对连接所有权的契约不一致，影响全部已登录HTTP消费者，不只runtime_auth一处。修复统一为单一contextmanager负责一次打开→真实认证→调用方使用→任意阶段异常均关闭，所有调用点同时迁移；不换新client隐藏二次enter、不重复会话POST、不吞RuntimeError或移除身份/CSRF验证。真实loopback工程例验证相同client认证后继续请求、退出/认证失败/调用方失败均关闭，保存的cookie恢复分支相同所有权；不使用容忍重入的假client来证明真实SDK行为。

镜像7360、source5ed、helper、源应用、Origin、fixtures和权限配置均未变化。修复后新批验证受影响及尚未执行的V02/V06/V07/V08/V09/V10/V11，V01是实际启动前提；V03/V04/V05只有在函数/上下文/同一镜像与主机边界逐项确认未变时，复用01a原批证据。最终11门台账逐门列出批次/版本和复用理由，不称新批“11全绿”；若继承条件不成立则补其受影响验证，不能以数量拼接掩盖完整任务缺口。原生完整附件—跨轮—回滚链V07/V08/V10/V11须在同一新批闭合，真实模型/员工SSO/浏览器仍另列未执行。

派生镜像在主线准确配方提交后构建，登记新digest及基础层前缀/配置/字节等价证据；只有显式已审查的候选digest才能用于非root。源容器、停止证据及root回滚仍绑定原5ed10ead…，回滚继续使用当前新附件/trace。运行和回滚两种镜像身份必须在helper/native/controller统一表达，不把候选digest偷偷代替旧回滚。原资产准备证据可在文件原语未变时复用；镜像改变影响启动/权限/转换/恢复，因此新原生批仍运行全部11个适用门，原失败保留，不改原输入或降低断言。

清理回执的已知现场形式为returncode1、stdout空JSON列表、stderr `Error response from daemon: No such container: <exact-target>`；原测试仅覆盖`Error: No such container: <exact-target>`。适配须明确验证完整目标、返回码和空结果的受控协议，其他错误保持unknown，不扩大成任意非零即不存在；id未知的create即便当前不存在仍不得放行。先修复并审查此协议，再对原workroot执行单独cleanup确认；不能修改旧FAIL报告或手删租约。runtime失败补充受限missing-module名字，避免为寻找原因反复运行完整场景；不输出异常消息、秘密或业务正文。

## 15. 切换前观测的语义与完成证明

控制器只读源层材料检查首批完成文件分类后，在两次`docker diff`原文本比较处失败；私有安全帧准确指向最后一次diff比较。随后固定单项诊断证明两次均238条、无重复路径、排序后成员及change完全相同，只有输出顺序不同。共同原因仍是把工具编码顺序当成资源身份；完整有序Compose输入仍须保序，但diff是按路径唯一的条目集合。后续先严格解析change/绝对path、拒绝重复与未知类型，再按明确canonical编码比较和计算摘要；不全局忽略顺序、重复或内容变化。原失败保留，不能把规范化后的新批冒称原批通过。该修复只涉及控制器观测，不改DAQ应用、helper或已有原生证据。

最终只读全机围栏原批又在inspect集合比较被拒绝。固定字段路径诊断发现差异仅位于三个服务的`Mounts`列表编码位置，容器成员未变；该诊断没有证明完整条目等价，也不能回填原批通过。同类问题按统一语义复核：只对已验证Destination唯一且为绝对路径的实际Mounts建立按Destination的canonical顺序，保留并精确比较每个条目的全部字段、类型、权限、传播、源和目标；重复目标或未知结构拒绝。其他Config/HostConfig/环境/网络列表保持原合同，不全局忽略数组顺序。新批必须在该明确规则下重新比较完整记录，原拒绝及诊断保留；这仍不是V12或业务静默证明。

真实源进程只读快照还观察到14个TID，包含一个晚于启动的futex等待线程。它不是活动任务的证据，也不能据comm/wchan判为闲置。`src/api/stream.py`的daemon pump、内存上传gate、parser spawn与附件GC具有不同生命周期；ledger零、连接零、ExitCode0都不足以证明业务完整排空。当前health没有统一可信的inflight/worker完成快照，因此控制器必须分别登记可观测资源与执行完成证据。缺少后者时明确`execution_completion_unproven`并在SourceStop之前阻断，保留原线上服务；不得用parent布尔断言、等待时长、推断线程名或强制退出填补。是否需要增加应用生命周期接口由真实检查结论决定，另行设计生产者→消费者与正常/断连/解析/GC的完整验收，不把局部计数端点当成排空能力。
