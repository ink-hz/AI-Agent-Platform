from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import json
import re
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row

from .dsn import validate_control_dsn


_AUDIT_EVENT_NAMESPACE = UUID("8fabf404-553e-4e15-bdd8-c744c05e1f5a")
_AUDIT_REQUEST_LOCK_NAMESPACE = UUID("850d125b-dad0-491f-b84e-cfa340ed2f73")
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OS_IDENTITY = re.compile(
    r"^(?:uid:[0-9]{1,10}|[a-z_][a-z0-9_.-]{0,31}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_-]{2,63}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODES = frozenset(
    {
        "business_rejected",
        "control_unavailable",
        "provider_probe_failed",
        "activation_failed",
    }
)
_ROLES = frozenset(
    {"member", "management_viewer", "platform_admin", "platform_owner"}
)
_RESULTS = frozenset({"requested", "completed", "failed"})
_UUID_KEYS = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "directory_generation_id",
        "previous_owner_internal_user_id",
        "new_owner_internal_user_id",
    }
)
_COUNT_KEYS = frozenset(
    {
        "expected_row_version",
        "expected_user_row_version",
        "expected_scope_row_version",
        "expected_owner_row_version",
        "expected_target_row_version",
        "row_version",
        "session_revocation_count",
        "previous_owner_row_version",
        "new_owner_row_version",
        "protected_target_lookup_version",
        "item_count",
        "previous_scope_count",
        "new_scope_count",
    }
)
_BOOLEAN_KEYS = frozenset({"before_scope", "after_scope"})
_ROLE_KEYS = frozenset(
    {"previous_role", "new_role", "previous_owner_role", "new_owner_role"}
)
_SCOPE_KEYS = frozenset({"previous_scopes", "new_scopes"})
_KNOWN_METADATA_KEYS = frozenset(
    {
        *_UUID_KEYS,
        *_COUNT_KEYS,
        *_BOOLEAN_KEYS,
        *_ROLE_KEYS,
        *_SCOPE_KEYS,
        "result",
        "agent_id",
        "os_operator",
        "approver_a",
        "approver_b",
        "backup_reference",
        "incident_reference",
        "directory_generation_digest",
        "protected_target_lookup_hash",
        "error_code",
        "previous_scope_sha256",
        "new_scope_sha256",
        "previous_manifest_sha256",
        "new_manifest_sha256",
        "previous_prompt_sha256",
        "new_prompt_sha256",
        "sanitized_result",
    }
)

