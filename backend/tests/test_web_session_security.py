from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import re
import threading
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.models import AuthContext, IdentityMode, Role
from test_control_plane_migration import control_database


def test_auth_random_values_have_256_bits_and_are_domain_separated() -> None:
    from app.control_plane.auth import AuthSecrets

    secrets = AuthSecrets(b"k" * 32, key_version=7)
    state = secrets.random_token()
    verifier = secrets.random_token()
    session = secrets.random_token()
    csrf = secrets.random_token()

    assert len(state) >= 43
    assert len(verifier) >= 43
    assert len(session) >= 43
    assert len(csrf) >= 43
    assert len({state, verifier, session, csrf}) == 4
    assert secrets.digest("oauth-state", state) != secrets.digest("session", state)
    assert secrets.matches("csrf", csrf, secrets.digest("csrf", csrf))
    assert not secrets.matches("csrf", csrf + "x", secrets.digest("csrf", csrf))


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil",
        "/%0d%0aLocation:%20https://evil.example/",
        "/account?next=https://evil.example/",
        "/%2f%2fevil.example/",
        "/login\r\nX-Test: bad",
        "/_preview/dingtalk-r1/../manage",
    ],
)
def test_safe_return_path_rejects_redirect_and_header_injection(candidate: str) -> None:
    from app.control_plane.auth import validate_return_path

    with pytest.raises(ValueError, match="return path"):
        validate_return_path(candidate, route_prefix="/")


def test_safe_return_path_accepts_only_same_environment_relative_paths() -> None:
    from app.control_plane.auth import validate_return_path

    assert validate_return_path("/", route_prefix="/") == "/"
    assert validate_return_path("/agents/a", route_prefix="/") == "/agents/a"
    assert (
        validate_return_path(
            "/_preview/dingtalk-r1/agents/a",
            route_prefix="/_preview/dingtalk-r1/",
        )
        == "/_preview/dingtalk-r1/agents/a"
    )
    with pytest.raises(ValueError):
        validate_return_path("/agents/a", route_prefix="/_preview/dingtalk-r1/")


def test_qr_attempt_persists_exact_admin_return_path() -> None:
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth

    repository = SessionRepository()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"r" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        state_ttl_seconds=300,
    )

    started = auth.start_qr("/admin/")
    stored = repository.attempts[auth.secrets.digest("oauth-state", started.state)]["record"]

    assert started.return_path == "/admin/"
    assert stored.return_path == "/admin/"


def test_qr_authorization_url_has_fixed_scope_callback_and_flow() -> None:
    from app.control_plane.auth import build_qr_authorization_url

    url = build_qr_authorization_url(
        app_key="test-app",
        callback_url="https://agent.example.test/api/v1/auth/dingtalk/callback",
        state="s" * 43,
        code_challenge="c" * 43,
    )

    assert "scope=openid+corpid" in url or "scope=openid%20corpid" in url
    assert "client_id=test-app" in url
    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert "flow=" not in url
    assert "https%3A%2F%2Fagent.example.test%2Fapi%2Fv1%2Fauth%2Fdingtalk%2Fcallback" in url


