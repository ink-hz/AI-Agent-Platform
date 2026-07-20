from fastapi import APIRouter, Request

from .monitor import ClusterMonitor


router = APIRouter(prefix="/api/cluster", tags=["cluster"])


def _monitor(request: Request) -> ClusterMonitor:
    return request.app.state.cluster_monitor


@router.get("/status")
def cluster_status(request: Request) -> dict:
    return _monitor(request).snapshot().model_dump()
