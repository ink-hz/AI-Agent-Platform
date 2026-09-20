from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..control_plane.models import Role

_PRIVATE = {'Cache-Control': 'private, no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'}
_CONTENT = Path(__file__).parent / 'content'
_DOCUMENTS = {
    'overview': '全景 v1', 'reading': '阅读说明', 'domains': '领域卡',
    'finance': '经营与披露证据', 'products': '产品族与部署形态', 'assets': '已有 AI 资产',
}
_ASSETS = {'panorama.svg': 'image/svg+xml', 'panorama.png': 'image/png'}


def _failure(status: int, detail: str):
    return HTTPException(status, detail, headers=_PRIVATE)


def _read(filename: str) -> bytes:
    # Names are exclusively selected from the fixed route maps below.
    try:
        manifest = json.loads((_CONTENT / 'source-manifest.json').read_text())
        data = (_CONTENT / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['files'][filename]['sha256']:
            raise ValueError
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise _failure(503, 'panorama content unavailable') from None


def build_ai_engineering_router() -> APIRouter:
    router = APIRouter(prefix='/api/v1/ai-engineering')

    def allowed(request: Request, *, probe=False) -> bool:
        context = getattr(request.state, 'auth_context', None)
        if context is None:
            raise _failure(401, 'authentication required')
        permitted = context.role in {Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER}
        if not permitted and not probe:
            raise _failure(403, 'panorama access denied')
        return permitted

    @router.get('/access')
    def access_probe(request: Request):
        return JSONResponse({'allowed': allowed(request, probe=True)}, headers=_PRIVATE)

    @router.get('')
    def index(request: Request):
        allowed(request)
        return JSONResponse({
            'title': '奥比中光 AI 工程全景', 'version': 'v1', 'updated_at': '2026-09-20',
            'diagram': {'svg_path': '/api/v1/ai-engineering/assets/panorama.svg',
                        'png_path': '/api/v1/ai-engineering/assets/panorama.png',
                        'alt': '奥比中光价值链与 AI 覆盖全景'},
            'documents': [{'slug': slug, 'title': title} for slug, title in _DOCUMENTS.items()],
        }, headers=_PRIVATE)

    @router.get('/documents/{slug}')
    def document(slug: str, request: Request):
        allowed(request)
        if slug not in _DOCUMENTS:
            raise _failure(404, 'document not found')
        return JSONResponse({'slug': slug, 'title': _DOCUMENTS[slug],
                             'markdown': _read(slug + '.md').decode('utf-8')}, headers=_PRIVATE)

    @router.get('/assets/{filename}')
    def asset(filename: str, request: Request):
        allowed(request)
        if filename not in _ASSETS:
            raise _failure(404, 'asset not found')
        return Response(_read(filename), media_type=_ASSETS[filename], headers={
            **_PRIVATE, 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        })

    return router
