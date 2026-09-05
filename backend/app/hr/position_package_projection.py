from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.conversation_repository import message_subject
from app.execution_relay.content_crypto import ContentCryptoError, SealedContent

from .repository import HrConflict, HrNotFound, HrUnavailable
from .structured_output import extract_hr_envelope

logger = logging.getLogger(__name__)

_WORKER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ENVELOPE_MARKER = "<!-- platform-hr-v1:"
_OTHER_ENVELOPE_KINDS = ("candidate_match", "candidate_interview_plan")


class PositionPackageProjectionError(RuntimeError):
    pass


class PositionPackageProjectionUnavailable(PositionPackageProjectionError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedPositionPackage:
    projection_id: UUID
    projection_request_id: UUID
    owner_id: UUID
    draft_id: UUID
    conversation_id: UUID
    turn_id: UUID
    assistant_message_id: UUID
    agent_id: str
    content_ciphertext: bytes
    encryption_key_version: int

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, UUID)
                for value in (
                    self.projection_id,
                    self.projection_request_id,
                    self.owner_id,
                    self.draft_id,
                    self.conversation_id,
                    self.turn_id,
                    self.assistant_message_id,
                )
            )
            or self.agent_id != "hr-bot"
            or not isinstance(self.content_ciphertext, bytes)
            or not self.content_ciphertext
            or isinstance(self.encryption_key_version, bool)
            or not isinstance(self.encryption_key_version, int)
            or self.encryption_key_version < 1
        ):
            raise ValueError("position package projection claim invalid")


class PositionPackageProjectionRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise ValueError("position package projection database URL required")
        if not callable(connect):
            raise TypeError("position package projection connection required")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def claim(
        self, worker_id: str, lease_seconds: int
    ) -> ClaimedPositionPackage | None:
        _validate_runtime(worker_id, lease_seconds)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from "
                    "platform_hr.claim_position_package_projection_v76(%s,%s)",
                    (worker_id, lease_seconds),
                ).fetchone()
            if row is None:
                return None
            return ClaimedPositionPackage(
                projection_id=row["projection_id"],
                projection_request_id=row["projection_request_id"],
                owner_id=row["owner_internal_user_id"],
                draft_id=row["draft_id"],
                conversation_id=row["conversation_id"],
                turn_id=row["turn_id"],
                assistant_message_id=row["assistant_message_id"],
                agent_id=row["agent_id"],
                content_ciphertext=bytes(row["content_ciphertext"]),
                encryption_key_version=row["encryption_key_version"],
            )
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionPackageProjectionUnavailable(
                "position package projection claim unavailable"
            ) from None

    def _transition(
        self,
        function_name: str,
        claim: ClaimedPositionPackage,
        worker_id: str,
        value: UUID | str | None,
    ) -> None:
        if not isinstance(claim, ClaimedPositionPackage):
            raise TypeError("position package projection claim required")
        if not isinstance(worker_id, str) or _WORKER_ID.fullmatch(worker_id) is None:
            raise ValueError("position package projection worker invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    f"select platform_hr.{function_name}(%s,%s,%s,%s)",
                    (
                        claim.projection_id,
                        worker_id,
                        claim.projection_request_id,
                        value,
                    ),
                ).fetchone()
            if row is None or tuple(row.values()) != (True,):
                raise PositionPackageProjectionUnavailable(
                    "position package projection transition unavailable"
                )
        except PositionPackageProjectionError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error):
            raise PositionPackageProjectionUnavailable(
                "position package projection transition unavailable"
            ) from None

    def complete(
        self,
        claim: ClaimedPositionPackage,
        worker_id: str,
        draft_version_id: UUID | None,
    ) -> None:
        if draft_version_id is not None and not isinstance(draft_version_id, UUID):
            raise TypeError("position package draft version invalid")
        self._transition(
            "complete_position_package_projection_v76",
            claim,
            worker_id,
            draft_version_id,
        )

    def fail(
        self, claim: ClaimedPositionPackage, worker_id: str, error_code: str
    ) -> None:
        self._transition(
            "fail_position_package_projection_v76", claim, worker_id, error_code
        )

    def release(
        self, claim: ClaimedPositionPackage, worker_id: str, error_code: str
    ) -> None:
        self._transition(
            "release_position_package_projection_v76", claim, worker_id, error_code
        )


