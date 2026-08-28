# AI 工程笔记 Mermaid 阅读体验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 工程笔记中的 Mermaid 图默认在一屏内完整展示，并可进入安全、可访问的大图查看器，同时逐图提升现有 16 张图的内容质量。

**Architecture:** 保留 `MermaidDiagram` 的严格渲染与 SVG 清洗链路，新增独立的元数据解析器和无第三方依赖的大图查看器。内嵌图使用独立于正文的视口高度约束；大图查看器复用已清洗的 data URL，不重复渲染。现有内容按明确的逐图决策补充无障碍元数据并调整布局方向。

**Tech Stack:** React 19、TypeScript、Mermaid、DOMPurify、CSS、Vitest、jsdom、Vite

## Global Constraints

- 桌面内嵌 Mermaid 最大高度为 `min(720px, 70vh)`；手机最大高度为 `68svh`。
- 内嵌图必须完整显示、等比缩放、居中，不裁剪且不引入嵌套滚动条。
- 大图缩放以适合窗口为 `1`，步长 `0.25`，范围 `1..4`。
- 保持 `securityLevel: "strict"`、`htmlLabels: false`、DOMPurify SVG profile 与 `script`/`foreignObject` 禁止规则。
- 大图查看器只能复用清洗后的 SVG data URL，不能重新调用 Mermaid。
- 不增加第三方缩放、画布或查看器依赖。
- 现有 16 张 Mermaid 图必须逐张复核并包含 `accTitle`、`accDescr`。
- 不用节点数量、源码长度或关键词硬匹配自动决定拆分和方向。
- 正文宽度、17px 字号、左侧目录树和 Markdown 语法保持不变。
- 实施使用隔离 worktree，并保留主工作区现有未跟踪文件。

---

## File Map

**新增**

- `webui/src/components/ai-notes/mermaidMetadata.ts`：只解析 Mermaid 可访问元数据。
- `webui/src/components/ai-notes/mermaidMetadata.test.ts`：元数据解析契约。
- `webui/src/components/ai-notes/MermaidLightbox.tsx`：只负责模态查看、缩放、拖动、滚动锁和退出。
- `webui/src/components/ai-notes/MermaidLightbox.test.tsx`：查看器行为契约。

**修改**

- `webui/src/components/ai-notes/MermaidDiagram.tsx`：内嵌查看入口与 lightbox 接线。
- `webui/src/components/ai-notes/MermaidDiagram.test.tsx`：元数据、焦点恢复、不重复渲染。
- `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`：16 张生产图真实渲染和元数据门禁。
- `webui/src/styles.css`、`webui/src/styles.test.ts`：桌面、手机、lightbox、打印规则。
- `backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md`：2 张图。
- `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`：5 张图。
- `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`：3 张图。
- `backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md`：4 张图。
- `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`：2 张图。

---

### Task 1: Mermaid 元数据解析器

**Files:**
- Create: `webui/src/components/ai-notes/mermaidMetadata.ts`
- Create: `webui/src/components/ai-notes/mermaidMetadata.test.ts`

**Interfaces:**
- Consumes: Mermaid 源码字符串。
- Produces: `mermaidMetadata(source: string): { title: string; description: string | null }`。
- Fallback: `title` 为 `Mermaid 图表`，`description` 为 `null`。

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from "vitest";
import { mermaidMetadata } from "./mermaidMetadata";

describe("mermaidMetadata", () => {
  it("extracts indented accessibility metadata", () => {
    expect(mermaidMetadata(`flowchart LR
      accTitle: RAG 查询链路
      accDescr: 从用户问题到引用校验和结果返回。`)).toEqual({
      title: "RAG 查询链路",
      description: "从用户问题到引用校验和结果返回。",
    });
  });

  it("trims values and falls back when metadata is absent or blank", () => {
    expect(mermaidMetadata("flowchart TB\n  accTitle:   \n  A-->B")).toEqual({
      title: "Mermaid 图表",
      description: null,
    });
    expect(mermaidMetadata("stateDiagram-v2\n  [*] --> Ready")).toEqual({
      title: "Mermaid 图表",
      description: null,
    });
  });
});
```

- [ ] **Step 2: 运行 RED**

Run: `cd webui && npm test -- --run src/components/ai-notes/mermaidMetadata.test.ts`  
Expected: FAIL，`./mermaidMetadata` 不存在。

- [ ] **Step 3: 写最小实现**

```ts
export type MermaidMetadata = {
  title: string;
  description: string | null;
};

