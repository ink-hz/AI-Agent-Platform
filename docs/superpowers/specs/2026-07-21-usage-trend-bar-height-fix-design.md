# Usage Trend Bar Height Fix Design

- 日期：2026-07-21
- 状态：已确认
- 范围：Agent Overview 的 `7-Day Trend` 柱状图

## 1. 问题与根因

`GET /api/fleet/overview` 正常返回七个非零日数据，当前总和为 95。`UsageTrend` 也会为每个柱子生成正确的百分比内联高度。

空白来自 CSS 高度解析：`.trend-track` 只有 `min-height: 132px`，没有确定的 `height`；其空内容子元素 `<i>` 使用百分比高度。在这种父级高度为 `auto` 的布局下，百分比高度不能可靠解析，柱子最终折叠为零高度。数字和日期不依赖柱高，因此仍能显示。

## 2. 方案

采用最小修复：为 `.trend-track` 增加 `height: 100%`，继续保留 `min-height: 132px`。

- `height: 100%` 让轨道填满 `trend-column` 中确定尺寸的 `1fr` 行，为子柱子的百分比高度提供可计算基准。
- `min-height: 132px` 保留当前最小视觉高度。
- 不修改 API、统计口径、日期、总数、React 比例算法或空状态逻辑。

未采用的方案：

- 固定 `height: 132px`：能修复，但不能利用网格行的剩余高度。
- `transform: scaleY()`：可以避开百分比高度，但会扩大组件与测试改动，不符合本次最小范围。

## 3. 测试与上线

- 在 `styles.test.ts` 增加回归契约，要求 `.trend-track` 同时包含 `height: 100%` 和 `min-height: 132px`。
- 先确认测试在旧 CSS 上失败，再实施一行生产 CSS 修复。
- 运行完整前端测试、TypeScript/Vite 构建和生产依赖审计。
- 合并到 `master` 后只重启 Platform，九个 MetaBot PID 必须保持不变。
- 线上 CSS 必须包含确定高度规则；Fleet API 仍返回七个非零日数据且总和与 7-Day Trend 汇总一致。

## 4. 验收标准

1. 七根蓝色柱子按日对话量比例显示，07/17 的 33 为最高柱。
2. 7-Day Trend 汇总仍为七日数据之和，当前为 95。
3. 零数据时的空状态逻辑保持不变。
4. Agent Overview 其他视觉、数据和响应式行为不变。
