/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessEventReporter, accessEventForRoute } from "./accessEventReporter";
import type { Account } from "./auth";

const account: Account = {
  internal_user_id: "owner", display_name: "苍渊", role: "platform_owner",
  departments: [], gender: null, observation_agent_ids: [], workspace_scopes: [],
  directory_freshness: "fresh", hard_stale_read_only: false, csrf_token: "secret-csrf",
};

describe("page access reporter", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000001") });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it("maps semantic routes without including resource identifiers", () => {
    expect(accessEventForRoute({ name: "conversations" })).toEqual({
      workspace_key: "platform", page_key: "platform.conversations",
    });
    expect(accessEventForRoute({ name: "conversation", conversationId: "private-conversation" })).toEqual({
      workspace_key: "platform", page_key: "platform.conversation",
    });
    expect(accessEventForRoute({ name: "marketing", agentSlug: "prospecting" })).toEqual({
      workspace_key: "marketing", page_key: "marketing.workspace", agent_id: "marketing-prospecting-bot",
    });
    expect(accessEventForRoute({ name: "hr-position", positionId: "private-position" })).toEqual({
      workspace_key: "hr", page_key: "hr.workspace",
    });
    expect(accessEventForRoute({
      name: "hr-position-conversation",
      positionId: "private-position",
      conversationId: "private-conversation",
    })).toEqual({ workspace_key: "hr", page_key: "hr.conversation" });
    expect(accessEventForRoute({ name: "admin-access" })).toEqual({
      workspace_key: "admin", page_key: "admin.access_history",
    });
    expect(accessEventForRoute({ name: "login" })).toBeNull();
    expect(JSON.stringify(accessEventForRoute({ name: "admin-session", sessionKey: "secret-session" }))).not.toContain("secret-session");
  });

  it("reports once per semantic page and never sends identity, CSRF, URL or query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState({}, "", "/conversations/private-id?secret=value#fragment");

    await act(async () => root.render(<AccessEventReporter account={account} route={{ name: "conversation", conversationId: "private-id" }} />));
    await act(async () => root.render(<AccessEventReporter account={account} route={{ name: "conversation", conversationId: "private-id" }} />));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/access-events/page-view");
    expect(init.credentials).toBe("include");
    expect(init.keepalive).toBe(true);
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    const body = String(init.body);
    expect(JSON.parse(body)).toEqual({
      access_event_id: "00000000-0000-4000-8000-000000000001",
      workspace_key: "platform",
      page_key: "platform.conversation",
    });
    expect(body).not.toContain("private-id");
    expect(body).not.toContain("secret");
    expect(body).not.toContain("苍渊");
  });

  it("swallows telemetry failures and reports again after navigating away and back", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => root.render(<AccessEventReporter account={account} route={{ name: "brain" }} />));
    await act(async () => root.render(<AccessEventReporter account={account} route={{ name: "agents" }} />));
    await act(async () => root.render(<AccessEventReporter account={account} route={{ name: "brain" }} />));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(container.textContent).toBe("");
  });

  it("cannot break the product when event id generation is unavailable", async () => {
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => { throw new Error("unsupported"); }) });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(act(async () => root.render(<AccessEventReporter account={account} route={{ name: "brain" }} />))).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
