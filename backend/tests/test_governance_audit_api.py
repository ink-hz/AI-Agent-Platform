from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import psycopg
import pytest

from app.control_plane.audit import (
    AuditCommand,
    AuditUnavailableError,
    IndeterminateMutationError,
    project_governance_metadata,
)
from app.control_plane.models import AuthContext, Role
from app.control_plane.routes_manage import (
    ManagementRepository,
    ManagementService,
    authenticated_context,
    csrf_protection,
    fresh_directory,
    management_service,
    router,
)
from app.control_plane.audit import AuditWriter
from test_control_plane_migration import control_database


OWNER = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
VIEWER = AuthContext(uuid4(), Role.MANAGEMENT_VIEWER, uuid4(), False)
MEMBER = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)


class FakeManagementRepository:
    def __init__(self) -> None:
        self.users = [
            {
                "internal_user_id": uuid4(),
                "display_name": "Synthetic User",
                "status": "active",
                "role": "member",
                "scopes": [],
            }
        ]
        self.mutations = []
        self.revocations = []
        self.states = {}
        self.ledger = {}

    def list_users(self):
        return self.users

    def viewer_state(self, target):
        return self.states.setdefault(
            target, {"role": "member", "row_version": 0, "scopes": []}
        )

    def observation_state(self, target, agent_id):
        state = self.viewer_state(target)
        return {
            **state,
            "scope_row_version": 1 if agent_id in state["scopes"] else 0,
        }

    def mutation_precondition(self, operation_id, action, target, agent_id=None):
        return self.ledger.get(operation_id)

    def assign_viewer(
        self, actor, target, operation_id, expected_version, audit_event_id
    ):
        mutation = ("assign", actor, target, operation_id, audit_event_id)
        if mutation not in self.mutations:
            self.mutations.append(mutation)
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_version,
            "expected_causal_row_version": 0,
        }
        state = self.viewer_state(target)
        state.update({"role": "management_viewer", "row_version": expected_version + 1})
        return {
            "operation_id": str(operation_id),
            "previous_role": "member",
            "new_role": "management_viewer",
            "row_version": expected_version + 1,
            "session_revocation_count": 0,
            "previous_scopes": [],
            "new_scopes": [],
        }

    def revoke_viewer(
        self, actor, target, operation_id, expected_version, audit_event_id
    ):
        state = self.viewer_state(target)
        before = list(state["scopes"])
        self.mutations.append(("revoke", actor, target, operation_id, audit_event_id))
        self.revocations.append(target)
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_version,
            "expected_causal_row_version": 0,
        }
        state.update({"role": "member", "row_version": expected_version + 1, "scopes": []})
        return {
            "operation_id": str(operation_id),
            "previous_role": "management_viewer",
            "new_role": "member",
            "row_version": expected_version + 1,
            "session_revocation_count": 1,
            "previous_scopes": before,
            "new_scopes": [],
        }

    def grant_observation(
        self, actor, target, agent_id, operation_id,
        expected_user_version, expected_scope_version, audit_event_id
    ):
        state = self.viewer_state(target)
        before = list(state["scopes"])
        state["scopes"] = sorted({*before, agent_id})
        self.mutations.append(("grant", actor, target, agent_id, operation_id, audit_event_id))
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_user_version,
            "expected_causal_row_version": expected_scope_version,
        }
        return {
            "operation_id": str(operation_id), "agent_id": agent_id,
            "before_scope": False, "after_scope": True, "row_version": 1,
            "previous_scopes": before, "new_scopes": state["scopes"],
        }

    def revoke_observation(
        self, actor, target, agent_id, operation_id,
        expected_user_version, expected_scope_version, audit_event_id
    ):
        state = self.viewer_state(target)
        before = list(state["scopes"])
        state["scopes"] = [scope for scope in before if scope != agent_id]
        self.mutations.append(("ungrant", actor, target, agent_id, operation_id, audit_event_id))
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_user_version,
            "expected_causal_row_version": expected_scope_version,
        }
        return {
            "operation_id": str(operation_id), "agent_id": agent_id,
            "before_scope": True, "after_scope": False,
            "row_version": expected_scope_version + 1,
            "previous_scopes": before, "new_scopes": state["scopes"],
        }

    def governance_audit(self):
        return [
            {
                "audit_event_id": uuid4(),
                "actor_internal_user_id": OWNER.internal_user_id,
                "event_type": "owner_binding_completed",
                "target_type": "internal_user",
                "target_internal_id": str(uuid4()),
                "request_id": uuid4(),
                "result": "completed",
                "reason_code": "approved binding",
                "sanitized_before_after": {"role": "platform_owner"},
                "occurred_at": "2026-08-13T00:00:00+00:00",
            }
        ]


