from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from .models import Ticket
from .service import AttachmentConflict, AttachmentNotFound, AttachmentRangeError


router = APIRouter(prefix="/api/attachments", tags=["attachments"])


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["preview", "download"]


def _service(request: Request):
    return request.app.state.attachment_service


def _raise_safe(error: RuntimeError) -> None:
    if isinstance(error, AttachmentNotFound):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, AttachmentConflict):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, AttachmentRangeError):
        raise HTTPException(
            status_code=416,
            detail=str(error),
            headers={"Content-Range": "bytes */*"},
        ) from error
    raise error


@router.post("/{attachment_id}/ticket", response_model=Ticket)
def issue_ticket(
    attachment_id: UUID, payload: TicketRequest, request: Request
) -> Ticket:
    try:
        return _service(request).issue_ticket(attachment_id, payload.purpose)
    except (AttachmentNotFound, AttachmentConflict) as error:
        _raise_safe(error)


@router.get("/content/{ticket}")
def content(
    ticket: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    context = {
        "request_id": request_id,
        "remote_class": "loopback"
        if request.client
        and request.client.host in {"127.0.0.1", "::1", "testclient"}
        else "proxy",
    }
    try:
        opened = _service(request).open_content(ticket, range_header, context)
    except (AttachmentNotFound, AttachmentConflict, AttachmentRangeError) as error:
        _raise_safe(error)
    return StreamingResponse(
        opened.stream,
        status_code=opened.status_code,
        media_type=opened.media_type,
        headers=opened.headers,
    )
