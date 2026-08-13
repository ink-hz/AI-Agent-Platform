from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.audit import (
    AuditCommand,
    AuditUnavailableError,
    AuditWriter,
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
        "reason": "approved access request",
        "metadata": {"role": "management_viewer", "result": "requested"},
    }
    values.update(overrides)
    return AuditCommand(**values)


def test_governance_metadata_is_allowlisted_and_content_free() -> None:
    sanitized = sanitize_governance_metadata(
        {
            "role": "management_viewer",
            "agent_id": "fae",
            "result": "completed",
            "provider_subject_id": "raw-provider-value",
            "session_text": "private conversation",
            "filename": "private-plan.pdf",
            "evidence": "secret evidence",
            "cookie_token": "cookie-secret",
        }
    )

    assert sanitized == {
        "agent_id": "fae",
        "result": "completed",
        "role": "management_viewer",
    }
    serialized = repr(sanitized).lower()
    assert "provider" not in serialized
    assert "conversation" not in serialized
    assert "private-plan" not in serialized
    assert "evidence" not in serialized
    assert "cookie-secret" not in serialized


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_audit_writer_requires_a_reason(reason) -> None:
    class Repository:
        def append(self, command, sanitized):  # pragma: no cover - must not run
            raise AssertionError("invalid audit command reached repository")

    with pytest.raises(ValueError, match="audit reason required"):
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
        return "changed"

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
            {"result": "requested", "role": "management_viewer"},
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
    event_id = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    ).append(
        _command(
            event_type="owner_replacement_requested",
            actor_internal_user_id=actor_id,
            target_id=str(actor_id),
            metadata={"result": "requested", "role": "platform_owner"},
        )
    )

    with psycopg.connect(
        environment["urls"]["platform_control_migrator"]
    ) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="owner role change invalid",
        ):
            connection.execute(
                "select platform_control.change_platform_owner("
                "'bind', %s, %s, %s)",
                (actor_id, uuid4(), event_id),
            )


def test_audit_module_uses_no_replica_or_distributed_transaction_claim() -> None:
    source = (Path(__file__).parents[1] / "app/control_plane/audit.py").read_text()
    assert "agent_platform_control" in source
    assert "agent_platform\"" not in source
    assert "distributed transaction" not in source.lower()
