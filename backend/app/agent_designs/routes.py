from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from app.control_plane.models import Role

CONTENT = Path(__file__).resolve().parent / 'content'
HEADERS = {'Cache-Control': 'private, no-store', 'Pragma': 'no-cache'}


def _index():
    entries = json.loads((CONTENT / 'index.json').read_text())['documents']
    if (not isinstance(entries, list)
            or not all(re.fullmatch('[a-z][a-z0-9-]{0,63}', item['slug']) for item in entries)
            or len({item['slug'] for item in entries}) != len(entries)):
        raise ValueError('invalid design registry')
    return entries


def build_agent_designs_router() -> APIRouter:
    router = APIRouter(prefix='/api/v1/manage/agent-designs', tags=['agent-designs'])

    def authorize(request: Request):
        context = getattr(request.state, 'auth_context', None)
        if context is None:
            raise HTTPException(401, 'authentication required', headers=HEADERS)
        if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
            raise HTTPException(403, 'platform management required', headers=HEADERS)

    @router.get('')
    def index(request: Request, response: Response):
        authorize(request)
        response.headers.update(HEADERS)
        try:
            return {'documents': _index()}
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            raise HTTPException(503, 'Agent designs unavailable', headers=HEADERS) from None

    @router.get('/{slug}')
    def document(slug: str, request: Request, response: Response):
        authorize(request)
        response.headers.update(HEADERS)
        try:
            entry = next((item for item in _index() if item['slug'] == slug), None)
            if entry is None:
                raise HTTPException(404, 'Agent design not found', headers=HEADERS)
            raw = (CONTENT / (entry['slug'] + '.md')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['source']['sha256']:
                raise ValueError('snapshot integrity mismatch')
            return {**entry, 'markdown': raw.decode('utf-8')}
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            raise HTTPException(503, 'Agent design unavailable', headers=HEADERS) from None

    return router