class FakeAuditWriter:
    def __init__(self) -> None:
        self.commands = []
        self.fail = False
        self.fail_completed_once = False

    def append(self, command):
        self.commands.append(command)
        if self.fail:
            raise AuditUnavailableError("required audit unavailable")
        if command.event_type.endswith("_completed") and self.fail_completed_once:
            self.fail_completed_once = False
            raise AuditUnavailableError("required audit unavailable")
        return uuid5(
            NAMESPACE_URL,
            f"{command.request_id}:{command.event_type}:{command.target_id}",
        )


def _client(context: AuthContext, *, csrf=True, fresh=True):
    repository = FakeManagementRepository()
    audit = FakeAuditWriter()
    service = ManagementService(repository, audit)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticated_context] = lambda: context
    app.dependency_overrides[csrf_protection] = lambda: csrf
    app.dependency_overrides[fresh_directory] = lambda: fresh
    app.dependency_overrides[management_service] = lambda: service
    return TestClient(app), repository, audit


def test_routes_fail_closed_without_server_auth_context() -> None:
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/api/v1/manage/users",
        headers={"X-Role": "platform_owner", "X-Internal-User-Id": str(uuid4())},
    )
    assert response.status_code in {401, 503}


@pytest.mark.parametrize("context", [MEMBER, VIEWER])
def test_only_owner_can_list_users_or_mutate(context: AuthContext) -> None:
    client, repository, _ = _client(context)
    assert client.get("/api/v1/manage/users").status_code == 403
    assert client.post(
        f"/api/v1/manage/viewers/{uuid4()}", json={"reason": "access_approved"}
    ).status_code == 403
    assert repository.mutations == []


def test_owner_user_list_is_internal_and_sanitized() -> None:
    client, _, _ = _client(OWNER)
    response = client.get("/api/v1/manage/users")
    assert response.status_code == 200
    payload = response.json()["users"][0]
    assert set(payload) == {
        "internal_user_id",
        "display_name",
        "status",
        "role",
        "scopes",
    }
    assert not set(payload) & {"provider_id", "mobile", "email"}


@pytest.mark.parametrize(
    ("csrf", "fresh", "context", "expected"),
    [(False, True, OWNER, 403), (True, False, OWNER, 503), (True, True, VIEWER, 403)],
)
def test_viewer_mutation_requires_owner_csrf_and_fresh_directory(
    csrf: bool, fresh: bool, context: AuthContext, expected: int
) -> None:
    client, repository, _ = _client(context, csrf=csrf, fresh=fresh)
    response = client.post(
        f"/api/v1/manage/viewers/{uuid4()}",
        json={"reason": "access_approved"},
    )
    assert response.status_code == expected
    assert repository.mutations == []


