# 2026 Q4 SDK 需求首版上线核对

## 范围与入口

- 唯一来源：`AI-SDK-Platform/OrbbecSDK  v2 2026年任务计划.xlsx` 的“2026年Q4上线版本”工作表；原件 SHA-256 `402030f9a76fb966b23fc6c9ad843d5f9abd85bcdce1d685835440ae6c51b7d0`。
- 原表有 67 个任务行、16 个有任务的栏目、8 个超链接。原表没有提出人、评审人和完整验收标准。计划开始/结束时间只在 6/5 行填写，版本在 24 行填写；这些空白没有被推算。
- 禅道产品 `OpenOrbbecSDK` 的现有需求 #3–#69 与 67 行一一对应。没有重新创建需求。#1、#2 是历史测试记录，没有加入 Q4 计划。
- 钉钉登录后访问 [Q4 原生计划](https://agent.orbbec.com.cn/sdk/index.php?m=productplan&f=view&planID=1)，或从 [禅道入口](https://agent.orbbec.com.cn/sdk/) 进入产品 `OpenOrbbecSDK` → 计划 → `2026年Q4上线版本`。此计划只是来源范围，不代表发布承诺或开发完成。

## 已在生产完成

2026-10-22 15:40 CST 先对线上 67 条做只读预检，核对固定工作簿、行→ID 清单、标题、来源、产品、负责人、评审状态；随后即时备份禅道数据库与数据目录。备份仅存服务器私有目录 `/opt/orbbec-agent-platform/staging/sdk-q4-operational-20261022/`，其中 `zentao-before.sql`、`zentao-data-before.tar`、`preflight.json` 均为 600 权限。线上禅道 `/apps/zentao/VERSION` 为 `22.6`，运行镜像 ID 为 `sha256:1deeb004b41a691ef45fd8670a4794ef446bb3c0362e402a2e87a705cf26d5ce`。

隔离生产副本先验证原生创建计划/模块、逐条关联、撤回统一评审、草稿、从草稿重新提交评审和幂等重跑。相同工具在生产按 `structure`、`link`、`draft` 三阶段执行，每条写后回读，最后 `verify` 结果：67/67 条关联计划、16/16 个需求模块存在、67/67 条为草稿且无当前评审人、受保护的标题/正文/验收栏/优先级/版本/来源/责任人/指派时间均无差异。独立 SQL 复核得到同样的 67 条草稿、67 条计划关联、16 个模块和 0 条当前版本评审记录。生产没有改 Nginx、平台镜像或行政服务。

生产写入实际使用的 `sdk_q4_operationalize.executed.py` SHA-256 是 `3e701305c35dd2e10d49f7de45674f97e7a07eb5a3f34b41faf0d06db13c99f3`；代码审查后补强的 `sdk_q4_operationalize.py` SHA-256 是 `176405c3f4bb7fdbac1fa445d60102b264623259e171a60ab46a708dde0f7b06`，以只读 `verify` 再验生产，核对完整原表正文/栏目和计划精确成员集合，结果仍是 67/67。私有目录还保存 `audit-report.json`、`structure-report.json`、`link-report.json`、`draft-report.json`、`verify-report.json`、`verify-report-reviewed.json` 以及 `source-gap-register.json`。后者按 67 个 Excel 行分别列 A:J 原值和单元格、8 个原链接、禅道 ID、当前账号对应及待业务决定事项；SHA-256 为 `e4407cd7f26dc638be7abcb97a3428d472404479a7c4e18ff44b7411ea11f933`。这些来源数据仅保存在私有目录，没有上传公开页面。

线上即时快照中 54 条已指派给与原表姓名完全一致的禅道账号，13 条未指派。隔离副本建立时只有 53 条；生产在预检前已有人将 #3 指派给“周公”，本次操作保留了该人工变更。13 条待业务核定的原表责任人如下：

| 需求 ID | 原 Excel 行 | 原责任人 |
| --- | ---: | --- |
| #9 | 8 | 小树 |
| #11 | 10 | 修罗 |
| #17 | 16 | 小树 |
| #19 | 18 | 托勒密/石头 |
| #25、#28、#29 | 26、29、30 | 夜枫/托勒密 |
| #31、#32、#33 | 33、34、35 | 朝小树/劳伦斯 |
| #37 | 40 | 戴维？ |
| #57、#61 | 73、77 | 亚连/沈兰舟 |

简称、多人和问号都不能直接等同于一个已接受工作的个人。原表没有评审人，所以将先前 67 条统一指向苍渊的“评审中”撤回为草稿，等待业务逐条指定真实评审人；未伪造提出人或验收结论。

## 首批五条真实评审

从原表 A2:J6 的逐格复核卡准备了首批讨论点。原表事实已在禅道需求正文；下列问题是待业务确认，不是已批准的需求或验收标准。

1. [#3 MB2620 项目定制](https://agent.orbbec.com.cn/sdk/index.php?m=story&f=view&storyID=3)：明确客户场景、接口与交付件，核定目标版本；原表“10 月 12 日提测版本 1”需要测试报告和接收证据。
2. [#4 IMU 全局时间戳优化](https://agent.orbbec.com.cn/sdk/index.php?m=story&f=view&storyID=4)：确认 Diff_SG 负数的复现条件、时间基准、误差阈值、受影响设备，以及固件改造和联调依赖。
3. [#5 GMSL IMU 延迟问题](https://agent.orbbec.com.cn/sdk/index.php?m=story&f=view&storyID=5)：量化延迟和抖动基线/目标，核定 I2C 与驱动限制；原表备注称“初步难以支持”，需要决定方案或暂缓条件。
4. [#6 benchmark 配置能力](https://agent.orbbec.com.cn/sdk/index.php?m=story&f=view&storyID=6)：界定配置格式、开流/滤波范围和错误处理；原表称已优化 CPU 占用率获取方式，这与本项配置能力不是同一验收。
5. [#7 四段式版本号](https://agent.orbbec.com.cn/sdk/index.php?m=story&f=view&storyID=7)：定义四段含义、SDK/API/制品兼容范围和验收用例；原表没有本行的起止日期。

## 使用方法与尚未完成的业务判断

负责人打开 Q4 计划中的草稿，核对原 Excel 行与来源备注，补充场景、设备/平台、可验证的验收标准、目标版本和依赖，再确认提出人及实际评审人，提交禅道原生评审。评审通过后再拆任务、关联测试和发布；原表“开发中”只是旧计划的来源文字，不自动变成禅道开发或验收状态。13 条未指派的需求先确认一个承接账号；多人合作应通过任务或相关需求关系表达，不把一个人随意当成全部责任人。

服务器内的禅道个人会话已验证 Q4 计划列表/详情和首五条需求页均返回 200 且显示草稿与 Q4 关联；公网未登录访问 `/sdk/` 返回 302 登录跳转。员工钉钉浏览器中的实际编辑、提交评审和后续任务操作仍需由业务人员亲自验收。自动化无法代替他们接受任务或签署需求。

代码核对：`ruff check` 通过；来源解析与写入保护相关 17 个测试通过；隔离副本完成 67 条全量场景及重复执行零新增；生产最终只读 `verify` 为 `source_rows=67, plan_id=1, modules_present=16, stories_linked=67, stories_draft=67, written=0`。