function directive(source: string, name: "accTitle" | "accDescr"): string | null {
  const value = new RegExp(`^\\s*${name}:\\s*(.*?)\\s*$`, "m").exec(source)?.[1]?.trim();
  return value || null;
}

export function mermaidMetadata(source: string): MermaidMetadata {
  return {
    title: directive(source, "accTitle") ?? "Mermaid 图表",
    description: directive(source, "accDescr"),
  };
}
```

- [ ] **Step 4: 运行 GREEN**

Run: `cd webui && npm test -- --run src/components/ai-notes/mermaidMetadata.test.ts`  
Expected: 1 file、2 tests passed。

- [ ] **Step 5: 提交**

```bash
git add webui/src/components/ai-notes/mermaidMetadata.ts webui/src/components/ai-notes/mermaidMetadata.test.ts
git commit -m "feat: parse Mermaid accessibility metadata"
```

---

### Task 2: Mermaid 大图查看器

**Files:**
- Create: `webui/src/components/ai-notes/MermaidLightbox.tsx`
- Create: `webui/src/components/ai-notes/MermaidLightbox.test.tsx`

**Interfaces:**
- Consumes: `imageSource`、`title`、`description`、`onClose`。
- Produces: 原生模态 `<dialog>`；按钮名固定为 `放大`、`缩小`、`恢复`、`关闭大图`。
- State: `scale` 为 `1..4`；`offset` 为 `{x,y}`；仅 `scale > 1` 可拖动。

- [ ] **Step 1: 写 modal、滚动锁和清理失败测试**

在 jsdom 中 mock `HTMLDialogElement.prototype.showModal/close`，渲染：

```tsx
<MermaidLightbox
  description="从问题到答案的检索过程。"
  imageSource="data:image/svg+xml,diagram"
  onClose={onClose}
  title="RAG 查询链路"
/>
```

断言：`showModal` 调用一次；`dialog[aria-label]` 为 `RAG 查询链路`；描述可被 `aria-describedby` 找到；打开时 `document.body.style.overflow === "hidden"`；unmount 后恢复此前值。

- [ ] **Step 2: 写缩放、拖动和关闭失败测试**

精确覆盖：

```ts
button("放大").click();
expect(image.style.transform).toContain("scale(1.25)");
drag(canvas, { x: 10, y: 10 }, { x: 35, y: 25 });
expect(image.style.transform).toContain("translate(25px, 15px)");
button("恢复").click();
expect(image.style.transform).toBe("translate(0px, 0px) scale(1)");
expect(button("缩小")).toBeDisabled();
```

再循环点击放大 20 次断言 `400%`，缩小 20 次断言 `100%`；向 dialog 派发可取消的 `cancel` 事件并断言 `onClose` 调用一次。

- [ ] **Step 3: 运行 RED**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidLightbox.test.tsx`  
Expected: FAIL，组件不存在。

- [ ] **Step 4: 实现查看器**

实现必须包含以下核心状态与边界；不得增加 wheel、动画或第三方库：

