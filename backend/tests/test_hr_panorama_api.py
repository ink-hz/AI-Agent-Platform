from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, Role
from app.hr.panorama_models import (
    PanoramaReport,
    PanoramaRun,
    PublicJobSnapshot,
    TalentInsightVersion,
    TalentSource,
)
from app.hr.panorama_repository import (
    PanoramaConflict,
    PanoramaNotFound,
    PanoramaUnavailable,
)

NOW = datetime(2026, 9, 5, 8, tzinfo=UTC)


def _router_builder():
    try:
        from app.hr.panorama_routes import build_panorama_router
    except ImportError:
        pytest.fail("Panorama router is absent")
    return build_panorama_router


class FakePanoramaService:
    def __init__(self, owner_id: UUID) -> None:
        self.owner_id = owner_id
        self.source = TalentSource(
            uuid4(),
            owner_id,
            uuid4(),
            "company",
            f"company-{uuid4().hex}",
            "联合光电",
            ("Union Optech",),
            ("https://example.com/jobs",),
            True,
            NOW,
            NOW,
        )
        self.run = PanoramaRun(
            uuid4(),
            owner_id,
            uuid4(),
            (self.source.source_id,),
            uuid4(),
            "queued",
            None,
            {},
            1,
            None,
            None,
            NOW,
            NOW,
        )
        observation_id = uuid4()
        self.snapshot = PublicJobSnapshot(
            uuid4(),
            owner_id,
            observation_id,
            self.run.run_id,
            self.source.source_id,
            "job-1",
            "结构工程师",
            "中山",
            "负责精密结构设计",
            "五年以上经验",
            "https://example.com/jobs/1",
            NOW,
            "a" * 64,
            "open",
            NOW,
        )
        self.insight = TalentInsightVersion(
            uuid4(),
            owner_id,
            uuid4(),
            self.run.run_id,
            1,
            (self.source.source_id,),
            (self.snapshot.snapshot_id,),
            (
                {
                    "fact_id": "f1",
                    "text": "公开招聘结构工程师",
                    "snapshot_id": str(self.snapshot.snapshot_id),
                    "observation_id": str(observation_id),
                    "source_url": self.snapshot.source_url,
                    "observed_at": "2026-09-05T08:00:00Z",
                },
            ),
            ({"text": "结构投入增加", "basis_fact_ids": ("f1",)},),
            ({"text": "招聘人数未知"},),
            {"结构": 4},
            "结构人才需求上升",
            self.run.conversation_id,
            uuid4(),
            "hr-bot",
            "gpt-5",
            NOW,
        )
        self.report_value = PanoramaReport(
            insight=self.insight,
            sources=(self.source,),
            snapshots=(self.snapshot,),
        )
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def _result(self, value):
        if self.error is not None:
            raise self.error
        return value

    def add_company(self, **values):
        self.calls.append(("add_company", values))
        return self._result(self.source)

    def list_companies(self, owner_id, *, include_inactive=False, limit=100):
        self.calls.append(("list_companies", owner_id, include_inactive, limit))
        return self._result((self.source,))

    def start_run(self, **values):
        self.calls.append(("start_run", values))
        return self._result(self.run)

    def run_status(self, owner_id, run_id):
        self.calls.append(("run_status", owner_id, run_id))
        if run_id != self.run.run_id:
            raise PanoramaNotFound()
        return self._result(self.run)

    def list_reports(self, owner_id, *, limit=100):
        self.calls.append(("list_reports", owner_id, limit))
        return self._result((self.insight,))

    def report(self, owner_id, insight_version_id):
        self.calls.append(("report", owner_id, insight_version_id))
        if insight_version_id != self.insight.insight_version_id:
            raise PanoramaNotFound()
        return self._result(self.report_value)


def _client(
    *,
    owner_id: UUID | None = None,
    stale: bool = False,
    entitled: bool = True,
):
    owner_id = owner_id or uuid4()
    service = FakePanoramaService(owner_id)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(owner_id, Role.MEMBER, uuid4(), stale)
        return await call_next(request)

    async def require_hr_access(request: Request, *, writable: bool = False):
        context = request.state.auth_context
        if not entitled:
            raise HTTPException(403, "HR Agent use denied")
        if writable and context.hard_stale_read_only:
            raise HTTPException(503, "account is read only")
        return context.internal_user_id

    app.include_router(_router_builder()(service, require_hr_access))
    return TestClient(app), service, owner_id


def _headers(request_id: UUID | None = None) -> dict[str, str]:
    return {
        "Idempotency-Key": str(request_id or uuid4()),
        "X-CSRF-Token": "csrf",
    }


