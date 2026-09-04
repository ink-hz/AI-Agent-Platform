from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.control_plane.models import AuthContext

from .candidate_models import (
    AppendHumanFeedback,
    Candidate,
    CandidateAnalysisVersion,
    CandidateDocument,
    CandidateDraft,
    ComparePositionCandidates,
    ConfirmCandidateDraft,
    CreateCandidateAnalysis,
    CreateCandidateDraftBatch,
    HumanFeedback,
    PositionCandidate,
    RetryCandidateDraft,
)
from .candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateUnavailable,
)
from .candidate_service import CandidateIdentityConflict, CandidateScopeViolation

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrCandidateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secure(request: Request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_PRIVATE_HEADERS}
                raise
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "HR candidate request invalid"}, status_code=422
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_row_version: int = Field(ge=1)


class DocumentTicketBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    purpose: Literal["preview", "download"]


class ConfirmBody(VersionBody):
    context_version_id: UUID
    stable_name: str = Field(min_length=1, max_length=500)
    confirmed_facts: dict[str, object]
    merge_candidate_id: UUID | None = None


class AnalysisBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_version_id: UUID
    document_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    feedback_ids: tuple[UUID, ...] = Field(max_length=100)
    analysis_kind: Literal["resume_extract", "match", "candidate_interview_plan"]
    result: dict[str, object]
    evidence: tuple[dict[str, object], ...] = Field(max_length=500)
    unknowns: tuple[str, ...] = Field(max_length=500)
    conflicts: tuple[str, ...] = Field(max_length=500)
    verification_questions: tuple[str, ...] = Field(max_length=500)
    agent_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_version_id: UUID
    feedback_kind: Literal["accepted", "rejected", "correction"]
    conclusion_key: str = Field(min_length=1, max_length=256)
    correction: str | None = Field(default=None, min_length=1, max_length=8000)
    reason: str = Field(min_length=1, max_length=4000)


class ComparisonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_candidate_ids: tuple[UUID, ...] = Field(min_length=2, max_length=20)
    context_version_id: UUID
    agent_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)


def _request_id(value: str | None) -> UUID:
    try:
        if value is None:
            raise ValueError
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Idempotency-Key must be a UUID") from None


def _command(factory, *args, **kwargs):
    try:
        return factory(*args, **kwargs)
    except ValueError:
        raise HTTPException(422, "HR candidate request invalid") from None


