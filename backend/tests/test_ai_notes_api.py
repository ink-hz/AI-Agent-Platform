from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_notes.repository import AiNotesRepository
from app.ai_notes.routes import build_ai_notes_router
from test_ai_notes_repository import write_article, write_category


def repository_with_article(tmp_path: Path) -> AiNotesRepository:
    category = write_category(
        tmp_path, "01-foundations", "基础与原理", "foundations"
    )
    write_article(category, "01-handbook.md", slug="handbook")
    write_article(category, "02-draft.md", slug="draft", draft=True)
    return AiNotesRepository.load(tmp_path, today=date(2026, 8, 27))


def test_index_and_article_are_no_store(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(build_ai_notes_router(repository_with_article(tmp_path)))
    client = TestClient(app)

    index = client.get("/api/v1/ai-notes")
    article = client.get("/api/v1/ai-notes/foundations/handbook")

    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    assert index.headers["pragma"] == "no-cache"
    assert index.json()["categories"][0]["articles"][0]["slug"] == "handbook"
    assert index.json()["categories"][0]["articles"][0]["author"] == "苍渊"
    assert index.json()["categories"][0]["articles"][0]["motto"] == "博观而约取，厚积而薄发。"
    assert article.status_code == 200
    assert article.headers["cache-control"] == "no-store"
    assert article.json()["markdown"].startswith("# 正文")


def test_unknown_draft_and_invalid_keys_are_generic_404(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(build_ai_notes_router(repository_with_article(tmp_path)))
    client = TestClient(app)

    for path in (
        "/api/v1/ai-notes/foundations/missing",
        "/api/v1/ai-notes/foundations/draft",
        "/api/v1/ai-notes/%2E%2E/handbook",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "AI note not found"}
        assert response.headers["cache-control"] == "no-store"


class BrokenReader:
    def index(self):
        raise RuntimeError("/private/path SECRET_BODY marker-value")

    def article(self, category_slug: str, article_slug: str):
        raise RuntimeError(
            f"/private/path SECRET_BODY marker-value {category_slug} {article_slug}"
        )


def test_content_failure_is_generic_no_store_503() -> None:
    app = FastAPI()
    app.include_router(build_ai_notes_router(BrokenReader()))
    client = TestClient(app)

    for path in (
        "/api/v1/ai-notes",
        "/api/v1/ai-notes/foundations/handbook",
    ):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "AI notes unavailable"}
        assert response.headers["cache-control"] == "no-store"
        assert "/private/path" not in response.text
        assert "SECRET_BODY" not in response.text