def test_panorama_sources_are_owner_scoped_idempotent_and_explicitly_serialized() -> (
    None
):
    client, service, owner_id = _client()
    request_id = uuid4()

    created = client.post(
        "/api/hr/panorama/sources",
        headers=_headers(request_id),
        json={
            "canonical_name": "联合光电",
            "aliases": ["Union Optech"],
            "approved_urls": ["https://example.com/jobs"],
        },
    )
    listed = client.get("/api/hr/panorama/sources?include_inactive=true&limit=20")

    assert created.status_code == listed.status_code == 200
    assert created.json() == {
        "source_id": str(service.source.source_id),
        "source_kind": "company",
        "canonical_name": "联合光电",
        "aliases": ["Union Optech"],
        "approved_urls": ["https://example.com/jobs"],
        "active": True,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    assert listed.json() == {"items": [created.json()]}
    assert service.calls == [
        (
            "add_company",
            {
                "owner_id": owner_id,
                "request_id": request_id,
                "canonical_name": "联合光电",
                "aliases": ("Union Optech",),
                "approved_urls": ("https://example.com/jobs",),
            },
        ),
        ("list_companies", owner_id, True, 20),
    ]
    assert "owner_id" not in created.text
    assert "client_request_id" not in created.text
    assert "company_key" not in created.text


def test_panorama_runs_are_owner_scoped_and_serialize_progress_without_internals() -> (
    None
):
    client, service, owner_id = _client()
    request_id = uuid4()

    started = client.post(
        "/api/hr/panorama/runs",
        headers=_headers(request_id),
        json={
            "source_ids": [str(service.source.source_id)],
            "conversation_id": str(service.run.conversation_id),
        },
    )
    status = client.get(f"/api/hr/panorama/runs/{service.run.run_id}")

    assert started.status_code == 202
    assert status.status_code == 200
    assert status.json() == started.json()
    assert started.json() == {
        "run_id": str(service.run.run_id),
        "selected_source_ids": [str(service.source.source_id)],
        "conversation_id": str(service.run.conversation_id),
        "state": "queued",
        "error_code": None,
        "source_failures": {},
        "row_version": 1,
        "started_at": None,
        "finished_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    assert service.calls[0] == (
        "start_run",
        {
            "owner_id": owner_id,
            "request_id": request_id,
            "source_ids": (service.source.source_id,),
            "conversation_id": service.run.conversation_id,
        },
    )
    assert "client_request_id" not in started.text
    assert "owner_id" not in started.text


def test_panorama_reports_list_and_detail_are_explicit_and_owner_scoped() -> None:
    client, service, owner_id = _client()

    listed = client.get("/api/hr/panorama/reports?limit=25")
    detail = client.get(
        f"/api/hr/panorama/reports/{service.insight.insight_version_id}"
    )

    assert listed.status_code == detail.status_code == 200
    assert listed.json()["items"][0]["summary"] == "结构人才需求上升"
    assert listed.json()["items"][0]["direction_clusters"] == {"结构": 4}
    assert detail.json()["insight"] == listed.json()["items"][0]
    assert detail.json()["sources"][0]["source_id"] == str(service.source.source_id)
    assert detail.json()["snapshots"][0] == {
        "snapshot_id": str(service.snapshot.snapshot_id),
        "run_id": str(service.run.run_id),
        "source_id": str(service.source.source_id),
        "public_job_key": "job-1",
        "title": "结构工程师",
        "location": "中山",
        "duty_excerpt": "负责精密结构设计",
        "requirement_excerpt": "五年以上经验",
        "source_url": "https://example.com/jobs/1",
        "observed_at": NOW.isoformat(),
        "content_sha256": "a" * 64,
        "status": "open",
        "created_at": NOW.isoformat(),
    }
    assert service.calls == [
        ("list_reports", owner_id, 25),
        ("report", owner_id, service.insight.insight_version_id),
    ]
    serialized = listed.text + detail.text
    assert "owner_id" not in serialized
    assert "client_request_id" not in serialized
    assert "origin_request_id" not in serialized


def test_panorama_reads_and_failures_always_disable_storage() -> None:
    client, service, _ = _client()
    responses = (
        client.get("/api/hr/panorama/sources"),
        client.get(f"/api/hr/panorama/runs/{service.run.run_id}"),
        client.get("/api/hr/panorama/reports"),
        client.get(f"/api/hr/panorama/reports/{service.insight.insight_version_id}"),
        client.get("/api/hr/panorama/reports?limit=101"),
    )

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 422]
    assert all(
        response.headers["cache-control"] == "private, no-store"
        for response in responses
    )
    assert all(response.headers["pragma"] == "no-cache" for response in responses)


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        (
            "/api/hr/panorama/sources",
            {
                "canonical_name": "联合光电",
                "aliases": [],
                "approved_urls": ["http://example.com/jobs"],
            },
        ),
        (
            "/api/hr/panorama/sources",
            {
                "canonical_name": "联合光电",
                "aliases": [],
                "approved_urls": ["https://example.com/jobs"],
                "source_kind": "person",
            },
        ),
        (
            "/api/hr/panorama/runs",
            {"source_ids": [], "conversation_id": str(uuid4())},
        ),
    ),
)
def test_panorama_mutations_require_valid_bounded_bodies(path, payload) -> None:
    client, service, _ = _client()

    response = client.post(path, json=payload, headers=_headers())

    assert response.status_code == 422
    assert response.json() == {"detail": "HR panorama request invalid"}
    assert service.calls == []


