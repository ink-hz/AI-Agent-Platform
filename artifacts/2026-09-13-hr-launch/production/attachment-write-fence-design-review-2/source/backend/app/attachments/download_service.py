from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, SealedContent

from .conversation_models import (
    MAX_FILE_BYTES,
    MAX_TASK_OUTPUT_BYTES,
    MAX_TASK_OUTPUT_FILES,
    AttachmentRecord,
)
from .conversation_repository import attachment_name_subject, attachment_object_subject
from .worker_runtime import derivative_object_subject

_CHUNK_BYTES = 1024 * 1024
_INLINE_MIMES = frozenset({"image/png", "image/jpeg", "application/pdf"})
_LOCATOR = re.compile(r"(?:version|etag):[^\x00-\x20\x7f]{1,1000}\Z")


class DownloadError(RuntimeError):
    pass


class DownloadNotFound(DownloadError):
    def __init__(self) -> None:
        super().__init__("attachment unavailable")


class DownloadUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("attachment service unavailable")


class DownloadConflict(DownloadError):
    def __init__(self) -> None:
        super().__init__("attachment operation unavailable")


class DownloadRangeError(DownloadError):
    def __init__(self, size: int) -> None:
        self.content_range = f"bytes */{size}"
        super().__init__("invalid byte range")


@dataclass(frozen=True)
class DownloadAsset:
    attachment_id: UUID
    owner_id: UUID
    conversation_id: UUID
    display_name: str = field(repr=False)
    media_type: str
    size_bytes: int
    sha256: bytes = field(repr=False)
    state: str
    object_ref: str = field(repr=False)
    immutable_locator: str | None = field(default=None, repr=False)
    artifact_key: str | None = None
    version_no: int | None = None
    derivative_kind: str | None = None

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, UUID)
                for value in (self.attachment_id, self.owner_id, self.conversation_id)
            )
            or not isinstance(self.display_name, str)
            or not self.display_name
            or not isinstance(self.media_type, str)
            or not self.media_type
            or isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 0 <= self.size_bytes <= MAX_FILE_BYTES
            or not isinstance(self.sha256, bytes)
            or len(self.sha256) != 32
            or not isinstance(self.object_ref, str)
            or not self.object_ref
            or (
                self.immutable_locator is not None
                and not _LOCATOR.fullmatch(self.immutable_locator)
            )
        ):
            raise ValueError("download asset invalid")


@dataclass(frozen=True, repr=False)
class BrowserTicket:
    ticket: str = field(repr=False)
    expires_at: datetime
    content_path: str = field(repr=False)

    def __repr__(self) -> str:
        return f"BrowserTicket(ticket=<redacted>, expires_at={self.expires_at!r}, content_path=<redacted>)"


@dataclass(frozen=True)
class OpenedDownload:
    stream: Iterator[bytes] = field(repr=False)
    status_code: int
    media_type: str
    headers: dict[str, str]


@dataclass(frozen=True, repr=False)
class _TicketClaim:
    owner_id: UUID
    resource_owner_id: UUID
    attachment_id: UUID
    purpose: str
    expires_at: datetime


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    if not isinstance(value, str) or not 40 <= len(value) <= 1024:
        raise ValueError
    return base64.b64decode(
        value.encode("ascii") + b"=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )


def _safe_name(value: str) -> str:
    first_line = value.splitlines()[0] if value.splitlines() else ""
    leaf = first_line.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = unicodedata.normalize("NFC", leaf)
    leaf = "".join(
        character for character in leaf if character >= " " and character != "\x7f"
    )
    leaf = leaf.strip(" .")
    return leaf[:240] or "attachment"


def _content_disposition(name: str, inline: bool) -> str:
    safe = _safe_name(name)
    ascii_name = (
        unicodedata.normalize("NFKD", safe).encode("ascii", "ignore").decode("ascii")
    )
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name).strip(" .")
    ascii_name = (ascii_name or "attachment").replace('"', "_")[:200]
    encoded = quote(safe, safe="!#$&+-.^_`|~")
    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    selected = value.strip()
    if not selected.startswith("bytes=") or "," in selected:
        raise DownloadRangeError(size)
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", selected)
    if match is None or not any(match.groups()) or size <= 0:
        raise DownloadRangeError(size)
    start_value, end_value = match.groups()
    if not start_value:
        suffix = int(end_value)
        if suffix <= 0:
            raise DownloadRangeError(size)
        return max(size - suffix, 0), size - 1
    start = int(start_value)
    end = size - 1 if not end_value else int(end_value)
    if start >= size or start > end:
        raise DownloadRangeError(size)
    return start, min(end, size - 1)


