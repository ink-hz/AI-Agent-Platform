from __future__ import annotations

import tempfile
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from .conversation_models import (
    MAX_FILE_BYTES,
    AttachmentRecord,
    BeginUpload,
    UploadRecord,
)
from .download_service import DownloadConflict, DownloadNotFound, DownloadRangeError
from .upload_service import AttachmentUploadConflict

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


class ConversationAttachmentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secure(request: Request):
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **PRIVATE_HEADERS}
                raise
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "attachment request invalid"}, status_code=422
                )
            response.headers.update(PRIVATE_HEADERS)
            return response

        return secure


class BeginUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: UUID
    original_name: str
    declared_mime: str
    declared_size: int


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["preview", "download"]


class UploadResponse(BaseModel):
    upload_id: UUID
    attachment_id: UUID
    conversation_id: UUID | None
    original_name: str
    declared_mime: str
    declared_size: int
    state: str
    uploaded_bytes: int
    expires_at: datetime


class AttachmentResponse(BaseModel):
    attachment_id: UUID
    conversation_id: UUID | None
    original_name: str
    declared_mime: str
    detected_mime: str | None
    size_bytes: int
    state: str
    created_at: datetime
    retained_until: datetime


class TicketResponse(BaseModel):
    ticket: str
    expires_at: datetime
    content_path: str


def _owner(request: Request) -> UUID:
    context = getattr(request.state, "auth_context", None)
    owner_id = getattr(context, "internal_user_id", None)
    if not isinstance(owner_id, UUID):
        raise HTTPException(status_code=401, detail="authentication required")
    return owner_id


def _upload_service(request: Request):
    return request.app.state.conversation_attachment_upload_service


def _download_service(request: Request):
    return request.app.state.conversation_attachment_download_service


def _upload_response(record: UploadRecord) -> UploadResponse:
    return UploadResponse(
        upload_id=record.upload_id,
        attachment_id=record.attachment_id,
        conversation_id=record.conversation_id,
        original_name=record.original_name,
        declared_mime=record.declared_mime,
        declared_size=record.declared_size,
        state=record.state,
        uploaded_bytes=record.actual_size or 0,
        expires_at=record.expires_at,
    )


def _attachment_response(record: AttachmentRecord) -> AttachmentResponse:
    return AttachmentResponse(
        attachment_id=record.attachment_id,
        conversation_id=record.conversation_id,
        original_name=record.original_name,
        declared_mime=record.declared_mime,
        detected_mime=record.detected_mime,
        size_bytes=record.size_bytes,
        state=record.state,
        created_at=record.created_at,
        retained_until=record.retained_until,
    )


def _raise_safe(error: RuntimeError) -> None:
    if isinstance(error, DownloadNotFound):
        raise HTTPException(status_code=404, detail="attachment unavailable") from None
    if isinstance(error, DownloadRangeError):
        raise HTTPException(
            status_code=416,
            detail="invalid byte range",
            headers={"Content-Range": error.content_range},
        ) from None
    if isinstance(error, (DownloadConflict, AttachmentUploadConflict)):
        raise HTTPException(
            status_code=409, detail="attachment operation unavailable"
        ) from None
    raise HTTPException(
        status_code=500, detail="attachment service unavailable"
    ) from None


def build_conversation_attachment_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        tags=["conversation-attachments"],
        route_class=ConversationAttachmentRoute,
    )

    @router.post("/attachments/uploads", response_model=UploadResponse, status_code=201)
    def begin_upload(payload: BeginUploadRequest, request: Request):
        try:
            result = _upload_service(request).begin(
                _owner(request),
                BeginUpload(
                    payload.conversation_id,
                    payload.original_name,
                    payload.declared_mime,
                    payload.declared_size,
                ),
            )
            return _upload_response(result)
        except RuntimeError as error:
            _raise_safe(error)

    @router.put(
        "/attachments/uploads/{upload_id}/content", response_model=UploadResponse
    )
    async def upload_content(
        upload_id: UUID,
        request: Request,
        content_length: str | None = Header(default=None, alias="Content-Length"),
    ):
        if (
            content_length is None
            or "chunked" in request.headers.get("transfer-encoding", "").lower()
        ):
            raise HTTPException(status_code=411, detail="content length required")
        try:
            size = int(content_length)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=411, detail="content length required"
            ) from None
        if size <= 0 or size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=409, detail="attachment operation unavailable"
            )
        received = 0
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as staged:
            async for chunk in request.stream():
                received += len(chunk)
                if received > size or received > MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=409, detail="attachment operation unavailable"
                    )
                staged.write(chunk)
            if received != size:
                raise HTTPException(
                    status_code=409, detail="attachment operation unavailable"
                )
            staged.seek(0)
            try:
                result = _upload_service(request).write(
                    _owner(request), upload_id, staged, size
                )
                return _upload_response(result)
            except RuntimeError as error:
                _raise_safe(error)

    @router.post(
        "/attachments/uploads/{upload_id}/complete", response_model=AttachmentResponse
    )
    def complete_upload(upload_id: UUID, request: Request):
        try:
            return _attachment_response(
                _upload_service(request).complete(_owner(request), upload_id)
            )
        except RuntimeError as error:
            _raise_safe(error)

    @router.delete("/attachments/uploads/{upload_id}", status_code=204)
    def cancel_upload(upload_id: UUID, request: Request):
        try:
            _download_service(request).cancel_upload(_owner(request), upload_id)
            return Response(status_code=204)
        except RuntimeError as error:
            _raise_safe(error)

    @router.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
    def attachment_status(attachment_id: UUID, request: Request):
        try:
            return _attachment_response(
                _download_service(request).attachment(_owner(request), attachment_id)
            )
        except RuntimeError as error:
            _raise_safe(error)

    @router.get(
        "/conversations/{conversation_id}/attachments",
        response_model=list[AttachmentResponse],
    )
    def list_attachments(conversation_id: UUID, request: Request):
        try:
            return [
                _attachment_response(record)
                for record in _download_service(request).list_conversation(
                    _owner(request), conversation_id
                )
            ]
        except RuntimeError as error:
            _raise_safe(error)

    @router.post("/attachments/{attachment_id}/ticket", response_model=TicketResponse)
    def issue_ticket(attachment_id: UUID, payload: TicketRequest, request: Request):
        try:
            return _download_service(request).issue_ticket(
                _owner(request), attachment_id, payload.purpose
            )
        except RuntimeError as error:
            _raise_safe(error)

    @router.get("/attachments/content/{ticket}")
    def content(
        ticket: str,
        request: Request,
        range_header: str | None = Header(default=None, alias="Range"),
    ):
        try:
            opened = _download_service(request).open_content(
                _owner(request), ticket, range_header
            )
        except RuntimeError as error:
            _raise_safe(error)
        return StreamingResponse(
            opened.stream,
            status_code=opened.status_code,
            media_type=opened.media_type,
            headers=opened.headers,
        )

    @router.delete("/attachments/{attachment_id}", status_code=204)
    def delete_attachment(attachment_id: UUID, request: Request):
        try:
            _download_service(request).delete_attachment(_owner(request), attachment_id)
            return Response(status_code=204)
        except RuntimeError as error:
            _raise_safe(error)

    @router.post("/conversations/{conversation_id}/artifacts/download")
    def archive_artifacts(conversation_id: UUID, request: Request):
        try:
            opened = _download_service(request).archive_conversation(
                _owner(request), conversation_id
            )
        except RuntimeError as error:
            _raise_safe(error)
        return StreamingResponse(
            opened.stream,
            status_code=opened.status_code,
            media_type=opened.media_type,
            headers=opened.headers,
        )

    return router
