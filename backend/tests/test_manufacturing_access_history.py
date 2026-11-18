"""Loopback page-view ingestion and owner history with disposable PostgreSQL."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from app.control_plane.access_history import AccessHistoryRepository
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.control_plane.authorization import (
    AuthorizationRepository,
    AuthorizationService,
)
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.routes_access_history import build_access_history_router
from fastapi import FastAPI
from test_control_plane_migration import (
    control_database as control_database,  # noqa: PLC0414
)
from test_hr_same_host import loopback_server

ROOT = Path(__file__).resolve().parents[2]
PAGE_VIEW = "/api/v1/access-events/page-view"
EVENTS = "/api/v1/manage/access-events"
SUBJECTS = "/api/v1/manage/access-subjects"
ORIGIN = "https://localhost"


@pytest.mark.postgres
@pytest.mark.parametrize("environment", ["production", "preview"])
def test_manufacturing_host_pages_match_migrated_catalog(control_database, environment):
    env = control_database["environments"][environment]
    with psycopg.connect(env["admin"]) as connection:
        catalog = connection.execute(
            "select page_key, allows_agent_id from platform_control.access_page_catalog "
            "where workspace_key='manufacturing' order by page_key"
        ).fetchall()

    assert len(catalog) == 35
    host_pages = json.loads(
        (ROOT / "webui/src/manufacturingAccessPages.json").read_text()
    )
    assert sorted(host_pages) == [page for page, _ in catalog]
    assert all(not allows_agent for _, allows_agent in catalog)


def _signed_in_app(env):
    # Only the provider exchange is local. The platform creates and validates both sessions.
    generation = uuid4()
    actors = {"member": uuid4(), "platform_owner": uuid4()}
    with psycopg.connect(env["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,source_member_count,department_count,"
            "source_schema_version,content_sha256,completed_at) "
            "values (%s,'complete',2,2,0,3,%s,now())",
            (generation, "a" * 64),
        )
        for role, actor in actors.items():
            name = "制造测试成员" if role == "member" else "制造测试所有者"
            connection.execute(
                "insert into platform_control.internal_users "
                "(internal_user_id,display_name,role,status,last_confirmed_generation_id) "
                "values (%s,%s,%s,'active',%s)",
                (actor, name, role, generation),
            )
            connection.execute(
                "insert into platform_control.directory_members "
                "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
                "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) "
                "values (%s,%s,%s,'employee',%s,1,%s,1,%s,'active')",
                (generation, uuid4(), actor, actor.bytes * 2, b"fixture", name),
            )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s,"
            "last_complete_at=now(),updated_at=now() where singleton",
            (generation,),
        )

    async def exchange(code, verifier):
        return actors[code]

    database = env["urls"]["platform_control_app"]
    secrets = AuthSecrets(b"m" * 32, key_version=1)
    auth = DingTalkWebAuth(
        repository=WebSessionRepository(database, secrets=secrets),
        secrets=secrets,
        qr_login=exchange,
        in_client_login=None,
        environment="production",
        route_prefix="/",
        public_base_url=ORIGIN,
        app_key="manufacturing-history-test",
    )
    cookies = {}
    for role in actors:
        session = asyncio.run(auth.complete_qr(auth.start_qr("/").state, role)).session
        cookies[role] = {auth.cookie_name: session.cookie_token}

    app = FastAPI()
    app.include_router(build_access_history_router(AccessHistoryRepository(database)))
    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(AuthorizationRepository(database)),
        routes=tuple(app.router.routes),
    )
    return app, cookies


def _view(key, workspace="manufacturing"):
    return {
        "access_event_id": str(uuid4()),
        "workspace_key": workspace,
        "page_key": key,
    }


@pytest.mark.postgres
def test_signed_in_member_page_views_reach_owner_history(control_database):
    env = control_database["environments"]["production"]
    app, cookies = _signed_in_app(env)
    kitting = _view("manufacturing.kitting")
    order = _view("manufacturing.order")
    production = _view("manufacturing.production.history")
    filters = {
        "workspace_key": "manufacturing",
        "display_name": "制造测试成员",
        "limit": 1,
    }
    with (
        loopback_server(app) as origin,
        httpx.Client(
            base_url=origin, trust_env=False, headers={"Origin": ORIGIN}
        ) as member,
        httpx.Client(
            base_url=origin,
            trust_env=False,
            headers={"Origin": ORIGIN},
            cookies=cookies["platform_owner"],
        ) as owner,
    ):
        assert member.post(PAGE_VIEW, json=kitting).status_code == 401
        member.cookies.update(cookies["member"])
        assert (
            member.post(
                PAGE_VIEW, json=kitting, headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )

        for body in (kitting, kitting, order, _view("platform.home", "platform")):
            response = member.post(PAGE_VIEW, json=body)
            assert response.status_code == 204, response.text
        assert owner.post(PAGE_VIEW, json=production).status_code == 204
        for invalid in (
            kitting | {"page_key": "manufacturing.order"},  # conflicting replay
            _view("manufacturing.order.FIXTURE_NUMBER"),
            _view("manufacturing.order", "platform"),
            _view("manufacturing.order") | {"agent_id": "marketing-inbound-bot"},
            _view("manufacturing.order") | {"url": "/order/FIXTURE_NUMBER"},
            _view("manufacturing.order") | {"internal_user_id": str(uuid4())},
        ):
            assert member.post(PAGE_VIEW, json=invalid).status_code == 400
        assert member.get(EVENTS, params=filters).status_code == 403
        assert member.get(SUBJECTS, params=filters).status_code == 403

        for offset, expected, page_label, module, has_more in (
            (0, order, "工单枢纽", "生产执行", True),
            (1, kitting, "齐套总览", "计划与物料", False),
        ):
            response = owner.get(EVENTS, params=filters | {"offset": offset})
            assert response.status_code == 200, response.text
            result = response.json()
            assert (result["offset"], result["limit"], result["has_more"]) == (
                offset,
                1,
                has_more,
            )
            (event,) = result["items"]
            assert event == {
                **expected,
                "display_name": "制造测试成员",
                "page_display_name": page_label,
                "module_display_name": module,
                "departments": [],
                "event_kind": "page_view",
                "login_kind": None,
                "agent_id": None,
                "occurred_at": event["occurred_at"],
            }
        index = owner.get(SUBJECTS, params=filters)
        assert index.status_code == 200, index.text
        (subject,) = index.json()["items"]
        assert subject == {
            "display_name": "制造测试成员",
            "departments": [],
            "event_count": 2,
            "latest_workspace_key": "manufacturing",
            "latest_module_display_name": "生产执行",
            "latest_page_display_name": "工单枢纽",
            "latest_agent_id": None,
            "latest_event_kind": "page_view",
            "latest_occurred_at": subject["latest_occurred_at"],
        }
        latest = owner.get(
            EVENTS, params={"workspace_key": "manufacturing", "limit": 1}
        )
        (event,) = latest.json()["items"]
        assert (
            event["access_event_id"],
            event["page_display_name"],
            event["module_display_name"],
        ) == (
            production["access_event_id"],
            "SN 履历",
            "产测分析",
        )