def test_panorama_limits_and_idempotency_keys_are_strict() -> None:
    client, service, _ = _client()
    source_payload = {
        "canonical_name": "联合光电",
        "aliases": [],
        "approved_urls": ["https://example.com/jobs"],
    }
    run_payload = {
        "source_ids": [str(service.source.source_id)],
        "conversation_id": str(service.run.conversation_id),
    }

    responses = (
        client.get("/api/hr/panorama/sources?limit=0"),
        client.get("/api/hr/panorama/sources?limit=101"),
        client.get("/api/hr/panorama/reports?limit=0"),
        client.get("/api/hr/panorama/reports?limit=101"),
        client.post("/api/hr/panorama/sources", json=source_payload),
        client.post(
            "/api/hr/panorama/sources",
            json=source_payload,
            headers={"Idempotency-Key": "not-a-uuid"},
        ),
        client.post("/api/hr/panorama/runs", json=run_payload),
    )

    assert all(response.status_code == 422 for response in responses)
    assert service.calls == []


def test_panorama_stale_mutations_and_unentitled_access_stop_before_service() -> None:
    stale, stale_service, _ = _client(stale=True)
    denied, denied_service, _ = _client(entitled=False)
    payload = {
        "canonical_name": "联合光电",
        "aliases": [],
        "approved_urls": ["https://example.com/jobs"],
    }

    blocked = stale.post("/api/hr/panorama/sources", json=payload, headers=_headers())
    denied_read = denied.get("/api/hr/panorama/sources")

    assert blocked.status_code == 503
    assert denied_read.status_code == 403
    assert stale_service.calls == denied_service.calls == []


def test_panorama_another_owner_ids_are_concealed_as_not_found() -> None:
    owner_client, owner_service, _ = _client()
    other_client, other_service, _ = _client()

    hidden_run = other_client.get(f"/api/hr/panorama/runs/{owner_service.run.run_id}")
    hidden_report = other_client.get(
        f"/api/hr/panorama/reports/{owner_service.insight.insight_version_id}"
    )

    assert hidden_run.status_code == hidden_report.status_code == 404
    assert (
        hidden_run.json() == hidden_report.json() == {"detail": "HR panorama not found"}
    )
    assert (
        owner_client.get(
            f"/api/hr/panorama/runs/{owner_service.run.run_id}"
        ).status_code
        == 200
    )
    assert other_service.calls[0][1] == other_service.owner_id


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    (
        (PanoramaNotFound("secret"), 404, "HR panorama not found"),
        (PanoramaConflict("secret"), 409, "HR panorama conflict"),
        (PanoramaUnavailable("secret"), 503, "HR panorama unavailable"),
        (ValueError("secret"), 422, "HR panorama request invalid"),
    ),
)
def test_panorama_repository_errors_are_mapped_without_leaking_details(
    error, expected_status, expected_detail
) -> None:
    client, service, _ = _client()
    service.error = error

    response = client.get("/api/hr/panorama/sources")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert response.headers["cache-control"] == "private, no-store"
    assert "secret" not in response.text


class _SecurityAuth:
    route_prefix = "/"
    cookie_name = "session"
    csrf_cookie_name = "csrf"
    public_base_url = "https://agent.example.test"
    trusted_proxy_networks = ()
    rate_limiter = None
    hard_stale_audit = lambda *_args: None

    def __init__(self, owner_id: UUID, *, stale: bool = False) -> None:
        self.owner_id = owner_id
        self.stale = stale
        self.repository = SimpleNamespace(directory_freshness=lambda **_kwargs: None)

    def authenticate(self, token):
        if token != "valid":
            return None
        return (
            AuthContext(self.owner_id, Role.MEMBER, uuid4(), self.stale),
            b"csrf-digest",
        )

    def verify_csrf(self, token, digest):
        return token == "csrf-token" and digest == b"csrf-digest"