```tsx
import { PointerEvent as ReactPointerEvent, useEffect, useId, useRef, useState } from "react";

type MermaidLightboxProps = {
  imageSource: string;
  title: string;
  description: string | null;
  onClose: () => void;
};
type Point = { x: number; y: number };
type DragState = Point & { pointerId: number; originX: number; originY: number };

const ORIGIN: Point = { x: 0, y: 0 };

export function MermaidLightbox({ imageSource, title, description, onClose }: MermaidLightboxProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const descriptionId = useId();
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>(ORIGIN);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      document.body.style.overflow = previousOverflow;
      if (dialog?.open) dialog.close();
    };
  }, []);

  function changeScale(delta: number) {
    const next = Math.min(4, Math.max(1, Number((scale + delta).toFixed(2))));
    setScale(next);
    if (next === 1) setOffset(ORIGIN);
  }

  function resetView() {
    setScale(1);
    setOffset(ORIGIN);
  }

  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (scale === 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      originX: offset.x,
      originY: offset.y,
    };
  }

  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setOffset({
      x: drag.originX + event.clientX - drag.x,
      y: drag.originY + event.clientY - drag.y,
    });
  }

  function pointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return <dialog
    aria-describedby={description ? descriptionId : undefined}
    aria-label={title}
    className="mermaid-lightbox"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    ref={dialogRef}
  >
    {description && <p className="mermaid-visually-hidden" id={descriptionId}>{description}</p>}
    <div className="mermaid-lightbox-toolbar">
      <output aria-live="polite">{Math.round(scale * 100)}%</output>
      <button aria-label="缩小" disabled={scale === 1} onClick={() => changeScale(-.25)} type="button">−</button>
      <button aria-label="放大" disabled={scale === 4} onClick={() => changeScale(.25)} type="button">＋</button>
      <button onClick={resetView} type="button">恢复</button>
      <button aria-label="关闭大图" autoFocus onClick={onClose} type="button">×</button>
    </div>
    <div
      className={`mermaid-lightbox-canvas${scale > 1 ? " is-zoomed" : ""}`}
      onPointerCancel={pointerEnd}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerEnd}
    >
      <img
        alt={title}
        className="mermaid-lightbox-image"
        draggable={false}
        src={imageSource}
        style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
      />
    </div>
  </dialog>;
}
```

- [ ] **Step 5: 运行 GREEN**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidLightbox.test.tsx`  
Expected: 查看器全部测试通过。

- [ ] **Step 6: 提交**

```bash
git add webui/src/components/ai-notes/MermaidLightbox.tsx webui/src/components/ai-notes/MermaidLightbox.test.tsx
git commit -m "feat: add Mermaid full-screen viewer"
```

---

### Task 3: 接入内嵌 Mermaid

**Files:**
- Modify: `webui/src/components/ai-notes/MermaidDiagram.tsx`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.test.tsx`

**Interfaces:**
- Consumes: Task 1 `mermaidMetadata`、Task 2 `MermaidLightbox`。
- Produces: `.mermaid-diagram-trigger`、准确 image alt、`.mermaid-diagram-zoom-hint`。
- Focus: 关闭查看器后回到原 trigger。

- [ ] **Step 1: 写失败测试**

用带 `accTitle: Agent 行动循环` 和 `accDescr` 的 source 渲染组件，断言：

```ts
expect(trigger.getAttribute("aria-label")).toBe("查看大图：Agent 行动循环");
expect(trigger.querySelector("img")?.alt).toBe("Agent 行动循环");
trigger.click();
expect(container.querySelector("dialog")?.getAttribute("aria-label")).toBe("Agent 行动循环");
expect(render).toHaveBeenCalledTimes(1);
button("关闭大图").click();
expect(container.querySelector("dialog")).toBeNull();
expect(document.activeElement).toBe(trigger);
```

保留并更新缺少元数据时 `alt="Mermaid 图表"` 的回退测试。

- [ ] **Step 2: 运行 RED**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.test.tsx`  
Expected: FAIL，当前没有 trigger、dialog 和准确 alt。

- [ ] **Step 3: 修改成功态结构**

保留原清洗、loading 和 fallback，只加入：

```tsx
const metadata = mermaidMetadata(source);
const triggerRef = useRef<HTMLButtonElement>(null);
const restoreFocusRef = useRef(false);
const [expanded, setExpanded] = useState(false);

useEffect(() => {
  if (expanded || !restoreFocusRef.current) return;
  restoreFocusRef.current = false;
  triggerRef.current?.focus();
}, [expanded]);

