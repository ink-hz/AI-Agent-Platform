import { useState } from "react";

import type { ConversationCitation } from "../../conversationTypes";

function retrievedLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

export function CitationList({ citations }: { citations: ConversationCitation[] }) {
  const [expanded, setExpanded] = useState(false);
  if (citations.length === 0) return null;
  return <section className="conversation-citations">
    <button aria-expanded={expanded} className="conversation-citations-toggle" onClick={() => setExpanded((value) => !value)} type="button">
      来源（{citations.length}）<span aria-hidden="true">{expanded ? "收起" : "展开"}</span>
    </button>
    {expanded && <ol>
      {citations.map((citation, index) => <li key={citation.citationKey}>
        <span className="conversation-citation-number">[{index + 1}]</span>
        <div><a href={citation.url} rel="noreferrer noopener" target="_blank">{citation.title}</a>
          <p>{citation.site}{retrievedLabel(citation.retrievedAt) ? ` · 检索于 ${retrievedLabel(citation.retrievedAt)}` : ""}</p>
          <small>支持：{citation.supports.join("；")}</small>
        </div>
      </li>)}
    </ol>}
  </section>;
}