class SessionRepository:
    def __init__(self) -> None:
        self.attempts: dict[bytes, dict] = {}
        self.sessions: dict[bytes, dict] = {}
        self.provider_calls = 0
        self.claims = 0
        self.revocations = 0

    def create_attempt(self, record):
        self.attempts[record.state_digest] = {
            "record": record,
            "claimed": False,
            "consumed": False,
        }
        return record.attempt_id

    def claim_attempt(self, *, state_digest, environment, attempt_kind):
        row = self.attempts.get(state_digest)
        if (
            row is None
            or row["claimed"]
            or row["consumed"]
            or row["record"].environment != environment
            or row["record"].attempt_kind != attempt_kind
            or row["record"].expires_at <= datetime.now(UTC)
        ):
            return None
        row["claimed"] = True
        self.claims += 1
        return row["record"]

    def fail_attempt(self, attempt_id, reason):
        for row in self.attempts.values():
            if row["record"].attempt_id == attempt_id:
                row["consumed"] = True

    def issue_session(self, *, attempt_id, internal_user_id, token_digest,
                      token_key_version, csrf_digest, csrf_key_version,
                      idle_seconds, absolute_seconds,
                      hard_stale_read_only=False):
        for row in self.attempts.values():
            if row["record"].attempt_id == attempt_id and row["claimed"] and not row["consumed"]:
                row["consumed"] = True
                now = datetime.now(UTC)
                session_id = uuid4()
                self.sessions[token_digest] = {
                    "session_id": session_id,
                    "user": internal_user_id,
                    "role": Role.MEMBER,
                    "csrf": csrf_digest,
                    "idle": now + timedelta(seconds=idle_seconds),
                    "absolute": now + timedelta(seconds=absolute_seconds),
                    "revoked": False,
                    "hard_stale_read_only": hard_stale_read_only,
                }
                return session_id, now + timedelta(seconds=idle_seconds), now + timedelta(seconds=absolute_seconds)
        return None

    def authenticate_session(self, *, token_digest, token_key_version, idle_seconds):
        row = self.sessions.get(token_digest)
        if row is None or row["revoked"]:
            return None
        return AuthContext(
            row["user"], row["role"], row["session_id"],
            row["hard_stale_read_only"],
        ), row["csrf"]

    def revoke_session(self, *, session_id, reason):
        for row in self.sessions.values():
            if row["session_id"] == session_id and not row["revoked"]:
                row["revoked"] = True
                self.revocations += 1
                return True
        return False


class Provider:
    def __init__(self, user_id=None, *, fail=False) -> None:
        self.user_id = user_id or uuid4()
        self.fail = fail
        self.calls = 0

    async def complete(self, code, verifier=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider detail must not escape")
        return self.user_id


def test_database_session_unknown_stored_role_fails_closed() -> None:
    from app.control_plane.auth import AuthSecrets, WebSessionRepository

    class UnknownRoleConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _parameters):
            return self

        def fetchone(self):
            return {
                "session_id": uuid4(),
                "internal_user_id": uuid4(),
                "role": "unknown_stored_role",
                "hard_stale_read_only": False,
                "csrf_hash": b"c" * 32,
                "csrf_hash_key_version": 1,
            }

    repository = WebSessionRepository(
        "dbname=agent_platform_control user=platform_control_app",
        secrets=AuthSecrets(b"u" * 32, key_version=1),
        connect=lambda *_args, **_kwargs: UnknownRoleConnection(),
    )

    assert repository.authenticate_session(
        token_digest=b"t" * 32,
        token_key_version=1,
        idle_seconds=28_800,
    ) is None


@pytest.mark.asyncio
async def test_unknown_expired_consumed_and_environment_mismatch_reject_before_provider() -> None:
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth

    repository = SessionRepository()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"a" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="preview",
        route_prefix="/_preview/dingtalk-r1/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        state_ttl_seconds=300,
    )

    with pytest.raises(Exception, match="login attempt invalid"):
        await auth.complete_qr("unknown", "code")
    assert provider.calls == 0

    started = auth.start_qr("/_preview/dingtalk-r1/")
    repository.attempts[auth.secrets.digest("oauth-state", started.state)]["record"] = replace(
        repository.attempts[auth.secrets.digest("oauth-state", started.state)]["record"],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(Exception, match="login attempt invalid"):
        await auth.complete_qr(started.state, "code")
    assert provider.calls == 0

    started = auth.start_qr("/_preview/dingtalk-r1/")
    digest = auth.secrets.digest("oauth-state", started.state)
    repository.attempts[digest]["record"] = replace(
        repository.attempts[digest]["record"], environment="production"
    )
    with pytest.raises(Exception, match="login attempt invalid"):
        await auth.complete_qr(started.state, "code")
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_attempt_is_one_time_provider_failure_is_terminal_and_session_rotates() -> None:
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth

    repository = SessionRepository()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"b" * 32, key_version=3),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        state_ttl_seconds=300,
    )
    started = auth.start_qr("/")
    issued = await auth.complete_qr(started.state, "one-time-code")

    assert provider.calls == 1
    assert issued.cookie_token not in repr(issued)
    assert issued.csrf_token not in repr(issued)
    assert issued.cookie_token != started.state
    assert issued.idle_expires_at <= issued.absolute_expires_at
    with pytest.raises(Exception, match="login attempt invalid"):
        await auth.complete_qr(started.state, "one-time-code")
    assert provider.calls == 1

    failing = Provider(fail=True)
    auth.qr_login = failing.complete
    failed = auth.start_qr("/")
    with pytest.raises(Exception, match="login unavailable"):
        await auth.complete_qr(failed.state, "one-time-code")
    with pytest.raises(Exception, match="login attempt invalid"):
        await auth.complete_qr(failed.state, "one-time-code")
    assert failing.calls == 1


