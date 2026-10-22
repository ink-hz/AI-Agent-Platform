from __future__ import annotations

from contextlib import contextmanager
from hashlib import md5, sha256
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.control_plane.middleware import is_public_request
from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.sso import SdkSsoService, build_sdk_sso_router
from app.sdk_workbench.provision import ProvisionUnavailable
from app.sdk_workbench.sso_repository import SdkSsoRepository


USER = UUID("11111111-1111-4111-8111-111111111111")
PLATFORM = "https://agent.orbbec.com.cn"
ZENTAO = "https://47.106.112.69"


class Repository:
    def __init__(self):
        self.pending = {}
        self.account = "cangyuan"
        self.can_issue = True
        self.bound = []
        self.login_slot_held = False
        self.department_changes = []
        self.eligible = True
        self.provision_state = "bound"
        self.identity_reads = 0

    def identity(self, actor):
        assert actor == USER
        self.identity_reads += 1
        return {"eligible": self.eligible, "account": self.account,
                "provision_state": self.provision_state}

    def departments(self):
        return [{"id": "22222222-2222-4222-8222-222222222222", "name": "应用SDK组", "selected": True}]

    def set_allowed_department(self, actor, department, enabled):
        self.department_changes.append((actor, department, enabled))
        return True

    def bind(self, actor, target, account):
        self.bound.append((actor, target, account))
        return {"internal_user_id": str(target), "account": account}

    def issue(self, actor, session_id, digest, target="home"):
        if not self.can_issue or self.account is None:
            return False
        self.pending[digest] = (self.account, target)
        return True

    def consume(self, digest):
        return self.pending.pop(digest, None)

    def next_login_time(self):
        return 1792471200

    @contextmanager
    def login_slot(self):
        self.login_slot_held = True
        try:
            yield self.next_login_time()
        finally:
            self.login_slot_held = False


def build_app(tmp_path, repository, *, actor=USER, role=Role.MEMBER, stale=False, zentao_response=None, provisioner=None):
    key = tmp_path / "entry-key"
    key.write_text("a" * 32, encoding="utf-8")
    seen = []

    def zen_reply(request):
        assert repository.login_slot_held
        seen.append(request)
        if zentao_response is not None:
            return zentao_response
        return httpx.Response(302, headers={"location": "/", "set-cookie": "zentaosid=0123456789abcdef0123456789abcdef; Path=/; HttpOnly"})

    service = SdkSsoService(
        repository, private_origin="http://zentao:80", public_origin=ZENTAO,
        platform_origin=PLATFORM, entry_code="sdk-sso", entry_key_file=key,
        transport=httpx.MockTransport(zen_reply), provisioner=provisioner,
    )
    app = FastAPI()

    @app.middleware("http")
    async def add_identity(request, call_next):
        if actor is not None:
            request.state.auth_context = AuthContext(actor, role, uuid4(), stale)
        return await call_next(request)

    app.include_router(build_sdk_sso_router(service))
    return app, seen


