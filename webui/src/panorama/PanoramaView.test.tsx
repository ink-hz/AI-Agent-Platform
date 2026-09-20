/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PanoramaData } from "../panoramaTypes";
import { PanoramaView } from "./PanoramaView";


const data: PanoramaData = {
  version: "v1.2", updated_at: "2026-09-20T00:00:00Z", title: "奥比中光 AI 工程全景",
  context: {
    period: "2026 上半年", metrics: [
      { label: "营业收入", value: "约 4.38 亿元" }, { label: "研发投入同比", value: "+22.17%" },
    ], observation: "收入基本持平、研发加码、利润下降", judgment: "效率与质量优先（项目判断）", source_ids: ["F1"],
  },
  revenue: {
    period: "2025 年", denominator_cents: 93504482249,
    segments: [
      { id: "consumer", label: "消费级应用设备", amount_cents: 58370857872 },
      { id: "sensor", label: "3D 视觉传感器", amount_cents: 29087133287 },
      { id: "industrial", label: "工业级应用设备", amount_cents: 2553137670 },
      { id: "other", label: "其他", amount_cents: 3486012420 },
    ], note: "分母为主营业务收入 935,044,822.49 元", source_ids: ["F2"],
  },
  domains: [
    { id: "market", title: "市场与客户", subtitle: "发现需求", items: ["机器人", "VOC"], detail: ["客户反馈与产品机会"], status: "已有应用", source_ids: ["D1"], related_ids: ["product", "service"], actions: ["voc"] },
    { id: "product", title: "产品与交付", subtitle: "形成产品", items: ["芯片", "模组／相机"], detail: ["产品族及交付区别"], status: "建设中", source_ids: ["D2"], related_ids: ["technology"], actions: [] },
    { id: "technology", title: "核心技术", subtitle: "构建能力", items: ["深度算法", "SDK"], detail: ["自研深度引擎芯片"], status: "已有能力", source_ids: ["D3"], related_ids: ["product"], actions: ["brain"] },
    { id: "supply", title: "供应与生产", subtitle: "稳定制造", items: ["模组组装", "成品测试"], detail: ["资料缺口待确认"], status: "资料缺口", source_ids: ["D4"], related_ids: ["service"], actions: [] },
    { id: "service", title: "交付与服务", subtitle: "闭环反馈", items: ["设计导入", "FAE"], detail: ["测量验收与问题反馈"], status: "已有应用", source_ids: ["D5"], related_ids: ["market"], actions: ["fae"] },
  ],
  support: [{ id: "management", title: "管理支撑", subtitle: "组织与制度", items: ["人才／HR", "行政"], detail: ["职责映射待确认"], status: "部分已有", source_ids: ["D6"], related_ids: ["market"], actions: ["hr", "office"] }],
  shared: { title: "共用能力", actions: ["brain", "agents", "missions", "sessions", "operations", "review", "identity", "access"], status: "已有能力" },
  asks: [{ owner: "产品／研发", request: "指定一条代表产品链的业务牵头人" }],
  sources: [
    { id: "F1", label: "2026 半年报", document: "finance" }, { id: "F2", label: "2025 年报 p.49", document: "finance" },
    { id: "D1", label: "市场事实", document: "domains" }, { id: "D2", label: "产品事实", document: "products" },
    { id: "D3", label: "技术事实", document: "assets" }, { id: "D4", label: "生产事实", document: "domains" },
    { id: "D5", label: "服务事实", document: "domains" }, { id: "D6", label: "管理事实", document: "domains" },
  ],
};

describe("PanoramaView", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); });

  it("draws revenue widths from original cents on one common scale", async () => {
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    const bars = [...container.querySelectorAll<SVGRectElement>("[data-revenue-segment]")];
    expect(bars).toHaveLength(4);
    expect(bars.map((bar) => Number(bar.getAttribute("width")))).toEqual([
      58370857872, 29087133287, 2553137670, 3486012420,
    ].map((amount) => amount / data.revenue.denominator_cents * 1000));
    expect(container.textContent).toContain("62.43%");
    expect(container.textContent).toContain("2.73%");
  });

  it("expands domains, highlights relationships, and returns with Escape", async () => {
    await act(async () => root.render(<PanoramaView data={data} onAction={vi.fn()} onEvidence={vi.fn()} />));
    const market = container.querySelector<HTMLElement>('[data-domain-id="market"]')!;
    await act(async () => market.querySelector<HTMLButtonElement>(".panorama-domain__toggle")!.click());
    expect(market.getAttribute("data-expanded")).toBe("true");
    expect(container.querySelector('[data-domain-id="product"]')?.classList.contains("is-related")).toBe(true);
    expect(container.textContent).toContain("客户反馈与产品机会");
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(market.getAttribute("data-expanded")).toBe("false");
  });

  it("searches API content, opens the matching domain, and dispatches known actions and evidence", async () => {
    const onAction = vi.fn(); const onEvidence = vi.fn();
    await act(async () => root.render(<PanoramaView data={data} onAction={onAction} onEvidence={onEvidence} isOwner={false} />));
    const search = container.querySelector<HTMLInputElement>('input[type="search"]')!;
    await act(async () => { search.value = "深度算法"; search.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(container.querySelector('[data-domain-id="technology"]')?.getAttribute("data-expanded")).toBe("true");
    expect(container.querySelector('[data-domain-id="technology"]')?.classList.contains("is-search-match")).toBe(true);

    await act(async () => container.querySelector<HTMLButtonElement>('[data-action-id="brain"]')!.click());
    await act(async () => container.querySelector<HTMLButtonElement>('[data-source-id="D3"]')!.click());
    expect(onAction).toHaveBeenCalledWith("brain");
    expect(onEvidence).toHaveBeenCalledWith("assets");
    expect(container.querySelector('[data-action-id="access"]')).toBeNull();
    await act(async () => container.querySelector<HTMLElement>('[data-domain-id="service"]')!.querySelector<HTMLButtonElement>(".panorama-domain__toggle")!.click());
    expect(container.textContent).toContain("独立入口");
  });
});
