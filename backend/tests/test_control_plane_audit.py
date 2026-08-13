from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.audit import (
    AppliedMutation,
    AuditCommand,
    AuditUnavailableError,
    AuditWriter,
    ControlCommitIndeterminateError,
    IndeterminateMutationError,
    SensitiveMutationCoordinator,
    sanitize_governance_metadata,
)
from test_control_plane_migration import control_database


def _command(**overrides) -> AuditCommand:
    values = {
        "event_type": "viewer_role_assignment_requested",
        "actor_internal_user_id": uuid4(),
        "target_type": "internal_user",
        "target_id": str(uuid4()),
        "request_id": uuid4(),
        "reason": "access_approved",
        "metadata": {
            "operation_id": None,
            "previous_role": "member",
            "new_role": "management_viewer",
            "expected_row_version": 0,
            "result": "requested",
        },
    }
    values.update(overrides)
    if values["metadata"].get("operation_id") is None:
        values["metadata"]["operation_id"] = str(values["request_id"])
    return AuditCommand(**values)


def test_governance_metadata_rejects_unknown_keys_instead_of_silently_dropping() -> None:
    with pytest.raises(ValueError, match="audit metadata invalid"):
        sanitize_governance_metadata(
        {
            "operation_id": str(uuid4()),
            "previous_role": "member",
            "new_role": "management_viewer",
            "expected_row_version": 0,
            "result": "requested",
            "provider_subject_id": "raw-provider-value",
        }
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("agent_id", "https://provider.example/user/123"),
        ("agent_id", "person@example.test"),
        ("agent_id", "+86-13800138000"),
        ("agent_id", "/private/evidence.txt"),
        ("agent_id", "bad\nagent"),
        ("agent_id", "a" * 129),
        ("approver_a", "Alice Smith"),
        ("incident_reference", "free form incident evidence"),
    ],
)
def test_governance_metadata_rejects_unsafe_string_classes(key, value) -> None:
    with pytest.raises(ValueError, match="audit metadata invalid"):
        sanitize_governance_metadata({key: value})


@pytest.mark.parametrize(
    ("event_type", "reason"),
    [
        ("arbitrary_requested", "access_approved"),
        ("viewer_role_assignment_requested", "free form reason"),
        ("viewer_role_assignment_requested", "scope_approved"),
    ],
)
def test_audit_writer_rejects_event_and_reason_outside_exact_vocabulary(
    event_type, reason
) -> None:
    class Repository:
        def append(self, *args):  # pragma: no cover - must not run
            raise AssertionError("invalid audit reached repository")

    with pytest.raises(ValueError, match="audit command invalid"):
        AuditWriter(Repository()).append(
            _command(event_type=event_type, reason=reason)
        )


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_audit_writer_requires_a_reason(reason) -> None:
    class Repository:
        def append(self, command, sanitized):  # pragma: no cover - must not run
            raise AssertionError("invalid audit command reached repository")

    with pytest.raises(ValueError, match="audit command invalid"):
        AuditWriter(Repository()).append(_command(reason=reason))


def test_audit_event_id_is_idempotent_for_request_phase_and_correlated() -> None:
    calls = []

    class Repository:
        def append(self, event_id, command, sanitized):
            calls.append((event_id, command.request_id, sanitized))
            return event_id

    writer = AuditWriter(Repository())
    command = _command()

    assert writer.append(command) == writer.append(command)
    assert len({call[0] for call in calls}) == 1
    assert {call[1] for call in calls} == {command.request_id}


def test_sensitive_mutation_fails_closed_before_control_change() -> None:
    mutations = []

    class FailingAudit:
        def append(self, command):
            raise AuditUnavailableError("required audit unavailable")

    coordinator = SensitiveMutationCoordinator(FailingAudit())

    with pytest.raises(AuditUnavailableError, match="required audit unavailable"):
        coordinator.execute(
            requested=_command(),
            mutate=lambda event_id: mutations.append(event_id),
        )

    assert mutations == []


def test_outcome_failure_is_explicit_indeterminate_and_retry_is_idempotent() -> None:
    appended = []
    mutation_links = []
    fail_completed_once = True

    class OutcomeFailingAudit:
        event_ids = {}

        def append(self, command):
            nonlocal fail_completed_once
            appended.append(command.event_type)
            if command.event_type.endswith("_completed") and fail_completed_once:
                fail_completed_once = False
                raise AuditUnavailableError("required audit unavailable")
            return self.event_ids.setdefault(command.event_type, uuid4())

    command = _command()
    coordinator = SensitiveMutationCoordinator(OutcomeFailingAudit())

    def mutate(event_id):
        if event_id not in mutation_links:
            mutation_links.append(event_id)
        return AppliedMutation(
            "changed",
            {
                "operation_id": str(command.request_id),
                "previous_role": "member",
                "new_role": "management_viewer",
                "row_version": 1,
                "session_revocation_count": 0,
                "previous_scopes": [],
                "new_scopes": [],
            },
        )

    with pytest.raises(IndeterminateMutationError) as caught:
        coordinator.execute(requested=command, mutate=mutate)
    assert caught.value.request_id == command.request_id
    assert len(mutation_links) == 1

    assert coordinator.execute(requested=command, mutate=mutate) == "changed"
    assert len(mutation_links) == 1
    assert appended == [
        "viewer_role_assignment_requested",
        "viewer_role_assignment_completed",
        "viewer_role_assignment_requested",
        "viewer_role_assignment_completed",
    ]