class PositionPackageProjector:
    def __init__(
        self,
        repository: object,
        positions: object,
        content_codec: object,
        *,
        worker_id: str,
        model_version: str,
        lease_seconds: int = 300,
    ) -> None:
        if any(
            not callable(getattr(repository, name, None))
            for name in ("claim", "complete", "fail", "release")
        ):
            raise ValueError("position package projection repository required")
        if not callable(getattr(positions, "create_draft_version", None)):
            raise TypeError("HR position service required")
        if not callable(getattr(content_codec, "unseal_json", None)):
            raise TypeError("position package content codec required")
        _validate_runtime(worker_id, lease_seconds)
        if (
            not isinstance(model_version, str)
            or not model_version.strip()
            or len(model_version.strip()) > 160
        ):
            raise ValueError("position package model version invalid")
        self._repository = repository
        self._positions = positions
        self._content_codec = content_codec
        self._worker_id = worker_id
        self._model_version = model_version.strip()
        self._lease_seconds = lease_seconds

    def _text(self, claim: ClaimedPositionPackage) -> str:
        value = self._content_codec.unseal_json(
            message_subject(claim.conversation_id, claim.assistant_message_id),
            SealedContent(
                claim.content_ciphertext,
                claim.encryption_key_version,
            ),
        )
        if set(value) != {"text"} or not isinstance(value["text"], str):
            raise ValueError("position package message invalid")
        text = value["text"]
        if not text.strip():
            raise ValueError("position package message invalid")
        return text

    @staticmethod
    def _package(text: str) -> tuple[str, dict[str, object]] | None:
        envelope = extract_hr_envelope(text, "position_package")
        if envelope is None:
            if any(
                extract_hr_envelope(text, kind) is not None
                for kind in _OTHER_ENVELOPE_KINDS
            ):
                return None
            if _ENVELOPE_MARKER in text:
                raise ValueError("position package envelope invalid")
            return None
        payload = envelope.payload
        title = payload.get("title")
        modules = payload.get("modules")
        if not isinstance(title, str) or not isinstance(modules, Mapping):
            raise ValueError("position package envelope invalid")
        return title, dict(modules)

    def _project(
        self,
        claim: ClaimedPositionPackage,
        title: str,
        modules: dict[str, object],
    ) -> UUID:
        version = self._positions.create_draft_version(
            owner_id=claim.owner_id,
            draft_id=claim.draft_id,
            request_id=claim.projection_request_id,
            title=title,
            modules=modules,
            source_conversation_id=claim.conversation_id,
            source_turn_id=claim.turn_id,
            source_assistant_message_id=claim.assistant_message_id,
            agent_id=claim.agent_id,
            model_version=self._model_version,
        )
        draft_version_id = getattr(version, "draft_version_id", None)
        if not isinstance(draft_version_id, UUID):
            raise TypeError("position package projection invalid")
        return draft_version_id

    def reconcile_one(self) -> bool:
        claim = self._repository.claim(self._worker_id, self._lease_seconds)
        if claim is None:
            return False
        if not isinstance(claim, ClaimedPositionPackage):
            raise PositionPackageProjectionUnavailable(
                "position package projection claim unavailable"
            )
        try:
            package = self._package(self._text(claim))
        except (ContentCryptoError, TypeError, ValueError):
            self._repository.fail(claim, self._worker_id, "envelope_invalid")
            return True
        if package is None:
            self._repository.complete(claim, self._worker_id, None)
            return True
        try:
            draft_version_id = self._project(claim, *package)
        except (HrConflict, HrNotFound):
            self._repository.fail(
                claim, self._worker_id, "projection_scope_invalid"
            )
            return True
        except ValueError:
            self._repository.fail(claim, self._worker_id, "envelope_invalid")
            return True
        except (HrUnavailable, TypeError):
            self._repository.release(
                claim, self._worker_id, "projection_unavailable"
            )
            return True
        self._repository.complete(claim, self._worker_id, draft_version_id)
        return True


def _validate_runtime(worker_id: str, lease_seconds: int) -> None:
    if (
        not isinstance(worker_id, str)
        or _WORKER_ID.fullmatch(worker_id) is None
        or isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 30 <= lease_seconds <= 900
    ):
        raise ValueError("position package projection runtime invalid")


async def position_package_projection_loop(
    projector: PositionPackageProjector,
    *,
    idle_seconds: float = 0.5,
) -> None:
    if not callable(getattr(projector, "reconcile_one", None)):
        raise TypeError("position package projector required")
    if (
        isinstance(idle_seconds, bool)
        or not isinstance(idle_seconds, (int, float))
        or idle_seconds <= 0
    ):
        raise ValueError("position package projection interval invalid")
    while True:
        try:
            changed = await asyncio.to_thread(projector.reconcile_one)
        except Exception:
            logger.exception("position package projection pass failed")
            await asyncio.sleep(idle_seconds)
            continue
        if not changed:
            await asyncio.sleep(idle_seconds)
