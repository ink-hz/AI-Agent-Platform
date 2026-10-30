"""Manager-only metadata views; use grants are always evaluated independently."""
import asyncio
from time import monotonic

from .budget import ActorWork, REQUEST_SECONDS, SOURCE_SECONDS
from .workspaces import SdkSummaryReader
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

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


def build_dashboard_router(service, authorization, *, health=None, workspace_summaries=True, route_prefix='', actor_limit=8):
    router = APIRouter(prefix='/api/dashboard',tags=['runtime-dashboard'],route_class=PrivateRoute)
    actor_work = ActorWork(actor_limit)
    sdk_reader = SdkSummaryReader(actors=actor_work)
    async def permitted(actor, deadline):
        if authorization is None:
            raise HTTPException(503,'agent_authorization_unavailable')
        try:
            cards = await actor_work.read(('catalog', actor.internal_user_id),
                lambda: authorization.permitted_catalog_for_user_id(actor.internal_user_id), deadline)
            return {card.agent_id for card in cards}
        except Exception:
            raise HTTPException(503,'agent_authorization_unavailable') from None

    @router.get('/catalog')
    async def catalog(request:Request):
        actor = require_manager(request)
        allowed = await permitted(actor, monotonic()+REQUEST_SECONDS)
        return {'agents':[{**card.model_dump(mode='json'),'can_use':card.agent_id in allowed,
            'dispatchable':card.dispatchable,'web_entry':web_entry_for(card.agent_id)} for card in service.catalog]}

    @router.get('/overview',response_model=DashboardOverview)
    async def overview(request:Request,days:int=Query(7, json_schema_extra={'enum':[7,30]})):
        if days not in {7,30}:
            raise HTTPException(422,'dashboard_query_invalid')
        actor = require_manager(request)
        deadline = monotonic()+REQUEST_SECONDS
        source_deadline = min(deadline-.2, monotonic()+SOURCE_SECONDS)
        now = service.clock()
        # Private scratch objects permit business reads to run independently of
        # the global numeric projection. No task may mutate the final response.
        scratch = service.overview(days, now=now, sources={})
        by_id = {app.id: app for app in scratch.applications}
        jobs = [asyncio.create_task(permitted(actor, source_deadline)),
            asyncio.create_task(service.sources(now, source_deadline)),
            asyncio.create_task(health.read(now, deadline=source_deadline) if health else asyncio.sleep(0, result={}))]
        if workspace_summaries:
            jobs.extend([asyncio.create_task(sdk_summary(request, by_id['sdk'], now,
                    reader=sdk_reader, deadline=source_deadline)),
                asyncio.create_task(manufacturing_summary(request, by_id['manufacturing'], now,
                    route_prefix=route_prefix, deadline=source_deadline)),
                asyncio.create_task(voc_business_summary(request, by_id['voc'], now, deadline=source_deadline))])
        try:
            values = await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
        allowed, sources, observations = values[:3]
        result = service.overview(days, now=now, sources=sources, health=observations)
        if workspace_summaries:
            for index, app in enumerate(result.applications):
                if app.kind == 'workspace':
                    result.applications[index] = by_id[app.id]
                elif app.id == 'voc':
                    app.business_metrics.update(by_id['voc'].business_metrics)
                    app.business_sources.update(by_id['voc'].business_sources)
        for app in result.applications:
            if app.kind == 'agent':
                app.can_use = app.id in allowed
            if not app.can_use:
                app.entry_path = None
        update_runtime_summary(result)
        return result
    return router
