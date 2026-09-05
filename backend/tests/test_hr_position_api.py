from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.hr.models import (
    ConfirmedPositionPackage,
    PositionDetail,
    PositionDraftRecord,
    PositionDraftVersion,
    PositionRecord,
)
from app.hr.position_intelligence_models import PositionContextVersion
from app.hr.repository import HrConflict, HrNotFound, HrUnavailable, PositionPage
from app.hr.routes import build_hr_position_router


def _position(owner_id):
    now = datetime.now(UTC)
    return PositionRecord(
        uuid4(), owner_id, "official_site", "J11014", "算法工程师", "机器人",
        ("深圳",), "active", "active", "sync-v1", 2, now, now,
    )


def _draft(owner_id):
    now = datetime.now(UTC)
    return PositionDraftRecord(
        uuid4(), owner_id, "new_conversation", "conversation:test", None,
        "结构工程师", {}, {"message_seq": 1}, "interactive-v1", "proposed",
        None, 1, now, now,
    )


class FakeAuthorization:
    def __init__(self, outcome="allowed") -> None:
        self.outcome = outcome

    def decide_for_user_id(self, owner_id, agent_id):
        assert agent_id == "hr-bot"
        if self.outcome == "unavailable":
            raise AgentUseAuthorizationUnavailable()
        return SimpleNamespace(allowed=self.outcome == "allowed")


class FakeService:
    def __init__(self, owner_id) -> None:
        self.position_record = _position(owner_id)
        now = datetime.now(UTC)
        self.conversation_id = uuid4()
        self.draft_record = _draft(owner_id)
        self.draft_record = PositionDraftRecord(
            self.draft_record.draft_id, owner_id, "new_conversation",
            f"conversation:{self.conversation_id}", self.conversation_id,
            self.draft_record.title, self.draft_record.proposal,
            self.draft_record.evidence, self.draft_record.discovery_rule_version,
            "proposed", None, 2, now, now,
        )
        self.draft_version = PositionDraftVersion(
            uuid4(), owner_id, self.draft_record.draft_id, uuid4(), 3,
            "高级结构工程师",
            {
                "mission": {"text": "负责高可靠挤出系统交付。"},
                "jd": {"text": "负责喷嘴与挤出系统结构设计。"},
                "jr": {"text": "具备精密机械量产经验。"},
            },
            self.conversation_id, uuid4(), uuid4(), "hr-bot", "gpt-5", 1,
            now, now,
        )
        context = PositionContextVersion(
            uuid4(), owner_id, self.position_record.position_id, 1,
            "confirmed", self.draft_version.modules,
            self.draft_version.title, None, None, self.conversation_id,
            self.draft_version.source_turn_id, None, (), "hr-bot", "gpt-5",
            owner_id, owner_id, now, now, 1,
        )
        self.confirmed_package = ConfirmedPositionPackage(
            self.position_record, context, self.conversation_id
        )
        self.calls = []
        self.error = None

    def _result(self, value):
        if self.error:
            raise self.error
        return value

    def list_positions(self, owner_id, **filters):
        self.calls.append(("list", owner_id, filters))
        return self._result(PositionPage((self.position_record,), None))

    def position(self, owner_id, position_id):
        self.calls.append(("position", owner_id, position_id))
        return self._result(PositionDetail(
            self.position_record, 2, 1, 3, (uuid4(),), (uuid4(),), (uuid4(),),
            (uuid4(),),
        ))

    def list_drafts(self, owner_id, *, state=None, limit=100):
        self.calls.append(("drafts", owner_id, state, limit))
        return self._result((self.draft_record,))

    def propose_draft(self, **values):
        self.calls.append(("propose", values))
        return self._result(self.draft_record)

    def confirm_draft(self, *args, **values):
        self.calls.append(("confirm", args, values))
        return self._result(self.position_record)

    def latest_draft_version(self, owner_id, draft_id):
        self.calls.append(("latest_package", owner_id, draft_id))
        return self._result(self.draft_version)

    def position_package_for_conversation(self, owner_id, conversation_id):
        self.calls.append(("conversation_package", owner_id, conversation_id))
        if conversation_id != self.conversation_id:
            raise HrNotFound("position package not found")
        return self._result((self.draft_record, self.draft_version))

    def confirm_package(self, *args, **values):
        self.calls.append(("confirm_package", args, values))
        return self._result(self.confirmed_package)

    def merge_draft(self, *args, **values):
        self.calls.append(("merge", args, values))
        return self._result(self.draft_record)

    def dismiss_draft(self, *args, **values):
        self.calls.append(("dismiss", args, values))
        return self._result(self.draft_record)

    def bind_conversation(self, *args, **values):
        self.calls.append(("bind", args, values))
        return self._result(SimpleNamespace(
            owner_id=args[0], position_id=args[1], conversation_id=args[2],
            client_request_id=args[3], binding_kind=values["binding_kind"],
            previous_position_id=None, created_at=datetime.now(UTC),
        ))

    def promote_material(self, owner_id, position_id, attachment_id, request_id):
        self.calls.append(("promote_material", position_id, attachment_id))
        now = datetime.now(UTC)
        return SimpleNamespace(
            position_id=position_id, attachment_id=attachment_id, active=True,
            created_at=now, updated_at=now,
        )

    def remove_material(self, owner_id, position_id, attachment_id, request_id):
        self.calls.append(("remove_material", position_id, attachment_id))
        now = datetime.now(UTC)
        return SimpleNamespace(
            position_id=position_id, attachment_id=attachment_id, active=False,
            created_at=now, updated_at=now,
        )