class ConversationAttachmentAccessRepository:
    def __init__(
        self, database_url: str, *, content_codec: ContentCodec, connect=psycopg.connect
    ) -> None:
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        validate_control_dsn(database_url, purpose="app")
        self._database_url = database_url
        self._content_codec = content_codec
        self._connect = connect

    def __repr__(self) -> str:
        return "ConversationAttachmentAccessRepository(database_url=<redacted>, content_codec=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
            row_factory=dict_row,
        )

    def _unseal(self, subject: str, ciphertext, version) -> dict:
        try:
            return self._content_codec.unseal_json(
                subject, SealedContent(bytes(ciphertext), int(version))
            )
        except Exception:  # noqa: BLE001 - crypto boundary is intentionally opaque
            raise DownloadNotFound() from None

    def _asset_from_row(self, row, *, derivative=False) -> DownloadAsset:
        attachment_id = row["attachment_id"]
        name = self._unseal(
            attachment_name_subject(attachment_id),
            row["original_name_ciphertext"],
            row["original_name_key_version"],
        )
        if set(name) != {"original_name"} or not isinstance(name["original_name"], str):
            raise DownloadNotFound()
        if derivative:
            subject = derivative_object_subject(attachment_id, row["derivative_id"])
        else:
            subject = attachment_object_subject(
                attachment_id, row.get("write_attempt_id")
            )
        reference = self._unseal(
            subject,
            row["object_ref_ciphertext"],
            row["object_ref_key_version"],
        )
        if set(reference) != {"object_ref"} or not isinstance(
            reference["object_ref"], str
        ):
            raise DownloadNotFound()
        sha256 = row["sha256"]
        conversation_id = row["conversation_id"]
        if sha256 is None or not isinstance(conversation_id, UUID):
            raise DownloadNotFound()
        return DownloadAsset(
            attachment_id=attachment_id,
            owner_id=row["owner_internal_user_id"],
            conversation_id=conversation_id,
            display_name=name["original_name"],
            media_type=row["detected_mime"] or "application/octet-stream",
            size_bytes=int(row["size_bytes"]),
            sha256=bytes(sha256),
            state=row["state"],
            object_ref=reference["object_ref"],
            immutable_locator=None if derivative else row["immutable_locator"],
            artifact_key=row.get("artifact_key"),
            version_no=row.get("version_no"),
            derivative_kind=row.get("kind") if derivative else None,
        )

    def downloadable(
        self, owner_id: UUID, attachment_id: UUID, purpose: str
    ) -> DownloadAsset:
        if not isinstance(owner_id, UUID) or not isinstance(attachment_id, UUID):
            raise DownloadNotFound()
        try:
            with self._connection() as connection:
                base = connection.execute(
                    "select attachment.*,upload.write_attempt_id from "
                    "platform_attachments.attachments attachment left join "
                    "platform_attachments.uploads upload using (attachment_id) "
                    "where attachment.attachment_id=%s and "
                    "attachment.owner_internal_user_id=%s and attachment.state='ready' "
                    "and attachment.retained_until>now() and attachment.immutable_locator is not null "
                    "and not exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=attachment.attachment_id)",
                    (attachment_id, owner_id),
                ).fetchone()
                if base is None:
                    raise DownloadNotFound()
                if purpose == "preview":
                    derivative = connection.execute(
                        "select derivative.derivative_id,derivative.kind,derivative.object_ref_ciphertext,"
                        "derivative.object_ref_key_version,derivative.detected_mime,"
                        "derivative.size_bytes,derivative.sha256,derivative.state,"
                        "attachment.attachment_id,attachment.owner_internal_user_id,"
                        "attachment.conversation_id,attachment.original_name_ciphertext,"
                        "attachment.original_name_key_version from "
                        "platform_attachments.derivatives derivative join "
                        "platform_attachments.attachments attachment using (attachment_id) "
                        "where derivative.attachment_id=%s and derivative.state='ready' "
                        "and derivative.kind in ('thumbnail','preview') "
                        "order by case derivative.kind when 'thumbnail' then 0 else 1 end limit 1",
                        (attachment_id,),
                    ).fetchone()
                    if derivative is not None:
                        return self._asset_from_row(derivative, derivative=True)
                    if base["detected_mime"] in _INLINE_MIMES:
                        raise DownloadNotFound()
            return self._asset_from_row(base)
        except DownloadError:
            raise
        except DownloadUnavailable:
            raise
        except psycopg.Error:
            raise DownloadUnavailable() from None
        except Exception:  # noqa: BLE001 - database boundary is intentionally opaque
            raise DownloadNotFound() from None

    def authorize_review(
        self, actor_id: UUID, attachment_id: UUID, purpose: str
    ) -> UUID:
        if (
            not isinstance(actor_id, UUID)
            or not isinstance(attachment_id, UUID)
            or purpose not in {"preview", "download"}
        ):
            raise DownloadNotFound()
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.authorize_review_attachment_access_v64("
                    "%s,%s,%s) as owner_internal_user_id",
                    (actor_id, attachment_id, purpose),
                ).fetchone()
            if row is None or not isinstance(row["owner_internal_user_id"], UUID):
                raise DownloadNotFound()
            return row["owner_internal_user_id"]
        except DownloadError:
            raise
        except Exception:  # noqa: BLE001 - authorization boundary is intentionally opaque
            raise DownloadNotFound() from None

    def attachment(self, owner_id: UUID, attachment_id: UUID) -> AttachmentRecord:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select attachment.* from platform_attachments.attachments attachment "
                    "where attachment_id=%s and owner_internal_user_id=%s and state<>'deleted' "
                    "and not exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=attachment.attachment_id)",
                    (attachment_id, owner_id),
                ).fetchone()
        except Exception:  # noqa: BLE001 - preserve an opaque operational failure type
            raise DownloadUnavailable() from None
        if row is None:
            raise DownloadNotFound()
        try:
            name = self._unseal(
                attachment_name_subject(attachment_id),
                row["original_name_ciphertext"],
                row["original_name_key_version"],
            )
            return AttachmentRecord(
                attachment_id,
                owner_id,
                row["conversation_id"],
                name["original_name"],
                row["declared_mime"] or "application/octet-stream",
                row["detected_mime"],
                int(row["size_bytes"]),
                bytes(row["sha256"]) if row["sha256"] else None,
                row["state"],
                row["created_at"],
                row["retained_until"],
            )
        except DownloadError:
            raise
        except Exception:  # noqa: BLE001 - corrupt row remains indistinguishable from absence
            raise DownloadNotFound() from None

    def list_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[AttachmentRecord, ...]:
        try:
            with self._connection() as connection:
                if (
                    connection.execute(
                        "select 1 from platform_control.conversations where conversation_id=%s "
                        "and owner_internal_user_id=%s",
                        (conversation_id, owner_id),
                    ).fetchone()
                    is None
                ):
                    raise DownloadNotFound()
                rows = connection.execute(
                    "select attachment.* from platform_attachments.attachments attachment "
                    "where owner_internal_user_id=%s and conversation_id=%s and state<>'deleted' "
                    "and not exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=attachment.attachment_id) "
                    "order by created_at,attachment_id",
                    (owner_id, conversation_id),
                ).fetchall()
            return tuple(
                self.attachment(owner_id, row["attachment_id"]) for row in rows
            )
        except (DownloadError, DownloadUnavailable):
            raise
        except Exception:  # noqa: BLE001 - database boundary is intentionally opaque
            raise DownloadNotFound() from None

    def list_current_artifacts(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[DownloadAsset, ...]:
        try:
            with self._connection() as connection:
                if (
                    connection.execute(
                        "select 1 from platform_control.conversations where conversation_id=%s "
                        "and owner_internal_user_id=%s",
                        (conversation_id, owner_id),
                    ).fetchone()
                    is None
                ):
                    raise DownloadNotFound()
                rows = connection.execute(
                    "select version.*,artifact.artifact_key,artifact.owner_internal_user_id,"
                    "artifact.conversation_id,upload.write_attempt_id from "
                    "platform_attachments.current_artifact_versions version join "
                    "platform_attachments.artifacts artifact using (artifact_id) join "
                    "platform_attachments.attachments attachment using (attachment_id) left join "
                    "platform_attachments.uploads upload using (attachment_id) where "
                    "artifact.owner_internal_user_id=%s and artifact.conversation_id=%s "
                    "and version.immutable_locator is not null and version.retained_until>now() "
                    "and not exists (select 1 from platform_attachments.erasure_jobs erasure "
                    "where erasure.attachment_id=version.attachment_id) "
                    "order by artifact.artifact_key,version.version_no,version.attachment_id",
                    (owner_id, conversation_id),
                ).fetchall()
            return tuple(self._asset_from_row(row) for row in rows)
        except DownloadError:
            raise
        except Exception:  # noqa: BLE001 - database boundary is intentionally opaque
            raise DownloadNotFound() from None

    def request_erasure(self, owner_id: UUID, attachment_id: UUID) -> None:
        job_id = uuid4()
        subject = f"attachment:{attachment_id}:erasure:{job_id}:reason"
        reason = self._content_codec.seal_json(subject, {"reason": "owner_requested"})
        digest = hashlib.sha256(b"owner_requested").digest()
        try:
            with self._connection() as connection:
                selected = connection.execute(
                    "select conversation_id from platform_attachments.attachments "
                    "where attachment_id=%s and owner_internal_user_id=%s",
                    (attachment_id, owner_id),
                ).fetchone()
                if selected is None:
                    raise DownloadNotFound()
                requested = connection.execute(
                    "select platform_attachments.request_attachment_erasure_v64("
                    "%s,%s,%s,%s,%s,%s,%s) as attachment_id",
                    (
                        attachment_id,
                        owner_id,
                        selected["conversation_id"],
                        job_id,
                        reason.ciphertext,
                        reason.key_version,
                        digest,
                    ),
                ).fetchone()
                if requested is None or requested["attachment_id"] is None:
                    raise DownloadConflict()
        except DownloadError:
            raise
        except Exception:  # noqa: BLE001 - database boundary is intentionally opaque
            raise DownloadConflict() from None

    def cancel_upload(self, owner_id: UUID, upload_id: UUID) -> None:
        job_id = uuid4()
        try:
            with self._connection() as connection:
                selected = connection.execute(
                    "select attachment_id,conversation_id "
                    "from platform_attachments.uploads "
                    "where upload_id=%s and owner_internal_user_id=%s",
                    (upload_id, owner_id),
                ).fetchone()
            if selected is None:
                raise DownloadNotFound()
            subject = (
                f"attachment:{selected['attachment_id']}:erasure:{job_id}:reason"
            )
            reason = self._content_codec.seal_json(
                subject, {"reason": "owner_requested"}
            )
            digest = hashlib.sha256(b"owner_requested").digest()
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_attachments.cancel_upload_v64("
                    "%s,%s,%s,%s,%s,%s,%s) as attachment_id",
                    (
                        upload_id,
                        owner_id,
                        selected["conversation_id"],
                        job_id,
                        reason.ciphertext,
                        reason.key_version,
                        digest,
                    ),
                ).fetchone()
            if row is None or row["attachment_id"] is None:
                raise DownloadNotFound()
        except DownloadError:
            raise
        except psycopg.errors.NoDataFound:
            raise DownloadNotFound() from None
        except Exception:  # noqa: BLE001 - database boundary is intentionally opaque
            raise DownloadConflict() from None


class S3ImmutableAttachmentStore:
    def __init__(self, client, bucket: str) -> None:
        if not isinstance(bucket, str) or not bucket:
            raise ValueError("attachment bucket invalid")
        self._client, self._bucket = client, bucket

    def __repr__(self) -> str:
        return "S3ImmutableAttachmentStore(client=<redacted>, bucket=<redacted>)"

    @classmethod
    def from_config(cls, config):
        import boto3
        from botocore.config import Config as BotoConfig

        from .object_writer import _credential

        client = boto3.client(
            "s3",
            endpoint_url=config.attachment_s3_endpoint,
            region_name="us-east-1",
            aws_access_key_id=_credential(config.attachment_s3_access_key_file),
            aws_secret_access_key=_credential(config.attachment_s3_secret_key_file),
            config=BotoConfig(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 2},
                connect_timeout=5,
                read_timeout=5,
            ),
        )
        return cls(client, config.attachment_s3_bucket)

    def stage_verified(self, asset: DownloadAsset, directory: str | Path) -> Path:
        target = Path(directory) / uuid4().hex
        request = {"Bucket": self._bucket, "Key": asset.object_ref}
        locator = asset.immutable_locator
        if locator is not None and locator.startswith("version:"):
            request["VersionId"] = locator.removeprefix("version:")
        elif locator is not None and locator.startswith("etag:"):
            request["IfMatch"] = locator.removeprefix("etag:")
        elif locator is not None or asset.derivative_kind is None:
            raise DownloadNotFound()
        body = None
        try:
            response = self._client.get_object(**request)
            body = response["Body"]
            size, digest = 0, hashlib.sha256()
            with target.open("xb") as output:
                while True:
                    chunk = body.read(_CHUNK_BYTES)
                    if not isinstance(chunk, bytes):
                        raise DownloadNotFound()
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > asset.size_bytes:
                        raise DownloadNotFound()
                    digest.update(chunk)
                    output.write(chunk)
            if size != asset.size_bytes or digest.digest() != asset.sha256:
                raise DownloadNotFound()
            return target
        except DownloadError:
            target.unlink(missing_ok=True)
            raise
        except Exception:  # noqa: BLE001 - storage boundary is intentionally opaque
            target.unlink(missing_ok=True)
            raise DownloadNotFound() from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:  # noqa: BLE001,S110 - close cannot change safe outcome
                    pass