@pytest.mark.asyncio
async def test_concurrent_double_callback_calls_provider_at_most_once() -> None:
    from app.control_plane.auth import AuthenticationError, AuthSecrets, DingTalkWebAuth

    repository = SessionRepository()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"q" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        state_ttl_seconds=300,
    )
    started = auth.start_qr("/")

    results = await asyncio.gather(
        auth.complete_qr(started.state, "one-time-code"),
        auth.complete_qr(started.state, "one-time-code"),
        return_exceptions=True,
    )

    assert provider.calls == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert any(
        isinstance(result, AuthenticationError)
        and str(result) == "login attempt invalid"
        for result in results
    )


def test_cookie_policy_is_exact_for_production_and_preview() -> None:
    from app.control_plane.auth import cookie_policy

    production = cookie_policy(IdentityMode.PRODUCTION, "/")
    preview = cookie_policy(IdentityMode.PREVIEW, "/_preview/dingtalk-r1/")

    assert production == {"httponly": True, "secure": True, "samesite": "lax", "path": "/"}
    assert preview == {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/_preview/dingtalk-r1/",
    }


@pytest.fixture
def production_environment(control_database):
    return control_database["environments"]["production"]


def _db_repository(environment):
    from app.control_plane.auth import AuthSecrets, WebSessionRepository

    return WebSessionRepository(
        environment["urls"]["platform_control_app"],
        secrets=AuthSecrets(b"w" * 32, key_version=9),
    )


def _seed_current_bound_member(environment):
    generation_id = uuid4()
    internal_user_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,0,%s,now())",
            (generation_id, "d" * 64),
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,last_confirmed_generation_id) "
            "values (%s,'Web Session User','active',%s)",
            (internal_user_id, generation_id),
        )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status,"
            "union_lookup_hmac,union_lookup_key_version) "
            "values (%s,%s,%s,'employee',%s,1,%s,1,'Web Session User','active',%s,1)",
            (generation_id, uuid4(), internal_user_id, b"c" * 32, b"cipher", b"u" * 32),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s,last_complete_at=now(),updated_at=now() where singleton",
            (generation_id,),
        )
    return internal_user_id, generation_id


@pytest.mark.postgres
def test_account_snapshot_returns_only_display_name_and_active_exact_scopes(
    production_environment,
) -> None:
    repository = _db_repository(production_environment)
    internal_user_id, _ = _seed_current_bound_member(production_environment)
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='management_viewer' "
            "where internal_user_id=%s",
            (internal_user_id,),
        )
        connection.execute(
            "insert into platform_control.observation_grants "
            "(observation_grant_id,viewer_internal_user_id,agent_id,created_by) values "
            "(%s,%s,'ai-fae-agent',%s),(%s,%s,'hr-bot',%s)",
            (
                uuid4(), internal_user_id, internal_user_id,
                uuid4(), internal_user_id, internal_user_id,
            ),
        )

    assert repository.account_snapshot(internal_user_id) == {
        "display_name": "Web Session User",
        "observation_agent_ids": ["ai-fae-agent", "hr-bot"],
    }