def _client(*, stale=False, authorization="allowed"):
    owner_id = uuid4()
    service = FakeService(owner_id)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(
            owner_id, Role.MEMBER, uuid4(), stale
        )
        return await call_next(request)

    app.include_router(build_hr_position_router(
        service, FakeAuthorization(authorization)
    ))
    return TestClient(app), service, owner_id


class _SecurityAuth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None

    def __init__(self, owner_id, *, stale=False):
        self.owner_id = owner_id
        self.stale = stale

    def authenticate(self, token):
        if token != "valid":
            return None
        return (
            AuthContext(self.owner_id, Role.MEMBER, uuid4(), self.stale),
            b"csrf-digest",
        )

    def verify_csrf(self, token, digest):
        return token == "csrf-token" and digest == b"csrf-digest"


def _secured_client(*, stale=False):
    owner_id = uuid4()
    service = FakeService(owner_id)
    app = FastAPI()
    app.include_router(build_hr_position_router(service, FakeAuthorization()))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=_SecurityAuth(owner_id, stale=stale),
        public_assets=frozenset(),
        authorization=AuthorizationService(
            SimpleNamespace(permits=lambda *_: False)
        ),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": "csrf-token",
        "Idempotency-Key": str(uuid4()),
    }
    return client, service, headers


def test_position_reads_are_private_owner_scoped_and_explicitly_serialized() -> None:
    client, service, owner_id = _client()

    response = client.get("/api/hr/positions?source=official_site&limit=20")
    detail = client.get(f"/api/hr/positions/{service.position_record.position_id}")
    drafts = client.get("/api/hr/position-drafts?state=proposed")

    assert response.status_code == detail.status_code == drafts.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["items"][0]["official_job_id"] == "J11014"
    assert detail.json()["conversation_count"] == 2
    assert len(detail.json()["conversation_ids"]) == 1
    assert len(detail.json()["artifact_attachment_ids"]) == 1
    assert drafts.json()["items"][0]["evidence"] == {"message_seq": 1}
    assert service.calls[0][1] == owner_id


def test_hr_routes_fail_closed_when_grant_is_denied_or_unavailable() -> None:
    denied, _, _ = _client(authorization="denied")
    unavailable, _, _ = _client(authorization="unavailable")

    assert denied.get("/api/hr/positions").status_code == 403
    assert unavailable.get("/api/hr/positions").status_code == 503


