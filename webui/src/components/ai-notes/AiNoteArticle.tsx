import type { AiNoteArticle as AiNoteArticleData } from "../../aiNotesTypes";
import { ArticleMarkdown } from "./ArticleMarkdown";


export function AiNoteArticle({ article }: { article: AiNoteArticleData }) {
  return <article className="ai-note-article">
    <header className="ai-note-header">
      <p className="ai-note-path">{article.category_title} / {article.filename}</p>
      <h1>{article.title}</h1>
      <div className="ai-note-meta">
        <time dateTime={article.published_at}>发布于 {article.published_at}</time>
        {article.updated_at && article.updated_at !== article.published_at
          && <time dateTime={article.updated_at}>更新于 {article.updated_at}</time>}
        <span>约 {article.reading_minutes} 分钟</span>
      </div>
      {article.tags.length > 0 && <ul className="ai-note-tags" aria-label="文章标签">
        {article.tags.map((tag) => <li key={tag}>{tag}</li>)}
      </ul>}
    </header>
    <ArticleMarkdown markdown={article.markdown} />
  </article>;
}
