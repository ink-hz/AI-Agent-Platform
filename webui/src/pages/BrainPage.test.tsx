/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Account } from "../auth";
import type { Mission, MissionPage } from "../brainTypes";
import { BrainPage, type BrainPageClient } from "./BrainPage";


const account: Account = {
  internal_user_id: "member", display_name: "洛奇", role: "member",
  observation_agent_ids: [], directory_freshness: "fresh",
  hard_stale_read_only: false, csrf_token: "csrf",
};

const mission: Mission = {
  mission_id: "4e2ac19d-00cc-43ca-a953-f678b8bf7029",
  mode: "brain", direct_agent_id: null, status: "planning", cancel_requested: false,
  row_version: 1, created_at: "2026-08-22T10:00:00Z", updated_at: "2026-08-22T10:00:00Z",
  terminal_at: null, prompt: "找视觉人才", content_available: true,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
}


describe("BrainPage", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("is immediately usable after account load and shows only the focused first viewport", async () => {
    const client: BrainPageClient = {
      listMissions: vi.fn().mockResolvedValue({ items: [], next_cursor: null } satisfies MissionPage),
      createSubmission: vi.fn(),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenMission={vi.fn()} />));

    expect(container.querySelector("h1")?.textContent).toBe("Agent 大脑");
    expect(container.querySelector("textarea")?.disabled).toBe(false);
    expect(container.querySelectorAll(".brain-example")).toHaveLength(3);
    expect(container.textContent).toContain("最近任务");
    expect(container.textContent).toContain("专业 Agent");
    expect(container.textContent).not.toContain("累计对话");
    expect(container.textContent).not.toContain("运行摘要");
  });

  it("submits once while pending and opens the persisted Mission URL", async () => {
    const pending = deferred<Mission>();
    const send = vi.fn(() => pending.promise);
    const onOpenMission = vi.fn();
    const client: BrainPageClient = {
      listMissions: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
      createSubmission: vi.fn().mockReturnValue({ idempotencyKey: "request", send }),
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenMission={onOpenMission} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const submit = container.querySelector<HTMLButtonElement>(".brain-submit")!;
    await act(async () => { submit.click(); submit.click(); });
    expect(send).toHaveBeenCalledTimes(1);

    await act(async () => { pending.resolve(mission); await pending.promise; });
    expect(onOpenMission).toHaveBeenCalledWith("/missions/4e2ac19d-00cc-43ca-a953-f678b8bf7029");
  });

  it("retries a failed submission with the same retained operation", async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(mission);
    const createSubmission = vi.fn().mockReturnValue({ idempotencyKey: "same", send });
    const client: BrainPageClient = {
      listMissions: vi.fn().mockResolvedValue({ items: [], next_cursor: null }), createSubmission,
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenMission={vi.fn()} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "找视觉人才");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-submit")?.click());
    expect(container.textContent).toContain("任务暂未提交成功");
    await act(async () => container.querySelector<HTMLButtonElement>(".brain-retry")?.click());

    expect(createSubmission).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("blocks text that exceeds 32 KiB after UTF-8 encoding", async () => {
    const createSubmission = vi.fn();
    const client: BrainPageClient = {
      listMissions: vi.fn().mockResolvedValue({ items: [], next_cursor: null }), createSubmission,
    };
    await act(async () => root.render(<BrainPage account={account} client={client} onOpenMission={vi.fn()} />));
    const textarea = container.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "人".repeat(11_000));
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(container.textContent).toContain("32 KiB");
    expect(container.querySelector<HTMLButtonElement>(".brain-submit")?.disabled).toBe(true);
    expect(createSubmission).not.toHaveBeenCalled();
  });
});
