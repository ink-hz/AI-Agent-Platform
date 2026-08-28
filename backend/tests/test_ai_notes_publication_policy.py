from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.ai_notes.models import ArticleFrontmatter
from app.ai_notes.publication_policy import (
    AiNotesPublicationPolicyError,
    validate_published_article_policy,
)


def article_source(
    markdown: str,
    *,
    slug: str = "sample",
    draft: bool = False,
    author: str = "苍渊",
    motto: str = "博观而约取，厚积而薄发。",
) -> tuple[Path, ArticleFrontmatter, str]:
    frontmatter = ArticleFrontmatter.model_validate(
        {
            "title": "示例文章",
            "slug": slug,
            "description": "用于验证发布策略。",
            "author": author,
            "motto": motto,
            "publishedAt": date(2026, 8, 28),
            "updatedAt": date(2026, 8, 28),
            "tags": ["AI 工程"],
            "draft": draft,
        }
    )
    return Path(f"{slug}.md"), frontmatter, markdown


VALID_DIAGRAM = """## 正文

```mermaid
flowchart TB
    accTitle: 示例系统边界
    accDescr: 输入经过模型后形成结果。
    subgraph SYSTEM[系统边界]
        A[输入] --> B[模型] --> C[结果]
    end
    classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
    class A input;
    style SYSTEM fill:#FFFFFF,stroke:#CBD5E1,color:#172033;
```
"""


@pytest.mark.parametrize(
    ("author", "motto"),
    [("其他作者", "博观而约取，厚积而薄发。"), ("苍渊", "其他座右铭")],
)
def test_rejects_wrong_published_identity(author: str, motto: str) -> None:
    with pytest.raises(AiNotesPublicationPolicyError):
        validate_published_article_policy(
            (article_source(VALID_DIAGRAM, author=author, motto=motto),)
        )


@pytest.mark.parametrize("markdown", ["# 一级标题\n", "## 正文\n\n<div>HTML</div>\n"])
def test_rejects_h1_and_raw_html(markdown: str) -> None:
    with pytest.raises(AiNotesPublicationPolicyError):
        validate_published_article_policy((article_source(markdown),))


@pytest.mark.parametrize(
    "diagram",
    [
        "```mermaid\nflowchart TB\nA-->B\n```",
        "```mermaid\nflowchart TB\naccTitle: 只有标题\nA-->B\n```",
        (
            "```mermaid\nflowchart TB\naccTitle: 无样式\n"
            "accDescr: 没有语义样式。\nA-->B\n```"
        ),
        """```mermaid
flowchart TB
accTitle: 重复标题一
accTitle: 重复标题二
accDescr: 同一张图不能声明两次标题。
A-->B
classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
```""",
        """```mermaid
flowchart TB
accTitle: 灰色分组
accDescr: 大分组错误使用灰色背景。
subgraph SYSTEM[系统]
A-->B
end
classDef input fill:#DBEAFE,stroke:#60A5FA,color:#172033;
style SYSTEM fill:#F8FAFC,stroke:#CBD5E1,color:#172033;
```""",
    ],
)
def test_rejects_incomplete_or_gray_mermaid(diagram: str) -> None:
    with pytest.raises(AiNotesPublicationPolicyError):
        validate_published_article_policy((article_source(f"## 正文\n\n{diagram}\n"),))


def test_rejects_duplicate_accessibility_metadata() -> None:
    with pytest.raises(AiNotesPublicationPolicyError):
        validate_published_article_policy(
            (
                article_source(VALID_DIAGRAM, slug="first"),
                article_source(VALID_DIAGRAM, slug="second"),
            )
        )


def test_ignores_editorial_rules_for_drafts() -> None:
    validate_published_article_policy(
        (article_source("# 未完成\n\n旧内容。", draft=True),)
    )


def test_accepts_clean_published_article() -> None:
    validate_published_article_policy((article_source(VALID_DIAGRAM),))
