import { useEffect, useRef, useState } from "react";
import { platformPath } from "../../auth";
import type { HrR12Api } from "../../hrR12Api";
import type { HrPositionArtifactItem, HrPositionMaterialItem, HrPositionResources } from "../../hrR12Types";

type ResourceItem = HrPositionMaterialItem | HrPositionArtifactItem;
const STATE_LABELS: Record<string, string> = {
  ready: "可预览和下载", uploading: "正在上传，暂不可使用", validating: "正在安全检查，暂不可使用",
  scanning: "正在安全检查，暂不可使用", failed: "生成失败，可回到任务重试", quarantined: "安全隔离，暂不可使用",
  rejected: "文件未通过安全检查", deleted: "已删除或保留期已结束",
};
function size(bytes: number): string { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function typeLabel(mediaType: string): string { if (mediaType === "application/pdf") return "PDF"; if (mediaType.includes("wordprocessingml") || mediaType === "application/msword") return "Word"; if (mediaType.startsWith("image/")) return "图片"; return mediaType; }
function isArtifact(item: ResourceItem): item is HrPositionArtifactItem { return "artifactId" in item; }

export function HrPositionResourcesPanel({ api, positionId }: {
  api: Pick<HrR12Api, "resources" | "downloadResource">; positionId: string;
}) {
  const [resources, setResources] = useState<HrPositionResources | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController(); setResources(null); setSelected([]); setNotice(null);
    void api.resources(positionId, controller.signal).then((value) => { if (!controller.signal.aborted) setResources(value); }).catch(() => { if (!controller.signal.aborted) setNotice("岗位材料暂时不可用"); });
    return () => { controller.abort(); mutation.current?.abort(); };
  }, [api, positionId]);

  async function ticket(attachmentId: string, purpose: "preview" | "download", signal: AbortSignal) {
    const issued = await api.downloadResource(positionId, attachmentId, crypto.randomUUID(), purpose, signal);
    if (!signal.aborted) window.open(platformPath(issued.contentPath), "_blank", "noopener,noreferrer");
  }
  async function open(attachmentId: string, purpose: "preview" | "download") {
    mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller;
    try { await ticket(attachmentId, purpose, controller.signal); } catch { if (!controller.signal.aborted) setNotice(purpose === "preview" ? "预览未完成，请重试。" : "下载未完成，请重试。"); }
  }
  async function downloadSelected() {
    mutation.current?.abort(); const controller = new AbortController(); mutation.current = controller;
    setNotice(`正在准备 ${selected.length} 项安全下载…`);
    try { for (const attachmentId of selected) await ticket(attachmentId, "download", controller.signal); if (!controller.signal.aborted) setNotice(`已打开 ${selected.length} 项下载。`); } catch { if (!controller.signal.aborted) setNotice("部分下载未完成，已下载的文件不受影响，可以重试其余项目。"); }
  }
  const render = (item: ResourceItem) => <article key={`${isArtifact(item) ? item.artifactId : "material"}:${item.attachmentId}`}>
    <div><strong>{item.filename}</strong><span>{isArtifact(item) ? `成果 v${item.artifactVersion}` : "岗位材料"}</span></div>
    <dl><div><dt>类型</dt><dd>{typeLabel(item.mediaType)}</dd></div><div><dt>大小</dt><dd>{size(item.sizeBytes)}</dd></div><div><dt>创建时间</dt><dd><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString("zh-CN")}</time></dd></div></dl>
    <p>{STATE_LABELS[item.state] ?? "当前状态暂不可使用"}</p>
    {(item.sourceConversationId || item.sourceTurnId) && <p className="hr-resource-source">{item.sourceConversationId && `来源对话 ${item.sourceConversationId.slice(0, 8)}`}{item.sourceTurnId && ` · 轮次 ${item.sourceTurnId.slice(0, 8)}`}</p>}
    <div className="hr-resource-actions"><label><input disabled={!item.downloadAvailable} type="checkbox" value={item.attachmentId} checked={selected.includes(item.attachmentId)} onChange={() => setSelected((ids) => ids.includes(item.attachmentId) ? ids.filter((id) => id !== item.attachmentId) : [...ids, item.attachmentId])} />加入批量下载</label>{item.previewAvailable && <button type="button" onClick={() => void open(item.attachmentId, "preview")}>预览{item.filename}</button>}{item.downloadAvailable && <button type="button" onClick={() => void open(item.attachmentId, "download")}>下载{item.filename}</button>}</div>
  </article>;
  return <section aria-label="岗位材料与成果" className="hr-r12-panel hr-resources-panel"><header><div><span>POSITION RESOURCES</span><h2>岗位材料与成果</h2></div><button disabled={selected.length === 0} type="button" onClick={() => void downloadSelected()}>下载已选 {selected.length} 项</button></header>{!resources && <p aria-live="polite">{notice ?? "正在读取岗位资源…"}</p>}{resources && <><section aria-label="岗位材料"><h3>材料（{resources.materials.length}）</h3>{resources.materials.length === 0 ? <p>当前岗位还没有已绑定材料。</p> : resources.materials.map(render)}</section><section aria-label="生成成果"><h3>成果（{resources.artifacts.length}）</h3>{resources.artifacts.length === 0 ? <p>当前岗位还没有生成成果。</p> : resources.artifacts.map(render)}</section></>}{notice && resources && <p role="status">{notice}</p>}</section>;
}
