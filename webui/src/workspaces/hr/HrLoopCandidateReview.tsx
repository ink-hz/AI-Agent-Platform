import { useEffect, useRef, useState } from "react";
import { platformPath } from "../../auth";
import { issueAttachmentTicket } from "../../attachmentApi";
import {
  candidateError,
  type CandidateChoice,
  type CandidateConfirmation,
  type CandidateItem,
  type HrLoopCandidatesApi,
} from "../../hrLoopCandidatesApi";
import { MessageMarkdown } from "../../components/MessageMarkdown";

export function HrLoopCandidateReview({
  item,
  choices,
  api,
  csrf,
  disabled,
  onChanged,
  onError,
}: {
  item: CandidateItem;
  choices: CandidateChoice[];
  api: HrLoopCandidatesApi;
  csrf: string;
  disabled: boolean;
  onChanged: () => void;
  onError: (error: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [decision, setDecision] = useState("create");
  const [target, setTarget] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [download, setDownload] = useState("");
  const active = useRef(true),
    sending = useRef(false);
  const keys = useRef(new Map<string, string>());
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const hasGaps = !item.coverage_complete || item.unread_ranges.length > 0;
  const allowedTarget = choices.some(
    (candidate) => candidate.available && candidate.candidate_id === target,
  );
  async function confirm() {
    if (
      disabled ||
      sending.current ||
      !name.trim() ||
      !summary.trim() ||
      !item.result_ref ||
      (hasGaps && !reviewed) ||
      (decision === "link_existing" && !allowedTarget)
    )
      return;
    const body: CandidateConfirmation = {
      expected_row_version: item.row_version,
      result_ref: item.result_ref,
      display_name: name.trim(),
      summary: summary.trim(),
      decision:
        decision === "create"
          ? { kind: "create" }
          : { kind: "link_existing", candidate_id: target },
      reviewed_limitations: reviewed,
    };
    const fingerprint = JSON.stringify(body);
    if (!keys.current.has(fingerprint))
      keys.current.set(fingerprint, crypto.randomUUID());
    sending.current = true;
    setBusy(true);
    setError("");
    try {
      await api.confirm(item.item_id, body, keys.current.get(fingerprint)!);
      if (active.current) onChanged();
    } catch (error) {
      if (active.current) {
        setError(candidateError(error));
        onError(error);
      }
    } finally {
      if (active.current) {
        sending.current = false;
        setBusy(false);
      }
    }
  }
  async function original() {
    setDownload("");
    setError("");
    try {
      const ticket = await issueAttachmentTicket(
        item.attachment_id,
        "download",
        csrf,
      );
      if (active.current) setDownload(platformPath(ticket.contentPath));
    } catch (error) {
      if (active.current) {
        setError(candidateError(error));
        onError(error);
      }
    }
  }
  return (
    <section className="hr-loop-candidate-review">
      <details open>
        <summary>研究草稿（供人工参考）</summary>
        <MessageMarkdown content={item.profile_body ?? ""} />
      </details>
      <button type="button" disabled={disabled} onClick={() => void original()}>
        获取原件
      </button>
      {download && (
        <a href={download} target="_blank" rel="noopener noreferrer">
          下载原件
        </a>
      )}
      <form
        aria-label="人工核对材料"
        onSubmit={(event) => {
          event.preventDefault();
          void confirm();
        }}
      >
        <fieldset disabled={disabled || busy}>
          <legend>人工核对后建档</legend>
          <p>姓名与摘要由你填写，研究草稿仅供参考。</p>
          <label>
            姓名
            <input
              aria-label="姓名"
              required
              maxLength={500}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            已审阅摘要
            <textarea
              aria-label="已审阅摘要"
              required
              maxLength={16000}
              rows={4}
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
            />
          </label>
          <label>
            建档方式
            <select
              aria-label="建档方式"
              value={decision}
              onChange={(event) => {
                setDecision(event.target.value);
                setTarget("");
              }}
            >
              <option value="create">新建候选人</option>
              <option value="link_existing">关联已有候选人</option>
            </select>
          </label>
          {decision === "link_existing" && (
            <>
              <label>
                已有候选人
                <select
                  aria-label="已有候选人"
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                >
                  <option value="">请明确选择一位候选人</option>
                  {choices
                    .filter((candidate) => candidate.available)
                    .map((candidate) => (
                      <option
                        key={candidate.candidate_id}
                        value={candidate.candidate_id}
                      >
                        {candidate.display_name} —{" "}
                        {candidate.summary?.slice(0, 100)}
                      </option>
                    ))}
                </select>
              </label>
              <p>
                同名不代表同一人。关联会追加本文件及核对记录，保留已有档案的姓名与摘要。
              </p>
            </>
          )}
          {hasGaps && (
            <label className="hr-loop-review-limit">
              <input
                type="checkbox"
                checked={reviewed}
                onChange={(event) => setReviewed(event.target.checked)}
              />
              我已核对原件，并了解上述解析或阅读限制
            </label>
          )}
          {error && <p role="alert">{error}</p>}
          <button
            type="submit"
            disabled={
              disabled ||
              busy ||
              !name.trim() ||
              !summary.trim() ||
              (hasGaps && !reviewed) ||
              (decision === "link_existing" && !allowedTarget)
            }
          >
            {busy
              ? "正在确认…"
              : decision === "create"
                ? "确认新建候选人"
                : "确认关联已有候选人"}
          </button>
        </fieldset>
      </form>
    </section>
  );
}
