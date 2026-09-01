from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.control_plane.models import Role


def _management_context(request: Request):
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if context.role not in {Role.PLATFORM_OWNER, Role.PLATFORM_ADMIN}:
        raise HTTPException(status_code=403, detail="management role required")
    return context


router = APIRouter(
    prefix="/api/admin/fae/reports",
    tags=["fae-reports"],
    dependencies=[Depends(_management_context)],
)


@router.get("")
def reports(request: Request, status: str | None = None):
    return request.app.state.fae_report_service.list(status=status)


@router.get("/latest")
def latest(request: Request):
    result = request.app.state.fae_report_service.latest()
    if result is None:
        raise HTTPException(status_code=404, detail="fae report not found")
    return result


@router.get("/{report_id}")
def detail(
    report_id: str,
    request: Request,
    version: int | None = Query(default=None, ge=1),
):
    result = request.app.state.fae_report_service.detail(report_id, version)
    if result is None:
        raise HTTPException(status_code=404, detail="fae report not found")
    return result