_VIEWER_REQUEST = frozenset(
    {
        "operation_id",
        "previous_role",
        "new_role",
        "expected_row_version",
        "result",
    }
)
_VIEWER_COMPLETED = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "previous_role",
        "new_role",
        "row_version",
        "session_revocation_count",
        "previous_scopes",
        "new_scopes",
        "result",
    }
)
_VIEWER_COMPLETED_SUMMARY = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "previous_role",
        "new_role",
        "row_version",
        "session_revocation_count",
        "previous_scope_count",
        "previous_scope_sha256",
        "new_scope_count",
        "new_scope_sha256",
        "result",
    }
)
_SCOPE_REQUEST = frozenset(
    {
        "operation_id",
        "agent_id",
        "expected_user_row_version",
        "expected_scope_row_version",
        "result",
    }
)
_SCOPE_COMPLETED = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "agent_id",
        "before_scope",
        "after_scope",
        "row_version",
        "previous_scopes",
        "new_scopes",
        "result",
    }
)
_OWNER_COMMON_REQUEST = frozenset(
    {
        "operation_id",
        "directory_generation_id",
        "directory_generation_digest",
        "protected_target_lookup_hash",
        "protected_target_lookup_version",
        "os_operator",
        "approver_a",
        "approver_b",
        "backup_reference",
        "incident_reference",
        "expected_owner_row_version",
        "expected_target_row_version",
        "result",
    }
)
_OWNER_COMPLETED = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "previous_owner_internal_user_id",
        "new_owner_internal_user_id",
        "previous_owner_role",
        "new_owner_role",
        "session_revocation_count",
        "previous_owner_row_version",
        "new_owner_row_version",
        "result",
    }
)
_OWNER_BINDING_COMPLETED = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "new_owner_internal_user_id",
        "new_owner_role",
        "session_revocation_count",
        "new_owner_row_version",
        "result",
    }
)
_READ_REQUEST = frozenset({"operation_id", "result"})
_READ_COMPLETED = frozenset(
    {"operation_id", "linked_audit_event_id", "item_count", "result"}
)
_DETAIL_READ_COMPLETED = frozenset(
    {"operation_id", "linked_audit_event_id", "result"}
)
_FAILED = frozenset(
    {"operation_id", "linked_audit_event_id", "error_code", "result"}
)
_BRAIN_CONFIG_HASHES = frozenset(
    {
        "previous_manifest_sha256",
        "new_manifest_sha256",
        "previous_prompt_sha256",
        "new_prompt_sha256",
    }
)
_BRAIN_CONFIG_REQUEST = frozenset({"operation_id", "result"}) | _BRAIN_CONFIG_HASHES
_BRAIN_CONFIG_COMPLETED = frozenset(
    {"operation_id", "linked_audit_event_id", "sanitized_result", "result"}
) | _BRAIN_CONFIG_HASHES
_BRAIN_CONFIG_FAILED = frozenset(
    {
        "operation_id",
        "linked_audit_event_id",
        "error_code",
        "sanitized_result",
        "result",
    }
) | _BRAIN_CONFIG_HASHES

_EVENT_SCHEMAS: dict[str, tuple[frozenset[str], ...]] = {}
_EVENT_REASON: dict[str, str] = {}
_EVENT_TARGET: dict[str, str] = {}
_EVENT_TRANSITION: dict[str, tuple[str, str]] = {}


def _register_events(
    stems: Sequence[str],
    *,
    reason: str,
    target: str,
    requested: frozenset[str],
    completed: frozenset[str],
    failed: frozenset[str] = _FAILED,
) -> None:
    for stem in stems:
        for result, schema in (
            ("requested", requested),
            ("completed", completed),
            ("failed", failed),
        ):
            event_type = f"{stem}_{result}"
            _EVENT_SCHEMAS[event_type] = (schema,)
            _EVENT_REASON[event_type] = reason
            _EVENT_TARGET[event_type] = target


