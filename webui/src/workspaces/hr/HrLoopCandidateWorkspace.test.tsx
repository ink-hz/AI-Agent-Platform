/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HrLoopCandidateWorkspace } from "./HrLoopCandidateWorkspace";
import { beginAttachmentUpload, uploadAttachmentContent, completeAttachmentUpload, fetchConversationAttachment } from "../../attachmentApi";

vi.mock("../../attachmentApi", async (original) => ({
  ...(await original<typeof import("../../attachmentApi")>()),
  beginAttachmentUpload: vi.fn(), uploadAttachmentContent: vi.fn(),
  completeAttachmentUpload: vi.fn(), fetchConversationAttachment: vi.fn(),
}));
const exact = (id: string, revision = "v1") => ({ kind: "result", id, revision, sha256: id.padEnd(64, "a") });
const deferred = <T,>() => { let resolve!: (v: T) => void; return { promise: new Promise<T>((r) => resolve = r), resolve }; };
function candidatesApi() { return {
  candidates: vi.fn().mockResolvedValue({ items: [
    { candidate_id: "a", display_name: "候选人甲", summary: "甲摘要", available: true },
    { candidate_id: "b", display_name: "候选人乙", summary: "乙摘要", available: true },
  ]}),
  candidate: vi.fn(async (id: string) => ({ candidate_id: id, display_name: id === "a" ? "候选人甲" : "候选人乙", summary: `${id}摘要`, position_ids: ["p"], documents: [{ document_id: `doc-${id}`, attachment_id: `att-${id}`, source_ref: { ...exact(`source-${id}`), kind: "material" }, result_ref: exact(`draft-${id}`), reviewed_limitations: true }] })),
  interviewRecords: vi.fn().mockResolvedValue({ items: [] }),
  interviewRecord: vi.fn(), registerInterviewRecord: vi.fn(),
}; }
function loopApi() { return {
  results: vi.fn(async ({ candidate }: {candidate: string}) => ({ items: [{ ref: exact(`associated-${candidate}`), title: `${candidate}成果`, description: "" }], next_cursor: null })),
  result: vi.fn(async (ref: ReturnType<typeof exact>) => ({ ref, kind: ref.id.startsWith("associated") ? "interview_record" : "candidate_profile", title: ref.id, body: `${ref.id}正文`, objects: [{kind:"candidate",id:ref.id.endsWith("a")?"a":"b"}], changes: [], base_standard_ref: null, basis: [] })),
  material: vi.fn().mockResolvedValue({ text_ref: { ...exact("uploaded:text"), kind: "material" } }),
}; }
let root: ReturnType<typeof createRoot>, el: HTMLDivElement;
beforeEach(() => { el = document.createElement("div"); document.body.append(el); root = createRoot(el); (globalThis as never as {IS_REACT_ACT_ENVIRONMENT:boolean}).IS_REACT_ACT_ENVIRONMENT = true; vi.mocked(beginAttachmentUpload).mockResolvedValue({uploadId:"u",attachmentId:"uploaded"} as never); vi.mocked(uploadAttachmentContent).mockResolvedValue({} as never); vi.mocked(completeAttachmentUpload).mockResolvedValue({} as never); vi.mocked(fetchConversationAttachment).mockResolvedValue({attachmentId:"uploaded",state:"ready",displayName:"raw.txt"} as never); });
afterEach(async () => { await act(async () => root.unmount()); el.remove(); vi.clearAllMocks(); });
const button = (name: string) => [...el.querySelectorAll("button")].find((b) => b.textContent === name)!;
async function change(label: string, value: string) { await act(async () => { const n = el.querySelector<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>(`[aria-label="${label}"]`)!; Object.getOwnPropertyDescriptor(n.tagName === "SELECT" ? HTMLSelectElement.prototype : n.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(n, value); n.dispatchEvent(new Event(n.tagName === "SELECT" ? "change" : "input", {bubbles:true})); }); }
async function render(c = candidatesApi(), l = loopApi(), onUse = vi.fn()) { await act(async () => root.render(<HrLoopCandidateWorkspace csrf="csrf" disabled={false} candidatesApi={c as never} api={l as never} onClose={() => {}} onAccessError={() => {}} onUse={onUse}/>)); return {c,l,onUse}; }

it("clears A selections and ignores a late A preview after switching to B", async () => {
  const c = candidatesApi(), l = loopApi(), late = deferred<never>();
  await render(c,l); await change("选择候选人", "a");
  await act(async () => el.querySelector<HTMLInputElement>('[aria-label="选择已确认草稿 draft-a"]')!.click());
  l.result.mockImplementationOnce(() => late.promise);
  await act(async () => button("查看已确认草稿 draft-a").click());
  await change("继续工作的意图", "甲的私有草稿"); await change("选择候选人", "b");
  expect((el.querySelector('[aria-label="继续工作的意图"]') as HTMLTextAreaElement).value).toBe("");
  await act(async () => late.resolve({} as never));
  expect(el.textContent).not.toContain("甲的私有草稿");
  expect(el.querySelector<HTMLInputElement>('[aria-label="选择已确认草稿 draft-b"]')!.checked).toBe(false);
});

it("keeps confirmed drafts separate from associated latest results and uses exact selected refs", async () => {
  const onUse = vi.fn(); await render(candidatesApi(), loopApi(), onUse); await change("选择候选人", "a");
  expect(el.textContent).toContain("已确认草稿"); expect(el.textContent).toContain("关联成果");
  expect(el.textContent).toContain("AI 整理 · associated-a");
  await act(async () => el.querySelector<HTMLInputElement>('[aria-label="选择已确认草稿 draft-a"]')!.click());
  await act(async () => el.querySelector<HTMLInputElement>('[aria-label="选择准确正文 doc-a"]')!.click());
  await act(async () => el.querySelector<HTMLInputElement>('[aria-label="选择关联成果 associated-a"]')!.click());
  await change("继续工作的意图", "基于准确材料准备追问"); await act(async () => button("带所选内容继续工作").click());
  expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ candidateId:"a", positionId:"p", goal:"基于准确材料准备追问", references:[exact("draft-a"), {...exact("source-a"),kind:"material"}, exact("associated-a")] }));
});

