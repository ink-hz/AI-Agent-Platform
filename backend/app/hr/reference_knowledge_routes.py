"""Human browsing of the cloud copy; the Agent reads its local release."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from .reference_knowledge import HrKnowledgeError
from .routes import HrPositionRoute, _auth_context


def build_hr_knowledge_router(repository, agent_use_authorization) -> APIRouter:
    router = APIRouter(tags=["hr-knowledge"], route_class=HrPositionRoute)

    async def require_access(request):
        context = _auth_context(request)
        try:
            decision = await asyncio.to_thread(
                agent_use_authorization.decide_for_user_id,
                context.internal_user_id,
                "hr-bot",
            )
        except AgentUseAuthorizationUnavailable:
            raise HTTPException(503, "HR Agent authorization unavailable") from None
        if not getattr(decision, "allowed", False):
            raise HTTPException(403, "HR Agent use denied")
        if repository is None:
            raise HTTPException(503, "HR knowledge unavailable")

    @router.get("/api/hr/knowledge")
    async def index(request: Request, source_commit: str | None = None):
        await require_access(request)
        try:
            return await asyncio.to_thread(repository.index, source_commit)
        except HrKnowledgeError:
            raise HTTPException(503, "HR knowledge release unavailable") from None

    @router.get("/api/hr/knowledge/{source_commit}/{resource_id}")
    async def article(request: Request, source_commit: str, resource_id: str):
        await require_access(request)
        try:
            return await asyncio.to_thread(
                repository.article, source_commit, resource_id
            )
        except HrKnowledgeError:
            raise HTTPException(404, "HR knowledge resource unavailable") from None

    return router
