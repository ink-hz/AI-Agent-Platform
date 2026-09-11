# C3 用量、来源与审读自述纠正记录

核查时间：2026-09-11 05:33 UTC。核查者：Codex AI 子代理 `/root/b2b_workbench/review_b2b`。本次只读复核既有公开证据及所附运行前源码快照，并新增本报告；未改既有 evidence / 请求 / 反馈 / 成果 blob、根文档或 C 汇总，未联网、探测网关或调用模型。这是仅 AI 的证据核查，不是人类验收。

可确认四项需收紧的自述：run9/run11 的全部最终 attempt 扣记均采用输入估算下限，并非部分采用实报；run9 有两次成功摘要；run9 的来源定位元数据少于 run11；独立审读并不等于盲评、未经执行者处理的反馈或人工 HR 签收。成功配置仅覆盖试验副本，不能称默认配置已通过 C3。

## 1. 逐 attempt 用量：reported、floor、reserve、charged

数据来自 [run9 evidence](artifacts/2026-09-11-hr-c3/run-9/evidence.json)、[run11 evidence](artifacts/2026-09-11-hr-c3/run-11/evidence.json) 各自最后阶段的 attempts，以完整 attempt_id 连接同目录 public-requests；不累加重复的阶段快照。表中序号为各自 ordinal，可唯一反查完整 ID。

已核验每个运行附带的六份 runner-sources SHA-256 与 `runner_files_sha256_before_first_send` 一致。以下算法按该运行快照复算，不借当前代码补解释历史。

- 网关自报总量 R = `reply.usage.input_total + output_total`。Anthropic 协议归一化输入包含 input、cache creation、cache read 三个非重叠桶；本两次运行缓存桶均为 0。thinking 是 output 的细分，不能再相加。
- 输入下限 F = `len(canonical_json({messages, tools}).encode('utf8')) + 256 + 32 * len(messages)`，`canonical_json` 使用 UTF-8、排序键及紧凑分隔符。这是保守字节估算，并非模型 tokenizer 的实际 token 数。
- 发出时先扣预留 P = F + 请求 `max_output_tokens`；结算时 C = max(R, F)，并将工作账本从 P 调整为 C。**不是** F + 实报输出，也不是计费公式。
- 本次所有行都满足 F > R，故最终 C = F，attempt `usage_quality=estimated`；reply 的 `usage.quality=reported` 只说明接收到了自报计数，两层不矛盾。

### run9（每次最大输出 8,192）

| ordinal | purpose | reported input | reported output | R 合计 | F 下限 = C 最终扣记 | P 发出预留 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | work | 8,128 | 293 | 8,421 | 17,221 | 25,413 |
| 2 | work | 22,342 | 6,651 | 28,993 | 51,549 | 59,741 |
| 3 | work | 29,258 | 980 | 30,238 | 66,756 | 74,948 |
| 4 | summary | 25,591 | 2,909 | 28,500 | 60,492 | 68,684 |
| 5 | work | 11,888 | 430 | 12,318 | 26,531 | 34,723 |
| 6 | work | 22,037 | 242 | 22,279 | 50,562 | 58,754 |
| 7 | work | 28,262 | 635 | 28,897 | 65,071 | 73,263 |
| 8 | work | 31,750 | 237 | 31,987 | 74,101 | 82,293 |
| 9 | work | 33,943 | 6,318 | 40,261 | 79,770 | 87,962 |
| 10 | work | 45,290 | 1,138 | 46,428 | 107,830 | 116,022 |
| 11 | summary | 42,239 | 1,606 | 43,845 | 100,756 | 108,948 |
| 12 | work | 11,745 | 4,554 | 16,299 | 25,532 | 33,724 |
| 13 | work | 16,472 | 729 | 17,201 | 36,665 | 44,857 |
| 14 | work | 18,513 | 853 | 19,366 | 40,508 | 48,700 |
| **合计** | **12 work + 2 summary** | **347,458** | **27,575** | **375,033** | **803,344** | **918,032** |