class ConversationAttachmentDownloadService:
    def __init__(
        self,
        repository,
        store,
        *,
        ticket_secret: bytes,
        ticket_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(ticket_secret, bytes) or len(ticket_secret) != 32:
            raise ValueError("attachment ticket secret invalid")
        if (
            isinstance(ticket_seconds, bool)
            or not isinstance(ticket_seconds, int)
            or ticket_seconds <= 0
        ):
            raise ValueError("attachment ticket lifetime invalid")
        self._repository, self._store = repository, store
        self._ticket_key = hmac.digest(
            ticket_secret, b"conversation-attachment-ticket-v1", "sha256"
        )
        self._ticket_seconds = min(ticket_seconds, 300)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tickets: dict[bytes, _TicketClaim] = {}
        self._ticket_lock = threading.Lock()
        self._temporary_root: str | Path | None = None

    def __repr__(self) -> str:
        return "ConversationAttachmentDownloadService(repository=<redacted>, store=<redacted>, tickets=<redacted>)"

    def attachment(self, owner_id: UUID, attachment_id: UUID) -> AttachmentRecord:
        return self._repository.attachment(owner_id, attachment_id)

    def list_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> tuple[AttachmentRecord, ...]:
        return self._repository.list_conversation(owner_id, conversation_id)

    def cancel_upload(self, owner_id: UUID, upload_id: UUID) -> None:
        self._repository.cancel_upload(owner_id, upload_id)

    def issue_ticket(
        self, owner_id: UUID, attachment_id: UUID, purpose: str
    ) -> BrowserTicket:
        if purpose not in {"preview", "download"}:
            raise DownloadNotFound()
        self._repository.downloadable(owner_id, attachment_id, purpose)
        return self._build_ticket(owner_id, owner_id, attachment_id, purpose)

    def issue_review_ticket(
        self, actor_id: UUID, attachment_id: UUID, purpose: str
    ) -> BrowserTicket:
        resource_owner_id = self._repository.authorize_review(
            actor_id, attachment_id, purpose
        )
        self._repository.downloadable(resource_owner_id, attachment_id, purpose)
        return self._build_ticket(
            actor_id, resource_owner_id, attachment_id, purpose
        )

    def _build_ticket(
        self,
        owner_id: UUID,
        resource_owner_id: UUID,
        attachment_id: UUID,
        purpose: str,
    ) -> BrowserTicket:
        expires = self._clock() + timedelta(seconds=self._ticket_seconds)
        nonce, ticket_id = os.urandom(12), uuid4()
        payload = json.dumps(
            {
                "jti": str(ticket_id),
                "owner": str(owner_id),
                "attachment": str(attachment_id),
                "purpose": purpose,
                "exp": int(expires.timestamp()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        token = _urlsafe_encode(
            nonce
            + AESGCM(self._ticket_key).encrypt(nonce, payload, b"attachment-ticket-v1")
        )
        digest = hashlib.sha256(token.encode("ascii")).digest()
        with self._ticket_lock:
            now = self._clock()
            self._tickets = {
                key: claim
                for key, claim in self._tickets.items()
                if claim.expires_at > now
            }
            self._tickets[digest] = _TicketClaim(
                owner_id, resource_owner_id, attachment_id, purpose, expires
            )
        return BrowserTicket(token, expires, f"/api/v1/attachments/content/{token}")

    def _consume(self, owner_id: UUID, ticket: str) -> _TicketClaim:
        try:
            raw = _urlsafe_decode(ticket)
            if len(raw) < 29:
                raise ValueError
            payload = AESGCM(self._ticket_key).decrypt(
                raw[:12], raw[12:], b"attachment-ticket-v1"
            )
            document = json.loads(payload.decode("ascii"))
            if set(document) != {"jti", "owner", "attachment", "purpose", "exp"}:
                raise ValueError
            UUID(document["jti"])
            digest = hashlib.sha256(ticket.encode("ascii")).digest()
            with self._ticket_lock:
                now = self._clock()
                self._tickets = {
                    key: value
                    for key, value in self._tickets.items()
                    if value.expires_at > now
                }
                claim = self._tickets.get(digest)
                if claim is None:
                    raise ValueError
                valid = (
                    claim.owner_id == owner_id
                    and hmac.compare_digest(document["owner"], str(owner_id))
                    and hmac.compare_digest(
                        document["attachment"], str(claim.attachment_id)
                    )
                    and document["purpose"] == claim.purpose
                    and document["exp"] == int(claim.expires_at.timestamp())
                    and claim.expires_at > now
                )
                if not valid:
                    raise ValueError
                del self._tickets[digest]
            return claim
        except Exception:  # noqa: BLE001 - ticket parsing is intentionally opaque
            raise DownloadNotFound() from None

    @staticmethod
    def _stream_file(
        path: Path, directory: Path, *, start: int, length: int
    ) -> Iterator[bytes]:
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining:
                    chunk = source.read(min(_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise DownloadConflict()
                    remaining -= len(chunk)
                    yield chunk
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def open_content(
        self, owner_id: UUID, ticket: str, range_header: str | None
    ) -> OpenedDownload:
        claim = self._consume(owner_id, ticket)
        asset = self._repository.downloadable(
            claim.resource_owner_id, claim.attachment_id, claim.purpose
        )
        selected_range = _parse_range(range_header, asset.size_bytes)
        directory = Path(
            tempfile.mkdtemp(prefix="attachment-read-", dir=self._temporary_root)
        )
        try:
            staged = self._store.stage_verified(asset, directory)
        except Exception:  # noqa: BLE001 - storage boundary is intentionally opaque
            shutil.rmtree(directory, ignore_errors=True)
            raise DownloadNotFound() from None
        start, end = selected_range or (0, asset.size_bytes - 1)
        length = end - start + 1
        inline = claim.purpose == "preview" and asset.media_type in _INLINE_MIMES
        headers = {
            "Content-Disposition": _content_disposition(asset.display_name, inline),
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        status = 200
        if selected_range is not None:
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{asset.size_bytes}"
        if inline and asset.media_type == "application/pdf":
            headers["Content-Security-Policy"] = "sandbox"
        return OpenedDownload(
            self._stream_file(staged, directory, start=start, length=length),
            status,
            asset.media_type,
            headers,
        )

    def delete_attachment(self, owner_id: UUID, attachment_id: UUID) -> None:
        self._repository.request_erasure(owner_id, attachment_id)
        with self._ticket_lock:
            self._tickets = {
                digest: claim
                for digest, claim in self._tickets.items()
                if not (
                    claim.resource_owner_id == owner_id
                    and claim.attachment_id == attachment_id
                )
            }

    @staticmethod
    def _unique_archive_names(assets: tuple[DownloadAsset, ...]) -> tuple[str, ...]:
        used: set[str] = set()
        result: list[str] = []
        for asset in assets:
            base = _safe_name(asset.display_name)
            stem, suffix = os.path.splitext(base)
            candidate, ordinal = base, 1
            while candidate.casefold() in used:
                ordinal += 1
                candidate = f"{stem} ({ordinal}){suffix}"
            used.add(candidate.casefold())
            result.append(candidate)
        return tuple(result)

    def archive_conversation(
        self, owner_id: UUID, conversation_id: UUID
    ) -> OpenedDownload:
        assets = tuple(
            sorted(
                self._repository.list_current_artifacts(owner_id, conversation_id),
                key=lambda asset: (
                    asset.artifact_key or "",
                    asset.version_no or 0,
                    str(asset.attachment_id),
                ),
            )
        )
        if (
            len(assets) > MAX_TASK_OUTPUT_FILES
            or sum(asset.size_bytes for asset in assets) > MAX_TASK_OUTPUT_BYTES
        ):
            raise DownloadNotFound()
        directory = Path(
            tempfile.mkdtemp(prefix="attachment-archive-", dir=self._temporary_root)
        )
        archive_path = directory / "artifacts.zip"
        try:
            with zipfile.ZipFile(
                archive_path, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=False
            ) as archive:
                for asset, name in zip(
                    assets, self._unique_archive_names(assets), strict=True
                ):
                    staged = self._store.stage_verified(asset, directory)
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    with (
                        staged.open("rb") as source,
                        archive.open(info, "w", force_zip64=False) as output,
                    ):
                        while True:
                            chunk = source.read(_CHUNK_BYTES)
                            if not chunk:
                                break
                            output.write(chunk)
                    staged.unlink(missing_ok=True)
                    if archive_path.stat().st_size > MAX_TASK_OUTPUT_BYTES:
                        raise DownloadNotFound()
            size = archive_path.stat().st_size
            if size > MAX_TASK_OUTPUT_BYTES:
                raise DownloadNotFound()
        except Exception as error:
            shutil.rmtree(directory, ignore_errors=True)
            if isinstance(error, DownloadError):
                raise
            raise DownloadNotFound() from None
        return OpenedDownload(
            self._stream_file(archive_path, directory, start=0, length=size),
            200,
            "application/zip",
            {
                "Content-Disposition": _content_disposition("artifacts.zip", False),
                "Content-Length": str(size),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
