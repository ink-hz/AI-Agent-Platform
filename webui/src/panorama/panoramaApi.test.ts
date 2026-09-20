/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { AiEngineeringApiError } from "../aiEngineeringApi";
import { panoramaClient } from "./panoramaApi";


const valid = {
  version: "v1", updated_at: "2026-09-20T00:00:00Z", title: "AI 工程全景",
  context: {
    period: "2026 上半年", metrics: [{ label: "收入", value: "约 4.38 亿元" }],
    observation: "收入基本持平", judgment: "效率与质量优先（项目判断）", source_ids: ["S1"],
  },
  revenue: {
    period: "2025 年", denominator_cents: 10000,
    segments: [{ id: "sensors", label: "3D 视觉传感器", amount_cents: 3111 }],
    note: "分母为主营业务收入", source_ids: ["S2"],
  },
  domains: [{
    id: "market", title: "市场与客户", subtitle: "需求与反馈", items: ["机器人"], detail: ["关联产品"],
    status: "已有应用", source_ids: ["S3"], related_ids: [], actions: ["voc"],
  }],
  support: [{
    id: "management", title: "管理支撑", subtitle: "组织与制度", items: ["人才"], detail: ["资料待确认"],
    status: "资料缺口", source_ids: ["S4"], related_ids: [], actions: ["hr"],
  }],
  shared: { title: "共用能力", actions: ["brain", "access"], status: "已有能力" },
  asks: [{ owner: "产品／研发", request: "指定业务牵头人" }],
  sources: [
    { id: "S1", label: "半年报 p.12", document: "finance" },
    { id: "S2", label: "年报 p.49", document: "finance" },
    { id: "S3", label: "市场事实", document: "domains" },
    { id: "S4", label: "管理事实", document: "domains" },
  ],
};

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

describe("panorama API client", () => {
  it("loads protected panorama data through the deployment prefix", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/ai-engineering");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(valid), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(panoramaClient.fetchPanorama()).resolves.toEqual(valid);
    expect(fetchMock).toHaveBeenCalledWith("/_preview/dingtalk-r1/api/v1/ai-engineering/panorama", expect.objectContaining({
      cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" },
    }));
  });

  it("preserves authorization failures and rejects malformed or expanded contracts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 403 })));
    await expect(panoramaClient.fetchPanorama()).rejects.toEqual(expect.objectContaining<Partial<AiEngineeringApiError>>({ status: 403 }));

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...valid, private_note: "must not pass" }), { status: 200 })));
    await expect(panoramaClient.fetchPanorama()).rejects.toThrow("AI engineering panorama response invalid");
  });

  it("rejects invalid actions, dangling relationships, and inconsistent revenue totals", async () => {
    const malformed = structuredClone(valid);
    malformed.domains[0].actions = ["arbitrary-url" as never];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(malformed), { status: 200 })));
    await expect(panoramaClient.fetchPanorama()).rejects.toThrow("AI engineering panorama response invalid");

    const inconsistent = structuredClone(valid);
    inconsistent.revenue.segments[0].amount_cents = 10001;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(inconsistent), { status: 200 })));
    await expect(panoramaClient.fetchPanorama()).rejects.toThrow("AI engineering panorama response invalid");
  });
});
