import asyncio
from ipaddress import ip_network
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.control_plane.admin_cli import OfflineOwnerAdministrator
from app.control_plane.audit import AuditCommand, AuditWriter
from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
from app.control_plane.dingtalk import DingTalkAuthResult, DingTalkMember
from app.control_plane.identity import IdentityResolver
from app.control_plane.models import DirectoryFreshness
from app.control_plane.rate_limit import ControlRateLimiter
from app.control_plane.routes_manage import ManagementRepository, ManagementService
from test_control_plane_migration import control_database
from test_dingtalk_auth_api import _app
from test_dingtalk_identity import DirectoryClient, _stage_and_promote_generation
from test_identity_crypto import _codec


@pytest.mark.postgres
def test_owner_grants_before_login_and_revoke_survives_request_replay(
    control_database, tmp_path, monkeypatch
):
    env = control_database["environments"]["production"]
    app_url = env["urls"]["platform_control_app"]
    writer = AuditWriter.from_database_url(env["urls"]["platform_audit_append"])
    codec = _codec(tmp_path)
    owner = DingTalkMember("owner-staff", "owner-union", "Owner", True, (1,))
    employee = DingTalkMember("employee-staff", "employee-union", "新员工", True, (1,))
    other = DingTalkMember("other-staff", "other-union", "另一人", True, (1,))
    generation = _stage_and_promote_generation(env, codec, [owner, employee, other])
    codes = {"owner-code": owner, "first-employee-code": employee}

    async def exchange_code(code, verifier):
        assert verifier
        member = codes.pop(code)
        resolver = IdentityResolver(
            app_url,
            corp_id="test-corp",
            identity_codec=codec,
            client=DirectoryClient(member),
        )
        return await resolver.resolve_login_identity(
            DingTalkAuthResult(member.unionid, member.userid, "test-corp"),
            DirectoryFreshness.FRESH,
        )

    secrets = AuthSecrets(b"t" * 32, key_version=1)
    origin = "https://agent.example.test"
    auth = DingTalkWebAuth(
        repository=WebSessionRepository(
            app_url, secrets=secrets, identity_codec=codec, directory_id="test-corp"
        ),
        secrets=secrets,
        qr_login=exchange_code,
        in_client_login=exchange_code,
        environment="production",
        route_prefix="/",
        public_base_url=origin,
        app_key="test-app",
        corp_id="test-corp",
        rate_limiter=ControlRateLimiter(control_database_url=app_url, secrets=secrets),
        trusted_proxy_networks=(ip_network("127.0.0.1/32"),),
    )
    app = _app(tmp_path, monkeypatch, auth)
    app.state.identity_management_service = ManagementService(
        ManagementRepository(app_url), writer
    )

    def client():
        return TestClient(
            app,
            base_url=origin,
            client=("127.0.0.1", 41101),
            headers={
                "Origin": origin,
                "X-Real-IP": "192.0.2.27",
                "X-Forwarded-For": "192.0.2.27",
                "X-Forwarded-Proto": "https",
            },
        )

    def login(session, code):
        assert session.get("/login").status_code == 200
        response = session.post(
            "/api/v1/auth/dingtalk/in-client/exchange", json={"code": code}
        )
        assert response.status_code == 200, response.text
        session.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    # Offline owner binding requires an existing verified internal identity.
    asyncio.run(exchange_code("owner-code", "bootstrap"))
    manager = client()
    OfflineOwnerAdministrator(
        env["urls"]["platform_control_migrator"],
        owner_role="platform_control_owner",
        identity_codec=codec,
        audit_writer=writer,
    ).bind_owner(
        provider_id=IdentityResolver.corporate_provider_id("test-corp", owner.userid),
        subject_kind="employee",
        generation_id=generation,
        os_operator="root",
        approvers=("uid:1001", "uid:1002"),
        backup_reference="BACKUP_2026_001",
        incident_reference="INC_2026_001",
    )
    codes["renew-owner-code"] = owner
    login(manager, "renew-owner-code")
    current_owner = manager.get("/api/v1/account")
    assert current_owner.status_code == 200, current_owner.text
    assert current_owner.json()["role"] == "platform_owner"
    results = manager.get(
        "/api/v1/manage/users",
        params={"view": "candidates_roster_v2", "q": employee.display_name},
    )
    assert results.status_code == 200, results.text
    (candidate,) = results.json()["users"]
    assert candidate["internal_user_id"] is None
    target = candidate["assignment_user_id"]
    path = "/api/v1/manage/roster-admins/" + candidate["directory_member_id"]
    request_id = str(uuid4())
    body = {
        "reason": "admin_access_approved",
        "request_id": request_id,
        "directory_generation_id": candidate["directory_generation_id"],
    }
    # A worker that stops immediately after recording intent has no mutation receipt yet.
    writer.append(
        AuditCommand(
            event_type="admin_role_assignment_requested",
            actor_internal_user_id=UUID(current_owner.json()["internal_user_id"]),
            target_type="internal_user",
            target_id=target,
            request_id=UUID(request_id),
            reason=body["reason"],
            metadata={
                "operation_id": request_id,
                "previous_role": "member",
                "new_role": "platform_admin",
                "expected_row_version": 0,
                "result": "requested",
            },
        )
    )
    (alternative,) = manager.get(
        "/api/v1/manage/users",
        params={"view": "candidates_roster_v2", "q": other.display_name},
    ).json()["users"]
    changed_target = manager.post(
        "/api/v1/manage/roster-admins/" + alternative["directory_member_id"], json=body
    )
    assert changed_target.status_code == 409, changed_target.text
    with psycopg.connect(env["admin"]) as db:
        assert db.execute(
            "SELECT count(*) FROM platform_control.internal_users WHERE internal_user_id=%s",
            (alternative["assignment_user_id"],),
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM platform_control.audit_events WHERE request_id=%s",
            (request_id,),
        ).fetchone() == (1,)
    granted = manager.post(path, json=body)
    assert granted.status_code == 200, granted.text
    assert granted.json() == {"status": "ok", "internal_user_id": target}
    assert manager.post(path, json=body).json() == granted.json()
    admins = manager.get(
        "/api/v1/manage/users", params={"view": "administrators"}
    ).json()["users"]
    assert any(
        row["internal_user_id"] == target and row["role"] == "platform_admin"
        for row in admins
    )

    with psycopg.connect(env["admin"]) as db:
        assert db.execute(
            "SELECT count(*) FROM platform_control.web_sessions WHERE internal_user_id=%s",
            (target,),
        ).fetchone() == (0,)

    member = client()
    login(member, "first-employee-code")
    account = member.get("/api/v1/account").json()
    assert (account["internal_user_id"], account["role"]) == (target, "platform_admin")
    removed = manager.request(
        "DELETE",
        "/api/v1/manage/admins/" + target,
        json={"reason": "admin_access_revoked", "request_id": str(uuid4())},
    )
    assert removed.status_code == 200, removed.text
    assert member.get("/api/v1/account").status_code == 401
    assert manager.post(path, json=body).json() == granted.json()
    with psycopg.connect(env["admin"]) as db:
        assert db.execute(
            "SELECT role FROM platform_control.internal_users WHERE internal_user_id=%s",
            (target,),
        ).fetchone() == ("member",)
        assert db.execute(
            "SELECT count(*) FROM platform_control.provider_identities WHERE internal_user_id=%s",
            (target,),
        ).fetchone() == (2,)
        assert db.execute(
            "SELECT count(*) FROM platform_control.management_mutations WHERE operation_id=%s",
            (request_id,),
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT event_type FROM platform_control.audit_events WHERE request_id=%s "
            "ORDER BY event_type",
            (request_id,),
        ).fetchall() == [
            ("admin_role_assignment_completed",),
            ("admin_role_assignment_requested",),
        ]