精确比例 C/R = **803344/375033**，约 **2.142062165 倍**；扣记比自报高 **428,311（114.2062165%）**。自报占扣记约 **46.68398594%**。14 行均 committed / estimated；两次 summary 是 ordinal 4、11。现有 continuation “包含1次成功摘要”应改为“包含2次成功摘要”。thinking 自报合计 7,096，已包含在 27,575 output 内。

### run11（每次最大输出 16,384）

| ordinal | purpose | reported input | reported output | R 合计 | F 下限 = C 最终扣记 | P 发出预留 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | work | 7,882 | 290 | 8,172 | 16,582 | 32,966 |
| 2 | work | 17,198 | 13,368 | 30,566 | 40,112 | 56,496 |
| 3 | work | 38,604 | 2,189 | 40,793 | 89,150 | 105,534 |
| 4 | summary | 34,914 | 2,348 | 37,262 | 84,677 | 101,061 |
| 5 | work | 10,493 | 502 | 10,995 | 23,192 | 39,576 |
| 6 | work | 25,774 | 11,107 | 36,881 | 60,567 | 76,951 |
| 7 | work | 46,108 | 1,511 | 47,619 | 112,961 | 129,345 |
| **合计** | **6 work + 1 summary** | **180,973** | **31,315** | **212,288** | **427,241** | **541,929** |

精确比例 C/R = **427241/212288**，约 **2.012553701 倍**；扣记比自报高 **214,953（101.2553701%）**。自报占扣记约 **49.68811514%**。7 行均 committed / estimated；summary 是 ordinal 4。thinking 自报合计 6,581，已包含在 31,315 output 内。

### mixed 具体是哪层

运行快照 repository.py 的发送路径先令 work budget `usage_quality='mixed'`；`_settle` 在结算后也无条件将该工作字段设为 mixed。它是工作账本的保守状态标记，并非按 attempt 比例计算的分类结果，也不能证明存在“实报结算的调用 + 估算结算的调用”的混合。

因此建议把“run9/11 混合保守估算与实报”改成：

> run9 网关自报输入加输出 375,033，工作扣记 803,344；run11 自报 212,288，扣记 427,241。两次运行所有 attempt 的最终扣记均被保守输入估算下限抬高，attempt 全为 estimated；work 标记 mixed 来自账本算法，不是供应商账单或精确成本。数字不含其他失败运行与诊断。

### 空响应与未结算预留

run9 和 run11 **没有**“可见正文与工具调用均为空的 end_turn”记录，全部 attempt 有可提交 reply；不能把纯 tool_use 的空 text 当空响应。两者最终扣记中没有空响应保留预留的行。

为解释此前失败的用量语义，另只读核对早期归档：run2/5/6/7/8 中共见以下 6 个可见正文字符数=0、工具参数字符数=0、stop=end_turn 的记录，对应 interrupted、reply=null、estimated，charged=reserved：

| run | attempt_id | 保留预留 token |
| --- | --- | ---: |
| 2 | 233ed0a3-7c4a-424c-93ee-c4086e3556b3 | 65,372 |
| 5 | 11cc0367-d644-4d4c-98e7-8bb20be4cd24 | 47,940 |
| 6 | 12cd4f63-8379-4ae9-b27d-21e830ff2a00 | 63,656 |
| 7 | 52d9e658-e624-40d4-84f1-2a1e86ff44ce | 47,296 |
| 8 | c46a78a1-51df-410e-b2d6-1b4e23f98b21 | 62,454 |
| 8 | e7fb3555-e275-4527-b386-666df4c944a9 | 62,902 |
| **合计** | **已捕获的6条** | **349,620** |

这些早期 transport 条目未保留 usage_observations，`error_code` 还可能为 null：传输层已正常结束，语义解码后才拒绝空回复。不能只搜索 `transport.error_code=empty_response` 计数；也不能把这些预留当实报用量，更不能推断供应商免费、计费为零或总失败成本恰为 349,620。运行快照 model.py 在构造可提交 Usage/ModelReply 之前检查空响应，缺少可结算 reply 时发送预留可能保留。run1 缺少同等传输捕获，不能从未查到条目证明其无空响应。

