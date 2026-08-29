/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account, PlatformRole } from "../auth";
import { IdentityManagementPage } from "./IdentityManagementPage";
import { PartnerAccessPanel } from "./PartnerAccessPanel";


const owner: Account = {
  internal_user_id: "62a31b32-2a92-47d4-9f79-f0c61bca12aa",
  display_name: "苍渊",
  departments: [],
  gender: null,
  role: "platform_owner",
  observation_agent_ids: [],
  directory_freshness: "fresh",
  hard_stale_read_only: false,
  csrf_token: "csrf",
};
const organizationId = "20000000-0000-4000-8000-000000000001";
const operatorId = "30000000-0000-4000-8000-000000000001";
const subjectId = "40000000-0000-4000-8000-000000000001";
const bindingRequestId = "50000000-0000-4000-8000-000000000001";
const now = "2026-08-29T08:00:00Z";


const organization = {
  partner_organization_id: organizationId,
  display_name: "合作方甲",
  status: "active",
  created_at: now,
  updated_at: now,
  invalidated_at: null,
};
const operator = {
  partner_operator_id: operatorId,
  subject_id: subjectId,
  partner_organization_id: organizationId,
  display_name: "合作方客服",
  status: "active",
  fae_grant_active: false,
  fae_granted_at: null,
  created_at: now,
  updated_at: now,
  invalidated_at: null,
};
const bindingRequest = {
  binding_request_id: bindingRequestId,
  provider_kind: "qianniu",
  display_name: "待绑定坐席",
  status: "pending",
  verified_at: now,
  requested_at: now,
  expires_at: now,
  resolved_at: null,
  linked_partner_operator_id: null,
};


function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}


function safeFetch(): ReturnType<typeof vi.fn> {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/v1/manage/users")) return json({ users: [] });
    if (init?.method && init.method !== "GET") {
      const body = JSON.parse(String(init.body)) as { request_id: string };
      if (path.includes("/binding-requests/")) {
        return json({ request_id: body.request_id, binding_request: bindingRequest });
      }
      if (path.includes("/operators")) {
        return json({ request_id: body.request_id, operator });
      }
      return json({ request_id: body.request_id, organization });
    }
    if (path.endsWith("/organizations")) return json({ organizations: [organization] });
    if (path.endsWith("/operators")) return json({ operators: [operator] });
    if (path.endsWith("/binding-requests")) {
      return json({ binding_requests: [bindingRequest] });
    }
    throw new Error(`unexpected request ${path}`);
  });
}


function setInput(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}


async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}


