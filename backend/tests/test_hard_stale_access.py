from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app.control_plane.dingtalk import DingTalkAuthResult, DingTalkMember
from app.control_plane.identity import (
    IdentityResolutionError,
    decide_stale_access,
)
from app.control_plane.admin_cli import OfflineOwnerAdministrator
from app.control_plane.audit import AuditWriter
from app.control_plane.models import (
    ControlUser,
    DirectoryFreshness,
    DirectoryState,
    Role,
    ResolvedLoginIdentity,
    StaleAccessDecision,
)
from test_control_plane_migration import control_database
from test_dingtalk_identity import _resolver
from test_identity_admin_cli import _codec, _seed_internal_user
from test_web_session_security import (
    Provider,
    SessionRepository,
    _db_repository,
    _seed_current_bound_member,
)


NOW = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)
GENERATION = uuid4()


def _user(
    role: Role,
    *,
    status: str = "active",
    last_confirmed_active: bool = True,
    locally_invalidated_at: datetime | None = None,
) -> ControlUser:
    return ControlUser(
        internal_user_id=uuid4(),
        role=role,
        status=status,
        last_confirmed_active=last_confirmed_active,
        locally_invalidated_at=locally_invalidated_at,
    )


def _directory(freshness: DirectoryFreshness) -> DirectoryState:
    return DirectoryState(
        active_generation_id=GENERATION,
        last_complete_at=NOW,
        freshness=freshness,
    )


@pytest.mark.parametrize("freshness", [DirectoryFreshness.FRESH, DirectoryFreshness.WARNING])
@pytest.mark.parametrize("role", list(Role))
def test_fresh_and_warning_allow_bound_active_users_without_read_only(
    freshness: DirectoryFreshness,
    role: Role,
) -> None:
    assert decide_stale_access(_user(role), _directory(freshness)) == (
        StaleAccessDecision(
            allowed=True,
            read_only=False,
            reason=freshness.value,
        )
    )


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.MANAGEMENT_VIEWER])
def test_hard_stale_allows_only_previously_bound_privileged_users_read_only(
    role: Role,
) -> None:
    assert decide_stale_access(
        _user(role), _directory(DirectoryFreshness.HARD_STALE)
    ) == StaleAccessDecision(
        allowed=True,
        read_only=True,
        reason="privileged_last_generation",
    )


def test_hard_stale_rejects_existing_and_new_members() -> None:
    for confirmed in (True, False):
        assert decide_stale_access(
            _user(Role.MEMBER, last_confirmed_active=confirmed),
            _directory(DirectoryFreshness.HARD_STALE),
        ) == StaleAccessDecision(
            allowed=False,
            read_only=True,
            reason="member_hard_stale",
        )


@pytest.mark.parametrize("role", list(Role))
def test_unbound_identity_is_rejected_even_before_hard_stale(role: Role) -> None:
    decision = decide_stale_access(
        _user(role, last_confirmed_active=False),
        _directory(DirectoryFreshness.WARNING),
    )
    assert decision == StaleAccessDecision(False, True, "unbound_identity")


@pytest.mark.parametrize("role", [Role.PLATFORM_OWNER, Role.MANAGEMENT_VIEWER])
@pytest.mark.parametrize(
    "status,invalidated",
    [
        ("inactive", None),
        ("disabled", None),
        ("active", NOW),
    ],
)
def test_local_departure_or_disable_overrides_privileged_continuity(
    role: Role,
    status: str,
    invalidated: datetime | None,
) -> None:
    assert decide_stale_access(
        _user(role, status=status, locally_invalidated_at=invalidated),
        _directory(DirectoryFreshness.HARD_STALE),
    ) == StaleAccessDecision(False, True, "locally_inactive")


def test_missing_complete_generation_rejects_every_identity() -> None:
    directory = DirectoryState(
        active_generation_id=None,
        last_complete_at=None,
        freshness=DirectoryFreshness.HARD_STALE,
    )
    assert decide_stale_access(_user(Role.PLATFORM_OWNER), directory) == (
        StaleAccessDecision(False, True, "unbound_identity")
    )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("role", ["platform_owner", "management_viewer"])