@pytest.mark.asyncio
async def test_first_sso_entry_creates_personal_account_and_timeout_reconciles_without_recreate(tmp_path):
    class FirstLoginRepository(Repository):
        def __init__(self):
            super().__init__()
            self.account = None
            self.state = None
            self.marker = None

        @contextmanager
        def provision_slot(self, actor):
            yield self, {"eligible": True, "display_name": "SDK User", "account": self.account,
                         "provision_state": self.state, "marker": self.marker}

        account_for = staticmethod(SdkSsoRepository.account_for)

        def mark_provision_attempt(self, connection, actor, account, marker):
            self.state = "reconcile_required"
            self.marker = marker

        def complete_provision(self, connection, actor, account):
            self.account = account
            self.state = "bound"

    class Provisioner:
        def __init__(self):
            self.created = []
            self.reconciled = []
            self.access_changes = []

        def create(self, account, name, marker):
            self.created.append((account, name, marker))
            raise ProvisionUnavailable("response lost")

        def reconcile(self, account, name, marker):
            self.reconciled.append((account, name, marker))

        def set_access(self, account, marker, *, enabled):
            self.access_changes.append((account, marker, enabled))

    repository, provisioner = FirstLoginRepository(), Provisioner()
    app, _ = build_app(tmp_path, repository, provisioner=provisioner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        first = await client.post("/api/v1/sdk/sso/start", json={"target": "requirements"})
        second = await client.post("/api/v1/sdk/sso/start", json={"target": "requirements"})
    assert first.status_code == 503
    assert second.status_code == 200
    assert len(provisioner.created) == 1
    assert provisioner.reconciled == provisioner.created
    assert provisioner.access_changes == [(provisioner.created[0][0], provisioner.created[0][2], True)]
    assert len(provisioner.created[0][0]) == 30


@pytest.mark.asyncio
async def test_first_sso_entry_adopts_prepared_directory_account_without_duplicate_creation(tmp_path):
    prepared_account = SdkSsoRepository.account_for(uuid4())
    marker = "sdk:" + uuid4().hex

    class PreparedRepository(Repository):
        def __init__(self):
            super().__init__()
            self.account = None
            self.adopted = False

        @contextmanager
        def provision_slot(self, actor):
            yield self, {"eligible": True, "display_name": "SDK User", "account": self.account,
                         "provision_state": None, "marker": None,
                         "prepared": {"account": prepared_account, "marker": marker}}

        def adopt_directory_account(self, connection, actor, account, selected_marker):
            assert (account, selected_marker) == (prepared_account, marker)
            self.adopted = True

        def complete_provision(self, connection, actor, account):
            assert self.adopted
            self.account = account

    class Provisioner:
        def __init__(self): self.reconciled = []; self.access = []
        def create(self, *args): raise AssertionError("prepared account must not be recreated")
        def reconcile(self, *args): self.reconciled.append(args)
        def set_access(self, *args, **kwargs): self.access.append((args, kwargs))

    repository, provisioner = PreparedRepository(), Provisioner()
    app, _ = build_app(tmp_path, repository, provisioner=provisioner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.post("/api/v1/sdk/sso/start", json={"target": "requirements"})
    assert response.status_code == 200
    assert repository.account == prepared_account
    assert provisioner.reconciled == [(prepared_account, "SDK User", marker)]
    assert provisioner.access == [((prepared_account, marker), {"enabled": True})]


@pytest.mark.asyncio
async def test_suspended_personal_account_is_restored_before_new_ticket(tmp_path):
    class SuspendedRepository(Repository):
        @contextmanager
        def provision_slot(self, actor):
            yield self, {"eligible": True, "display_name": "SDK User", "account": SdkSsoRepository.account_for(actor),
                         "provision_state": "suspended", "marker": "sdk:1234567890abcdef1234567890abcdef"}

        def mark_access_state(self, connection, actor, old_state, new_state):
            self.transition = (old_state, new_state)

    class Provisioner:
        def set_access(self, account, marker, *, enabled):
            self.restored = (account, marker, enabled)

    repository, provisioner = SuspendedRepository(), Provisioner()
    app, _ = build_app(tmp_path, repository, provisioner=provisioner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.post("/api/v1/sdk/sso/start", json={"target": "tasks"})
    assert response.status_code == 200
    assert provisioner.restored[2] is True
    assert repository.transition == ("suspended", "bound")


@pytest.mark.asyncio
async def test_directory_revocation_during_creation_never_grants_zen_permissions(tmp_path):
    class RevokedRepository(Repository):
        @contextmanager
        def provision_slot(self, actor):
            yield self, {"eligible": True, "display_name": "SDK User", "account": None,
                         "provision_state": None, "marker": None}

        account_for = staticmethod(SdkSsoRepository.account_for)

        def mark_provision_attempt(self, connection, actor, account, marker): pass

        def complete_provision(self, connection, actor, account):
            raise HTTPException(403, "sdk_sso_access_required")

    class Provisioner:
        def __init__(self): self.created = False; self.granted = False
        def create(self, account, name, marker): self.created = True
        def set_access(self, account, marker, *, enabled): self.granted = True

    repository, provisioner = RevokedRepository(), Provisioner()
    app, _ = build_app(tmp_path, repository, provisioner=provisioner)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.post("/api/v1/sdk/sso/start")
    assert response.status_code == 403
    assert provisioner.created is True
    assert provisioner.granted is False


def test_directory_sync_removes_auto_account_access_and_restores_it_when_scope_returns(tmp_path):
    class AccessRepository(Repository):
        def __init__(self):
            super().__init__()
            self.eligible = False
            self.state = "bound"

        def access_changes(self):
            return [{"internal_user_id": USER}] if self.state == ("suspended" if self.eligible else "bound") else []

        @contextmanager
        def access_slot(self, actor):
            yield self, {"account": SdkSsoRepository.account_for(actor), "marker": "sdk:1234567890abcdef1234567890abcdef",
                         "eligible": self.eligible, "state": self.state}

        def mark_access_state(self, connection, actor, old_state, new_state):
            assert self.state == old_state
            self.state = new_state

    class Provisioner:
        def __init__(self): self.operations = []
        def set_access(self, account, marker, *, enabled): self.operations.append(enabled)

    repository, provisioner = AccessRepository(), Provisioner()
    from app.sdk_workbench.sso import SdkSsoService
    key = tmp_path / "entry-key"
    key.write_text("k" * 32)
    service = SdkSsoService(repository, private_origin="http://zentao:80", public_origin=ZENTAO,
                            platform_origin=PLATFORM, entry_code="sdk-sso", entry_key_file=key,
                            provisioner=provisioner)
    assert service.sync_auto_access() == (1, 0)
    assert repository.state == "suspended"
    repository.eligible = True
    assert service.sync_auto_access() == (1, 0)
    assert repository.state == "bound"
    assert provisioner.operations == [False, True]


def test_directory_sync_defers_failed_account_to_keep_queue_moving(tmp_path):
    class AccessRepository(Repository):
        def __init__(self):
            super().__init__()
            self.deferred = []

        def access_changes(self):
            return [{"internal_user_id": USER}]

        @contextmanager
        def access_slot(self, actor):
            yield self, {"account": SdkSsoRepository.account_for(actor),
                         "marker": "sdk:1234567890abcdef1234567890abcdef",
                         "eligible": False, "state": "bound"}

        def defer_access_retry(self, actor):
            self.deferred.append(actor)

    class FailingProvisioner:
        def set_access(self, account, marker, *, enabled):
            raise ProvisionUnavailable("zentao_unavailable")

    repository = AccessRepository()
    from app.sdk_workbench.sso import SdkSsoService
    key = tmp_path / "entry-key"
    key.write_text("k" * 32)
    service = SdkSsoService(repository, private_origin="http://zentao:80", public_origin=ZENTAO,
                            platform_origin=PLATFORM, entry_code="sdk-sso", entry_key_file=key,
                            provisioner=FailingProvisioner())
    assert service.sync_auto_access() == (0, 1)
    assert repository.deferred == [USER]


@pytest.mark.asyncio
async def test_only_owner_can_manage_department_scope(tmp_path):
    repository = Repository()
    app, _ = build_app(tmp_path, repository, role=Role.PLATFORM_OWNER)
    department = "22222222-2222-4222-8222-222222222222"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        listed = await client.get("/api/v1/sdk/sso/departments")
        changed = await client.put(f"/api/v1/sdk/sso/departments/{department}", json={"enabled": False})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "应用SDK组"
    assert changed.status_code == 200
    assert repository.department_changes == [(USER, UUID(department), False)]
    member_app, _ = build_app(tmp_path, repository, role=Role.MEMBER)
    async with AsyncClient(transport=ASGITransport(app=member_app), base_url=PLATFORM) as client:
        denied = await client.put(f"/api/v1/sdk/sso/departments/{department}", json={"enabled": True})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_sdk_member_can_post_single_use_ticket_to_zentao(tmp_path):
    repository = Repository()
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        start = await client.post("/api/v1/sdk/sso/start")
    assert start.status_code == 200
    payload = start.json()
    assert payload["consume_url"] == f"{ZENTAO}/sdk-sso/consume"
    assert "ticket" not in payload["consume_url"]
    assert sha256(payload["ticket"].encode()).digest() in repository.pending
    assert is_public_request("POST", "/api/v1/sdk/sso/consume", "/")
    assert not is_public_request("GET", "/api/v1/sdk/sso/consume", "/")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        consume = await client.post(
            "/api/v1/sdk/sso/consume", data={"ticket": payload["ticket"]},
            headers={"origin": PLATFORM, "x-forwarded-proto": "https", "cookie": "zentaosid=old-session"},
            follow_redirects=False,
        )
        replay = await client.post(
            "/api/v1/sdk/sso/consume", data={"ticket": payload["ticket"]},
            headers={"origin": PLATFORM, "x-forwarded-proto": "https"},
        )
    assert consume.status_code == 303
    assert consume.headers["location"] == "/"
    assert "zentaosid=0123456789abcdef0123456789abcdef" in consume.headers["set-cookie"]
    for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert flag.lower() in consume.headers["set-cookie"].lower()
    assert consume.headers["referrer-policy"] == "no-referrer"
    assert replay.status_code == 403
    assert "set-cookie" not in replay.headers
    assert len(seen) == 1
    assert "cookie" not in seen[0].headers
    assert seen[0].url.host == "zentao"
    assert seen[0].url.params["account"] == "cangyuan"
    assert seen[0].url.params["token"] == md5(b"sdk-sso" + b"a" * 32 + b"1792471200").hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("target,location", [
    ("requirements", "/index.php?m=product&f=browse&productID=1"),
    ("tasks", "/index.php?m=my&f=work&mode=task"),
    ("tests", "/index.php?m=testcase&f=browse&productID=1"),
    ("releases", "/index.php?m=release&f=browse&productID=1"),
])
async def test_sso_ticket_redirects_only_to_fixed_work_page(tmp_path, target, location):
    repository = Repository()
    app, _ = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        started = await client.post("/api/v1/sdk/sso/start", json={"target": target})
    assert started.status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        consumed = await client.post("/api/v1/sdk/sso/consume", data={"ticket": started.json()["ticket"]},
                                     headers={"origin": PLATFORM, "x-forwarded-proto": "https"}, follow_redirects=False)
    assert consumed.status_code == 303
    assert consumed.headers["location"] == location


@pytest.mark.asyncio
async def test_sso_rejects_arbitrary_destination(tmp_path):
    app, _ = build_app(tmp_path, Repository())
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.post("/api/v1/sdk/sso/start", json={"target": "https://evil.example"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sso_denies_unbound_or_read_only_and_rejects_wrong_consume_origin(tmp_path):
    repository = Repository()
    repository.can_issue = False
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        denied = await client.post("/api/v1/sdk/sso/start")
    assert denied.status_code == 403

    repository.can_issue = True
    stale_app, _ = build_app(tmp_path, repository, stale=True)
    async with AsyncClient(transport=ASGITransport(app=stale_app), base_url=PLATFORM) as client:
        stale = await client.post("/api/v1/sdk/sso/start")
    assert stale.status_code == 503

    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        ticket = (await client.post("/api/v1/sdk/sso/start")).json()["ticket"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        wrong = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": "https://evil.example", "x-forwarded-proto": "https"})
        valid = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": PLATFORM, "x-forwarded-proto": "https"}, follow_redirects=False)
    assert wrong.status_code == 404
    assert valid.status_code == 303
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_bad_zentao_response_never_sets_browser_cookie(tmp_path):
    repository = Repository()
    app, _ = build_app(tmp_path, repository, zentao_response=httpx.Response(200, text="error"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        ticket = (await client.post("/api/v1/sdk/sso/start")).json()["ticket"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        result = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": PLATFORM, "x-forwarded-proto": "https"})
    assert result.status_code == 502
    assert "set-cookie" not in result.headers


@pytest.mark.asyncio
async def test_direct_entry_sets_only_scoped_personal_zentao_session(tmp_path):
    repository = Repository()
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.get("/api/v1/sdk/sso/entry", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/sdk/index.php?m=my&f=index"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["set-cookie"].startswith("zentaosid=0123456789abcdef0123456789abcdef;")
    assert "Path=/sdk/" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert len(seen) == 1
    assert repository.identity_reads == 1


@pytest.mark.asyncio
async def test_direct_access_checks_binding_without_creating_zentao_session(tmp_path):
    repository = Repository()
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.get("/api/v1/sdk/sso/access")
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert repository.identity_reads == 1
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible,account,state", [
    (False, "cangyuan", "bound"), (True, None, None), (True, "cangyuan", "suspended"),
])
async def test_direct_entry_and_access_deny_out_of_scope_accounts(tmp_path, eligible, account, state):
    repository = Repository()
    repository.eligible, repository.account, repository.provision_state = eligible, account, state
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        access = await client.get("/api/v1/sdk/sso/access")
        entry = await client.get("/api/v1/sdk/sso/entry", follow_redirects=False)
    assert access.status_code == 403
    assert entry.status_code == 403
    assert "set-cookie" not in entry.headers
    assert seen == []


@pytest.mark.asyncio
async def test_direct_entry_and_access_reject_stale_or_missing_identity(tmp_path):
    repository = Repository()
    stale_app, seen = build_app(tmp_path, repository, stale=True)
    missing_app, _ = build_app(tmp_path, repository, actor=None)
    async with AsyncClient(transport=ASGITransport(app=stale_app), base_url=PLATFORM) as client:
        stale_access = await client.get("/api/v1/sdk/sso/access")
        stale_entry = await client.get("/api/v1/sdk/sso/entry")
    async with AsyncClient(transport=ASGITransport(app=missing_app), base_url=PLATFORM) as client:
        missing_access = await client.get("/api/v1/sdk/sso/access")
        missing_entry = await client.get("/api/v1/sdk/sso/entry")
    assert (stale_access.status_code, stale_entry.status_code) == (503, 503)
    assert (missing_access.status_code, missing_entry.status_code) == (401, 401)
    assert seen == []


@pytest.mark.asyncio
async def test_direct_entry_failed_zentao_login_does_not_set_cookie(tmp_path):
    app, _ = build_app(tmp_path, Repository(), zentao_response=httpx.Response(200, text="error"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        response = await client.get("/api/v1/sdk/sso/entry", follow_redirects=False)
    assert response.status_code == 502
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_only_platform_owner_can_bind_member_to_zentao_account(tmp_path):
    repository = Repository()
    member_app, _ = build_app(tmp_path, repository)
    target = uuid4()
    async with AsyncClient(transport=ASGITransport(app=member_app), base_url=PLATFORM) as client:
        denied = await client.put(f"/api/v1/sdk/sso/bindings/{target}", json={"account": "cangyuan"})
    assert denied.status_code == 403
    owner_app, _ = build_app(tmp_path, repository, role=Role.PLATFORM_OWNER)
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url=PLATFORM) as client:
        allowed = await client.put(f"/api/v1/sdk/sso/bindings/{target}", json={"account": "cangyuan"})
    assert allowed.status_code == 200
    assert repository.bound == [(USER, target, "cangyuan")]
