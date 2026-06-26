from fastapi import APIRouter, HTTPException, Request

from .repository import Repository

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _repo(request: Request) -> Repository:
    return request.app.state.repo


@router.get("")
def list_agents(request: Request) -> list[dict]:
    return [agent.public_dict() for agent in _repo(request).list_agents()]


@router.get("/{agent_id}")
def get_agent(agent_id: str, request: Request) -> dict:
    agent = _repo(request).get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    return agent.public_dict()
