# 岗位列表合并冲突处理

仅融合HrPositionIndex.tsx与HrPositionIndex.test.tsx，并git add标记resolved；没有commit、浏览器或生产操作。自动合并的HrPositionWorkflow、HrWorkspacePage及其他源文件未修改。双方提交身份和原stage1/2/3字节保存在merge-inputs.json及stage-*文件。

以ours完整岗位/草稿页面为基础：保留官网与内部分类、草稿确认/合并/忽略、明确合并目标、新建对话、失败重试/跨remount幂等、Hannah入口和主对话选择。原11个测试及全部业务断言原样保留。

承接线上行为：所有归档岗位仍有可读工作流链接；单独显示内部进行中/草案/已归档，不与官网在招/下线混淆；状态与原有名称/编号/部门/地点搜索联合筛选；增加JD/JR、候选人、面试、复盘及查看工作流入口。还承接线上重复分页游标拒绝与异步取消保护，避免把不完整列表当作完整结果。草稿操作不会被正式岗位筛选隐藏。

线上归档测试的链接、非旧picker、已归档和JD/JR断言均迁入完整夹具，补齐listDrafts及完整岗位字段；另新增状态联合搜索和重复游标错误用例。归档可读同时保持“在主对话中继续”只允许active的既有写入边界。

- 20260913T021122369118Z-red.log：ours实现+新增测试，3failed/11passed，exit1；对应目录保存当时完整源码和相对ours patch。
- 20260913T021214317179Z-green.log：Index14、Workflow2、WorkspacePage13，共29passed，exit0；对应目录保存最终完整源码和patch。仅出现既有jsdom Window.scrollTo未实现提示，不作为浏览器验收。
- 20260913T021307227447Z-typescript.log：npx tsc -b --pretty false，exit0。
- resolved-index.json：仅两个文件已标记resolved，暂存字节与最终已测工作树一致；diff-check exit0。

本结果只证明合并后的相关前端组件和类型检查通过，不代表生产历史业务场景或正式发布已验收。
