/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import type { ConversationMessage } from "../../conversationTypes";
import { ConversationMessages } from "./ConversationMessages";

it("shows a restored user-selected method as user input metadata", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  const message: ConversationMessage = {
    message_id: "m1", conversation_id: "c1", seq: 1, role: "user", content: "设计面试",
    turn_id: "t1", delivery_status: "completed", created_at: "2026-09-08T00:00:00Z", completed_at: "2026-09-08T00:00:01Z",
    input_attachments: [], output_attachments: [], active_attachment_ids: [],
    userSelectedResources: [{ sourceCommit: "abc123", id: "structured-interview", revision: "2026-09-08", sha256: "a".repeat(64) }],
  };
  try {
    await act(async () => root.render(<ConversationMessages messages={[message]} />));
    expect(host.textContent).toContain("用户指定参考");
    expect(host.textContent).toContain("structured-interview · 2026-09-08");
  } finally { await act(async () => root.unmount()); host.remove(); }
});
