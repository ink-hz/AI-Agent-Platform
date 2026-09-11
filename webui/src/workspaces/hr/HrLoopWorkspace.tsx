import { useEffect, useMemo, useRef, useState } from "react";
import type { Account } from "../../auth";
import { platformPath } from "../../auth";
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition } from "../../hrTypes";
import {
  createHrLoopApi,
  HrLoopError,
  readPages,
  readStream,
  type HrLoopApi,
  type ExactRef,
  type WorkObject,
  type WorkView,
  type Message,
  type WorkEvent,
  type SavedResult,
  type StandardView,
  type ResourceItem,
  type MaterialView,
  type BudgetAddition,
  type ExtendInput,
} from "../../hrLoopApi";
import {
  beginAttachmentUpload,
  uploadAttachmentContent,
  completeAttachmentUpload,
  type AttachmentUpload,
} from "../../attachmentApi";
import { MessageMarkdown } from "../../components/MessageMarkdown";
import { HrLoopMethodPreview } from "./HrLoopMethodPreview";
import type { HrLoopCandidatesApi } from "../../hrLoopCandidatesApi";
import { HrLoopCandidatesPanel } from "./HrLoopCandidatesPanel";
import { HrLoopIntelligencePicker } from "./HrLoopIntelligencePicker";
import { HrPositionPicker } from "./HrPositionPicker";
import { HrWorkspaceShell } from "./HrWorkspaceShell";
import "./HrLoopWorkspace.css";

type Props = {
  account: Account;
  api?: HrLoopApi;
  positionApi?: HrApi;
  candidatesApi?: HrLoopCandidatesApi;
  initialPositionId?: string;
  initialWorkId?: string;
};
type Upload = {
  file: File;
  upload?: AttachmentUpload;
  contentDone?: boolean;
  completeDone?: boolean;
  material?: MaterialView;
  error?: string;
  parseKey: string;
};
const identity = (ref: ExactRef) =>
  `${ref.kind}:${ref.id}:${ref.revision}:${ref.sha256}`;
const states: Record<string, string> = {
  queued: "等待开始",
  running: "正在研究",
  waiting_user: "等待你的补充",
  waiting_budget: "已暂停，等待追加额度",
  completed: "回答结束",
  cancelled: "已停止",
  failed: "本次执行失败",
  blocked: "工作暂停，需要处理",
};
const toolStates: Record<string, string> = {
  missing: "所需资料缺失",
  unavailable: "资料暂时不可用",
  forbidden: "没有访问权限",
  conflict: "内容已更新，需要重新阅读",
  invalid: "本次工具请求未完成",
};
function friendly(error: unknown) {
  return error instanceof HrLoopError
    ? error.message
    : "服务暂时不可用，请稍后重试。";
}

function materialStatus(material?: MaterialView) {
  if (!material) return "正在上传与检查…";
  if (["quarantined", "rejected"].includes(material.state))
    return "文件未通过检查，无法作为本次参考。请核对后重新上传。";
  if (["deleted", "expired"].includes(material.state))
    return "文件已删除或超过保留期，无法读取，请重新上传。";
  if (material.state !== "ready") return "文件正在检查，尚不能读取正文。";
  if (material.parse_state === "ready")
    return material.coverage_complete
      ? "正文可读。"
      : "正文部分可读；图片、扫描内容或部分页面未覆盖，请核对原件。";
  if (["failed", "unsupported"].includes(material.parse_state))
    return "正文无法解析，请核对文件后重新上传。";
  return "正在解析正文…";
}

