from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..control_plane.models import AuthContext, Role
from .exports import render_png, render_svg
from .models import PanoramaValidationError, validate_panorama
from .seed import PANORAMA_SEED
from .store import PanoramaConflict, PanoramaStore, PanoramaUnavailable

_PRIVATE = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
_CONTENT = Path(__file__).parent / "content"
_DOCUMENTS = {
    "overview": "全景 v1",
    "reading": "阅读说明",
    "domains": "领域卡",
    "finance": "经营与披露证据",
    "products": "产品族与部署形态",
    "assets": "已有 AI 资产",
}
_ASSETS = {"panorama.svg": "image/svg+xml", "panorama.png": "image/png"}


def _failure(status, detail):
    return HTTPException(status, detail, headers=_PRIVATE)


def _read(filename):
    try:
        manifest = json.loads((_CONTENT / "source-manifest.json").read_text())
        data = (_CONTENT / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["files"][filename]["sha256"]:
            raise ValueError
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise _failure(503, "panorama content unavailable") from None


def _store():
    path = os.environ.get("PLATFORM_PANORAMA_STATE_PATH", "").strip()
    return PanoramaStore(path) if path else None


def _state():
    store = _store()
    if store is None:
        return {
            "revision": 0,
            "published": PANORAMA_SEED,
            "draft": None,
            "previous": None,
        }
    try:
        return store.read()
    except PanoramaUnavailable:
        raise _failure(503, "panorama state unavailable") from None


def _digest(data):
    return hashlib.sha256(
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def build_ai_engineering_router():
    router = APIRouter(prefix="/api/v1/ai-engineering")

    def allowed(request, probe=False, mutation=False):
        context = getattr(request.state, "auth_context", None)
        if not isinstance(context, AuthContext):
            raise _failure(401, "authentication required")
        permitted = context.role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
        if not permitted and not probe:
            raise _failure(403, "panorama access denied")
        if mutation:
            if context.hard_stale_read_only:
                raise _failure(503, "hard_stale_read_only")
            expected = getattr(request.state, "csrf_token", "")
            submitted = request.headers.get("x-csrf-token", "")
            if (
                not expected
                or not submitted
                or not hmac.compare_digest(expected, submitted)
            ):
                raise _failure(403, "CSRF verification failed")
        return context if permitted else False

    def editor_store():
        store = _store()
        if store is None:
            raise _failure(503, "panorama editing unavailable")
        return store

    def write(call):
        try:
            return JSONResponse(call(), headers=_PRIVATE)
        except PanoramaConflict:
            raise _failure(409, "panorama revision conflict") from None
        except (PanoramaValidationError, ValueError) as error:
            raise _failure(422, str(error)) from None
        except PanoramaUnavailable:
            raise _failure(503, "panorama state unavailable") from None

    @router.get("/access")
    def access(request: Request):
        return JSONResponse(
            {"allowed": bool(allowed(request, probe=True))}, headers=_PRIVATE
        )

    @router.get("")
    def index(request: Request):
        allowed(request)
        data = _state()["published"]
        return JSONResponse(
            {
                "title": data["title"],
                "version": data["version"],
                "updated_at": data["updated_at"],
                "diagram": {
                    "svg_path": "/api/v1/ai-engineering/assets/panorama.svg",
                    "png_path": "/api/v1/ai-engineering/assets/panorama.png",
                    "alt": "奥比中光四层 AI 工程全景",
                },
                "documents": [{"slug": s, "title": t} for s, t in _DOCUMENTS.items()],
            },
            headers=_PRIVATE,
        )

    @router.get("/documents/{slug}")
    def document(slug: str, request: Request):
        allowed(request)
        if slug not in _DOCUMENTS:
            raise _failure(404, "document not found")
        return JSONResponse(
            {
                "slug": slug,
                "title": _DOCUMENTS[slug],
                "markdown": _read(slug + ".md").decode(),
            },
            headers=_PRIVATE,
        )

    @router.get("/panorama")
    def panorama(request: Request):
        allowed(request)
        data = _state()["published"]
        return JSONResponse(
            data, headers={**_PRIVATE, "X-Panorama-Content-SHA256": _digest(data)}
        )

    @router.get("/panorama/draft")
    def draft(request: Request):
        allowed(request)
        try:
            return JSONResponse(editor_store().read(), headers=_PRIVATE)
        except PanoramaUnavailable:
            raise _failure(503, "panorama state unavailable") from None

    @router.put("/panorama/draft")
    def save(request: Request, body: Annotated[dict, Body()]):
        context = allowed(request, mutation=True)
        if set(body) != {"expected_revision", "data"}:
            raise _failure(422, "invalid draft request")
        return write(
            lambda: editor_store().save_draft(
                body["expected_revision"],
                validate_panorama(body["data"]),
                actor=str(context.internal_user_id),
            )
        )

    @router.delete("/panorama/draft")
    def delete(request: Request, body: Annotated[dict, Body()]):
        context = allowed(request, mutation=True)
        if set(body) != {"expected_revision"}:
            raise _failure(422, "invalid draft request")
        return write(
            lambda: editor_store().delete_draft(
                body["expected_revision"], actor=str(context.internal_user_id)
            )
        )

    @router.post("/panorama/publish")
    def publish(request: Request, body: Annotated[dict, Body()]):
        context = allowed(request, mutation=True)
        if set(body) != {"expected_revision"}:
            raise _failure(422, "invalid publish request")
        return write(
            lambda: editor_store().publish(
                body["expected_revision"], actor=str(context.internal_user_id)
            )
        )

    @router.post("/panorama/restore")
    def restore(request: Request, body: Annotated[dict, Body()]):
        context = allowed(request, mutation=True)
        if set(body) != {"expected_revision"}:
            raise _failure(422, "invalid restore request")
        return write(
            lambda: editor_store().restore(
                body["expected_revision"], actor=str(context.internal_user_id)
            )
        )

    def published_export(request, version, kind):
        allowed(request)
        data = _state()["published"]
        if version is not None and version != data["version"]:
            raise _failure(409, "panorama version mismatch")
        payload = render_svg(data) if kind == "svg" else render_png(data)
        media = "image/svg+xml" if kind == "svg" else "image/png"
        headers = {
            **_PRIVATE,
            "Content-Disposition": f'attachment; filename="orbbec-ai-panorama.{kind}"',
            "X-Panorama-Content-SHA256": _digest(data),
        }
        if kind == "svg":
            headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; sandbox"
            )
        return Response(payload, media_type=media, headers=headers)

    @router.get("/export.svg")
    def svg(request: Request, version: str | None = None):
        return published_export(request, version, "svg")

    @router.get("/export.png")
    def png(request: Request, version: str | None = None):
        return published_export(request, version, "png")

    @router.get("/assets/{filename}")
    def asset(filename: str, request: Request):
        allowed(request)
        if filename not in _ASSETS:
            raise _failure(404, "asset not found")
        return Response(
            _read(filename),
            media_type=_ASSETS[filename],
            headers={
                **_PRIVATE,
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            },
        )

    return router
