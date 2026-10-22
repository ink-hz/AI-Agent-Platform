# SDK R0-a 离线来源对账

从 `backend/` 运行，使用现有 Python 3.11，不安装额外依赖：

```bash
/opt/homebrew/bin/python3.11 -m app.sdk_workbench.source_reconcile_cli --candidates A.json --csv derived.csv --workbook original.xlsx --output report.json
```

四个路径均必填；只有显式 `--existing-export scoped-export.json` 才读取既有映射。输入只读，输出必须尚不存在，父目录须已存在；拒绝已有文件、符号链接及输入的同路径/硬链接别名。输出以独占创建方式写入，不覆盖竞争期间出现的文件。所有输入路径来自命令行，不从环境、配置或默认目录发现来源。

退出 0 表示生成提案报告，退出 2 表示参数、输入或文件错误。正常运行 stdout/stderr 均为空；`--help` 返回 0 并向 stdout 打印帮助。错误 stderr 仅含单行 JSON `{"error": "错误码", "path": "字段路径或固定参数名"}`，不含原文、参数值、文件绝对路径或 traceback。缺少/未知参数（含参数缩写）为 `invalid_arguments/arguments`；读写失败为 `file_io/explicit_files`；已有输出为 `output_exists/output`，输入别名为 `output_is_input/output`。输入结构错误沿用核心 `ReconcileError` 的错误码与字段路径，CSV 解析器错误码原样传递。意外核心代码缺陷保持可见，不用全局异常兜底制造输入错误。

此工具不联网、不连数据库，不初始化应用，不创建、批准或导入业务对象。

候选须为 schema `0.1.0`、协议 `A-wave-01-scope-v0.1` 的准备态包。原 XLSX 只校验字节摘要，不重新抽取；候选检查结构、来源键、原值形态、坐标、合并解释、日期与控制数，原单元格真实性依赖 A 线提取和独立校验。四字段来源键为 `file_sha256/sheet_name/row_start/row_end`。全部十列、记录类别及真实合并栏目解释共同生成比较键；只在比较层统一换行、空值展示和明确的 E/F 日期展示，不改原文、不按标题或行号匹配。重复、歧义及无法匹配进入待核，不能挑第一条自动关联。

报告 schema 为 `r0a-report-1`，规则版本为 `r0a-compare-1`。`input_sha256` 保存全部输入摘要；`source_records` 保存原候选 raw/interpreted；`csv_records` 保留摘要、逻辑记录号、物理起始行及十列原文。`proposal_id` 仅是提案身份，不是业务工作 ID；`alias_proposals` 永远 `not_reviewed`，`production_import_eligible` 永远 `false`。报告无生成时间，相同字节输入得到相同报告。

无对象导出时 `existing_objects.collection_state` 为 `not_collected`；历史、部分或冲突导出为 `unknown`；合法完整当前导出仅按声明作用域为 `observed`，未覆盖记录仍为 `unknown`。`observed` 是导出方的快照声明，不是在线验证或批准。没有导出时不根据历史 #3–#69 推断对象不存在或新增。

可选导出 schema 为 `r0a-existing-1`，必需 `instance_id`、精确 `zentao_version`、带时区的 `exported_at`、`evidence_state`（`current/historical`）、`complete`、`scope`（`csv_sha256/native_object_type=story/native_ids`）和 `rows`（`logical_record_number/plan_import_id/plan_row_id/native_id/native_version/native_state`）。主键均保留非空字符串原格式；`instance_id` 应来自导出方受控注册，本工具只检查字段，不核验实例。`complete` 只声明该 scope 内完整。导出由另行授权流程提供，本工具不采集；合成导出测试不能补齐真实对象证据。

真实基线为 69 条来源记录：67 个任务、2 个栏目，17 个栏目锚点；得到 69 个候选关系时仍是 67 个任务加 2 个栏目关系，不能称已批准映射或新增需求。缺真实导出时对象状态保持 `not_collected`。迁移 117/121 是一对一历史模型，报告显式保留兼容问题；本包不调整约束、不生成拆分工作 ID、不增加原始任务控制数。正式交接、现网采集、导入、UI/HTTP/数据库/CI/发布不在本包。

定向检查（排除与本包无关的 pytest asyncio 配置，不改平台配置）：

```bash
/opt/homebrew/bin/python3.11 -m pytest -c /dev/null -o pythonpath=. tests/test_sdk_plan_csv.py tests/test_sdk_source_reconcile.py -q -p no:cacheprovider
```

真实来源例默认明确 skip，须显式提供三个获准的来源路径，另行运行至通过；测试在每次运行前后核输入摘要，并比较两次输出稳定性：

```bash
SDK_R0A_CANDIDATES='/Users/neo/Developer/work/AI-SDK-Platform/artifacts/sdk-platform/analysis/q4-source-candidates.json' SDK_R0A_CSV='/Users/neo/Developer/work/AI-SDK-Platform/OrbbecSDK  v2 2026年任务计划.csv' SDK_R0A_WORKBOOK='/Users/neo/Developer/work/AI-SDK-Platform/OrbbecSDK  v2 2026年任务计划.xlsx' /opt/homebrew/bin/python3.11 -m pytest -c /dev/null -o pythonpath=. tests/test_sdk_source_reconcile.py::test_real_candidate_csv_end_to_end_if_explicit_paths -q -p no:cacheprovider
```

测试使用既定真实输入摘要和控制数；来源改变时保留差异并复核，不改预期掩盖。实际 CLI 使用相同显式输入参数，输出选择新的 `/tmp` 路径，不在应用树复制原资料。不运行全平台或 live 写测试。

回退：停止使用新 CLI 即可，无数据库或业务数据回滚。保留报告、输入摘要和失败；隔离分支的四个新增文件（含已完成的核心模块）可由主 agent 按正常 Git 审查流程撤回，不能 reset 主工作树或删除用户材料。输出路径写失败可能留下不完整新文件，应保留为失败证据并换新路径重跑，不能拿它作成功报告。源码工作完成不等于部署或业务验收。
