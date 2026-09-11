"""Fixed argv registry lookup outside database locks; no model-provided executable."""
import asyncio
import json
import os
import re
from pathlib import Path

async def read_official(runtime,request,raw,grant_id,token,*,version="v6"):
    base='/api/v1/execution-worker/hr/'+version+'/'
    source=await runtime.cloud.post_hr_tool(base+'official-source',raw,grant_id,token)
    if source.status_code!=200:
        return source
    selected=source.json()
    job=selected.get('officialJobId')
    if not selected.get('verificationRequired') or not isinstance(job,str) or re.fullmatch(r'J[0-9]{4,12}|JOBAD:[0-9]{1,20}',job) is None:
        return None
    async def unavailable():
        body={'request':request.model_dump(mode='json',by_alias=True),'observation':{'verification':'unavailable'}}
        return await runtime.cloud.post_hr_tool(base+'official-verifications',json.dumps(body,ensure_ascii=False,separators=(',',':')).encode(),grant_id,token)
    cli=Path(os.environ.get('PLATFORM_HR_JD_CLI',''))
    state=Path(os.environ.get('PLATFORM_HR_RUNTIME_STATE_DIR',''))
    node=Path(os.environ.get('PLATFORM_HR_NODE_EXECUTABLE',''))
    if not all(path.is_absolute() for path in (cli,state,node)) or not cli.is_file() or not state.is_dir() or not node.is_file():
        return await unavailable()
    child=None
    try:
        child=await asyncio.create_subprocess_exec(str(node),str(cli),'verify-job',job,'--json',
            env={**os.environ,'METABOT_RUNTIME_STATE_DIR':str(state)},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        async with asyncio.timeout(62):
            # Bounded pipe reading, including historical CLI versions we do not forward.
            chunks=[];size=0
            while chunk:=await child.stdout.read(65536):
                size+=len(chunk)
                if size>1048576: raise ValueError('registry reply too large')
                chunks.append(chunk)
            await child.wait()
        value=json.loads(b''.join(chunks))
        if value.get('command')!='verify-job' or value.get('verification') not in {'current','failed_using_last_valid'}:
            return await unavailable()
        observation={key:value[key] for key in ('job','registryVersion','jobContentHash','lastSuccessfulSyncAt','verification','health')}
        return await runtime.cloud.post_hr_tool(base+'official-verifications',json.dumps({'request':request.model_dump(mode='json',by_alias=True),'observation':observation},ensure_ascii=False,separators=(',',':')).encode(),grant_id,token)
    except (TimeoutError,ValueError,KeyError,OSError):
        return await unavailable()
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
