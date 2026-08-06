from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Ticket(BaseModel):
    ticket: str
    expires_at: datetime
    content_path: str


@dataclass(frozen=True)
class ResolvedAttachment:
    attachment_id: UUID
    purpose: str
    display_name: str
    mime_type: str
    size_bytes: int
    bucket: str
    object_key: str
    sha256: str


@dataclass(frozen=True)
class OpenedAttachment:
    stream: Iterable[bytes]
    status_code: int
    media_type: str
    headers: dict[str, str]
