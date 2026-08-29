from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.control_plane.models import AuthContext, Role
from app.control_plane.partner_models import PartnerIdentityError
from app.control_plane.routes_manage import authenticated_context, csrf_protection
from app.control_plane.routes_partner import partner_service, router

NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
OWNER_ID = UUID("10000000-0000-4000-8000-000000000001")
ORGANIZATION_ID = UUID("20000000-0000-4000-8000-000000000001")
OPERATOR_ID = UUID("30000000-0000-4000-8000-000000000001")
SUBJECT_ID = UUID("40000000-0000-4000-8000-000000000001")
BINDING_REQUEST_ID = UUID("50000000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("60000000-0000-4000-8000-000000000001")


class FakePartnerService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail: PartnerIdentityError | None = None

    @staticmethod
    def _organization() -> dict[str, object]:
        return {
            "partner_organization_id": ORGANIZATION_ID,
            "display_name": "合作方甲",
            "status": "active",
            "created_at": NOW,
            "updated_at": NOW,
            "invalidated_at": None,
        }

    @staticmethod
    def _operator(*, granted: bool = False) -> dict[str, object]:
        return {
            "partner_operator_id": OPERATOR_ID,
            "subject_id": SUBJECT_ID,
            "partner_organization_id": ORGANIZATION_ID,
            "display_name": "合作方客服",
            "status": "active",
            "fae_grant_active": granted,
            "fae_granted_at": NOW if granted else None,
            "created_at": NOW,
            "updated_at": NOW,
            "invalidated_at": None,
        }

    @staticmethod
    def _binding(*, status: str = "pending") -> dict[str, object]:
        return {
            "binding_request_id": BINDING_REQUEST_ID,
            "provider_kind": "qianniu",
            "display_name": "待绑定坐席",
            "status": status,
            "verified_at": NOW,
            "requested_at": NOW,
            "expires_at": NOW,
            "resolved_at": NOW if status != "pending" else None,
            "linked_partner_operator_id": OPERATOR_ID if status == "linked" else None,
        }

    def _record(self, name: str, values: dict[str, object]) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((name, values))

    def list_organizations(self):
        self._record("list_organizations", {})
        return (self._organization(),)

    def create_organization(self, **values):
        self._record("create_organization", values)
        return self._organization()

    def set_organization_status(self, **values):
        self._record("set_organization_status", values)
        return {**self._organization(), "status": str(values["status"])}

    def list_operators(self):
        self._record("list_operators", {})
        return (self._operator(),)

    def create_operator(self, **values):
        self._record("create_operator", values)
        return self._operator()

    def set_operator_status(self, **values):
        self._record("set_operator_status", values)
        return {**self._operator(), "status": str(values["status"])}

    def grant_fae(self, **values):
        self._record("grant_fae", values)
        return self._operator(granted=True)

    def revoke_fae(self, **values):
        self._record("revoke_fae", values)
        return self._operator()

    def list_binding_requests(self):
        self._record("list_binding_requests", {})
        return (self._binding(),)

    def link_binding_request(self, **values):
        self._record("link_binding_request", values)
        return self._binding(status="linked")

    def reject_binding_request(self, **values):
        self._record("reject_binding_request", values)
        return self._binding(status="rejected")


def _client(
    role: Role,
    service: FakePartnerService | None = None,
    *,
    csrf_verified: bool = True,
) -> tuple[TestClient, FakePartnerService]:
    selected = service or FakePartnerService()
    app = FastAPI()
    app.state.partner_service = selected
    app.include_router(router)
    context = AuthContext(
        OWNER_ID if role is Role.PLATFORM_OWNER else uuid4(),
        role,
        uuid4(),
        False,
    )
    app.dependency_overrides[authenticated_context] = lambda: context
    app.dependency_overrides[csrf_protection] = lambda: csrf_verified
    app.dependency_overrides[partner_service] = lambda: selected
    return TestClient(app), selected


MUTATION_CASES = (
    (
        "POST",
        "/api/v1/manage/partners/organizations",
        {
            "display_name": "合作方甲",
            "reason": "客服试点",
            "request_id": str(REQUEST_ID),
        },
    ),
    (
        "PATCH",
        f"/api/v1/manage/partners/organizations/{ORGANIZATION_ID}/status",
        {"status": "suspended", "reason": "暂停服务", "request_id": str(REQUEST_ID)},
    ),
    (
        "POST",
        "/api/v1/manage/partners/operators",
        {
            "partner_organization_id": str(ORGANIZATION_ID),
            "display_name": "合作方客服",
            "reason": "客服试点",
            "request_id": str(REQUEST_ID),
        },
    ),
    (
        "PATCH",
        f"/api/v1/manage/partners/operators/{OPERATOR_ID}/status",
        {"status": "disabled", "reason": "合同结束", "request_id": str(REQUEST_ID)},
    ),
    (
        "PUT",
        f"/api/v1/manage/partners/operators/{OPERATOR_ID}/fae-grant",
        {"reason": "授权试点", "request_id": str(REQUEST_ID)},
    ),
    (
        "DELETE",
        f"/api/v1/manage/partners/operators/{OPERATOR_ID}/fae-grant",
        {"reason": "撤销授权", "request_id": str(REQUEST_ID)},
    ),
    (
        "POST",
        f"/api/v1/manage/partners/binding-requests/{BINDING_REQUEST_ID}/link",
        {
            "partner_operator_id": str(OPERATOR_ID),
            "reason": "名单核验通过",
            "request_id": str(REQUEST_ID),
        },
    ),
    (
        "POST",
        f"/api/v1/manage/partners/binding-requests/{BINDING_REQUEST_ID}/reject",
        {"reason": "不在试点名单", "request_id": str(REQUEST_ID)},
    ),
)

READ_CASES = (
    ("GET", "/api/v1/manage/partners/organizations"),
    ("GET", "/api/v1/manage/partners/operators"),
    ("GET", "/api/v1/manage/partners/binding-requests"),
)


@pytest.mark.parametrize(
    "role", [Role.MEMBER, Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN]
)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    tuple((*case, None) for case in READ_CASES) + MUTATION_CASES,
)
def test_only_platform_owner_can_manage_partners(
    role: Role, method: str, path: str, body: dict[str, str] | None
) -> None:
    client, service = _client(role)

    response = client.request(method, path, json=body)

    assert response.status_code == 403
    assert service.calls == []


