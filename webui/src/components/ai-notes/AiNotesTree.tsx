import { useEffect, useMemo, useState } from "react";

import type { AiNotesIndex } from "../../aiNotesTypes";


export interface AiNotesSelection {
  categorySlug: string;
  articleSlug: string;
}

interface AiNotesTreeProps {
  index: AiNotesIndex;
  selectedPath: AiNotesSelection | null;
  onSelect: (categorySlug: string, articleSlug: string) => void;
}


export function AiNotesTree({ index, selectedPath, onSelect }: AiNotesTreeProps) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(selectedPath ? [selectedPath.categorySlug] : []),
  );

  useEffect(() => {
    if (!selectedPath) return;
    setExpanded((current) => {
      if (current.has(selectedPath.categorySlug)) return current;
      const next = new Set(current);
      next.add(selectedPath.categorySlug);
      return next;
    });
  }, [selectedPath]);

  const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
  const categories = useMemo(() => index.categories.flatMap((category) => {
    if (!normalizedQuery) return [{ ...category, visibleArticles: category.articles }];
    const categoryMatches = category.title.toLocaleLowerCase("zh-CN").includes(normalizedQuery);
    const visibleArticles = categoryMatches
      ? category.articles
      : category.articles.filter((article) => [article.title, article.filename]
        .some((value) => value.toLocaleLowerCase("zh-CN").includes(normalizedQuery)));
    return visibleArticles.length ? [{ ...category, visibleArticles }] : [];
  }), [index.categories, normalizedQuery]);
  const total = index.categories.reduce((sum, category) => sum + category.articles.length, 0);

  return (
    <section className="ai-notes-tree" aria-label="文章目录">
      <header className="ai-notes-tree-header">
        <h1>AI 工程笔记</h1>
        <span>{total} 篇文章</span>
      </header>
      <label className="ai-notes-search">
        <span className="sr-only">搜索文章</span>
        <input
          aria-label="搜索文章"
          onChange={(event) => setQuery(event.currentTarget.value)}
          placeholder="搜索文章"
          type="search"
          value={query}
        />
      </label>
      <div className="ai-notes-categories">
        {categories.map((category) => {
          const open = Boolean(normalizedQuery) || expanded.has(category.slug);
          return <section className="ai-notes-category" key={category.slug}>
            <button
              aria-expanded={open}
              className="ai-notes-category-toggle"
              data-category={category.slug}
              onClick={() => setExpanded((current) => {
                const next = new Set(current);
                if (next.has(category.slug)) next.delete(category.slug);
                else next.add(category.slug);
                return next;
              })}
              type="button"
            >
              <span>{category.title}</span><span>{category.articles.length}</span>
            </button>
            {open && <div className="ai-notes-files">
              {category.visibleArticles.map((article) => {
                const current = selectedPath?.categorySlug === category.slug
                  && selectedPath.articleSlug === article.slug;
                return <button
                  aria-current={current ? "page" : undefined}
                  className={current ? "is-current" : undefined}
                  data-article={`${category.slug}/${article.slug}`}
                  key={article.slug}
                  onClick={() => onSelect(category.slug, article.slug)}
                  type="button"
                ><span>{article.title}</span><small>{article.filename}</small></button>;
              })}
            </div>}
          </section>;
        })}
        {normalizedQuery && categories.length === 0 && <p className="ai-notes-no-results" role="status">
          没有匹配的文章
        </p>}
      </div>
    </section>
  );
}
