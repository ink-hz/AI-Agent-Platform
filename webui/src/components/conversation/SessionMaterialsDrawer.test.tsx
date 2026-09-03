/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentAttachmentLimits } from "../../brainTypes";
import type { ConversationAttachment } from "../../conversationTypes";
import { SessionMaterialsDrawer } from "./SessionMaterialsDrawer";


const limits: AgentAttachmentLimits = {
  max_file_bytes: 50, max_files_per_message: 5, max_bytes_per_message: 50,
  max_files_per_conversation: 50, max_bytes_per_conversation: 500,
};
const user: ConversationAttachment = {
  attachmentId: "user-1", conversationId: "conversation-1", source: "user",
  displayName: "岗位说明.docx", detectedMime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  sizeBytes: 20, sha256: null, state: "ready", stateReason: null,
  createdAt: "2026-09-03T10:00:00Z", retainedUntil: "2027-09-03T10:00:00Z",
  preview: null, coverage: null,
};
const generated: ConversationAttachment = {
  ...user, attachmentId: "agent-1", source: "agent", displayName: "面试方案.docx",
};

describe("SessionMaterialsDrawer", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      clear: () => values.clear(), getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key),
    }});
    localStorage.clear();
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); localStorage.clear(); delete (globalThis as { localStorage?: unknown }).localStorage; });

  it("groups active, uploaded, and generated materials and remembers the drawer state", async () => {
    const onActiveIdsChange = vi.fn();
    await act(async () => root.render(<SessionMaterialsDrawer
      activeAttachmentIds={["user-1"]}
      attachments={[user, generated]}
      limits={limits}
      onActiveIdsChange={onActiveIdsChange}
    />));

    expect(container.textContent).toContain("本轮启用");
    expect(container.textContent).toContain("已上传材料");
    expect(container.textContent).toContain("生成结果");
    expect(container.textContent).toContain("1 / 50 个");
    expect(container.textContent).toContain("岗位说明.docx");
    expect(container.textContent).toContain("面试方案.docx");

    await act(async () => container.querySelector<HTMLButtonElement>(".session-materials-toggle")?.click());
    expect(localStorage.getItem("platform.session-materials.open")).toBe("false");
    expect(container.querySelector(".session-materials-panel")).toBeNull();
  });
});
