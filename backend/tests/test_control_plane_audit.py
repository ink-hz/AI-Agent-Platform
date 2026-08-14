from __future__ import annotations

import json
from pathlib import Path
import threading
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
    project_governance_metadata,
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


def _admin_command(
    *,
    actor_id,
    target_id,
    request_id,
    revoke: bool = False,
    expected_row_version: int = 0,
) -> AuditCommand:
    previous_role, new_role = (
        ("platform_admin", "member")
        if revoke
        else ("member", "platform_admin")
    )
    return AuditCommand(
        event_type=(
            "admin_role_revocation_requested"
            if revoke
            else "admin_role_assignment_requested"
        ),
        actor_internal_user_id=actor_id,
        target_type="internal_user",
        target_id=str(target_id),
        request_id=request_id,
        reason="admin_access_revoked" if revoke else "admin_access_approved",
        metadata={
            "operation_id": str(request_id),
            "previous_role": previous_role,
            "new_role": new_role,
            "expected_row_version": expected_row_version,
            "result": "requested",
        },
    )


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


@pytest.mark.parametrize(
    ("revoke", "reason", "previous_role", "new_role"),
    [
        (False, "admin_access_approved", "member", "platform_admin"),
        (True, "admin_access_revoked", "platform_admin", "member"),
    ],
)
def test_administrator_audit_events_require_exact_reason_and_transition(
    revoke, reason, previous_role, new_role
) -> None:
    appended = []

    class Repository:
        def append(self, event_id, command, sanitized):
            appended.append((command, sanitized))
            return event_id

    actor_id, target_id, request_id = uuid4(), uuid4(), uuid4()
    command = _admin_command(
        actor_id=actor_id,
        target_id=target_id,
        request_id=request_id,
        revoke=revoke,
    )
    writer = AuditWriter(Repository())
    requested_id = writer.append(command)
    writer.append_outcome(
        command,
        requested_id,
        actual={
            "operation_id": str(request_id),
            "previous_role": previous_role,
            "new_role": new_role,
            "row_version": 1,
            "session_revocation_count": 1 if revoke else 0,
            "previous_scopes": [],
            "new_scopes": [],
        },
    )

    assert [item[0].reason for item in appended] == [reason, reason]
    assert [item[1]["result"] for item in appended] == [
        "requested",
        "completed",
    ]
    for invalid in (
        AuditCommand(**{**command.__dict__, "reason": "access_approved"}),
        AuditCommand(
            **{
                **command.__dict__,
                "metadata": {**command.metadata, "previous_role": "member"},
            }
        )
        if revoke
        else AuditCommand(
            **{
                **command.__dict__,
                "metadata": {
                    **command.metadata,
                    "new_role": "management_viewer",
                },
            }
        ),
    ):
        with pytest.raises(ValueError, match="audit (command|metadata) invalid"):
            writer.append(invalid)


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
def test_platform_admin_mutation_functions_exist(control_database) -> None:
    environment = control_database["environments"]["production"]
    with psycopg.connect(environment["admin"]) as connection:
        functions = connection.execute(
            "select to_regprocedure(%s), to_regprocedure(%s)",
            (
                "platform_control.assign_platform_admin(uuid,uuid,uuid,bigint,uuid)",
                "platform_control.revoke_platform_admin(uuid,uuid,uuid,bigint,uuid)",
            ),
        ).fetchone()
    assert functions[0] is not None
    assert functions[1] is not None


