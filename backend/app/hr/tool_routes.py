"""HR tools share the signed worker transport; bearer scope is checked separately."""
import json
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from app.execution_relay.contracts_v6 import parse_v6_tool_request, V6ContractError
from app.execution_relay.content_crypto import ContentCryptoError
from .tool_service import HrToolError


def attach_hr_tool_routes(router, authenticated, service):
    from app.execution_relay.routes import _NO_STORE, _error

    async def invoke(request, expected_tool):
        auth = await authenticated(request)
        if isinstance(auth, JSONResponse):
            return auth
        if 'hr-bot' not in auth.identity.allowed_agent_ids:
            return _error(403, 'HR tool forbidden')
        try:
            # Bearer is never part of the model's business arguments or source receipts.
            grant_id = UUID(request.headers.get('X-Hr-Tool-Grant', ''))
            authorization = request.headers.get('Authorization', '')
            if not authorization.startswith('Bearer '):
                return _error(401, 'HR tool unauthorized')
            token = authorization.removeprefix('Bearer ')
            value = parse_v6_tool_request(json.loads(auth.body))
            if value.tool != expected_tool:
                return _error(400, 'HR tool route mismatch')
            reply = await run_in_threadpool(service.execute, auth.identity.worker_id, grant_id, token, value)
            return JSONResponse(reply, headers=_NO_STORE)
        except (V6ContractError, ValueError):
            return _error(400, 'HR tool request invalid')
        except HrToolError as error:
            status = 401 if error.code=='permission_expired' else 409
            return JSONResponse({'status':'error','code':error.code,'retryable':error.retryable,
                'message':str(error)}, status_code=status, headers=_NO_STORE)
        except (psycopg.Error, ContentCryptoError):
            return _error(503, 'HR tool temporarily unavailable')

    @router.post('/hr/v6/query')
    async def query(request: Request):
        return await invoke(request, 'hr.read_context')

    @router.post('/hr/v6/results')
    async def result(request: Request):
        return await invoke(request, 'hr.submit_result')

    @router.post('/hr/v6/confirmations')
    async def confirmation(request: Request):
        return await invoke(request, 'hr.confirm_standard')


def build_hr_result_router(service, require_hr_access):
    router = APIRouter()

    @router.get('/api/v1/hr/results/{result_id}')
    def result(result_id: UUID, owner_id: UUID=Depends(require_hr_access)):
        try:
            return JSONResponse(service.result(owner_id, result_id), headers={'Cache-Control':'no-store'})
        except HrToolError:
            raise HTTPException(404, 'HR result unavailable') from None
        except (psycopg.Error, ContentCryptoError):
            raise HTTPException(503, 'HR result temporarily unavailable') from None
    return router
