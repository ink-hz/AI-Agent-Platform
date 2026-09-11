"""Validate observations from the authenticated worker's fixed registry CLI."""
import json
import re
from datetime import datetime,timezone,timedelta
from .importers import OfficialJob, _instant


def validate_observation(value,job_id):
    if type(value) is not dict or set(value)!={'job','registryVersion','jobContentHash','lastSuccessfulSyncAt','verification','health'}:
        raise ValueError('official verification invalid')
    if len(json.dumps(value,ensure_ascii=False).encode())>262144:
        raise ValueError('official verification too large')
    version=value['registryVersion']
    if not isinstance(version,str) or not re.fullmatch('[A-Za-z0-9._:-]{1,256}',version):
        raise ValueError('official version invalid')
    job=OfficialJob.parse({**value['job'],'contentHash':value['jobContentHash']})
    if job.canonical_id!=job_id:
        raise ValueError('official identity mismatch')
    now=datetime.now(timezone.utc)
    synced=_instant(value['lastSuccessfulSyncAt'],'registry sync')
    if synced>now+timedelta(minutes=5) or value['verification'] not in {'current','failed_using_last_valid'}:
        raise ValueError('official verification invalid')
    health=value['health']
    if type(health) is not dict or health.get('status') not in {'healthy','current','stale','degraded','unavailable'}:
        raise ValueError('official health invalid')
    warnings=health.get('warnings',[])
    if type(warnings) is not list or len(warnings)>32 or any(not isinstance(x,str) or len(x)>256 for x in warnings):
        raise ValueError('official warnings invalid')
    current=(value['verification']=='current' and now-synced<=timedelta(hours=24)
        and job.status in {'active','stale'} and health['status'] not in {'degraded','unavailable'})
    return {**value,'verification':'current' if current else 'degraded',
        'health':{'status':health['status'],'warnings':warnings}}
