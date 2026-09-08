import { useEffect, useMemo, useRef, useState } from "react";

import { ArticleMarkdown } from "../../components/ai-notes/ArticleMarkdown";
import { fetchHrKnowledgeArticle, fetchHrKnowledgeIndex, type HrKnowledgeArticle, type HrKnowledgeIndex } from "../../hrKnowledgeApi";
import type { HrKnowledgeSelection } from "../../conversationTypes";

export interface HrKnowledgeClient {
  index(signal?: AbortSignal): Promise<HrKnowledgeIndex>;
  article(sourceCommit: string, resourceId: string, signal?: AbortSignal): Promise<HrKnowledgeArticle>;
}

const defaultClient: HrKnowledgeClient = { index: fetchHrKnowledgeIndex, article: fetchHrKnowledgeArticle };

function withoutReleaseLinks(markdown: string): string {
  return markdown.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (link, label: string, href: string) => {
    const target = href.trim();
    return !/^[a-z][a-z0-9+.-]*:/i.test(target) && /\.md(?:#[^)]*)?$/i.test(target) ? label : link;
  });
}

export function HrKnowledgePanel({ client = defaultClient, onClose, onSelect }: {
  client?: HrKnowledgeClient;
  onClose: () => void;
  onSelect: (selection: HrKnowledgeSelection) => void;
}) {
  const [index, setIndex] = useState<HrKnowledgeIndex | null>(null);
  const [article, setArticle] = useState<HrKnowledgeArticle | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [failure, setFailure] = useState(false);
  const articleRequest = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    client.index(controller.signal).then(setIndex).catch(() => { if (!controller.signal.aborted) setFailure(true); });
    return () => controller.abort();
  }, [client]);
  const filters = useMemo(() => index ? [...new Set(index.resources.flatMap((item) => [...item.domains, ...item.knowledgeForms]))] : [], [index]);
  const resources = index?.resources.filter((item) => !filter || item.domains.includes(filter) || item.knowledgeForms.includes(filter)) ?? [];
  const open = (id: string) => {
    if (!index) return;
    const request = articleRequest.current + 1;
    articleRequest.current = request;
    setFailure(false);
    void client.article(index.sourceCommit, id).then((value) => {
      if (articleRequest.current === request) setArticle(value);
    }).catch(() => { if (articleRequest.current === request) setFailure(true); });
  };
  return <><button aria-label="关闭方法与模型" className="hr-drawer-backdrop" onClick={onClose} type="button" />
    <aside aria-label="方法与模型" aria-modal="true" className="hr-knowledge-panel" role="dialog">
      <header><div><span>HR 参考知识</span><h2>方法与模型</h2></div><button onClick={onClose} type="button">关闭</button></header>
      <p className="hr-knowledge-note">选择后，请继续写下你想解决的问题。</p>
      {!index && !failure && <p role="status">正在读取方法库…</p>}
      {failure && <p role="alert">方法库暂时无法读取。</p>}
      {index && <div className="hr-knowledge-layout">
        <nav aria-label="方法分类">
          <button aria-pressed={filter === null} onClick={() => setFilter(null)} type="button">全部</button>
          {filters.map((item) => <button aria-pressed={filter === item} key={item} onClick={() => setFilter(item)} type="button">{item}</button>)}
          <ul>{resources.map((item) => <li key={item.id}><button onClick={() => open(item.id)} type="button"><strong>{item.title}</strong><small>{item.domains.join(" · ")} · {item.knowledgeForms.join(" · ")}</small></button></li>)}</ul>
        </nav>
        <main>
          {article ? <>
            <div className="hr-knowledge-meta"><span>版本 {article.revision}</span></div>
            <ArticleMarkdown markdown={withoutReleaseLinks(article.markdown)} />
            <button className="hr-knowledge-discuss" onClick={() => onSelect({ sourceCommit: article.sourceCommit,
              id: article.id, revision: article.revision, sha256: article.sha256 })} type="button">带着这个方法讨论</button>
          </> : <div className="hr-knowledge-empty"><h3>选择一个方法查看详情</h3><p>可按适用领域或知识形式筛选，再带入下一轮对话。</p></div>}
        </main>
      </div>}
    </aside>
  </>;
}
