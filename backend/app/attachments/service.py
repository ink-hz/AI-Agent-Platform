from collections.abc import Iterator
from typing import Any
from urllib.parse import quote
from uuid import UUID
import logging
import re
import unicodedata

from .models import OpenedAttachment, ResolvedAttachment, Ticket
from .repository import AttachmentRepository
from .store import AttachmentStore, AttachmentStoreError


logger = logging.getLogger(__name__)
CHUNK_SIZE = 1024 * 1024
INLINE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
    }
)


class AttachmentNotFound(RuntimeError):
    pass


class AttachmentConflict(RuntimeError):
    pass


class AttachmentRangeError(RuntimeError):
    pass


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", value.strip())
    if not match:
        raise AttachmentRangeError("invalid byte range")
    start, end = (int(part) for part in match.groups())
    if start > end or end >= size:
        raise AttachmentRangeError("invalid byte range")
    return start, end


def _content_disposition(name: str, inline: bool) -> str:
    cleaned = name.replace("\r", "").replace("\n", "") or "attachment"
    ascii_name = unicodedata.normalize("NFKD", cleaned).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name).strip()
    ascii_name = ascii_name or "attachment"
    encoded = quote(cleaned, safe="!#$&+-.^_`|~")
    disposition = "inline" if inline else "attachment"
    return (
        f'{disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded}"
    )


class AttachmentService:
    def __init__(
        self,
        repository: AttachmentRepository,
        store: AttachmentStore,
        ticket_seconds: int,
    ) -> None:
        self._repository = repository
        self._store = store
        self._ticket_seconds = min(max(ticket_seconds, 1), 300)

    def issue_ticket(self, attachment_id: UUID, purpose: str) -> Ticket:
        issued = self._repository.issue_ticket(
            attachment_id, purpose, self._ticket_seconds
        )
        if issued is None:
            raise AttachmentNotFound("attachment unavailable")
        return issued

    def open_content(
        self,
        ticket: str,
        range_header: str | None,
        context: dict[str, Any],
    ) -> OpenedAttachment:
        trusted_context = {
            key: context[key]
            for key in ("request_id", "remote_class")
            if context.get(key) is not None
        }
        trusted_context["range_requested"] = range_header is not None
        resolved = self._repository.resolve_ticket(ticket, trusted_context)
        if resolved is None:
            raise AttachmentNotFound("attachment unavailable")
        inline = (
            resolved.purpose == "preview"
            and resolved.mime_type in INLINE_MIME_TYPES
        )
        if resolved.purpose == "preview" and not inline:
            self._repository.record_access(
                resolved, "unsupported_preview", trusted_context
            )
            raise AttachmentConflict(
                "preview is unavailable for this content type; use download"
            )
        byte_range = _parse_range(range_header, resolved.size_bytes)
        try:
            body, content_length = self._store.open(resolved, byte_range)
        except AttachmentStoreError as error:
            result = (
                "size_mismatch"
                if "size mismatch" in str(error)
                else "store_failed"
            )
            self._repository.record_access(resolved, result, trusted_context)
            raise AttachmentConflict(
                "attachment size or storage check failed"
            ) from error
        expected_length = (
            resolved.size_bytes
            if byte_range is None
            else byte_range[1] - byte_range[0] + 1
        )
        if content_length != expected_length:
            try:
                body.close()
            finally:
                self._repository.record_access(
                    resolved, "size_mismatch", trusted_context
                )
            raise AttachmentConflict("attachment size mismatch")

        headers = {
            "Content-Disposition": _content_disposition(
                resolved.display_name, inline
            ),
            "Content-Length": str(content_length),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Accept-Ranges": "bytes",
        }
        if inline and resolved.mime_type == "application/pdf":
            headers["Content-Security-Policy"] = "sandbox"
        status_code = 200
        if byte_range is not None:
            status_code = 206
            headers["Content-Range"] = (
                f"bytes {byte_range[0]}-{byte_range[1]}/{resolved.size_bytes}"
            )

        return OpenedAttachment(
            stream=self._stream(body, resolved, trusted_context),
            status_code=status_code,
            media_type=resolved.mime_type,
            headers=headers,
        )

    def _stream(
        self,
        body,
        resolved: ResolvedAttachment,
        context: dict[str, Any],
    ) -> Iterator[bytes]:
        result = "stream_failed"
        try:
            while True:
                chunk = body.read(CHUNK_SIZE)
                if not chunk:
                    result = "streamed"
                    break
                yield chunk
        finally:
            try:
                body.close()
            finally:
                self._repository.record_access(resolved, result, context)
                logger.info("attachment stream completed result=%s", result)
