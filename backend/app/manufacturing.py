"""Load the trusted, versioned manufacturing release without changing sys.path."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.responses import JSONResponse

from .control_plane.models import AuthContext, Role
from .manufacturing_paths import MANUFACTURING_PREFIX, MANUFACTURING_ROUTES

_PRIVATE = {'Cache-Control': 'private, no-store'}


def authorize_manufacturing(request: Request, write: bool = False) -> dict:
    context = getattr(request.state, 'auth_context', None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, 'authentication required', headers=_PRIVATE)
    if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
        raise HTTPException(403, 'platform management required', headers=_PRIVATE)
    if write and context.hard_stale_read_only:
        raise HTTPException(503, 'hard_stale_read_only', headers=_PRIVATE)
    actor = str(context.internal_user_id)
    return {'user_id': actor, 'display_name': actor, 'read_only': context.hard_stale_read_only}


def _unavailable_router() -> APIRouter:
    router = APIRouter(prefix=MANUFACTURING_PREFIX)

    def unavailable(request: Request):
        authorize_manufacturing(request, write=request.method not in {'GET', 'HEAD', 'OPTIONS'})
        return JSONResponse({'detail': 'Manufacturing unavailable'}, status_code=503, headers=_PRIVATE)

    def access(request: Request):
        authorize_manufacturing(request)
        return JSONResponse({'allowed': True, 'available': False, 'can_edit': False}, headers=_PRIVATE)

    for method, path in sorted(MANUFACTURING_ROUTES):
        router.add_api_route(path, access if path == '/access' else unavailable, methods=[method])
    # Reserve the namespace even when identity middleware is disabled: unknown
    # paths/methods cannot fall through to the platform's generic SPA shell.
    class PrivateFallbackRoute(APIRoute):
        def matches(self, scope):
            match, child_scope = super().matches(scope)
            return (Match.FULL if match == Match.PARTIAL else match), child_scope

        async def handle(self, scope, receive, send):
            await self.app(scope, receive, send)

    def unmatched(request: Request):
        authorize_manufacturing(request)
        raise HTTPException(404, 'Manufacturing resource not found', headers=_PRIVATE)

    for path in ('', '/{unmatched_path:path}'):
        router.add_api_route(path, unmatched, methods=['GET'], include_in_schema=False, route_class_override=PrivateFallbackRoute)
    return router


def build_manufacturing_router() -> APIRouter:
    """Configuration failures disable only this feature; never import arbitrary paths.

    Each resolved release has a private module namespace, so importing a globally
    installed package or another release cannot substitute the configured source.
    No startup database writes and no process-wide PYTHONPATH/sys.path mutation.
    """
    namespace = None
    try:
        configured_root = os.environ.get('PLATFORM_MANUFACTURING_ROOT', '')
        database = os.environ.get('PLATFORM_MANUFACTURING_DATABASE_PATH', '')
        if not configured_root or not Path(configured_root).is_absolute() or not database or not Path(database).is_absolute():
            return _unavailable_router()
        root = Path(configured_root).resolve(strict=True)
        package = (root / 'src/manufacturing_platform').resolve(strict=True)
        assets = (root / 'webui/dist').resolve(strict=True)
        if not package.is_relative_to(root) or not assets.is_relative_to(root):
            return _unavailable_router()
        if not all((assets / name).is_file() for name in ('manufacturing.js', 'manufacturing.css')):
            return _unavailable_router()
        namespace = '_platform_manufacturing_' + hashlib.sha256(str(root).encode()).hexdigest()[:24]
        # Cache only immutable trusted release source, never user identity or data.
        module = sys.modules.get(namespace)
        if module is None:
            spec = importlib.util.spec_from_file_location(namespace, package / '__init__.py', submodule_search_locations=[str(package)])
            if spec is None or spec.loader is None:
                return _unavailable_router()
            module = importlib.util.module_from_spec(spec)
            sys.modules[namespace] = module
            spec.loader.exec_module(module)
        router = module.build_router(database_path=database, assets_dir=str(assets), authorize=authorize_manufacturing,
            release=os.environ.get('PLATFORM_MANUFACTURING_RELEASE', 'development'))
        if not isinstance(router, APIRouter):
            raise TypeError('invalid manufacturing router')
        return router
    except Exception:
        # Bad optional releases cannot prevent the unrelated platform starting.
        if namespace:
            for name in tuple(sys.modules):
                if name == namespace or name.startswith(namespace + '.'):
                    sys.modules.pop(name, None)
        return _unavailable_router()
