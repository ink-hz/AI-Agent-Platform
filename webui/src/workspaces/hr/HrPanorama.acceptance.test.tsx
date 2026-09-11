/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import companies from "../../../../backend/tests/fixtures/hr_intelligence_company/companies.json";
import detail from "../../../../backend/tests/fixtures/hr_intelligence_company/company-insta360.json";
import type { Account } from "../../auth";
import { HrLegacyPanoramaWorkspace as HrPanoramaWorkspace } from "./HrLegacyPanoramaWorkspace";

const account: Account = { internal_user_id: "member", display_name: "HR", role: "member", departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [], directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "csrf" };
afterEach(() => vi.restoreAllMocks());

it("reads the real-derived company directory and pins its detail request", async () => {
  history.replaceState({}, "", "/hr/panorama"); const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async request => { const path = String(request); requests.push(path); if (path === "/api/hr/panorama/companies") return Response.json(companies); if (path.startsWith(`/api/hr/panorama/companies/${detail.company.company_key}?bundle_id=`)) return Response.json(detail); throw new Error(`unexpected request ${path}`); });
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container); (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  try { await act(async () => { root.render(<HrPanoramaWorkspace account={account} />); await Promise.resolve(); await Promise.resolve(); }); const company = container.querySelector<HTMLButtonElement>(`[data-company-key="${detail.company.company_key}"]`)!; await act(async () => { company.click(); await Promise.resolve(); await Promise.resolve(); }); expect(container.textContent).toContain(detail.company.canonical_name); expect(container.textContent).toContain(detail.units[0].response.summary); expect(requests[1]).toContain(`bundle_id=${companies.bundle_id}`); expect(requests.every(path => !path.includes("reports"))).toBe(true); } finally { await act(async () => root.unmount()); container.remove(); }
});
