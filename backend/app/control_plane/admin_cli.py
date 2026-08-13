from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.local_secrets import read_secret_file

from .audit import (
    AppliedMutation,
    AuditCommand,
    AuditUnavailableError,
    AuditWriter,
    ControlCommitIndeterminateError,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
)
from .crypto import IdentityKeyring, ProviderIdentityCodec
from .dsn import validate_control_dsn


_OWNER_ROLES = frozenset(
    {"platform_control_owner", "platform_control_owner_preview"}
)
_DATABASES = frozenset(
    {"agent_platform_control", "agent_platform_control_preview"}
)
_STABLE_OPERATOR = re.compile(
    r"^(?:uid:[0-9]{1,10}|[a-z_][a-z0-9_.-]{0,31}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_-]{2,63}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_FIELDS = frozenset(
    {
        "operation_id",
        "action",
        "protected_target_lookup_hash",
        "protected_target_lookup_version",
        "generation_id",
        "backup_reference",
        "incident_reference",
        "approvers",
        "os_operator",
        "directory_generation_digest",
        "current_owner_internal_user_id",
        "current_owner_row_version",
        "target_row_version",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _safe_parent(path: Path, *, expected_uid: int, error: str) -> None:
    try:
        metadata = path.parent.stat()
    except OSError:
        raise ValueError(error) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ValueError(error)


def _open_private_file(
    path: Path, *, expected_uid: int, error: str
) -> tuple[int, os.stat_result]:
    _safe_parent(path, expected_uid=expected_uid, error=error)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise ValueError(error) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != expected_uid
        or (metadata.st_dev, metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
    ):
        os.close(descriptor)
        raise ValueError(error)
    return descriptor, metadata


def _read_private_json(
    path: Path, *, expected_uid: int, error: str
) -> tuple[dict[str, object], int, os.stat_result]:
    descriptor, metadata = _open_private_file(
        path, expected_uid=expected_uid, error=error
    )
    try:
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict):
            raise ValueError(error)
        return document, descriptor, metadata
    except (OSError, TypeError, json.JSONDecodeError):
        os.close(descriptor)
        raise ValueError(error) from None


def _receipt_key(path: Path, key_version: int, *, expected_uid: int) -> bytes:
    try:
        document, descriptor, _ = _read_private_json(
            path,
            expected_uid=expected_uid,
            error="confirmation receipt key invalid",
        )
        encoded = document["keys"][str(key_version)]
        key = base64.b64decode(encoded, validate=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("confirmation receipt key invalid") from None
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if document.get("purpose") != "offline-owner-receipt-hmac" or len(key) != 32:
        raise ValueError("confirmation receipt key invalid")
    return key


def _validated_receipt_payload(payload: dict[str, object]) -> dict[str, object]:
    if not isinstance(payload, dict) or frozenset(payload) != _RECEIPT_FIELDS:
        raise ValueError("confirmation receipt invalid")
    try:
        UUID(str(payload["operation_id"]))
        UUID(str(payload["generation_id"]))
        owner = payload["current_owner_internal_user_id"]
        if owner is not None:
            UUID(str(owner))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("confirmation receipt invalid") from None
    approvers = payload["approvers"]
    if (
        payload["action"] not in {"bind", "replace"}
        or not isinstance(approvers, list)
        or len(approvers) != 2
        or len(set(approvers)) != 2
        or any(
            not isinstance(value, str) or _STABLE_OPERATOR.fullmatch(value) is None
            for value in approvers
        )
        or not isinstance(payload["os_operator"], str)
        or _STABLE_OPERATOR.fullmatch(payload["os_operator"]) is None
        or not isinstance(payload["backup_reference"], str)
        or _REFERENCE.fullmatch(payload["backup_reference"]) is None
        or not isinstance(payload["incident_reference"], str)
        or _REFERENCE.fullmatch(payload["incident_reference"]) is None
        or not isinstance(payload["protected_target_lookup_hash"], str)
        or _HEX_64.fullmatch(payload["protected_target_lookup_hash"]) is None
        or not isinstance(payload["directory_generation_digest"], str)
        or _HEX_64.fullmatch(payload["directory_generation_digest"]) is None
    ):
        raise ValueError("confirmation receipt invalid")
    for key in (
        "protected_target_lookup_version",
        "current_owner_row_version",
        "target_row_version",
    ):
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("confirmation receipt invalid")
    if payload["action"] == "replace" and owner is None:
        raise ValueError("confirmation receipt invalid")
    if payload["action"] == "bind" and owner is not None:
        raise ValueError("confirmation receipt invalid")
    return payload


def write_confirmation_receipt(
    receipt_file: str | Path,
    key_file: str | Path,
    *,
    key_version: int,
    payload: dict[str, object],
    issued_at: datetime,
    expires_at: datetime,
    expected_uid: int = 0,
) -> dict[str, object]:
    selected = _validated_receipt_payload(dict(payload))
    if (
        not isinstance(key_version, int)
        or isinstance(key_version, bool)
        or key_version <= 0
        or issued_at.tzinfo is None
        or expires_at.tzinfo is None
        or expires_at <= issued_at
        or expires_at - issued_at > timedelta(minutes=15)
    ):
        raise ValueError("confirmation receipt invalid")
    key = _receipt_key(Path(key_file), key_version, expected_uid=expected_uid)
    payload_hash = hashlib.sha256(_canonical_json(selected)).hexdigest()
    authenticated = {
        "receipt_version": 1,
        "key_version": key_version,
        "payload_hash": payload_hash,
        "payload": selected,
        "issued_at": issued_at.astimezone(UTC).isoformat(),
        "expires_at": expires_at.astimezone(UTC).isoformat(),
    }
    document = {
        **authenticated,
        "mac": hmac.new(key, _canonical_json(authenticated), hashlib.sha256).hexdigest(),
    }
    path = Path(receipt_file)
    _safe_parent(
        path, expected_uid=expected_uid, error="confirmation receipt unavailable"
    )
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise ValueError("confirmation receipt unavailable") from None
    descriptor, _ = _open_private_file(
        path,
        expected_uid=expected_uid,
        error="confirmation receipt unavailable",
    )
    os.close(descriptor)
    return document


def consume_confirmation_receipt(
    receipt_file: str | Path,
    key_file: str | Path,
    *,
    expected_payload: dict[str, object],
    now: datetime,
    expected_uid: int = 0,
) -> dict[str, object]:
    path = Path(receipt_file)
    try:
        document, descriptor, metadata = _read_private_json(
            path,
            expected_uid=expected_uid,
            error="confirmation receipt unavailable",
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ValueError("confirmation receipt unavailable")
            key_version = document["key_version"]
            key = _receipt_key(Path(key_file), key_version, expected_uid=expected_uid)
            authenticated = {
                key_name: document[key_name]
                for key_name in (
                    "receipt_version",
                    "key_version",
                    "payload_hash",
                    "payload",
                    "issued_at",
                    "expires_at",
                )
            }
            expected_mac = hmac.new(
                key, _canonical_json(authenticated), hashlib.sha256
            ).hexdigest()
            selected = _validated_receipt_payload(dict(document["payload"]))
            expected = _validated_receipt_payload(dict(expected_payload))
            issued_at = datetime.fromisoformat(document["issued_at"])
            expires_at = datetime.fromisoformat(document["expires_at"])
            if (
                document["receipt_version"] != 1
                or not hmac.compare_digest(document["mac"], expected_mac)
                or document["payload_hash"]
                != hashlib.sha256(_canonical_json(selected)).hexdigest()
                or selected != expected
                or now.tzinfo is None
                or now < issued_at
                or now > expires_at
            ):
                raise ValueError("confirmation receipt invalid")
            consumed = path.with_name(
                f"{path.name}.consumed-{selected['operation_id']}"
            )
            if consumed.exists():
                raise ValueError("confirmation receipt unavailable")
            os.rename(path, consumed)
            return selected
        finally:
            os.close(descriptor)
    except ValueError:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("confirmation receipt invalid") from None


def read_confirmation_journal(
    journal_file: str | Path,
    key_file: str | Path,
    *,
    expected_uid: int = 0,
) -> dict[str, object]:
    path = Path(journal_file)
    try:
        document, descriptor, _ = _read_private_json(
            path,
            expected_uid=expected_uid,
            error="confirmation journal unavailable",
        )
        key = _receipt_key(
            Path(key_file), document["key_version"], expected_uid=expected_uid
        )
        authenticated = {
            key_name: document[key_name]
            for key_name in (
                "receipt_version", "key_version", "payload_hash", "payload",
                "issued_at", "expires_at",
            )
        }
        selected = _validated_receipt_payload(dict(document["payload"]))
        if (
            document["receipt_version"] != 1
            or not hmac.compare_digest(
                document["mac"],
                hmac.new(key, _canonical_json(authenticated), hashlib.sha256).hexdigest(),
            )
            or document["payload_hash"]
            != hashlib.sha256(_canonical_json(selected)).hexdigest()
            or path.name.rsplit(".consumed-", 1)[-1] != selected["operation_id"]
        ):
            raise ValueError("confirmation journal invalid")
        return selected
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("confirmation journal invalid") from None
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


@dataclass(frozen=True)
class AdminRequest:
    command: str
    approvers: tuple[str, ...] = ()

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> AdminRequest:
        approvers = tuple(
            value.strip()
            for value in getattr(namespace, "approver", ())
            if isinstance(value, str) and value.strip()
        )
        if namespace.command in {"bind-owner", "replace-owner"} and (
            len(approvers) != 2 or len(set(approvers)) != 2
        ):
            raise ValueError("two distinct approvers required")
        if any(_STABLE_OPERATOR.fullmatch(value) is None for value in approvers):
            raise ValueError("stable approver identity required")
        return cls(namespace.command, approvers)


class OfflineOwnerAdministrator:
    def __init__(
        self,
        migrator_database_url: str,
        *,
        owner_role: str,
        identity_codec: ProviderIdentityCodec,
        audit_writer: AuditWriter,
        connect=psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(migrator_database_url, purpose="migrator")
        if owner_role not in _OWNER_ROLES:
            raise ValueError("approved control owner role required")
        expected_owner = "platform_control_owner" + (
            "_preview" if parsed.environment == "preview" else ""
        )
        if owner_role != expected_owner:
            raise ValueError("control owner role environment mismatch")
        audit_environment = getattr(audit_writer, "environment", None)
        if audit_environment is not None and audit_environment != parsed.environment:
            raise ValueError("control and audit environment mismatch")
        self._database_url = migrator_database_url
        self.owner_role = owner_role
        self.identity_codec = identity_codec
        self.audit_writer = audit_writer
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    @staticmethod
    def _safe_database_error(error: psycopg.Error) -> ValueError:
        message = getattr(error.diag, "message_primary", "")
        allowed = {
            "owner already bound",
            "owner replacement precondition failed",
            "owner target precondition failed",
            "owner precondition unavailable",
            "matching audit intent required",
            "operation identity collision",
            "target unavailable in selected generation",
        }
        return ValueError(
            message if message in allowed else "offline owner administration failed"
        )

    def resolve_target(
        self,
        *,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
    ) -> UUID:
        candidates = self.identity_codec.lookup_candidates(subject_kind, provider_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.resolve_owner_binding_target("
                    "%s, %s, %s, %s) as internal_user_id",
                    (
                        generation_id,
                        subject_kind,
                        [version for version, _ in candidates],
                        [lookup for _, lookup in candidates],
                    ),
                ).fetchone()
                if row is None:
                    raise ValueError("target unavailable in selected generation")
                return row["internal_user_id"]
        except ValueError:
            raise
        except psycopg.Error as error:
            raise self._safe_database_error(error) from None

    def prepare_owner_change(
        self,
        *,
        action: str,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        os_operator: str,
        approvers: tuple[str, str],
        backup_reference: str,
        incident_reference: str,
        operation_id: UUID | None = None,
    ) -> dict[str, object]:
        if action not in {"bind", "replace"}:
            raise ValueError("owner operation invalid")
        if (
            len(approvers) != 2
            or len(set(approvers)) != 2
            or any(_STABLE_OPERATOR.fullmatch(value) is None for value in approvers)
            or _STABLE_OPERATOR.fullmatch(os_operator) is None
            or _REFERENCE.fullmatch(backup_reference) is None
            or _REFERENCE.fullmatch(incident_reference) is None
        ):
            raise ValueError("owner confirmation identity invalid")
        target = self.resolve_target(
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
        )
        try:
            with self._connection() as connection:
                precondition = connection.execute(
                    "select * from platform_control.owner_change_precondition(%s,%s)",
                    (generation_id, target),
                ).fetchone()
            if precondition is None:
                raise ValueError("owner precondition unavailable")
        except ValueError:
            raise
        except psycopg.Error as error:
            raise self._safe_database_error(error) from None
        owner_id = precondition["current_owner_internal_user_id"]
        if action == "bind" and owner_id is not None:
            raise ValueError("owner already bound")
        if action == "replace" and owner_id is None:
            raise ValueError("owner replacement precondition failed")
        return {
            "operation_id": str(operation_id or uuid4()),
            "action": action,
            "protected_target_lookup_hash": precondition[
                "protected_target_lookup_hash"
            ],
            "protected_target_lookup_version": precondition[
                "protected_target_lookup_version"
            ],
            "generation_id": str(generation_id),
            "backup_reference": backup_reference,
            "incident_reference": incident_reference,
            "approvers": list(approvers),
            "os_operator": os_operator,
            "directory_generation_digest": precondition[
                "directory_generation_digest"
            ],
            "current_owner_internal_user_id": str(owner_id) if owner_id else None,
            "current_owner_row_version": precondition["current_owner_row_version"],
            "target_row_version": precondition["target_row_version"],
        }

    def _change_owner(
        self,
        *,
        payload: dict[str, object],
        provider_id: str,
        subject_kind: str,
    ) -> dict[str, str]:
        selected = _validated_receipt_payload(dict(payload))
        operation = str(selected["action"])
        generation_id = UUID(str(selected["generation_id"]))
        correlation_id = UUID(str(selected["operation_id"]))
        target = self.resolve_target(
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
        )
        requested = self._owner_request(selected, target)

        def mutate(requested_event_id: UUID) -> AppliedMutation[dict[str, str]]:
            try:
                connection = self._connection()
            except psycopg.Error:
                raise ValueError("offline owner administration failed") from None
            try:
                try:
                    row = connection.execute(
                        "select platform_control.change_platform_owner_v2("
                        "%s,%s,%s,%s,%s,%s,%s,%s) as result",
                        (
                            correlation_id,
                            operation,
                            target,
                            generation_id,
                            selected["current_owner_internal_user_id"],
                            selected["current_owner_row_version"],
                            selected["target_row_version"],
                            requested_event_id,
                        ),
                    ).fetchone()
                except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as error:
                    connection.rollback()
                    raise self._safe_database_error(error) from None
                except psycopg.Error:
                    raise ControlCommitIndeterminateError(correlation_id) from None
                if row is None or not isinstance(row["result"], dict):
                    connection.rollback()
                    raise ValueError("offline owner administration failed")
                try:
                    connection.commit()
                except psycopg.Error:
                    raise ControlCommitIndeterminateError(correlation_id) from None
                applied = dict(row["result"])
                if operation == "bind":
                    for key in (
                        "previous_owner_internal_user_id",
                        "previous_owner_role",
                        "previous_owner_row_version",
                    ):
                        applied.pop(key, None)
                output = {
                    "status": "ok",
                    "operation": operation,
                    "internal_user_id": str(target),
                    "request_id": str(correlation_id),
                    "audit_event_id": str(requested_event_id),
                }
                return AppliedMutation(output, applied)
            finally:
                connection.close()

        return SensitiveMutationCoordinator(self.audit_writer).execute(
            requested=requested, mutate=mutate
        )

    @staticmethod
    def _owner_request(
        selected: dict[str, object], target: UUID
    ) -> AuditCommand:
        operation = str(selected["action"])
        generation_id = UUID(str(selected["generation_id"]))
        correlation_id = UUID(str(selected["operation_id"]))
        event_stem = "owner_binding" if operation == "bind" else "owner_replacement"
        metadata: dict[str, Any] = {
            "operation_id": str(correlation_id),
            "directory_generation_id": str(generation_id),
            "directory_generation_digest": selected["directory_generation_digest"],
            "protected_target_lookup_hash": selected[
                "protected_target_lookup_hash"
            ],
            "protected_target_lookup_version": selected[
                "protected_target_lookup_version"
            ],
            "os_operator": selected["os_operator"],
            "approver_a": selected["approvers"][0],
            "approver_b": selected["approvers"][1],
            "backup_reference": selected["backup_reference"],
            "incident_reference": selected["incident_reference"],
            "expected_owner_row_version": selected["current_owner_row_version"],
            "expected_target_row_version": selected["target_row_version"],
            "result": "requested",
        }
        if operation == "replace":
            metadata["previous_owner_internal_user_id"] = selected[
                "current_owner_internal_user_id"
            ]
        return AuditCommand(
            event_type=f"{event_stem}_requested",
            actor_internal_user_id=target,
            target_type="internal_user",
            target_id=str(target),
            request_id=correlation_id,
            reason=(
                "initial_owner_binding" if operation == "bind" else "owner_departure"
            ),
            metadata=metadata,
        )

    def reconcile_owner_change(
        self,
        *,
        payload: dict[str, object],
        provider_id: str,
        subject_kind: str,
    ) -> dict[str, str]:
        selected = _validated_receipt_payload(dict(payload))
        target = self.resolve_target(
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=UUID(str(selected["generation_id"])),
        )
        requested = self._owner_request(selected, target)
        requested_event_id = self.audit_writer.append(requested)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.reconcile_platform_owner_v2("
                    "%s,%s,%s,%s,%s,%s,%s,%s) as result",
                    (
                        selected["operation_id"], selected["action"], target,
                        selected["generation_id"],
                        selected["current_owner_internal_user_id"],
                        selected["current_owner_row_version"],
                        selected["target_row_version"], requested_event_id,
                    ),
                ).fetchone()
        except psycopg.Error as error:
            raise self._safe_database_error(error) from None
        applied = row["result"] if row else None
        if applied is None:
            self.audit_writer.append_outcome(
                requested, requested_event_id, error_code="control_unavailable"
            )
            return {
                "status": "not_committed",
                "operation": str(selected["action"]),
                "request_id": str(selected["operation_id"]),
            }
        actual = dict(applied)
        if selected["action"] == "bind":
            for key in (
                "previous_owner_internal_user_id",
                "previous_owner_role",
                "previous_owner_row_version",
            ):
                actual.pop(key, None)
        self.audit_writer.append_outcome(
            requested, requested_event_id, actual=actual
        )
        return {
            "status": "completed",
            "operation": str(selected["action"]),
            "request_id": str(selected["operation_id"]),
        }

    def bind_owner(
        self,
        *,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        os_operator: str,
        approvers: tuple[str, str],
        backup_reference: str,
        incident_reference: str,
        request_id: UUID | None = None,
    ) -> dict[str, str]:
        payload = self.prepare_owner_change(
            action="bind",
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
            os_operator=os_operator,
            approvers=approvers,
            backup_reference=backup_reference,
            incident_reference=incident_reference,
            operation_id=request_id,
        )
        return self._change_owner(
            payload=payload,
            provider_id=provider_id,
            subject_kind=subject_kind,
        )

    def replace_owner(
        self,
        *,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        os_operator: str,
        approvers: tuple[str, str],
        backup_reference: str,
        incident_reference: str,
        request_id: UUID | None = None,
    ) -> dict[str, str]:
        payload = self.prepare_owner_change(
            action="replace",
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
            os_operator=os_operator,
            approvers=approvers,
            backup_reference=backup_reference,
            incident_reference=incident_reference,
            operation_id=request_id,
        )
        return self._change_owner(
            payload=payload,
            provider_id=provider_id,
            subject_kind=subject_kind,
        )

    def show_directory_generation(self) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.show_directory_generation()"
                ).fetchone()
            if row is None:
                return {"status": "unavailable", "generation": None}
            return {
                "status": "ok",
                "generation": {
                    "generation_id": str(row["generation_id"]),
                    "status": row["status"],
                    "completed_at": row["completed_at"].isoformat(),
                    "is_active": row["is_active"],
                },
            }
        except psycopg.Error:
            raise ValueError("directory generation unavailable") from None


def _common_owner_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider-id-file", required=True)
    parser.add_argument("--subject-kind", default="employee")
    parser.add_argument("--generation-id", type=UUID, required=True)
    parser.add_argument("--incident-reference", required=True)
    parser.add_argument("--backup-reference", required=True)
    parser.add_argument("--approver", action="append", required=True)
    parser.add_argument("--receipt-file", required=True)
    parser.add_argument("--receipt-key-file", required=True)
    parser.add_argument("--receipt-key-version", type=int, required=True)
    parser.add_argument("--confirm", metavar="RECEIPT")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline platform owner administration")
    parser.add_argument("--database-url-file")
    parser.add_argument("--audit-database-url-file")
    parser.add_argument("--encryption-keyring-file")
    parser.add_argument("--hmac-keyring-file")
    parser.add_argument("--owner-role")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bind = subparsers.add_parser("bind-owner")
    _common_owner_arguments(bind)

    replace = subparsers.add_parser("replace-owner")
    _common_owner_arguments(replace)

    reconcile = subparsers.add_parser("reconcile-owner")
    reconcile.add_argument("--provider-id-file", required=True)
    reconcile.add_argument("--subject-kind", default="employee")
    reconcile.add_argument("--receipt-journal", required=True)
    reconcile.add_argument("--receipt-key-file", required=True)

    subparsers.add_parser("show-directory-generation")
    return parser


def render_result(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def render_error(error: Exception) -> str:
    payload: dict[str, str] = {"status": "error"}
    if isinstance(error, IndeterminateMutationError):
        payload.update(
            {
                "code": "management_mutation_indeterminate",
                "request_id": str(error.request_id),
                "audit_event_id": str(error.requested_audit_event_id),
            }
        )
    elif isinstance(error, AuditUnavailableError):
        payload["code"] = "required_audit_unavailable"
    elif isinstance(error, ValueError):
        payload.update({"code": "invalid_request", "detail": str(error)})
    else:
        payload["code"] = "offline_administration_failed"
    return render_result(payload)


def _required(value: str | None, environment_name: str) -> str:
    selected = value or os.getenv(environment_name, "")
    if not selected:
        raise ValueError(f"{environment_name.lower()} required")
    return selected


def _administrator(namespace: argparse.Namespace) -> OfflineOwnerAdministrator:
    database_file = _required(
        namespace.database_url_file,
        "PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE",
    )
    audit_file = _required(
        namespace.audit_database_url_file,
        "PLATFORM_CONTROL_AUDIT_DATABASE_URL_FILE",
    )
    encryption_file = _required(
        namespace.encryption_keyring_file,
        "PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE",
    )
    hmac_file = _required(
        namespace.hmac_keyring_file,
        "PLATFORM_IDENTITY_HMAC_KEYRING_FILE",
    )
    codec = ProviderIdentityCodec(
        IdentityKeyring.from_file(
            encryption_file,
            expected_purpose="provider-encryption",
            expected_key_length=32,
        ),
        IdentityKeyring.from_file(
            hmac_file,
            expected_purpose="provider-lookup-hmac",
            expected_key_length=32,
        ),
    )
    return OfflineOwnerAdministrator(
        read_secret_file(database_file),
        owner_role=_required(namespace.owner_role, "PLATFORM_CONTROL_OWNER_ROLE"),
        identity_codec=codec,
        audit_writer=AuditWriter.from_database_url(read_secret_file(audit_file)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    request = AdminRequest.from_namespace(namespace)
    administrator = _administrator(namespace)
    if namespace.command == "show-directory-generation":
        print(render_result(administrator.show_directory_generation()))
        return 0
    if os.geteuid() != 0:
        raise ValueError("offline owner administration requires root")

    provider_id = read_secret_file(namespace.provider_id_file)
    if namespace.command == "reconcile-owner":
        payload = read_confirmation_journal(
            namespace.receipt_journal,
            namespace.receipt_key_file,
            expected_uid=0,
        )
        print(render_result(administrator.reconcile_owner_change(
            payload=payload,
            provider_id=provider_id,
            subject_kind=namespace.subject_kind,
        )))
        return 0
    action = "bind" if namespace.command == "bind-owner" else "replace"
    receipt_path = Path(namespace.receipt_file)
    operation_id = None
    if namespace.confirm is not None:
        if Path(namespace.confirm) != receipt_path:
            raise ValueError("confirmation receipt invalid")
        try:
            document, descriptor, _ = _read_private_json(
                receipt_path,
                expected_uid=0,
                error="confirmation receipt invalid",
            )
            operation_id = UUID(str(document["payload"]["operation_id"]))
            os.close(descriptor)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("confirmation receipt invalid") from None
    payload = administrator.prepare_owner_change(
        action=action,
        provider_id=provider_id,
        subject_kind=namespace.subject_kind,
        generation_id=namespace.generation_id,
        os_operator=getpass.getuser(),
        approvers=request.approvers,
        backup_reference=namespace.backup_reference,
        incident_reference=namespace.incident_reference,
        operation_id=operation_id,
    )
    if namespace.confirm is None:
        issued_at = datetime.now(UTC)
        receipt = write_confirmation_receipt(
            receipt_path,
            namespace.receipt_key_file,
            key_version=namespace.receipt_key_version,
            payload=payload,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=15),
            expected_uid=0,
        )
        print(
            render_result(
                {
                    "status": "dry_run",
                    "operation": action,
                    "operation_id": payload["operation_id"],
                    "generation_id": str(namespace.generation_id),
                    "directory_generation_digest": payload[
                        "directory_generation_digest"
                    ],
                    "expires_at": receipt["expires_at"],
                    "receipt_created": True,
                }
            )
        )
        return 0
    confirmed = consume_confirmation_receipt(
        receipt_path,
        namespace.receipt_key_file,
        expected_payload=payload,
        now=datetime.now(UTC),
        expected_uid=0,
    )
    result = administrator._change_owner(
        payload=confirmed,
        provider_id=provider_id,
        subject_kind=namespace.subject_kind,
    )
    print(render_result(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(render_error(error))
        raise SystemExit(1) from None
