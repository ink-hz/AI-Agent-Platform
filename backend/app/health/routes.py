from fastapi import APIRouter, HTTPException, Request

from .poller import HealthCache

# This router must be included before registry.routes so /api/agents/health
# resolves here instead of registry's dynamic /api/agents/{agent_id}.
router = APIRouter(prefix="/api/agents", tags=["health"])


def _cache(request: Request) -> HealthCache:
    return request.app.state.health_cache


@router.get("/health")
def batch_health(request: Request) -> list[dict]:
    return [status.model_dump() for status in _cache(request).all()]


@router.get("/{agent_id}/health")
def agent_health(agent_id: str, request: Request) -> dict:
    status = _cache(request).get(agent_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return status.model_dump()