## 2. run9 来源身份：原捕获与本次后验核验分列

### 原运行实际有的内容

`public_provenance` 仅有 company_key、job_count、bundle_manifest_sha256、jobs、scope。每条 jobs 保留 job_id、title、source_url、observed_at、public_job_key、evidence_sha256、完整 duty / requirements；另有 source_text_sha256 与字符数。scope 明确只覆盖该静态归档公司的所有规范身份，不是现行企业全部岗位。

原运行 public_archive 源码确实读取 raw-evidence-index 与 normalized-jobs，以原件 SHA 定位 locator，核验字节哈希，并用公开 ID 在 raw.jobs 唯一匹配，然后保存正文。**源码执行了这些步骤，不等于其全部中间定位元数据已导出。** 原 evidence 未记录 bundle_id、bundle_path、每原件 locator、size/MIME、JSON 指针、source_id、raw-evidence-index 哈希或 normalized-jobs 哈希。README 的本机路径属于运行说明，不能替代 evidence 内的逐项捕获。

19 条 source_url 仅是 `gwtd.html` / `gwtd1.html` 列表入口，不是19个独立详情链接；原始材料是 `application/vnd.orbbec.hr-panorama+json` 归档容器，含 jobs 与 `_evidence_pages`，不能称19份独立原始网页快照。导出的 requirements 原样带有收藏脚本等网页尾部文本，应称“归档原始字段全文”，而不是经人工逐字段清洗的纯招聘正文。

### 本报告新增的后验核验事实（不是补写原捕获）

本次读取当前本机 bundle `/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/hr-intelligence/bundles/2b49ecc4-42fe-45ae-80ac-26891f42ac6c`，重新完成以下匹配：

- 当前 manifest SHA-256 `5d446fe136a3fe2d3d1bb06873f9584e4a357f9546e9f66e686e14950dab98a3` 与 run9 原捕获一致。
- 当前 raw-evidence-index SHA-256 `48b0aed80bed8b2bf77512cfe7a09c6082b69a9db5d8178a09d37019ec771170`；normalized-jobs SHA-256 `89db450608da4aa40f06174071bc9350419a18f50a8fb7ed2c96e0ecb26f6a0d`。两者哈希是本次后验计算，run9 未捕获。
- 19 个 job_id 在当前规范层唯一匹配，标题、列表 URL、观察时间、公开 ID、原件 SHA 全部与原 evidence 对应字段相同；其 source_id 为 `7248fb5e-5fcc-54ab-bdda-1e4d95b9c55c`，此 source_id 同样是后验查得。
- 17 岗原件：`evidence/sha256/66/669c4995efe74f7adfc379aedcfe99f4fca8ebed2ad22214a08166ab45f49c25`，248,816 bytes，观察于 `2026-09-06T14:38:14.472238+00:00`，列表入口 gwtd.html。
- 2 岗原件：`evidence/sha256/2d/2d618a89dda144baf5bee896160b4243abef3eff188f38134508db3f31483820`，39,147 bytes，观察于 `2026-09-06T14:38:14.916310+00:00`，列表入口 gwtd1.html。
- 两原件当前字节 SHA 均等于各 locator 中的完整 digest。按公开 ID 匹配的 38 个 duty / requirements 字段与 run9 evidence 逐字一致。按捕获的 public_archive 拼接规则重建正文，得到 **16,969 字符**，SHA-256 `f090893f4afdb640f0b85b585490ccb0c0b1a9ff09f665f6ceb082131ae109e7`，与原运行 source_text_sha256 一致。

本节已经是一份新增的后验验证记录。若还需机器可读 sidecar，应新增独立文件、写入核查时间/核查者/原 evidence 哈希与当前 bundle 哈希，并标注 `verification_kind=post_hoc_local_archive_match`；不得修改原 evidence 或重新哈希原记录使其看似最初就捕获了这些字段。后验匹配不证明当前路径就是当时环境变量所指的唯一目录，也不证明当前官网有效或采集容器以外的网络原始字节完整性。

