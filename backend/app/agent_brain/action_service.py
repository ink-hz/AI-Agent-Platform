from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from app.agent_brain.action_models import (
    ActionProjection,
    ActionProposal,
    stable_action_id,
)
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import (
    ContentCodec,
    ContentCryptoError,
    SealedContent,
)


class ActionCommandError(RuntimeError):
    pass


class ActionCommandDenied(ActionCommandError):
    pass


class ActionCommandConflict(ActionCommandError):
    pass


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _subject(action_id: UUID, field: str) -> str:
    return f"brain-action:{action_id}:{field}"


class ActionCommandService:
    def __init__(
        self,
        control_database_url: str,
        *,
        content_codec: ContentCodec,
        dsn_purpose: str,
        connect: Callable = psycopg.connect,
    ) -> None:
        validate_control_dsn(control_database_url, purpose=dsn_purpose)
        self._database_url = control_database_url
        self._codec = content_codec
        self._connect = connect

    def _connection(self):
        return self._connect(self._database_url, row_factory=dict_row)

    def propose(self, proposal: ActionProposal) -> ActionProjection:
        if not isinstance(proposal, ActionProposal):
            raise ValueError("action proposal invalid")
        summary = self._codec.seal_json(
            _subject(proposal.action_id, "summary"), {"text": proposal.summary}
        )
        impact = self._codec.seal_json(
            _subject(proposal.action_id, "impact"), {"text": proposal.impact}
        )
        parameters = self._codec.seal_json(
            _subject(proposal.action_id, "parameters"), proposal.parameters
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.propose_agent_task_action_v51("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        proposal.action_id,
                        proposal.platform_task_id,
                        proposal.action_seq,
                        proposal.action_kind,
                        summary.ciphertext,
                        summary.key_version,
                        hashlib.sha256(proposal.summary.encode()).digest(),
                        impact.ciphertext,
                        impact.key_version,
                        hashlib.sha256(proposal.impact.encode()).digest(),
                        parameters.ciphertext,
                        parameters.key_version,
                        hashlib.sha256(_json_bytes(proposal.parameters)).digest(),
                        bytes.fromhex(proposal.action_digest),
                        proposal.expires_at,
                        proposal.execution_timeout_seconds,
                    ),
                )
            return self.get(proposal.action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            raise ActionCommandConflict() from None
        except (ContentCryptoError, psycopg.Error, ValueError):
            raise ActionCommandError() from None

    def confirm(
        self, owner_id: UUID, action_id: UUID, digest_hex: str
    ) -> ActionProjection:
        if (
            not isinstance(owner_id, UUID)
            or not isinstance(action_id, UUID)
            or type(digest_hex) is not str
            or len(digest_hex) != 64
        ):
            raise ValueError("action confirmation invalid")
        try:
            digest = bytes.fromhex(digest_hex)
            delivery_id = uuid5(action_id, "execution")
            idempotency_key = f"action-execution:{action_id}"
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.confirm_agent_task_action_v51("
                    "%s,%s,%s,%s,%s)",
                    (owner_id, action_id, digest, delivery_id, idempotency_key),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            raise ActionCommandConflict() from None
        except (ValueError, psycopg.Error):
            raise ActionCommandError() from None

    def reject(self, owner_id: UUID, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.reject_agent_task_action_v51(%s,%s)",
                    (owner_id, action_id),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.errors.InsufficientPrivilege:
            raise ActionCommandDenied() from None
        except psycopg.errors.CheckViolation:
            raise ActionCommandConflict() from None
        except psycopg.Error:
            raise ActionCommandError() from None

    def supersede(self, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_brain.supersede_agent_task_action_v51(%s)",
                    (action_id,),
                )
                connection.execute(
                    "select platform_brain.resume_action_resolution_v51(%s)",
                    (action_id,),
                )
            return self.get(action_id)
        except psycopg.Error:
            raise ActionCommandError() from None

    def get(self, action_id: UUID) -> ActionProjection:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_brain.agent_task_actions where action_id=%s",
                    (action_id,),
                ).fetchone()
            if row is None:
                raise ActionCommandConflict()
            summary = self._codec.unseal_json(
                _subject(action_id, "summary"),
                SealedContent(bytes(row["summary_ciphertext"]), row["summary_key_version"]),
            )["text"]
            impact = self._codec.unseal_json(
                _subject(action_id, "impact"),
                SealedContent(bytes(row["impact_ciphertext"]), row["impact_key_version"]),
            )["text"]
            return ActionProjection(
                action_id=row["action_id"],
                task_id=row["task_id"],
                action_seq=row["action_seq"],
                action_kind=row["action_kind"],
                summary=summary,
                impact=impact,
                action_digest=bytes(row["action_digest"]).hex(),
                status=row["status"],
                expires_at=row["expires_at"],
                execution_status=row["execution_status"],
                confirmed_by_internal_user_id=row["confirmed_by_internal_user_id"],
                confirmed_at=row["confirmed_at"],
                execution_deadline_at=row["execution_deadline_at"],
            )
        except ActionCommandConflict:
            raise
        except (ContentCryptoError, psycopg.Error, ValueError, TypeError):
            raise ActionCommandError() from None


__all__ = [
    "ActionCommandConflict",
    "ActionCommandDenied",
    "ActionCommandError",
    "ActionCommandService",
    "stable_action_id",
]
