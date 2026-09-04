from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import psycopg
from app.control_plane.models import AuthContext, Role
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from test_control_plane_migration import control_database


class FakeAccessHistoryRepository:
    def __init__(self) -> None:
        self.page_calls = []
        self.query_calls = []
        self.page_outcome = "inserted"
        self.items = []
        self.error: Exception | None = None

    def record_page_view(self, event_id, context, page):
        if self.error is not None:
            raise self.error
        self.page_calls.append((event_id, context, page))
        return self.page_outcome

    def list_events(self, context, filters):
        if self.error is not None:
            raise self.error
        self.query_calls.append((context, filters))
        return self.items


def _client(repository, role: Role = Role.PLATFORM_OWNER) -> tuple[TestClient, AuthContext]:
    from app.control_plane.routes_access_history import build_access_history_router

    context = AuthContext(uuid4(), role, uuid4(), False)
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request: Request, call_next):
        request.state.auth_context = context
        return await call_next(request)

    app.include_router(build_access_history_router(repository))
    return TestClient(app), context


def test_page_view_uses_server_auth_context_and_accepts_idempotent_replay() -> None:
    repository = FakeAccessHistoryRepository()
    client, context = _client(repository, Role.MEMBER)
    event_id = uuid4()
    body = {
        "access_event_id": str(event_id),
        "workspace_key": "office",
        "page_key": "office.services",
        "agent_id": None,
    }

    assert client.post(
        "/api/v1/access-events/page-view", json=body
    ).status_code == 204
    repository.page_outcome = "duplicate"
    assert client.post(
        "/api/v1/access-events/page-view", json=body
    ).status_code == 204

    assert repository.page_calls[0][0] == event_id
    assert repository.page_calls[0][1] == context
    assert repository.page_calls[0][2].workspace_key == "office"
    assert repository.page_calls[0][2].page_key == "office.services"


@pytest.mark.parametrize(
    ("headers", "content", "expected_status"),
    [
        ({"content-type": "text/plain"}, b"{}", 415),
        ({"content-type": "application/json"}, b"x" * 2049, 413),
        (
            {"content-type": "application/json"},
            b'{"access_event_id":"00000000-0000-0000-0000-000000000001",'
            b'"workspace_key":"office","page_key":"office.services",'
            b'"agent_id":null,"url":"/office/?view=services"}',
            400,
        ),
    ],
)
def test_page_view_rejects_non_json_oversize_and_extra_fields(
    headers, content, expected_status
) -> None:
    repository = FakeAccessHistoryRepository()
    client, _ = _client(repository, Role.MEMBER)

    response = client.post(
        "/api/v1/access-events/page-view", headers=headers, content=content
    )

    assert response.status_code == expected_status
    assert repository.page_calls == []


def test_page_view_rate_limit_is_session_scoped_and_bounded() -> None:
    repository = FakeAccessHistoryRepository()
    repository.page_outcome = "rate_limited"
    client, _ = _client(repository, Role.MEMBER)

    response = client.post(
        "/api/v1/access-events/page-view",
        json={
            "access_event_id": str(uuid4()),
            "workspace_key": "platform",
            "page_key": "platform.brain",
            "agent_id": None,
        },
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "role",
    [Role.MEMBER, Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN],
)
def test_access_history_query_is_platform_owner_only(role: Role) -> None:
    repository = FakeAccessHistoryRepository()
    client, _ = _client(repository, role)

    response = client.get("/api/v1/manage/access-events")

    assert response.status_code == 403
    assert repository.query_calls == []


def test_access_history_defaults_to_seven_days_and_returns_bounded_page() -> None:
    from app.control_plane.access_history import AccessHistoryEvent

    repository = FakeAccessHistoryRepository()
    now = datetime.now(UTC)
    repository.items = [
        AccessHistoryEvent(
            access_event_id=uuid4(),
            display_name="苍渊",
            event_kind="page_view",
            login_kind=None,
            workspace_key="admin",
            page_key="admin.access_history",
            page_display_name="访问记录",
            agent_id=None,
            occurred_at=now,
        )
    ]
    client, context = _client(repository)

    response = client.get("/api/v1/manage/access-events")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "access_event_id": str(repository.items[0].access_event_id),
                "display_name": "苍渊",
                "event_kind": "page_view",
                "login_kind": None,
                "workspace_key": "admin",
                "page_key": "admin.access_history",
                "page_display_name": "访问记录",
                "agent_id": None,
                "occurred_at": now.isoformat().replace("+00:00", "Z"),
            }
        ],
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }
    called_context, filters = repository.query_calls[0]
    assert called_context == context
    assert filters.limit == 50
    assert filters.offset == 0
    assert timedelta(days=6, hours=23) < filters.date_to - filters.date_from <= timedelta(days=7)


def test_access_history_database_failure_is_not_reported_as_empty() -> None:
    from app.control_plane.access_history import AccessHistoryUnavailable

    repository = FakeAccessHistoryRepository()
    repository.error = AccessHistoryUnavailable("database offline")
    client, _ = _client(repository)

    assert client.get("/api/v1/manage/access-events").status_code == 503
    assert client.post(
        "/api/v1/access-events/page-view",
        json={
            "access_event_id": str(uuid4()),
            "workspace_key": "platform",
            "page_key": "platform.brain",
            "agent_id": None,
        },
    ).status_code == 503


@pytest.mark.postgres
def test_database_repository_limits_page_views_per_session_and_reads_for_owner(
    control_database,
) -> None:
    from app.control_plane.access_history import (
        AccessHistoryFilter,
        AccessHistoryRepository,
        PageAccessDescriptor,
    )

    environment = control_database["environments"]["production"]
    owner_id = uuid4()
    session_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) "
            "values (%s,'苍渊','active','platform_owner')",
            (owner_id,),
        )
        connection.execute(
            "insert into platform_control.web_sessions("
            "session_id,internal_user_id,token_hash,token_hash_key_version,"
            "csrf_hash,csrf_hash_key_version,idle_expires_at,absolute_expires_at"
            ") values (%s,%s,%s,1,%s,1,now()+interval '1 hour',"
            "now()+interval '2 hours')",
            (session_id, owner_id, uuid4().bytes * 2, uuid4().bytes * 2),
        )

    repository = AccessHistoryRepository(
        environment["urls"]["platform_control_app"]
    )
    context = AuthContext(owner_id, Role.PLATFORM_OWNER, session_id, False)
    page = PageAccessDescriptor("admin", "admin.access_history", None)
    for _ in range(120):
        assert repository.record_page_view(uuid4(), context, page) == "inserted"
    assert repository.record_page_view(uuid4(), context, page) == "rate_limited"

    now = datetime.now(UTC)
    events = repository.list_events(
        context,
        AccessHistoryFilter(
            now - timedelta(days=7),
            now + timedelta(minutes=1),
            "苍渊",
            "admin",
            "page_view",
            50,
            0,
        ),
    )
    assert len(events) == 51
    assert all(event.display_name == "苍渊" for event in events)
    assert all(event.page_key == "admin.access_history" for event in events)
