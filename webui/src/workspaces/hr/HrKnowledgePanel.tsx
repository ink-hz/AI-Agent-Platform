import { useEffect, useMemo, useState } from "react";

import { ArticleMarkdown } from "../../components/ai-notes/ArticleMarkdown";
import { fetchHrKnowledgeArticle, fetchHrKnowledgeIndex, type HrKnowledgeArticle, type HrKnowledgeIndex } from "../../hrKnowledgeApi";
import type { HrKnowledgeSelection } from "../../conversationTypes";

export interface HrKnowledgeClient {
  index(signal?: AbortSignal): Promise<HrKnowledgeIndex>;
  article(sourceCommit: string, resourceId: string, signal?: AbortSignal): Promise<HrKnowledgeArticle>;
}

const defaultClient: HrKnowledgeClient = { index: fetchHrKnowledgeIndex, article: fetchHrKnowledgeArticle };

export function HrKnowledgePanel({ client = defaultClient, onClose, onSelect }: {
  client?: HrKnowledgeClient;
  onClose: () => void;
  onSelect: (selection: HrKnowledgeSelection) => void;
}) {
  const [index, setIndex] = useState<HrKnowledgeIndex | null>(null);
  const [article, setArticle] = useState<HrKnowledgeArticle | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [failure, setFailure] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    client.index(controller.signal).then(setIndex).catch(() => { if (!controller.signal.aborted) setFailure(true); });
    return () => controller.abort();
  }, [client]);
  const filters = useMemo(() => index ? [...new Set(index.resources.flatMap((item) => [...item.domains, ...item.knowledgeForms]))] : [], [index]);
  const resources = index?.resources.filter((item) => !filter || item.domains.includes(filter) || item.knowledgeForms.includes(filter)) ?? [];
  const open = (id: string) => {
    if (!index) return;
    setFailure(false);
    void client.article(index.sourceCommit, id).then(setArticle).catch(() => setFailure(true));
  };
  return <><button aria-label="关闭方法与模型" className="hr-drawer-backdrop" onClick={onClose} type="button" />
    <aside aria-label="方法与模型" aria-modal="true" className="hr-knowledge-panel" role="dialog">
      <header><div><span>HR 参考知识</span><h2>方法与模型</h2></div><button onClick={onClose} type="button">关闭</button></header>
      <p className="hr-knowledge-note">选择方法只会把版本标识加入下一轮任务；回答中提及的参考方法是 Agent 自述，不代表已核验实际读取。</p>
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
            <div className="hr-knowledge-meta"><span>版本 {article.revision}</span><span>来源 {article.path}</span></div>
            <ArticleMarkdown markdown={article.markdown} />
            <button className="hr-knowledge-discuss" onClick={() => onSelect({ sourceCommit: article.sourceCommit,
              id: article.id, revision: article.revision, sha256: article.sha256 })} type="button">带着这个方法讨论</button>
          </> : <ArticleMarkdown markdown={index.index} />}
        </main>
      </div>}
    </aside>
  </>;
}