@pytest.mark.postgres
def test_platform_admin_mutations_are_owner_only_audited_and_idempotent(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    admin_url = environment["admin"]
    app_url = environment["urls"]["platform_control_app_preview"]
    audit_url = environment["urls"]["platform_audit_append_preview"]
    owner_id, admin_actor_id = uuid4(), uuid4()
    target_id, protected_member_id, unrelated_id = uuid4(), uuid4(), uuid4()
    managed_viewer_id = uuid4()
    generation_id, unrelated_grant_id = uuid4(), uuid4()
    with psycopg.connect(admin_url) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,content_sha256,completed_at) values "
            "(%s,'complete',%s,now())",
            (generation_id, "a" * 64),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "active_generation_id=%s,last_complete_at=now(),updated_at=now() "
            "where singleton",
            (generation_id,),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role,"
            "last_confirmed_generation_id) values "
            "(%s,'Admin Boundary Owner','active','platform_owner',%s),"
            "(%s,'Admin Boundary Actor','active','platform_admin',%s),"
            "(%s,'Admin Boundary Target','active','member',%s),"
            "(%s,'Admin Boundary Protected','active','member',%s),"
            "(%s,'Admin Boundary Unrelated','active','management_viewer',%s),"
            "(%s,'Admin Managed Viewer','active','member',%s)",
            (
                owner_id, generation_id,
                admin_actor_id, generation_id,
                target_id, generation_id,
                protected_member_id, generation_id,
                unrelated_id, generation_id,
                managed_viewer_id, generation_id,
            ),
        )
        connection.execute(
            "insert into platform_control.observation_grants "
            "(observation_grant_id,agent_id,viewer_internal_user_id,created_by) "
            "values (%s,'unrelated-agent',%s,%s)",
            (unrelated_grant_id, unrelated_id, owner_id),
        )

    writer = AuditWriter.from_database_url(audit_url)

    def append_request(
        actor_id, selected_target_id, *, revoke=False, expected_version=0
    ):
        request_id = uuid4()
        command = _admin_command(
            actor_id=actor_id,
            target_id=selected_target_id,
            request_id=request_id,
            revoke=revoke,
            expected_row_version=expected_version,
        )
        return request_id, writer.append(command)

    try:
        viewer_request_id = uuid4()
        viewer_command = _command(
            actor_internal_user_id=admin_actor_id,
            target_id=str(managed_viewer_id),
            request_id=viewer_request_id,
            metadata={
                "operation_id": str(viewer_request_id),
                "previous_role": "member",
                "new_role": "management_viewer",
                "expected_row_version": 0,
                "result": "requested",
            },
        )
        viewer_audit_id = writer.append(viewer_command)
        scope_request_id = uuid4()
        scope_command = AuditCommand(
            event_type="observation_scope_assignment_requested",
            actor_internal_user_id=admin_actor_id,
            target_type="agent_observation_scope",
            target_id=f"{managed_viewer_id}:managed-agent",
            request_id=scope_request_id,
            reason="scope_approved",
            metadata={
                "operation_id": str(scope_request_id),
                "agent_id": "managed-agent",
                "expected_user_row_version": 1,
                "expected_scope_row_version": 0,
                "result": "requested",
            },
        )
        scope_audit_id = writer.append(scope_command)
        with psycopg.connect(app_url) as connection:
            connection.execute(
                "select platform_control.assign_management_viewer("
                "%s,%s,%s,0,%s)",
                (
                    viewer_request_id,
                    admin_actor_id,
                    managed_viewer_id,
                    viewer_audit_id,
                ),
            )
            connection.execute(
                "select platform_control.grant_observation_scope("
                "%s,%s,%s,'managed-agent',1,0,%s)",
                (
                    scope_request_id,
                    admin_actor_id,
                    managed_viewer_id,
                    scope_audit_id,
                ),
            )

        stale_request_id, stale_audit_id = append_request(
            owner_id, protected_member_id
        )
        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "update platform_control.directory_state set "
                "last_complete_at=now()-interval '25 hours' where singleton"
            )
        with psycopg.connect(app_url) as connection:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="admin assignment precondition failed",
            ):
                connection.execute(
                    "select platform_control.assign_platform_admin("
                    "%s,%s,%s,0,%s)",
                    (
                        stale_request_id,
                        owner_id,
                        protected_member_id,
                        stale_audit_id,
                    ),
                )
        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "update platform_control.directory_state set "
                "last_complete_at=now(),updated_at=now() where singleton"
            )

        assignment_id, assignment_audit_id = append_request(owner_id, target_id)
        with psycopg.connect(app_url) as connection:
            assigned = connection.execute(
                "select platform_control.assign_platform_admin(%s,%s,%s,%s,%s)",
                (assignment_id, owner_id, target_id, 0, assignment_audit_id),
            ).fetchone()[0]
        assert assigned == {
            "operation_id": str(assignment_id),
            "previous_role": "member",
            "new_role": "platform_admin",
            "row_version": 1,
            "session_revocation_count": 0,
            "previous_scopes": [],
            "new_scopes": [],
        }
        with psycopg.connect(app_url) as connection:
            assert connection.execute(
                "select platform_control.assign_platform_admin(%s,%s,%s,%s,%s)",
                (assignment_id, owner_id, target_id, 0, assignment_audit_id),
            ).fetchone()[0] == assigned

        for changed_target, changed_version in (
            (protected_member_id, 0),
            (target_id, 1),
        ):
            with psycopg.connect(app_url) as connection:
                with pytest.raises(
                    psycopg.errors.UniqueViolation,
                    match="operation identity collision",
                ):
                    connection.execute(
                        "select platform_control.assign_platform_admin("
                        "%s,%s,%s,%s,%s)",
                        (
                            assignment_id,
                            owner_id,
                            changed_target,
                            changed_version,
                            assignment_audit_id,
                        ),
                    )

        denied_assignment_id, denied_assignment_audit = append_request(
            admin_actor_id, protected_member_id
        )
        denied_revocation_id, denied_revocation_audit = append_request(
            admin_actor_id, target_id, revoke=True, expected_version=1
        )
        for function_name, arguments in (
            (
                "assign_platform_admin",
                (
                    denied_assignment_id,
                    admin_actor_id,
                    protected_member_id,
                    0,
                    denied_assignment_audit,
                ),
            ),
            (
                "revoke_platform_admin",
                (
                    denied_revocation_id,
                    admin_actor_id,
                    target_id,
                    1,
                    denied_revocation_audit,
                ),
            ),
        ):
            with psycopg.connect(app_url) as connection:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(
                        f"select platform_control.{function_name}(%s,%s,%s,%s,%s)",
                        arguments,
                    )

        for protected_target in (owner_id, admin_actor_id):
            request_id, audit_id = append_request(owner_id, protected_target)
            with psycopg.connect(app_url) as connection:
                with pytest.raises(
                    psycopg.errors.CheckViolation,
                    match="admin assignment precondition failed",
                ):
                    connection.execute(
                        "select platform_control.assign_platform_admin("
                        "%s,%s,%s,0,%s)",
                        (request_id, owner_id, protected_target, audit_id),
                    )

        missing_audit_operation = uuid4()
        with psycopg.connect(app_url) as connection:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="matching audit intent required",
            ):
                connection.execute(
                    "select platform_control.assign_platform_admin("
                    "%s,%s,%s,0,%s)",
                    (missing_audit_operation, owner_id, protected_member_id, uuid4()),
                )
        mismatch_id, mismatch_audit_id = append_request(
            owner_id, protected_member_id, expected_version=1
        )
        with psycopg.connect(app_url) as connection:
            with pytest.raises(
                psycopg.errors.CheckViolation,
                match="audit payload mismatch",
            ):
                connection.execute(
                    "select platform_control.assign_platform_admin("
                    "%s,%s,%s,0,%s)",
                    (mismatch_id, owner_id, protected_member_id, mismatch_audit_id),
                )

        session_ids = (uuid4(), uuid4())
        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "insert into platform_control.web_sessions "
                "(session_id,internal_user_id,token_hash,csrf_hash,"
                "idle_expires_at,absolute_expires_at) values "
                "(%s,%s,%s,%s,now()+interval '1 hour',now()+interval '8 hours'),"
                "(%s,%s,%s,%s,now()+interval '1 hour',now()+interval '8 hours')",
                (
                    session_ids[0], target_id, b"admin-session-1", b"admin-csrf-1",
                    session_ids[1], target_id, b"admin-session-2", b"admin-csrf-2",
                ),
            )
        revocation_id, revocation_audit_id = append_request(
            owner_id, target_id, revoke=True, expected_version=1
        )
        with psycopg.connect(app_url) as connection:
            revoked = connection.execute(
                "select platform_control.revoke_platform_admin(%s,%s,%s,%s,%s)",
                (revocation_id, owner_id, target_id, 1, revocation_audit_id),
            ).fetchone()[0]
            assert connection.execute(
                "select platform_control.revoke_platform_admin(%s,%s,%s,%s,%s)",
                (revocation_id, owner_id, target_id, 1, revocation_audit_id),
            ).fetchone()[0] == revoked
        assert revoked == {
            "operation_id": str(revocation_id),
            "previous_role": "platform_admin",
            "new_role": "member",
            "row_version": 2,
            "session_revocation_count": 2,
            "previous_scopes": [],
            "new_scopes": [],
        }

        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "update platform_control.internal_users set role='platform_admin' "
                "where internal_user_id=%s",
                (owner_id,),
            )
        replay_calls = (
            (
                "assign_platform_admin",
                (assignment_id, owner_id, target_id, 0, assignment_audit_id),
            ),
            (
                "revoke_platform_admin",
                (revocation_id, owner_id, target_id, 1, revocation_audit_id),
            ),
            (
                "assign_platform_admin",
                (
                    assignment_id,
                    owner_id,
                    protected_member_id,
                    0,
                    assignment_audit_id,
                ),
            ),
        )
        for function_name, arguments in replay_calls:
            with psycopg.connect(app_url) as connection:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(
                        f"select platform_control.{function_name}(%s,%s,%s,%s,%s)",
                        arguments,
                    )
        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "update platform_control.internal_users set role='platform_owner' "
                "where internal_user_id=%s",
                (owner_id,),
            )

        with psycopg.connect(admin_url) as connection:
            assert connection.execute(
                "select role::text,row_version from platform_control.internal_users "
                "where internal_user_id=%s",
                (target_id,),
            ).fetchone() == ("member", 2)
            assert connection.execute(
                "select count(*) from platform_control.web_sessions "
                "where internal_user_id=%s and revoked_at is not null "
                "and revoked_reason='admin_role_revoked'",
                (target_id,),
            ).fetchone() == (2,)
            assert connection.execute(
                "select role::text,row_version from platform_control.internal_users "
                "where internal_user_id=%s",
                (owner_id,),
            ).fetchone() == ("platform_owner", 0)
            assert connection.execute(
                "select revoked_at is null from platform_control.observation_grants "
                "where observation_grant_id=%s",
                (unrelated_grant_id,),
            ).fetchone() == (True,)
            assert connection.execute(
                "select role::text,row_version from platform_control.internal_users "
                "where internal_user_id=%s",
                (protected_member_id,),
            ).fetchone() == ("member", 0)
            assert connection.execute(
                "select role::text,row_version from platform_control.internal_users "
                "where internal_user_id=%s",
                (managed_viewer_id,),
            ).fetchone() == ("management_viewer", 1)
            assert connection.execute(
                "select count(*) from platform_control.observation_grants "
                "where viewer_internal_user_id=%s and agent_id='managed-agent' "
                "and revoked_at is null",
                (managed_viewer_id,),
            ).fetchone() == (1,)
    finally:
        with psycopg.connect(admin_url) as connection:
            connection.execute(
                "update platform_control.internal_users set role='member' "
                "where internal_user_id=%s",
                (owner_id,),
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


@pytest.mark.postgres
def test_database_append_boundary_allows_at_most_one_terminal_per_request(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor, request_id, requested_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Terminal Actor','active')",
            (actor,),
        )
    writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    )
    requested = _command(
        event_type="viewer_role_assignment_requested",
        actor_internal_user_id=actor,
        target_id=str(actor),
        request_id=request_id,
        metadata={"operation_id": str(request_id), "previous_role": "member",
                  "new_role": "management_viewer", "expected_row_version": 0,
                  "result": "requested"},
    )
    requested_id = writer.append(requested)
    writer.append_outcome(
        requested, requested_id, error_code="control_unavailable"
    )
    with pytest.raises(AuditUnavailableError):
        writer.append_outcome(
            requested,
            requested_id,
            actual={"operation_id": str(request_id), "previous_role": "member",
                    "new_role": "management_viewer", "row_version": 1,
                    "session_revocation_count": 0, "previous_scopes": [],
                    "new_scopes": []},
        )