function closeExpanded() {
  restoreFocusRef.current = true;
  setExpanded(false);
}
```

成功态 JSX 精确为：

```tsx
<figure className="mermaid-diagram">
  <button
    aria-label={`查看大图：${metadata.title}`}
    className="mermaid-diagram-trigger"
    onClick={() => setExpanded(true)}
    ref={triggerRef}
    type="button"
  >
    <img alt={metadata.title} onError={() => setFailed(true)} src={imageSource} />
    <span aria-hidden="true" className="mermaid-diagram-zoom-hint">查看大图 ↗</span>
  </button>
  {metadata.description && <figcaption className="mermaid-visually-hidden">{metadata.description}</figcaption>}
  {expanded && <MermaidLightbox
    description={metadata.description}
    imageSource={imageSource}
    onClose={closeExpanded}
    title={metadata.title}
  />}
</figure>
```

source effect 开始时 `setExpanded(false)`，切换文章不保留旧查看器。

- [ ] **Step 4: 运行组件回归**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.test.tsx src/components/ai-notes/MermaidLightbox.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx`  
Expected: 3 files passed；Mermaid 每张图只渲染一次。

- [ ] **Step 5: 提交**

```bash
git add webui/src/components/ai-notes/MermaidDiagram.tsx webui/src/components/ai-notes/MermaidDiagram.test.tsx
git commit -m "feat: open Mermaid diagrams in full-screen viewer"
```

---

### Task 4: 一屏概览、查看器和打印样式

**Files:**
- Modify: `webui/src/styles.css`
- Modify: `webui/src/styles.test.ts`

**Interfaces:**
- Consumes: Tasks 2–3 的 class 名。
- Produces: 桌面、手机、lightbox、安全区、焦点和打印样式契约。

- [ ] **Step 1: 写失败的样式契约**

在 AI notes 样式用例中加入：

```ts
expect(rule(".mermaid-diagram-trigger")).toContain("cursor: zoom-in");
expect(rule(".mermaid-diagram img")).toContain("max-height: min(720px, 70vh)");
expect(rule(".mermaid-diagram img")).toContain("object-fit: contain");
expect(rule(".mermaid-lightbox")).toContain("height: 100dvh");
expect(rule(".mermaid-lightbox-canvas")).toContain("touch-action: none");
expect(rule(".mermaid-lightbox-image")).toContain("transform-origin: center");
expect(rule(".mermaid-diagram-trigger:focus-visible")).toContain("outline: 3px solid");
expect(aiNotesMobile).toContain(".mermaid-diagram img { max-height: 68svh; }");
expect(block("@media print")).toContain(".mermaid-diagram img { max-height: none; }");
expect(block("@media print")).toContain(".mermaid-diagram-zoom-hint, .mermaid-lightbox { display: none !important; }");
```

- [ ] **Step 2: 运行 RED**

Run: `cd webui && npm test -- --run src/styles.test.ts`  
Expected: FAIL，缺少一屏概览和 lightbox 规则。

- [ ] **Step 3: 实现桌面规则**