_register_events(
    ("viewer_role_assignment",),
    reason="access_approved",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
_register_events(
    ("viewer_role_revocation",),
    reason="access_revoked",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
_register_events(
    ("admin_role_assignment",),
    reason="admin_access_approved",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
_register_events(
    ("admin_role_revocation",),
    reason="admin_access_revoked",
    target="internal_user",
    requested=_VIEWER_REQUEST,
    completed=_VIEWER_COMPLETED,
)
for _stem, _transition in (
    ("admin_role_assignment", ("member", "platform_admin")),
    ("admin_role_revocation", ("platform_admin", "member")),
):
    for _result in ("requested", "completed"):
        _EVENT_TRANSITION[f"{_stem}_{_result}"] = _transition
_EVENT_SCHEMAS["viewer_role_revocation_completed"] = (
    _VIEWER_COMPLETED,
    _VIEWER_COMPLETED_SUMMARY,
)
_register_events(
    ("observation_scope_assignment",),
    reason="scope_approved",
    target="agent_observation_scope",
    requested=_SCOPE_REQUEST,
    completed=_SCOPE_COMPLETED,
)
_register_events(
    ("observation_scope_revocation",),
    reason="scope_revoked",
    target="agent_observation_scope",
    requested=_SCOPE_REQUEST,
    completed=_SCOPE_COMPLETED,
)
_register_events(
    ("owner_binding",),
    reason="initial_owner_binding",
    target="internal_user",
    requested=_OWNER_COMMON_REQUEST,
    completed=_OWNER_BINDING_COMPLETED,
)
_register_events(
    ("owner_replacement",),
    reason="owner_departure",
    target="internal_user",
    requested=_OWNER_COMMON_REQUEST | {"previous_owner_internal_user_id"},
    completed=_OWNER_COMPLETED,
)
_register_events(
    ("management_user_list_read",),
    reason="privileged_read",
    target="management_user_directory",
    requested=_READ_REQUEST,
    completed=_READ_COMPLETED,
)
_register_events(
    ("brain_model_configuration_change",),
    reason="model_configuration_change",
    target="brain_model_configuration",
    requested=_BRAIN_CONFIG_REQUEST,
    completed=_BRAIN_CONFIG_COMPLETED,
    failed=_BRAIN_CONFIG_FAILED,
)
_register_events(
    ("governance_audit_read",),
    reason="privileged_read",
    target="governance_audit",
    requested=_READ_REQUEST,
    completed=_READ_COMPLETED,
)
_register_events(
    ("fae_session_detail_read",),
    reason="privileged_read",
    target="fae_session",
    requested=_READ_REQUEST,
    completed=_DETAIL_READ_COMPLETED,
)

AuditScalar = str | int | bool
AuditValue = AuditScalar | Sequence[str]
T = TypeVar("T")


class AuditUnavailableError(RuntimeError):
    """A required immutable audit event could not be persisted."""


class ControlCommitIndeterminateError(RuntimeError):
    """The server result of a control transaction commit is unknown."""

    def __init__(self, request_id: UUID) -> None:
        super().__init__("control mutation commit outcome indeterminate")
        self.request_id = request_id


class IndeterminateMutationError(AuditUnavailableError):
    """A mutation result cannot safely be described as failed or completed."""

    def __init__(self, request_id: UUID, requested_audit_event_id: UUID) -> None:
        super().__init__("management mutation outcome indeterminate")
        self.request_id = request_id
        self.requested_audit_event_id = requested_audit_event_id


@dataclass(frozen=True)
class AuditCommand:
    event_type: str
    actor_internal_user_id: UUID
    target_type: str
    target_id: str
    request_id: UUID
    reason: str
    metadata: Mapping[str, AuditValue]


@dataclass(frozen=True)
class AppliedMutation(Generic[T]):
    value: T
    outcome_metadata: Mapping[str, AuditValue]


def _uuid_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def _safe_metadata_value(key: str, value: Any) -> bool:
    if key in _UUID_KEYS:
        return _uuid_string(value)
    if key in _COUNT_KEYS:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if key in _BOOLEAN_KEYS:
        return isinstance(value, bool)
    if key in _ROLE_KEYS:
        return isinstance(value, str) and value in _ROLES
    if key == "result":
        return isinstance(value, str) and value in _RESULTS
    if key == "agent_id":
        return isinstance(value, str) and _AGENT_ID.fullmatch(value) is not None
    if key in {"os_operator", "approver_a", "approver_b"}:
        return isinstance(value, str) and _OS_IDENTITY.fullmatch(value) is not None
    if key in {"backup_reference", "incident_reference"}:
        return isinstance(value, str) and _REFERENCE.fullmatch(value) is not None
    if key in {
        "directory_generation_digest",
        "protected_target_lookup_hash",
        "previous_scope_sha256",
        "new_scope_sha256",
        "previous_manifest_sha256",
        "new_manifest_sha256",
        "previous_prompt_sha256",
        "new_prompt_sha256",
    }:
        return isinstance(value, str) and _HEX_64.fullmatch(value) is not None
    if key == "sanitized_result":
        return isinstance(value, str) and value in {
            "activated",
            "unchanged",
            "probe_rejected",
            "activation_rejected",
        }
    if key == "error_code":
        return isinstance(value, str) and value in _ERROR_CODES
    if key in _SCOPE_KEYS:
        return (
            isinstance(value, (list, tuple))
            and len(value) <= 256
            and all(
                isinstance(item, str) and _AGENT_ID.fullmatch(item) is not None
                for item in value
            )
            and list(value) == sorted(set(value))
        )
    return False


def sanitize_governance_metadata(
    metadata: Mapping[str, AuditValue],
    *,
    event_type: str | None = None,
) -> dict[str, AuditValue]:
    if not isinstance(metadata, Mapping):
        raise ValueError("audit metadata invalid")
    keys = frozenset(metadata)
    if not keys <= _KNOWN_METADATA_KEYS:
        raise ValueError("audit metadata invalid")
    if event_type is not None and keys not in _EVENT_SCHEMAS.get(event_type, ()):
        raise ValueError("audit metadata invalid")
    sanitized: dict[str, AuditValue] = {}
    for key in sorted(metadata):
        value = metadata[key]
        if not _safe_metadata_value(key, value):
            raise ValueError("audit metadata invalid")
        sanitized[key] = list(value) if key in _SCOPE_KEYS else value
    transition = _EVENT_TRANSITION.get(event_type or "")
    if transition is not None and (
        sanitized.get("previous_role"), sanitized.get("new_role")
    ) != transition:
        raise ValueError("audit metadata invalid")
    return sanitized


def project_governance_metadata(
    metadata: Mapping[str, Any], *, event_type: str
) -> tuple[str, dict[str, AuditValue]]:
    try:
        return "current", sanitize_governance_metadata(
            metadata, event_type=event_type
        )
    except ValueError:
        pass
    if (
        not isinstance(metadata, Mapping)
        or event_type not in _EVENT_SCHEMAS
        or "operation_id" in metadata
        or metadata.get("result") != _event_result(event_type)
    ):
        return "unsupported_redacted", {}
    legacy: dict[str, AuditValue] = {
        "result": str(metadata["result"]),
    }
    redacted = False
    expected_operation = (
        "bind" if event_type.startswith("owner_binding_")
        else "replace" if event_type.startswith("owner_replacement_")
        else None
    )
    expected_new_role = None
    expected_previous_role = None
    if event_type.startswith("viewer_role_assignment_"):
        expected_new_role = "management_viewer"
    elif event_type.startswith("viewer_role_revocation_"):
        expected_new_role = "member"
    elif event_type.startswith("admin_role_assignment_"):
        expected_previous_role, expected_new_role = "member", "platform_admin"
    elif event_type.startswith("admin_role_revocation_"):
        expected_previous_role, expected_new_role = "platform_admin", "member"
    typed_rules: dict[str, Callable[[Any], bool]] = {
        "directory_generation_id": _uuid_string,
        "linked_audit_event_id": _uuid_string,
        "agent_id": lambda value: (
            isinstance(value, str) and _AGENT_ID.fullmatch(value) is not None
        ),
        "operation": lambda value: (
            isinstance(value, str) and value == expected_operation
        ),
        "role": lambda value: (
            isinstance(value, str) and value == "platform_owner"
        ),
        "previous_role": lambda value: (
            isinstance(value, str)
            and (
                value == expected_previous_role
                if expected_previous_role is not None
                else value in _ROLES
            )
        ),
        "new_role": lambda value: (
            isinstance(value, str) and value == expected_new_role
        ),
        "session_revocation_count": lambda value: (
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ),
    }
    event_typed_keys: dict[str, frozenset[str]] = {
        "owner_binding_requested": frozenset(
            {"directory_generation_id", "operation", "role"}
        ),
        "owner_replacement_requested": frozenset(
            {"directory_generation_id", "operation", "role"}
        ),
        "observation_scope_assignment_requested": frozenset({"agent_id"}),
        "observation_scope_revocation_requested": frozenset({"agent_id"}),
        "viewer_role_assignment_completed": frozenset(
            {"linked_audit_event_id", "new_role", "previous_role",
             "session_revocation_count"}
        ),
        "viewer_role_revocation_completed": frozenset(
            {"linked_audit_event_id", "new_role", "previous_role",
             "session_revocation_count"}
        ),
        "admin_role_assignment_completed": frozenset(
            {"linked_audit_event_id", "new_role", "previous_role",
             "session_revocation_count"}
        ),
        "admin_role_revocation_completed": frozenset(
            {"linked_audit_event_id", "new_role", "previous_role",
             "session_revocation_count"}
        ),
    }
    allowed_typed_keys = event_typed_keys.get(
        event_type,
        frozenset({"linked_audit_event_id"})
        if event_type.endswith(("_completed", "_failed"))
        else frozenset(),
    )
    for key, valid in typed_rules.items():
        if key in metadata:
            if key in allowed_typed_keys and valid(metadata[key]):
                legacy[key] = metadata[key]
            else:
                redacted = True
    for key in {"os_operator", "approver_a", "approver_b"}:
        if key in metadata:
            redacted = True
    recognized = set(typed_rules) | {
        "os_operator", "approver_a", "approver_b", "result"
    }
    if set(metadata) - recognized:
        redacted = True
    required: dict[str, frozenset[str]] = {
        "owner_binding_requested": frozenset(
            {"directory_generation_id", "operation", "role"}
        ),
        "owner_replacement_requested": frozenset(
            {"directory_generation_id", "operation", "role"}
        ),
        "observation_scope_assignment_requested": frozenset({"agent_id"}),
        "observation_scope_revocation_requested": frozenset({"agent_id"}),
        "viewer_role_assignment_completed": frozenset(
            {"new_role", "previous_role"}
        ),
        "viewer_role_revocation_completed": frozenset(
            {"new_role", "previous_role"}
        ),
        "admin_role_assignment_completed": frozenset(
            {"new_role", "previous_role"}
        ),
        "admin_role_revocation_completed": frozenset(
            {"new_role", "previous_role"}
        ),
    }
    if event_type.endswith(("_completed", "_failed")):
        required[event_type] = required.get(event_type, frozenset()) | {
            "linked_audit_event_id"
        }
    if not required.get(event_type, frozenset()) <= set(legacy):
        return "unsupported_redacted", {}
    return ("legacy_005_redacted" if redacted else "legacy_005"), legacy


def _validate_target(command: AuditCommand) -> bool:
    if command.target_type != _EVENT_TARGET.get(command.event_type):
        return False
    if command.target_type == "internal_user":
        return _uuid_string(command.target_id)
    if command.target_type == "agent_observation_scope":
        user_id, separator, agent_id = command.target_id.partition(":")
        return (
            separator == ":"
            and _uuid_string(user_id)
            and _AGENT_ID.fullmatch(agent_id) is not None
        )
    if command.target_type == "management_user_directory":
        return command.target_id == "all"
    if command.target_type == "governance_audit":
        return command.target_id == "sanitized"
    if command.target_type == "brain_model_configuration":
        return command.target_id == "active"
    if command.target_type == "fae_session":
        return _HEX_64.fullmatch(command.target_id) is not None
    return False


def _event_result(event_type: str) -> str:
    return event_type.rsplit("_", 1)[-1]


def _event_id(command: AuditCommand) -> UUID:
    identity = json.dumps(
        {
            "actor": str(command.actor_internal_user_id),
            "event": command.event_type,
            "request": str(command.request_id),
            "target_id": command.target_id,
            "target_type": command.target_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return uuid5(_AUDIT_EVENT_NAMESPACE, identity)


class AuditRepository:
    def __init__(self, audit_database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(audit_database_url, purpose="audit")
        self._database_url = audit_database_url
        self.environment = parsed.environment
        self._connect = connect
        self._session_connection = None

    def _scoped(self, connection) -> AuditRepository:
        repository = object.__new__(AuditRepository)
        repository._database_url = self._database_url
        repository.environment = self.environment
        repository._connect = self._connect
        repository._session_connection = connection
        return repository

    def _open(self, *, autocommit: bool = False):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
            autocommit=autocommit,
        )

    @contextmanager
    def serialized(self, request_id: UUID):
        lock_key = int.from_bytes(
            uuid5(_AUDIT_REQUEST_LOCK_NAMESPACE, str(request_id)).bytes[:8],
            byteorder="big",
            signed=True,
        )
        connection = None
        try:
            connection = self._open(autocommit=True)
            connection.execute("select pg_advisory_lock(%s)", (lock_key,))
        except psycopg.Error:
            if connection is not None:
                connection.close()
            raise AuditUnavailableError("required audit unavailable") from None
        try:
            yield self._scoped(connection)
        finally:
            connection.close()

    @staticmethod
    def _append_on(
        connection,
        event_id: UUID,
        command: AuditCommand,
        sanitized: Mapping[str, AuditValue],
    ) -> UUID:
        row = connection.execute(
            "select platform_control.append_audit_event("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) as event_id",
            (
                event_id,
                command.actor_internal_user_id,
                command.event_type,
                command.target_type,
                command.target_id,
                command.request_id,
                _event_result(command.event_type),
                command.reason,
                json.dumps(sanitized, sort_keys=True),
            ),
        ).fetchone()
        if row is None or row["event_id"] != event_id:
            raise AuditUnavailableError("required audit unavailable")
        return event_id

    def append(
        self,
        event_id: UUID,
        command: AuditCommand,
        sanitized: Mapping[str, AuditValue],
    ) -> UUID:
        try:
            if self._session_connection is not None:
                return self._append_on(
                    self._session_connection, event_id, command, sanitized
                )
            with self._open() as connection:
                return self._append_on(connection, event_id, command, sanitized)
        except AuditUnavailableError:
            raise
        except psycopg.Error:
            raise AuditUnavailableError("required audit unavailable") from None

    def terminal_result(self, request_id: UUID) -> str | None:
        def select(connection):
            row = connection.execute(
                "select platform_control.audit_terminal_result(%s) as result",
                (request_id,),
            ).fetchone()
            return None if row is None else row["result"]

        try:
            if self._session_connection is not None:
                return select(self._session_connection)
            with self._open() as connection:
                return select(connection)
        except psycopg.Error:
            raise AuditUnavailableError("required audit unavailable") from None


class AuditWriter:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    @property
    def environment(self) -> str | None:
        return getattr(self.repository, "environment", None)

    @classmethod
    def from_database_url(cls, audit_database_url: str) -> AuditWriter:
        return cls(AuditRepository(audit_database_url))

    def append(self, command: AuditCommand) -> UUID:
        if (
            not isinstance(command, AuditCommand)
            or not isinstance(command.actor_internal_user_id, UUID)
            or not isinstance(command.request_id, UUID)
            or command.event_type not in _EVENT_SCHEMAS
            or command.reason != _EVENT_REASON.get(command.event_type)
            or not _validate_target(command)
        ):
            raise ValueError("audit command invalid")
        sanitized = sanitize_governance_metadata(
            command.metadata, event_type=command.event_type
        )
        if sanitized.get("result") != _event_result(command.event_type):
            raise ValueError("audit command invalid")
        if sanitized.get("operation_id") != str(command.request_id):
            raise ValueError("audit command invalid")
        event_id = _event_id(command)
        return self.repository.append(event_id, command, sanitized)

    @contextmanager
    def serialized(self, request_id: UUID):
        with self.repository.serialized(request_id) as repository:
            yield AuditWriter(repository)

    def terminal_result(self, request_id: UUID) -> str | None:
        result = self.repository.terminal_result(request_id)
        if result not in {None, "completed", "failed"}:
            raise AuditUnavailableError("required audit unavailable")
        return result

    def append_outcome(
        self,
        requested: AuditCommand,
        requested_audit_event_id: UUID,
        *,
        actual: Mapping[str, AuditValue] | None = None,
        error_code: str | None = None,
    ) -> UUID:
        return self.append(
            _outcome_command(
                requested,
                requested_audit_event_id,
                "completed" if actual is not None else "failed",
                actual,
                error_code=error_code,
            )
        )


def _outcome_command(
    requested: AuditCommand,
    requested_audit_event_id: UUID,
    result: str,
    actual: Mapping[str, AuditValue] | None = None,
    *,
    error_code: str | None = None,
) -> AuditCommand:
    base = requested.event_type.removesuffix("_requested")
    if result == "completed":
        metadata = dict(actual or {})
        metadata.update(
            {
                "linked_audit_event_id": str(requested_audit_event_id),
                "result": "completed",
            }
        )
    else:
        metadata: dict[str, AuditValue] = {
            "operation_id": str(requested.request_id),
            "linked_audit_event_id": str(requested_audit_event_id),
            "error_code": error_code or "control_unavailable",
            "result": "failed",
        }
        if requested.event_type == "brain_model_configuration_change_requested":
            metadata.update(
                {
                    key: requested.metadata[key]
                    for key in _BRAIN_CONFIG_HASHES
                }
            )
            metadata["sanitized_result"] = (
                "probe_rejected"
                if error_code == "provider_probe_failed"
                else "activation_rejected"
            )
    return AuditCommand(
        event_type=f"{base}_{result}",
        actor_internal_user_id=requested.actor_internal_user_id,
        target_type=requested.target_type,
        target_id=requested.target_id,
        request_id=requested.request_id,
        reason=requested.reason,
        metadata=metadata,
    )


class SensitiveMutationCoordinator:
    """Coordinate requested audit, idempotent control commit, and outcome audit."""

    def __init__(self, audit_writer: Any) -> None:
        self.audit_writer = audit_writer

    def execute(
        self,
        *,
        requested: AuditCommand,
        mutate: Callable[[UUID], AppliedMutation[T]],
    ) -> T:
        serializer = getattr(self.audit_writer, "serialized", None)
        scope = (
            serializer(requested.request_id)
            if serializer is not None
            else nullcontext(self.audit_writer)
        )
        with scope as audit_writer:
            terminal_result = getattr(audit_writer, "terminal_result", None)
            if (
                terminal_result is not None
                and terminal_result(requested.request_id) == "failed"
            ):
                raise ValueError("operation already terminal failed")
            return self._execute_locked(
                audit_writer=audit_writer,
                requested=requested,
                mutate=mutate,
            )

    @staticmethod
    def _execute_locked(
        *,
        audit_writer: Any,
        requested: AuditCommand,
        mutate: Callable[[UUID], AppliedMutation[T]],
    ) -> T:
        requested_event_id = audit_writer.append(requested)
        try:
            applied = mutate(requested_event_id)
            if not isinstance(applied, AppliedMutation):
                raise RuntimeError("control mutation result invalid")
        except ControlCommitIndeterminateError:
            raise IndeterminateMutationError(
                requested.request_id, requested_event_id
            ) from None
        except Exception as error:
            error_code = (
                "business_rejected"
                if isinstance(error, ValueError)
                else "control_unavailable"
            )
            try:
                audit_writer.append(
                    _outcome_command(
                        requested,
                        requested_event_id,
                        "failed",
                        error_code=error_code,
                    )
                )
            except (AuditUnavailableError, ValueError):
                raise IndeterminateMutationError(
                    requested.request_id, requested_event_id
                ) from None
            raise
        try:
            audit_writer.append(
                _outcome_command(
                    requested,
                    requested_event_id,
                    "completed",
                    applied.outcome_metadata,
                )
            )
        except (AuditUnavailableError, ValueError):
            raise IndeterminateMutationError(
                requested.request_id, requested_event_id
            ) from None
        return applied.value