@pytest.mark.postgres
def test_migration_015_revokes_direct_attempt_and_session_dml(production_environment) -> None:
    migration = (
        __import__("pathlib").Path(__file__).parents[1]
        / "control_migrations/015_secure_web_sessions.sql"
    )
    assert migration.is_file()
    sql = migration.read_text(encoding="utf-8")
    assert "security definer" in sql.lower()
    assert "set search_path = pg_catalog, platform_control" in sql
    assert "lock_dingtalk_identity_directory" in sql

    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "insert into platform_control.login_attempts "
                "(login_attempt_id,attempt_kind,state_hash,expires_at) "
                "values (%s,'qr',%s,now()+interval '5 minutes')",
                (uuid4(), b"x" * 32),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "update platform_control.web_sessions set revoked_at=now()"
            )

    signatures = (
        "platform_control.create_web_login_attempt(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer)",
        "platform_control.claim_web_login_attempt(bytea,integer,text,text)",
        "platform_control.fail_web_login_attempt(uuid,text)",
        "platform_control.consume_attempt_and_issue_session_v22(uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean)",
        "platform_control.authenticate_web_session_v22(bytea,integer,integer)",
        "platform_control.revoke_web_session(uuid,text)",
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        for signature in signatures:
            assert connection.execute(
                "select has_function_privilege('platform_control_app',%s,'execute')",
                (signature,),
            ).fetchone() == (True,)
            assert connection.execute(
                "select has_function_privilege('platform_control_app_preview',%s,'execute')",
                (signature,),
            ).fetchone() == (False,)
        assert connection.execute(
            "select count(*) from information_schema.routine_privileges "
            "where grantee='PUBLIC' and routine_schema='platform_control' "
            "and routine_name in ('create_web_login_attempt','claim_web_login_attempt',"
            "'fail_web_login_attempt','consume_attempt_and_issue_session',"
            "'consume_attempt_and_issue_session_v22','authenticate_web_session',"
            "'authenticate_web_session_v22','revoke_web_session')"
        ).fetchone() == (0,)


@pytest.mark.postgres
def test_system_health_read_audit_is_exact_owner_only(production_environment) -> None:
    from app.control_plane.auth import SystemHealthAuditWriter

    owner_id = uuid4()
    member_id = uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) values "
            "(%s,'Health Owner','active','platform_owner'),"
            "(%s,'Health Member','active','member')",
            (owner_id,member_id),
        )
    writer = SystemHealthAuditWriter(
        production_environment["urls"]["platform_audit_append"]
    )
    writer(AuthContext(owner_id,Role.PLATFORM_OWNER,uuid4(),False))
    with pytest.raises(Exception, match="rejected"):
        writer(AuthContext(member_id,Role.MEMBER,uuid4(),False))
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select event_type,target_type,result,reason_code "
            "from platform_control.audit_events where actor_internal_user_id=%s",
            (owner_id,),
        ).fetchone() == (
            "system_health_read_completed","platform_system","completed","privileged_read"
        )


