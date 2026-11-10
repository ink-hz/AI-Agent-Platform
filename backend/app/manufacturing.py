"""Load the trusted, versioned manufacturing release without changing sys.path."""

import hashlib
import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from starlette.routing import Match

from .control_plane.models import AuthContext, Role
from .manufacturing_paths import (
    MANUFACTURING_PREFIX,
    MANUFACTURING_ROUTES,
    PRODUCTION_TOOLS_ROUTES,
    WORKFLOW_ROUTES,
)

_PRIVATE = {"Cache-Control": "private, no-store"}


def authorize_manufacturing(request: Request, write: bool = False) -> dict:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required", headers=_PRIVATE)
    if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
        raise HTTPException(403, "platform management required", headers=_PRIVATE)
    if write and context.hard_stale_read_only:
        raise HTTPException(503, "hard_stale_read_only", headers=_PRIVATE)
    actor = str(context.internal_user_id)
    return {
        "user_id": actor,
        "display_name": actor,
        "read_only": context.hard_stale_read_only,
    }


def authorize_workflow_identity(request: Request, write: bool = False) -> dict:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required", headers=_PRIVATE)
    if write and context.hard_stale_read_only:
        raise HTTPException(503, "hard_stale_read_only", headers=_PRIVATE)
    return {
        "user_id": str(context.internal_user_id),
        "display_name": str(context.internal_user_id),
        "read_only": context.hard_stale_read_only
        or context.role == Role.MANAGEMENT_VIEWER,
        "configuration_admin": context.role
        in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN},
    }


