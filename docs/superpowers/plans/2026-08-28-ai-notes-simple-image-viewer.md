# AI 工程笔记简化图片查看器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task by task. Use `test-driven-development` for every behavior change, `requesting-code-review` before merge, and `verification-before-completion` before release. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复正文 Mermaid 图片宽度坍缩，并把全屏查看器简化为点击退出、滚轮缩放和放大后拖动。

**Architecture:** 保留 Mermaid 严格渲染、SVG 消毒、原生 dialog、焦点恢复和安全降级。正文触发器改为确定宽度；全屏查看器移除显式缩放工具栏，由图片点击、空白点击、滚轮和指针拖动直接控制。

**Tech Stack:** React 19、TypeScript、CSS、Vitest、jsdom、Mermaid 11.17.2。

## Global Constraints

- 正文桌面最大高度为 `min(720px, 70vh)`，手机为 `68svh`。
- 全屏缩放范围为 `1..4`，滚轮步长为 `0.25`。
- 点击全屏图片、空白区域、关闭按钮或按 `Esc` 都退出。
- 拖动结束不得触发误关闭。
- 不显示百分比、放大、缩小或恢复控件。
- 不降低 Mermaid、DOMPurify 或 CSP 安全规则。

---

### Task 1: 修复正文图片宽度

**Files:**
- Modify: `webui/src/styles.test.ts`
- Modify: `webui/src/styles.css`

- [ ] **Step 1: 写失败测试**

把 AI 笔记样式合同增加为：`.mermaid-diagram-trigger` 必须包含 `display: block` 与 `width: 100%`；正文直接子图片必须包含 `width: 100%`。测试同时保留桌面、手机、打印和 lightbox 独立高度合同。

- [ ] **Step 2: 运行 RED**

Run: `cd webui && npm test -- --run src/styles.test.ts`

Expected: 因触发器仍为 `inline-block` 且无确定宽度失败。

- [ ] **Step 3: 最小修复**

将触发器改为块级全宽容器，将正文图片宽度改为 `100%`，继续使用 `max-width: 100%`、视口最大高度与 `object-fit: contain`。

- [ ] **Step 4: 运行 GREEN**

Run: `cd webui && npm test -- --run src/styles.test.ts`

Expected: 样式合同全部通过。

---

### Task 2: 简化全屏交互

**Files:**
- Modify: `webui/src/components/ai-notes/MermaidLightbox.test.tsx`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.test.tsx`
- Modify: `webui/src/components/ai-notes/MermaidLightbox.tsx`
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

- [ ] **Step 1: 写失败测试**

测试必须覆盖：工具栏不再包含百分比、放大、缩小、恢复；点击图片、空白和关闭按钮分别调用 `onClose`；`cancel` 被接管；滚轮上下按 `0.25` 改变比例并限制在 `1..4`；缩回 `1` 清除位移；`1` 时不可拖，放大后可拖；发生实际拖动后的合成 click 不关闭。

- [ ] **Step 2: 运行 RED**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidLightbox.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx src/styles.test.ts`

Expected: 旧工具栏与缺失的滚轮、点击退出行为造成失败。

- [ ] **Step 3: 最小实现**

删除 `changeScale` 按钮路径和比例输出；新增 `onWheel`，根据 `deltaY` 以 `0.25` 更新比例；图片与空白点击调用关闭；保留右上角关闭按钮。指针移动超过 4px 后标记为拖动，消费随后一次 click。比例回到 `1` 时重置位移。

- [ ] **Step 4: 运行 GREEN**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidLightbox.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx src/styles.test.ts`

Expected: 三个文件全部通过。

- [ ] **Step 5: 提交**

提交组件、样式及测试，提交信息为 `fix: restore simple Mermaid image viewing`。

---

### Task 3: 审查、验证与上线

**Files:**
- Verify all modified files and production assets.

- [ ] **Step 1: 定向与全量验证**

Run:

```bash
cd webui
npm test -- --run src/components/ai-notes/MermaidLightbox.test.tsx src/components/ai-notes/MermaidDiagram.test.tsx src/components/ai-notes/MermaidDiagram.integration.test.tsx src/styles.test.ts
npm test -- --run
npm run build
```

- [ ] **Step 2: 独立代码审查**

按 `requesting-code-review` 检查正文可见性、CSS 级联、点击退出、滚轮边界、拖动 click 抑制、焦点和安全降级。Critical/Important 清零才继续。

- [ ] **Step 3: 合并与部署**

快进合并到 `master`，推送 `origin/master`，从干净工作树运行云端部署脚本。发布输出的 release 必须等于部署前 `git rev-parse HEAD`。

- [ ] **Step 4: 生产验证**

确认公网健康为 `200`，远端 current release 正确，六个核心容器 healthy，公网 CSS 包含全宽正文触发器且不包含旧缩放工具栏规则。