## 3. 审读身份与反馈来源自述

建议统一使用以下固定格式，而不是裸写“独立审读”：**审读者固定身份／是否参与准备及范围／实际审读材料范围／反馈是否经主执行者编辑／仅AI审查与人类验收状态**。参与情况未知时写未知，不因分代理运行而推定未参与。

| 对象 | 可证实身份与参与情况 | 反馈编辑与不可证实边界 | 建议表述 |
| --- | --- | --- | --- |
| run7 语义审读 | 当前导出的报告没有稳定代理身份和准备参与声明；报告自述不改输出/角色/知识、未调用网络或模型 | 仅凭该 Markdown 不能核实审读者真实人员身份、是否参与早期材料/方法/问题准备；也不能认定人类审查 | 将“独立人工语义审读”改为“独立 AI 语义审读；代理身份与准备参与情况未在当时报告记录，不构成人类 HR 签收” |
| run9 语义审读 | review-decision-1.review_record 标记 `c3_summary_review`，并声明读 stage2 与 all19 original jobs；最终报告是 AI 审读。该记录可提供逻辑身份，不构成模型实例/版本的完整身份认证 | 本次导出不足以证明该代理是否参与最初19岗选取、夹具或方法准备；应写“准备参与情况未记录”，不能写“完全独立盲评” | “由 AI 代理 c3_summary_review 对静态19岗与实际输出作分工审读；准备参与未记录；不是人类验收” |
| run9 第二轮反馈 | review-decision-2.review_record 明写 `main-agent checked and corrected overstated interpretation of coordinated preferred condition`；实际 text 收紧了“者优先”修饰范围问题 | 可证实反馈经过主执行代理检查和修正；无法仅凭当前最终文件重建编辑前全文、精确逐字 diff 或全部作者分工 | “第二轮提交的是独立 AI 审读后、经主执行代理核对修正的反馈；通过同一 work 的真实 inputs 提交”，不称“未经执行者编辑的独立反馈” |
| run11 语义审读 | 固定身份 `/root/b2b_workbench/review_b2b`，即本报告作者；本人参与七岗选择、原件校验、场景及人工审读依据准备，之后分开审读 stage1/3 | 本人生成 c3-run11-review-input.md；主执行者在实际 decision-1 末尾增加同 result ID 新修订、正文移除实现字段等要求，原反馈是其 text 的完整前缀；本人未直接改模型保存稿。不能称未参与准备的盲评 | “由参与七岗夹具准备的独立分工 AI 代理复审；提交反馈经主执行者附加交付要求；仅AI意见，人类验收待定” |

run9 的最终成果经过第二次真实模型修订，这不因反馈由执行者修正而失效；应纠正的是审读独立性程度和反馈作者自述。也不能把“review_record 存在”当作人类作者身份验证。

证据 README 现称“审读交接文件由独立审读者读完真实保存稿后生成”，范围过强：run9 首轮反馈时没有成功保存成果，且第二轮反馈经主执行者编辑。建议改成：

> 审读代理核对实际可见输出及原始材料后提供意见；存在成果时读取准确保存稿。主执行代理负责将审读反馈及必要交付要求提交到绑定 work/input revision 的交接文件；run9 第二轮明确包含执行者核对修正。最终 AI 审读报告确认实际新修订，不构成人类验收。各代理准备参与情况分别记录。

## 4. 模型/网关身份与默认配置的证据边界

run9/run11 捕获的非秘密 profile 均声明 model=`claude-opus-5`、protocol=`anthropic_messages_sse`、profile revision=`hr-opus5-20260911`、tokenizer=`conservative_utf8`、context window=131072、auth_scheme=bearer；response metadata 也自报模型名。`identity_assurance` 明确为 `provider_self_report_only`。

