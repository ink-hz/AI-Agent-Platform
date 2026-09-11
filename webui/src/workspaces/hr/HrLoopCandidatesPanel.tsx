import { useEffect, useMemo, useRef, useState } from "react";
import {
  AttachmentApiError,
  attachmentUploadErrorMessage,
  beginAttachmentUpload,
  uploadAttachmentContent,
  completeAttachmentUpload,
  fetchConversationAttachment,
  type AttachmentUpload,
  type AttachmentUploadStage,
} from "../../attachmentApi";
import { HrLoopError } from "../../hrLoopApi";
import {
  candidateError,
  createHrLoopCandidatesApi,
  type CandidateBatch,
  type CandidateChoice,
  type CandidateItem,
  type HrLoopCandidatesApi,
} from "../../hrLoopCandidatesApi";
import { HrLoopCandidateReview } from "./HrLoopCandidateReview";
import "./HrLoopCandidatesPanel.css";

type Upload = {
  id: string;
  file: File;
  upload?: AttachmentUpload;
  contentDone?: boolean;
  completeDone?: boolean;
  state: "uploading" | "checking" | "ready" | "failed" | "registered";
  busy: boolean;
  error?: string;
};
const states: Record<CandidateItem["state"], string> = {
  queued: "等待处理",
  parsing: "正在解析正文",
  profiling: "正在整理研究草稿",
  awaiting_review: "草稿已保存，等待人工核对",
  failed: "本文件处理未完成",
  confirmed: "已人工确认归档",
};
function issue(item: CandidateItem) {
  return (
    (
      {
        processing_not_authorized:
          "处理权限待开通，请由组织配置获准的材料处理服务。",
        configuration_unavailable: "处理服务暂不可用。",
        source_unavailable: "原件已不可用或权限发生变化，请核对材料。",
        unsupported_kind: "文件格式暂不支持解析。",
        empty_text: "没有可读正文，请核对原件或提供可读文件。",
        parse_failed: "正文解析失败，可单独重试。",
        profile_missing: "尚未保存可供核对的研究草稿。",
        profile_unread: "尚未完成有效正文阅读，不能作为可核对草稿。",
        profile_scope_changed: "研究范围发生变化，请重新核对本文件。",
      } as Record<string, string>
    )[item.error_code ?? ""] ?? "处理尚未完成，请查看状态或单独重试。"
  );
}
export function HrLoopCandidatesPanel({
  api: injectedApi,
  csrf,
  disabled,
  budgetProfile,
  positionId,
  positionTitle,
  onClose,
  onAccessError,
  onOpenWork,
}: {
  api?: HrLoopCandidatesApi;
  csrf: string;
  disabled: boolean;
  budgetProfile: string | null;
  positionId?: string;
  positionTitle?: string;
  onClose: () => void;
  onAccessError: (error: unknown) => void;
  onOpenWork: (id: string) => void;
}) {
  const api = useMemo(
    () => injectedApi ?? createHrLoopCandidatesApi(csrf),
    [injectedApi, csrf],
  );
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [batches, setBatches] = useState<
    { batch_id: string; created_at: string }[]
  >([]);
  const [choices, setChoices] = useState<CandidateChoice[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<CandidateBatch | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [goal, setGoal] = useState(
    "逐份阅读本文件，整理可核对的经历、证据与未知项，并保存研究草稿供人工核对。",
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [retrying, setRetrying] = useState<string[]>([]);
  const active = useRef(true),
    denied = useRef(false),
    mutation = useRef(false);
  const retries = useRef(new Set<string>()),
    keys = useRef(new Map<string, string>());
  const controller = useRef(new AbortController());
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const batchEpoch = useRef(0);
  const current = () => active.current && !denied.current;
  const blocked = disabled || denied.current;
  function report(error: unknown) {
    if (!current()) return;
    const access =
      error instanceof AttachmentApiError
        ? new HrLoopError(error.status, "", {})
        : error;
    setError(candidateError(access));
    if (access instanceof HrLoopError && [401, 403].includes(access.status)) {
      denied.current = true;
      controller.current.abort();
      batchEpoch.current++;
      setUploads([]);
      setBatches([]);
      setBatch(null);
      setChoices([]);
      setNames({});
      setGoal("");
      onAccessError(access);
    }
  }
  const keyFor = (operation: string, body: unknown) => {
    const fingerprint = operation + JSON.stringify(body);
    if (!keys.current.has(fingerprint))
      keys.current.set(fingerprint, crypto.randomUUID());
    return keys.current.get(fingerprint)!;
  };
  useEffect(() => {
    active.current = true;
    if (controller.current.signal.aborted)
      controller.current = new AbortController();
    return () => {
      active.current = false;
      controller.current.abort();
      batchEpoch.current++;
      for (const timer of timers.current) clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    let live = true;
    void Promise.all([api.batches(), api.candidates()])
      .then(([list, candidates]) => {
        if (live && current()) {
          setBatches(list.items);
          setChoices(candidates.items);
        }
      })
      .catch((error) => {
        if (live) report(error);
      });
    return () => {
      live = false;
    };
  }, [api, reload]);
  useEffect(() => {
    if (!batchId) return;
    const id = batchId,
      epoch = ++batchEpoch.current;
    let live = true,
      timer: ReturnType<typeof setTimeout>;
    const valid = () => live && current() && epoch === batchEpoch.current;
    async function refresh() {
      try {
        const value = await api.batch(id);
        if (!valid()) return;
        const metadata = await Promise.allSettled(
          value.items.map((item) =>
            fetchConversationAttachment(
              item.attachment_id,
              controller.current.signal,
            ),
          ),
        );
        if (!valid()) return;
        const labels: Record<string, string> = {};
        const items = value.items.map((item, index) => {
          const info = metadata[index];
          if (info.status === "fulfilled") {
            if (info.value.state === "ready") {
              labels[item.attachment_id] = info.value.displayName;
              return item;
            }
          } else {
            if (
              info.reason instanceof AttachmentApiError &&
              [401, 403].includes(info.reason.status)
            )
              throw info.reason;
            if (
              !(info.reason instanceof AttachmentApiError) ||
              ![404, 410].includes(info.reason.status)
            )
              return item;
          }
          return {
            ...item,
            state: "failed" as const,
            error_code: "source_unavailable",
            profile_body: null,
            result_ref: null,
            text_ref: null,
            original_ref: null,
            parse_state: "unavailable",
            coverage_notes: [],
            unread_ranges: [],
            work_id: null,
          };
        });
        setNames(labels);
        setBatch({ ...value, items });
        if (
          items.some((item) =>
            ["queued", "parsing", "profiling"].includes(item.state),
          )
        )
          timer = setTimeout(() => void refresh(), 2500);
      } catch (error) {
        if (valid()) {
          setBatch(null);
          setNames({});
          report(error);
        }
      }
    }
    void refresh();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [api, batchId, reload]);
  const update = () => {
    if (current()) setUploads((old) => [...old]);
  };
  async function upload(entry: Upload) {
    if (blocked || !current() || entry.busy) return;
    entry.busy = true;
    entry.error = undefined;
    entry.state = "uploading";
    update();
    let stage: AttachmentUploadStage = "begin";
    try {
      entry.upload ??= await beginAttachmentUpload(
        null,
        entry.file,
        csrf,
        controller.current.signal,
      );
      if (!current()) return;
      if (!entry.contentDone) {
        stage = "content";
        await uploadAttachmentContent(
          entry.upload.uploadId,
          entry.file,
          csrf,
          controller.current.signal,
        );
        entry.contentDone = true;
      }
      if (!current()) return;
      if (!entry.completeDone) {
        stage = "complete";
        await completeAttachmentUpload(
          entry.upload.uploadId,
          csrf,
          controller.current.signal,
        );
        entry.completeDone = true;
      }
      if (!current()) return;
      stage = "processing";
      const material = await fetchConversationAttachment(
        entry.upload.attachmentId,
        controller.current.signal,
      );
      if (!current()) return;
      if (material.state === "ready") entry.state = "ready";
      else if (
        ["quarantined", "rejected", "deleted"].includes(material.state)
      ) {
        entry.state = "failed";
        entry.error = "文件未通过检查或已不可用，请核对后更换文件。";
      } else {
        entry.state = "checking";
        const timer = setTimeout(() => {
          timers.current.delete(timer);
          void upload(entry);
        }, 1500);
        timers.current.add(timer);
      }
    } catch (error) {
      if (current()) {
        entry.state = "failed";
        entry.error = attachmentUploadErrorMessage(error, stage);
        if (
          error instanceof AttachmentApiError &&
          [401, 403].includes(error.status)
        )
          report(error);
      }
    } finally {
      entry.busy = false;
      update();
    }
  }
  function addFiles(files: File[]) {
    if (blocked || !current()) return;
    if (files.length + uploads.length > 100) {
      setError("每次最多保留100份待登记材料，请先处理已有文件。");
      return;
    }
    const entries: Upload[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      state: "uploading",
      busy: false,
    }));
    setUploads((old) => [...old, ...entries]);
    let next = 0;
    void Promise.all(
      Array.from({ length: Math.min(3, entries.length) }, async () => {
        while (next < entries.length && current())
          await upload(entries[next++]);
      }),
    );
  }
  const ready = uploads.filter(
    (entry) => entry.state === "ready" && entry.upload,
  );
  async function register() {
    if (
      blocked ||
      mutation.current ||
      !current() ||
      !budgetProfile ||
      !goal.trim() ||
      !ready.length
    )
      return;
    const selected = [...ready];
    const body = {
      attachment_ids: selected.map((entry) => entry.upload!.attachmentId),
      position_id: positionId ?? null,
      text: goal.trim(),
      budget_profile: budgetProfile,
    };
    mutation.current = true;
    setBusy(true);
    setError("");
    try {
      const receipt = await api.createBatch(body, keyFor("batch", body));
      if (current()) {
        for (const entry of selected) entry.state = "registered";
        update();
        setBatch(null);
        setBatchId(receipt.batch_id);
        setReload((value) => value + 1);
        setNotice(
          "就绪材料已登记，后续逐文件处理。人工确认前不会建立候选人档案。",
        );
      }
    } catch (error) {
      report(error);
    } finally {
      if (current()) {
        mutation.current = false;
        setBusy(false);
      }
    }
  }
  async function retry(item: CandidateItem) {
    if (
      blocked ||
      retries.current.has(item.item_id) ||
      !item.failed_stage ||
      item.error_code === "source_unavailable"
    )
      return;
    retries.current.add(item.item_id);
    setRetrying([...retries.current]);
    setError("");
    const body = {
      expected_row_version: item.row_version,
      stage: item.failed_stage,
    };
    try {
      await api.retry(
        item.item_id,
        body,
        keyFor("retry:" + item.item_id, body),
      );
      if (current()) {
        batchEpoch.current++;
        setReload((value) => value + 1);
      }
    } catch (error) {
      report(error);
    } finally {
      if (current()) {
        retries.current.delete(item.item_id);
        setRetrying([...retries.current]);
      }
    }
  }
  return (
    <section
      className="hr-loop-method hr-loop-candidates"
      aria-label="批量简历材料"
    >
      <header>
        <h2>批量简历材料</h2>
        <button type="button" onClick={onClose}>
          关闭材料面板
        </button>
      </header>
      <p>
        逐份保留解析、研究和人工核对状态。分析需使用组织已开通的获准处理服务。
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <label>
        选择简历材料
        <input
          type="file"
          multiple
          accept=".pdf,.docx,.txt"
          disabled={blocked || busy}
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </label>
      <div aria-label="待登记文件">
        {uploads.map((entry) => (
          <article className="hr-loop-upload" key={entry.id}>
            <strong>{entry.file.name}</strong>
            <p>
              {entry.error ??
                {
                  uploading: "正在上传…",
                  checking: "文件正在检查，尚不能登记。",
                  ready: "文件检查就绪，可登记。",
                  failed: "上传尚未完成。",
                  registered: "已登记，处理状态见下方批次。",
                }[entry.state]}
            </p>
            {entry.state === "failed" && (
              <button
                type="button"
                disabled={blocked || entry.busy}
                onClick={() => void upload(entry)}
              >
                重试上传
              </button>
            )}
            {["ready", "failed"].includes(entry.state) && (
              <button
                type="button"
                disabled={blocked || busy}
                onClick={() =>
                  setUploads((old) =>
                    old.filter((other) => other.id !== entry.id),
                  )
                }
              >
                移除待登记文件
              </button>
            )}
          </article>
        ))}
      </div>
      <label>
        本批次处理目标
        <textarea
          aria-label="本批次处理目标"
          rows={3}
          maxLength={32000}
          disabled={blocked || busy}
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
        />
      </label>
      <p>
        {positionId
          ? `本批次关联岗位：${positionTitle ?? "已选岗位"}`
          : "本批次暂不关联岗位。"}
      </p>
      <button
        type="button"
        disabled={
          blocked || busy || !budgetProfile || !goal.trim() || !ready.length
        }
        onClick={() => void register()}
      >
        登记已就绪材料
      </button>
      <section aria-label="最近材料批次">
        <h3>最近材料批次</h3>
        {batches.length === 0 && <p>暂无已登记批次。</p>}
        {batches.map((value, index) => (
          <div key={value.batch_id}>
            <button
              type="button"
              onClick={() => {
                batchEpoch.current++;
                setBatch(null);
                setBatchId(value.batch_id);
                setReload((count) => count + 1);
              }}
            >
              查看批次 {index + 1}
            </button>{" "}
            <time dateTime={value.created_at}>
              {new Date(value.created_at).toLocaleString("zh-CN")}
            </time>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            setError("");
            setReload((value) => value + 1);
          }}
        >
          重新加载状态
        </button>
      </section>
      {batchId && !batch && !error && <p role="status">正在读取逐文件状态…</p>}
      {batch && (
        <section aria-label="逐文件处理状态">
          {batch.items.map((item, index) => (
            <article className="hr-loop-candidate-item" key={item.item_id}>
              <h3>{names[item.attachment_id] ?? `材料 ${index + 1}`}</h3>
              <p>
                {item.error_code === "processing_not_authorized"
                  ? "处理权限待开通"
                  : states[item.state]}
              </p>
              {item.state === "failed" && <p role="alert">{issue(item)}</p>}
              {item.parse_state === "ready" && (
                <p>
                  {item.coverage_complete
                    ? "文本解析完成，仍需人工核对。"
                    : "正文部分可读，请核对原件。"}
                </p>
              )}
              {item.coverage_notes?.map((note, i) => (
                <p key={i}>{note}</p>
              ))}
              {item.unread_ranges?.length > 0 && (
                <p>
                  仍有 {item.unread_ranges.length}{" "}
                  段正文未读，请核对原件与研究限制。
                </p>
              )}
              {item.work_state === "waiting_budget" && (
                <p>研究已暂停，等待你为该文件追加额度。</p>
              )}
              {item.work_state === "waiting_user" && (
                <p>研究正在等待你的补充。</p>
              )}
              {item.work_id && (
                <button type="button" onClick={() => onOpenWork(item.work_id!)}>
                  查看处理工作
                </button>
              )}
              {item.state === "failed" &&
                item.failed_stage &&
                item.error_code !== "source_unavailable" && (
                  <button
                    type="button"
                    disabled={blocked || retrying.includes(item.item_id)}
                    onClick={() => void retry(item)}
                  >
                    {item.error_code === "processing_not_authorized"
                      ? "权限开通后重试"
                      : item.failed_stage === "parse"
                        ? "重试正文解析"
                        : "重试研究草稿"}
                  </button>
                )}
              {item.state === "awaiting_review" &&
                item.result_ref &&
                item.profile_body !== null && (
                  <HrLoopCandidateReview
                    key={`${item.item_id}:${item.row_version}:${item.result_ref.sha256}`}
                    item={item}
                    choices={choices}
                    api={api}
                    csrf={csrf}
                    disabled={blocked}
                    onError={report}
                    onChanged={() => {
                      batchEpoch.current++;
                      setNotice("材料已人工确认归档。");
                      setReload((value) => value + 1);
                    }}
                  />
                )}
            </article>
          ))}
        </section>
      )}
    </section>
  );
}
