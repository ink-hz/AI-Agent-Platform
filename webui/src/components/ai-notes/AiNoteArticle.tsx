import type { AiNoteArticle as AiNoteArticleData } from "../../aiNotesTypes";
import { ArticleMarkdown } from "./ArticleMarkdown";


export function AiNoteArticle({ article }: { article: AiNoteArticleData }) {
  return <article className="ai-note-article">
    <header className="ai-note-header">
      <p className="ai-note-path">{article.category_title} / {article.filename}</p>
      <h1>{article.title}</h1>
      <div className="ai-note-signature">
        <p className="ai-note-byline">
          <span>by</span>{" "}
          <strong className="ai-note-author">{article.author}</strong>
          <span aria-hidden="true"> · </span>
          <time dateTime={article.published_at}>{article.published_at}</time>
          {article.updated_at && article.updated_at !== article.published_at && <>
            <span aria-hidden="true"> · </span>
            <span>updated </span>
            <time dateTime={article.updated_at}>{article.updated_at}</time>
          </>}
        </p>
        {article.motto && <p className="ai-note-motto">// {article.motto}</p>}
      </div>
      {article.tags.length > 0 && <ul className="ai-note-tags" aria-label="文章标签">
        {article.tags.map((tag) => <li key={tag}>{tag}</li>)}
      </ul>}
    </header>
    <ArticleMarkdown markdown={article.markdown} />
  </article>;
}
