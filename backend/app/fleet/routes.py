from fastapi import APIRouter, Request

from .models import FleetOverview
from .service import FleetReadService


router = APIRouter(prefix="/api/fleet", tags=["fleet"])


def _service(request: Request) -> FleetReadService:
    return request.app.state.fleet_service


@router.get("/overview", response_model=FleetOverview)
async def fleet_overview(request: Request) -> FleetOverview:
    return await _service(request).overview()
