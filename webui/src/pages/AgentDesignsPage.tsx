import { useEffect, useMemo, useState } from "react";
import { AgentDesignError, getAgentDesign, listAgentDesigns, type AgentDesign, type AgentDesignDocument } from "../agentDesignsApi";
import { prepareDesignMarkdown } from "../agentDesignMarkdown";
import { ArticleMarkdown } from "../components/ai-notes/ArticleMarkdown";
import "../agentDesigns.css";

export function AgentDesignsPage() {
  const [index, setIndex] = useState<AgentDesign[] | null>(null);
  const [selected, setSelected] = useState("");
  const [article, setArticle] = useState<AgentDesignDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [denied, setDenied] = useState(false);
  const content = useMemo(() => article ? prepareDesignMarkdown(article.markdown) : null, [article]);
  function fail(reason: unknown) {
    if (reason instanceof AgentDesignError && [401, 403].includes(reason.status)) {
      setIndex(null); setArticle(null); setDenied(true);
      setError(reason.status === 401 ? "请重新登录。" : "无权限，请联系苍渊。");
    } else setError("设计文档暂时无法读取。");
  }
  useEffect(() => {
    const controller = new AbortController();
    setError(null); setDenied(false); setIndex(null); setArticle(null); setSelected("");
    void listAgentDesigns(controller.signal).then(entries => {
      if (controller.signal.aborted) return;
      setIndex(entries); setSelected(entries[0]?.slug ?? "");
    }).catch(reason => { if (!controller.signal.aborted) fail(reason); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (!selected || denied) return;
    const controller = new AbortController();
    setArticle(null); setError(null);
    void getAgentDesign(selected, controller.signal).then(document => {
      if (!controller.signal.aborted) setArticle(document);
    }).catch(reason => { if (!controller.signal.aborted) fail(reason); });
    return () => controller.abort();
  }, [selected, denied]);
  return <section className="agent-designs-page">
    <header className="agent-designs-heading"><h1>Agent 设计</h1>
      {index && index.length > 0 && <label><span className="sr-only">选择 Agent</span><select aria-label="选择 Agent" value={selected}
        onChange={event => { setArticle(null); setSelected(event.currentTarget.value); }}>
        {index.map(item => <option key={item.slug} value={item.slug}>{item.agent} · {item.title}</option>)}
      </select></label>}
    </header>
    {error ? <div className="agent-designs-state" role="alert">{error}{!denied && <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button>}</div>
      : index?.length === 0 ? <p>暂无设计文档。</p>
      : !article || !content ? <p role="status">正在读取设计文档…</p>
      : <div className="agent-designs-layout">
        <nav className="agent-designs-toc" aria-label="章节目录">{content.headings.map(heading => <a key={heading.id}
          className={`is-level-${heading.level}`} href={`#${heading.id}`} onClick={event => {
            event.preventDefault(); document.getElementById(heading.id)?.scrollIntoView({ block: "start" });
          }}>{heading.title}</a>)}</nav>
        <article className="agent-designs-article" key={article.slug}>
          <details className="agent-designs-source"><summary>文档来源</summary>
            <p>{article.source.repository} / {article.source.path}</p>
            <p>展示快照：{article.source.captured_at.slice(0, 10)} · {article.source.working_tree_modified ? "包含源文档未提交修改" : "已提交版本"}</p>
            <p>源提交：<code>{article.source.commit}</code></p><p>内容校验：<code>{article.source.sha256}</code></p>
          </details>
          <ArticleMarkdown markdown={content.markdown} headingIds={content.headingIds} />
        </article>
      </div>}
  </section>;
}
