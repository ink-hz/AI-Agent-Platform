"""Same-origin SDK requirement routes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.control_plane.models import AuthContext


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequirementBody(StrictBody):
    request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=4000)
    device_platform: str = Field(min_length=1, max_length=500)
    acceptance: str = Field(min_length=1, max_length=4000)


class TaskBody(StrictBody):
    request_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class ReviewBody(StrictBody):
    request_id: UUID
    decision: Literal["approved", "rejected", "needs_changes"]
    reason: str = Field(min_length=1, max_length=2000)


class MemberBody(StrictBody):
    request_id: UUID
    internal_user_id: UUID
    role: Literal["viewer", "contributor", "reviewer", "release_manager"]


class ReconcileBody(StrictBody):
    request_id: UUID
    external_id: int = Field(gt=0)


class TargetBody(StrictBody):
    os: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    arch: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")


class ManifestBody(StrictBody):
    packaging_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    sdk_version: str = Field(min_length=1, max_length=80)
    script_path: str = Field(min_length=1, max_length=300)
    modules: dict[str, str]


class CandidateBody(StrictBody):
    request_id: UUID
    story_id: int = Field(gt=0)
    task_ids: list[int] = Field(min_length=1, max_length=100)
    manifest: ManifestBody
    targets: list[TargetBody] = Field(min_length=1, max_length=30)


class BuildBody(StrictBody):
    request_id: UUID
    os: str = Field(min_length=1, max_length=40)
    arch: str = Field(min_length=1, max_length=40)
    status: Literal["success", "failed", "cancelled", "timeout"]
    command: str = Field(min_length=1, max_length=2000)
    log_uri: str = Field(min_length=1, max_length=2000)
    artifact_name: str | None = Field(None, min_length=1, max_length=300)
    artifact_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    artifact_size: int | None = Field(None, gt=0)
    artifact_uri: str | None = Field(None, min_length=1, max_length=2000)


class TestBody(StrictBody):
    request_id: UUID
    suite: Literal["unit", "hardware"]
    status: Literal["passed", "failed", "blocked", "skipped"]
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    report_uri: str = Field(min_length=1, max_length=2000)
    device: str = Field("", max_length=300)
    firmware: str = Field("", max_length=300)


class ApprovalBody(StrictBody):
    request_id: UUID
    note: str = Field(min_length=1, max_length=2000)


class ReleaseBody(StrictBody):
    request_id: UUID
    channel: str = Field(min_length=1, max_length=100)
    rollback_uri: str = Field(min_length=1, max_length=2000)
    released_at: datetime


def _actor(request: Request) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise HTTPException(401, "authentication_required")
    return context


def _service(request: Request):
    service = getattr(request.app.state, "sdk_service", None)
    if service is None:
        raise HTTPException(503, "sdk_unavailable")
    return service


def _evidence(request: Request):
    service = getattr(request.app.state, "sdk_evidence_service", None)
    if service is None:
        raise HTTPException(503, "sdk_unavailable")
    return service


def build_sdk_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/sdk", tags=["sdk-workbench"])

    @router.get("/me")
    async def me(request: Request):
        return JSONResponse(_service(request).me(_actor(request)), headers=_NO_STORE)

    @router.get("/members")
    async def members(request: Request):
        return JSONResponse(_service(request).list_members(_actor(request)), headers=_NO_STORE)

    @router.post("/members")
    async def grant_member(request: Request, body: MemberBody):
        result = _service(request).grant_member(_actor(request), body.internal_user_id, body.role, body.request_id)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/reconciliation")
    async def reconciliation(request: Request):
        return JSONResponse(_service(request).reconciliation(_actor(request)), headers=_NO_STORE)

    @router.post("/reconciliation/{actor_id}/{target_request_id}/bind")
    async def bind_reconciliation(request: Request, actor_id: UUID, target_request_id: UUID, body: ReconcileBody):
        result = await _service(request).bind_reconciliation(_actor(request), actor_id, target_request_id, body.request_id, body.external_id)
        return JSONResponse(result, headers=_NO_STORE)

    @router.get("/requirements")
    async def list_requirements(request: Request, page: int = Query(1, ge=1, le=1000)):
        return JSONResponse(await _service(request).list_requirements(_actor(request), page), headers=_NO_STORE)

    @router.post("/requirements")
    async def create_requirement(request: Request, body: RequirementBody):
        result = await _service(request).create_requirement(_actor(request), body.request_id, body.title, body.scenario, body.device_platform, body.acceptance)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/requirements/{story_id}")
    async def requirement(request: Request, story_id: int):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        return JSONResponse(await _service(request).requirement(_actor(request), story_id), headers=_NO_STORE)

    @router.post("/requirements/{story_id}/tasks")
    async def create_task(request: Request, story_id: int, body: TaskBody):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        result = await _service(request).create_task(_actor(request), story_id, body.request_id, body.name, body.description)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.post("/requirements/{story_id}/reviews")
    async def review(request: Request, story_id: int, body: ReviewBody):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        result = await _service(request).review(_actor(request), story_id, body.request_id, body.decision, body.reason)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/requirements/{story_id}/reviews")
    async def reviews(request: Request, story_id: int, page: int = Query(1, ge=1, le=1000)):
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        return JSONResponse(await _service(request).reviews(_actor(request), story_id, page), headers=_NO_STORE)

    @router.get("/candidates")
    async def candidates(request: Request, page: int = Query(1, ge=1, le=1000)):
        return JSONResponse(_evidence(request).list_candidates(_actor(request), page), headers=_NO_STORE)

    @router.post("/candidates")
    async def create_candidate(request: Request, body: CandidateBody):
        result = await _evidence(request).create_candidate(_actor(request), body.request_id, body.story_id, body.task_ids,
                                                           body.manifest.model_dump(), [target.model_dump() for target in body.targets])
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/candidates/{candidate_id}")
    async def candidate(request: Request, candidate_id: int):
        return JSONResponse(_evidence(request).candidate(_actor(request), candidate_id), headers=_NO_STORE)

    @router.post("/candidates/{candidate_id}/builds")
    async def build(request: Request, candidate_id: int, body: BuildBody):
        result = _evidence(request).add_build(_actor(request), candidate_id, body.request_id, body.model_dump(exclude={"request_id"}))
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.post("/artifacts/{build_id}/tests")
    async def test(request: Request, build_id: int, body: TestBody):
        result = _evidence(request).add_test(_actor(request), build_id, body.request_id, body.model_dump(exclude={"request_id"}))
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.get("/candidates/{candidate_id}/gate")
    async def gate(request: Request, candidate_id: int):
        return JSONResponse(await _evidence(request).gate(_actor(request), candidate_id), headers=_NO_STORE)

    @router.post("/candidates/{candidate_id}/approval")
    async def approval(request: Request, candidate_id: int, body: ApprovalBody):
        result = await _evidence(request).approve(_actor(request), candidate_id, body.request_id, body.note)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    @router.post("/candidates/{candidate_id}/release")
    async def release(request: Request, candidate_id: int, body: ReleaseBody):
        if body.released_at.tzinfo is None:
            raise HTTPException(422, "sdk_release_time_timezone_required")
        result = await _evidence(request).release(_actor(request), candidate_id, body.request_id, body.channel, body.rollback_uri, body.released_at)
        return JSONResponse(result, status_code=201, headers=_NO_STORE)

    return router
