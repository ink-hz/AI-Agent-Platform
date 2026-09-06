from __future__ import annotations

import asyncio
import inspect
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from .panorama_export import (
    build_panorama_pdf,
    build_panorama_xlsx,
    filter_panorama_snapshots,
)
from .panorama_models import (
    PanoramaReport,
    PublicJobSnapshot,
    PublishedPanorama,
    SourceCollectionAttempt,
    TalentInsightVersion,
    TalentSource,
    thaw_json,
)
from .panorama_repository import (
    PanoramaConflict,
    PanoramaNotFound,
    PanoramaUnavailable,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class HrPanoramaRoute(APIRoute):
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
                    {"detail": "HR panorama request invalid"}, status_code=422
                )
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return secure


def _source(record: TalentSource) -> dict[str, object]:
    return {
        "source_id": str(record.source_id),
        "source_kind": record.source_kind,
        "canonical_name": record.canonical_name,
        "aliases": list(record.aliases),
        "approved_urls": list(record.approved_urls),
        "active": record.active,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _insight(record: TalentInsightVersion) -> dict[str, object]:
    return {
        "insight_version_id": str(record.insight_version_id),
        "run_id": None if record.run_id is None else str(record.run_id),
        "production_batch_id": (
            None
            if record.production_batch_id is None
            else str(record.production_batch_id)
        ),
        "version_number": record.version_number,
        "selected_source_ids": [str(value) for value in record.selected_source_ids],
        "snapshot_ids": [str(value) for value in record.snapshot_ids],
        "facts": thaw_json(record.facts),
        "inferences": thaw_json(record.inferences),
        "unknowns": thaw_json(record.unknowns),
        "direction_clusters": thaw_json(record.direction_clusters),
        "summary": record.summary,
        "source_conversation_id": (
            None
            if record.source_conversation_id is None
            else str(record.source_conversation_id)
        ),
        "source_turn_id": (
            None if record.source_turn_id is None else str(record.source_turn_id)
        ),
        "agent_id": record.agent_id,
        "model_version": record.model_version,
        "created_at": record.created_at.isoformat(),
    }


def _snapshot(record: PublicJobSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": str(record.snapshot_id),
        "run_id": None if record.run_id is None else str(record.run_id),
        "production_batch_id": (
            None
            if record.production_batch_id is None
            else str(record.production_batch_id)
        ),
        "observation_id": (
            None if record.observation_id is None else str(record.observation_id)
        ),
        "source_id": str(record.source_id),
        "public_job_key": record.public_job_key,
        "title": record.title,
        "location": record.location,
        "duty_excerpt": record.duty_excerpt,
        "requirement_excerpt": record.requirement_excerpt,
        "source_url": record.source_url,
        "observed_at": record.observed_at.isoformat(),
        "content_sha256": record.content_sha256,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }


def _publication(record: PublishedPanorama) -> dict[str, object]:
    return {
        "publication_id": str(record.publication_id),
        "batch_id": str(record.batch_id),
        "insight_version_id": str(record.insight_version_id),
        "coverage_state": record.coverage_state,
        "source_coverage": thaw_json(record.source_coverage),
        "published_at": record.published_at.isoformat(),
    }


def _evidence(record: SourceCollectionAttempt) -> dict[str, object]:
    return {
        "source_id": str(record.source_id),
        "source_url": record.source_url,
        "attempt_number": record.attempt_number,
        "state": record.state,
        "error_code": record.error_code,
        "sha256": record.evidence_sha256,
        "mime": record.evidence_mime,
        "size_bytes": record.evidence_size_bytes,
        "normalized_job_count": record.normalized_job_count,
        "observed_at": record.observed_at.isoformat(),
    }


def _report(record: PanoramaReport) -> dict[str, object]:
    if record.publication is None:
        raise PanoramaUnavailable("published panorama metadata unavailable")
    return {
        "publication": _publication(record.publication),
        "insight": _insight(record.insight),
        "sources": [_source(value) for value in record.sources],
        "snapshots": [_snapshot(value) for value in record.snapshots],
        "evidence": [
            _evidence(value)
            for value in record.evidence_attempts
            if value.evidence_sha256 is not None
        ],
    }


def _report_summary(record: PanoramaReport) -> dict[str, object]:
    if record.publication is None:
        raise PanoramaUnavailable("published panorama metadata unavailable")
    return {
        "publication": _publication(record.publication),
        "insight": _insight(record.insight),
    }


def build_panorama_router(service, require_hr_access) -> APIRouter:
    required = ("current_report", "list_reports", "report", "evidence_file")
    if any(not callable(getattr(service, name, None)) for name in required):
        raise ValueError("panorama service required")
    if not callable(require_hr_access):
        raise TypeError("HR access dependency required")

    router = APIRouter(tags=["hr-panorama"], route_class=HrPanoramaRoute)

    async def authorize(request: Request) -> None:
        selected = require_hr_access(request, writable=False)
        if inspect.isawaitable(selected):
            selected = await selected
        if not isinstance(selected, UUID):
            raise HTTPException(401, "authentication required")

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except PanoramaNotFound:
            raise HTTPException(404, "HR panorama not found") from None
        except PanoramaConflict:
            raise HTTPException(409, "HR panorama conflict") from None
        except PanoramaUnavailable:
            raise HTTPException(503, "HR panorama unavailable") from None
        except (TypeError, ValueError):
            raise HTTPException(422, "HR panorama request invalid") from None

    @router.get("/api/hr/panorama/current")
    async def current_report(request: Request):
        await authorize(request)
        record = await call(service.current_report)
        return Response(status_code=204) if record is None else _report(record)

    @router.get("/api/hr/panorama/reports")
    async def list_reports(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ):
        await authorize(request)
        records = await call(service.list_reports, limit=limit)
        return {"items": [_report_summary(record) for record in records]}

    @router.get("/api/hr/panorama/reports/{publication_id}")
    async def report(
        request: Request,
        publication_id: Annotated[UUID, Path()],
    ):
        await authorize(request)
        return _report(await call(service.report, publication_id))

    @router.get("/api/hr/panorama/reports/{publication_id}/export")
    async def export_report(
        request: Request,
        publication_id: Annotated[UUID, Path()],
        export_format: Annotated[Literal["pdf", "xlsx"], Query(alias="format")],
        source_id: Annotated[UUID | None, Query()] = None,
        recruitment_track: Annotated[
            Literal["social", "campus", "intern", "unknown"] | None, Query()
        ] = None,
        location: Annotated[str | None, Query(min_length=1, max_length=1000)] = None,
        status: Annotated[Literal["open", "closed", "unknown"] | None, Query()] = None,
        technical_direction: Annotated[
            Literal[
                "algorithm",
                "optics",
                "hardware",
                "structure",
                "software",
                "manufacturing",
                "quality",
                "product",
                "supply_chain",
                "other",
            ]
            | None,
            Query(),
        ] = None,
    ):
        await authorize(request)
        record = await call(service.report, publication_id)
        snapshots = filter_panorama_snapshots(
            record,
            source_id=source_id,
            recruitment_track_filter=recruitment_track,
            location=location,
            status=status,
            technical_direction_filter=technical_direction,
        )
        filtered = any(
            value is not None
            for value in (
                source_id,
                recruitment_track,
                location,
                status,
                technical_direction,
            )
        )
        if export_format == "pdf":
            content = await asyncio.to_thread(
                build_panorama_pdf, record, snapshots, filtered=filtered
            )
            media_type = "application/pdf"
        else:
            content = await asyncio.to_thread(
                build_panorama_xlsx, record, snapshots, filtered=filtered
            )
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        filename = (
            f"hr-panorama-v{record.insight.version_number}-"
            f"{record.publication.published_at.date().isoformat()}.{export_format}"
        )
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get(
        "/api/hr/panorama/reports/{publication_id}/evidence/{evidence_sha256}"
    )
    async def download_evidence(
        request: Request,
        publication_id: Annotated[UUID, Path()],
        evidence_sha256: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    ):
        await authorize(request)
        selected = await call(
            service.evidence_file, publication_id, evidence_sha256
        )
        extension = {
            "application/json": "json",
            "text/html": "html",
            "text/plain": "txt",
        }.get(selected.mime, "bin")
        return Response(
            selected.body,
            media_type=selected.mime,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="source-evidence-'
                    f'{selected.sha256[:12]}.{extension}"'
                )
            },
        )

    return router


__all__ = ["build_panorama_router"]
