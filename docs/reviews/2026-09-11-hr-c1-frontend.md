# C1 批量简历材料面板

## 实施前设计

新增独立 `hrLoopCandidatesApi.ts`、`HrLoopCandidatesPanel.tsx` 与人工核对表单组件；工作台仅提供入口和展开面板，不替换一般材料上传表单。接口遵守已有身份Cookie、no-store、CSRF和调用方稳定幂等键，准确发送row_version与result_ref。

复用attachmentApi上传/完成/元数据轮询。多文件分别保留上传阶段与错误；失败文件可独立恢复，其他就绪文件可先登记；create_batch只包含当前ready且未登记的attachment_id。面板不提前调用模型或直接parse，批次登记后的个人材料属性与处理门控交由服务端负责。

最近批次可重新打开并轮询逐文件状态，展示解析覆盖、未读区间、研究草稿、失败阶段。processing_not_authorized明确显示“处理权限待开通”，不把材料保存、解析或模型回答冒充人工建档。等待用户/预算时可进入实际work继续；失败项携带准确阶段与row_version重试。

人工核对使用姓名、已审阅摘要两个空白字段；模型profile_body仅作为旁边可读草稿，不自动填入表单。用户选择新建，或从自己当前可读候选人列表明确选取已有身份；同名不触发关联。关联仅追加该文件的核对记录，不改已有主档摘要。存在解析/阅读缺口时必须明确勾选已核对限制。原件通过现有票据下载接口读取。

仅webui与本记录；先失败测试再最小实现。测试使用合成文件与模拟HTTP响应，不连接生产、不处理真实个人材料、不调用模型。后端接口/数据库验收由root统一报告。

## 实现与验证

实现文件：`webui/src/hrLoopCandidatesApi.ts`、`webui/src/workspaces/hr/HrLoopCandidatesPanel.tsx`、`HrLoopCandidateReview.tsx` 和局部CSS；`HrLoopWorkspace.tsx`仅增加入口、参数与卸载。每个文件保留上传阶段；3个并发上传槽，各失败项可独立重试。待检查附件只轮询元数据，未重复传输正文；最近批次读取服务端持久化状态。确认失败保留人工表单和准确引用，同一请求内容重试保留幂等键。

首次API/组件测试在新增模块不存在时失败；工作台入口测试在按钮不存在时失败。额外反例捕获了React StrictMode清理后复用已中止上传signal，以及原件410时仍显示旧解析备注，均已最小修复并复测。401/403清除私有材料和表单，延迟返回不能恢复；原件404/410清除该文件正文、引用、备注并显示不可用。readonly阻止上传、重试和确认。默认处理权限缺失只显示待开通，不伪造profile或candidate。

本地验证（2026-09-11）：

- `cd webui && npm test -- src/hrLoopCandidatesApi.test.ts src/workspaces/hr/HrLoopCandidatesPanel.test.tsx src/workspaces/hr/HrLoopWorkspace.test.tsx src/hrLoopApi.test.ts src/workspaces/hr/HrLoopIntelligencePicker.test.tsx`：5个文件、53个测试通过（本任务2个API边界、13个面板和1个入口新增用例）。
- `cd webui && ./node_modules/.bin/tsc -b`：通过。
- 覆盖人工空白字段、限制勾选、同名显式关联、exact result_ref/row_version、网络失败与冲突幂等重试、ready-only、pending metadata、独立失败阶段重试、票据原件、预算暂停进入实际work、权限清理和延迟响应。

这些是前端组件与请求边界的工程合成夹具测试：真实组件和API封装运行，网络/上传提供方边界使用mock，不能称为真实HTTP/数据库、模型或业务质量验收。真实HTTP与PG由后端报告单独记录。本子任务未运行浏览器文件选择/布局验收，未访问生产、网络或真实个人材料，未部署。未登记上传在关闭面板后不恢复本地File对象；已登记批次通过服务端列表恢复。批次列表最多50条，显式候选人列表最多100条，遵守当前后端有限列表契约；大规模搜索/分页后续补充。默认服务仍需组织明确配置获准的处理服务后才能生成研究草稿。