两次试验 profile 指纹均为 `9f96f33cff4fda4a1424f4b2e06e2861ae9c9ab44fd40247c850dea10e92084f`；source profile 非秘密指纹为 `13bd0c430a6b8a8c2bf87312fa073070d1f260703f8a688aac2c0d6410b4aa7a`。fingerprint_scope 排除 endpoint 与 credentials，source_configuration.status 为 not_recorded。因此这些记录能比对所声明的非秘密配置，**不能**认证官方模型、实际上游路由、网关部署版本或完整源配置身份，也不是独立供应商账单。

可保留指纹用于配置比对，不需把秘密端点、凭证或思考正文写进证据。建议称“经已配置网关真实调用、模型身份为网关自报 claude-opus-5”，不称“独立验证官方 Opus5 服务”。本次未联网探针；无须靠额外真实调用掩盖已有证据缺口。

成功 C3 的准确配置是：

| 运行 | source 声明 timeout | 实际试验 timeout | 每次输出上限 | 收尾 token 预留 | 结论覆盖 |
| --- | ---: | ---: | ---: | ---: | --- |
| run9 | 120s | 300s | 8,192 | 16,000 | 静态19岗，同work续作及反馈修订 |
| run11 | 120s | 300s | 16,384 | 32,768 | 七岗定向实体样本，同work续作及反馈修订 |

**没有成功 C3 记录证明默认 120s 与未作试验调整的输出/收尾配置能够完成相同业务闭环。** run9 的8,192是该 C3测试预算默认值，不应称为生产输出默认值；run11 的16,384为显式试验覆盖。source provider profile 不包含业务预算的 max_output_tokens，不能由它认证生产输出额度。不能把“没有改生产默认值”写成“生产默认值已验收”。

run11 单次自报输出最高13,368，含 thinking，观测最长传输245.098640秒；run9 最长传输117.803667秒。后者虽然低于120秒，仍是300秒配置下的观察，不能据此声称120秒配置已复跑成功。16k probe 的完整返回只能解释该请求在该参数下能返回，不替代 work 保存与质量审读。

## 5. 最小复算方法与建议改词落点

以下只读取导出文件；与运行源码快照中的估算/结算规则逐行比对后执行过同等断言，21个 attempt 均通过。无需加载服务或秘密配置：

```python
import json
from pathlib import Path
from fractions import Fraction
root = Path('docs/reviews/artifacts/2026-09-11-hr-c3')
for run in (9, 11):
    path = root / f'run-{run}'
    e = json.loads((path / 'evidence.json').read_text())
    q = {r['attempt_id']: r for r in json.loads((path / 'public-requests.json').read_text())}
    reported_sum = charged_sum = 0
    for a in e['stages'][-1]['attempts']:
        r = q[a['attempt_id']]
        payload = json.dumps({'messages': r['messages'], 'tools': r['tools']},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        floor = len(payload.encode()) + 256 + 32 * len(r['messages'])
        u = a['reply']['usage']
        reported = u['input_total'] + u['output_total']
        assert floor + r['max_output_tokens'] == a['reserved_tokens']
        assert max(reported, floor) == a['charged_tokens']
        assert floor > reported and a['usage_quality'] == 'estimated'
        reported_sum += reported
        charged_sum += a['charged_tokens']
    assert charged_sum == e['stages'][-1]['work']['budget']['charged_tokens']
    print(run, reported_sum, charged_sum, Fraction(charged_sum, reported_sum))
```

建议由文档拥有者后续修改维护性说明，原始 blob 保持不动：

- C汇总、continuation、证据 README 的“混合实报”改为本报告第1节明确算法与两套总数；continuation 的 run9 “1次成功摘要”改为2次。
- run7 “人工语义审读”按第3节改为 AI，未知身份/参与留空或标未记录；run9报告补固定逻辑代理名、准备参与未知及主执行者编辑反馈；run11保留参与准备声明并补反馈附加要求来源。
- README 的交接说明限定到实际流程，不声称每次都是独立审读者未经编辑生成、也不声称首轮必然已有保存稿。
- 来源说明区分 run9 原捕获、README 提供的路径及本次后验匹配；如追加 sidecar，明确后验，不回填原 evidence。
- 成功范围必须保留300秒和试验输出额度；明确未完成默认120秒及生产预算配置的 C3成功验收。