@pytest.mark.postgres
def test_database_attempt_claim_is_atomic_and_environment_bound(production_environment) -> None:
    from app.control_plane.auth import LoginAttempt

    repository = _db_repository(production_environment)
    state = repository.secrets.random_token()
    now = datetime.now(UTC)
    attempt = LoginAttempt(
        uuid4(), "qr", repository.secrets.digest("oauth-state", state), 9,
        repository.secrets.digest("pkce-verifier", repository.secrets.random_token()), 9,
        repository.secrets.seal_verifier(repository.secrets.random_token()),
        "/", "production", now + timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    results = []
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        results.append(repository.claim_attempt(
            state_digest=attempt.state_digest,
            environment="production",
            attempt_kind="qr",
        ))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(item is not None for item in results) == 1
    assert repository.claim_attempt(
        state_digest=attempt.state_digest,
        environment="preview",
        attempt_kind="qr",
    ) is None


@pytest.mark.postgres
def test_session_issuance_rechecks_current_generation_under_shared_lock(production_environment) -> None:
    from app.control_plane.auth import LoginAttempt

    repository = _db_repository(production_environment)
    user_id, old_generation = _seed_current_bound_member(production_environment)
    state = repository.secrets.random_token()
    attempt = LoginAttempt(
        uuid4(), "qr", repository.secrets.digest("oauth-state", state), 9,
        repository.secrets.digest("pkce-verifier", repository.secrets.random_token()), 9,
        repository.secrets.seal_verifier(repository.secrets.random_token()),
        "/", "production", datetime.now(UTC) + timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    assert repository.claim_attempt(
        state_digest=attempt.state_digest, environment="production", attempt_kind="qr"
    ) is not None

    replacement = uuid4()
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',0,0,%s,now())",
            (replacement, "e" * 64),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s,last_complete_at=now(),updated_at=now() where singleton",
            (replacement,),
        )

    assert repository.issue_session(
        attempt_id=attempt.attempt_id,
        internal_user_id=user_id,
        token_digest=repository.secrets.digest("session", repository.secrets.random_token()),
        token_key_version=9,
        csrf_digest=repository.secrets.digest("csrf", repository.secrets.random_token()),
        csrf_key_version=9,
        idle_seconds=28_800,
        absolute_seconds=86_400,
    ) is None


@pytest.mark.postgres
def test_database_session_uses_db_expiry_rechecks_member_and_revokes_logout(production_environment) -> None:
    from app.control_plane.auth import LoginAttempt

    repository = _db_repository(production_environment)
    user_id, generation = _seed_current_bound_member(production_environment)
    state = repository.secrets.random_token()
    attempt = LoginAttempt(
        uuid4(), "in_client", repository.secrets.digest("oauth-state", state), 9,
        repository.secrets.digest("pkce-verifier", repository.secrets.random_token()), 9,
        repository.secrets.seal_verifier(repository.secrets.random_token()),
        "/", "production", datetime.now(UTC) + timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    repository.claim_attempt(state_digest=attempt.state_digest, environment="production", attempt_kind="in_client")
    raw_cookie = repository.secrets.random_token()
    raw_csrf = repository.secrets.random_token()
    issued = repository.issue_session(
        attempt_id=attempt.attempt_id, internal_user_id=user_id,
        token_digest=repository.secrets.digest("session", raw_cookie), token_key_version=9,
        csrf_digest=repository.secrets.digest("csrf", raw_csrf), csrf_key_version=9,
        idle_seconds=28_800, absolute_seconds=86_400,
    )
    assert issued is not None
    session_id, idle, absolute = issued
    assert timedelta(hours=7, minutes=59) < idle - datetime.now(UTC) <= timedelta(hours=8)
    assert timedelta(hours=23, minutes=59) < absolute - datetime.now(UTC) <= timedelta(hours=24)
    authenticated = repository.authenticate_session(
        token_digest=repository.secrets.digest("session", raw_cookie), token_key_version=9,
        idle_seconds=28_800,
    )
    assert authenticated is not None
    context, expected_csrf = authenticated
    assert context.internal_user_id == user_id
    assert expected_csrf == repository.secrets.digest("csrf", raw_csrf)
    assert repository.revoke_session(session_id=session_id, reason="logout")
    assert repository.authenticate_session(
        token_digest=repository.secrets.digest("session", raw_cookie), token_key_version=9,
        idle_seconds=28_800,
    ) is None


@pytest.mark.postgres
def test_directory_promotion_serializes_before_waiting_session_issue(
    production_environment,
) -> None:
    from app.control_plane.auth import LoginAttempt

    repository = _db_repository(production_environment)
    user_id, _ = _seed_current_bound_member(production_environment)
    state = repository.secrets.random_token()
    verifier = repository.secrets.random_token()
    attempt = LoginAttempt(
        uuid4(),"qr",repository.secrets.digest("oauth-state",state),9,
        repository.secrets.digest("pkce-verifier",verifier),9,
        repository.secrets.seal_verifier(verifier),"/","production",
        datetime.now(UTC)+timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    repository.claim_attempt(
        state_digest=attempt.state_digest,environment="production",attempt_kind="qr"
    )
    promotion_locked = threading.Event()
    release_promotion = threading.Event()
    issuer_started = threading.Event()
    result = []

    def promote_departure():
        replacement = uuid4()
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute("select platform_control.lock_dingtalk_identity_directory()")
            connection.execute(
                "insert into platform_control.directory_generations "
                "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
                "values (%s,'complete',0,0,%s,now())",
                (replacement,"f"*64),
            )
            connection.execute(
                "update platform_control.directory_state set active_generation_id=%s,last_complete_at=now(),updated_at=now() where singleton",
                (replacement,),
            )
            promotion_locked.set()
            assert release_promotion.wait(5)

    def issue():
        issuer_started.set()
        result.append(repository.issue_session(
            attempt_id=attempt.attempt_id,internal_user_id=user_id,
            token_digest=repository.secrets.digest("session",repository.secrets.random_token()),
            token_key_version=9,
            csrf_digest=repository.secrets.digest("csrf",repository.secrets.random_token()),
            csrf_key_version=9,idle_seconds=28_800,absolute_seconds=86_400,
        ))

    promotion = threading.Thread(target=promote_departure)
    promotion.start()
    assert promotion_locked.wait(5)
    issuer = threading.Thread(target=issue)
    issuer.start()
    assert issuer_started.wait(5)
    issuer.join(0.05)
    assert issuer.is_alive()
    release_promotion.set()
    promotion.join(5)
    issuer.join(5)

    assert not promotion.is_alive()
    assert not issuer.is_alive()
    assert result == [None]
