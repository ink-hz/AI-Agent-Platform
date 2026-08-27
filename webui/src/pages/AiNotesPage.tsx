import { useEffect, useMemo, useRef, useState } from "react";

import { aiNotesClient, type AiNotesClient } from "../aiNotesApi";
import { AiNotesApiError, type AiNoteArticle as AiNoteArticleData, type AiNotesIndex } from "../aiNotesTypes";
import { AiNotesTree, type AiNotesSelection } from "../components/ai-notes/AiNotesTree";
import { AiNoteArticle } from "../components/ai-notes/AiNoteArticle";
import { navigate, type NavigateOptions } from "../router";


interface AiNotesPageProps {
  categorySlug?: string;
  articleSlug?: string;
  client?: AiNotesClient;
  onNavigate?: (path: string, options?: NavigateOptions) => void;
}


function firstArticle(index: AiNotesIndex): AiNotesSelection | null {
  for (const category of index.categories) {
    const article = category.articles[0];
    if (article) return { categorySlug: category.slug, articleSlug: article.slug };
  }
  return null;
}


export function AiNotesPage({
  categorySlug,
  articleSlug,
  client = aiNotesClient,
  onNavigate = navigate,
}: AiNotesPageProps) {
  const [index, setIndex] = useState<AiNotesIndex | null>(null);
  const [indexState, setIndexState] = useState<"loading" | "ready" | "error">("loading");
  const [automaticSelection, setAutomaticSelection] = useState<AiNotesSelection | null>(null);
  const [article, setArticle] = useState<AiNoteArticleData | null>(null);
  const [articleError, setArticleError] = useState<"missing" | "unavailable" | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const articleRequest = useRef(0);
  const mobileOpener = useRef<HTMLButtonElement>(null);
  const mobileCloser = useRef<HTMLButtonElement>(null);
  const mobileWasOpen = useRef(false);

  useEffect(() => {
    if (mobileOpen) mobileCloser.current?.focus();
    else if (mobileWasOpen.current) mobileOpener.current?.focus();
    mobileWasOpen.current = mobileOpen;
  }, [mobileOpen]);

  useEffect(() => {
    if (!mobileOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileOpen]);

  useEffect(() => {
    const controller = new AbortController();
    setIndexState("loading");
    void client.fetchIndex(controller.signal).then((loaded) => {
      setIndex(loaded);
      setIndexState("ready");
      if (!categorySlug && !articleSlug) {
        const selected = firstArticle(loaded);
        setAutomaticSelection(selected);
        if (selected) {
          onNavigate(`/ai-notes/${selected.categorySlug}/${selected.articleSlug}`, { replace: true });
        }
      }
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setIndexState("error");
    });
    return () => controller.abort();
  }, [client, onNavigate]);

  const selectedPath = useMemo<AiNotesSelection | null>(() => {
    if (categorySlug && articleSlug) return { categorySlug, articleSlug };
    return automaticSelection;
  }, [articleSlug, automaticSelection, categorySlug]);

  useEffect(() => {
    if (!selectedPath || indexState !== "ready") return;
    const controller = new AbortController();
    const request = ++articleRequest.current;
    setArticleError(null);
    void client.fetchArticle(
      selectedPath.categorySlug,
      selectedPath.articleSlug,
      controller.signal,
    ).then((loaded) => {
      if (request !== articleRequest.current) return;
      setArticle(loaded);
      setArticleError(null);
    }).catch((error: unknown) => {
      if (controller.signal.aborted || request !== articleRequest.current) return;
      setArticleError(error instanceof AiNotesApiError && error.status === 404 ? "missing" : "unavailable");
    });
    return () => controller.abort();
  }, [client, indexState, selectedPath]);

  if (indexState === "loading") {
    return <section className="ai-notes-page-state" role="status">正在读取文章目录</section>;
  }
  if (indexState === "error" || !index) {
    return <section className="ai-notes-page-state" role="alert">
      <h1>AI 工程笔记暂时不可用</h1><p>文章目录暂时无法读取，请稍后再试。</p>
    </section>;
  }

  const count = index.categories.reduce((sum, category) => sum + category.articles.length, 0);
  const selectArticle = (nextCategory: string, nextArticle: string) => {
    setMobileOpen(false);
    onNavigate(`/ai-notes/${nextCategory}/${nextArticle}`);
  };
  return (
    <div className="ai-notes-layout">
      <button
        aria-expanded={mobileOpen}
        aria-label="打开文章目录"
        className="ai-notes-mobile-menu"
        onClick={() => setMobileOpen(true)}
        ref={mobileOpener}
        type="button"
      >目录</button>
      <aside className="ai-notes-sidebar">
        <AiNotesTree
          index={index}
          onSelect={selectArticle}
          selectedPath={selectedPath}
        />
      </aside>
      {mobileOpen && <>
        <button
          aria-label="关闭文章目录遮罩"
          className="ai-notes-drawer-backdrop"
          onClick={() => setMobileOpen(false)}
          tabIndex={-1}
          type="button"
        />
        <aside aria-label="文章目录" aria-modal="true" className="ai-notes-drawer" role="dialog">
          <button
            aria-label="关闭文章目录"
            className="ai-notes-drawer-close"
            onClick={() => setMobileOpen(false)}
            ref={mobileCloser}
            type="button"
          >×</button>
          <AiNotesTree index={index} onSelect={selectArticle} selectedPath={selectedPath} />
        </aside>
      </>}
      <section className="ai-notes-reader" aria-live="polite">
        {articleError === "missing" && <div className="ai-notes-reader-notice" role="alert">文章不存在，请从目录选择其他文章。</div>}
        {articleError === "unavailable" && <div className="ai-notes-reader-notice" role="alert">文章暂时无法打开，已保留当前内容。</div>}
        {article ? <AiNoteArticle article={article} /> : count === 0
          ? <div className="ai-notes-empty"><h2>暂无已发布文章</h2></div>
          : !articleError && <div className="ai-notes-loading" role="status">正在打开文章</div>}
      </section>
    </div>
  );
}
