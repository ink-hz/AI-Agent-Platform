"""User-bound readonly business adapters; no global business cache."""
import asyncio
import httpx

from .models import Deployment, Metric, SourceEvidence
from .repository import timestamp


def unavailable(app, now, reason):
    app.entry_path = None
    app.can_use = False
    app.source = SourceEvidence(source_id=app.id,source_kind=app.id,checked_at=now,
        error=reason,permission_scope='business_scope')
    app.business_metrics = {'documents' if app.id == 'sdk' else 'visible_projects': Metric(
        unit='document' if app.id == 'sdk' else 'project',definition='已授权业务范围的资源数量',reason=reason)}


async def sdk_summary(request, app, now):
    from app.sdk_admin.routes import _allowed, _read
    try:
        if not await _allowed(request):
            unavailable(app,now,'business_permission_denied')
            return
        catalog = await _read(request,'catalog')
        observed = timestamp(catalog['generated_at'])
        metrics = {}
        for name in ('documents','knowledge_pages','knowledge_navigation'):
            value = catalog['counts'][name]
            if type(value) is not int or value < 0:
                raise ValueError
            metrics[name] = Metric(value=value,status='observed',unit='document',
                definition={'documents':'已发布文档总数','knowledge_pages':'已发布知识正文页数','knowledge_navigation':'知识导航页数'}[name])
        app.can_use = True
        app.source = SourceEvidence(source_id='sdk-catalog',source_kind='sdk',status='fresh',complete=True,
            observed_at=observed,published_at=observed,checked_at=now,permission_scope='business_scope')
        app.business_metrics = metrics
        app.deployment = Deployment(version=catalog['release_id'],observed_at=observed,evidence='immutable_catalog_release')
    except Exception:
        unavailable(app,now,'sdk_catalog_unavailable')


async def manufacturing_summary(request, app, now, *, route_prefix=''):
    """Fixed ASGI paths forward the real session; middleware reauthenticates both GETs."""
    base = str(request.base_url)
    headers = {'cookie':request.headers.get('cookie',''), 'host':request.headers['host']}
    transport = httpx.ASGITransport(app=request.app,raise_app_exceptions=False)
    try:
        async with asyncio.timeout(3):
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


async def voc_business_summary(request, app, now):
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
        async with asyncio.timeout(3):
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