def test_owner_can_assign_revoke_viewer_and_exact_observation_scope() -> None:
    client, repository, audit = _client(OWNER)
    target = uuid4()
    assert client.post(
        f"/api/v1/manage/viewers/{target}",
        json={"reason": "access_approved"},
    ).status_code == 200
    assert client.put(
        f"/api/v1/manage/viewers/{target}/observations/fae",
        json={"reason": "scope_approved"},
    ).status_code == 200
    assert client.request(
        "DELETE",
        f"/api/v1/manage/viewers/{target}/observations/fae",
        json={"reason": "scope_revoked"},
    ).status_code == 200
    assert client.request(
        "DELETE",
        f"/api/v1/manage/viewers/{target}",
        json={"reason": "access_revoked"},
    ).status_code == 200
    assert repository.revocations == [target]
    assert [entry[0] for entry in repository.mutations] == [
        "assign",
        "grant",
        "ungrant",
        "revoke",
    ]
    assert any(command.event_type == "viewer_role_revocation_requested" for command in audit.commands)


def test_required_initial_audit_failure_returns_503_without_mutation() -> None:
    client, repository, audit = _client(OWNER)
    audit.fail = True
    response = client.post(
        f"/api/v1/manage/viewers/{uuid4()}",
        json={"reason": "access_approved"},
    )
    assert response.status_code == 503
    assert repository.mutations == []


def test_indeterminate_mutation_returns_request_id_and_retry_is_idempotent() -> None:
    client, repository, audit = _client(OWNER)
    audit.fail_completed_once = True
    target = uuid4()
    request_id = uuid4()
    body = {"reason": "access_approved", "request_id": str(request_id)}

    first = client.post(f"/api/v1/manage/viewers/{target}", json=body)
    assert first.status_code == 503
    assert first.json()["detail"] == {
        "code": "management_mutation_indeterminate",
        "request_id": str(request_id),
    }
    second = client.post(f"/api/v1/manage/viewers/{target}", json=body)
    assert second.status_code == 200
    assert len(repository.mutations) == 1


@pytest.mark.parametrize("context", [OWNER, VIEWER])
def test_owner_and_viewer_read_sanitized_governance_and_read_is_audited(
    context: AuthContext,
) -> None:
    client, _, audit = _client(context)
    response = client.get("/api/v1/manage/audit/governance")
    assert response.status_code == 200
    payload = response.json()["events"][0]
    forbidden = {"provider_id", "session", "message", "filename", "evidence"}
    assert not forbidden & {key.lower() for key in payload}
    assert any(
        command.event_type == "governance_audit_read_requested"
        for command in audit.commands
    )


def test_member_cannot_read_governance_audit() -> None:
    client, _, audit = _client(MEMBER)
    assert client.get("/api/v1/manage/audit/governance").status_code == 403
    assert audit.commands == []


@pytest.mark.postgres
def test_governance_projection_includes_management_directory_reads(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    actor = uuid4()
    request_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Read Reviewer', 'active', 'member')",
            (actor,),
        )
    writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    )
    requested = writer.append(AuditCommand(
        "management_user_list_read_requested", actor,
        "management_user_directory", "all", request_id,
        "privileged_read", {"operation_id": str(request_id), "result": "requested"},
    ))
    writer.append(AuditCommand(
        "management_user_list_read_completed", actor,
        "management_user_directory", "all", request_id,
        "privileged_read", {"operation_id": str(request_id),
                            "linked_audit_event_id": str(requested),
                            "item_count": 1, "result": "completed"},
    ))
    events = ManagementRepository(
        environment["urls"]["platform_control_app"]
    ).governance_audit()
    assert {event["event_type"] for event in events} >= {
        "management_user_list_read_requested",
        "management_user_list_read_completed",
    }