async def test_existing_privileged_identity_can_resolve_read_only_when_hard_stale(
    control_database,
    tmp_path,
    role: str,
) -> None:
    environment = control_database["environments"]["production"]
    member = DingTalkMember("stale-user", "stale-union", "Stale User", True, (1,))
    resolver = _resolver(environment, tmp_path, member)
    auth_result = DingTalkAuthResult(member.unionid, member.userid, "test-corp")
    internal_user_id = await resolver.resolve_active_member(
        auth_result, DirectoryFreshness.FRESH
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role=%s "
            "where internal_user_id=%s",
            (role, internal_user_id),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp()-interval '25 hours' where singleton"
        )

    resolved = await resolver.resolve_login_identity(
        auth_result, DirectoryFreshness.HARD_STALE
    )

    assert resolved.internal_user_id == internal_user_id
    assert resolved.hard_stale_read_only is True
    assert resolved.reason == "privileged_last_generation"


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.parametrize("role", ["member", "platform_owner"])
async def test_hard_stale_rejects_member_or_locally_departed_owner(
    control_database,
    tmp_path,
    role: str,
) -> None:
    environment = control_database["environments"]["production"]
    member = DingTalkMember(
        f"rejected-{role}", f"union-{role}", "Rejected User", True, (1,)
    )
    resolver = _resolver(environment, tmp_path, member)
    auth_result = DingTalkAuthResult(member.unionid, member.userid, "test-corp")
    internal_user_id = await resolver.resolve_active_member(
        auth_result, DirectoryFreshness.FRESH
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role=%s,"
            "locally_invalidated_at=case when %s='platform_owner' "
            "then clock_timestamp() else null end where internal_user_id=%s",
            (role, role, internal_user_id),
        )

    with pytest.raises(IdentityResolutionError, match="directory unavailable"):
        await resolver.resolve_login_identity(
            auth_result, DirectoryFreshness.HARD_STALE
        )


@pytest.mark.asyncio
async def test_hard_stale_login_issues_a_read_only_web_session() -> None:
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth

    repository = SessionRepository()
    user_id = uuid4()

    class StaleProvider(Provider):
        async def complete(self, code, verifier=None):
            self.calls += 1
            return ResolvedLoginIdentity(
                user_id,
                True,
                "privileged_last_generation",
            )

    provider = StaleProvider(user_id)
    audit_calls = []
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"z" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        hard_stale_audit=lambda actor, access_kind, target: audit_calls.append(
            (actor, access_kind, target)
        ),
    )
    started = auth.start_qr("/")

    issued = await auth.complete_qr(started.state, "one-time-code")
    authenticated, _ = auth.authenticate(issued.cookie_token)

    assert authenticated.internal_user_id == user_id
    assert authenticated.hard_stale_read_only is True
    assert audit_calls == [(user_id, "login", "self")]


@pytest.mark.asyncio
async def test_hard_stale_login_fails_closed_and_revokes_session_when_audit_fails() -> None:
    from app.control_plane.auth import (
        AuthSecrets,
        AuthenticationError,
        DingTalkWebAuth,
    )

    repository = SessionRepository()
    user_id = uuid4()

    class StaleProvider(Provider):
        async def complete(self, code, verifier=None):
            return ResolvedLoginIdentity(
                user_id, True, "privileged_last_generation"
            )

    def fail_audit(*_args) -> None:
        raise AuthenticationError("required audit unavailable")

    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"z" * 32, key_version=1),
        qr_login=StaleProvider(user_id).complete,
        in_client_login=StaleProvider(user_id).complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        hard_stale_audit=fail_audit,
    )
    started = auth.start_qr("/")

    with pytest.raises(AuthenticationError, match="required audit unavailable"):
        await auth.complete_qr(started.state, "one-time-code")
    assert repository.sessions
    assert all(row["revoked"] for row in repository.sessions.values())


@pytest.mark.postgres
def test_hard_stale_access_audit_exposes_only_reason_and_freshness_time(
    control_database,
) -> None:
    from app.control_plane.auth import HardStaleAccessAuditWriter

    environment = control_database["environments"]["production"]
    user_id, _ = _seed_current_bound_member(environment)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='member' "
            "where role='platform_owner'"
        )
        connection.execute(
            "update platform_control.internal_users set role='platform_owner' "
            "where internal_user_id=%s",
            (user_id,),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp()-interval '25 hours' where singleton"
        )
    writer = HardStaleAccessAuditWriter(
        environment["urls"]["platform_audit_append"]
    )

    writer(user_id, "login", "self")
    writer(user_id, "read", "governance_audit")

    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select event_type,reason_code,sanitized_before_after "
            "from platform_control.audit_events "
            "where actor_internal_user_id=%s and event_type like 'hard_stale%%' "
            "order by event_type",
            (user_id,),
        ).fetchall()
    assert [row[0] for row in rows] == [
        "hard_stale_privileged_login_completed",
        "hard_stale_privileged_read_completed",
    ]
    assert all(row[1] == "privileged_last_generation" for row in rows)
    assert all(set(row[2]) == {"freshness_reason", "last_complete_at"} for row in rows)