def test_completed_outcome_uses_applied_snapshot_not_requested_metadata() -> None:
    commands = []

    class Audit:
        def append(self, command):
            commands.append(command)
            return uuid4()

    command = _command(
        metadata={
            "operation_id": None,
            "previous_role": "member",
            "new_role": "management_viewer",
            "expected_row_version": 7,
            "result": "requested",
        }
    )
    command = _command(
        request_id=command.request_id,
        metadata={
            **command.metadata,
            "operation_id": str(command.request_id),
        },
    )
    actual = {
        "operation_id": str(command.request_id),
        "previous_role": "member",
        "new_role": "management_viewer",
        "row_version": 8,
        "session_revocation_count": 3,
        "previous_scopes": ["fae"],
        "new_scopes": ["fae"],
    }

    result = SensitiveMutationCoordinator(Audit()).execute(
        requested=command,
        mutate=lambda _: AppliedMutation("changed", actual),
    )

    assert result == "changed"
    completed_metadata = dict(commands[-1].metadata)
    assert isinstance(UUID(completed_metadata.pop("linked_audit_event_id")), UUID)
    assert completed_metadata == {**actual, "result": "completed"}
    assert "expected_row_version" not in commands[-1].metadata


def test_failed_outcome_append_failure_is_indeterminate_not_business_failure() -> None:
    command = _command()

    class Audit:
        def append(self, appended):
            if appended.event_type.endswith("_failed"):
                raise AuditUnavailableError("required audit unavailable")
            return uuid4()

    with pytest.raises(IndeterminateMutationError) as caught:
        SensitiveMutationCoordinator(Audit()).execute(
            requested=command,
            mutate=lambda _: (_ for _ in ()).throw(ValueError("rejected")),
        )
    assert caught.value.request_id == command.request_id


def test_control_commit_ambiguity_is_indeterminate_without_failed_outcome() -> None:
    command = _command()
    appended = []

    class Audit:
        def append(self, entry):
            appended.append(entry.event_type)
            return uuid4()

    with pytest.raises(IndeterminateMutationError):
        SensitiveMutationCoordinator(Audit()).execute(
            requested=command,
            mutate=lambda _: (_ for _ in ()).throw(
                ControlCommitIndeterminateError(command.request_id)
            ),
        )
    assert appended == ["viewer_role_assignment_requested"]