describe("PartnerAccessPanel", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    sessionStorage.clear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lets only the owner manage safe partner organization and operator projections", async () => {
    vi.stubGlobal("fetch", safeFetch());

    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();

    expect(container.textContent).toContain("合作方客服");
    expect(container.textContent).toContain("合作方甲");
    expect(container.textContent).toContain("待绑定坐席");
    expect(container.textContent).toContain("状态：pending");
    expect(container.textContent).toContain(`核验时间：${now}`);
    expect(container.textContent).toContain(`请求时间：${now}`);
    expect(container.textContent).toContain(`到期时间：${now}`);
    expect([...container.querySelectorAll("button")].some(
      (button) => button.textContent === "创建合作方",
    )).toBe(true);
    expect(container.textContent).not.toMatch(/Provider Token/i);
    expect(container.textContent).not.toContain("provider_subject");
    expect(container.textContent).not.toContain("raw-seat-42");
  });

  it.each(["platform_admin", "management_viewer", "member"] as const)(
    "does not render or fetch partner management for %s",
    async (role: PlatformRole) => {
      const fetchMock = safeFetch();
      vi.stubGlobal("fetch", fetchMock);

      await act(async () => root.render(
        <PartnerAccessPanel account={{ ...owner, role }} />,
      ));
      await settle();

      expect(container.textContent).not.toContain("合作方客服");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("requires a reason and sends one client-generated request ID", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const fetchMock = safeFetch();
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();

    const create = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "创建合作方",
    ) as HTMLButtonElement;
    const name = container.querySelector(
      "input[aria-label='合作方名称']",
    ) as HTMLInputElement;
    const reason = container.querySelector(
      "input[aria-label='合作方变更原因']",
    ) as HTMLInputElement;
    expect(create.disabled).toBe(true);
    await act(async () => {
      setInput(name, "合作方乙");
      setInput(reason, "ab");
    });
    expect(create.disabled).toBe(true);
    await act(async () => setInput(reason, "新增试点"));
    expect(create.disabled).toBe(false);

    await act(async () => create.click());
    await settle();

    const mutationCalls = fetchMock.mock.calls.filter(
      (call) => call[1]?.method === "POST",
    );
    expect(mutationCalls).toHaveLength(1);
    expect(JSON.parse(String(mutationCalls[0][1]?.body))).toEqual({
      display_name: "合作方乙",
      reason: "新增试点",
      request_id: requestId,
    });
    expect(globalThis.crypto.randomUUID).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("变更成功");
  });

  it("shows a persistent explicit indeterminate state on a mutation 5xx", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const fetchMock = safeFetch();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return json({
          detail: { code: "partner_mutation_indeterminate", request_id: requestId },
        }, 503);
      }
      const path = String(input);
      if (path.endsWith("/organizations")) return json({ organizations: [organization] });
      if (path.endsWith("/operators")) return json({ operators: [operator] });
      return json({ binding_requests: [bindingRequest] });
    });
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    await act(async () => {
      setInput(
        container.querySelector("input[aria-label='合作方名称']") as HTMLInputElement,
        "合作方乙",
      );
      setInput(
        container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
        "新增试点",
      );
    });
    const create = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "创建合作方",
    ) as HTMLButtonElement;

    await act(async () => create.click());
    await settle();

    expect(container.textContent).toContain("结果无法确认");
    expect(container.textContent).not.toContain("变更成功");
    expect(JSON.parse(String(sessionStorage.getItem(
      `platform.identity.pending-partner.v1:${owner.internal_user_id}`,
    )))).toMatchObject({ kind: "indeterminate", request_id: requestId });
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent !== "刷新合作方状态")
      .every((button) => button.hasAttribute("disabled"))).toBe(true);

    await act(async () => root.unmount());
    root = createRoot(container);
    vi.stubGlobal("fetch", safeFetch());
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();

    expect(container.textContent).toContain("结果无法确认");
    expect(container.textContent).not.toContain("变更成功");
    expect(sessionStorage.getItem(
      `platform.identity.pending-partner.v1:${owner.internal_user_id}`,
    )).not.toBeNull();
  });

  it("canonicalizes corrupt persisted state and keeps mutations blocked", async () => {
    const key = `platform.identity.pending-partner.v1:${owner.internal_user_id}`;
    sessionStorage.setItem(key, JSON.stringify({
      version: 1,
      kind: "indeterminate",
      request_id: "not-a-uuid",
      label: "tampered",
      unexpected: true,
    }));
    vi.stubGlobal("fetch", safeFetch());

    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();

    expect(container.textContent).toContain("响应校验失败");
    expect(JSON.parse(String(sessionStorage.getItem(key)))).toEqual({
      version: 1,
      kind: "integrity_failure",
    });
    expect([...container.querySelectorAll("button")]
      .filter((button) => button.textContent !== "刷新合作方状态")
      .every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("keeps confirmed mutations blocked until refresh matches the response", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    const expectedOrganization = { ...organization, display_name: "合作方乙" };
    let reconciled = false;
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") {
        return json({ request_id: requestId, organization: expectedOrganization });
      }
      if (path.endsWith("/organizations")) {
        return json({ organizations: [reconciled ? expectedOrganization : organization] });
      }
      if (path.endsWith("/operators")) return json({ operators: [operator] });
      return json({ binding_requests: [bindingRequest] });
    }));
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    await act(async () => {
      setInput(
        container.querySelector("input[aria-label='合作方名称']") as HTMLInputElement,
        "合作方乙",
      );
      setInput(
        container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
        "新增试点",
      );
    });
    const create = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "创建合作方",
    ) as HTMLButtonElement;

    await act(async () => create.click());
    await settle();

    expect(container.textContent).toContain("未能与响应一致");
    expect(container.textContent).not.toContain("变更成功");
    expect(JSON.parse(String(sessionStorage.getItem(
      `platform.identity.pending-partner.v1:${owner.internal_user_id}`,
    )))).toMatchObject({ kind: "confirmed", request_id: requestId });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    expect(container.textContent).toContain("未能与响应一致");

    reconciled = true;
    const refresh = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "刷新合作方状态",
    ) as HTMLButtonElement;
    await act(async () => refresh.click());
    await settle();

    expect(container.textContent).toContain("变更成功");
    expect(sessionStorage.getItem(
      `platform.identity.pending-partner.v1:${owner.internal_user_id}`,
    )).toBeNull();
  });

  it("fails closed when a successful mutation response omits its projection", async () => {
    const requestId = "60000000-0000-4000-8000-000000000001";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const validFetch = safeFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") {
        const body = JSON.parse(String(init.body)) as { request_id: string };
        return json({ request_id: body.request_id });
      }
      return validFetch(input, init);
    }));
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    await act(async () => {
      setInput(
        container.querySelector("input[aria-label='合作方名称']") as HTMLInputElement,
        "合作方乙",
      );
      setInput(
        container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
        "新增试点",
      );
    });
    const create = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "创建合作方",
    ) as HTMLButtonElement;

    await act(async () => create.click());
    await settle();

    expect(container.textContent).toContain("响应校验失败");
    expect(container.textContent).not.toContain("变更成功");
    expect(JSON.parse(String(sessionStorage.getItem(
      `platform.identity.pending-partner.v1:${owner.internal_user_id}`,
    )))).toMatchObject({ kind: "integrity_failure", request_id: requestId });
  });

  it("fails closed if a read response contains a raw provider identity field", async () => {
    const fetchMock = safeFetch();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/organizations")) return json({ organizations: [organization] });
      if (path.endsWith("/operators")) return json({ operators: [operator] });
      return json({
        binding_requests: [{ ...bindingRequest, provider_subject: "raw-seat-42" }],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();

    expect(container.textContent).not.toContain("raw-seat-42");
    expect(container.textContent).toContain("无法读取合作方访问状态");
  });

  it("keeps FAE revocation available for an inactive operator", async () => {
    const fetchMock = safeFetch();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/organizations")) {
        return json({ organizations: [organization] });
      }
      if (path.endsWith("/operators")) {
        return json({
          operators: [{
            ...operator,
            status: "suspended",
            fae_grant_active: true,
            fae_granted_at: now,
          }],
        });
      }
      return json({ binding_requests: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    await act(async () => setInput(
      container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
      "紧急撤权",
    ));

    const revoke = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "撤销 FAE",
    ) as HTMLButtonElement;
    expect(revoke).toBeTruthy();
    expect(revoke.disabled).toBe(false);
  });

  it("requires and accepts an explicit binding target with one operator", async () => {
    vi.stubGlobal("fetch", safeFetch());
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    const select = container.querySelector(
      `select[aria-label='${bindingRequestId}绑定坐席']`,
    ) as HTMLSelectElement;
    const bind = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "绑定到坐席",
    ) as HTMLButtonElement;

    expect(select.querySelector("option[value='']")?.textContent).toBe("请选择坐席");
    expect(select.value).toBe("");
    await act(async () => setInput(
      container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
      "名单核验通过",
    ));
    expect(bind.disabled).toBe(true);

    await act(async () => {
      select.value = operatorId;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(bind.disabled).toBe(false);
    await act(async () => bind.click());
    await settle();
    expect(container.textContent).toContain("变更成功");
  });

  it("drops an inactive operator-creation organization after refresh", async () => {
    const secondOrganizationId = "20000000-0000-4000-8000-000000000002";
    const secondOrganization = {
      ...organization,
      partner_organization_id: secondOrganizationId,
      display_name: "合作方乙",
    };
    let organizations = [organization, secondOrganization];
    let operators = [operator];
    let createOperatorBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "PATCH" && path.includes(secondOrganizationId)) {
        const body = JSON.parse(String(init.body)) as { request_id: string };
        const suspended = { ...secondOrganization, status: "suspended" };
        organizations = [organization, suspended];
        return json({ request_id: body.request_id, organization: suspended });
      }
      if (init?.method === "POST" && path.endsWith("/operators")) {
        const body = JSON.parse(String(init.body)) as {
          request_id: string;
          partner_organization_id: string;
          display_name: string;
        };
        createOperatorBody = body;
        const created = {
          ...operator,
          partner_organization_id: body.partner_organization_id,
          display_name: body.display_name,
        };
        operators = [created];
        return json({ request_id: body.request_id, operator: created });
      }
      if (path.endsWith("/organizations")) return json({ organizations });
      if (path.endsWith("/operators")) return json({ operators });
      return json({ binding_requests: [] });
    }));
    await act(async () => root.render(<PartnerAccessPanel account={owner} />));
    await settle();
    const organizationSelect = container.querySelector(
      "select[aria-label='坐席所属合作方']",
    ) as HTMLSelectElement;
    await act(async () => {
      organizationSelect.value = secondOrganizationId;
      organizationSelect.dispatchEvent(new Event("change", { bubbles: true }));
      setInput(
        container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
        "暂停试点",
      );
    });
    const secondCard = [...container.querySelectorAll("article")].find(
      (item) => item.textContent?.includes("合作方乙"),
    ) as HTMLElement;
    const suspend = [...secondCard.querySelectorAll("button")].find(
      (button) => button.textContent === "暂停合作方",
    ) as HTMLButtonElement;

    await act(async () => suspend.click());
    await settle();

    expect(organizationSelect.value).toBe(organizationId);
    await act(async () => {
      setInput(
        container.querySelector("input[aria-label='坐席展示名']") as HTMLInputElement,
        "新坐席",
      );
      setInput(
        container.querySelector("input[aria-label='合作方变更原因']") as HTMLInputElement,
        "新增坐席",
      );
    });
    const createOperator = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "创建坐席",
    ) as HTMLButtonElement;
    await act(async () => createOperator.click());
    await settle();
    expect(createOperatorBody).toMatchObject({
      partner_organization_id: organizationId,
    });
  });

  it("is embedded below enterprise identity controls without partner navigation", async () => {
    vi.stubGlobal("fetch", safeFetch());

    await act(async () => root.render(<IdentityManagementPage account={owner} />));
    await settle();

    const enterprise = container.querySelector(".identity-page");
    const partner = container.querySelector("[data-partner-access-panel]");
    expect(enterprise).toBeTruthy();
    expect(partner).toBeTruthy();
    expect(
      enterprise && partner
        ? Boolean(enterprise.compareDocumentPosition(partner) & Node.DOCUMENT_POSITION_FOLLOWING)
        : false,
    ).toBe(true);
    expect([...container.querySelectorAll("nav a")].some(
      (item) => item.textContent?.includes("合作方"),
    )).toBe(false);
  });
});
