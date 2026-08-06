/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AttachmentSummary } from "../types";
import { AttachmentList } from "./AttachmentList";


const attachment = (overrides: Partial<AttachmentSummary> = {}): AttachmentSummary => ({
  attachment_id: "attachment/one",
  direction: "user_input",
  display_name: "一份非常非常非常非常非常非常非常长的文件名称.pdf",
  mime_type: "application/pdf",
  size_bytes: 1536,
  received_or_generated_at: "2026-08-03T09:08:07Z",
  archive_status: "available",
  delivery_status: "delivered",
  expires_at: "2027-08-03T09:08:07Z",
  ...overrides,
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { setItem: vi.fn() } });
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: { setItem: vi.fn() } });
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete (globalThis as { localStorage?: unknown }).localStorage;
  delete (globalThis as { sessionStorage?: unknown }).sessionStorage;
  vi.restoreAllMocks();
});


describe("AttachmentList", () => {
  it("renders nothing for an empty attachment array", async () => {
    await act(async () => root.render(<AttachmentList attachments={[]} label="输入附件" />));
    expect(container.innerHTML).toBe("");
  });

  it("shows metadata, archive labels, and actions only for available attachments", async () => {
    const states: AttachmentSummary["archive_status"][] = [
      "pending", "available", "failed", "source_unavailable", "expired",
    ];
    await act(async () => root.render(<AttachmentList
      attachments={states.map((archive_status, index) => attachment({
        attachment_id: String(index), archive_status,
        display_name: index === 1 ? null : `文件-${archive_status}.pdf`,
        mime_type: index === 1 ? "text/plain" : "application/pdf",
      }))}
      label="输入附件"
    />));

    for (const label of ["归档中", "可查看", "归档失败", "历史源文件不可用", "已按一年保留策略清理"]) {
      expect(container.textContent).toContain(label);
    }
    expect(container.textContent).toContain("未命名附件");
    expect(container.textContent).toContain("text/plain");
    expect(container.textContent).toContain("1.5 KB");
    expect(container.textContent).toContain("2026/08/03");
    expect([...container.querySelectorAll("button")].map((button) => button.textContent)).toEqual(["下载"]);
    expect(container.querySelector(".attachment-list")?.getAttribute("aria-label")).toBe("输入附件");
  });

  it("requests a ticket before opening a validated same-origin content path without leaking it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      ticket: "secret-ticket", expires_at: "2026-08-03T09:10:00Z",
      content_path: "/api/attachments/content/secret-ticket",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await act(async () => root.render(<AttachmentList attachments={[attachment()]} label="输入附件" />));

    const preview = [...container.querySelectorAll("button")].find((button) => button.textContent === "查看")!;
    expect(preview.getAttribute("aria-label")).toBe("查看 一份非常非常非常非常非常非常非常长的文件名称.pdf");
    await act(async () => preview.click());

    expect(fetchMock).toHaveBeenCalledWith("/api/attachments/attachment%2Fone/ticket", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose: "preview" }),
    });
    expect(open).toHaveBeenCalledWith("/api/attachments/content/secret-ticket", "_blank", "noopener,noreferrer");
    expect(container.textContent).not.toContain("secret-ticket");
    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(sessionStorage.setItem).not.toHaveBeenCalled();
  });

  it("exposes loading and error states and refuses unvalidated paths", async () => {
    let resolve!: (response: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise((done) => { resolve = done; }));
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await act(async () => root.render(<AttachmentList attachments={[attachment()]} label="输入附件" />));

    const download = [...container.querySelectorAll("button")].find((button) => button.textContent === "下载")!;
    act(() => download.click());
    expect(download.getAttribute("aria-busy")).toBe("true");
    expect(download.textContent).toBe("处理中…");

    await act(async () => resolve(new Response(JSON.stringify({
      ticket: "secret-ticket", expires_at: "2026-08-03T09:10:00Z",
      content_path: "https://storage.example/secret-ticket",
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    expect(open).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("附件访问失败，请重试");
    expect(container.textContent).not.toContain("secret-ticket");
  });
});
