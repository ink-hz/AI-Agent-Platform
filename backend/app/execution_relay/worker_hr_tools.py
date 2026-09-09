"""Loopback tool proxy; only the registered run's capability reaches signed HTTP."""
import asyncio
import hashlib
import hmac
import json
from uuid import UUID
from .contracts_v6 import parse_v6_tool_request
from .contracts_v7 import parse_v7_tool_request


def _authorize(store, worker_id, run_id, callback_token, envelope):
    if type(envelope) is not dict or set(envelope)!={'grantId','bearerToken','request'}:
        raise ValueError('HR proxy invalid')
    grant_id=UUID(envelope['grantId']);token=envelope['bearerToken']
    import re
    if not isinstance(token,str) or re.fullmatch('[A-Za-z0-9_-]{43}',token) is None:
        raise PermissionError('HR proxy unauthorized')
    with store._connection() as connection:
        row=connection.execute('select * from execution_worker.v5_callback_runs where run_id=%s',(run_id,)).fetchone()
        if row is None:
            raise RuntimeError('HR callback registration pending')
        if (row['worker_id']==worker_id and row['core_contract_version'] in {'core_chat_collaboration_v6','core_chat_collaboration_v7'}
            and hmac.compare_digest(bytes(row['token_hash']),hashlib.sha256(callback_token.encode()).digest())
            and row['business_grant_id']!=grant_id):
            raise RuntimeError('HR capability renewal pending')
        if (row['worker_id']!=worker_id or row['core_contract_version'] not in {'core_chat_collaboration_v6','core_chat_collaboration_v7'}
            or row['business_grant_id']!=grant_id
            or not hmac.compare_digest(bytes(row['token_hash']),hashlib.sha256(callback_token.encode()).digest())
            or not hmac.compare_digest(bytes(row['business_token_hash']),hashlib.sha256(token.encode()).digest())):
            raise PermissionError('HR proxy unauthorized')
    version=row['core_contract_version'].rsplit('_',1)[1]
    request=(parse_v7_tool_request if version=='v7' else parse_v6_tool_request)(envelope['request'])
    return request,grant_id,token,version


async def proxy(runtime,run_id,callback_token,body,writer):
    status,payload=503,{'status':'error','code':'source_unavailable','retryable':True,'message':'HR 工具暂时不可用'}
    try:
        request,grant_id,token,version=await asyncio.to_thread(_authorize,runtime.store,runtime.worker_id,run_id,callback_token,json.loads(body))
        endpoint={'hr.read_context':'query','hr.submit_result':'results','hr.confirm_standard':'confirmations'}[request.tool]
        raw=request.model_dump_json(by_alias=True).encode()
        response=None
        if request.tool=='hr.read_context' and request.resource_kind=='official_position':
            from .worker_official_verification import read_official
            response=await read_official(runtime,request,raw,grant_id,token,version=version)
        if response is None:
            response=await runtime.cloud.post_hr_tool('/api/v1/execution-worker/hr/'+version+'/'+endpoint,raw,grant_id,token)
        status,payload=response.status_code,response.json()
    except PermissionError:
        status,payload=401,{'status':'error','code':'permission_expired','retryable':False,'message':'业务工具授权失效'}
    except (ValueError,TypeError,KeyError):
        status,payload=400,{'detail':'HR proxy request invalid'}
    except Exception:
        pass  # Stable public failure; never serialize a token or source exception.
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    writer.write((f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n').encode()+raw)
    await writer.drain()
    writer.close()
    await writer.wait_closed()
