from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.control_plane.models import AuthContext


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _auth_context(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if not isinstance(context, AuthContext):
        raise HTTPException(401, "authentication required", headers=_NO_STORE)
    return context


def build_agent_catalog_router(agent_use_authorization) -> APIRouter:
    if not hasattr(agent_use_authorization, "permitted_catalog_for_user_id"):
        raise ValueError("Agent Catalog authorization required")
    router = APIRouter(tags=["agent-catalog"])

    def permitted(owner):
        try:
            return tuple(
                agent_use_authorization.permitted_catalog_for_user_id(owner)
            )
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(
                503, "Agent catalog unavailable", headers=_NO_STORE
            ) from None

    @router.get("/api/v1/catalog/agents")
    async def catalog(request: Request, response: Response):
        context = _auth_context(request)
        cards = await asyncio.to_thread(permitted, context.internal_user_id)
        response.headers.update(_NO_STORE)
        return {
            "agents": [
                {**card.model_dump(mode="json"), "dispatchable": card.dispatchable}
                for card in cards
            ]
        }

    return router
