# 材料权限检查与正文读取分离

## 实施前设计

范围为 C0 历史读取性能整改的材料权限子任务；仅本地真实上传、一次性 PostgreSQL 和自有对象存储测试，不连接生产或调用模型。

新增 `material_authority.py` 与迁移 `098_hr_agent_material_authority.sql`。服务端完成实际 UTF-8 字节/hash/解码或解析结果完整性校验后，由 `MaterialService.resolve/read_text` 写入不可更新的凭据：owner、attachment、准确正文 revision/sha、source_identity_sha、创建时间。source_identity_sha 包含实际源字节 SHA、MIME、大小、不可变定位和 write_attempt；表内不保存正文、文件路径或 locator。附件与 owner 采用复合外键，附件删除级联清除凭据。应用角色仅 SELECT/INSERT。

`MaterialService.authorize_refs` 单次批量查询当前附件元数据、删除请求/保留期与匹配凭据；不读取对象、不解密正文、不为每个引用另开连接、不跨调用缓存决定。缺少凭据的旧引用返回 410；用户显式重新 resolve/read 同一精确内容可以建立凭据，不能自动换成当前内容。ResourceReader 保留工作所选引用和 owner 检查，将材料引用一次性交给此方法。

凭据只说明特定来源身份的正文曾被验证，不是当前授权，也不是对象存储的新鲜字节审计。每次授权仍查当前附件状态；实际 read_text 仍读取并校验源字节。解析正文还校验其已加密保存的解析结果与源身份；097 按源 SHA 查询并单独比较 locator/write_attempt/MIME/大小，不仅凭同 hash 复用。解析的显式正文读取补充源字节验证，权限检查不解密解析记录。

文件范围：新模块、materials.py、resources.py 的材料权限分支、098、config.py 就绪校验、新测试模块及必要的基础表数/就绪回归更新。root 负责 repository.py、根设计文档与计划，避免共享修改。

## 实现与调用契约

`MaterialService.authorize_refs(owner_id: UUID, refs)` 成功返回 None，失败抛出 `HrAgentProblem`。非材料 kind 不是可接受参数；未知附件、owner 不匹配、缺少准确凭据、源元数据变化或当前不可访问均失败关闭。它只校验材料所有权与内容身份；HR 使用权限、本次工作选中范围仍由 ResourceReader/HTTP 身份边界负责。

一次调用先按完整引用去重，用 `jsonb_to_recordset` 将所有引用交给同一条 SQL；LEFT JOIN 当前附件、上传 write_attempt 与凭据，检查当前删除请求和数据库时钟下的保留期。只选择需要的元数据，不加载附件对象路径密文或解析正文密文；同一连接完成全部引用判断。下一调用重新查询，没有进程级授权缓存。

ResourceReader 原有 owner、对象和工作已选引用判断保留；材料分支汇总后调用一次 authorize_refs。未进入作用域的另一附件即使同 owner、凭据完整仍返回403。原有上传测试夹具改用同一 ResourceReader 验证器，不再通过读取正文的测试回调代替权限接口。

成功 resolve/read_text 才能写凭据，单纯知道/计算正确 ExactRef 不能通过工作受理；客户端无凭据写接口。凭据主键使重复校验幂等，应用角色不能 UPDATE/DELETE，复合外键阻止跨 owner 的凭据记录。原始附件实际删除时级联移除凭据。人工删除凭据后的同一准确 read_text 可以重建；错误 hash 的失败读取不能重建凭据。

实际正文读取仍校验原始大小与 SHA。解析正文沿用097的 sealed_content 验证及 source_identity 比较，并在返回之前另外读取原始字节与重新检查当前附件。发现解析密文损坏时，过去逃逸的 ContentCryptoError 现在转换为不暴露内部信息的503；没有将失败结果持久化成有效凭据。

## 迁移与就绪

098 SHA-256：`8981330f48c9ed3673d9377e678c44e9b0494f5aae615793023c363eb8018267`。config 就绪检查要求这份迁移、凭据表及 SELECT/INSERT 权限；缺失不会运行时补表。基础表数量在本子任务提交时由17变为18，后续099由其交付者扩展。

没有批量后台回填。升级后的历史材料若缺凭据，会返回410，必须显式读取其同一准确正文或 resolve 建立凭据；不能静默切换到最新正文或绕过当前权限。这一行为需要在最终部署/兼容验收时纳入已有工作的恢复流程。

## 验证记录

先运行新最小失败用例，观察 `MaterialService.authorize_refs` 缺失；新解析密文损坏测试进一步发现原有解密异常逃逸，确认失败后补充503边界。

```bash
.venv/bin/python -m pytest -q \
  tests/test_hr_agent_material_authority.py \
  tests/test_hr_agent_materials.py \
  tests/test_hr_agent_material_parsing.py \
  tests/test_hr_agent_foundation.py
```

**73 passed in 23.08s**，其中新权限测试20项。新增模块、materials、config、resources、权限测试及上传夹具 Ruff check通过；格式检查覆盖新增/修改的已格式化文件并通过。基础测试文件仅改表数和迁移版本，未做无关整体格式化。

- 接口/数据库：真实本地 HTTP 上传、处理、材料resolve与受理路径；真实身份中间件、Origin/CSRF、去重键；隔离 PostgreSQL 和本地对象存储。身份服务使用既有本地测试替身，不连接组织目录。证明未resolve的正确引用受理410、HTTP伪造凭据入口被拒绝、current scope不能借同owner另一个已验证附件扩大。
- 性能与撤权：3份不同实际上传文本重复组成120个引用，单次授权仅1个连接；对象读取和材料/解析解密边界设置失败探针，均未调用。再次授权重新开连接。过期、隔离、删除状态、删除请求、源SHA、MIME、大小、locator、write_attempt任一变化均拒绝；owner、revision、正文hash和不存在附件也拒绝。
- 持久化/完整性：无正文/路径的准确凭据、重复读幂等、跨owner外键拒绝、实际附件DELETE级联清除、缺凭据的显式恢复、错误引用不建凭据；实际DOCX上传/解析与源字节、解析密文破坏；源SHA相同但locator变化不得复用097解析。
- 进程故障：本命令包含既有材料读取进程被杀后无明文文件、解析进程死亡后恢复的测试，均通过；未据此声称完整Worker/模型故障矩阵完成。root负责历史锁与取消响应的独立证据。
- 前端组件、浏览器、生产验收：未在本子任务执行。所有样本为公开岗位文本或生成的虚构PDF/DOCX，未调用模型或外部对象存储。

## 信任边界与限制

授权凭据不是最新字节审计。在对象存储违背不可变性但数据库元数据未变时，授权检查仍可通过；实际read_text发现损坏返回503。解析密文同样在实际读取时验证；授权过程不会重新解密它。测试明确展示这种区别，不能把“发模型前权限复查”描述为“每次重读原始附件”。

当前授权检查以同一数据库语句的快照为准，不承诺已经发送给模型的数据能够撤回。后续操作仍重新检查当前授权；本子任务不替代root的每轮历史过滤与取消/租约处理。没有生产迁移、上线、自动清理或旧历史凭据回填。