it("uploads raw UTF-8 text once, retries registration without duplicating the file, and shows saved only after success", async () => {
  const c = candidatesApi(); c.registerInterviewRecord.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({ record_id:"r", authorship:"user_supplied" });
  await render(c); await change("选择候选人", "a"); await change("面试原文", "用户逐字原文"); await change("原文标题", " 一面原文 ");
  await act(async () => button("登记用户原文").click()); expect(el.textContent).not.toContain("原文已保存");
  await act(async () => button("重试登记").click());
  expect(beginAttachmentUpload).toHaveBeenCalledOnce(); expect(c.registerInterviewRecord).toHaveBeenCalledTimes(2);
  expect(c.registerInterviewRecord.mock.calls[0][1]).toMatchObject({ title:"一面原文", occurred_at:null, position_id:"p", material_ref: expect.objectContaining({id:"uploaded:text"}) });
  expect(el.textContent).toContain("原文已保存"); expect(el.textContent).toContain("用户提供");
});

it("polls a checking upload without uploading its bytes again", async () => {
  vi.useFakeTimers();
  vi.mocked(fetchConversationAttachment).mockResolvedValueOnce({attachmentId:"uploaded",state:"scanning",displayName:"raw.txt"} as never).mockResolvedValueOnce({attachmentId:"uploaded",state:"ready",displayName:"raw.txt"} as never);
  const c = candidatesApi(); c.registerInterviewRecord.mockResolvedValue({record_id:"polled",candidate_id:"a",material_ref:{...exact("uploaded:text"),kind:"material"},title:"标题",occurred_at:null,position_id:"p",interview_plan_ref:null,authorship:"user_supplied",created_at:"now"}); await render(c); await change("选择候选人", "a"); await change("面试原文", "原文"); await change("原文标题", "标题");
  await act(async () => { button("登记用户原文").click(); await vi.advanceTimersByTimeAsync(1500); });
  expect(fetchConversationAttachment).toHaveBeenCalledTimes(2); expect(uploadAttachmentContent).toHaveBeenCalledOnce(); expect(completeAttachmentUpload).toHaveBeenCalledOnce(); expect(c.registerInterviewRecord).toHaveBeenCalledOnce();
  vi.useRealTimers();
});
