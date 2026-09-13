# 既有前端失败与日志缺失追溯

基线为 b184b57ec09828033ef127ddf34dd748794325dd。原审计证据、styles.css和styles.test.ts与该基线逐字相同，核对见 baseline-byte-check.json。本轮只读检查并运行原三个指定用例，**仍3 failed**；没有修改源码、测试或旧artifact。不能把“既有”写成“可以忽略”。

## 三项失败的准确来源与当前判断

原日志：`artifacts/2026-09-11-hr-d-validation/frontend-known-baseline.log`（独立styles34项，3失败）；E封包原日志：`artifacts/2026-09-11-hr-e-review/final/frontend-repository-verified.log`（全仓1193 passed / 3 failed / 2 skipped）。E的 `final/styles-baseline.json` 另对b974a87声明字节相同。b184是继承这些结果的上线基线，不是三项最早首次产生的提交。

| 原准确用例（均在webui/src/styles.test.ts） | 当前复现及源码事实 | 处置判断 |
| --- | --- | --- |
| Executive Operations visual contract > never renders visible text below the approved minimum（第84行，失败断言第91行） | 最小阈值11.5px，实际发现11px；styles.css第2272/2286行会话侧栏标题和元信息、第2560行知识面板页眉、第2870行岗位详情按钮。 | 是现存字体契约失败。知识面板“HR参考知识”和岗位picker“查看JD/JR”有真实TSX，HrWorkspacePage实际接入；因此不能简单视为死代码。浏览器计算字体、可读性与缩放尚未验收；本次不是证明所有声明都在当前页面可见。建议修正实际字体或明确重新评审产品最小值，不能降低断言求绿。 |
| Executive Operations visual contract > renders HR as a calm full-height recruiting workspace instead of a card dashboard（第95行，失败第100行） | 测试要求字面background:#eef1f4；现有shell是多个radial-gradient与linear-gradient，实际HrWorkspaceShell仍使用该类。 | 当前外观与旧纯色契约不符，但此断言不测布局、对比度或响应式。不能仅凭失败认定渐变损坏UI，也不能认定新设计已获验收。测试后续还有grid和移动端断言，首个失败使其未执行。须结合当前设计目标评审并保留实际结构验证，不能删整条测试。 |
| Executive Operations visual contract > keeps the position conversation primary with on-demand responsive controls（第126行，失败第133行） | 测试找248px minmax(0,1fr)，现有CSS使用var(--hr-sidebar-width,248px) minmax(0,1fr)，shell变量默认248px。当前src/**/*.tsx未发现hr-position-chat-surface/旧hr-position-workspace使用。 | 首个断言是字面实现差异，默认值等价，并有退役页面选择器迹象；不构成已证明当前岗位工作流响应式缺陷。不能据此前移所有后续抽屉/移动端断言为通过。应将保留的产品能力映射到当前HrPositionWorkflow等可达组件后维护测试，而非回退CSS变量或整项豁免。 |

本次最小命令与cwd、HEAD、源码SHA、起止时间和退出码在 current-three-1.json；原始输出 current-three-1.log。执行 `npm test -- --run src/styles.test.ts -t '<三个原用例的完整名称以|连接>'`，测试进程exit1、3failed，31skipped仅为-t未选中的用例，不是新增源码skip，也未执行全前端。运行前后源文件SHA一致。对应源码、package与lockfile快照在 source/。这些是CSS源码契约测试，不是浏览器计算样式，也不是组件真实视觉渲染验收。

## 八份缺失日志

准确留存声明为 `artifacts/2026-09-11-hr-e-review/evidence-retention-gap.md`。该文件SHA为621fee5382975fbabfc81fffc4b1f886d5bef085dad0fb66e8483fa7f257956a，和b184一致。声明只保存以下八个basename，**没有保存各文件原绝对路径、完整命令、源码状态或各自失败用例**；不能依据邻近final文件目录臆造完整原路径或逐次结果。

1. frontend-tests-corrected-targeted.log
2. frontend-p0-rewrite.log
3. frontend-p0-rewrite-attempt-2.log
4. frontend-p0-rewrite-attempt-3.log
5. frontend-p0-rewrite-green.log
6. frontend-candidate-corrected.log
7. frontend-corrected-related.log
8. frontend-corrected-broad.log

声明明确：尚未跟踪/提交的中间试跑日志整理时误删，没移到其他目录，无可恢复原件；包含fixture改写期间失败和中间通过，不能据文件名green补写通过数字。b184树里不存在这些原件；保留的 `frontend-corrected-broad-final.log`、`frontend-corrected-related-final.log` 等均不是其原字节替代品。

可确证的相关工作是 `final/frontend-baseline-review.md` 所记旧HrCandidateWorkspace（旧startTask契约）和P0 combined（旧position-package流程）测试修订。现存原始 `final/frontend.log` 的11failed/305passed、三基线目标11failed/17passed、最终相关317项/目标29项及构建日志支持各自报告。**无法将八个缺失文件逐一映射到其中某个命令或某条用例**；任何这样的精确归因都超出当前原证据。11项旧候选人测试失配与这里3项styles失败也是两组不同问题。

当前结论仍为证据留存缺口，不宣称恢复、不重新试跑冒充旧输出、不将缺失日志计入通过数。后续新组件回归与最终扫码登录后的浏览器验收可以提供当前证据，无法补回当时过程证据。