def _workflow_unavailable(
    *, configured_failure=False, legacy_available=False, release="development"
):
    router = APIRouter(prefix=MANUFACTURING_PREFIX + "/workflow")

    def unavailable(request: Request):
        authorize_workflow_identity(
            request, write=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        return JSONResponse(
            {"detail": "Workflow unavailable"}, status_code=503, headers=_PRIVATE
        )

    def access(request: Request):
        identity = authorize_workflow_identity(request)
        if configured_failure:
            return JSONResponse(
                {"detail": "Workflow unavailable"}, status_code=503, headers=_PRIVATE
            )
        legacy = identity["configuration_admin"]
        return JSONResponse(
            {
                "allowed": legacy,
                "available": legacy_available,
                "workflow_available": False,
                "workflow_access": False,
                "legacy_access": legacy,
                "configuration_admin": legacy,
                "can_edit": not identity["read_only"],
                "dataset": None,
                "project_ids": [],
                "release": release,
                "protocol": 1,
                "user": {
                    "id": identity["user_id"],
                    "display_name": identity["display_name"],
                },
            },
            headers=_PRIVATE,
        )

    for method, path in sorted(WORKFLOW_ROUTES):
        router.add_api_route(
            path, access if path == "/access" else unavailable, methods=[method]
        )
    return router


def _unavailable_router() -> APIRouter:
    router = APIRouter(prefix=MANUFACTURING_PREFIX)

    def unavailable(request: Request):
        authorize_manufacturing(
            request, write=request.method not in {"GET", "HEAD", "OPTIONS"}
        )
        return JSONResponse(
            {"detail": "Manufacturing unavailable"}, status_code=503, headers=_PRIVATE
        )

    def access(request: Request):
        authorize_manufacturing(request)
        return JSONResponse(
            {"allowed": True, "available": False, "can_edit": False}, headers=_PRIVATE
        )

    for method, path in sorted(MANUFACTURING_ROUTES):
        router.add_api_route(
            path, access if path == "/access" else unavailable, methods=[method]
        )

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
        raise HTTPException(404, "Manufacturing resource not found", headers=_PRIVATE)

    for path in ("", "/{unmatched_path:path}"):
        router.add_api_route(
            path,
            unmatched,
            methods=["GET"],
            include_in_schema=False,
            route_class_override=PrivateFallbackRoute,
        )
    return router


def _native_production_options(module, paths, dataset):
    """Optional connector errors must never disable the controlled workflow."""
    parameter = inspect.signature(module.build_workflow_router).parameters.get("production_tools")
    compatible = (
        parameter is not None
        and parameter.kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        and callable(getattr(module, "build_native_config", None))
        and callable(getattr(module, "validate_snapshot_path", None))
    )
    if not compatible:
        logging.getLogger(__name__).warning("PRODUCTION_TOOLS_RELEASE_INCOMPATIBLE")
        return False, {}
    options = {"production_tools": None}
    config_path = os.environ.get("PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_CONFIG_PATH", "")
    snapshot_path = os.environ.get("PLATFORM_MANUFACTURING_PRODUCTION_TOOLS_SNAPSHOT_PATH", "")
    if config_path or snapshot_path:
        try:
            if not config_path or not snapshot_path or not Path(config_path).is_absolute():
                raise ValueError("invalid native settings")
            module.validate_snapshot_path(snapshot_path, (*map(str, paths), config_path))
            options["production_tools"] = module.build_native_config(config_path, snapshot_path, dataset)
        except Exception:  # noqa: BLE001 - never log private settings or source errors
            logging.getLogger(__name__).warning("PRODUCTION_TOOLS_CONFIG_INVALID")
    return True, options


def build_manufacturing_router(*, resolve_workflow_subject=None, resolve_workflow_profiles=None, workflow_business_clock: Callable[[], datetime] | None = None) -> APIRouter:
    """Load legacy and workflow independently; reserve workflow ahead of legacy catchall."""
    namespace = None
    root_text = os.environ.get("PLATFORM_MANUFACTURING_ROOT", "")
    database = os.environ.get("PLATFORM_MANUFACTURING_DATABASE_PATH", "")
    release = os.environ.get("PLATFORM_MANUFACTURING_RELEASE", "development")
    names = (
        "PLATFORM_MANUFACTURING_WORKFLOW_DATABASE_PATH",
        "PLATFORM_MANUFACTURING_AUTHORITY_DATABASE_PATH",
        "PLATFORM_MANUFACTURING_DATASET_ID",
        "PLATFORM_MANUFACTURING_DATASET_MODE",
        "PLATFORM_MANUFACTURING_DATASET_LABEL",
    )
    values = [os.environ.get(name, "") for name in names]
    legacy = _unavailable_router()
    module = None
    assets = None
    try:
        if (
            root_text
            and Path(root_text).is_absolute()
            and database
            and Path(database).is_absolute()
        ):
            root = Path(root_text).resolve(strict=True)
            package = (root / "src/manufacturing_platform").resolve(strict=True)
            assets = (root / "webui/dist").resolve(strict=True)
            if (
                not package.is_relative_to(root)
                or not assets.is_relative_to(root)
                or not all(
                    (assets / n).is_file()
                    for n in ("manufacturing.js", "manufacturing.css")
                )
            ):
                raise ValueError("invalid release")
            namespace = (
                "_platform_manufacturing_"
                + hashlib.sha256(str(root).encode()).hexdigest()[:24]
            )
            module = sys.modules.get(namespace)
            if module is None:
                spec = importlib.util.spec_from_file_location(
                    namespace,
                    package / "__init__.py",
                    submodule_search_locations=[str(package)],
                )
                if spec is None or spec.loader is None:
                    raise ValueError("invalid release")
                module = importlib.util.module_from_spec(spec)
                sys.modules[namespace] = module
                spec.loader.exec_module(module)
            legacy = module.build_router(
                database_path=database,
                assets_dir=str(assets),
                authorize=authorize_manufacturing,
                release=release,
            )
            if not isinstance(legacy, APIRouter):
                raise TypeError("invalid manufacturing router")
    except Exception:  # noqa: BLE001 - an optional release must not stop the platform
        if namespace:
            for name in tuple(sys.modules):
                if name == namespace or name.startswith(namespace + "."):
                    sys.modules.pop(name, None)
        module = None
        legacy = _unavailable_router()
    available = module is not None
    native_compatible = False
    workflow = _workflow_unavailable(
        configured_failure=any(values) and not all(values),
        legacy_available=available,
        release=release,
    )
    if (
        all(values)
        and module is not None
        and getattr(module, "WORKFLOW_API_VERSION", None) == 1
    ):
        try:
            domain, authority, dataset_id, mode, label = values
            paths = [Path(domain), Path(authority), Path(database)]
            if (
                resolve_workflow_subject is None
                or any(not p.is_absolute() for p in paths)
                or len({str(p.resolve()) for p in paths}) != 3
            ):
                raise ValueError("invalid workflow configuration")
            for i in range(len(paths)):
                for j in range(i):
                    if (
                        paths[i].exists()
                        and paths[j].exists()
                        and paths[i].samefile(paths[j])
                    ):
                        raise ValueError("invalid workflow configuration")
            dataset = {"id": dataset_id, "mode": mode, "label": label}
            native_compatible, profile_options = _native_production_options(module, paths, dataset)
            profile_parameter = inspect.signature(module.build_workflow_router).parameters.get("resolve_subject_profiles")
            if profile_parameter is not None and profile_parameter.kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
                profile_options["resolve_subject_profiles"] = resolve_workflow_profiles
            if workflow_business_clock is not None:
                clock_parameter = inspect.signature(module.build_workflow_router).parameters.get("business_clock")
                if clock_parameter is None or clock_parameter.kind not in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
                    raise ValueError("workflow release does not support trusted business clock")
                profile_options["business_clock"] = workflow_business_clock
            workflow = module.build_workflow_router(
                database_path=domain,
                authority_database_path=authority,
                assets_dir=str(assets),
                authenticate=authorize_workflow_identity,
                resolve_subject=resolve_workflow_subject,
                release=release,
                dataset={"id": dataset_id, "mode": mode, "label": label},
                **profile_options,
            )
            if not isinstance(workflow, APIRouter):
                raise TypeError("invalid workflow router")
        except Exception:  # noqa: BLE001 - isolate a broken optional workflow release
            workflow = _workflow_unavailable(
                configured_failure=True, legacy_available=available, release=release
            )
    elif all(values) and module is None:
        workflow = _workflow_unavailable(
            configured_failure=True, legacy_available=available, release=release
        )
    combined = APIRouter()
    # Older/partially upgraded releases may omit routes despite accepting the keyword.
    released = {(method, route.path) for route in workflow.routes
                if isinstance(route, APIRoute) for method in route.methods}
    reserved = {MANUFACTURING_PREFIX + "/workflow" + path
                for method, path in PRODUCTION_TOOLS_ROUTES
                if not native_compatible or (method, MANUFACTURING_PREFIX + "/workflow" + path) not in released}
    native_unavailable = _workflow_unavailable()
    native_unavailable.routes = [route for route in native_unavailable.routes if route.path in reserved]
    combined.include_router(native_unavailable)
    combined.include_router(workflow)
    combined.include_router(legacy)
    return combined
