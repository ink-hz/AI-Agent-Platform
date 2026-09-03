import { useMemo, useState } from "react";

import type { ArtifactVersion, ConversationAttachment } from "../../conversationTypes";
import { AttachmentCard } from "./AttachmentCard";

function statusLabel(version: ArtifactVersion): string {
  if (version.status === "failed") return "生成失败";
  if (version.status === "processing") return "正在校验";
  return "可下载";
}

export function ArtifactVersionList({ versions, onOpen, onDownloadAll }: {
  versions: ArtifactVersion[];
  onOpen?: (attachment: ConversationAttachment, purpose: "preview" | "download") => void;
  onDownloadAll?: () => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const groups = useMemo(() => {
    const result = new Map<string, ArtifactVersion[]>();
    for (const version of versions) {
      const selected = result.get(version.artifactKey) ?? [];
      selected.push(version); result.set(version.artifactKey, selected);
    }
    for (const selected of result.values()) selected.sort((left, right) => right.versionNo - left.versionNo);
    return [...result.entries()];
  }, [versions]);
  const readyCount = versions.filter((version) => version.status === "ready" && version.attachment).length;
  if (versions.length === 0) return null;
  return <section className="conversation-artifacts" aria-label="生成结果">
    <header><strong>生成结果</strong>{readyCount > 1 && onDownloadAll && <button className="conversation-download-all" onClick={onDownloadAll} type="button">全部下载</button>}</header>
    {groups.map(([artifactKey, selected]) => {
      const current = selected.find((version) => version.current && version.status === "ready")
        ?? selected.find((version) => version.status === "ready") ?? selected[0];
      const shown = expanded[artifactKey] ? selected : [current];
      return <article className="conversation-artifact" key={artifactKey}>
        {shown.map((version) => <div className="conversation-artifact-version" data-status={version.status} key={version.producerVersionId}>
          <div className="conversation-artifact-version-heading"><span>版本 {version.versionNo}</span>
            {version === current && version.status === "ready" && <mark>当前版本</mark>}
            <small>{statusLabel(version)}</small>
          </div>
          {version.attachment && <AttachmentCard attachment={version.attachment} compact onOpen={onOpen} />}
        </div>)}
        {selected.length > 1 && <button aria-expanded={Boolean(expanded[artifactKey])} className="conversation-version-toggle" onClick={() => setExpanded((value) => ({ ...value, [artifactKey]: !value[artifactKey] }))} type="button">
          {expanded[artifactKey] ? "收起历史版本" : `查看所有版本（${selected.length}）`}
        </button>}
      </article>;
    })}
  </section>;
}
