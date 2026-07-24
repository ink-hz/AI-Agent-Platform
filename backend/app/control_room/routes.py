from fastapi import APIRouter, HTTPException, Request

from .models import AgentRuntimeView


router = APIRouter(prefix="/api/agents", tags=["control-room"])


@router.get("/{agent_id}/runtime", response_model=AgentRuntimeView)
async def agent_runtime(agent_id: str, request: Request):
    result = await request.app.state.control_room_service.get_runtime(agent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return result
