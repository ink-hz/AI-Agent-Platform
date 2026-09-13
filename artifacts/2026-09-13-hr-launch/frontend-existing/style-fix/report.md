# 三项既有style失败最小修复

仅修改styles.css、styles.test.ts及授权的三个HR CSS。没有改组件行为、布局、颜色或响应式规则。生产CSS变动仅16处字号：global4处11→12，HrLoop2处11→12，company1处11→12，research8处10/11→12及1处.8em→12px（移动端正文14px下原.8em只有11.2px）。原11.5px下限完整保留，并对三个HR CSS新增相同下限检查。

原三个失败RED为上级目录current-three-1.log及原源码快照；新增HR字号约束先运行red-hr-fonts-1，三个文件均失败，再改字号。旧外观断言的修改属于已存在CSS的契约校正，不称作新实现TDD行为RED。

## 九处旧断言逐项替换

| 旧断言 | 新断言及继续保护的性质 |
| --- | --- |
| shell纯色#eef1f4 | 核验现有radial与linear渐变（含浅色终点）并核验sidebar变量248px；保留100dvh/width/padding/topbar隐藏，未将渐变改回纯色。 |
| topbar blur20/saturate135 | 现有blur22/saturate138，仍验证玻璃顶栏效果。 |
| hr-bot grid固定268px | var(--hr-sidebar-width,248px)+minmax(0,1fr)，与shell248定义共同核对；新增移动端display:block。 |
| main纯色#f7f8fa | 透明承接shell背景；新增min-height:0及scroll-region overflow-y:auto，保持可收缩与滚动性质。 |
| conversation-page max-width960 | 当前流式max-width:none+width100%+左右28px内距；新增手机14px内距，仍约束阅读容器空间。未自行改回固定宽。 |
| composer bottom0 | 当前relative+flex:0 0 auto+bottom:auto，在固定高度工作区中保留不收缩输入区，配合独立消息滚动；未改变定位。 |
| textarea min-height320..420 | 当前min96/max220/overflow-y:auto，验证最小输入高度、最大占用与内容滚动。 |
| attachments上边线 | 当前grid-column1/grid-row2/min-width0，继续约束附件独立区域与窄屏收缩，未删除附件承重检查。 |
| 旧position chat grid固定248 | 当前变量默认248+shell定义，新增手机display:block；原compact desktop、抽屉固定定位/宽高/滚动、手机底部及全宽约束全部保留。 |

第一条大测试完整检查后一次替换了上述8项过时预期；第二条替换1项。其余断言没有删除。旧position-workspace/chat-surface类当前TSX不再使用，测试加注释明确它是保留的旧CSS契约，不能拿这条通过替代当前HrPositionWorkflow交互验收。shell、对话、知识面板、岗位picker、HR Loop、研究/情报等现行组件另跑定向测试。

## 最终验证

- green-styles-1：styles完整37 passed，无skip。
- green-components-1：实际匹配7文件59 passed，无skip（原命令多传了一个不存在的hrCompanyIntelligence.test.ts路径，Vitest忽略；不声称运行该文件）。
- green-intelligence-components-1：随后补跑真正使用情报CSS的Topic/Panorama组件，实际文件与数量见原日志。
- green-tsc-1：npx tsc -b，exit0。

每次准确命令、退出码、HEAD、源码SHA和前后未变核对在独立command.json；五个修改源文件在各次source/完整保存。源码无新增skip。没有浏览器/截图/生产视觉验收；最终仍等待扫码登录后的关键页面验收。旧triage与八份日志缺失声明未修改，本次新通过不冒充恢复旧日志。
