/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AiEngineeringApiError } from "../aiEngineeringApi";
import { loadAccount } from "../auth";
import type { PanoramaData, PanoramaEditorState } from "../panoramaTypes";
import { PanoramaView } from "./PanoramaView";
import { panoramaClient } from "./panoramaApi";

vi.mock("../auth", async (original) => ({ ...(await original<typeof import("../auth")>()), loadAccount: vi.fn() }));

const nodes: PanoramaData["nodes"] = [
  { id: "semiconductor", title: "半导体制造", subtitle: "光电器件", detail: [], actions: [], source_ids: [] },
  { id: "orbbec", title: "奥比中光", subtitle: "3D 视觉感知技术与产品", detail: [], actions: [], source_ids: [] },
  { id: "robotics", title: "机器人", subtitle: "", detail: ["机器人行业应用"], actions: [], source_ids: [] },
  { id: "scanning", title: "三维扫描", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "camera", title: "视觉模组与相机", subtitle: "Gemini 330", detail: ["双目产品族"], actions: [], source_ids: ["products"] },
  { id: "software", title: "软件与算法", subtitle: "SDK · ROS 2", detail: ["开发工具"], actions: [], source_ids: [] },
  { id: "chip-tech", title: "自研芯片", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "optics", title: "光学感知", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "sdk-tech", title: "固件与 SDK", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "market-insight", title: "市场洞察", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "customer-use", title: "客户应用", subtitle: "客户反馈", detail: ["VOC 闭环"], actions: ["voc"], source_ids: [] },
  { id: "research", title: "技术预研", subtitle: "", detail: [], actions: [], source_ids: [] },
  { id: "integration", title: "集成服务", subtitle: "技术支持", detail: ["现场支持"], actions: ["fae"], source_ids: [] },
  { id: "talent", title: "组织人才", subtitle: "", detail: [], actions: ["hr"], source_ids: [] },
  { id: "digital", title: "数字化与知识", subtitle: "", detail: ["平台能力"], actions: ["brain", "access", "notes"], source_ids: [] },
  { id: "office", title: "行政保障", subtitle: "", detail: [], actions: ["office"], source_ids: [] },
];
const data: PanoramaData = {
  version: "v1.2", updated_at: "2026-09-20T00:00:00Z", title: "奥比中光 / 全景",
  layers: [
    { id: "industry", title: "产业位置", kind: "industry", groups: [
      { id: "upstream", title: "上游供给", role: "upstream", columns: 1, node_ids: ["semiconductor"] },
      { id: "company", title: "公司", role: "company", columns: 1, node_ids: ["orbbec"] },
      { id: "downstream", title: "下游：设备厂商 · 集成商 · 行业客户", role: "downstream", columns: 2, node_ids: ["robotics", "scanning"] },
    ] },
    { id: "portfolio", title: "产品与技术", kind: "portfolio", groups: [
      { id: "products", title: "产品", role: "products", columns: 2, node_ids: ["camera", "software"] },
      { id: "technology", title: "技术支撑", role: "technology", columns: 3, node_ids: ["chip-tech", "optics", "sdk-tech"] },
    ] },
    { id: "workflow", title: "Marketing ↔ Technology", kind: "workflow", groups: [
      { id: "marketing", title: "Marketing", role: "marketing", columns: 2, node_ids: ["market-insight", "customer-use"] },
      { id: "delivery", title: "Technology", role: "delivery", columns: 2, node_ids: ["research", "integration"] },
    ] },
    { id: "support", title: "支撑体系", kind: "support", groups: [
      { id: "support", title: "全公司共用能力", role: "support", columns: 3, node_ids: ["talent", "digital", "office"] },
    ] },
  ], nodes,
  edges: [
    { id: "camera-robotics", from: "camera", to: "robotics", kind: "supply", label: "Gemini 330" },
    { id: "software-robotics", from: "software", to: "robotics", kind: "supply", label: "SDK / ROS 2" },
    { id: "chip-camera", from: "chip-tech", to: "camera", kind: "supports", label: "技术支撑" },
    { id: "optics-camera", from: "optics", to: "camera", kind: "supports", label: "技术支撑" },
    { id: "sdk-software", from: "sdk-tech", to: "software", kind: "supports", label: "技术支撑" },
    { id: "feedback", from: "customer-use", to: "research", kind: "feedback", label: "应用反馈 · 产品迭代" },
  ], sources: [{ id: "products", label: "产品底稿", document: "products" }],
};
const account = {
  internal_user_id: "owner", display_name: "Owner", role: "platform_owner" as const, departments: [], gender: null,
  observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh" as const, hard_stale_read_only: false, csrf_token: "csrf",
};
function button(container: HTMLElement, text: string) {
  const result = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(text));
  if (!result) throw new Error(`button not found: ${text}`); return result;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

