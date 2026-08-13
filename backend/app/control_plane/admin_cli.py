from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.local_secrets import read_secret_file

from .audit import (
    AuditCommand,
    AuditUnavailableError,
    AuditWriter,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
)
from .crypto import IdentityKeyring, ProviderIdentityCodec


_OWNER_ROLES = frozenset(
    {"platform_control_owner", "platform_control_owner_preview"}
)
_DATABASES = frozenset(
    {"agent_platform_control", "agent_platform_control_preview"}
)


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
        if namespace.command == "replace-owner" and (
            len(approvers) != 2 or len(set(approvers)) != 2
        ):
            raise ValueError("two distinct approvers required")
        return cls(namespace.command, approvers)


def _known_database_url(database_url: str) -> None:
    try:
        database = conninfo_to_dict(database_url).get("dbname")
    except (TypeError, ValueError, psycopg.Error):
        raise ValueError("control migrator database DSN required") from None
    if database not in _DATABASES:
        raise ValueError("control migrator database DSN required")


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
        _known_database_url(migrator_database_url)
        if owner_role not in _OWNER_ROLES:
            raise ValueError("approved control owner role required")
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
            "active owner already bound",
            "active owner unavailable",
            "owner role change invalid",
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

    def _change_owner(
        self,
        *,
        operation: str,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        reason: str,
        os_operator: str,
        approvers: tuple[str, str] | tuple[()] = (),
        request_id: UUID | None = None,
    ) -> dict[str, str]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("incident reason required")
        if not isinstance(os_operator, str) or not os_operator.strip():
            raise ValueError("OS operator required")
        target = self.resolve_target(
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
        )
        correlation_id = request_id or uuid4()
        event_stem = "owner_binding" if operation == "bind" else "owner_replacement"
        metadata: dict[str, str] = {
            "directory_generation_id": str(generation_id),
            "operation": operation,
            "os_operator": os_operator.strip(),
            "result": "requested",
            "role": "platform_owner",
        }
        if approvers:
            metadata.update(
                {"approver_a": approvers[0], "approver_b": approvers[1]}
            )
        requested = AuditCommand(
            event_type=f"{event_stem}_requested",
            actor_internal_user_id=target,
            target_type="internal_user",
            target_id=str(target),
            request_id=correlation_id,
            reason=reason.strip(),
            metadata=metadata,
        )

        def mutate(requested_event_id: UUID) -> dict[str, str]:
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "select platform_control.change_platform_owner("
                        "%s, %s, %s, %s) as internal_user_id",
                        (operation, target, generation_id, requested_event_id),
                    ).fetchone()
                    if row is None or row["internal_user_id"] != target:
                        raise ValueError("offline owner administration failed")
                return {
                    "status": "ok",
                    "operation": operation,
                    "internal_user_id": str(target),
                    "request_id": str(correlation_id),
                    "audit_event_id": str(requested_event_id),
                }
            except ValueError:
                raise
            except psycopg.Error as error:
                raise self._safe_database_error(error) from None

        return SensitiveMutationCoordinator(self.audit_writer).execute(
            requested=requested, mutate=mutate
        )

    def bind_owner(
        self,
        *,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        reason: str,
        os_operator: str,
        request_id: UUID | None = None,
    ) -> dict[str, str]:
        return self._change_owner(
            operation="bind",
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
            reason=reason,
            os_operator=os_operator,
            request_id=request_id,
        )

    def replace_owner(
        self,
        *,
        provider_id: str,
        subject_kind: str,
        generation_id: UUID,
        reason: str,
        os_operator: str,
        approvers: tuple[str, str],
        backup_confirmed: bool,
        confirmed: bool,
        request_id: UUID | None = None,
    ) -> dict[str, str]:
        if (
            len(approvers) != 2
            or len(set(approvers)) != 2
            or any(not value.strip() for value in approvers)
        ):
            raise ValueError("two distinct approvers required")
        if not backup_confirmed:
            raise ValueError("database backup confirmation required")
        if not confirmed:
            raise ValueError("separate confirmation required")
        return self._change_owner(
            operation="replace",
            provider_id=provider_id,
            subject_kind=subject_kind,
            generation_id=generation_id,
            reason=reason,
            os_operator=os_operator,
            approvers=approvers,
            request_id=request_id,
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
    parser.add_argument("--reason", required=True)
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--confirm", action="store_true")


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
    replace.add_argument("--approver", action="append", required=True)
    replace.add_argument("--backup-confirmed", action="store_true")

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

    provider_id = read_secret_file(namespace.provider_id_file)
    target = administrator.resolve_target(
        provider_id=provider_id,
        subject_kind=namespace.subject_kind,
        generation_id=namespace.generation_id,
    )
    if not namespace.confirm:
        print(
            render_result(
                {
                    "status": "dry_run",
                    "operation": namespace.command,
                    "internal_user_id": str(target),
                    "generation_id": str(namespace.generation_id),
                }
            )
        )
        return 0

    common = {
        "provider_id": provider_id,
        "subject_kind": namespace.subject_kind,
        "generation_id": namespace.generation_id,
        "reason": namespace.reason,
        "os_operator": getpass.getuser(),
        "request_id": namespace.request_id,
    }
    if namespace.command == "bind-owner":
        result = administrator.bind_owner(**common)
    else:
        result = administrator.replace_owner(
            **common,
            approvers=request.approvers,
            backup_confirmed=namespace.backup_confirmed,
            confirmed=True,
        )
    print(render_result(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(render_error(error))
        raise SystemExit(1) from None