@pytest.mark.postgres
def test_governance_projection_classifies_legacy_005_and_malformed_rows(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    actor = uuid4()
    legacy_id, malformed_id = uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,'Legacy Actor','active')",
            (actor,),
        )
        connection.execute(
            "insert into platform_control.audit_events "
            "(audit_event_id,actor_internal_user_id,event_type,target_type,"
            "target_internal_id,request_id,result,reason_code,sanitized_before_after) "
            "values (%s,%s,'owner_binding_requested','internal_user',%s,%s,"
            "'requested','approved binding',%s::jsonb),"
            "(%s,%s,'viewer_role_assignment_requested','internal_user',%s,%s,"
            "'requested','unsafe reason',%s::jsonb)",
            (legacy_id, actor, str(actor), uuid4(),
             json.dumps({"directory_generation_id": str(uuid4()),
                         "operation": "bind", "os_operator": "root",
                         "result": "requested", "role": "platform_owner",
                         "secret_evidence": "must-not-project"}),
             malformed_id, actor, str(actor), uuid4(),
             json.dumps({"provider_id": "sensitive", "message": "raw"})),
        )
    events = ManagementRepository(
        environment["urls"]["platform_control_app_preview"]
    ).governance_audit()
    selected = {event["audit_event_id"]: event for event in events}
    assert selected[legacy_id]["projection_status"] == "legacy_005_redacted"
    assert selected[legacy_id]["sanitized_before_after"] == {
        "directory_generation_id": selected[legacy_id]["sanitized_before_after"][
            "directory_generation_id"
        ],
        "operation": "bind",
        "result": "requested",
        "role": "platform_owner",
    }
    assert selected[malformed_id]["projection_status"] == "unsupported_redacted"
    assert selected[malformed_id]["sanitized_before_after"] == {}
    assert "provider_id" not in json.dumps(list(selected.values()), default=str)


@pytest.mark.parametrize(
    ("event_type", "metadata", "forbidden"),
    [
        ("owner_binding_requested", {"directory_generation_id": "provider-secret",
         "operation": "bind", "os_operator": "Bearer token", "result": "requested",
         "role": "platform_owner"}, "provider-secret"),
        ("owner_replacement_requested", {"directory_generation_id": str(uuid4()),
         "operation": "../../secret.txt", "os_operator": "root", "result": "requested",
         "role": "platform_owner"}, "../../secret.txt"),
        ("observation_scope_assignment_requested", {"agent_id": "Bearer token",
         "result": "requested"}, "Bearer token"),
        ("viewer_role_assignment_completed", {"linked_audit_event_id": "message.txt",
         "new_role": "provider-id", "previous_role": "member", "result": "completed",
         "session_revocation_count": -1}, "provider-id"),
        ("owner_binding_requested", {"directory_generation_id": str(uuid4()),
         "operation": "bind", "os_operator": "provider_identity", "result": "requested",
         "role": "platform_owner", "approver_a": "token=secret",
         "approver_b": "/tmp/evidence.txt"}, "token=secret"),
    ],
)
def test_legacy_projection_never_returns_suspicious_allowlisted_values(
    event_type, metadata, forbidden
) -> None:
    status, projected = project_governance_metadata(metadata, event_type=event_type)
    assert status in {"legacy_005_redacted", "unsupported_redacted"}
    assert forbidden not in json.dumps(projected)


def test_hard_stale_owner_cannot_mutate_but_can_read_governance() -> None:
    stale_owner = AuthContext(
        OWNER.internal_user_id,
        OWNER.role,
        OWNER.session_id,
        True,
    )
    client, repository, _ = _client(stale_owner)
    assert client.post(
        f"/api/v1/manage/viewers/{uuid4()}",
        json={"reason": "access_approved"},
    ).status_code == 503
    assert client.get("/api/v1/manage/audit/governance").status_code == 200
    assert repository.mutations == []


def test_no_owner_replacement_web_api_exists() -> None:
    client, _, _ = _client(OWNER)
    assert client.post(
        "/api/v1/manage/replace-owner", json={"reason": "forbidden"}
    ).status_code == 404