@pytest.mark.postgres
def test_hard_stale_management_read_adds_required_access_audit(
    control_database,
) -> None:
    from app.control_plane.auth import HardStaleAccessAuditWriter
    from app.control_plane.models import AuthContext, Role
    from app.control_plane.routes_manage import ManagementRepository, ManagementService

    environment = control_database["environments"]["production"]
    user_id, _ = _seed_current_bound_member(environment)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='member' "
            "where role='platform_owner'"
        )
        connection.execute(
            "update platform_control.internal_users set role='platform_owner' "
            "where internal_user_id=%s",
            (user_id,),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp()-interval '25 hours' where singleton"
        )
    service = ManagementService(
        ManagementRepository(environment["urls"]["platform_control_app"]),
        AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append"]
        ),
        hard_stale_audit=HardStaleAccessAuditWriter(
            environment["urls"]["platform_audit_append"]
        ),
    )

    service.list_users(AuthContext(user_id, Role.PLATFORM_OWNER, uuid4(), True))

    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.audit_events "
            "where actor_internal_user_id=%s "
            "and event_type='hard_stale_privileged_read_completed' "
            "and target_internal_id='management_user_directory'",
            (user_id,),
        ).fetchone() == (1,)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "role,allowed,read_only",
    [
        ("member", False, None),
        ("platform_owner", True, True),
        ("management_viewer", True, True),
    ],
)
def test_existing_session_is_rechecked_when_directory_crosses_hard_stale(
    control_database,
    role: str,
    allowed: bool,
    read_only: bool | None,
) -> None:
    from app.control_plane.auth import LoginAttempt

    environment = control_database["environments"]["production"]
    repository = _db_repository(environment)
    user_id, _ = _seed_current_bound_member(environment)
    state = repository.secrets.random_token()
    verifier = repository.secrets.random_token()
    attempt = LoginAttempt(
        uuid4(),
        "qr",
        repository.secrets.digest("oauth-state", state),
        9,
        repository.secrets.digest("pkce-verifier", verifier),
        9,
        repository.secrets.seal_verifier(verifier),
        "/",
        "production",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    assert repository.claim_attempt(
        state_digest=attempt.state_digest,
        environment="production",
        attempt_kind="qr",
    ) is not None
    raw_cookie = repository.secrets.random_token()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='member' "
            "where role='platform_owner'"
        )
        connection.execute(
            "update platform_control.internal_users set role=%s "
            "where internal_user_id=%s",
            (role, user_id),
        )
    assert repository.issue_session(
        attempt_id=attempt.attempt_id,
        internal_user_id=user_id,
        token_digest=repository.secrets.digest("session", raw_cookie),
        token_key_version=9,
        csrf_digest=repository.secrets.digest("csrf", "csrf"),
        csrf_key_version=9,
        idle_seconds=28_800,
        absolute_seconds=86_400,
        hard_stale_read_only=False,
    ) is not None

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp()-interval '25 hours' "
            "where singleton"
        )
    authenticated = repository.authenticate_session(
        token_digest=repository.secrets.digest("session", raw_cookie),
        token_key_version=9,
        idle_seconds=28_800,
    )

    assert (authenticated is not None) is allowed
    if authenticated is not None:
        assert authenticated[0].hard_stale_read_only is read_only