describe("PanoramaView", () => {
  let container: HTMLDivElement; let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.mocked(loadAccount).mockResolvedValue(account);
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("renders the native four-layer homepage without management figures", async () => {
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    expect([...container.querySelectorAll(".panorama-layer > h2")].map((node) => node.textContent)).toEqual(["产业位置", "产品与技术", "Marketing ↔ Technology", "支撑体系"]);
    expect(container.querySelector('[data-node-id="orbbec"]')?.classList.contains("panorama-node--company")).toBe(true);
    expect(container.textContent).not.toMatch(/营业收入|研发投入|\d+\.%/);
    expect(container.querySelector<HTMLAnchorElement>('a[download][href*="export.svg"]')?.href).toContain("version=v1.2");
  });

  it("keeps positions stable while revealing the representative robotics relation", async () => {
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    const order = [...container.querySelectorAll<HTMLElement>("[data-node-id]")].map((node) => node.dataset.nodeId);
    await act(async () => container.querySelector<HTMLButtonElement>('[data-node-id="robotics"] button')!.click());
    expect([...container.querySelectorAll<HTMLElement>("[data-node-id].is-related")].map((node) => node.dataset.nodeId)).toEqual(expect.arrayContaining(["camera", "software", "chip-tech", "optics", "sdk-tech"]));
    expect(container.querySelector('[data-edge-id="camera-robotics"]')).not.toBeNull();
    expect([...container.querySelectorAll<HTMLElement>("[data-node-id]")].map((node) => node.dataset.nodeId)).toEqual(order);
  });

  it("selecting technology finds its supported product and downstream application", async () => {
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    await act(async () => container.querySelector<HTMLButtonElement>('[data-node-id="chip-tech"] button')!.click());
    expect(container.querySelector('[data-node-id="camera"]')?.classList.contains("is-related")).toBe(true);
    expect(container.querySelector('[data-node-id="robotics"]')?.classList.contains("is-related")).toBe(true);
  });

  it("routes aggregate structure by semantic roles after groups reorder and workflow columns change", async () => {
    const reordered = structuredClone(data);
    reordered.layers[0].groups.reverse();
    reordered.layers[1].groups.reverse();
    reordered.layers[2].groups.reverse();
    reordered.layers[2].groups.forEach((group) => { group.columns = 1; });
    await act(async () => root.render(<PanoramaView data={reordered} onAction={vi.fn()} onEvidence={vi.fn()} />));
    expect(container.querySelector('[data-aggregate-edge="industry-upstream-company"][data-from-role="upstream"][data-to-role="company"]')).not.toBeNull();
    expect(container.querySelector('[data-aggregate-edge="industry-company-downstream"][data-from-role="company"][data-to-role="downstream"]')).not.toBeNull();
    expect(container.querySelector('[data-aggregate-edge="portfolio-technology-products"][data-from-role="technology"][data-to-role="products"]')).not.toBeNull();
    expect(container.querySelector('[data-aggregate-edge="workflow-handoff-0"][data-from-role="marketing"][data-to-role="delivery"]')).not.toBeNull();
    expect(container.querySelector('[data-aggregate-edge="workflow-marketing-0"][data-from-node="market-insight"][data-to-node="customer-use"]')).not.toBeNull();
    expect(container.querySelector('[data-group-id="marketing"] .panorama-group__nodes')?.classList.contains("panorama-columns-1")).toBe(true);
  });

  it("keeps actions in details and filters owner-only access", async () => {
    const onAction = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={onAction} onEvidence={vi.fn()} />));
    expect(container.querySelector('[data-action-id="brain"]')).toBeNull();
    await act(async () => container.querySelector<HTMLButtonElement>('[data-node-id="digital"] button')!.click());
    expect(container.querySelector('[data-action-id="brain"]')).not.toBeNull(); expect(container.querySelector('[data-action-id="access"]')).toBeNull();
    await act(async () => container.querySelector<HTMLButtonElement>('[data-action-id="brain"]')!.click());
    expect(onAction).toHaveBeenCalledWith("brain");
    await act(async () => root.render(<PanoramaView data={data} onAction={onAction} onEvidence={vi.fn()} isOwner />));
    expect(container.querySelector('[data-action-id="access"]')).not.toBeNull();
  });

  it("does not capture graph shortcuts while inactive or while an editor owns focus", async () => {
    await act(async () => root.render(<><textarea aria-label="工作区输入" /><PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} active={false} /></>));
    const inactiveSlash = new KeyboardEvent("keydown", { key: "/", bubbles: true, cancelable: true });
    await act(async () => document.dispatchEvent(inactiveSlash)); expect(inactiveSlash.defaultPrevented).toBe(false);
    await act(async () => root.render(<><textarea aria-label="工作区输入" /><PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} active /></>));
    const editor = container.querySelector<HTMLTextAreaElement>("textarea")!; editor.focus();
    const editingSlash = new KeyboardEvent("keydown", { key: "/", bubbles: true, cancelable: true });
    await act(async () => editor.dispatchEvent(editingSlash)); expect(editingSlash.defaultPrevented).toBe(false); expect(document.activeElement).toBe(editor);
  });

  it("previews, saves, cancels, publishes, and restores persisted state", async () => {
    const initial: PanoramaEditorState = { revision: 3, published: data, draft: null, previous: { ...data, version: "v0" } };
    const saved = { ...initial, revision: 4, draft: { ...data, title: "草稿全景" } };
    const discarded = { ...initial, revision: 5, draft: null };
    const published = { revision: 6, published: { ...data, version: "v2", title: "草稿全景" }, draft: null, previous: data };
    const restored = { revision: 7, published: { ...data, version: "v3" }, draft: null, previous: published.published };
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue(initial);
    vi.spyOn(panoramaClient, "saveDraft").mockResolvedValue(saved);
    vi.spyOn(panoramaClient, "discardDraft").mockResolvedValue(discarded);
    vi.spyOn(panoramaClient, "publish").mockResolvedValue(published);
    vi.spyOn(panoramaClient, "restore").mockResolvedValue(restored);
    const onDataChange = vi.fn(); const onDirtyChange = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} onDataChange={onDataChange} onDirtyChange={onDirtyChange} />));
    await act(async () => button(container, "调整布局").click());
    const title = container.querySelector<HTMLInputElement>('input[name="panorama-title"]')!;
    await act(async () => { title.value = "草稿全景"; title.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(onDirtyChange).toHaveBeenLastCalledWith(true); expect(container.querySelector(".panorama")?.getAttribute("aria-label")).toBe("草稿全景");
    await act(async () => button(container, "预览草稿").click());
    expect(container.querySelector(".panorama-editor--preview")).not.toBeNull();
    await act(async () => button(container, "返回编辑").click());
    await act(async () => button(container, "保存草稿").click());
    expect(panoramaClient.saveDraft).toHaveBeenCalledWith("csrf", 3, expect.objectContaining({ title: "草稿全景" }), expect.any(AbortSignal));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await act(async () => button(container, "发布").click()); expect(onDataChange).toHaveBeenCalledWith(published.published);
    await act(async () => button(container, "恢复上一版").click()); expect(onDataChange).toHaveBeenCalledWith(restored.published);
    const titleAfter = container.querySelector<HTMLInputElement>('input[name="panorama-title"]')!;
    await act(async () => { titleAfter.value = "未保存"; titleAfter.dispatchEvent(new Event("input", { bubbles: true })); });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => button(container, "取消修改").click()); expect(panoramaClient.discardDraft).toHaveBeenCalled();
  });

  it("preserves local edits on conflict and clears the editor after authorization loss", async () => {
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue({ revision: 3, published: data, draft: null, previous: null });
    vi.spyOn(panoramaClient, "saveDraft").mockRejectedValueOnce(new AiEngineeringApiError(409));
    const onAuthorizationFailure = vi.fn(); const onDirtyChange = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} onAuthorizationFailure={onAuthorizationFailure} onDirtyChange={onDirtyChange} />));
    await act(async () => button(container, "调整布局").click());
    const title = container.querySelector<HTMLInputElement>('input[name="panorama-title"]')!;
    await act(async () => { title.value = "保留的修改"; title.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => button(container, "保存草稿").click());
    expect(container.textContent).toContain("版本冲突"); expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe("保留的修改");
    expect(button(container, "保存草稿").disabled).toBe(true);
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValueOnce({ revision: 4, published: data, draft: null, previous: null });
    await act(async () => button(container, "核对服务器状态").click());
    vi.spyOn(panoramaClient, "saveDraft").mockRejectedValueOnce(new AiEngineeringApiError(403));
    await act(async () => button(container, "保存草稿").click());
    expect(onAuthorizationFailure).toHaveBeenCalled(); expect(container.querySelector('[aria-label="全景布局编辑器"]')).toBeNull(); expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("preserves edits typed after save starts and requires another save", async () => {
    const initial: PanoramaEditorState = { revision: 3, published: data, draft: null, previous: null };
    const pending = deferred<PanoramaEditorState>();
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue(initial);
    vi.spyOn(panoramaClient, "saveDraft").mockReturnValue(pending.promise);
    const onDirtyChange = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} onDirtyChange={onDirtyChange} />));
    await act(async () => button(container, "调整布局").click());
    const title = container.querySelector<HTMLInputElement>('input[name="panorama-title"]')!;
    await act(async () => { title.value = "已发送"; title.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => button(container, "保存草稿").click());
    await act(async () => { title.value = "请求后继续编辑"; title.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => pending.resolve({ ...initial, revision: 4, draft: { ...data, title: "已发送" } }));
    expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe("请求后继续编辑");
    expect(button(container, "保存草稿").disabled).toBe(false);
    expect(button(container, "发布").disabled).toBe(true);
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
  });

  it("adopts the current server draft when reconciling a clean publish conflict", async () => {
    const draftA = { ...data, title: "草稿 A" };
    const draftB = { ...data, title: "草稿 B" };
    vi.spyOn(panoramaClient, "fetchEditorState")
      .mockResolvedValueOnce({ revision: 3, published: data, draft: draftA, previous: null })
      .mockResolvedValueOnce({ revision: 4, published: data, draft: draftB, previous: null });
    vi.spyOn(panoramaClient, "publish").mockRejectedValueOnce(new AiEngineeringApiError(409));
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    await act(async () => button(container, "调整布局").click());
    expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe("草稿 A");
    await act(async () => button(container, "发布").click());
    await act(async () => button(container, "核对服务器状态").click());
    expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe("草稿 B");
    expect(button(container, "发布").disabled).toBe(false);
  });

  it("preserves edits typed while reconciliation is fetching server state", async () => {
    const draftA = { ...data, title: "草稿 A" };
    const pending = deferred<PanoramaEditorState>();
    vi.spyOn(panoramaClient, "fetchEditorState")
      .mockResolvedValueOnce({ revision: 3, published: data, draft: draftA, previous: null })
      .mockReturnValueOnce(pending.promise);
    vi.spyOn(panoramaClient, "publish").mockRejectedValueOnce(new AiEngineeringApiError(409));
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    await act(async () => button(container, "调整布局").click());
    await act(async () => button(container, "发布").click());
    await act(async () => button(container, "核对服务器状态").click());
    const title = container.querySelector<HTMLInputElement>('input[name="panorama-title"]')!;
    await act(async () => { title.value = "核对期间的新修改"; title.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => pending.resolve({ revision: 4, published: data, draft: { ...data, title: "草稿 B" }, previous: null }));
    expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe("核对期间的新修改");
    expect(button(container, "保存草稿").disabled).toBe(false);
    expect(button(container, "发布").disabled).toBe(true);
  });

  it.each(["publish", "restore"] as const)("refreshes published data when reconciling an unknown %s result", async (kind) => {
    const previous = { ...data, version: "v0" };
    const initial: PanoramaEditorState = {
      revision: 3, published: data, draft: kind === "publish" ? { ...data, title: "待发布" } : null,
      previous: kind === "restore" ? previous : null,
    };
    const published = { ...data, version: "v2", title: kind === "publish" ? "待发布" : "已恢复" };
    vi.spyOn(panoramaClient, "fetchEditorState")
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce({ revision: 4, published, draft: null, previous: data });
    vi.spyOn(panoramaClient, kind).mockRejectedValueOnce(new TypeError("connection lost"));
    const onDataChange = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} onDataChange={onDataChange} />));
    await act(async () => button(container, "调整布局").click());
    await act(async () => button(container, kind === "publish" ? "发布" : "恢复上一版").click());
    await act(async () => button(container, "核对服务器状态").click());
    expect(onDataChange).toHaveBeenLastCalledWith(published);
    expect(container.querySelector<HTMLInputElement>('input[name="panorama-title"]')?.value).toBe(published.title);
  });

  it("edits dynamic groups, node order, nodes, relations, and action bindings", async () => {
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue({ revision: 9, published: data, draft: null, previous: null });
    vi.spyOn(panoramaClient, "saveDraft").mockImplementation(async (_csrf, revision, value) => ({ revision: revision + 1, published: data, draft: value, previous: null }));
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    await act(async () => button(container, "调整布局").click());
    const productGroup = [...container.querySelectorAll<HTMLElement>(".panorama-editor__group")].find((item) => item.querySelector<HTMLInputElement>('header input')?.value === "产品")!;
    const groupTitle = productGroup.querySelector<HTMLInputElement>('header input')!;
    await act(async () => { groupTitle.value = "产品矩阵"; groupTitle.dispatchEvent(new Event("input", { bubbles: true })); });
    const columns = productGroup.querySelector<HTMLInputElement>('input[type="number"]')!;
    await act(async () => { columns.value = "3"; columns.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => productGroup.querySelector<HTMLButtonElement>('button[aria-label="视觉模组与相机下移"]')!.click());
    await act(async () => [...productGroup.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === "添加节点")!.click());
    const newNode = productGroup.querySelector<HTMLElement>('[data-editor-node-id^="node-"]')!;
    const name = newNode.querySelector<HTMLInputElement>('input')!;
    await act(async () => { name.value = "新产品"; name.dispatchEvent(new Event("input", { bubbles: true })); });
    const details = newNode.querySelector<HTMLDetailsElement>("details")!; details.open = true;
    const brain = [...details.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find((item) => item.parentElement?.textContent?.includes("大脑"))!;
    await act(async () => brain.click());
    await act(async () => button(container, "添加关系").click());
    await act(async () => button(container, "保存草稿").click());
    const saved = vi.mocked(panoramaClient.saveDraft).mock.calls[0][2];
    const group = saved.layers.flatMap((layer) => layer.groups).find((item) => item.id === "products")!;
    expect(group.title).toBe("产品矩阵"); expect(group.columns).toBe(3); expect(group.node_ids.slice(0, 2)).toEqual(["software", "camera"]);
    expect(saved.nodes.find((node) => node.title === "新产品")?.actions).toContain("brain");
    expect(saved.edges).toHaveLength(data.edges.length + 1);
  });

  it("keeps hard-stale administrators in read-only graph mode", async () => {
    vi.mocked(loadAccount).mockResolvedValue({ ...account, hard_stale_read_only: true, directory_freshness: "hard_stale" });
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue({ revision: 1, published: data, draft: null, previous: null });
    const onAuthorizationFailure = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} onAuthorizationFailure={onAuthorizationFailure} />));
    await act(async () => button(container, "调整布局").click());
    expect(container.textContent).toContain("当前只能查看全景");
    expect(container.querySelector('[data-node-id="orbbec"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="全景布局编辑器"]')).toBeNull();
    expect(onAuthorizationFailure).not.toHaveBeenCalled();
  });

  it("does not resurrect an editor load after the graph becomes inactive", async () => {
    let resolveState!: (value: PanoramaEditorState) => void;
    vi.spyOn(panoramaClient, "fetchEditorState").mockReturnValue(new Promise((resolve) => { resolveState = resolve; }));
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    await act(async () => button(container, "调整布局").click());
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} active={false} />));
    await act(async () => resolveState({ revision: 1, published: data, draft: null, previous: null }));
    expect(container.querySelector('[aria-label="全景布局编辑器"]')).toBeNull();
    expect(button(container, "调整布局").disabled).toBe(false);
  });
  it("places live organization between workflow and support without including it in layout drafts", async () => {
    const renderOrganization = vi.fn((active: boolean) => <section data-organization-active={String(active)}>公司组织</section>);
    vi.spyOn(panoramaClient, "fetchEditorState").mockResolvedValue({ revision: 1, published: data, draft: null, previous: null });
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} renderOrganization={renderOrganization} />));
    const org = container.querySelector('[data-organization-active="true"]')!;
    expect(org).not.toBeNull();
    expect(container.querySelector('[data-layer-id="workflow"]')!.compareDocumentPosition(org) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(org.compareDocumentPosition(container.querySelector('[data-layer-id="support"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await act(async () => button(container, "调整布局").click());
    expect(container.querySelector('[data-organization-active="false"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="全景布局编辑器"]')?.textContent).not.toContain('公司组织');
  });

});
