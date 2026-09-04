from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agent_brain.authorization import AgentUseAuthorizationUnavailable
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.hr.models import PositionDetail, PositionDraftRecord, PositionRecord
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
        self.draft_record = _draft(owner_id)
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

    assert [response.status_code for response in requests] == [200] * 10


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
