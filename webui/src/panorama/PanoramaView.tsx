import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AiEngineeringApiError } from "../aiEngineeringApi";
import { loadAccount, platformPath, PlatformApiError } from "../auth";
import type { AiEngineeringDocumentSlug } from "../aiEngineeringApi";
import type { PanoramaActionId, PanoramaData, PanoramaEditorState } from "../panoramaTypes";
import { PanoramaCanvas } from "./PanoramaCanvas";
import { PanoramaEditor } from "./PanoramaEditor";
import { panoramaClient, parsePanorama } from "./panoramaApi";
import "./panorama.css";

interface Props {
  renderOrganization?: (active: boolean, onOpen: () => void) => ReactNode;
  data: PanoramaData;
  onAction: (actionId: PanoramaActionId) => void;
  onEvidence: (slug: AiEngineeringDocumentSlug) => void;
  isOwner?: boolean;
  active?: boolean;
  onDataChange?: (data: PanoramaData) => void;
  onAuthorizationFailure?: (error: AiEngineeringApiError) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

interface Editing {
  csrf: string;
  state: PanoramaEditorState;
  local: PanoramaData;
  dirty: boolean;
  busy: boolean;
  locked: boolean;
  preview: boolean;
  message: string;
}

function cloneData(data: PanoramaData): PanoramaData { return structuredClone(data); }
function authorizationError(error: unknown): AiEngineeringApiError | null {
  if (error instanceof AiEngineeringApiError && (error.status === 401 || error.status === 403)) return error;
  if (error instanceof PlatformApiError && (error.status === 401 || error.status === 403)) return new AiEngineeringApiError(error.status);
  return null;
}
function operationMessage(error: unknown): { text: string; locked: boolean } {
  if (error instanceof AiEngineeringApiError && error.status === 409) return { text: "版本冲突：服务器草稿已变化，本地修改仍保留。请先核对服务器状态。", locked: true };
  if (error instanceof AiEngineeringApiError && error.status === 422) return { text: "草稿内容未通过校验，请修正后再保存。", locked: false };
  return { text: "请求结果未知，本地修改仍保留。为避免重复写入，请先核对服务器状态。", locked: true };
}

export function PanoramaView({ data, onAction, onEvidence, isOwner = false, active = true, onDataChange, onAuthorizationFailure, onDirtyChange, renderOrganization }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [presenting, setPresenting] = useState(false);
  const [opening, setOpening] = useState(false);
  const [editorNotice, setEditorNotice] = useState("");
  const [editor, setEditor] = useState<Editing | null>(null);
  const queryRef = useRef<HTMLInputElement>(null);
  const operation = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const localDraft = useRef<PanoramaData | null>(null);
  const localEditGeneration = useRef(0);
  const effectiveData = editor?.local ?? data;
  const matchIds = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(); if (!needle) return new Set<string>();
    return new Set(effectiveData.nodes.filter((node) => [node.title, node.subtitle, ...node.detail].join(" ").toLocaleLowerCase().includes(needle)).map((node) => node.id));
  }, [effectiveData.nodes, query]);

  const reportDirty = useCallback((value: boolean) => { onDirtyChange?.(value); }, [onDirtyChange]);
  const clearEditorForAuthorization = useCallback((error: AiEngineeringApiError) => {
    operation.current?.abort(); operation.current = null; localDraft.current = null; setOpening(false); setEditor(null); reportDirty(false); onAuthorizationFailure?.(error);
  }, [onAuthorizationFailure, reportDirty]);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; operation.current?.abort(); }; }, []);
  useEffect(() => {
    if (!active) {
      operation.current?.abort(); setOpening(false);
      setEditor((value) => value?.busy ? { ...value, busy: false, locked: true, message: "请求已中断，结果可能未知。本地修改仍保留，返回后请核对服务器状态。" } : value);
    }
  }, [active]);
  useEffect(() => {
    if (!query.trim() || !matchIds.size) return;
    setSelectedId([...matchIds][0]);
  }, [matchIds, query]);
  useEffect(() => {
    if (!active || editor) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (event.defaultPrevented || target?.isContentEditable || target?.closest("input, textarea, select")) return;
      if (event.key === "Escape") { setSelectedId(null); setQuery(""); setPresenting(false); }
      if (event.key === "/") { event.preventDefault(); queryRef.current?.focus(); }
    };
    document.addEventListener("keydown", onKeyDown); return () => document.removeEventListener("keydown", onKeyDown);
  }, [active, editor]);

  const openEditor = async () => {
    if (opening || editor) return;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller; setOpening(true); setEditorNotice("");
    try {
      const [account, state] = await Promise.all([loadAccount(), panoramaClient.fetchEditorState(controller.signal)]);
      if (controller.signal.aborted || !mounted.current) return;
      if (!(["platform_admin", "platform_owner"] as string[]).includes(account.role) || account.hard_stale_read_only) {
        setEditorNotice(account.hard_stale_read_only ? "企业目录状态已过期，当前只能查看全景，暂不能调整布局。" : "当前账号没有调整全景布局的权限。"); return;
      }
      const local = cloneData(state.draft ?? state.published);
      localDraft.current = local; localEditGeneration.current = 0;
      setEditor({ csrf: account.csrf_token, state, local, dirty: false, busy: false, locked: false, preview: false, message: state.draft ? "已载入共享草稿。" : "正在预览当前发布版；修改后保存为共享草稿。" });
      setSelectedId(null); reportDirty(false);
    } catch (error) {
      if (controller.signal.aborted || !mounted.current) return;
      const auth = authorizationError(error); if (auth) clearEditorForAuthorization(auth);
      else { setOpening(false); setEditorNotice("布局编辑器暂时无法打开，请稍后再试。"); }
    } finally {
      if (operation.current === controller) operation.current = null;
      if (!controller.signal.aborted && mounted.current) setOpening(false);
    }
  };
  const closeEditor = () => {
    if (!editor) return;
    if (editor.dirty && !window.confirm("当前有未保存的布局输入。确定退出并放弃本地输入？")) return;
    operation.current?.abort(); localDraft.current = null; setEditor(null); reportDirty(false);
  };
  const changeLocal = (local: PanoramaData) => {
    localDraft.current = local; localEditGeneration.current += 1;
    setEditor((value) => value ? { ...value, local, dirty: true, message: "" } : value); reportDirty(true);
  };

  const mutate = async (kind: "save" | "discard" | "publish" | "restore") => {
    const current = editor; if (!current || current.busy || current.locked) return;
    if (kind === "discard" && current.dirty && !window.confirm("确定放弃本地输入和已保存的共享草稿？")) return;
    if (kind === "save") { try { parsePanorama(current.local); } catch { setEditor({ ...current, message: "草稿内容未通过校验，请检查空标题、重复节点或无效关系。" }); return; } }
    const startedGeneration = localEditGeneration.current;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller;
    setEditor({ ...current, busy: true, message: "" });
    try {
      const next = kind === "save"
        ? await panoramaClient.saveDraft(current.csrf, current.state.revision, current.local, controller.signal)
        : kind === "discard"
          ? await panoramaClient.discardDraft(current.csrf, current.state.revision, controller.signal)
          : kind === "publish"
            ? await panoramaClient.publish(current.csrf, current.state.revision, controller.signal)
            : await panoramaClient.restore(current.csrf, current.state.revision, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      const editedAfterStart = localEditGeneration.current !== startedGeneration;
      const local = editedAfterStart && localDraft.current ? cloneData(localDraft.current) : cloneData(next.draft ?? next.published);
      localDraft.current = local;
      setEditor({
        ...current, state: next, local, dirty: editedAfterStart, busy: false, locked: false,
        message: editedAfterStart
          ? "服务器操作已完成；请求期间的新修改仍在本地，请再次保存后再发布。"
          : kind === "save" ? "草稿已保存，当前首页尚未改变。" : kind === "discard" ? "修改已取消，当前发布版保持不变。" : kind === "publish" ? "草稿已发布。" : "上一版已恢复为新的发布版本。",
      });
      reportDirty(editedAfterStart);
      if (kind === "publish" || kind === "restore") onDataChange?.(next.published);
    } catch (error) {
      if (!mounted.current) return;
      const auth = authorizationError(error); if (auth) { clearEditorForAuthorization(auth); return; }
      const result = operationMessage(error);
      setEditor((value) => value ? { ...value, busy: false, locked: result.locked, message: result.text } : value);
    } finally { if (operation.current === controller) operation.current = null; }
  };
  const reconcile = async () => {
    const current = editor; if (!current || current.busy) return;
    const startedGeneration = localEditGeneration.current;
    const controller = new AbortController(); operation.current?.abort(); operation.current = controller; setEditor({ ...current, busy: true });
    try {
      const state = await panoramaClient.fetchEditorState(controller.signal); if (controller.signal.aborted || !mounted.current) return;
      const editedAfterStart = localEditGeneration.current !== startedGeneration;
      const matches = JSON.stringify(state.draft) === JSON.stringify(current.local);
      const preserveLocal = editedAfterStart || (current.dirty && !matches);
      const local = preserveLocal && localDraft.current ? cloneData(localDraft.current) : cloneData(state.draft ?? state.published);
      localDraft.current = local;
      setEditor({
        ...current, state, local, dirty: preserveLocal, busy: false, locked: false,
        message: preserveLocal
          ? "已读取最新修订号；本地未保存修改仍保留，请复核后再次保存。"
          : matches ? "服务器已确认保存此草稿。" : state.draft ? "已载入服务器最新共享草稿。" : "已载入服务器当前发布版。",
      });
      reportDirty(preserveLocal);
      onDataChange?.(state.published);
    } catch (error) {
      if (!mounted.current) return;
      const auth = authorizationError(error); if (auth) clearEditorForAuthorization(auth);
      else setEditor((value) => value ? { ...value, busy: false, locked: true, message: "暂时无法核对服务器状态，本地修改仍保留。" } : value);
    } finally { if (operation.current === controller) operation.current = null; }
  };

  return <main className={`panorama${presenting ? " panorama--presenting" : ""}${editor ? " panorama--editing" : ""}`} aria-label={effectiveData.title}>
    <header className="panorama-header">
      <div><p className="panorama-eyebrow">AI ENGINEERING PANORAMA · {effectiveData.version}</p><h1>{effectiveData.title}</h1></div>
      <div className="panorama-toolbar">
        <label className="panorama-search"><span>搜索</span><input ref={queryRef} type="search" value={query} placeholder="产品、技术或场景…" onInput={(event) => setQuery(event.currentTarget.value)} /></label>
        <button type="button" onClick={openEditor} disabled={opening || !!editor}>{opening ? "读取布局…" : "调整布局"}</button>
        <button type="button" onClick={() => setPresenting(true)}>展示模式</button>
        <a className="panorama-export" href={platformPath(`/api/v1/ai-engineering/export.svg?version=${encodeURIComponent(data.version)}`)} download title="导出业务布局 SVG，不含实时组织目录">业务图 SVG</a>
        <a className="panorama-export" href={platformPath(`/api/v1/ai-engineering/export.png?version=${encodeURIComponent(data.version)}`)} download title="导出业务布局 PNG，不含实时组织目录">业务图 PNG</a>
      </div>
    </header>
    {editorNotice && <p className="panorama-editor-notice" role="status">{editorNotice}</p>}
    {presenting && <button type="button" className="panorama-presentation-exit" onClick={() => setPresenting(false)}>退出展示</button>}
    <PanoramaCanvas data={effectiveData} isOwner={isOwner} matchIds={matchIds} onAction={onAction} onEvidence={onEvidence} onSelect={setSelectedId} selectedId={selectedId}
      organization={renderOrganization?.(active && !editor && !selectedId, () => setSelectedId(null))} />
    {editor && <PanoramaEditor
      busy={editor.busy} data={editor.local} dirty={editor.dirty} locked={editor.locked} message={editor.message}
      onChange={changeLocal} onClose={closeEditor} onDiscard={() => void mutate("discard")} onPreview={() => setEditor({ ...editor, preview: !editor.preview })}
      onPublish={() => void mutate("publish")} onReconcile={() => void reconcile()} onRestore={() => void mutate("restore")} onSave={() => void mutate("save")}
      preview={editor.preview} state={editor.state}
    />}
    <footer className="panorama-footer"><span>更新于 {effectiveData.updated_at}</span><span>版本 {effectiveData.version}</span></footer>
  </main>;
}
