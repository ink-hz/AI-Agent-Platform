/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  currentLocationPath,
  navigate,
  parseRoute,
  routePath,
  routeSection,
  safeLegacyWorkspaceSearch,
} from "./router";


afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});


describe("Platform router", () => {
  it.each([
    ["/hr", { name: "legacy-redirect", to: "/hr/", navigation: "spa" }],
    ["/hr/", { name: "hr" }],
    ["/hr/conversations/c%3A1", { name: "hr-conversation", conversationId: "c:1" }],
    ["/marketing", { name: "legacy-redirect", to: "/marketing/prospecting", navigation: "spa" }],
    ["/marketing/", { name: "legacy-redirect", to: "/marketing/prospecting", navigation: "spa" }],
    ["/marketing/inbound", { name: "marketing", agentSlug: "inbound" }],
    ["/marketing/gtm/conversations/c-2", { name: "marketing-conversation", agentSlug: "gtm", conversationId: "c-2" }],
    ["/fae/manage/", { name: "fae-manage-overview" }],
    ["/fae/manage/sessions/s%3A1", { name: "fae-manage-session", sessionKey: "s:1" }],
    ["/fae/manage/issues/00000000-0000-4000-8000-000000000001", { name: "fae-manage-issue", issueId: "00000000-0000-4000-8000-000000000001" }],
    ["/agents/hr-bot", { name: "legacy-redirect", to: "/hr/", navigation: "spa" }],
    ["/agents/ai-fae-agent", { name: "legacy-redirect", to: "/fae/", navigation: "document" }],
    ["/admin/fae/reports", { name: "legacy-redirect", to: "/fae/manage/reports", navigation: "spa" }],
    ["/admin/voc", { name: "legacy-redirect", to: "/voc/manage/", navigation: "document" }],
  ])("parses %s", (path, expected) => expect(parseRoute(path)).toEqual(expected));

  it("parses use, account and unknown routes", () => {
    expect(parseRoute("/")).toEqual({ name: "brain" });
    expect(parseRoute("/agents")).toEqual({ name: "agents" });
    expect(parseRoute("/agents/ai-fae-agent")).toEqual({ name: "legacy-redirect", to: "/fae/", navigation: "document" });
    expect(parseRoute("/agents/voc/workspace")).toEqual({
      name: "legacy-redirect", to: "/voc/", navigation: "document",
    });
    expect(parseRoute("/admin/voc")).toEqual({
      name: "legacy-redirect", to: "/voc/manage/", navigation: "document",
    });
    expect(parseRoute("/missions/one")).toEqual({ name: "mission", missionId: "one" });
    expect(parseRoute("/unknown")).toEqual({ name: "not-found" });
    expect(parseRoute("/login")).toEqual({ name: "login" });
    expect(parseRoute("/account")).toEqual({ name: "account" });
  });

  it("parses and serializes AI notes routes", () => {
    expect(parseRoute("/ai-notes")).toEqual({ name: "ai-notes" });
    const route = parseRoute("/ai-notes/agent-architecture/system-handbook");
    expect(route).toEqual({
      name: "ai-note",
      categorySlug: "agent-architecture",
      articleSlug: "system-handbook",
    });
    expect(routePath(route)).toBe("/ai-notes/agent-architecture/system-handbook");
    expect(routeSection(route)).toBe("ai-notes");
  });

  it("rejects malformed AI notes paths", () => {
    expect(parseRoute("/ai-notes/a/b/c")).toEqual({ name: "not-found" });
    expect(parseRoute("/ai-notes/../handbook")).toEqual({ name: "not-found" });
    expect(parseRoute("/ai-notes/UPPER/handbook")).toEqual({ name: "not-found" });
  });

  it("parses and navigates inside the isolated preview prefix", () => {
    window.history.replaceState({}, "", "/_preview/dingtalk-r1/account");
    expect(parseRoute(window.location.pathname)).toEqual({ name: "account" });
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);

    navigate("/agents");

    expect(window.location.pathname).toBe("/_preview/dingtalk-r1/agents");
  });

  it("creates encoded canonical detail paths", () => {
    expect(routePath({ name: "mission", missionId: "a/b" })).toBe("/missions/a%2Fb");
    expect(routePath({ name: "agent", agentId: "ai-fae-agent" })).toBe("/agents/ai-fae-agent");
    expect(routePath({ name: "admin-agent-runtime", agentId: "fae/a" })).toBe("/admin/agents/fae%2Fa/runtime");
    expect(routePath({ name: "admin-review" })).toBe("/admin/review");
    expect(routePath({ name: "admin-voc" })).toBe("/admin/voc");
    expect(routePath({ name: "account" })).toBe("/account");
    expect(routePath({ name: "voc-workspace" })).toBe("/agents/voc/workspace");
    expect(routePath({ name: "hr-conversation", conversationId: "c:1" })).toBe("/hr/conversations/c%3A1");
    expect(routePath({ name: "marketing-conversation", agentSlug: "voice", conversationId: "c:2" })).toBe("/marketing/voice/conversations/c%3A2");
    expect(routePath({ name: "fae-manage-report", reportId: "weekly:one" })).toBe("/fae/manage/reports/weekly%3Aone");
  });

  it("keeps detail pages in their parent navigation section", () => {
    expect(routeSection({ name: "agent", agentId: "ai-fae-agent" })).toBe("agents");
    expect(routeSection({ name: "mission", missionId: "one" })).toBe("missions");
    expect(routeSection({ name: "voc-workspace" })).toBe("agents");
    expect(routeSection({ name: "admin-session", sessionKey: "fae:abc" })).toBe("admin");
    expect(routeSection({ name: "admin-voc" })).toBe("admin");
    expect(routeSection({ name: "hr" })).toBe("agents");
    expect(routeSection({ name: "marketing", agentSlug: "inbound" })).toBe("agents");
    expect(routeSection({ name: "fae-manage-overview" })).toBe("fae");
  });

  it("redirects legacy FAE workbench collection and detail routes", () => {
    expect(parseRoute("/admin/fae")).toEqual({ name: "legacy-redirect", to: "/fae/manage/", navigation: "spa" });
    expect(parseRoute("/admin/fae/sessions/fae%3Asession-1")).toEqual({
      name: "legacy-redirect", to: "/fae/manage/sessions/fae%3Asession-1", navigation: "spa",
    });
    expect(parseRoute("/admin/fae/issues/00000000-0000-0000-0000-000000000001")).toEqual({
      name: "legacy-redirect", to: "/fae/manage/issues/00000000-0000-0000-0000-000000000001", navigation: "spa",
    });
    expect(parseRoute("/admin/fae/reports/weekly:2026-08-31")).toEqual({
      name: "legacy-redirect", to: "/fae/manage/reports/weekly:2026-08-31", navigation: "spa",
    });
    expect(parseRoute("/admin/fae/sessions/%E0%A4%A")).toEqual({ name: "not-found" });
  });

  it("treats search changes as navigation", () => {
    window.history.replaceState({}, "", "/admin/sessions?agent_id=one");
    navigate("/admin/sessions?agent_id=two", { replace: true });
    expect(currentLocationPath()).toBe("/admin/sessions?agent_id=two");
  });

  it("preserves caller state in a new history entry", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    navigate("/missions/one", { state: { returnTo: "/missions" } });
    expect(window.history.state).toEqual({ returnTo: "/missions" });
  });

  it("does not assign a legacy redirect to primary navigation", () => {
    expect(routeSection({ name: "legacy-redirect", to: "/admin", navigation: "spa" })).toBeNull();
  });

  it("preserves only valid FAE Session filters on compatibility redirects", () => {
    expect(safeLegacyWorkspaceSearch(
      "/fae/manage/sessions",
      "?q=timeout&sentiment=negative&date_before=2026-08-31T00%3A00%3A00%2B08%3A00&has_subject=true&page=2&unknown=drop",
    )).toBe("?q=timeout&sentiment=negative&date_before=2026-08-31T00%3A00%3A00%2B08%3A00&has_subject=true&page=2");
    expect(safeLegacyWorkspaceSearch(
      "/fae/manage/sessions/s%3A1",
      "?q=one&q=two&sentiment=invalid&date_from=2026-02-30T00%3A00%3A00Z&abnormal=1&page=0",
    )).toBe("");
  });

  it("preserves validated FAE Issue filters and paired turn links", () => {
    expect(safeLegacyWorkspaceSearch(
      "/fae/manage/issues",
      "?status=fixing&priority=P1&failure_layer=model&owner=corp%3Aone&q=timeout&created_after=2026-08-31T00%3A00%3A00%2B08%3A00&page=2&unknown=drop",
    )).toBe("?status=fixing&priority=P1&failure_layer=model&owner=corp%3Aone&q=timeout&created_after=2026-08-31T00%3A00%3A00%2B08%3A00&page=2");
    expect(safeLegacyWorkspaceSearch(
      "/fae/manage/issues",
      "?session_key=fae%3Asession-1&turn_key=turn-2",
    )).toBe("?session_key=fae%3Asession-1&turn_key=turn-2");
    expect(safeLegacyWorkspaceSearch(
      "/fae/manage/issues",
      "?status=fixing&disposition=actionable&session_key=fae%3Asession-1&page=0",
    )).toBe("");
  });

  it("preserves a positive report version only on report details", () => {
    expect(safeLegacyWorkspaceSearch("/fae/manage/reports/weekly%3Aone", "?version=2&unknown=drop")).toBe("?version=2");
    expect(safeLegacyWorkspaceSearch("/fae/manage/reports", "?version=2")).toBe("");
    expect(safeLegacyWorkspaceSearch("/fae/manage/reports/weekly%3Aone", "?version=0")).toBe("");
    expect(safeLegacyWorkspaceSearch("/voc/manage/", "?view=management")).toBe("");
  });
});