```css
.mermaid-diagram { max-width: 100%; margin: 28px 0; text-align: center; }
.mermaid-diagram-trigger { position: relative; display: inline-block; max-width: 100%; padding: 0; border: 0; background: transparent; color: inherit; cursor: zoom-in; }
.mermaid-diagram-trigger:focus-visible { outline: 3px solid rgba(36,104,197,.34); outline-offset: 4px; }
.mermaid-diagram img { display: block; width: auto; max-width: 100%; height: auto; max-height: min(720px, 70vh); margin: 0 auto; object-fit: contain; }
.mermaid-diagram-zoom-hint { position: absolute; right: 10px; bottom: 10px; padding: 5px 8px; border: 1px solid rgba(255,255,255,.72); border-radius: 999px; background: rgba(18,63,120,.9); color: #fff; font-size: 11.5px; font-weight: 700; box-shadow: 0 4px 12px rgba(15,27,45,.18); }
.mermaid-visually-hidden { position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.mermaid-lightbox { width: 100vw; max-width: none; height: 100dvh; max-height: none; margin: 0; padding: 0; overflow: hidden; border: 0; background: #f8fafc; color: var(--ink); }
.mermaid-lightbox::backdrop { background: rgba(15,27,45,.72); }
.mermaid-lightbox-toolbar { position: absolute; z-index: 2; top: max(14px, env(safe-area-inset-top)); right: max(14px, env(safe-area-inset-right)); display: flex; min-height: 44px; align-items: center; gap: 6px; padding: 6px; border: 1px solid var(--line); border-radius: 10px; background: rgba(255,255,255,.96); box-shadow: 0 10px 28px rgba(15,27,45,.16); }
.mermaid-lightbox-toolbar output { min-width: 48px; color: var(--ink-soft); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.mermaid-lightbox-toolbar button { min-width: 34px; min-height: 34px; padding: 5px 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); color: var(--ink); font: inherit; cursor: pointer; }
.mermaid-lightbox-toolbar button:disabled { cursor: default; opacity: .42; }
.mermaid-lightbox-toolbar button:focus-visible { outline: 3px solid rgba(36,104,197,.34); outline-offset: 1px; }
.mermaid-lightbox-canvas { display: grid; width: 100%; height: 100%; overflow: hidden; padding: 78px 28px 28px; cursor: default; place-items: center; touch-action: none; }
.mermaid-lightbox-canvas.is-zoomed { cursor: grab; }
.mermaid-lightbox-canvas.is-zoomed:active { cursor: grabbing; }
.mermaid-lightbox-image { display: block; max-width: calc(100vw - 56px); max-height: calc(100dvh - 106px); user-select: none; transform-origin: center; will-change: transform; }
```

- [ ] **Step 4: 实现手机与打印规则**

在 AI notes 目标手机媒体块加入：

```css
.mermaid-diagram img { max-height: 68svh; }
.mermaid-lightbox-canvas { padding: 74px 14px 18px; }
.mermaid-lightbox-image { max-width: calc(100vw - 28px); max-height: calc(100dvh - 92px); }
```

在 `@media print` 加入：

```css
.mermaid-diagram-zoom-hint, .mermaid-lightbox { display: none !important; }
.mermaid-diagram-trigger { display: block; cursor: default; }
.mermaid-diagram img { max-height: none; }
```

- [ ] **Step 5: 运行 GREEN 与回归**

Run: `cd webui && npm test -- --run src/styles.test.ts src/components/ai-notes/MermaidDiagram.test.tsx src/components/ai-notes/MermaidLightbox.test.tsx`  
Expected: 3 files passed。

- [ ] **Step 6: 提交**

```bash
git add webui/src/styles.css webui/src/styles.test.ts
git commit -m "style: fit Mermaid diagrams to the reading viewport"
```

---

### Task 5: 治理基础与企业架构的 7 张图

**Files:**
- Modify: `backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md`
- Modify: `backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md`
- Modify: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Interfaces:**
- Consumes: Mermaid `accTitle`、`accDescr`。
- Produces: 7 张有唯一标题和描述的图；2 张调整方向，1 张重写结构。

- [ ] **Step 1: 先让本批 7 张生产图的元数据门禁失败**

新增测试 helper：

```ts
function expectAccessibilityMetadata(sources: string[]) {
  for (const source of sources) {
    expect(source).toMatch(/^\s*accTitle:\s*\S.+$/m);
    expect(source).toMatch(/^\s*accDescr:\s*\S.+$/m);
  }
}
```

