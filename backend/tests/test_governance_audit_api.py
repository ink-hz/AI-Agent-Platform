from __future__ import annotations

import json
import threading
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
from app.control_plane.fae_access import (
    FaeWorkbenchAccessService,
    FaeWorkbenchAccessUnavailable,
)
from app.control_plane.voc_access import VocWorkbenchAccessService
from app.control_plane.audit import AuditWriter
from test_control_plane_migration import control_database


OWNER = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
ADMIN = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
VIEWER = AuthContext(uuid4(), Role.MANAGEMENT_VIEWER, uuid4(), False)
MEMBER = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)


class FakeManagementRepository:
    def __init__(self) -> None:
        self.member_key = uuid4()
        self.generation_id = uuid4()
        self.fae_grant_id = uuid4()
        self.fae_internal_user_id = uuid4()
        self.fae_grant_replays = {}
        self.voc_grant_id = uuid4()
        self.voc_internal_user_id = uuid4()
        self.voc_grant_replays = {}
        self.voc_allowed_user_ids = {self.voc_internal_user_id}
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
        self.fae_grants = [{
            "grant_id": self.fae_grant_id,
            "internal_user_id": self.fae_internal_user_id,
            "display_name": "花名一",
            "status": "active",
            "permission": "manager",
            "created_at": "2026-09-01T00:00:00+00:00",
            "row_version": 0,
        }]
        self.voc_grants = [{
            "grant_id": self.voc_grant_id,
            "internal_user_id": self.voc_internal_user_id,
            "display_name": "稻夫",
            "status": "active",
            "permission": "manager",
            "created_at": "2026-09-04T00:00:00+00:00",
            "row_version": 0,
        }]

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

    def assign_admin(
        self, actor, target, operation_id, expected_version, audit_event_id
    ):
        mutation = ("admin_assign", actor, target, operation_id, audit_event_id)
        if mutation not in self.mutations:
            self.mutations.append(mutation)
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_version,
            "expected_causal_row_version": 0,
        }
        state = self.viewer_state(target)
        state.update({"role": "platform_admin", "row_version": expected_version + 1})
        return {
            "operation_id": str(operation_id),
            "previous_role": "member",
            "new_role": "platform_admin",
            "row_version": expected_version + 1,
            "session_revocation_count": 0,
            "previous_scopes": [],
            "new_scopes": [],
        }

    def revoke_admin(
        self, actor, target, operation_id, expected_version, audit_event_id
    ):
        mutation = ("admin_revoke", actor, target, operation_id, audit_event_id)
        if mutation not in self.mutations:
            self.mutations.append(mutation)
        self.revocations.append(target)
        self.ledger[operation_id] = {
            "expected_target_row_version": expected_version,
            "expected_causal_row_version": 0,
        }
        state = self.viewer_state(target)
        state.update({"role": "member", "row_version": expected_version + 1})
        return {
            "operation_id": str(operation_id),
            "previous_role": "platform_admin",
            "new_role": "member",
            "row_version": expected_version + 1,
            "session_revocation_count": 1,
            "previous_scopes": [],
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

    def active_fae_workbench_member(self, display_name):
        if display_name != "花名一":
            raise ValueError("directory_member_not_found")
        return {
            "generation_id": self.generation_id,
            "member_key": self.member_key,
        }

    def fae_workbench_grant_replay(self, actor, display_name, operation_id):
        return self.fae_grant_replays.get((actor, display_name, operation_id))

    def list_fae_workbench_grants(self):
        return self.fae_grants

    def grant_fae_workbench(
        self,
        actor,
        display_name,
        operation_id,
        expected_generation_id,
        expected_member_key,
        new_user_id,
        corporate_identity_id,
        union_identity_id,
        audit_event_id,
    ):
        self.mutations.append((
            "grant_fae",
            actor,
            display_name,
            operation_id,
            expected_generation_id,
            expected_member_key,
            new_user_id,
            corporate_identity_id,
            union_identity_id,
            audit_event_id,
        ))
        result = {
            "operation_id": str(operation_id),
            "grant_id": str(self.fae_grant_id),
            "internal_user_id": str(self.fae_internal_user_id),
            "permission": "manager",
            "row_version": 0,
        }
        self.fae_grant_replays[(actor, display_name, operation_id)] = {
            "generation_id": expected_generation_id,
            "member_key": expected_member_key,
            "result": result,
        }
        return result

    def revoke_fae_workbench(
        self,
        actor,
        target,
        operation_id,
        expected_row_version,
        audit_event_id,
    ):
        self.mutations.append((
            "revoke_fae",
            actor,
            target,
            operation_id,
            expected_row_version,
            audit_event_id,
        ))
        self.fae_grants = [
            grant for grant in self.fae_grants
            if grant["internal_user_id"] != target
        ]
        return {
            "operation_id": str(operation_id),
            "grant_id": str(self.fae_grant_id),
            "internal_user_id": str(target),
            "permission": "manager",
            "row_version": expected_row_version + 1,
        }

    def allows(self, internal_user_id):
        return internal_user_id in self.voc_allowed_user_ids

    def active_voc_workbench_member(self, display_name):
        if display_name != "稻夫":
            raise ValueError("directory_member_not_found")
        return {
            "generation_id": self.generation_id,
            "member_key": self.member_key,
        }

    def voc_workbench_grant_replay(self, actor, display_name, operation_id):
        return self.voc_grant_replays.get((actor, display_name, operation_id))

    def list_voc_workbench_grants(self):
        return self.voc_grants

    def grant_voc_workbench(
        self,
        actor,
        display_name,
        operation_id,
        expected_generation_id,
        expected_member_key,
        new_user_id,
        corporate_identity_id,
        union_identity_id,
        audit_event_id,
    ):
        self.mutations.append((
            "grant_voc",
            actor,
            display_name,
            operation_id,
            expected_generation_id,
            expected_member_key,
            new_user_id,
            corporate_identity_id,
            union_identity_id,
            audit_event_id,
        ))
        result = {
            "operation_id": str(operation_id),
            "grant_id": str(self.voc_grant_id),
            "internal_user_id": str(self.voc_internal_user_id),
            "permission": "manager",
            "row_version": 0,
        }
        self.voc_grant_replays[(actor, display_name, operation_id)] = {
            "generation_id": expected_generation_id,
            "member_key": expected_member_key,
            "result": result,
        }
        return result

    def revoke_voc_workbench(
        self,
        actor,
        target,
        operation_id,
        expected_row_version,
        audit_event_id,
    ):
        self.mutations.append((
            "revoke_voc",
            actor,
            target,
            operation_id,
            expected_row_version,
            audit_event_id,
        ))
        self.voc_allowed_user_ids.discard(target)
        self.voc_grants = [
            grant for grant in self.voc_grants
            if grant["internal_user_id"] != target
        ]
        return {
            "operation_id": str(operation_id),
            "grant_id": str(self.voc_grant_id),
            "internal_user_id": str(target),
            "permission": "manager",
            "row_version": expected_row_version + 1,
        }


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
    service = ManagementService(
        repository,
        audit,
        hard_stale_audit=lambda *_args: None,
    )
    app = FastAPI()
    app.state.fae_access = FaeWorkbenchAccessService(repository, audit)
    app.state.voc_access = VocWorkbenchAccessService(repository, audit)
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


def test_owner_can_list_fae_workbench_grants_without_private_identity() -> None:
    client, repository, _ = _client(OWNER)

    response = client.get("/api/v1/manage/fae-workbench/grants")

    assert response.status_code == 200
    assert response.json() == {
        "grants": [{
            "grant_id": str(repository.fae_grant_id),
            "internal_user_id": str(repository.fae_internal_user_id),
            "display_name": "花名一",
            "status": "active",
            "permission": "manager",
            "created_at": "2026-09-01T00:00:00+00:00",
            "row_version": 0,
        }]
    }
    assert "provider_id" not in response.text
    assert "unionid" not in response.text
    assert "mobile" not in response.text


def test_owner_grants_fae_workbench_access_by_unique_display_name_only() -> None:
    client, repository, audit = _client(OWNER)
    request_id = uuid4()

    response = client.post(
        "/api/v1/manage/fae-workbench/grants",
        json={
            "display_name": "花名一",
            "reason": "fae_workbench_access_approved",
            "request_id": str(request_id),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "internal_user_id": str(repository.fae_internal_user_id),
        "row_version": 0,
    }
    mutation = repository.mutations[-1]
    assert mutation[:6] == (
        "grant_fae",
        OWNER.internal_user_id,
        "花名一",
        request_id,
        repository.generation_id,
        repository.member_key,
    )
    assert mutation[6] != mutation[7] != mutation[8]
    assert [command.event_type for command in audit.commands] == [
        "fae_workbench_grant_requested",
        "fae_workbench_grant_completed",
    ]
    assert audit.commands[0].target_type == "directory_member"
    assert audit.commands[0].target_id == str(repository.member_key)
    assert audit.commands[0].metadata == {
        "operation_id": str(request_id),
        "expected_generation_id": str(repository.generation_id),
        "expected_member_key": str(repository.member_key),
        "result": "requested",
    }


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (ValueError("directory_member_not_found"), 409, "directory_member_not_found"),
        (
            FaeWorkbenchAccessUnavailable("control unavailable"),
            503,
            "fae workbench access unavailable",
        ),
    ],
)
def test_fae_grant_name_resolution_fails_closed(
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    client, repository, audit = _client(OWNER)

    def fail_resolution(_display_name):
        raise error

    repository.active_fae_workbench_member = fail_resolution
    response = client.post(
        "/api/v1/manage/fae-workbench/grants",
        json={
            "display_name": "不存在",
            "reason": "fae_workbench_access_approved",
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert repository.mutations == []
    assert audit.commands == []


def test_fae_workbench_grant_request_id_derives_replay_stable_identity_ids() -> None:
    client, repository, _ = _client(OWNER)
    request_id = uuid4()
    payload = {
        "display_name": "花名一",
        "reason": "fae_workbench_access_approved",
        "request_id": str(request_id),
    }

    assert client.post("/api/v1/manage/fae-workbench/grants", json=payload).status_code == 200
    assert client.post("/api/v1/manage/fae-workbench/grants", json=payload).status_code == 200

    first = repository.mutations[-2]
    second = repository.mutations[-1]
    assert first[6:9] == second[6:9]


def test_fae_grant_replays_before_directory_state_is_resolved_again() -> None:
    client, repository, _ = _client(OWNER)
    request_id = uuid4()
    payload = {
        "display_name": "花名一",
        "reason": "fae_workbench_access_approved",
        "request_id": str(request_id),
    }

    first = client.post("/api/v1/manage/fae-workbench/grants", json=payload)
    assert first.status_code == 200

    def changed_directory(_display_name):
        raise ValueError("directory_name_not_unique")

    repository.active_fae_workbench_member = changed_directory
    replay = client.post("/api/v1/manage/fae-workbench/grants", json=payload)

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len([entry for entry in repository.mutations if entry[0] == "grant_fae"]) == 2


def test_owner_revokes_fae_workbench_access_with_expected_grant_row_version() -> None:
    client, repository, audit = _client(OWNER)
    request_id = uuid4()

    response = client.request(
        "DELETE",
        f"/api/v1/manage/fae-workbench/grants/{repository.fae_internal_user_id}",
        json={
            "reason": "fae_workbench_access_revoked",
            "request_id": str(request_id),
            "expected_row_version": 0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "row_version": 1}
    assert repository.mutations[-1][:5] == (
        "revoke_fae",
        OWNER.internal_user_id,
        repository.fae_internal_user_id,
        request_id,
        0,
    )
    assert [command.event_type for command in audit.commands] == [
        "fae_workbench_revoke_requested",
        "fae_workbench_revoke_completed",
    ]
    assert audit.commands[0].metadata == {
        "operation_id": str(request_id),
        "expected_row_version": 0,
        "result": "requested",
    }


@pytest.mark.parametrize("context", [ADMIN, VIEWER, MEMBER])
def test_only_owner_can_manage_fae_workbench_grants(context: AuthContext) -> None:
    client, repository, _ = _client(context)

    assert client.get("/api/v1/manage/fae-workbench/grants").status_code == 403
    assert client.post(
        "/api/v1/manage/fae-workbench/grants",
        json={
            "display_name": "花名一",
            "reason": "fae_workbench_access_approved",
            "request_id": str(uuid4()),
        },
    ).status_code == 403
    assert client.request(
        "DELETE",
        f"/api/v1/manage/fae-workbench/grants/{repository.fae_internal_user_id}",
            json={
                "reason": "fae_workbench_access_revoked",
                "request_id": str(uuid4()),
                "expected_row_version": 0,
            },
        ).status_code == 403
    assert repository.mutations == []


def test_voc_access_keeps_global_roles_and_allows_only_granted_members() -> None:
    repository = FakeManagementRepository()
    access = VocWorkbenchAccessService(repository, FakeAuditWriter())

    assert access.allows(OWNER) is True
    assert access.allows(ADMIN) is True
    assert access.allows(VIEWER) is True
    assert access.allows(
        AuthContext(repository.voc_internal_user_id, Role.MEMBER, uuid4(), False)
    ) is True
    assert access.allows(MEMBER) is False


def test_owner_manages_voc_grant_without_changing_platform_role() -> None:
    client, repository, audit = _client(OWNER)
    listed = client.get("/api/v1/manage/voc-workbench/grants")

    assert listed.status_code == 200
    assert listed.json() == {"grants": [{
        **repository.voc_grants[0],
        "grant_id": str(repository.voc_grant_id),
        "internal_user_id": str(repository.voc_internal_user_id),
    }]}
    request_id = uuid4()
    granted = client.post(
        "/api/v1/manage/voc-workbench/grants",
        json={
            "display_name": "稻夫",
            "reason": "voc_workbench_access_approved",
            "request_id": str(request_id),
        },
    )
    assert granted.status_code == 200
    assert granted.json() == {
        "status": "ok",
        "internal_user_id": str(repository.voc_internal_user_id),
        "row_version": 0,
    }
    assert repository.mutations[-1][:6] == (
        "grant_voc",
        OWNER.internal_user_id,
        "稻夫",
        request_id,
        repository.generation_id,
        repository.member_key,
    )
    assert [command.event_type for command in audit.commands] == [
        "voc_workbench_grant_requested",
        "voc_workbench_grant_completed",
    ]

    revoked = client.request(
        "DELETE",
        f"/api/v1/manage/voc-workbench/grants/{repository.voc_internal_user_id}",
        json={
            "reason": "voc_workbench_access_revoked",
            "request_id": str(uuid4()),
            "expected_row_version": 0,
        },
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"status": "ok", "row_version": 1}
    assert repository.mutations[-1][0] == "revoke_voc"
    assert [command.event_type for command in audit.commands[-2:]] == [
        "voc_workbench_revoke_requested",
        "voc_workbench_revoke_completed",
    ]


@pytest.mark.parametrize("context", [ADMIN, VIEWER, MEMBER])
def test_only_owner_can_manage_voc_workbench_grants(context: AuthContext) -> None:
    client, repository, _ = _client(context)

    assert client.get("/api/v1/manage/voc-workbench/grants").status_code == 403
    assert client.post(
        "/api/v1/manage/voc-workbench/grants",
        json={
            "display_name": "稻夫",
            "reason": "voc_workbench_access_approved",
            "request_id": str(uuid4()),
        },
    ).status_code == 403
    assert client.request(
        "DELETE",
        f"/api/v1/manage/voc-workbench/grants/{repository.voc_internal_user_id}",
        json={
            "reason": "voc_workbench_access_revoked",
            "request_id": str(uuid4()),
            "expected_row_version": 0,
        },
    ).status_code == 403
    assert repository.mutations == []


def test_admin_can_manage_viewers_scopes_and_read_governance() -> None:
    client, repository, audit = _client(ADMIN)
    target = uuid4()

    assert client.get("/api/v1/manage/users").status_code == 200
    assert client.post(
        f"/api/v1/manage/viewers/{target}",
        json={"reason": "access_approved"},
    ).status_code == 200
    assert client.put(
        f"/api/v1/manage/viewers/{target}/observations/fae",
        json={"reason": "scope_approved"},
    ).status_code == 200
    assert client.get("/api/v1/manage/audit/governance").status_code == 200
    assert [entry[0] for entry in repository.mutations] == ["assign", "grant"]
    assert any(
        command.event_type == "governance_audit_read_requested"
        for command in audit.commands
    )


def test_owner_can_assign_and_revoke_admin_with_exact_reasons() -> None:
    client, repository, audit = _client(OWNER)
    target = uuid4()
    request_id = uuid4()

    assert client.post(
        f"/api/v1/manage/admins/{target}",
        json={
            "reason": "admin_access_approved",
            "request_id": str(request_id),
        },
    ).status_code == 200
    assert client.request(
        "DELETE",
        f"/api/v1/manage/admins/{target}",
        json={"reason": "admin_access_revoked"},
    ).status_code == 200
    assert [entry[0] for entry in repository.mutations] == [
        "admin_assign",
        "admin_revoke",
    ]
    assert [command.event_type for command in audit.commands if command.event_type.endswith("_requested")] == [
        "admin_role_assignment_requested",
        "admin_role_revocation_requested",
    ]


def test_owner_can_list_and_revoke_inactive_admin_by_stable_account() -> None:
    client, repository, _ = _client(OWNER)
    target = uuid4()
    repository.users = [{
        "internal_user_id": target,
        "display_name": "Inactive Administrator",
        "status": "inactive",
        "role": "platform_admin",
        "scopes": [],
    }]
    repository.states[target] = {
        "role": "platform_admin",
        "row_version": 3,
        "scopes": [],
    }

    listed = client.get("/api/v1/manage/users")
    assert listed.status_code == 200
    assert listed.json()["users"] == [{
        "internal_user_id": str(target),
        "display_name": "Inactive Administrator",
        "status": "inactive",
        "role": "platform_admin",
        "scopes": [],
    }]
    assert client.request(
        "DELETE",
        f"/api/v1/manage/admins/{target}",
        json={"reason": "admin_access_revoked"},
    ).status_code == 200
    assert [entry[0] for entry in repository.mutations] == ["admin_revoke"]


@pytest.mark.parametrize("context", [ADMIN, MEMBER, VIEWER])
@pytest.mark.parametrize(
    ("method", "reason"),
    [
        ("POST", "admin_access_approved"),
        ("DELETE", "admin_access_revoked"),
    ],
)
def test_only_owner_can_mutate_admins_without_repository_invocation(
    context: AuthContext, method: str, reason: str
) -> None:
    client, repository, audit = _client(context)

    assert client.request(
        method,
        f"/api/v1/manage/admins/{uuid4()}",
        json={"reason": reason},
    ).status_code == 403
    assert repository.mutations == []
    assert audit.commands == []


@pytest.mark.parametrize(
    ("method", "reason"),
    [("POST", "admin_access_approved"), ("DELETE", "admin_access_revoked")],
)
@pytest.mark.parametrize(
    ("csrf", "fresh", "expected"),
    [(False, True, 403), (True, False, 503)],
)
def test_admin_mutation_requires_csrf_and_fresh_directory_without_repository_invocation(
    method: str,
    reason: str,
    csrf: bool,
    fresh: bool,
    expected: int,
) -> None:
    client, repository, audit = _client(OWNER, csrf=csrf, fresh=fresh)

    assert client.request(
        method,
        f"/api/v1/manage/admins/{uuid4()}",
        json={"reason": reason},
    ).status_code == expected
    assert repository.mutations == []
    assert audit.commands == []


@pytest.mark.parametrize("role", ["platform_owner", "platform_admin"])
def test_admin_assignment_rejects_owner_and_existing_admin_targets(role: str) -> None:
    client, repository, audit = _client(OWNER)
    target = uuid4()
    repository.states[target] = {"role": role, "row_version": 7, "scopes": []}

    assert client.post(
        f"/api/v1/manage/admins/{target}",
        json={"reason": "admin_access_approved"},
    ).status_code == 409
    assert repository.mutations == []
    assert audit.commands == []


def test_admin_mutation_rejects_inexact_reason_without_repository_invocation() -> None:
    client, repository, audit = _client(OWNER)

    assert client.post(
        f"/api/v1/manage/admins/{uuid4()}",
        json={"reason": "access_approved"},
    ).status_code == 422
    assert repository.mutations == []
    assert audit.commands == []


def test_admin_initial_audit_failure_returns_503_without_mutation() -> None:
    client, repository, audit = _client(OWNER)
    audit.fail = True

    assert client.post(
        f"/api/v1/manage/admins/{uuid4()}",
        json={"reason": "admin_access_approved"},
    ).status_code == 503
    assert repository.mutations == []


def test_admin_assignment_replay_succeeds_without_second_mutation() -> None:
    client, repository, audit = _client(OWNER)
    audit.fail_completed_once = True
    target = uuid4()
    request_id = uuid4()
    body = {
        "reason": "admin_access_approved",
        "request_id": str(request_id),
    }

    first = client.post(f"/api/v1/manage/admins/{target}", json=body)
    assert first.status_code == 503
    assert first.json()["detail"] == {
        "code": "management_mutation_indeterminate",
        "request_id": str(request_id),
    }
    assert client.post(f"/api/v1/manage/admins/{target}", json=body).status_code == 200
    assert [entry[0] for entry in repository.mutations] == ["admin_assign"]


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
            "(%s,%s,'viewer_role_assignment_completed','internal_user',%s,%s,"
            "'completed','unsafe reason',%s::jsonb)",
            (legacy_id, actor, str(actor), uuid4(),
             json.dumps({"directory_generation_id": str(uuid4()),
                         "operation": "bind", "os_operator": "root",
                         "result": "requested", "role": "platform_owner",
                         "secret_evidence": "must-not-project"}),
             malformed_id, actor, str(actor), uuid4(),
             json.dumps({"linked_audit_event_id": str(uuid4()),
                         "previous_role": [], "new_role": "management_viewer",
                         "session_revocation_count": 0,
                         "result": "completed"})),
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


@pytest.mark.parametrize(
    ("event_type", "previous_role", "new_role"),
    [
        ("admin_role_assignment_completed", "member", "platform_admin"),
        ("admin_role_revocation_completed", "platform_admin", "member"),
    ],
)
def test_legacy_admin_projection_accepts_only_exact_role_transition(
    event_type, previous_role, new_role
) -> None:
    metadata = {
        "linked_audit_event_id": str(uuid4()),
        "previous_role": previous_role,
        "new_role": new_role,
        "session_revocation_count": 0,
        "result": "completed",
    }
    assert project_governance_metadata(metadata, event_type=event_type)[0] == (
        "legacy_005"
    )
    wrong_previous = {**metadata, "previous_role": "management_viewer"}
    wrong_new = {**metadata, "new_role": "platform_owner"}
    assert project_governance_metadata(
        wrong_previous, event_type=event_type
    ) == ("unsupported_redacted", {})
    assert project_governance_metadata(
        wrong_new, event_type=event_type
    ) == ("unsupported_redacted", {})


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
def test_real_admin_revocation_links_audit_and_revokes_sessions_atomically(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    admin_id = uuid4()
    departed_session_id = uuid4()
    orphan_session_id = uuid4()
    generation_id = uuid4()
    lookup_hmac = b"d" * 32
    with psycopg.connect(environment["admin"]) as connection:
        existing_owner = connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role = 'platform_owner'"
        ).fetchone()
        if existing_owner is not None:
            owner_id = existing_owner[0]
        else:
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id, display_name, status, role) values "
                "(%s, 'Admin API Owner', 'active', 'platform_owner')",
                (owner_id,),
            )
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id, status, content_sha256, completed_at) values "
            "(%s, 'complete', %s, now())",
            (generation_id, generation_id.hex * 2),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "active_generation_id=%s, last_complete_at=now(), updated_at=now() "
            "where singleton",
            (generation_id,),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id, display_name, status, role, "
            "last_confirmed_generation_id) values "
            "(%s, 'Admin API Target', 'active', 'member', "
            "%s)",
            (admin_id, generation_id),
        )
        connection.execute(
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,'employee',%s,1,%s,1)",
            (uuid4(), admin_id, lookup_hmac, b"encrypted-departure-subject"),
        )

    context = AuthContext(owner_id, Role.PLATFORM_OWNER, uuid4(), False)
    service = ManagementService(
        ManagementRepository(environment["urls"]["platform_control_app"]),
        AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append"]
        ),
    )
    service.change_admin(
        context,
        admin_id,
        "admin_access_approved",
        request_id=uuid4(),
    )

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.web_sessions "
            "(session_id, internal_user_id, token_hash, csrf_hash, "
            "idle_expires_at, absolute_expires_at) values "
            "(%s, %s, %s, %s, now() + interval '1 hour', "
            "now() + interval '8 hours')",
            (
                departed_session_id,
                admin_id,
                b"departed-admin-session",
                b"departed-admin-csrf",
            ),
        )

    with psycopg.connect(
        environment["urls"]["platform_directory_worker"]
    ) as connection:
        assert connection.execute(
            "select platform_control.apply_directory_departure_v21("
            "1,%s,now(),%s)",
            (lookup_hmac, "d" * 64),
        ).fetchone() == ("applied",)

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.web_sessions "
            "(session_id, internal_user_id, token_hash, csrf_hash, "
            "idle_expires_at, absolute_expires_at) values "
            "(%s, %s, %s, %s, now() + interval '1 hour', "
            "now() + interval '8 hours')",
            (
                orphan_session_id,
                admin_id,
                b"orphan-admin-session",
                b"orphan-admin-csrf",
            ),
        )

    revocation_request_id = uuid4()
    service.change_admin(
        context,
        admin_id,
        "admin_access_revoked",
        revoke=True,
        request_id=revocation_request_id,
    )
    service.change_admin(
        context,
        admin_id,
        "admin_access_revoked",
        revoke=True,
        request_id=revocation_request_id,
    )

    with psycopg.connect(environment["admin"]) as connection:
        user = connection.execute(
            "select role::text,status,locally_invalidated_at is not null,"
            "row_version,role_audit_event_id from "
            "platform_control.internal_users where internal_user_id = %s",
            (admin_id,),
        ).fetchone()
        sessions = connection.execute(
            "select revoked_at is not null,revoked_reason from "
            "platform_control.web_sessions where internal_user_id = %s",
            (admin_id,),
        ).fetchall()
        events = connection.execute(
            "select event_type,sanitized_before_after from "
            "platform_control.audit_events where request_id = %s "
            "order by occurred_at, event_type",
            (revocation_request_id,),
        ).fetchall()
    assert user[:4] == ("member", "inactive", True, 2)
    assert user[4] is not None
    assert set(sessions) == {
        (True, "dingtalk_departure"),
        (True, "admin_role_revoked"),
    }
    assert [event[0] for event in events] == [
        "admin_role_revocation_requested",
        "admin_role_revocation_completed",
    ]
    assert events[1][1]["linked_audit_event_id"] == str(user[4])
    assert events[1][1]["session_revocation_count"] == 1
    projected = ManagementRepository(
        environment["urls"]["platform_control_app"]
    ).governance_audit()
    assert {
        event["event_type"]
        for event in projected
        if event["target_internal_id"] == str(admin_id)
    } >= {
        "admin_role_revocation_requested",
        "admin_role_revocation_completed",
    }


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