@pytest.mark.postgres
def test_real_viewer_revocation_links_audit_and_revokes_sessions_atomically(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    viewer_id = uuid4()
    session_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'API Owner', 'active', 'platform_owner'), "
            "(%s, 'API Viewer', 'active', 'management_viewer')",
            (owner_id, viewer_id),
        )
        connection.execute(
            "insert into platform_control.web_sessions "
            "(session_id, internal_user_id, token_hash, csrf_hash, "
            "idle_expires_at, absolute_expires_at) values "
            "(%s, %s, %s, %s, now() + interval '1 hour', "
            "now() + interval '8 hours')",
            (session_id, viewer_id, b"viewer-session", b"viewer-csrf"),
        )

    context = AuthContext(owner_id, Role.PLATFORM_OWNER, uuid4(), False)
    ManagementService(
        ManagementRepository(environment["urls"]["platform_control_app"]),
        AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append"]
        ),
    ).change_viewer(
        context,
        viewer_id,
        "access_revoked",
        revoke=True,
    )

    with psycopg.connect(environment["admin"]) as connection:
        user = connection.execute(
            "select role::text, role_audit_event_id from "
            "platform_control.internal_users where internal_user_id = %s",
            (viewer_id,),
        ).fetchone()
        session = connection.execute(
            "select revoked_at, revoked_reason from "
            "platform_control.web_sessions where session_id = %s",
            (session_id,),
        ).fetchone()
        events = connection.execute(
            "select event_type from platform_control.audit_events "
            "where request_id = (select request_id from "
            "platform_control.audit_events where audit_event_id = %s) "
            "order by occurred_at, event_type",
            (user[1],),
        ).fetchall()
    assert user[0] == "member"
    assert user[1] is not None
    assert session[0] is not None
    assert session[1] == "viewer_role_revoked"
    assert events == [
        ("viewer_role_revocation_requested",),
        ("viewer_role_revocation_completed",),
    ]


@pytest.mark.postgres
def test_real_sensitive_mutation_failure_states_are_reconcilable(
    control_database,
) -> None:
    environment = control_database["environments"]["preview"]
    owner_id = uuid4()
    target_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role) values "
            "(%s, 'Failure Owner', 'active', 'platform_owner'), "
            "(%s, 'Failure Target', 'active', 'member')",
            (owner_id, target_id),
        )

    repository = ManagementRepository(
        environment["urls"]["platform_control_app_preview"]
    )
    context = AuthContext(owner_id, Role.PLATFORM_OWNER, uuid4(), False)

    class InitialFailure:
        def append(self, command):
            raise AuditUnavailableError("required audit unavailable")

    with pytest.raises(HTTPException) as initial:
        ManagementService(repository, InitialFailure()).change_viewer(
            context,
            target_id,
            "access_approved",
            revoke=False,
            request_id=uuid4(),
        )
    assert initial.value.status_code == 503
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select role::text from platform_control.internal_users "
            "where internal_user_id = %s",
            (target_id,),
        ).fetchone() == ("member",)

    real_writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append_preview"]
    )

    class OutcomeFailure:
        def __init__(self):
            self.failed = False

        def append(self, command):
            if command.event_type.endswith("_completed") and not self.failed:
                self.failed = True
                raise AuditUnavailableError("required audit unavailable")
            return real_writer.append(command)

    request_id = uuid4()
    with pytest.raises(HTTPException) as outcome:
        ManagementService(repository, OutcomeFailure()).change_viewer(
            context,
            target_id,
            "access_approved",
            revoke=False,
            request_id=request_id,
        )
    assert outcome.value.status_code == 503
    assert outcome.value.detail["code"] == "management_mutation_indeterminate"

    ManagementService(repository, real_writer).change_viewer(
        context,
        target_id,
        "access_approved",
        revoke=False,
        request_id=request_id,
    )
    with psycopg.connect(environment["admin"]) as connection:
        user = connection.execute(
            "select role::text, role_audit_event_id from "
            "platform_control.internal_users where internal_user_id = %s",
            (target_id,),
        ).fetchone()
        events = connection.execute(
            "select event_type from platform_control.audit_events "
            "where request_id = %s order by event_type",
            (request_id,),
        ).fetchall()
    assert user[0] == "management_viewer"
    assert user[1] is not None
    assert events == [
        ("viewer_role_assignment_completed",),
        ("viewer_role_assignment_requested",),
    ]