只在“Agent 工程学习地图”和“企业 Agent 架构”两个现有测试中，把 `sources` 交给该 helper；另外三篇暂不启用该门禁。

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx`  
Expected: 2 个本批文章用例 FAIL，指出缺少 `accTitle`；其余 4 个用例通过。

- [ ] **Step 2: 治理学习地图 2 张图**

按出现顺序在图类型声明后加入：

```mermaid
accTitle: Agent 最小行动循环
accDescr: Agent 从目标和当前状态出发，经过行动提议、策略校验、工具执行和结果观察，最终继续、追问、停止或交还人工。
```

```mermaid
accTitle: Agent 工程能力递进路线
accDescr: 从固定证据和真实工具开始，逐步增加持久运行、知识检索、审批执行、失败评估和子 Agent 验证。
```

第一张保持 `flowchart TB`。第二张改为 `flowchart LR`，删除包裹全部内容的 `ROADMAP` subgraph 和 `style ROADMAP`；保留 `FOUNDATION`、`RUNTIME`、`QUALITY` 三个阶段及原语义颜色，结构固定为：

```mermaid
subgraph FOUNDATION["基础闭环"]
    direction LR
    A[固定日志与证据结论] --> B[工具搜索真实记录]
end
subgraph RUNTIME["生产运行"]
    direction LR
    C[持久任务与故障恢复] --> D[规范、历史与检索证据] --> E[审批后幂等创建工单]
end
subgraph QUALITY["质量进化"]
    direction LR
    F[真实失败回归集] --> G[验证是否需要独立子 Agent]
end
B -->|状态化| C
E -->|评估化| F
```

- [ ] **Step 3: 治理企业架构 5 张图**

| # | `accTitle` | `accDescr` | 方向 |
| --- | --- | --- | --- |
| 1 | 企业级 Agent 系统分层 | 入口与任务状态进入智能决策，行动经过信任控制和工具执行，并持续产生验证与审计证据。 | 保留 `TB`，四区是层次而非时间。 |
| 2 | Agent 受控运行循环 | Agent 读取状态、提出行动，经过结构和信任校验后执行工具，直到完成证据满足或返回修正。 | 保留 `TB`，决策回路上下阅读。 |
| 3 | Agent 任务生命周期 | 任务在就绪、运行、等待审批、等待外部结果、暂停、完成、失败和取消状态之间转换。 | `stateDiagram-v2` 后加入 `direction LR`。 |
| 4 | Agent 信任决策链路 | 行动依次经过主体、组织策略、资源授权、动态风险、审批和执行前复验，再执行并写入审计证据。 | 首行改为 `flowchart LR`。 |
| 5 | 传统 UI 与 Agent 复用领域服务 | 传统界面 API 和 Agent 工具接口共同调用领域服务，再访问数据与外部系统。 | 保留紧凑 `TB`。 |

每张图在类型声明后加入表中完整 `accTitle` 和 `accDescr`；不得缩写或复用同一标题。

- [ ] **Step 4: 运行本批 GREEN**

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx`  
Expected: 1 file、6 tests 全部通过；本批 7 张图通过元数据门禁，另外 9 张继续通过原有真实渲染与安全门禁。

- [ ] **Step 5: 提交**

```bash
git add backend/app/ai_notes/content/01-foundations/01-agent-engineering-learning-map.md backend/app/ai_notes/content/02-agent-architecture/01-enterprise-agent-system-architecture.md webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx
git commit -m "content: refine foundational Agent diagrams"
```

---

### Task 6: 治理 Claude Code、RAG 与方法论的 9 张图

**Files:**
- Modify: `backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md`
- Modify: `backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md`
- Modify: `backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md`
- Test: `webui/src/components/ai-notes/MermaidDiagram.integration.test.tsx`

**Interfaces:**
- Consumes: Task 5 元数据门禁。
- Produces: 剩余 9 张图的唯一标题、描述和已决方向；全部 16 张图真实渲染通过。

- [ ] **Step 1: 治理 Claude Code 3 张图**

| # | `accTitle` | `accDescr` | 方向 |
| --- | --- | --- | --- |
| 1 | Claude Code 多入口共享架构 | 终端、IDE、桌面、Web、远程控制和 CI/CD 共用上下文、推理、权限、工具与验证工作循环。 | 保留 `TB`。 |
| 2 | Claude Code Agent 工作循环 | 从用户目标和代码库约束出发，行动经过权限判断和工具执行，直到验证目标达到。 | 改为 `flowchart LR`。 |
| 3 | Claude Code 核心责任分区 | 上下文与编排连接权限控制、工具环境、验证和观测，形成完整工程运行时。 | 保留 `TB`。 |

