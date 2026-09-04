import { useEffect, useState } from "react";
import type { HrR12Api } from "../../hrR12Api";
import type { HrPositionResources } from "../../hrR12Types";

export function HrPositionResourcesPanel({ api, positionId }: { api: Pick<HrR12Api, "resources" | "downloadResource">; positionId: string }) {
  const [resources, setResources] = useState<HrPositionResources | null>(null); const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); void api.resources(positionId, controller.signal).then((value) => { if (!controller.signal.aborted) setResources(value); }).catch(() => setNotice("岗位材料暂时不可用")); return () => controller.abort(); }, [api, positionId]);
  async function open(attachmentId: string, purpose: "preview" | "download") { try { const ticket = await api.downloadResource(positionId, attachmentId, crypto.randomUUID(), purpose); window.open(ticket.contentPath, "_blank", "noopener,noreferrer"); } catch { setNotice("下载未完成，请重试。"); } }
  const items = resources ? [...resources.materials, ...resources.artifacts] : [];
  return <section aria-label="岗位材料与成果"><h2>岗位材料与成果</h2>{items.map((item) => <article key={item.attachmentId}><strong>{item.filename}</strong><span>{item.state}</span>{item.previewAvailable && <button type="button" onClick={() => void open(item.attachmentId, "preview")}>预览{item.filename}</button>}{item.downloadAvailable && <button type="button" onClick={() => void open(item.attachmentId, "download")}>下载{item.filename}</button>}</article>)}{notice && <p role="status">{notice}</p>}</section>;
}
