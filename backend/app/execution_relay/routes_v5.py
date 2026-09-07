"""Opt-in routes on the existing authenticated Relay lane."""

import json
from uuid import UUID

import psycopg
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from app.agent_brain.direct_command_binding import BindingRejected
from app.agent_brain.turn_attempts import LeaseRejected

from .content_crypto import ContentCryptoError
from .readiness_v5 import record_observation
from .source_v5 import accept_source


def attach_v5_routes(router, authenticated, bindings):
    from .routes import _NO_STORE, _error

    async def invoke(request, operation):
        auth = await authenticated(request)
        if isinstance(auth, JSONResponse):
            return auth
        if "hr-bot" not in auth.identity.allowed_agent_ids:
            return _error(403, "v5 transport forbidden")
        try:
            return await run_in_threadpool(
                operation, auth.identity.worker_id, auth.body
            )
        except PermissionError:
            return _error(401, "v5 transport unauthorized")
        except (BindingRejected, LeaseRejected):
            return _error(409, "v5 transport conflict")
        except (ValueError, UnicodeError):
            return _error(400, "v5 transport invalid")
        except (psycopg.Error, ContentCryptoError):
            return _error(503, "v5 transport unavailable")

    @router.post("/v5/readiness")
    async def readiness(request: Request):
        def execute(worker_id, body):
            if len(body) > 8192:
                raise ValueError
            applied = record_observation(bindings.relay, worker_id, json.loads(body))
            return JSONResponse({"recorded": applied}, headers=_NO_STORE)

        return await invoke(request, execute)

    @router.post("/v5/handoff")
    async def handoff(request: Request):
        def execute(worker_id, body):
            if json.loads(body) != {}:
                raise ValueError
            value = bindings.handoff(worker_id)
            return (
                Response(status_code=204, headers=_NO_STORE)
                if value is None
                else JSONResponse(value, headers=_NO_STORE)
            )

        return await invoke(request, execute)

    @router.post("/v5/runs/{run_id}/acceptance")
    async def acceptance(run_id: UUID, request: Request):
        def execute(worker_id, body):
            acknowledged = bindings.acknowledge_transport(
                worker_id, run_id, json.loads(body)
            )
            return JSONResponse({"acknowledged": acknowledged}, headers=_NO_STORE)

        return await invoke(request, execute)

    @router.post("/v5/runs/{run_id}/events")
    async def events(run_id: UUID, request: Request):
        def execute(worker_id, body):
            ack = accept_source(bindings, worker_id, run_id, body)
            return JSONResponse(
                ack.model_dump(mode="json", by_alias=True),
                status_code=409 if ack.status in {"gap", "conflict"} else 200,
                headers=_NO_STORE,
            )

        return await invoke(request, execute)
