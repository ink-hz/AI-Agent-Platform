/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ManagementMutationIndeterminate, type Account } from "./auth";
import { vocAccessApi } from "./vocAccessApi";


const account: Account = {
  internal_user_id: "owner",
  display_name: "苍渊",
  role: "platform_owner",
  departments: [],
  gender: null,
  observation_agent_ids: [],
  workspace_scopes: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf-token",
};

const grant = {
  grant_id: "8c13c965-1b60-472e-b275-199987d1d109",
  internal_user_id: "7b3a7d35-6fc0-4f15-9ac4-f229cbfc60e3",
  display_name: "稻夫",
  status: "active",
  permission: "manager" as const,
  created_at: "2026-09-04T00:00:00+00:00",
  row_version: 0,
};


afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


describe("VOC access API", () => {
  it("lists active grants with the exact sanitized response shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      grants: [grant],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(vocAccessApi.list()).resolves.toEqual([grant]);
  });

  it("rejects provider identity fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      grants: [{ ...grant, provider_userid: "secret" }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(vocAccessApi.list()).rejects.toThrow("VOC access response invalid");
  });

  it("grants by display name with the fixed audit reason", async () => {
    const requestId = "d2c33f73-b942-4417-8264-35f11af6ff1e";
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      internal_user_id: grant.internal_user_id,
      row_version: 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await vocAccessApi.grant(account, "稻夫", requestId);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/manage/voc-workbench/grants", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf-token",
      },
      body: JSON.stringify({
        display_name: "稻夫",
        reason: "voc_workbench_access_approved",
        request_id: requestId,
      }),
    });
  });

  it("revokes with row version and preserves indeterminate request ids", async () => {
    const requestId = "f7f20643-7c73-462f-8a4f-d1c084c18bd2";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: "ok", row_version: 1,
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: { code: "management_mutation_indeterminate", request_id: requestId },
      }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await vocAccessApi.revoke(account, grant, requestId);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/v1/manage/voc-workbench/grants/${grant.internal_user_id}`,
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({
          reason: "voc_workbench_access_revoked",
          request_id: requestId,
          expected_row_version: 0,
        }),
      }),
    );
    await expect(vocAccessApi.grant(account, "稻夫", requestId))
      .rejects.toBeInstanceOf(ManagementMutationIndeterminate);
  });
});