@pytest.mark.postgres
def test_same_request_failure_and_success_are_serialized_across_databases(
    control_database,
) -> None:
    environment = control_database["environments"]["production"]
    owner_id, target_id, request_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select internal_user_id from platform_control.internal_users "
            "where role='platform_owner'"
        ).fetchone()
        if row is not None:
            owner_id = row[0]
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Race Target','active','member')",
            (target_id,),
        )
        if row is None:
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,status,role) values "
                "(%s,'Race Owner','active','platform_owner')", (owner_id,)
            )
    repository = ManagementRepository(
        environment["urls"]["platform_control_app"]
    )
    writer = AuditWriter.from_database_url(
        environment["urls"]["platform_audit_append"]
    )
    context = AuthContext(owner_id, Role.PLATFORM_OWNER, uuid4(), False)
    failure_ready = threading.Event()
    release_failure = threading.Event()
    errors: list[int] = []

    def unavailable_mutation() -> None:
        class BlockingRepository:
            environment = "production"

            def mutation_precondition(self, *args):
                return None

            def viewer_state(self, target):
                return {"role": "member", "row_version": 0, "scopes": []}

            def assign_viewer(self, *args):
                failure_ready.set()
                release_failure.wait(timeout=10)
                raise RuntimeError("control unavailable")

        try:
            ManagementService(BlockingRepository(), writer).change_viewer(
                context, target_id, "access_approved", revoke=False,
                request_id=request_id,
            )
        except HTTPException as error:
            errors.append(error.status_code)

    first = threading.Thread(target=unavailable_mutation)
    first.start()
    assert failure_ready.wait(timeout=10)
    second_done = threading.Event()

    def successful_retry() -> None:
        try:
            ManagementService(repository, writer).change_viewer(
                context, target_id, "access_approved", revoke=False,
                request_id=request_id,
            )
        except HTTPException as error:
            errors.append(error.status_code)
        finally:
            second_done.set()

    second = threading.Thread(target=successful_retry)
    second.start()
    assert not second_done.wait(timeout=0.2)
    release_failure.set()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    with psycopg.connect(environment["admin"]) as connection:
        mutation_count = connection.execute(
            "select count(*) from platform_control.management_mutations "
            "where operation_id=%s", (request_id,)
        ).fetchone()[0]
        terminal = connection.execute(
            "select result from platform_control.audit_events "
            "where request_id=%s and result in ('completed','failed')",
            (request_id,),
        ).fetchall()
    assert mutation_count <= 1
    assert len(terminal) <= 1
    assert terminal == ([("completed",)] if mutation_count else [("failed",)])