- [ ] **Step 2: 治理 RAG 4 张图**

| # | `accTitle` | `accDescr` | 方向 |
| --- | --- | --- | --- |
| 1 | RAG 查询链路 | 用户问题经过查询理解、权限约束、候选召回、融合重排、上下文组装、证据生成和引用校验后返回。 | 改为 `flowchart LR`。 |
| 2 | RAG 索引链路 | 原始文档经过解析和语义分块，生成向量、词法、元数据权限以及可选图实体索引。 | 改为 `flowchart LR`。 |
| 3 | HNSW 多层导航 | 查询向量从最高层入口逐层扩展近邻并下降，最终在底层候选集合返回 Top-K。 | 保留 `TD`。 |
| 4 | 混合检索与重排 | 查询同时进入 BM25 和向量召回，结果去重融合并重排序，形成上下文候选。 | 改为 `flowchart LR`。 |

- [ ] **Step 3: 治理 AI Native 2 张图**

| # | `accTitle` | `accDescr` | 方向 |
| --- | --- | --- | --- |
| 1 | AI Native 架构协作闭环 | 原始材料经过来源提纯、问题约束、方案推演和人工决策，形成设计记录并持续检查、评审和回流。 | 改为 `flowchart LR`。 |
| 2 | 人与 AI 的架构责任边界 | 人负责目标、取舍、批准和最终验证，AI 辅助材料提取、冲突发现、候选方案和一致性检查。 | 改为 `flowchart LR`，两组责任并列。 |

Tasks 6 的 9 张图都在类型声明后逐字加入对应表格行的完整 `accTitle` 和 `accDescr`，不复用标题、不删减描述。

- [ ] **Step 4: 运行 16 张图真实渲染门禁**

先把 `expectAccessibilityMetadata(sources)` 移入 `expectProductionDiagramsToRender` 的开头，并删除 Task 5 中两个文章用例里的重复调用。此时全部 16 张图统一经过元数据、真实渲染与安全门禁。

再加入跨文章唯一性用例：

```ts
it("gives every production diagram unique accessibility metadata", () => {
  const files = [
    "01-foundations/01-agent-engineering-learning-map.md",
    "02-agent-architecture/01-enterprise-agent-system-architecture.md",
    "03-tools-and-frameworks/01-claude-code-architecture.md",
    "04-ai-engineering/01-rag-retrieval-engineering.md",
    "05-thinking-and-methods/01-ai-native-architecture-design.md",
  ];
  const sources = files.flatMap((file) => mermaidBlocks(productionArticle(file)));
  const titles = sources.map((source) => /^\s*accTitle:\s*(.+)$/m.exec(source)?.[1].trim());
  const descriptions = sources.map((source) => /^\s*accDescr:\s*(.+)$/m.exec(source)?.[1].trim());
  expect(sources).toHaveLength(16);
  expect(titles.every(Boolean)).toBe(true);
  expect(descriptions.every(Boolean)).toBe(true);
  expect(new Set(titles).size).toBe(16);
  expect(new Set(descriptions).size).toBe(16);
});
```

Run: `cd webui && npm test -- --run src/components/ai-notes/MermaidDiagram.integration.test.tsx`  
Expected: 1 file、7 tests passed；5 篇文章共 16 张图都有唯一元数据、语义颜色且安全渲染。

- [ ] **Step 5: 运行文章回归**

Run: `cd webui && npm test -- --run src/aiNotesApi.test.ts src/pages/AiNotesPage.test.tsx src/components/ai-notes/ArticleMarkdown.test.tsx`  
Expected: 3 files passed；10 篇文章仍能加载，Markdown 结构未破坏。

- [ ] **Step 6: 提交**

