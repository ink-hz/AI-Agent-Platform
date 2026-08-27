from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, HTTPException, Response

from .models import AiNoteArticle, AiNotesIndex


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class AiNotesReader(Protocol):
    def index(self) -> AiNotesIndex:
        raise NotImplementedError

    def article(
        self, category_slug: str, article_slug: str
    ) -> AiNoteArticle | None:
        raise NotImplementedError


class UnavailableAiNotesReader:
    def index(self) -> AiNotesIndex:
        raise RuntimeError("AI notes unavailable")

    def article(
        self, category_slug: str, article_slug: str
    ) -> AiNoteArticle | None:
        del category_slug, article_slug
        raise RuntimeError("AI notes unavailable")


def build_ai_notes_router(reader: AiNotesReader) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ai-notes", tags=["ai-notes"])

    @router.get("")
    def index(response: Response):
        response.headers.update(_NO_STORE)
        try:
            return reader.index()
        except Exception:
            raise HTTPException(
                503,
                "AI notes unavailable",
                headers=_NO_STORE,
            ) from None

    @router.get("/{category_slug}/{article_slug}")
    def article(
        category_slug: str,
        article_slug: str,
        response: Response,
    ):
        response.headers.update(_NO_STORE)
        try:
            selected = reader.article(category_slug, article_slug)
        except Exception:
            raise HTTPException(
                503,
                "AI notes unavailable",
                headers=_NO_STORE,
            ) from None
        if selected is None:
            raise HTTPException(
                404,
                "AI note not found",
                headers=_NO_STORE,
            )
        return selected

    return router
