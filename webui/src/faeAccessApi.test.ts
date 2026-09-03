/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ManagementMutationIndeterminate, type Account } from "./auth";
import { faeAccessApi } from "./faeAccessApi";


const account: Account = {
  internal_user_id: "owner",
  display_name: "苍渊",
  role: "platform_owner",
  departments: ["项目管理部"],
  gender: "male",
  observation_agent_ids: [],
  workspace_scopes: ["fae_workbench"],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf-token",
};

const grant = {
  grant_id: "8c13c965-1b60-472e-b275-199987d1d109",
  internal_user_id: "7b3a7d35-6fc0-4f15-9ac4-f229cbfc60e3",
  display_name: "花名一",
  status: "active",
  permission: "manager" as const,
  created_at: "2026-09-01T00:00:00+00:00",
  row_version: 0,
};


afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


describe("FAE access API", () => {
  it("lists active grants with the exact sanitized response shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      grants: [grant],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(faeAccessApi.list()).resolves.toEqual([grant]);
  });

  it("rejects grant list responses that expose provider identity fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      grants: [{ ...grant, provider_userid: "secret" }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(faeAccessApi.list()).rejects.toThrow("FAE access response invalid");
  });

  it("grants by display name without sending a target UUID", async () => {
    const requestId = "d2c33f73-b942-4417-8264-35f11af6ff1e";
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      internal_user_id: grant.internal_user_id,
      row_version: 0,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await faeAccessApi.grant(account, "花名一", requestId);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/manage/fae-workbench/grants", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf-token",
      },
      body: JSON.stringify({
        display_name: "花名一",
        reason: "fae_workbench_access_approved",
        request_id: requestId,
      }),
    });
  });

  it("revokes with the current row version and exact reason", async () => {
    const requestId = "f7f20643-7c73-462f-8a4f-d1c084c18bd2";
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      row_version: 1,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await faeAccessApi.revoke(account, grant, requestId);

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/manage/fae-workbench/grants/${grant.internal_user_id}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf-token",
        },
        body: JSON.stringify({
          reason: "fae_workbench_access_revoked",
          request_id: requestId,
          expected_row_version: 0,
        }),
      },
    );
  });

  it("preserves indeterminate request ids for replay", async () => {
    const requestId = "6c18cb43-b253-4ff4-9894-1874093aa050";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "management_mutation_indeterminate",
        request_id: requestId,
      },
    }), { status: 503, headers: { "Content-Type": "application/json" } })));

    await expect(
      faeAccessApi.grant(account, "花名一", requestId),
    ).rejects.toBeInstanceOf(ManagementMutationIndeterminate);
  });
});