def _draft(record: CandidateDraft) -> dict[str, object]:
    return {
        "draft_id": str(record.draft_id),
        "position_id": str(record.position_id),
        "attachment_id": str(record.attachment_id),
        "batch_request_id": str(record.batch_request_id),
        "state": record.state,
        "extracted_facts": record.extracted_facts,
        "identity_candidates": [str(value) for value in record.identity_candidates],
        "error_code": record.error_code,
        "row_version": record.row_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _candidate(record: Candidate) -> dict[str, object]:
    return {
        "candidate_id": str(record.candidate_id),
        "stable_name": record.stable_name,
        "facts": record.facts,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _document(record: CandidateDocument) -> dict[str, object]:
    return {
        "document_id": str(record.document_id),
        "candidate_id": str(record.candidate_id),
        "attachment_id": str(record.attachment_id),
        "source_draft_id": str(record.source_draft_id),
        "document_kind": record.document_kind,
        "version_number": record.version_number,
        "content_sha256": record.content_sha256,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }


def _document_ticket(ticket) -> dict[str, object]:
    return {
        "content_path": ticket.content_path,
        "expires_at": (
            ticket.expires_at.isoformat()
            if hasattr(ticket.expires_at, "isoformat")
            else ticket.expires_at
        ),
    }


def _position_candidate(record: PositionCandidate) -> dict[str, object]:
    return {
        "position_candidate_id": str(record.position_candidate_id),
        "position_id": str(record.position_id),
        "candidate_id": str(record.candidate_id),
        "context_version_id": str(record.context_version_id),
        "source_draft_id": str(record.source_draft_id),
        "status": record.status,
        "row_version": record.row_version,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _analysis(record: CandidateAnalysisVersion) -> dict[str, object]:
    return {
        "analysis_version_id": str(record.analysis_version_id),
        "position_candidate_id": str(record.position_candidate_id),
        "position_id": str(record.position_id),
        "candidate_id": str(record.candidate_id),
        "context_version_id": str(record.context_version_id),
        "version_number": record.version_number,
        "analysis_kind": record.analysis_kind,
        "document_ids": [str(value) for value in record.document_ids],
        "feedback_ids": [str(value) for value in record.feedback_ids],
        "result": record.result,
        "evidence": list(record.evidence),
        "unknowns": list(record.unknowns),
        "conflicts": list(record.conflicts),
        "verification_questions": list(record.verification_questions),
        "agent_version": record.agent_version,
        "model_version": record.model_version,
        "created_at": record.created_at.isoformat(),
    }


def _feedback(record: HumanFeedback) -> dict[str, object]:
    return {
        "feedback_id": str(record.feedback_id),
        "position_candidate_id": str(record.position_candidate_id),
        "analysis_version_id": str(record.analysis_version_id),
        "feedback_kind": record.feedback_kind,
        "conclusion_key": record.conclusion_key,
        "correction": record.correction,
        "reason": record.reason,
        "created_at": record.created_at.isoformat(),
    }


def build_candidate_router(service, require_hr_access) -> APIRouter:
    required = (
        "create_drafts", "list_drafts", "draft", "retry_draft",
        "dismiss_draft", "confirm_draft", "list_position_candidates",
        "candidate", "documents", "candidate_document", "position_candidate",
        "candidate_document_ticket",
        "list_analyses", "add_analysis",
        "list_feedback", "append_feedback", "compare",
    )
    if any(not callable(getattr(service, name, None)) for name in required):
        raise ValueError("candidate service required")
    if not callable(require_hr_access):
        raise ValueError("HR access resolver required")
    router = APIRouter(tags=["hr-candidates"], route_class=HrCandidateRoute)

    async def owner(request: Request, *, writable: bool = False) -> UUID:
        context = getattr(request.state, "auth_context", None)
        if not isinstance(context, AuthContext):
            raise HTTPException(401, "authentication required")
        if writable and context.hard_stale_read_only:
            raise HTTPException(503, "account is read only")
        selected = require_hr_access(request, writable=writable)
        if inspect.isawaitable(selected):
            selected = await selected
        if not isinstance(selected, UUID) or selected != context.internal_user_id:
            raise HTTPException(401, "authentication required")
        return selected

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except (CandidateNotFound, CandidateScopeViolation):
            raise HTTPException(404, "HR candidate not found") from None
        except (CandidateConflict, CandidateIdentityConflict):
            raise HTTPException(409, "HR candidate conflict") from None
        except CandidateUnavailable:
            raise HTTPException(503, "HR candidate unavailable") from None
        except ValueError:
            raise HTTPException(422, "HR candidate request invalid") from None

    @router.post(
        "/api/hr/positions/{position_id}/candidate-drafts:batch",
        status_code=202,
    )
    async def create_batch(
        body: BatchBody,
        request: Request,
        position_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        owner_id = await owner(request, writable=True)
        request_id = _request_id(idempotency_key)
        records = await call(
            service.create_drafts,
            _command(
                CreateCandidateDraftBatch,
                owner_id, position_id, body.attachment_ids, request_id
            ),
        )
        return {"batch_id": str(request_id), "items": [_draft(item) for item in records]}

    @router.get("/api/hr/positions/{position_id}/candidate-drafts")
    async def list_drafts(
        request: Request,
        position_id: Annotated[UUID, Path()],
        batch_request_id: Annotated[UUID | None, Query()] = None,
    ):
        owner_id = await owner(request)
        records = await call(
            service.list_drafts, owner_id, position_id,
            batch_request_id=batch_request_id,
        )
        return {"items": [_draft(item) for item in records]}

    @router.get("/api/hr/candidate-drafts/{draft_id}")
    async def draft_detail(
        request: Request, draft_id: Annotated[UUID, Path()]
    ):
        return _draft(await call(service.draft, await owner(request), draft_id))

    @router.post("/api/hr/candidate-drafts/{draft_id}:retry")
    async def retry_draft(
        body: VersionBody,
        request: Request,
        draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        command = _command(
            RetryCandidateDraft,
            await owner(request, writable=True), draft_id,
            _request_id(idempotency_key), body.expected_row_version,
        )
        return _draft(await call(service.retry_draft, command))

    @router.post("/api/hr/candidate-drafts/{draft_id}:dismiss")
    async def dismiss_draft(
        body: VersionBody,
        request: Request,
        draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        command = _command(
            RetryCandidateDraft,
            await owner(request, writable=True), draft_id,
            _request_id(idempotency_key), body.expected_row_version,
        )
        return _draft(await call(service.dismiss_draft, command))

    @router.post("/api/hr/candidate-drafts/{draft_id}:confirm", status_code=201)
    async def confirm_draft(
        body: ConfirmBody,
        request: Request,
        draft_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        owner_id = await owner(request, writable=True)
        request_id = _request_id(idempotency_key)
        command = _command(
            ConfirmCandidateDraft,
            owner_id=owner_id,
            draft_id=draft_id,
            client_request_id=request_id,
            expected_row_version=body.expected_row_version,
            candidate_id=uuid5(owner_id, f"{request_id}:candidate"),
            stable_name=body.stable_name,
            confirmed_facts=body.confirmed_facts,
            merge_candidate_id=body.merge_candidate_id,
        )
        confirmed = await call(
            service.confirm_draft, command,
            context_version_id=body.context_version_id,
        )
        return {
            "candidate": _candidate(confirmed.candidate),
            "document": _document(confirmed.document),
            "position_candidate": _position_candidate(
                confirmed.position_candidate
            ),
        }

    @router.get("/api/hr/positions/{position_id}/candidates")
    async def list_position_candidates(
        request: Request, position_id: Annotated[UUID, Path()]
    ):
        records = await call(
            service.list_position_candidates, await owner(request), position_id
        )
        return {"items": [_position_candidate(item) for item in records]}

    @router.get("/api/hr/candidates/{candidate_id}")
    async def candidate_detail(
        request: Request, candidate_id: Annotated[UUID, Path()]
    ):
        return _candidate(
            await call(service.candidate, await owner(request), candidate_id)
        )

    @router.get("/api/hr/candidates/{candidate_id}/documents")
    async def candidate_documents(
        request: Request, candidate_id: Annotated[UUID, Path()]
    ):
        records = await call(
            service.documents, await owner(request), candidate_id
        )
        return {"items": [_document(item) for item in records]}

    @router.get("/api/hr/candidate-documents/{document_id}")
    async def candidate_document_detail(
        request: Request, document_id: Annotated[UUID, Path()]
    ):
        return _document(
            await call(service.candidate_document, await owner(request), document_id)
        )

    @router.post("/api/hr/candidate-documents/{document_id}/ticket")
    async def candidate_document_ticket(
        body: DocumentTicketBody,
        request: Request,
        document_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        _request_id(idempotency_key)
        return _document_ticket(await call(
            service.candidate_document_ticket,
            await owner(request, writable=True),
            document_id,
            body.purpose,
        ))

    @router.get("/api/hr/position-candidates/{position_candidate_id}")
    async def position_candidate_detail(
        request: Request,
        position_candidate_id: Annotated[UUID, Path()],
    ):
        return _position_candidate(await call(
            service.position_candidate, await owner(request), position_candidate_id
        ))

    @router.get("/api/hr/position-candidates/{position_candidate_id}/analyses")
    async def list_analyses(
        request: Request,
        position_candidate_id: Annotated[UUID, Path()],
    ):
        records = await call(
            service.list_analyses, await owner(request), position_candidate_id
        )
        return {"items": [_analysis(item) for item in records]}

    @router.post(
        "/api/hr/position-candidates/{position_candidate_id}/analyses",
        status_code=201,
    )
    async def add_analysis(
        body: AnalysisBody,
        request: Request,
        position_candidate_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        command = _command(
            CreateCandidateAnalysis,
            owner_id=await owner(request, writable=True),
            position_candidate_id=position_candidate_id,
            context_version_id=body.context_version_id,
            document_ids=body.document_ids,
            feedback_ids=body.feedback_ids,
            analysis_kind=body.analysis_kind,
            client_request_id=_request_id(idempotency_key),
            result=body.result,
            evidence=body.evidence,
            unknowns=body.unknowns,
            conflicts=body.conflicts,
            verification_questions=body.verification_questions,
            agent_version=body.agent_version,
            model_version=body.model_version,
        )
        return _analysis(await call(service.add_analysis, command))

    @router.get("/api/hr/position-candidates/{position_candidate_id}/feedback")
    async def list_feedback(
        request: Request,
        position_candidate_id: Annotated[UUID, Path()],
    ):
        records = await call(
            service.list_feedback, await owner(request), position_candidate_id
        )
        return {"items": [_feedback(item) for item in records]}

    @router.post(
        "/api/hr/position-candidates/{position_candidate_id}/feedback",
        status_code=201,
    )
    async def append_feedback(
        body: FeedbackBody,
        request: Request,
        position_candidate_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        command = _command(
            AppendHumanFeedback,
            owner_id=await owner(request, writable=True),
            position_candidate_id=position_candidate_id,
            analysis_version_id=body.analysis_version_id,
            feedback_kind=body.feedback_kind,
            conclusion_key=body.conclusion_key,
            correction=body.correction,
            reason=body.reason,
            client_request_id=_request_id(idempotency_key),
        )
        return _feedback(await call(service.append_feedback, command))

    @router.post(
        "/api/hr/positions/{position_id}/candidate-comparisons",
        status_code=201,
    )
    async def compare_candidates(
        body: ComparisonBody,
        request: Request,
        position_id: Annotated[UUID, Path()],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key")
        ] = None,
    ):
        command = _command(
            ComparePositionCandidates,
            owner_id=await owner(request, writable=True),
            position_id=position_id,
            position_candidate_ids=body.position_candidate_ids,
            context_version_id=body.context_version_id,
            client_request_id=_request_id(idempotency_key),
            agent_version=body.agent_version,
            model_version=body.model_version,
        )
        return _analysis(await call(service.compare, command))

    return router