def test_position_mutations_require_writable_identity_and_idempotency_uuid() -> None:
    stale, service, _ = _client(stale=True)
    current, _, _ = _client()
    payload = {
        "source_kind": "new_conversation",
        "source_key": "conversation:new",
        "source_conversation_id": None,
        "title": "结构工程师",
        "proposal": {},
        "evidence": {"message_seq": 1},
        "discovery_rule_version": "interactive-v1",
    }

    assert stale.post(
        "/api/hr/position-drafts", json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).status_code == 503
    assert not service.calls
    assert current.post("/api/hr/position-drafts", json=payload).status_code == 422
    invalid = current.post(
        "/api/hr/position-drafts", json={**payload, "external_ats": "beisen"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "HR position request invalid"}

    oversized = current.post(
        "/api/hr/position-drafts",
        json={**payload, "proposal": {"request": "x" * 131_073}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert oversized.status_code == 422
    assert oversized.json() == {"detail": "HR position request invalid"}

    oversized_evidence = current.post(
        "/api/hr/position-drafts",
        json={**payload, "evidence": {"excerpt": "中" * 22_000}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert oversized_evidence.status_code == 422
    assert oversized_evidence.json() == {"detail": "HR position request invalid"}


def test_draft_commands_forward_versions_and_conversation_binding() -> None:
    client, service, _ = _client()
    request_id = str(uuid4())
    draft_id = service.draft_record.draft_id
    target_id = service.position_record.position_id

    assert client.post(
        f"/api/hr/position-drafts/{draft_id}/merge",
        json={"target_position_id": str(target_id), "expected_row_version": 1},
        headers={"Idempotency-Key": request_id},
    ).status_code == 200
    assert service.calls[-1][0] == "merge"
    conversation_id = uuid4()
    assert client.post(
        f"/api/hr/positions/{target_id}/conversations/{conversation_id}",
        json={}, headers={"Idempotency-Key": str(uuid4())},
    ).status_code == 200
    assert service.calls[-1][0] == "bind"


def test_conversation_position_package_is_owner_scoped_and_strictly_serialized() -> None:
    client, service, owner_id = _client()

    response = client.get(
        f"/api/hr/conversations/{service.conversation_id}/position-package"
    )

    assert response.status_code == 200
    assert response.json() == {
        "draft_id": str(service.draft_record.draft_id),
        "draft_version_id": str(service.draft_version.draft_version_id),
        "conversation_id": str(service.conversation_id),
        "version_number": 3,
        "title": "高级结构工程师",
        "modules": {
            "mission": {"text": "负责高可靠挤出系统交付。"},
            "jd": {"text": "负责喷嘴与挤出系统结构设计。"},
            "jr": {"text": "具备精密机械量产经验。"},
        },
        "row_version": 2,
        "created_at": service.draft_version.created_at.isoformat(),
        "updated_at": service.draft_version.updated_at.isoformat(),
    }
    assert service.calls == [
        ("conversation_package", owner_id, service.conversation_id),
    ]
    assert response.headers["cache-control"] == "private, no-store"


def test_position_package_confirmation_is_atomic_and_strictly_serialized() -> None:
    client, service, owner_id = _client()
    request_id = uuid4()

    confirmed = client.post(
        f"/api/hr/position-drafts/{service.draft_record.draft_id}"
        f"/versions/{service.draft_version.draft_version_id}/confirm",
        headers={
            "Idempotency-Key": str(request_id),
            "X-CSRF-Token": "csrf",
        },
        json={"expected_row_version": 2},
    )

    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "position_id": str(service.position_record.position_id),
        "context_version_id": str(
            service.confirmed_package.context.context_version_id
        ),
        "conversation_id": str(service.conversation_id),
    }
    assert service.calls == [(
        "confirm_package",
        (
            owner_id,
            service.draft_record.draft_id,
            service.draft_version.draft_version_id,
            request_id,
        ),
        {"expected_row_version": 2},
    )]


def test_position_package_routes_conceal_absence_conflict_and_unavailability() -> None:
    client, service, _ = _client()
    package_path = (
        f"/api/hr/conversations/{service.conversation_id}/position-package"
    )
    confirm_path = (
        f"/api/hr/position-drafts/{service.draft_record.draft_id}"
        f"/versions/{service.draft_version.draft_version_id}/confirm"
    )

    service.error = HrNotFound("encrypted-content=secret")
    missing = client.get(package_path)
    service.error = HrConflict("raw database conflict")
    conflict = client.post(
        confirm_path,
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_row_version": 2},
    )
    service.error = HrUnavailable("artifact_locator=s3://secret")
    unavailable = client.get(package_path)

    assert missing.status_code == 404
    assert missing.json() == {"detail": "HR position not found"}
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "HR position conflict"}
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "HR position unavailable"}


def test_conversation_without_a_position_package_returns_404() -> None:
    client, service, _ = _client()

    missing = client.get(f"/api/hr/conversations/{uuid4()}/position-package")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "HR position not found"}


def test_position_package_routes_deny_unentitled_and_read_only_identities() -> None:
    denied, denied_service, _ = _client(authorization="denied")
    stale, stale_service, _ = _client(stale=True)

    cross_owner = denied.get(
        f"/api/hr/conversations/{denied_service.conversation_id}/position-package"
    )
    read_only = stale.post(
        f"/api/hr/position-drafts/{stale_service.draft_record.draft_id}"
        f"/versions/{stale_service.draft_version.draft_version_id}/confirm",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_row_version": 2},
    )

    assert cross_owner.status_code == 403
    assert read_only.status_code == 503
    assert denied_service.calls == stale_service.calls == []


def test_position_package_routes_reject_cross_owner_service_results() -> None:
    client, service, owner_id = _client()
    foreign_owner = uuid4()
    service.draft_record = replace(
        service.draft_record, owner_id=foreign_owner
    )
    service.draft_version = replace(
        service.draft_version, owner_id=foreign_owner
    )
    foreign_position = replace(
        service.confirmed_package.position, owner_id=foreign_owner
    )
    foreign_context = replace(
        service.confirmed_package.context,
        owner_id=foreign_owner,
        created_by=foreign_owner,
        confirmed_by=foreign_owner,
    )
    service.confirmed_package = ConfirmedPositionPackage(
        foreign_position, foreign_context, service.conversation_id
    )

    package = client.get(
        f"/api/hr/conversations/{service.conversation_id}/position-package"
    )
    assert service.calls == [
        ("conversation_package", owner_id, service.conversation_id)
    ]
    confirmed = client.post(
        f"/api/hr/position-drafts/{service.draft_record.draft_id}"
        f"/versions/{service.draft_version.draft_version_id}/confirm",
        headers={"Idempotency-Key": str(uuid4())},
        json={"expected_row_version": 2},
    )

    assert package.status_code == confirmed.status_code == 403
    assert package.json() == confirmed.json() == {
        "detail": "HR position access denied"
    }


def test_position_material_promotion_and_removal_are_explicit_mutations() -> None:
    client, service, _ = _client()
    position_id, attachment_id = service.position_record.position_id, uuid4()
    path = f"/api/hr/positions/{position_id}/materials/{attachment_id}"
    headers = {"Idempotency-Key": str(uuid4())}

    promoted = client.post(path, json={}, headers=headers)
    removed = client.delete(path, headers={"Idempotency-Key": str(uuid4())})

    assert promoted.status_code == removed.status_code == 200
    assert promoted.json()["active"] is True
    assert removed.json()["active"] is False
    assert [call[0] for call in service.calls] == [
        "promote_material", "remove_material"
    ]


def test_repository_failures_have_stable_concealed_http_projection() -> None:
    for error, status in (
        (HrNotFound(), 404), (HrConflict(), 409), (HrUnavailable(), 503)
    ):
        client, service, _ = _client()
        service.error = error
        response = client.get(f"/api/hr/positions/{uuid4()}")
        assert response.status_code == status
        assert response.json() == {"detail": {
            404: "HR position not found",
            409: "HR position conflict",
            503: "HR position unavailable",
        }[status]}


def test_every_hr_route_passes_the_real_identity_security_middleware() -> None:
    client, service, headers = _secured_client()
    position_id = service.position_record.position_id
    draft_id = service.draft_record.draft_id
    conversation_id = uuid4()
    attachment_id = uuid4()
    proposal = {
        "source_kind": "new_conversation",
        "source_key": "conversation:secured",
        "source_conversation_id": None,
        "title": "结构工程师",
        "proposal": {},
        "evidence": {"message_seq": 1},
        "discovery_rule_version": "interactive-v1",
    }
    version = {"expected_row_version": 1}
    requests = (
        client.get("/api/hr/positions"),
        client.get(f"/api/hr/positions/{position_id}"),
        client.get("/api/hr/position-drafts"),
        client.get(
            f"/api/hr/conversations/{service.conversation_id}/position-package"
        ),
        client.post("/api/hr/position-drafts", json=proposal, headers=headers),
        client.post(
            f"/api/hr/position-drafts/{draft_id}/confirm",
            json=version, headers=headers,
        ),
        client.post(
            f"/api/hr/position-drafts/{draft_id}/merge",
            json={**version, "target_position_id": str(position_id)},
            headers=headers,
        ),
        client.post(
            f"/api/hr/position-drafts/{draft_id}/dismiss",
            json=version, headers=headers,
        ),
        client.post(
            f"/api/hr/position-drafts/{draft_id}"
            f"/versions/{service.draft_version.draft_version_id}/confirm",
            json={"expected_row_version": 2}, headers=headers,
        ),
        client.post(
            f"/api/hr/positions/{position_id}/conversations/{conversation_id}",
            json={}, headers=headers,
        ),
        client.post(
            f"/api/hr/positions/{position_id}/materials/{attachment_id}",
            json={}, headers=headers,
        ),
        client.delete(
            f"/api/hr/positions/{position_id}/materials/{attachment_id}",
            headers=headers,
        ),
    )

    assert [response.status_code for response in requests] == [200] * 12


def test_real_security_middleware_blocks_stale_hr_mutation_before_router() -> None:
    client, service, headers = _secured_client(stale=True)
    response = client.post(
        "/api/hr/position-drafts",
        json={
            "source_kind": "new_conversation",
            "source_key": "conversation:stale",
            "source_conversation_id": None,
            "title": "结构工程师",
            "proposal": {},
            "evidence": {},
            "discovery_rule_version": "interactive-v1",
        },
        headers=headers,
    )

    assert response.status_code == 503
    assert service.calls == []
