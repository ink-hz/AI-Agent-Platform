"""Manager-only metadata views; use grants are always evaluated independently."""
import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.agent_catalog.web_entries import web_entry_for
from app.control_plane.models import AuthContext, Role
from .models import DashboardOverview
from .service import update_runtime_summary
from .workspaces import sdk_summary, manufacturing_summary, voc_business_summary

HEADERS = {'Cache-Control':'private, no-store','Pragma':'no-cache'}


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def private(request):
            try:
                response = await handler(request)
            except HTTPException as error:
                response = JSONResponse({'detail':error.detail},status_code=error.status_code)
            except RequestValidationError:
                response = JSONResponse({'detail':'dashboard_query_invalid'},status_code=422)
            response.headers.update(HEADERS)
            return response
        return private


def require_manager(request):
    actor = getattr(request.state,'auth_context',None)
    if not isinstance(actor,AuthContext):
        raise HTTPException(401,'authentication_required')
    if actor.role not in {Role.PLATFORM_OWNER,Role.PLATFORM_ADMIN}:
        raise HTTPException(403,'platform_management_required')
    return actor


def build_dashboard_router(service, authorization, *, health=None, workspace_summaries=True, route_prefix=''):
    router = APIRouter(prefix='/api/dashboard',tags=['runtime-dashboard'],route_class=PrivateRoute)
    async def permitted(actor):
        if authorization is None:
            raise HTTPException(503,'agent_authorization_unavailable')
        try:
            cards = await asyncio.to_thread(authorization.permitted_catalog_for_user_id,actor.internal_user_id)
            return {card.agent_id for card in cards}
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(503,'agent_authorization_unavailable') from None

    @router.get('/catalog')
    async def catalog(request:Request):
        actor = require_manager(request)
        allowed = await permitted(actor)
        return {'agents':[{**card.model_dump(mode='json'),'can_use':card.agent_id in allowed,
            'dispatchable':card.dispatchable,'web_entry':web_entry_for(card.agent_id)} for card in service.catalog]}

    @router.get('/overview',response_model=DashboardOverview)
    async def overview(request:Request,days:int=Query(7, json_schema_extra={'enum':[7,30]})):
        if days not in {7,30}:
            raise HTTPException(422,'dashboard_query_invalid')
        actor = require_manager(request)
        allowed = await permitted(actor)
        now = service.clock()
        observations = await health.read(now) if health else {}
        result = await asyncio.to_thread(service.overview,days,now=now,health=observations)
        workspaces = {a.id:a for a in result.applications if a.kind == 'workspace'}
        if workspace_summaries:
            await asyncio.gather(sdk_summary(request,workspaces['sdk'],now),
                manufacturing_summary(request,workspaces['manufacturing'],now,route_prefix=route_prefix),
                voc_business_summary(request,next(a for a in result.applications if a.id == 'voc'),now))
        for app in result.applications:
            if app.kind == 'agent':
                app.can_use = app.id in allowed
            if not app.can_use:
                app.entry_path = None
        update_runtime_summary(result)
        return result
    return router
