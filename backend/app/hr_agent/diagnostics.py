"""Opt-in encrypted diagnostics with trusted-role and expiry checks."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.execution_relay.content_crypto import SealedContent

from .types import DiagnosticIdentity, DiagnosticRecord


class DiagnosticAccessError(RuntimeError):
    pass


class DiagnosticCodec(Protocol):
    def seal_json(self, subject: str, value: dict[str, object]): ...
    def unseal_json(self, subject: str, sealed): ...


class DiagnosticStore:
    def __init__(
        self,
        root: Path,
        codec: DiagnosticCodec,
        *,
        enabled: bool = False,
        trusted_roles: tuple[str, ...] = (),
        max_ttl_seconds: int = 0,
        audit: Callable[[str, UUID, UUID], None] | None = None,
    ) -> None:
        self._root = root
        self._codec = codec
        self._enabled = enabled
        self._roles = frozenset(trusted_roles)
        self._max_ttl = max_ttl_seconds
        self._audit = audit or (lambda _action, _actor, _diagnostic: None)
        self._records: dict[UUID, DiagnosticRecord] = {}
        if enabled:
            if (
                not root.is_absolute()
                or root.is_symlink()
                or not self._roles
                or max_ttl_seconds <= 0
            ):
                raise ValueError("invalid diagnostic configuration")
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.chmod(0o700)
            self._load_records()

    def create(
        self,
        actor: DiagnosticIdentity,
        work_id: UUID,
        payload: dict[str, object],
        *,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> DiagnosticRecord:
        self._authorize(actor)
        if (
            not isinstance(work_id, UUID)
            or not isinstance(payload, dict)
            or not 0 < ttl_seconds <= self._max_ttl
        ):
            raise DiagnosticAccessError("diagnostic denied")
        created = now or datetime.now(timezone.utc)
        if created.tzinfo is None:
            raise DiagnosticAccessError("diagnostic denied")
        diagnostic_id = uuid4()
        expires = created + timedelta(seconds=ttl_seconds)
        subject = self._subject(diagnostic_id)
        sealed = self._codec.seal_json(subject, payload)
        record = DiagnosticRecord(diagnostic_id, work_id, expires, sealed)
        self._records[diagnostic_id] = record
        self._persist_metadata(record)
        self._audit("create", actor.actor_id, diagnostic_id)
        return record

    def read(
        self,
        actor: DiagnosticIdentity,
        diagnostic_id: UUID,
        *,
        now: datetime | None = None,
    ) -> DiagnosticRecord:
        self._authorize(actor)
        if not isinstance(diagnostic_id, UUID):
            raise DiagnosticAccessError("diagnostic denied")
        record = self._records.get(diagnostic_id)
        current = now or datetime.now(timezone.utc)
        if record is None or current >= record.expires_at:
            raise DiagnosticAccessError("diagnostic unavailable")
        try:
            payload = self._codec.unseal_json(
                self._subject(diagnostic_id), record.sealed_payload
            )
        except (RuntimeError, TypeError, ValueError, OSError):
            raise DiagnosticAccessError("diagnostic unavailable") from None
        self._audit("read", actor.actor_id, diagnostic_id)
        return DiagnosticRecord(
            record.diagnostic_id, record.work_id, record.expires_at, payload
        )

    def delete(self, actor: DiagnosticIdentity, diagnostic_id: UUID) -> None:
        self._authorize(actor)
        if not isinstance(diagnostic_id, UUID):
            raise DiagnosticAccessError("diagnostic denied")
        self._records.pop(diagnostic_id, None)
        path = self._root / f"{diagnostic_id}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise DiagnosticAccessError("diagnostic unavailable") from None
        self._audit("delete", actor.actor_id, diagnostic_id)

    def cleanup(self, actor: DiagnosticIdentity, *, now: datetime | None = None) -> int:
        self._authorize(actor)
        current = now or datetime.now(timezone.utc)
        expired = [
            key for key, record in self._records.items() if current >= record.expires_at
        ]
        for diagnostic_id in expired:
            self.delete(actor, diagnostic_id)
        return len(expired)

    def _authorize(self, actor: DiagnosticIdentity) -> None:
        if (
            not self._enabled
            or not isinstance(actor, DiagnosticIdentity)
            or not self._roles.intersection(actor.roles)
        ):
            raise DiagnosticAccessError("diagnostic denied")

    @staticmethod
    def _subject(diagnostic_id: UUID) -> str:
        return f"hr-agent:diagnostic:{diagnostic_id}"

    def _persist_metadata(self, record: DiagnosticRecord) -> None:
        path = self._root / f"{record.diagnostic_id}.json"
        sealed = record.sealed_payload
        if isinstance(sealed, SealedContent):
            stored = {
                "kind": "content_codec",
                "ciphertext": base64.b64encode(sealed.ciphertext).decode("ascii"),
                "key_version": sealed.key_version,
            }
        elif (
            isinstance(sealed, tuple)
            and len(sealed) == 2
            and isinstance(sealed[0], str)
            and isinstance(sealed[1], bytes)
        ):
            stored = {
                "kind": "test_codec",
                "subject": sealed[0],
                "ciphertext": base64.b64encode(sealed[1]).decode("ascii"),
            }
        else:
            raise DiagnosticAccessError("diagnostic unavailable")
        data = json.dumps(
            {
                "work_id": str(record.work_id),
                "expires_at": record.expires_at.isoformat(),
                "sealed": stored,
            },
            separators=(",", ":"),
        ).encode()
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)

    def _load_records(self) -> None:
        for path in self._root.glob("*.json"):
            try:
                diagnostic_id = UUID(path.stem)
                raw = json.loads(path.read_text(encoding="utf-8"))
                work_id = UUID(raw["work_id"])
                expires_at = datetime.fromisoformat(raw["expires_at"])
                stored = raw["sealed"]
                ciphertext = base64.b64decode(stored["ciphertext"], validate=True)
                if stored["kind"] == "content_codec":
                    sealed = SealedContent(ciphertext, int(stored["key_version"]))
                elif stored["kind"] == "test_codec":
                    sealed = (stored["subject"], ciphertext)
                else:
                    continue
                self._records[diagnostic_id] = DiagnosticRecord(
                    diagnostic_id, work_id, expires_at, sealed
                )
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                # Corrupt records remain inaccessible and are never logged.
                continue
