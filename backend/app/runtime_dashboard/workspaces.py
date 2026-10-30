"""User-bound readonly business adapters; no global business cache."""
import asyncio
import httpx
import inspect
from time import monotonic

from app.control_plane.models import AuthContext, Role
from .budget import ActorWork, SharedWork, SOURCE_SECONDS, remaining, wait_for

from .models import Deployment, Metric, SourceEvidence
from .repository import timestamp


def unavailable(app, now, reason):
    app.entry_path = None
    app.can_use = False
    app.source = SourceEvidence(source_id=app.id,source_kind=app.id,checked_at=now,
        error=reason,permission_scope='business_scope')
    app.business_metrics = {'documents' if app.id == 'sdk' else 'visible_projects': Metric(
        unit='document' if app.id == 'sdk' else 'project',definition='已授权业务范围的资源数量',reason=reason)}


class SdkSummaryReader:
    """Only immutable, nonpersonal catalog numbers are shared across actors."""
    def __init__(self, *, actor_limit=8, actors=None):
        self.catalog_work = SharedWork()
        self.actors = actors or ActorWork(actor_limit)
        self._catalog = None
        self._expires = 0

    def catalog(self, request, deadline):
        from app.sdk_admin.routes import _repository
        if monotonic() < self._expires:
            return self._catalog
        remaining(deadline)
        raw = _repository(request).catalog()
        remaining(deadline)
        # Do not retain document bodies, titles, projects or actor fields.
        result = {'generated_at': raw['generated_at'], 'release_id': raw['release_id'],
            'counts': {name: raw['counts'][name] for name in ('documents', 'knowledge_pages', 'knowledge_navigation')}}
        for value in result['counts'].values():
            if type(value) is not int or value < 0:
                raise ValueError('sdk_catalog_invalid')
        timestamp(result['generated_at'])
        remaining(deadline)
        self._catalog, self._expires = result, monotonic()+30
        return result

    async def allowed(self, request, deadline):
        from app.sdk_admin.routes import _READ_ROLES
        actor = getattr(request.state, 'auth_context', None)
        if not isinstance(actor, AuthContext):
            return False
        if actor.role == Role.PLATFORM_OWNER:
            return True
        service = getattr(request.app.state, 'sdk_service', None)
        if service is None:
            service = getattr(request.app.state, 'sdk_plan_service', None)
        if service is None:
            return False
        from fastapi import HTTPException
        try:
            result = await self.actors.read(('sdk', actor.internal_user_id), lambda: service.me(actor), deadline)
            if inspect.isawaitable(result):
                async with asyncio.timeout(remaining(deadline)):
                    result = await result
        except HTTPException as error:
            if error.status_code == 403:
                return False
            raise
        return isinstance(result, dict) and isinstance(result.get('role'), str) and result['role'] in _READ_ROLES


async def sdk_summary(request, app, now, *, reader=None, deadline=None):
    reader = reader or SdkSummaryReader()
    deadline = deadline if deadline is not None else monotonic()+SOURCE_SECONDS
    granted = False
    try:
        remaining(deadline)
        catalog_job = reader.catalog_work.start(lambda: reader.catalog(request, deadline))
        if not await reader.allowed(request, deadline):
            unavailable(app,now,'business_permission_denied')
            return
        granted = True
        catalog = await wait_for(catalog_job, deadline)
        apply_sdk_catalog(app, catalog, now)
    except Exception:
        # Prior immutable numbers remain usable only after this request's fresh
        # actor check succeeded. A denied/failed access lookup never gets them.
        if granted and reader._catalog is not None:
            apply_sdk_catalog(app, reader._catalog, now, error='sdk_catalog_unavailable')
        else:
            unavailable(app,now,'sdk_catalog_unavailable')


def apply_sdk_catalog(app, catalog, now, *, error=None):
    observed = timestamp(catalog['generated_at'])
    app.can_use = True
    app.source = SourceEvidence(source_id='sdk-catalog', source_kind='sdk',
        status='stale' if error else 'fresh', complete=not error,
        observed_at=observed, published_at=observed, checked_at=now,
        permission_scope='business_scope', error=error)
    app.business_metrics = {name: Metric(value=catalog['counts'][name],
        status='partial' if error else 'observed', reason=error, unit='document',
        definition={'documents':'已发布文档总数','knowledge_pages':'已发布知识正文页数','knowledge_navigation':'知识导航页数'}[name])
        for name in ('documents','knowledge_pages','knowledge_navigation')}
    app.deployment = Deployment(version=catalog['release_id'], observed_at=observed,
        evidence='immutable_catalog_release')