def test_non_owner_is_denied_before_partner_service_availability_is_disclosed() -> None:
    app = FastAPI()
    app.include_router(router)
    context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    app.dependency_overrides[authenticated_context] = lambda: context

    response = TestClient(app).get("/api/v1/manage/partners/organizations")

    assert response.status_code == 403
    assert response.json() == {"detail": "platform owner required"}


def test_owner_lists_only_safe_partner_projections() -> None:
    client, _service = _client(Role.PLATFORM_OWNER)

    responses = [client.request(method, path) for method, path in READ_CASES]

    assert all(response.status_code == 200 for response in responses)
    serialized = "".join(response.text.lower() for response in responses)
    for forbidden in (
        "provider_subject",
        "ciphertext",
        "lookup_hmac",
        "token",
        "secret",
        "raw-seat-42",
    ):
        assert forbidden not in serialized
    assert "合作方客服" in serialized
    assert "qianniu" in serialized


@pytest.mark.parametrize(("method", "path", "body"), MUTATION_CASES)
def test_owner_mutations_forward_client_request_id_and_return_it(
    method: str, path: str, body: dict[str, str]
) -> None:
    client, service = _client(Role.PLATFORM_OWNER)

    response = client.request(method, path, json=body)

    assert response.status_code == 200
    assert response.json()["request_id"] == str(REQUEST_ID)
    mutation_calls = [call for call in service.calls if "request_id" in call[1]]
    assert len(mutation_calls) == 1
    name, values = mutation_calls[0]
    assert name not in {
        "list_organizations",
        "list_operators",
        "list_binding_requests",
    }
    assert values["actor_id"] == OWNER_ID
    assert values["request_id"] == REQUEST_ID


def test_partner_mutation_requires_route_level_csrf_verification() -> None:
    client, service = _client(Role.PLATFORM_OWNER, csrf_verified=False)

    response = client.post(
        "/api/v1/manage/partners/organizations",
        json={
            "display_name": "合作方甲",
            "reason": "客服试点",
            "request_id": str(REQUEST_ID),
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF verification failed"}
    assert service.calls == []


def test_delete_fae_grant_requires_canonical_operator_uuid() -> None:
    client, service = _client(Role.PLATFORM_OWNER)

    response = client.request(
        "DELETE",
        f"/api/v1/manage/partners/operators/{{{OPERATOR_ID}}}/fae-grant",
        json={"reason": "撤销授权", "request_id": str(REQUEST_ID)},
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "合作方甲", "reason": "客服试点"},
        {"display_name": "合作方甲", "reason": "ab", "request_id": str(REQUEST_ID)},
        {"display_name": "合作方甲", "reason": "   ", "request_id": str(REQUEST_ID)},
        {
            "display_name": "合作方甲",
            "reason": "客服试点",
            "request_id": "not-a-uuid",
        },
        {
            "display_name": "合作方甲",
            "reason": "客服试点",
            "request_id": str(REQUEST_ID),
            "provider_subject": "must-not-be-accepted",
        },
    ],
)
def test_mutation_models_require_strict_safe_fields(body: dict[str, str]) -> None:
    client, service = _client(Role.PLATFORM_OWNER)

    response = client.post("/api/v1/manage/partners/organizations", json=body)

    assert response.status_code == 422
    assert service.calls == []


def test_status_model_rejects_unknown_status() -> None:
    client, service = _client(Role.PLATFORM_OWNER)

    response = client.patch(
        f"/api/v1/manage/partners/operators/{OPERATOR_ID}/status",
        json={"status": "deleted", "reason": "合同结束", "request_id": str(REQUEST_ID)},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_partner_mutation_5xx_is_explicitly_indeterminate_with_client_request_id() -> (
    None
):
    service = FakePartnerService()
    service.fail = PartnerIdentityError("partner_identity_unavailable", 503)
    client, _service = _client(Role.PLATFORM_OWNER, service)

    response = client.post(
        "/api/v1/manage/partners/organizations",
        json={
            "display_name": "合作方甲",
            "reason": "客服试点",
            "request_id": str(REQUEST_ID),
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "partner_mutation_indeterminate",
            "request_id": str(REQUEST_ID),
        }
    }
