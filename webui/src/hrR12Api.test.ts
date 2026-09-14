import { describe, expect, it, vi } from "vitest";

import { HrR12ApiError, createHrR12Api } from "./hrR12Api";


const POSITION_ID = "00000000-0000-4000-8000-000000000001";
const REQUEST_ID = "00000000-0000-4000-8000-000000000002";
const ATTACHMENT_ID = "00000000-0000-4000-8000-000000000003";
const CONVERSATION_ID = "00000000-0000-4000-8000-000000000008";
const TURN_ID = "00000000-0000-4000-8000-000000000009";
const ARTIFACT_VERSION_ID = "00000000-0000-4000-8000-00000000000a";
const OFFICIAL_VERSION_ID = "00000000-0000-4000-8000-00000000000b";


describe("R1.2 HR API", () => {
  it("normalizes complete official position versions and downloads the selected immutable source", async () => {
    const raw = {
      official_position_version_id: OFFICIAL_VERSION_ID, position_id: POSITION_ID,
      official_job_id: "JOBAD:511189335", title: "DQE工程师（数采设备）",
      department: "质量部", locations: ["广东省·深圳市"], category: "质量",
      subcategory: null, headcount: 0, degree: "本科", employment_type: "全职",
      salary: "官网未公开", duty: "负责数采设备质量策划", requirement: "熟悉 DQE 方法",
      source_version: "2026-09-05", source_changed_at: "2026-09-05T01:00:00Z",
      source_snapshot_at: "2026-09-05T02:00:00Z", content_hash: "a".repeat(64),
      first_observed_at: "2026-09-04T01:00:00Z", last_observed_at: "2026-09-05T02:00:00Z",
      official_status: "active", status_reason: "published", consecutive_misses: 0,
      official_status_code: 1, created_at: "2026-09-05T02:00:00Z",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [raw] }), { status: 200 }))
      .mockResolvedValueOnce(new Response("# DQE工程师（数采设备）", {
        status: 200, headers: { "Content-Type": "text/markdown; charset=utf-8", "Content-Disposition": "attachment; filename=official-position.md" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createHrR12Api("csrf");

    await expect(api.officialVersions(POSITION_ID)).resolves.toEqual([expect.objectContaining({
      officialVersionId: OFFICIAL_VERSION_ID,
      duty: "负责数采设备质量策划",
      requirement: "熟悉 DQE 方法",
      headcount: 0,
    })]);
    await expect(api.downloadOfficialVersion(POSITION_ID, OFFICIAL_VERSION_ID)).resolves.toMatchObject({
      filename: "official-position.md",
    });
    expect(fetchMock.mock.calls[1]?.[0]).toContain(`/official-versions/${OFFICIAL_VERSION_ID}/export`);
  });
  it("uses caller request ids and preserves abort signals for mutations", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ materials: [], artifacts: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ content_path: `/api/v1/attachments/content/${"a".repeat(32)}`, expires_at: "2026-09-04T00:05:00Z" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createHrR12Api("csrf").resources(POSITION_ID, controller.signal);
    await createHrR12Api("csrf").downloadResource(POSITION_ID, ATTACHMENT_ID, REQUEST_ID, "download", controller.signal);

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
    const init = fetchMock.mock.calls[1]?.[1];
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(REQUEST_ID);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("csrf");
    expect(init?.signal).toBe(controller.signal);
  });

  it("keeps actionable HTTP statuses for UI recovery", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "baseline changed" }), { status: 409 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toBeInstanceOf(HrR12ApiError);
    await expect(createHrR12Api("csrf").resources(POSITION_ID)).rejects.toMatchObject({ status: 409 });
  });

  it("normalizes exact material metadata without accepting a storage locator", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ materials: [{
      attachment_id: ATTACHMENT_ID, filename: "岗位说明.pdf", media_type: "application/pdf", state: "ready",
      size_bytes: 2, created_at: "2026-09-04T00:00:00Z", source_conversation_id: null,
      source_turn_id: null, preview_available: true, download_available: true,
    }], artifacts: [] }), { status: 200 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).resolves.toMatchObject({
      materials: [{ attachmentId: ATTACHMENT_ID, filename: "岗位说明.pdf" }],
    });
  });

  it("preserves exact artifact-version identity separately from its downloadable attachment", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ materials: [], artifacts: [{
      artifact_id: REQUEST_ID, artifact_version_id: ARTIFACT_VERSION_ID,
      attachment_id: ATTACHMENT_ID, artifact_version: 3, filename: "面试题.pdf",
      media_type: "application/pdf", state: "ready", size_bytes: 1024,
      created_at: "2026-09-04T00:00:00Z", source_conversation_id: null,
      source_turn_id: null, preview_available: true, download_available: true,
    }] }), { status: 200 })));

    await expect(createHrR12Api("csrf").resources(POSITION_ID)).resolves.toMatchObject({ artifacts: [{
      artifactVersionId: ARTIFACT_VERSION_ID, attachmentId: ATTACHMENT_ID,
    }] });
  });


});
