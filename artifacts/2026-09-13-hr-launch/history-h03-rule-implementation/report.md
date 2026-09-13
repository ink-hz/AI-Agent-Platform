# HR 草稿授权与要求校准实施

按根已审阅采纳的三处建议实施：role 明确当前草稿授权与旧来源真实性/长期标准确认分开；职责、范围或表达调整不自动加筛选门槛；requirement-calibration revision3 补充能力、既往经历和组织权限分别论证、保留替代证据。没有六类产品或 H03 答案硬编码，没有改变工具权限或运行代码。

两份根入口各补一段一致说明。附件 fence 段由 launch agent 先在 a608970bb2d1283e16d3f787101fe3555d674fda 提交冻结，本次未重写。provenance.json 只更新被修改方法的SHA，并新增本地适配记录、此前SHA和审读引用；上游仓库/commit身份不变，不把本地新增说成上游原文。

本次行为失败依据是已冻结 H03 v5 实际模型输出和专业审读。没有新造只匹配措辞的测试，也没有把工程测试 green 称为模型质量修复通过。未调用模型，旧 run3/run4/run5及审读保持原字节。

## 验证与实际失败

- 第一轮现有 `tests/test_hr_agent_knowledge_releases.py`：1 failed,3 passed；方法字节改变但 provenance 仍为旧SHA，构建正确报 source digest mismatch。独立 build-1 同样失败。原始日志与原始前/后源字节保留。build-1外层脚本在子进程退出1后assert终止，未写原计划aggregate记录；单独build-1-command.json如实补记已执行命令和子进程退出，不伪装为当时已生成的汇总。
- 同步必要provenance后，同一现有套件：**4 passed in1.20s**。测试覆盖固定旧知识继续读取、缺失旧版本拒绝、非法指针拒绝、实际知识可复现构建与篡改拒绝；其中数据库用既有真实本地fixture，没有新增替身。
- 实际 build_knowledge_release 连续两次：exit0，均 `hr-4d13f741dad2c1eeddf3e79e`，9条资源。构建自身核验来源SHA/方法frontmatter/身份，现有测试逐项读取7份方法且核验不可变性。没有单独名为selfcheck的工具；不虚报另一个未执行命令。
- 定向git diff --check：exit0。

所有执行参数、退出、日志在本目录的 *command.json/*commands.json（实际存在者）与日志；最终完整patch另存final-implementation.patch，早期implementation.patch不覆写。before/after保存五个修改文件，verification-source保存现有测试与builder，knowledge-build保存实际新包。fingerprints.json绑定这些字节。

知识包仅在本地证据目录构建，未安装到生产或替换运行中任务。新内容不能改写旧输入绑定；根冻结代码后另行安排针对实际修复的真实模型验证。当前结论仅为规则已实施、知识打包与相关工程检查通过，H03专业质量仍未通过。
