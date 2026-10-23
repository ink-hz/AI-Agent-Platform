"""SDK knowledge facts and model proposals behind current SDK read authorization."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import BrainModelError
from app.control_plane.models import Role
from app.sdk_admin.routes import _require_access

from .analysis import AnalysisFailure, ChangeAnalyzer
from .corpus import KnowledgeCorpus, KnowledgeUnavailable
from .git_source import GitSourceReader, SourceObservationError
from .models import AnalysisRequest, ChangeRequest
from .service import KnowledgeService


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request):
            try:
                response = await handler(request)
            except HTTPException as error:
                response = JSONResponse({'detail': error.detail}, status_code=error.status_code)
            except RequestValidationError:
                response = JSONResponse({'detail': 'sdk_knowledge_query_invalid'}, status_code=422)
            response.headers['Cache-Control'] = 'private, no-store'
            return response

        return private


def _service(request: Request):
    service = getattr(request.app.state, 'sdk_knowledge_service', None)
    if service is not None:
        return service
    path, digest = os.getenv('SDK_KNOWLEDGE_INDEX_PATH', ''), os.getenv('SDK_KNOWLEDGE_INDEX_SHA256', '')
    if not path or not digest:
        raise HTTPException(503, 'sdk_knowledge_not_configured')
    try:
        corpus = KnowledgeCorpus.from_file(Path(path), expected_sha256=digest)
        root = os.getenv('SDK_KNOWLEDGE_SOURCE_ROOT', '')
        source = GitSourceReader(Path(root), corpus.repositories) if root else None
        service = KnowledgeService(corpus, source)
    except (KnowledgeUnavailable, ValueError, OSError):
        raise HTTPException(503, 'sdk_knowledge_unavailable') from None
    request.app.state.sdk_knowledge_service = service
    return service


async def _require_source_access(request: Request, repository: str):
    await _require_access(request)
    actor = request.state.auth_context
    if actor.role == Role.PLATFORM_OWNER:
        return
    authorize = getattr(request.app.state, 'sdk_knowledge_source_authorizer', None)
    if authorize is None:
        raise HTTPException(403, 'sdk_source_access_required')
    try:
        allowed = await run_in_threadpool(authorize, actor, repository)
    except Exception:  # noqa: BLE001 -- external authorization fails closed without leaking internals
        raise HTTPException(503, 'sdk_source_authorization_unavailable') from None
    if allowed is not True:
        raise HTTPException(403, 'sdk_source_access_required')


@contextmanager
def configured_analyzer():
    model_id = os.getenv('SDK_KNOWLEDGE_MODEL_ID', '')
    base = os.getenv('SDK_KNOWLEDGE_MODEL_BASE_URL', '')
    key_file = os.getenv('SDK_KNOWLEDGE_MODEL_KEY_FILE', '')
    if not model_id or not base or not key_file:
        raise HTTPException(503, 'sdk_knowledge_model_not_configured')
    try:
        timeout = float(os.getenv('SDK_KNOWLEDGE_MODEL_TIMEOUT_SECONDS', '120'))
        if not 1 <= timeout <= 600:
            raise ValueError
    except ValueError:
        raise HTTPException(503, 'sdk_knowledge_model_configuration_invalid') from None
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        try:
            model = AnthropicMessagesAdapter.from_secret_file(
                base_url=base, api_key_file=key_file,
                auth_scheme=os.getenv('SDK_KNOWLEDGE_MODEL_AUTH_SCHEME', 'x-api-key'),
                client=client, max_attempts=1,
            )
            analyzer = ChangeAnalyzer(model, model_id=model_id)
        except ValueError:
            raise HTTPException(503, 'sdk_knowledge_model_configuration_invalid') from None
        yield analyzer


async def _operation(method, *args, **kwargs):
    try:
        return await run_in_threadpool(method, *args, **kwargs)
    except KnowledgeUnavailable:
        raise HTTPException(503, 'sdk_knowledge_unavailable') from None
    except SourceObservationError as error:
        raise HTTPException(409, str(error)) from None
    except AnalysisFailure as error:
        raise HTTPException(422, str(error)) from None
    except BrainModelError:
        raise HTTPException(502, 'sdk_knowledge_model_failed') from None
    except ValueError as error:
        if str(error) == 'source_not_configured':
            raise HTTPException(503, 'sdk_knowledge_source_not_configured') from None
        raise HTTPException(422, 'sdk_knowledge_input_invalid') from None


def build_sdk_knowledge_router() -> APIRouter:
    router = APIRouter(prefix='/api/v1/sdk-knowledge', tags=['sdk-knowledge'], route_class=_PrivateRoute)

    @router.get('/search')
    async def search(request: Request, q: str = Query('', max_length=200),
                     repository: str | None = Query(None, max_length=128),
                     path: str | None = Query(None, max_length=512),
                     symbol: str | None = Query(None, max_length=200),
                     device: str | None = Query(None, max_length=200),
                     platform: str | None = Query(None, max_length=200),
                     limit: int = Query(10, ge=1, le=10)):
        await _require_access(request)
        service = _service(request)
        result = await _operation(service.corpus.search, q=q, repository=repository, path=path,
                                  symbol=symbol, device=device, platform=platform, limit=limit)
        await _require_access(request)
        return result

    @router.get('/pages/{page_id}')
    async def page(request: Request, page_id: str):
        await _require_access(request)
        if len(page_id) > 160:
            raise HTTPException(404, 'sdk_knowledge_page_not_found')
        result = await _operation(_service(request).corpus.page, page_id)
        if result is None:
            raise HTTPException(404, 'sdk_knowledge_page_not_found')
        await _require_access(request)
        return result

    @router.post('/changes/inspect')
    async def inspect_change(request: Request, body: ChangeRequest):
        await _require_source_access(request, body.repository)
        result = await _operation(_service(request).inspect_change, **body.model_dump())
        await _require_source_access(request, body.repository)
        return result

    @router.post('/changes/analyze')
    async def analyze_change(request: Request, body: AnalysisRequest):
        await _require_source_access(request, body.repository)
        packet = await _operation(_service(request).inspect_change, **body.model_dump(exclude={'objective'}))
        await _require_source_access(request, body.repository)
        with configured_analyzer() as analyzer:
            result = await _operation(analyzer.analyze, packet, body.objective)
        await _require_source_access(request, body.repository)
        return {'source_packet': packet, 'analysis': result}

    return router
