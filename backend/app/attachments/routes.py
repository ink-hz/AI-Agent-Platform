from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from .models import Ticket
from .service import AttachmentConflict, AttachmentNotFound, AttachmentRangeError


PRIVATE_RESPONSE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


class AttachmentRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def secure_route_handler(request: Request):
            try:
                response = await route_handler(request)
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    **PRIVATE_RESPONSE_HEADERS,
                }
                raise
            except RequestValidationError as error:
                response = await request_validation_exception_handler(
                    request, error
                )
            response.headers.update(PRIVATE_RESPONSE_HEADERS)
            return response

        return secure_route_handler


router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
    route_class=AttachmentRoute,
)


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
    raise HTTPException(
        status_code=500, detail="attachment service unavailable"
    ) from error


@router.post("/{attachment_id}/ticket", response_model=Ticket)
def issue_ticket(
    attachment_id: UUID, payload: TicketRequest, request: Request
) -> Ticket:
    try:
        return _service(request).issue_ticket(attachment_id, payload.purpose)
    except RuntimeError as error:
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
    except RuntimeError as error:
        _raise_safe(error)
    return StreamingResponse(
        opened.stream,
        status_code=opened.status_code,
        media_type=opened.media_type,
        headers=opened.headers,
    )