```bash
git add backend/app/ai_notes/content/03-tools-and-frameworks/01-claude-code-architecture.md backend/app/ai_notes/content/04-ai-engineering/01-rag-retrieval-engineering.md backend/app/ai_notes/content/05-thinking-and-methods/01-ai-native-architecture-design.md
git commit -m "content: refine Mermaid diagrams for continuous reading"
```

---

### Task 7: 完整验证、独立审查与上线

**Files:**
- Verify only; do not add unrelated files.

**Interfaces:**
- Consumes: Tasks 1–6。
- Produces: 可合并、可发布、线上已验证版本。

- [ ] **Step 1: 运行定向回归**

```bash
cd webui
npm test -- --run \
  src/components/ai-notes/mermaidMetadata.test.ts \
  src/components/ai-notes/MermaidLightbox.test.tsx \
  src/components/ai-notes/MermaidDiagram.test.tsx \
  src/components/ai-notes/MermaidDiagram.integration.test.tsx \
  src/components/ai-notes/ArticleMarkdown.test.tsx \
  src/pages/AiNotesPage.test.tsx \
  src/styles.test.ts
```

Expected: 7 个文件全部通过，0 failures。

- [ ] **Step 2: 运行全量测试和构建**

```bash
cd webui
npm test -- --run
npm run build
```

Expected: 58 个以上测试文件全部通过；`tsc -b && vite build` exit 0。既有大 chunk warning 可保留，不得出现新错误。

- [ ] **Step 3: 检查补丁范围**

```bash
mermaid_base_sha="$(git merge-base HEAD master)"
git diff --check "$mermaid_base_sha"..HEAD
git status --short
git diff --stat "$mermaid_base_sha"..HEAD
```

Expected: diff check 无输出；只包含 File Map 中的文件；临时 `node_modules` symlink 不进入提交。

- [ ] **Step 4: 请求独立审查**

审查 Critical/Important/Minor，覆盖 `<dialog>` 语义、焦点恢复、body 滚动锁、缩放边界、Pointer Events 清理、不重复渲染、安全链路、桌面/手机/打印、16 张图元数据唯一性和布局决定。无 Critical/Important 才能继续；Minor 经技术核验后修复或说明不采纳原因。

- [ ] **Step 5: 真实浏览器视觉验收**

桌面 1440×900、手机 390×844 至少验证：

1. “能力递进”内嵌图完整且不超过一屏约束；
2. 点击/Enter 打开；放大、拖动、恢复、Esc 关闭；
3. 关闭后焦点和正文滚动位置恢复；
4. 16 张图逐张可视复核文字、方向、颜色；
5. 没有嵌套滚动条、裁剪或工具栏遮挡。

- [ ] **Step 6: 合并、推送与发布**

合并到 `master` 后复跑全量测试和构建，然后：

```bash
git push origin master
./deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: 输出 `CLOUD_PLATFORM_DEPLOY_OK release=`，其后 SHA 与 `git rev-parse HEAD` 完全一致，结尾为 `mode=dingtalk`。

- [ ] **Step 7: 验证线上五层证据**

- `/opt/orbbec-agent-platform/current` 指向本次 master SHA；
- 六个核心容器全部 healthy；
- `https://agent.orbbec.com.cn/api/health` 为 HTTP 200；
- 线上 CSS 包含 `max-height:min(720px,70vh)` 与 `max-height:68svh`；
- 线上 JS 包含 `查看大图`、`关闭大图`，文章内容包含 16 组 `accTitle`、`accDescr`。

---

## Execution Notes

- 开始前用 `using-git-worktrees` 建立隔离工作区。
- 每个任务执行 RED → GREEN → 回归 → commit，不压成单个提交。
- 内容任务必须逐张阅读图和周边正文，不得正则批量替换。
- 若真实渲染证明某个已决 `LR/TB` 产生更差的交叉线或文字缩放，停止该图修改并回到设计讨论，不自行发明第四种布局策略。
- 上线前使用 `requesting-code-review`；完成声明前使用 `verification-before-completion`；合并清理使用 `finishing-a-development-branch`。