@pytest.mark.postgres
def test_real_audit_role_appends_immutable_idempotent_correlated_rows(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values (%s, %s, 'active')",
            (actor_id, "Audit Actor"),
        )

    writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    )
    command = _command(actor_internal_user_id=actor_id)
    first = writer.append(command)
    second = writer.append(command)

    assert first == second
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select audit_event_id, request_id, result, reason_code, "
            "sanitized_before_after from platform_control.audit_events "
            "where audit_event_id = %s",
            (first,),
        ).fetchone()
        assert row == (
            first,
            command.request_id,
            "requested",
            command.reason,
            dict(command.metadata),
        )

    for role in (
        "platform_control_app",
        "platform_audit_append",
        "platform_directory_worker",
        "platform_stream_ingest",
    ):
        with psycopg.connect(environment["urls"][role]) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "delete from platform_control.audit_events "
                    "where audit_event_id = %s",
                    (first,),
                )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("event_type", "reason", "metadata"),
    [
        ("arbitrary_requested", "access_approved", {"result": "requested"}),
        (
            "viewer_role_assignment_requested",
            "free form reason",
            {"result": "requested"},
        ),
        (
            "viewer_role_assignment_requested",
            "access_approved",
            {"result": "requested", "provider_id": "provider-secret"},
        ),
        (
            "observation_scope_assignment_requested",
            "scope_approved",
            {
                "operation_id": "00000000-0000-4000-8000-000000000001",
                "agent_id": "https://provider.example/user",
                "expected_user_row_version": 0,
                "expected_scope_row_version": 0,
                "result": "requested",
            },
        ),
    ],
)
def test_database_append_boundary_rejects_unsafe_vocabulary_and_metadata(
    control_database, event_type, reason, metadata
) -> None:
    environment = control_database["environments"]["preview"]
    actor = uuid4()
    operation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values "
            "(%s, 'Unsafe Audit Actor', 'active')",
            (actor,),
        )
    metadata = {
        key: str(operation_id) if key == "operation_id" else value
        for key, value in metadata.items()
    }
    with psycopg.connect(
        environment["urls"]["platform_audit_append_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.append_audit_event("
                "%s,%s,%s,'internal_user',%s,%s,'requested',%s,%s::jsonb)",
                (
                    uuid4(),
                    actor,
                    event_type,
                    str(actor),
                    operation_id,
                    reason,
                    json.dumps(metadata),
                ),
            )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "metadata",
    [
        {
            "operation_id": None,
            "previous_role": "member",
            "new_role": "management_viewer",
            "expected_row_version": 0,
            "result": "requested",
        },
        {
            "operation_id": "REQUEST_ID",
            "previous_role": None,
            "new_role": "management_viewer",
            "expected_row_version": 0,
            "result": "requested",
        },
    ],
)
def test_database_append_boundary_rejects_required_json_nulls(
    control_database, metadata
) -> None:
    environment = control_database["environments"]["preview"]
    actor, operation_id = uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values (%s, 'Null Actor', 'active')",
            (actor,),
        )
    metadata = {
        key: str(operation_id) if value == "REQUEST_ID" else value
        for key, value in metadata.items()
    }
    with psycopg.connect(
        environment["urls"]["platform_audit_append_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.append_audit_event("
                "%s,%s,'viewer_role_assignment_requested','internal_user',"
                "%s,%s,'requested','access_approved',%s::jsonb)",
                (uuid4(), actor, str(actor), operation_id, json.dumps(metadata)),
            )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "scopes",
    [["fae", "fae"], [f"agent-{index:03d}" for index in range(257)]],
)
def test_database_append_boundary_rejects_noncanonical_scope_arrays(
    control_database, scopes
) -> None:
    environment = control_database["environments"]["preview"]
    actor, operation_id, linked = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values "
            "(%s, 'Array Actor', 'active')",
            (actor,),
        )
    metadata = {
        "operation_id": str(operation_id),
        "linked_audit_event_id": str(linked),
        "previous_role": "member",
        "new_role": "management_viewer",
        "row_version": 1,
        "session_revocation_count": 0,
        "previous_scopes": scopes,
        "new_scopes": scopes,
        "result": "completed",
    }
    with psycopg.connect(
        environment["urls"]["platform_audit_append_preview"]
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select platform_control.append_audit_event("
                "%s,%s,'viewer_role_assignment_completed','internal_user',"
                "%s,%s,'completed','access_approved',%s::jsonb)",
                (uuid4(), actor, str(actor), operation_id, json.dumps(metadata)),
            )


@pytest.mark.postgres
def test_offline_owner_function_rejects_mismatched_audit_intent(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status) values "
            "(%s, 'Intent Actor', 'active')",
            (actor_id,),
        )
    request_id = uuid4()
    generation_id = uuid4()
    event_id = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    ).append(
        _command(
            event_type="owner_replacement_requested",
            actor_internal_user_id=actor_id,
            target_id=str(actor_id),
            request_id=request_id,
            reason="owner_departure",
            metadata={
                "operation_id": str(request_id),
                "directory_generation_id": str(generation_id),
                "directory_generation_digest": "a" * 64,
                "protected_target_lookup_hash": "b" * 64,
                "protected_target_lookup_version": 1,
                "os_operator": "root",
                "approver_a": "uid:1001",
                "approver_b": "uid:1002",
                "backup_reference": "BACKUP_123",
                "incident_reference": "INC_123",
                "previous_owner_internal_user_id": str(uuid4()),
                "expected_owner_row_version": 0,
                "expected_target_row_version": 0,
                "result": "requested",
            },
        )
    )

    with psycopg.connect(
        environment["urls"]["platform_control_migrator"]
    ) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="matching audit intent required",
        ):
            connection.execute(
                "select platform_control.change_platform_owner_v2("
                "%s, 'bind', %s, %s, null, 0, 0, %s)",
                (request_id, actor_id, generation_id, event_id),
            )


def test_audit_module_uses_no_replica_or_distributed_transaction_claim() -> None:
    source = (Path(__file__).parents[1] / "app/control_plane/audit.py").read_text()
    dsn_source = (Path(__file__).parents[1] / "app/control_plane/dsn.py").read_text()
    assert "validate_control_dsn" in source
    assert "agent_platform_control" in dsn_source
    assert "agent_platform\"" not in source
    assert "distributed transaction" not in source.lower()