@pytest.mark.postgres
def test_privileged_session_returns_to_normal_only_after_fresh_reconciliation(
    control_database,
) -> None:
    from app.control_plane.auth import LoginAttempt

    environment = control_database["environments"]["production"]
    repository = _db_repository(environment)
    user_id, _ = _seed_current_bound_member(environment)
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='member' "
            "where role='platform_owner'"
        )
        connection.execute(
            "update platform_control.internal_users set role='platform_owner' "
            "where internal_user_id=%s",
            (user_id,),
        )
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp()-interval '25 hours' "
            "where singleton"
        )
    state = repository.secrets.random_token()
    verifier = repository.secrets.random_token()
    attempt = LoginAttempt(
        uuid4(), "qr", repository.secrets.digest("oauth-state", state), 9,
        repository.secrets.digest("pkce-verifier", verifier), 9,
        repository.secrets.seal_verifier(verifier), "/", "production",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    repository.create_attempt(attempt)
    repository.claim_attempt(
        state_digest=attempt.state_digest,
        environment="production",
        attempt_kind="qr",
    )
    raw_cookie = repository.secrets.random_token()
    assert repository.issue_session(
        attempt_id=attempt.attempt_id,
        internal_user_id=user_id,
        token_digest=repository.secrets.digest("session", raw_cookie),
        token_key_version=9,
        csrf_digest=repository.secrets.digest("csrf", "csrf"),
        csrf_key_version=9,
        idle_seconds=28_800,
        absolute_seconds=86_400,
        hard_stale_read_only=True,
    ) is not None
    first = repository.authenticate_session(
        token_digest=repository.secrets.digest("session", raw_cookie),
        token_key_version=9,
        idle_seconds=28_800,
    )
    assert first is not None and first[0].hard_stale_read_only is True

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.directory_state set "
            "last_complete_at=clock_timestamp() where singleton"
        )
    second = repository.authenticate_session(
        token_digest=repository.secrets.digest("session", raw_cookie),
        token_key_version=9,
        idle_seconds=28_800,
    )
    assert second is not None and second[0].hard_stale_read_only is False


@pytest.mark.postgres
def test_break_glass_requires_explicit_last_stale_generation_and_rejects_local_departure(
    control_database,
    tmp_path,
) -> None:
    environment = control_database["environments"]["production"]
    codec = _codec(tmp_path)
    provider_id = "stale-break-glass-target"
    protected = codec.seal("employee", provider_id)
    target = _seed_internal_user(environment, protected, "Stale Replacement")
    generation_id = uuid4()
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set role='member' "
            "where role='platform_owner'"
        )
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status,role) "
            "values (%s,'Departed Owner','inactive','platform_owner')",
            (uuid4(),),
        )
        connection.execute(
            "insert into platform_control.directory_generations "
            "(generation_id,status,member_count,department_count,content_sha256,completed_at) "
            "values (%s,'complete',1,0,%s,clock_timestamp()-interval '25 hours')",
            (generation_id, "a" * 64),
        )
        connection.execute(
            "insert into platform_control.directory_members "
            "(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version,"
            "display_name,status) values (%s,%s,%s,'employee',%s,%s,%s,%s,%s,'active')",
            (
                generation_id,
                uuid4(),
                target,
                protected.lookup_hmac,
                protected.lookup_key_version,
                protected.ciphertext,
                protected.encryption_key_version,
                "Stale Replacement",
            ),
        )
        connection.execute(
            "update platform_control.internal_users set "
            "last_confirmed_generation_id=%s where internal_user_id=%s",
            (generation_id, target),
        )
        connection.execute(
            "update platform_control.directory_state set active_generation_id=%s,"
            "last_complete_at=clock_timestamp()-interval '25 hours' where singleton",
            (generation_id,),
        )
    administrator = OfflineOwnerAdministrator(
        environment["urls"]["platform_control_migrator"],
        owner_role="platform_control_owner",
        identity_codec=codec,
        audit_writer=AuditWriter.from_database_url(
            environment["urls"]["platform_audit_append"]
        ),
    )
    arguments = dict(
        action="replace",
        provider_id=provider_id,
        subject_kind="employee",
        generation_id=generation_id,
        os_operator="root",
        approvers=("uid:1001", "uid:1002"),
        backup_reference="BACKUP_STALE",
        incident_reference="INC_STALE",
    )

    with pytest.raises(ValueError, match="explicit stale generation acceptance required"):
        administrator.prepare_owner_change(
            **arguments, accept_stale_generation=None
        )
    payload = administrator.prepare_owner_change(
        **arguments, accept_stale_generation=generation_id
    )
    assert payload["accepted_stale_generation_id"] == str(generation_id)
    changed = administrator._change_owner(
        payload=payload,
        provider_id=provider_id,
        subject_kind="employee",
    )
    assert changed["internal_user_id"] == str(target)
    with psycopg.connect(environment["admin"]) as connection:
        assert connection.execute(
            "select role from platform_control.internal_users "
            "where internal_user_id=%s",
            (target,),
        ).fetchone() == ("platform_owner",)

    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "update platform_control.internal_users set "
            "locally_invalidated_at=clock_timestamp() where internal_user_id=%s",
            (target,),
        )
    with pytest.raises(ValueError, match="target unavailable in selected generation"):
        administrator.prepare_owner_change(
            **arguments, accept_stale_generation=generation_id
        )