export function HrLoopWorkspace(props: Props) {
  // A new account/route cannot inherit any private draft or outstanding response.
  return (
    <Workspace
      key={`${props.account.internal_user_id}:${props.account.csrf_token}:${props.initialWorkId ?? ""}:${props.initialPositionId ?? ""}`}
      {...props}
    />
  );
}
function Workspace({
  account,
  api: injectedApi,
  positionApi: injectedPositions,
  candidatesApi,
  initialPositionId,
  initialWorkId,
}: Props) {
  const api = useMemo(
    () => injectedApi ?? createHrLoopApi(account.csrf_token),
    [injectedApi, account.csrf_token],
  );
  const positionApi = useMemo(
    () => injectedPositions ?? createHrApi(account.csrf_token),
    [injectedPositions, account.csrf_token],
  );
  const [configuration, setConfiguration] = useState<Awaited<
    ReturnType<HrLoopApi["configuration"]>
  > | null>(null);
  const [showCandidates, setShowCandidates] = useState(false);
  const [methods, setMethods] = useState<ResourceItem[]>([]);
  const [intelligence, setIntelligence] = useState<{
    ref?: ExactRef;
    request: number;
  } | null>(null);
  const [method, setMethod] = useState<{
    ref: ExactRef;
    text: string;
    title: string;
  } | null>(null);
  const [threads, setThreads] = useState<
    { thread_id: string; title: string }[]
  >([]);
  const [history, setHistory] = useState<WorkView[]>([]);
  const [workId, setWorkId] = useState(initialWorkId);
  const [work, setWork] = useState<WorkView | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<WorkEvent[]>([]);
  const [objects, setObjects] = useState<WorkObject[]>(
    initialPositionId ? [{ kind: "position", id: initialPositionId }] : [],
  );
  const [references, setReferences] = useState<ExactRef[]>([]);
  const [unavailableIntelligence, setUnavailableIntelligence] = useState<
    string[]
  >([]);
  const [text, setText] = useState("");
  const [previousInput, setPreviousInput] = useState("");
  const [position, setPosition] = useState<HrPosition | null>(null);
  const [positionId, setPositionId] = useState(initialPositionId);
  const [results, setResults] = useState<SavedResult[]>([]);
  const [positionResults, setPositionResults] = useState<SavedResult[]>([]);
  const [standard, setStandard] = useState<StandardView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [addition, setAddition] = useState<BudgetAddition>({
    model_calls: 0,
    total_tokens: 0,
    active_seconds: 0,
  });
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);
  const active = useRef(true);
  const mutationBusy = useRef(false);
  const keys = useRef(new Map<string, string>());
  const revision = useRef(0);
  const budgetAttempt = useRef<{
    fingerprint: string;
    body: ExtendInput;
  } | null>(null);
  const methodRequest = useRef(0);
  const threadRequest = useRef(0);
  const currentPosition = useRef(positionId);
  currentPosition.current = positionId;
  const keyFor = (operation: string, body: unknown) => {
    const k = operation + JSON.stringify(body);
    let value = keys.current.get(k);
    if (!value) {
      value = crypto.randomUUID();
      keys.current.set(k, value);
    }
    return value;
  };
  const current = (value: number) => active.current && epoch.current === value;
  const disabled = account.hard_stale_read_only || busy;
  const hasUnavailableIntelligence = references.some((ref) =>
    unavailableIntelligence.includes(identity(ref)),
  );
  function clearPrivate() {
    revision.current = 0;
    setObjects([]);
    setMessages([]);
    setEvents([]);
    setResults([]);
    setPositionResults([]);
    setStandard(null);
    setReferences([]);
    setUnavailableIntelligence([]);
    setPreviousInput("");
    setWork(null);
    setMethod(null);
    setIntelligence(null);
    setShowCandidates(false);
    setUploads([]);
  }
  function report(error: unknown) {
    setError(friendly(error));
    if (
      error instanceof HrLoopError &&
      [401, 403, 404, 410].includes(error.status)
    ) {
      epoch.current++;
      mutationBusy.current = false;
      setBusy(false);
      setConfiguration(null);
      clearPrivate();
      // Revocation clears the entire private view; ordinary work selection retains navigation.
      setThreads([]);
      setHistory([]);
      setMethods([]);
      setPosition(null);
      setPositionId(undefined);
      setText("");
      setNotice("");
      setReason("");
      setAddition({ model_calls: 0, total_tokens: 0, active_seconds: 0 });
      keys.current.clear();
      budgetAttempt.current = null;
    }
  }
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    let live = true;
    const selection = epoch.current;
    const valid = () => live && current(selection);
    void Promise.all([
      api.configuration(),
      api.knowledge(),
      readPages((c) => api.threads(c)),
    ])
      .then(([config, knowledge, list]) => {
        if (valid()) {
          setConfiguration(config);
          setMethods(knowledge.items);
          setThreads(list);
        }
      })
      .catch((e) => {
        if (valid()) {
          setConfiguration(null);
          setMethods([]);
          setThreads([]);
          report(e);
        }
      });
    return () => {
      live = false;
    };
  }, [api, reload]);
  useEffect(() => {
    if (!positionId) {
      setPosition(null);
      setStandard(null);
      setPositionResults([]);
      return;
    }
    let live = true;
    const selection = epoch.current;
    const valid = () => live && current(selection);
    setStandard(null);
    setPositionResults([]);
    void positionApi
      .position(positionId)
      .then((p) => {
        if (valid()) setPosition(p);
      })
      .catch((e) => {
        if (valid()) {
          setPosition(null);
          report(e);
        }
      });
    void Promise.all([
      api.standard(positionId).catch((e) => {
        if (e instanceof HrLoopError && e.status === 404) return null;
        throw e;
      }),
      readPages((c) => api.results({ position: positionId, cursor: c })).then(
        (items) => Promise.all(items.map((i) => api.result(i.ref))),
      ),
    ])
      .then(([s, r]) => {
        if (valid()) {
          setStandard(s);
          setPositionResults(r);
        }
      })
      .catch((e) => {
        if (valid()) {
          setStandard(null);
          setPositionResults([]);
          report(e);
        }
      });
    return () => {
      live = false;
    };
  }, [api, positionApi, positionId, reload]);
  useEffect(() => {
    if (!workId) return;
    const selection = epoch.current;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const valid = () => live && current(selection);
    async function refresh() {
      try {
        // Re-read the complete visible history so revocations of older messages also replace the screen.
        const [view, input, msg, evt] = await Promise.all([
          api.work(workId!),
          api.input(workId!),
          readStream((n) => api.messages(workId!, n), 0, valid),
          readStream((n) => api.events(workId!, n), 0, valid),
        ]);
        if (!valid()) return;
        const catalogue = await readPages(
          (c) => api.results({ thread: view.thread_id, cursor: c }),
          valid,
        );
        const refs = new Map(
          [...view.result_refs, ...catalogue.map((i) => i.ref)].map((r) => [
            identity(r),
            r,
          ]),
        );
        const saved = await Promise.all(
          [...refs.values()].map((r) => api.result(r)),
        );
        if (!valid()) return;
        setWork({ ...view, input_revision: input.input_revision });
        setMessages(msg.items);
        setEvents(evt.items);
        setResults(saved);
        setPreviousInput(input.text);
        if (revision.current !== input.input_revision) {
          setObjects(input.objects);
          setReferences(input.references);
          revision.current = input.input_revision;
          const p = input.objects.find((o) => o.kind === "position");
          if (p && !currentPosition.current) setPositionId(p.id);
        }
        timer = setTimeout(() => void refresh(), 2500);
      } catch (e) {
        if (valid()) {
          clearPrivate();
          report(e);
        }
      }
    }
    void refresh();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [api, workId, reload]);
  function selectWork(id?: string) {
    epoch.current++;
    revision.current = 0;
    clearPrivate();
    setObjects(positionId ? [{ kind: "position", id: positionId }] : []);
    setText("");
    setError("");
    setHistory([]);
    setBusy(false);
    mutationBusy.current = false;
    keys.current.clear();
    budgetAttempt.current = null;
    setWorkId(id);
    if (id) setPositionId(undefined);
    setReload((n) => n + 1);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("work", id);
    else url.searchParams.delete("work");
    window.history.replaceState(null, "", url);
  }
  async function mutate(
    operation: string,
    body: unknown,
    action: (key: string) => Promise<unknown>,
    done?: (value: unknown) => void,
  ) {
    if (account.hard_stale_read_only || mutationBusy.current) return;
    const selection = epoch.current;
    mutationBusy.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await action(keyFor(operation, body));
      if (current(selection)) {
        done?.(value);
        setNotice("操作已保存。");
      }
    } catch (e) {
      if (current(selection)) report(e);
    } finally {
      if (current(selection)) {
        mutationBusy.current = false;
        setBusy(false);
      }
    }
  }
  function addReference(ref: ExactRef) {
    setReferences((old) =>
      old.some((r) => identity(r) === identity(ref)) ? old : [...old, ref],
    );
  }
  async function submit() {
    if (
      !text.trim() ||
      !configuration ||
      disabled ||
      hasUnavailableIntelligence
    )
      return;
    const content = { text: text.trim(), objects, references };
    if (work) {
      const body = {
        ...content,
        expected_input_revision: work.input_revision,
        question_id: work.pending_question_id,
      };
      await mutate(
        "input:" + work.work_id,
        body,
        (k) => api.append(work.work_id, body, k),
        (v) => {
          setWork(v as WorkView);
          setText("");
          setReload((n) => n + 1);
        },
      );
    } else {
      const body = {
        ...content,
        thread_id: null,
        budget_profile: configuration.budget_profile,
      };
      await mutate(
        "new",
        body,
        (k) => api.submit(body, k),
        (v) => {
          const next = v as WorkView;
          setWork(next);
          setWorkId(next.work_id);
          setText("");
          const url = new URL(window.location.href);
          url.searchParams.set("work", next.work_id);
          window.history.replaceState(null, "", url);
        },
      );
    }
  }
  async function openMethod(item: ResourceItem) {
    const selection = epoch.current;
    const request = ++methodRequest.current;
    setMethod(null);
    try {
      const value = await api.method(item.ref);
      if (current(selection) && request === methodRequest.current)
        setMethod({ ...value, title: item.title });
    } catch (e) {
      if (current(selection)) report(e);
    }
  }
  async function openThread(id: string) {
    const selection = epoch.current;
    const request = ++threadRequest.current;
    setHistory([]);
    try {
      const list = await readPages(
        (c) => api.works(id, c),
        () => current(selection) && request === threadRequest.current,
      );
      if (current(selection) && request === threadRequest.current)
        setHistory(list);
    } catch (e) {
      if (current(selection)) report(e);
    }
  }
  async function rereadStandard() {
    if (!positionId) return;
    const selection = epoch.current;
    const target = positionId;
    setStandard(null);
    try {
      const value = await api.standard(target);
      if (current(selection) && currentPosition.current === target)
        setStandard(value);
    } catch (e) {
      if (current(selection) && currentPosition.current === target) {
        if (e instanceof HrLoopError && e.status === 404) setStandard(null);
        else report(e);
      }
    }
  }
  async function download(result: SavedResult) {
    const selection = epoch.current;
    try {
      const blob = await api.download(result.ref);
      if (!current(selection)) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${result.title.replace(/[\\/:*?"<>|]/g, "_")}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (current(selection)) report(e);
    }
  }
  async function upload(item: Upload) {
    if (disabled) return;
    const selection = epoch.current;
    setBusy(true);
    mutationBusy.current = true;
    item.error = undefined;
    setUploads((old) => [...old]);
    const update = () => {
      if (current(selection)) setUploads((old) => [...old]);
    };
    try {
      item.upload ??= await beginAttachmentUpload(
        null,
        item.file,
        account.csrf_token,
      );
      if (!current(selection)) return;
      if (!item.contentDone) {
        await uploadAttachmentContent(
          item.upload.uploadId,
          item.file,
          account.csrf_token,
        );
        item.contentDone = true;
      }
      if (!current(selection)) return;
      if (!item.completeDone) {
        await completeAttachmentUpload(
          item.upload.uploadId,
          account.csrf_token,
        );
        item.completeDone = true;
      }
      if (!current(selection)) return;
      while (current(selection)) {
        const material = await api.material(item.upload.attachmentId);
        if (!current(selection)) return;
        item.material = material;
        update();
        if (
          ["quarantined", "rejected", "deleted", "expired"].includes(
            material.state,
          )
        )
          break;
        if (material.state === "ready") {
          if (material.text_ref) {
            addReference(material.text_ref);
            break;
          }
          if (material.parse_state === "not_started") {
            await api.parse(material.attachment_id, item.parseKey);
          } else if (["failed", "unsupported"].includes(material.parse_state))
            break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (e) {
      if (current(selection)) {
        item.error = friendly(e);
        update();
        report(e);
      }
    } finally {
      if (current(selection)) {
        setBusy(false);
        mutationBusy.current = false;
      }
    }
  }
  const allResults = [
    ...new Map(
      [...results, ...positionResults].map((r) => [identity(r.ref), r]),
    ).values(),
  ];
  return (
    <HrWorkspaceShell account={account} current="agent">
      <main className="hr-loop">
        <aside className="hr-loop-sidebar" aria-label="工作与方法">
          <button type="button" onClick={() => selectWork()}>
            新工作
          </button>
          <h2>最近工作</h2>
          {threads.length === 0 && <p>从一个问题开始。</p>}
          {threads.map((t) => (
            <button
              key={t.thread_id}
              type="button"
              onClick={() => void openThread(t.thread_id)}
            >
              {t.title || "未命名工作"}
            </button>
          ))}
          {history.map((w) => (
            <button
              key={w.work_id}
              type="button"
              onClick={() => selectWork(w.work_id)}
            >
              {states[w.state] ?? "查看工作"} · 第 {w.input_revision} 次输入
            </button>
          ))}
          <h2>专业方法</h2>
          <p>先阅读用途、边界和来源，再带入讨论。</p>
          {methods.map((m) => (
            <button
              type="button"
              key={identity(m.ref)}
              onClick={() => void openMethod(m)}
            >
              {m.title}
              <small>{m.description}</small>
            </button>
          ))}
          <h2>候选人材料</h2>
          <button type="button" onClick={() => setShowCandidates(true)}>
            批量简历材料
          </button>
          <h2>公开研究</h2>
          <button
            type="button"
            onClick={() =>
              setIntelligence((old) => ({ request: (old?.request ?? 0) + 1 }))
            }
          >
            公司与专题情报
          </button>
        </aside>
        <section className="hr-loop-conversation" aria-label="HR Agent 对话">
          <header>
            <span className="hr-loop-eyebrow">HR AGENT · 试用</span>
            <h1>与 Hannah 一起厘清招聘问题</h1>
            <p>从公开 JD、业务目标或一个问题开始；也可以先选岗位。</p>
            <HrPositionPicker
              api={positionApi}
              selected={position}
              disabled={disabled}
              onSelect={(p) => {
                setPosition(p);
                setPositionId(p?.positionId);
                setObjects((old) => [
                  ...old.filter((o) => o.kind !== "position"),
                  ...(p ? [{ kind: "position", id: p.positionId }] : []),
                ]);
              }}
            />
          </header>
          {error && (
            <div className="hr-loop-alert" role="alert">
              {error}
              <button
                type="button"
                onClick={() => {
                  setError("");
                  setReload((n) => n + 1);
                }}
              >
                重新加载
              </button>
            </div>
          )}
          {notice && (
            <p role="status" className="hr-loop-notice">
              {notice}
            </p>
          )}
          {method && (
            <section className="hr-loop-method">
              <header>
                <h2>{method.title}</h2>
                <button
                  type="button"
                  onClick={() => {
                    methodRequest.current++;
                    setMethod(null);
                  }}
                >
                  关闭方法
                </button>
              </header>
              <HrLoopMethodPreview content={method.text} />
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  addReference(method.ref);
                  setMethod(null);
                }}
              >
                带此方法讨论
              </button>
            </section>
          )}
          {showCandidates && (
            <HrLoopCandidatesPanel
              api={candidatesApi}
              csrf={account.csrf_token}
              disabled={disabled}
              budgetProfile={configuration?.budget_profile ?? null}
              positionId={positionId}
              positionTitle={position?.title}
              onClose={() => setShowCandidates(false)}
              onAccessError={report}
              onOpenWork={selectWork}
            />
          )}
          {intelligence && (
            <HrLoopIntelligencePicker
              key={intelligence.request}
              api={api}
              initialRef={intelligence.ref}
              disabled={disabled}
              onClose={() => setIntelligence(null)}
              onAccessError={report}
              onAvailability={(ref, available) =>
                setUnavailableIntelligence((old) =>
                  available
                    ? old.filter((value) => value !== identity(ref))
                    : old.includes(identity(ref))
                      ? old
                      : [...old, identity(ref)],
                )
              }
              onSelect={(ref) => {
                addReference(ref);
                setNotice("情报已加入本次参考。");
              }}
            />
          )}
          {work && (
            <section className="hr-loop-status" aria-label="工作状态">
              <strong>
                {states[work.state] ?? "工作进行中"}
                {work.phase === "finalizing" ? " · 正在整理已有成果" : ""}
              </strong>
              <span>
                {work.answer_state === "ended"
                  ? "本次回答已结束"
                  : work.answer_state === "partial"
                    ? "已有部分回答"
                    : "尚未形成回答"}{" "}
                · 已保存 {work.result_refs.length} 份成果
              </span>
              {work.block_reason && (
                <p>
                  所需资料、权限或执行配置发生变化，请检查后重新加载或发起新工作。
                </p>
              )}
              {events
                .filter(
                  (e) =>
                    e.type === "tool_error" || e.type === "recovery_started",
                )
                .slice(-4)
                .map((e) => (
                  <p key={e.seq}>
                    {e.type === "recovery_started"
                      ? "正在恢复已保存的工作。"
                      : (toolStates[e.tool_status ?? ""] ??
                        "工具暂未完成，请检查所需资料。")}
                  </p>
                ))}
              {work.checkpoint?.readings.some(
                (r) => r.remaining_ranges.length > 0,
              ) && <p>仍有材料尚未完整阅读，现有结论只覆盖已读部分。</p>}
              {work.checkpoint?.open_questions.map((q, i) => (
                <p key={i}>待确认：{q}</p>
              ))}
              {!["completed", "failed", "cancelled"].includes(work.state) && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    void mutate(
                      "cancel:" + work.work_id,
                      {},
                      (k) => api.cancel(work.work_id, k),
                      (v) => {
                        setWork(v as WorkView);
                        setReload((n) => n + 1);
                      },
                    )
                  }
                >
                  停止本次工作
                </button>
              )}
            </section>
          )}
          <div className="hr-loop-messages" aria-live="polite">
            {!workId && (
              <div className="hr-loop-welcome">
                <h2>你希望这次解决什么？</h2>
                <p>
                  例如：这份 JD
                  中哪些要求与实际任务有关，哪些需要向用人经理确认？
                </p>
              </div>
            )}
            {messages.map((m) => (
              <article
                key={m.entry_id}
                className={`hr-loop-message hr-loop-message--${m.kind}`}
              >
                <strong>{m.kind === "user" ? "你" : "Hannah"}</strong>
                {m.visibility === "restricted" ? (
                  <p>这条内容的访问权限已变化。</p>
                ) : (
                  <>
                    <MessageMarkdown content={m.body ?? ""} />
                    {m.options?.map((o) => (
                      <button
                        key={o}
                        type="button"
                        disabled={disabled}
                        onClick={() => setText(o)}
                      >
                        {o}
                      </button>
                    ))}
                  </>
                )}
              </article>
            ))}
          </div>
          {work?.state === "waiting_budget" && (
            <section className="hr-loop-budget">
              <h2>继续前，请指定追加额度</h2>
              <p>
                已使用 {work.budget.charged_calls} 次调用、
                {work.budget.charged_tokens} tokens、
                {Math.round(work.budget.active_seconds)} 秒活动时间。
                {work.budget.usage_quality === "estimated"
                  ? "用量含估算。"
                  : work.budget.usage_quality === "mixed"
                    ? "用量包含报告值与估算值。"
                    : ""}
              </p>
              <p>
                当前上限：{work.budget.limits.model_calls} 次调用 /{" "}
                {work.budget.limits.total_tokens} tokens /{" "}
                {work.budget.limits.active_seconds} 秒。
              </p>
              {(["model_calls", "total_tokens", "active_seconds"] as const).map(
                (field, i) => (
                  <label key={field}>
                    {["追加调用次数", "追加 tokens", "追加活动秒数"][i]}
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={addition[field]}
                      disabled={disabled}
                      onChange={(e) =>
                        setAddition((old) => ({
                          ...old,
                          [field]: Math.max(
                            0,
                            Math.floor(Number(e.target.value) || 0),
                          ),
                        }))
                      }
                    />
                  </label>
                ),
              )}
              <label>
                继续目标
                <input
                  value={reason}
                  disabled={disabled}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={
                  disabled ||
                  !reason.trim() ||
                  !Object.values(addition).some((n) => n > 0)
                }
                onClick={() => {
                  const fingerprint = JSON.stringify([
                    work.work_id,
                    addition,
                    reason.trim(),
                  ]);
                  if (budgetAttempt.current?.fingerprint !== fingerprint)
                    budgetAttempt.current = {
                      fingerprint,
                      body: {
                        expected_budget_revision: work.budget.revision,
                        addition: { ...addition },
                        reason: reason.trim(),
                      },
                    };
                  const body = budgetAttempt.current.body;
                  void mutate(
                    "budget:" + work.work_id,
                    body,
                    (k) => api.extend(work.work_id, body, k),
                    (v) => {
                      setWork(v as WorkView);
                      budgetAttempt.current = null;
                      setAddition({
                        model_calls: 0,
                        total_tokens: 0,
                        active_seconds: 0,
                      });
                      setReason("");
                      setReload((n) => n + 1);
                    },
                  );
                }}
              >
                按以上额度继续
              </button>
            </section>
          )}
          <form
            className="hr-loop-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            {previousInput && (
              <details>
                <summary>当前已接收的输入</summary>
                <MessageMarkdown content={previousInput} />
              </details>
            )}
            {objects.length > 0 && (
              <p>
                工作对象：
                {objects
                  .map((o) =>
                    o.kind === "position"
                      ? position?.positionId === o.id
                        ? position.title
                        : "已选岗位"
                      : "已选材料对象",
                  )
                  .join("、")}
              </p>
            )}
            {references.length > 0 && (
              <div className="hr-loop-references" aria-label="本次参考">
                {references.map((r, i) => (
                  <span key={identity(r)}>
                    {r.kind === "intelligence" ? (
                      <button
                        type="button"
                        onClick={() =>
                          setIntelligence((old) => ({
                            ref: r,
                            request: (old?.request ?? 0) + 1,
                          }))
                        }
                      >
                        已选情报 {i + 1}
                      </button>
                    ) : (
                      <>
                        {methods.find((m) => identity(m.ref) === identity(r))
                          ?.title ??
                          (r.kind === "material"
                            ? r.id.endsWith(":text")
                              ? "材料正文"
                              : "材料原件"
                            : r.kind === "method"
                              ? "已选方法"
                              : r.kind === "result"
                                ? "已存成果"
                                : "已选参考")}{" "}
                        {i + 1}
                      </>
                    )}
                    <button
                      type="button"
                      disabled={disabled}
                      aria-label={`移除参考 ${i + 1}`}
                      onClick={() =>
                        setReferences((old) =>
                          old.filter((v) => identity(v) !== identity(r)),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            {hasUnavailableIntelligence && (
              <p role="alert">
                本次参考中有不可读取的情报，请重试读取或移除后继续。
              </p>
            )}
            {uploads.map((u, i) => (
              <div key={i} className="hr-loop-upload">
                <strong>{u.file.name}</strong>
                <p>{u.error ?? materialStatus(u.material)}</p>
                {u.material?.coverage_notes?.map((note, j) => (
                  <p key={j}>{note}</p>
                ))}
                {u.error && (
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => void upload(u)}
                  >
                    重试上传处理
                  </button>
                )}
              </div>
            ))}
            <label htmlFor="hr-loop-input">
              {work ? "补充问题或回复 Hannah" : "你的问题"}
            </label>
            <textarea
              id="hr-loop-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="描述岗位任务、目标或需要澄清的问题…"
              rows={4}
              disabled={account.hard_stale_read_only}
            />
            <footer>
              <label className="hr-loop-file">
                添加公开 JD
                <input
                  type="file"
                  accept=".txt,.md,.pdf,.docx"
                  disabled={disabled}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      const item: Upload = {
                        file,
                        parseKey: crypto.randomUUID(),
                      };
                      setUploads((old) => [...old, item]);
                      void upload(item);
                    }
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                type="submit"
                disabled={
                  disabled ||
                  !configuration ||
                  hasUnavailableIntelligence ||
                  !text.trim() ||
                  Boolean(workId && !work) ||
                  work?.state === "waiting_budget"
                }
              >
                {busy ? "处理中…" : "发送"}
              </button>
            </footer>
            <small>
              材料先经过检查和解析。回答结束、成果保存与标准确认分别展示。
            </small>
          </form>
        </section>
        <aside className="hr-loop-results" aria-label="成果与标准">
          <h2>成果</h2>
          {allResults.length === 0 && (
            <p>保存后的成果会出现在这里，也可从关联岗位找回。</p>
          )}
          {allResults.map((r) => (
            <ResultCard
              key={`${identity(r.ref)}:${positionId ?? ""}`}
              result={r}
              standard={standard}
              positionId={positionId}
              disabled={disabled}
              onDownload={() => void download(r)}
              onReference={() => addReference(r.ref)}
              onLink={() => {
                if (positionId) {
                  const body = {
                    objects: [{ kind: "position", id: positionId }],
                    expected_result_revision: r.ref.revision,
                  };
                  void mutate(
                    "link:" + r.ref.id,
                    body,
                    (k) => api.link(r.ref, body.objects, k),
                    () => setReload((n) => n + 1),
                  );
                }
              }}
              onConfirm={(ids) => {
                if (positionId) {
                  const body = {
                    proposal_ref: r.ref,
                    selected_change_ids: ids,
                    expected_standard_revision:
                      r.base_standard_ref?.revision ?? null,
                  };
                  void mutate(
                    "confirm:" + positionId,
                    body,
                    (k) => api.confirm(positionId, body, k),
                    (v) => setStandard(v as StandardView),
                  );
                }
              }}
            />
          ))}
          {positionId && (
            <section className="hr-loop-standard">
              <h2>当前已确认标准</h2>
              {standard ? (
                <>
                  <ul>
                    {standard.items.map((item) => (
                      <li key={item.item_id}>{item.text}</li>
                    ))}
                  </ul>
                  <small>确认时间：{standard.confirmed_at}</small>
                </>
              ) : (
                <p>暂无已确认标准，或尚未读取。</p>
              )}
              {standard && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => addReference(standard.ref)}
                >
                  带此标准讨论
                </button>
              )}
              <button type="button" onClick={() => void rereadStandard()}>
                重新阅读当前标准
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() =>
                  setText(
                    "请阅读当前已确认标准，并依据最新标准重新提出调整建议。",
                  )
                }
              >
                请求修订提案
              </button>
              <a
                href={platformPath(
                  `/hr/positions/${encodeURIComponent(positionId)}`,
                )}
              >
                查看岗位资料
              </a>
            </section>
          )}
        </aside>
      </main>
    </HrWorkspaceShell>
  );
}
function ResultCard({
  result,
  standard,
  positionId,
  disabled,
  onDownload,
  onReference,
  onLink,
  onConfirm,
}: {
  result: SavedResult;
  standard: StandardView | null;
  positionId?: string;
  disabled: boolean;
  onDownload: () => void;
  onReference: () => void;
  onLink: () => void;
  onConfirm: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const exactBase = Boolean(
    standard &&
      result.base_standard_ref &&
      identity(standard.ref) === identity(result.base_standard_ref),
  );
  const originalText = (change: SavedResult["changes"][number]) =>
    exactBase
      ? standard?.items.find((item) => item.item_id === change.target_item_id)
          ?.text
      : undefined;
  const selectable = (change: SavedResult["changes"][number]) =>
    change.action === "add" || originalText(change) !== undefined;
  const chosen = selected.filter((id) =>
    result.changes.some(
      (change) => change.change_id === id && selectable(change),
    ),
  );
  return (
    <article className="hr-loop-result">
      <h3>{result.title}</h3>
      <span>
        成果已保存
        {result.kind === "standard_proposal" ? " · 建议尚待用户确认" : ""}
      </span>
      {result.basis.length > 0 && (
        <section className="hr-loop-basis" aria-label="基准性质">
          <strong>基准性质</strong>
          <ul>
            {result.basis.map((basis, index) => (
              <li key={index}>
                {basis.kind === "confirmed_standard"
                  ? "已确认标准 · 采用成果保存时引用的标准版本。"
                  : basis.kind === "official_original"
                    ? "官网原文 · 采用成果保存时引用的官网材料。"
                    : `临时要求 · 来自第 ${basis.input_revision} 次已接收输入，尚未确认为正式标准。`}
              </li>
            ))}
          </ul>
        </section>
      )}
      <MessageMarkdown content={result.body} />
      {result.kind === "standard_proposal" && (
        <fieldset disabled={disabled}>
          <legend>选择你认可的具体标准</legend>
          {result.changes.map((c) => (
            <label key={c.change_id}>
              <input
                type="checkbox"
                checked={chosen.includes(c.change_id)}
                disabled={!selectable(c)}
                onChange={(e) =>
                  setSelected((old) =>
                    e.target.checked
                      ? [...old, c.change_id]
                      : old.filter((id) => id !== c.change_id),
                  )
                }
              />
              <span>
                {c.action === "remove"
                  ? "删除："
                  : c.action === "replace"
                    ? "调整："
                    : "新增："}
                {c.action === "add" ? (
                  c.text
                ) : originalText(c) === undefined ? (
                  "请先重新阅读与提案基准一致的原标准；基准已变化时需请求修订提案。"
                ) : c.action === "remove" ? (
                  originalText(c)
                ) : (
                  <>
                    {originalText(c)} → {c.text}
                  </>
                )}
              </span>
            </label>
          ))}
          {!positionId && <p>选择岗位后可确认标准。</p>}
          <button
            type="button"
            disabled={!positionId || chosen.length === 0 || disabled}
            onClick={() => onConfirm(chosen)}
          >
            确认选中条目
          </button>
        </fieldset>
      )}
      <footer>
        <button type="button" onClick={onDownload}>
          下载成果
        </button>
        <button type="button" disabled={disabled} onClick={onReference}>
          带此成果讨论
        </button>
        {positionId && (
          <button type="button" disabled={disabled} onClick={onLink}>
            关联到所选岗位
          </button>
        )}
      </footer>
    </article>
  );
}
