from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


def _non_blank(value: str) -> str:
    selected = value.strip()
    if not selected:
        raise ValueError("value must not be blank")
    return selected


class CategoryFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")

    _title_not_blank = field_validator("title")(_non_blank)


class ArticleFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    description: str = Field(min_length=1)
    published_at: date = Field(alias="publishedAt")
    updated_at: date | None = Field(default=None, alias="updatedAt")
    tags: tuple[str, ...] = ()
    draft: StrictBool

    _text_not_blank = field_validator("title", "description")(_non_blank)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_non_blank(item) for item in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("tags must be unique")
        return normalized


class AiNoteSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    filename: str
    description: str
    published_at: date
    updated_at: date | None
    tags: tuple[str, ...]
    reading_minutes: int = Field(ge=1)


class AiNoteCategory(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    articles: tuple[AiNoteSummary, ...]


class AiNotesIndex(BaseModel):
    model_config = ConfigDict(frozen=True)

    categories: tuple[AiNoteCategory, ...]


class AiNoteArticle(AiNoteSummary):
    category_slug: str
    category_title: str
    markdown: str
