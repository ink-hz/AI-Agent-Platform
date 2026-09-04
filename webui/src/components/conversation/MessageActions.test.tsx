/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageActions } from "./MessageActions";

describe("MessageActions", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("uses accessible icon actions and opens a reason/comment panel before downvote submission", async () => {
    const onCopy = vi.fn().mockResolvedValue(true); const onFeedback = vi.fn();
    await act(async () => root.render(<MessageActions
      copyText={() => "渲染后的回答"} feedbackState={undefined} onCopy={onCopy} onFeedback={onFeedback}
    />));
    const copy = container.querySelector<HTMLButtonElement>('[aria-label="复制回答"]')!;
    const helpful = container.querySelector<HTMLButtonElement>('[aria-label="有用"]')!;
    const unhelpful = container.querySelector<HTMLButtonElement>('[aria-label="不达标"]')!;
    expect(copy.querySelector("svg")).not.toBeNull();
    expect(helpful.querySelector("svg")).not.toBeNull();
    expect(unhelpful.querySelector("svg")).not.toBeNull();

    await act(async () => copy.click());
    expect(onCopy).toHaveBeenCalledWith("渲染后的回答");
    expect(container.querySelector('[aria-label="已复制"]')).not.toBeNull();
    await act(async () => unhelpful.click());
    expect(onFeedback).not.toHaveBeenCalled();
    expect(container.textContent).toContain("文件或格式有问题");
    expect(container.textContent).toContain("来源或时效有问题");
    const reason = [...container.querySelectorAll("button")].find((item) => item.textContent === "来源或时效有问题");
    await act(async () => reason?.click());
    const textarea = container.querySelector("textarea")!;
    const value = "😀".repeat(1001);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(Array.from(textarea.value)).toHaveLength(1000);
    await act(async () => [...container.querySelectorAll("button")].find((item) => item.textContent === "提交反馈")?.click());
    expect(onFeedback).toHaveBeenCalledWith("unhelpful", "source_timeliness", "😀".repeat(1000));
  });
});
