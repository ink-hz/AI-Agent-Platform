/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";
import { AiEngineeringApiError } from "../aiEngineeringApi";
import type { PanoramaData, PanoramaEditorState } from "../panoramaTypes";
import { panoramaClient, parsePanorama } from "./panoramaApi";

const valid: PanoramaData = {
  version: "v1", updated_at: "2026-09-20T00:00:00Z", title: "AI 工程全景",
  layers: [
    { id: "industry", title: "产业位置", kind: "industry", groups: [
      { id: "upstream", title: "上游", role: "upstream", columns: 1, node_ids: ["semiconductor"] },
      { id: "company", title: "公司", role: "company", columns: 1, node_ids: ["orbbec"] },
      { id: "downstream", title: "下游", role: "downstream", columns: 1, node_ids: ["robotics"] },
    ] },
    { id: "portfolio", title: "产品与技术", kind: "portfolio", groups: [
      { id: "products", title: "产品", role: "products", columns: 2, node_ids: ["camera"] },
      { id: "technology", title: "技术", role: "technology", columns: 2, node_ids: ["optics"] },
    ] },
    { id: "workflow", title: "Marketing ↔ Technology", kind: "workflow", groups: [
      { id: "marketing", title: "Marketing", role: "marketing", columns: 2, node_ids: ["customer-use"] },
      { id: "delivery", title: "Technology", role: "delivery", columns: 2, node_ids: ["integration"] },
    ] },
    { id: "support", title: "支撑体系", kind: "support", groups: [
      { id: "support", title: "支撑", role: "support", columns: 2, node_ids: ["digital"] },
    ] },
  ],
  nodes: [
    { id: "semiconductor", title: "半导体制造", subtitle: "", detail: [], actions: [], source_ids: [] },
    { id: "orbbec", title: "奥比中光", subtitle: "3D 视觉感知技术与产品", detail: [], actions: [], source_ids: [] },
    { id: "robotics", title: "机器人", subtitle: "", detail: [], actions: [], source_ids: [] },
    { id: "camera", title: "视觉模组与相机", subtitle: "Gemini 330", detail: ["机器人视觉"], actions: [], source_ids: ["p1"] },
    { id: "optics", title: "光学感知", subtitle: "", detail: [], actions: [], source_ids: [] },
    { id: "customer-use", title: "客户应用", subtitle: "", detail: [], actions: ["voc"], source_ids: [] },
    { id: "integration", title: "集成服务", subtitle: "", detail: [], actions: ["fae"], source_ids: [] },
    { id: "digital", title: "数字化与知识", subtitle: "", detail: [], actions: ["brain", "access"], source_ids: [] },
  ],
  edges: [
    { id: "camera-robotics", from: "camera", to: "robotics", kind: "supply", label: "Gemini 330" },
    { id: "optics-camera", from: "optics", to: "camera", kind: "supports", label: "技术支撑" },
  ],
  sources: [{ id: "p1", label: "产品底稿", document: "products" }],
};
const editor: PanoramaEditorState = { revision: 7, published: valid, draft: null, previous: null };

afterEach(() => { window.history.replaceState({}, "", "/"); vi.unstubAllGlobals(); });

describe("panorama API client", () => {
  it("parses the exact four-layer contract through the deployment prefix", async () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/ai-engineering");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(valid), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(panoramaClient.fetchPanorama()).resolves.toEqual(valid);
    expect(fetchMock).toHaveBeenCalledWith("/_preview/dingtalk-r1/api/v1/ai-engineering/panorama", expect.objectContaining({
      cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" },
    }));
  });

  it("rejects expanded shapes, duplicate placement, dangling references, and unlisted actions", () => {
    expect(() => parsePanorama({ ...valid, revenue: {} })).toThrow("AI engineering panorama response invalid");
    const duplicate = structuredClone(valid); duplicate.layers[0].groups[1].node_ids.push("semiconductor");
    expect(() => parsePanorama(duplicate)).toThrow("AI engineering panorama response invalid");
    const dangling = structuredClone(valid); dangling.edges[0].to = "missing";
    expect(() => parsePanorama(dangling)).toThrow("AI engineering panorama response invalid");
    const unsafe = structuredClone(valid); unsafe.nodes[0].actions = ["arbitrary-url" as never];
    expect(() => parsePanorama(unsafe)).toThrow("AI engineering panorama response invalid");
  });

  it("matches backend limits for ids, columns, labels, and detail entries", () => {
    const badId = structuredClone(valid); badId.nodes[0].id = "Upper_case"; badId.layers[0].groups[0].node_ids = ["Upper_case"];
    expect(() => parsePanorama(badId)).toThrow("AI engineering panorama response invalid");
    const tooManyColumns = structuredClone(valid); tooManyColumns.layers[1].groups[0].columns = 9;
    expect(() => parsePanorama(tooManyColumns)).toThrow("AI engineering panorama response invalid");
    const longTitle = structuredClone(valid); longTitle.nodes[0].title = "字".repeat(81);
    expect(() => parsePanorama(longTitle)).toThrow("AI engineering panorama response invalid");
    const blankDetail = structuredClone(valid); blankDetail.nodes[0].detail = [""];
    expect(() => parsePanorama(blankDetail)).toThrow("AI engineering panorama response invalid");
  });

  it("accepts repeated detail prose allowed by the backend contract", () => {
    const repeated = structuredClone(valid); repeated.nodes[0].detail = ["说明", "说明"];
    expect(parsePanorama(repeated).nodes[0].detail).toEqual(["说明", "说明"]);
  });

  it("loads editor state and sends each mutation once with CSRF and revision", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(editor), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await panoramaClient.fetchEditorState();
    await panoramaClient.saveDraft("csrf", 7, valid);
    await panoramaClient.discardDraft("csrf", 7);
    await panoramaClient.publish("csrf", 7);
    await panoramaClient.restore("csrf", 7);
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(fetchMock.mock.calls.slice(1).map(([url, init]) => [url, init.method, new Headers(init.headers).get("X-CSRF-Token"), init.body])).toEqual([
      ["/api/v1/ai-engineering/panorama/draft", "PUT", "csrf", JSON.stringify({ expected_revision: 7, data: valid })],
      ["/api/v1/ai-engineering/panorama/draft", "DELETE", "csrf", JSON.stringify({ expected_revision: 7 })],
      ["/api/v1/ai-engineering/panorama/publish", "POST", "csrf", JSON.stringify({ expected_revision: 7 })],
      ["/api/v1/ai-engineering/panorama/restore", "POST", "csrf", JSON.stringify({ expected_revision: 7 })],
    ]);
  });

  it("preserves authorization and conflict statuses without retrying writes", async () => {
    const forbidden = vi.fn().mockResolvedValue(new Response("{}", { status: 403 })); vi.stubGlobal("fetch", forbidden);
    await expect(panoramaClient.fetchPanorama()).rejects.toMatchObject({ status: 403 } satisfies Partial<AiEngineeringApiError>);
    const conflict = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "revision conflict" }), { status: 409 })); vi.stubGlobal("fetch", conflict);
    await expect(panoramaClient.saveDraft("csrf", 7, valid)).rejects.toMatchObject({ status: 409 });
    expect(conflict).toHaveBeenCalledTimes(1);
  });
});