def test_audit_module_uses_no_replica_or_distributed_transaction_claim() -> None:
    source = (Path(__file__).parents[1] / "app/control_plane/audit.py").read_text()
    dsn_source = (Path(__file__).parents[1] / "app/control_plane/dsn.py").read_text()
    assert "validate_control_dsn" in source
    assert "agent_platform_control" in dsn_source
    assert "agent_platform\"" not in source
    assert "distributed transaction" not in source.lower()


@pytest.mark.postgres
def test_audit_request_lock_is_session_scoped_and_released_on_exception(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    )
    request_id = uuid4()
    entered = threading.Event()

    with pytest.raises(RuntimeError, match="simulated crash"):
        with writer.serialized(request_id):
            raise RuntimeError("simulated crash")

    def acquire_again() -> None:
        with writer.serialized(request_id):
            entered.set()

    thread = threading.Thread(target=acquire_again)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert entered.is_set()


@pytest.mark.parametrize(
    "key",
    [
        "directory_generation_id", "linked_audit_event_id", "agent_id",
        "operation", "role", "previous_role", "new_role",
        "session_revocation_count", "os_operator", "approver_a", "approver_b",
    ],
)
@pytest.mark.parametrize("malformed", [None, [], {}, 1, 1.5, False, True])
def test_legacy_projection_is_total_for_every_malformed_allowlisted_value(
    key, malformed
) -> None:
    metadata = {
        "directory_generation_id": str(uuid4()),
        "operation": "bind",
        "role": "platform_owner",
        "result": "requested",
        key: malformed,
    }
    status, projected = project_governance_metadata(
        metadata, event_type="owner_binding_requested"
    )
    assert status in {"legacy_005_redacted", "unsupported_redacted"}
    assert malformed not in projected.values()