async def manufacturing_summary(request, app, now, *, route_prefix='', deadline=None):
    """Fixed ASGI paths forward the real session; middleware reauthenticates both GETs."""
    base = str(request.base_url)
    headers = {'cookie':request.headers.get('cookie',''), 'host':request.headers['host']}
    transport = httpx.ASGITransport(app=request.app,raise_app_exceptions=False)
    try:
        async with asyncio.timeout(min(3, remaining(deadline)) if deadline is not None else 3):
            async with httpx.AsyncClient(transport=transport,base_url=base,headers=headers,
                    follow_redirects=False,timeout=2,trust_env=False) as client:
                async def read(path):
                    response = await client.get(route_prefix+'/api/v1/manufacturing/workflow/'+path)
                    if response.status_code in {401,403}:
                        raise PermissionError
                    if response.status_code != 200 or len(response.content) > 1_048_576:
                        raise ValueError
                    value = response.json()
                    if not isinstance(value,dict):
                        raise ValueError
                    return value
                access = await read('access')
                if access.get('workflow_available') is not True:
                    unavailable(app,now,'manufacturing_unavailable')
                    return
                if access.get('workflow_access') is not True:
                    raise PermissionError
                data = await read('dashboard')
                count = data.get('project_count')
                if type(count) is not int or count < 0:
                    raise ValueError
                app.business_metrics = {'visible_projects':Metric(value=count,status='observed',unit='project',
                    definition='当前身份经项目授权可见的项目数'),
                    'tasks':Metric(unit='task',definition='当前可见任务数',reason='task_metric_not_supplied')}
                dataset = access.get('dataset')
                if isinstance(dataset,dict):
                    app.dataset = {key: dataset[key] for key in ('mode','label') if isinstance(dataset.get(key),str)}
                app.can_use = True
                app.source = SourceEvidence(source_id='manufacturing-workflow',source_kind='manufacturing',
                    status='fresh',complete=True,observed_at=now,checked_at=now,permission_scope='current_actor_projects')
                if isinstance(access.get('release'),str):
                    app.deployment = Deployment(version=access['release'],observed_at=now,evidence='configured_package_release')
    except PermissionError:
        unavailable(app,now,'business_permission_denied')
    except Exception:
        unavailable(app,now,'manufacturing_summary_unavailable')


async def voc_business_summary(request, app, now, *, deadline=None):
    """Count only authorized pages of existing VOC records; discard every body."""
    import re
    seen_ids, seen_cursors = set(), set()
    count = 0
    scanned = 0
    complete = False
    duplicate = False
    reason = None
    cursor = None
    # Only actual session headers are forwarded. The BFF repeats VOC authorization
    # and signs its own upstream capability; this reader has no signing key.
    headers = {'cookie':request.headers.get('cookie',''), 'host':request.headers['host']}
    transport = httpx.ASGITransport(app=request.app,raise_app_exceptions=False)
    try:
        async with asyncio.timeout(min(3, remaining(deadline)) if deadline is not None else 3):
            async with httpx.AsyncClient(transport=transport,base_url=str(request.base_url),
                    headers=headers,follow_redirects=False,timeout=2,trust_env=False) as client:
                for _ in range(10):
                    params = {'limit':100}
                    if cursor is not None:
                        params['cursor'] = cursor
                    response = await client.get('/api/v1/extensions/voc/admin/vocs',params=params)
                    if response.status_code in {401,403}:
                        raise PermissionError
                    if response.status_code != 200 or len(response.content) > 1_048_576:
                        raise ValueError
                    page = response.json()
                    items = page['items']
                    next_cursor = page['next_cursor']
                    if not isinstance(items,list) or len(items) > 100:
                        raise ValueError
                    for item in items:
                        identity = item.get('voc_no') if isinstance(item,dict) else None
                        if not isinstance(identity,str) or not re.fullmatch(r'VOC-[0-9]{8}-[0-9]{3,}',identity):
                            raise ValueError
                        duplicate = duplicate or identity in seen_ids
                        seen_ids.add(identity)
                    scanned += len(items)
                    count = len(seen_ids)
                    # Do not retain item dictionaries, identities or responses after this call.
                    del items, page, response
                    if next_cursor is None:
                        complete = not duplicate
                        reason = 'voc_duplicate_records' if duplicate else None
                        break
                    if not isinstance(next_cursor,str) or not 1 <= len(next_cursor) <= 2048:
                        raise ValueError
                    if next_cursor in seen_cursors:
                        reason = 'voc_cursor_repeated'
                        break
                    if scanned >= 1000:
                        reason = 'voc_record_limit'
                        break
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
                else:
                    reason = 'voc_page_limit'
    except PermissionError:
        count = None
        reason = 'business_permission_denied'
    except Exception:
        count = count if scanned else None
        reason = 'voc_business_unavailable'
    status = 'observed' if complete else 'partial' if count is not None else 'unavailable'
    app.business_metrics['registered_vocs'] = Metric(value=count,status=status,unit='result',
        definition='当前账号可见的已入库VOC条目数；不代表用户轮次或成功任务',reason=reason)
    app.business_sources['registered_vocs'] = SourceEvidence(source_id='voc-authorized-records',source_kind='voc',
        status='fresh' if complete else 'stale' if count is not None else 'unavailable',
        complete=complete,observed_at=now if count is not None else None,checked_at=now,
        permission_scope='current_actor_voc_management',error=reason)