def _security_client(*, stale: bool = False):
    owner_id = uuid4()
    service = FakePanoramaService(owner_id)
    app = FastAPI()

    async def require_hr_access(request: Request, *, writable: bool = False):
        return request.state.auth_context.internal_user_id

    app.include_router(_router_builder()(service, require_hr_access))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=_SecurityAuth(owner_id, stale=stale),
        public_assets=frozenset(),
        authorization=AuthorizationService(
            SimpleNamespace(permits=lambda *_args: False)
        ),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    return client, service


def test_real_security_middleware_authorizes_panorama_in_the_existing_hr_universe() -> (
    None
):
    client, service = _security_client()
    payload = {
        "canonical_name": "联合光电",
        "aliases": [],
        "approved_urls": ["https://example.com/jobs"],
    }

    readable_without_csrf = client.get("/api/hr/panorama/sources")
    missing_csrf = client.post(
        "/api/hr/panorama/sources",
        json=payload,
        headers={
            "Origin": "https://agent.example.test",
            "Idempotency-Key": str(uuid4()),
        },
    )
    accepted = client.post(
        "/api/hr/panorama/sources",
        json=payload,
        headers={
            "Origin": "https://agent.example.test",
            "X-CSRF-Token": "csrf-token",
            "Idempotency-Key": str(uuid4()),
        },
    )

    assert readable_without_csrf.status_code == 200
    assert missing_csrf.status_code == 403
    assert accepted.status_code == 200
    assert service.calls[0][0] == "list_companies"
    assert service.calls[1][0] == "add_company"


def test_real_security_middleware_blocks_stale_panorama_mutations() -> None:
    client, service = _security_client(stale=True)

    readable = client.get("/api/hr/panorama/sources")
    blocked = client.post(
        "/api/hr/panorama/sources",
        json={
            "canonical_name": "联合光电",
            "aliases": [],
            "approved_urls": ["https://example.com/jobs"],
        },
        headers={
            "Origin": "https://agent.example.test",
            "X-CSRF-Token": "csrf-token",
            "Idempotency-Key": str(uuid4()),
        },
    )

    assert readable.status_code == 200
    assert blocked.status_code == 503
    assert service.calls == [("list_companies", service.owner_id, False, 100)]


class _AgentAuthorization:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def decide_for_user_id(self, _owner_id, agent_id):
        assert agent_id == "hr-bot"
        return SimpleNamespace(allowed=self.allowed)

    def permitted_catalog_for_user_id(self, _owner_id):
        return ()


def _create_app_security_client(monkeypatch, *, stale=False, allowed=True):
    from app import main as app_main

    owner_id = uuid4()
    service = FakePanoramaService(owner_id)
    identity_auth = _SecurityAuth(owner_id, stale=stale)
    monkeypatch.setattr(
        app_main,
        "build_auth_router",
        lambda *_args, **_kwargs: APIRouter(),
    )
    app = app_main.create_app(
        start_poller=False,
        identity_auth=identity_auth,
        agent_use_authorization=_AgentAuthorization(allowed=allowed),
        hr_panorama_service=service,
    )
    client = TestClient(app)
    client.cookies.set("session", "valid")
    client.cookies.set("csrf", "csrf-token")
    return client, service


def test_create_app_uses_real_identity_middleware_and_hr_entitlement(
    monkeypatch,
) -> None:
    client, service = _create_app_security_client(monkeypatch)
    denied, denied_service = _create_app_security_client(monkeypatch, allowed=False)
    stale, stale_service = _create_app_security_client(monkeypatch, stale=True)
    mutation_headers = {
        "Origin": "https://agent.example.test",
        "X-CSRF-Token": "csrf-token",
        "Idempotency-Key": str(uuid4()),
    }

    readable = client.get("/api/hr/panorama/sources")
    hidden = client.get(f"/api/hr/panorama/runs/{denied_service.run.run_id}")
    denied_read = denied.get("/api/hr/panorama/sources")
    stale_read = stale.get("/api/hr/panorama/sources")
    stale_write = stale.post(
        "/api/hr/panorama/sources",
        headers=mutation_headers,
        json={
            "canonical_name": "联合光电",
            "aliases": [],
            "approved_urls": ["https://example.com/jobs"],
        },
    )

    assert readable.status_code == stale_read.status_code == 200
    assert hidden.status_code == 404
    assert denied_read.status_code == 403
    assert stale_write.status_code == 503
    assert service.calls[0][0] == "list_companies"
    assert denied_service.calls == []
    assert stale_service.calls == [
        ("list_companies", stale_service.owner_id, False, 100)
    ]
