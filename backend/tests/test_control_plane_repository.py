from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.crypto import ProtectedProviderId
from app.control_plane.models import IssuedWebSession
from app.control_plane.repository import (
    ControlRepository,
    ControlRepositoryError,
    IdentityCollisionError,
    IdentityKeyPolicyError,
)
from test_identity_crypto import _codec
from test_control_plane_migration import control_database


@pytest.fixture
def production_environment(control_database):
    return control_database["environments"]["production"]


@pytest.fixture
def repository(production_environment, tmp_path: Path) -> ControlRepository:
    with psycopg.connect(
        production_environment["urls"]["platform_control_maintenance"]
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select platform_control.set_provider_identity_key_policy("
                "'dingtalk', array[1,2])"
            )
    repository = ControlRepository(
        production_environment["urls"]["platform_control_app"],
        identity_codec=_codec(tmp_path),
    )
    repository._test_admin_url = production_environment["admin"]
    return repository


def _stored_identity(protected: ProtectedProviderId, user_id: UUID) -> tuple:
    return (
        uuid4(),
        user_id,
        protected.subject_kind,
        protected.lookup_hmac,
        protected.lookup_key_version,
        protected.ciphertext,
        protected.encryption_key_version,
    )


def _seed_internal_user(
    repository: ControlRepository,
    protected: ProtectedProviderId,
    display_name: str,
) -> UUID:
    user_id = uuid4()
    with psycopg.connect(repository._test_admin_url) as connection:
        existing = connection.execute(
            "select internal_user_id from platform_control.provider_identities "
            "where subject_kind=%s and lookup_hmac=%s and lookup_key_version=%s",
            (
                protected.subject_kind,
                protected.lookup_hmac,
                protected.lookup_key_version,
            ),
        ).fetchone()
        if existing is not None:
            return existing[0]
        connection.execute(
            "insert into platform_control.internal_users "
            "(internal_user_id,display_name,status) values (%s,%s,'active')",
            (user_id, display_name),
        )
        connection.execute(
            "insert into platform_control.provider_identities "
            "(provider_identity_id,internal_user_id,subject_kind,lookup_hmac,"
            "lookup_key_version,encrypted_provider_id,encryption_key_version) "
            "values (%s,%s,%s,%s,%s,%s,%s)",
            _stored_identity(protected, user_id),
        )
    return user_id


@pytest.mark.postgres
def test_seeded_provider_identity_resolves_across_fresh_ciphertexts(
    repository: ControlRepository,
) -> None:
    first = repository.identity_codec.seal("employee", "synthetic-provider-id")
    second = repository.identity_codec.seal("employee", "synthetic-provider-id")

    first_user = _seed_internal_user(repository, first, "Synthetic User")
    second_user = repository.resolve_provider_identity(second)

    assert second_user == first_user
    assert repository.resolve_provider_identity(second) == first_user
    assert not hasattr(repository, "resolve_user_by_display_name")
    assert not hasattr(repository, "find_by_display_name")


@pytest.mark.postgres
def test_repository_establishes_and_checks_exact_database_transition_policy(
    repository: ControlRepository,
    production_environment,
) -> None:
    protected = repository.identity_codec.seal(
        "employee", "policy-provider-id"
    )

    _seed_internal_user(repository, protected, "Policy User")
    assert repository.resolve_provider_identity(protected) is not None

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select provider, lookup_transition_versions "
                "from platform_control.provider_identity_key_policies"
            )
            assert cursor.fetchall() == [("dingtalk", [1, 2])]


@pytest.mark.postgres
def test_every_identity_transaction_fails_closed_on_database_policy_mismatch(
    repository: ControlRepository,
    production_environment,
) -> None:
    original = repository.identity_codec.seal(
        "employee", "policy-mismatch-resolve-provider-id"
    )
    internal_user_id = _seed_internal_user(repository,
        original, "Policy Mismatch User"
    )
    with psycopg.connect(
        production_environment["urls"]["platform_control_maintenance"]
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select platform_control.set_provider_identity_key_policy("
                "'dingtalk', array[2,3])"
            )

    with pytest.raises(
        IdentityKeyPolicyError,
        match="provider identity key policy mismatch",
    ):
        repository.resolve_provider_identity(original)

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*), min(display_name) from "
                "platform_control.provider_identities identity join "
                "platform_control.internal_users users using (internal_user_id) "
                "where users.internal_user_id = %s",
                (internal_user_id,),
            )
            assert cursor.fetchone() == (1, "Policy Mismatch User")


@pytest.mark.postgres
def test_resolve_rejects_lookup_collision_without_leaking_values(
    repository: ControlRepository,
) -> None:
    protected = repository.identity_codec.seal("employee", "synthetic-provider-id")
    _seed_internal_user(repository, protected, "Synthetic User")
    collision = ProtectedProviderId(
        subject_kind=protected.subject_kind,
        lookup_hmac=protected.lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        ciphertext=b"unrelated-ciphertext",
        encryption_key_version=protected.encryption_key_version,
    )

    with pytest.raises(IdentityCollisionError, match="provider identity collision") as caught:
        repository.resolve_provider_identity(collision)

    assert "Synthetic User" not in str(caught.value)
    assert "Different User" not in str(caught.value)
    assert protected.lookup_hmac.hex() not in str(caught.value)


@pytest.mark.parametrize("tamper", ["lookup", "version"])
def test_repository_rejects_supplied_lookup_not_derived_from_plaintext_before_query(
    tmp_path: Path,
    tamper: str,
) -> None:
    connect_calls = 0

    def reject_query(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("database must not be queried")

    repository = ControlRepository(
        "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
        identity_codec=_codec(tmp_path),
        connect=reject_query,
    )
    sealed = repository.identity_codec.seal(
        "employee", "authenticated-provider-id"
    )
    malformed = ProtectedProviderId(
        subject_kind=sealed.subject_kind,
        lookup_hmac=(
            b"x" * 32 if tamper == "lookup" else sealed.lookup_hmac
        ),
        lookup_key_version=(
            99 if tamper == "version" else sealed.lookup_key_version
        ),
        ciphertext=sealed.ciphertext,
        encryption_key_version=sealed.encryption_key_version,
    )

    with pytest.raises(IdentityCollisionError, match="provider identity collision"):
        repository.resolve_provider_identity(malformed)

    assert connect_calls == 0


def test_generic_identity_create_and_rotation_are_retired(
    tmp_path: Path,
) -> None:
    connect_calls = 0

    def reject_connection(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("database connection must not be opened")

    repository = ControlRepository(
        "postgresql://platform_control_app@127.0.0.1/agent_platform_control",
        identity_codec=_codec(tmp_path),
        connect=reject_connection,
    )
    previous = repository.identity_codec.seal("employee", "provider-id")
    rotated = repository.identity_codec.rotate(previous)

    with pytest.raises(
        ControlRepositoryError, match="verified directory identity required"
    ):
        repository.create_internal_user(previous, "User")
    with pytest.raises(
        ControlRepositoryError, match="verified directory identity required"
    ):
        repository.rotate_provider_identity(uuid4(), previous, rotated)

    assert connect_calls == 0


@pytest.mark.postgres
def test_web_session_persists_only_hashes_and_uses_database_expiry(
    repository: ControlRepository,
    production_environment,
) -> None:
    user_id = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "session-provider-id"),
        "Session User",
    )

    issued = repository.create_web_session(user_id, idle_seconds=120, absolute_seconds=300)

    assert isinstance(issued, IssuedWebSession)
    assert issued.cookie_token not in repr(issued)
    assert issued.csrf_token not in repr(issued)
    assert issued.idle_expires_at < issued.absolute_expires_at
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select token_hash, csrf_hash, "
                "idle_expires_at - created_at, absolute_expires_at - created_at "
                "from platform_control.web_sessions where session_id = %s",
                (issued.session_id,),
            )
            token_hash, csrf_hash, idle_delta, absolute_delta = cursor.fetchone()
    assert bytes(token_hash) == hashlib.sha256(issued.cookie_token.encode()).digest()
    assert bytes(csrf_hash) == hashlib.sha256(issued.csrf_token.encode()).digest()
    assert idle_delta.total_seconds() == pytest.approx(120, abs=0.01)
    assert absolute_delta.total_seconds() == pytest.approx(300, abs=0.01)


@pytest.mark.postgres
def test_login_attempt_state_is_opaque_expiring_and_single_use(
    repository: ControlRepository,
    production_environment,
) -> None:
    raw_state = "opaque-login-state"
    attempt_id = repository.create_login_attempt(
        "qr", raw_state, ttl_seconds=60, return_path="/account"
    )

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select state_hash, expires_at > now(), consumed_at "
                "from platform_control.login_attempts where login_attempt_id = %s",
                (attempt_id,),
            )
            state_hash, active, consumed_at = cursor.fetchone()
    assert bytes(state_hash) == hashlib.sha256(raw_state.encode()).digest()
    assert active is True
    assert consumed_at is None
    assert repository.consume_login_attempt(raw_state) == attempt_id
    assert repository.consume_login_attempt(raw_state) is None


@pytest.mark.postgres
def test_expired_login_attempt_and_web_session_cannot_be_rotated(
    repository: ControlRepository,
    production_environment,
) -> None:
    attempt_id = repository.create_login_attempt("in_client", "expired-state", 60)
    user_id = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "expiry-provider-id"),
        "Expiry User",
    )
    issued = repository.create_web_session(user_id, 60, 120)
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update platform_control.login_attempts set expires_at = now() - interval '1 second' "
                "where login_attempt_id = %s",
                (attempt_id,),
            )
            cursor.execute(
                "update platform_control.web_sessions set idle_expires_at = now() - interval '2 seconds', "
                "absolute_expires_at = now() - interval '1 second' where session_id = %s",
                (issued.session_id,),
            )

    assert repository.consume_login_attempt("expired-state") is None
    assert repository.rotate_web_session(issued.cookie_token, 60) is None


@pytest.mark.postgres
def test_session_rotation_and_revocation_are_atomic(
    repository: ControlRepository,
    production_environment,
) -> None:
    user_id = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "revoke-provider-id"),
        "Revoke User",
    )
    first = repository.create_web_session(user_id, 60, 120)
    assert "absolute_seconds" not in inspect.signature(
        repository.rotate_web_session
    ).parameters
    rotated = repository.rotate_web_session(first.cookie_token, 60)

    assert rotated is not None
    assert rotated.session_id != first.session_id
    assert rotated.cookie_token != first.cookie_token
    assert rotated.absolute_expires_at == first.absolute_expires_at
    assert repository.rotate_web_session(first.cookie_token, 60) is None
    assert repository.revoke_user_sessions(user_id, "security-test") == 1
    assert repository.rotate_web_session(rotated.cookie_token, 60) is None
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select revoked_reason from platform_control.web_sessions "
                "where session_id = %s",
                (rotated.session_id,),
            )
            assert cursor.fetchone() == ("security-test",)


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("absolute_seconds", "rotated_idle_seconds"),
    [(90, 15), (600, 300)],
)
def test_session_rotation_preserves_smaller_and_larger_absolute_deadlines_exactly(
    repository: ControlRepository,
    absolute_seconds: int,
    rotated_idle_seconds: int,
) -> None:
    user_id = _seed_internal_user(repository,
        repository.identity_codec.seal(
            "employee", f"absolute-{absolute_seconds}-provider-id"
        ),
        "Absolute Deadline User",
    )
    first = repository.create_web_session(
        user_id,
        idle_seconds=30,
        absolute_seconds=absolute_seconds,
    )

    rotated = repository.rotate_web_session(
        first.cookie_token,
        idle_seconds=rotated_idle_seconds,
    )

    assert rotated is not None
    assert rotated.absolute_expires_at == first.absolute_expires_at
    assert rotated.idle_expires_at <= rotated.absolute_expires_at


@pytest.mark.postgres
def test_database_enforces_at_most_one_owner_role_including_inactive(
    repository: ControlRepository,
    production_environment,
) -> None:
    first = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "owner-one-provider-id"),
        "Owner One",
    )
    second = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "owner-two-provider-id"),
        "Owner Two",
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from platform_control.internal_users "
                "where role = 'platform_owner'"
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "update platform_control.internal_users set role = 'platform_owner' "
                "where internal_user_id = %s",
                (first,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cursor.execute(
                    "update platform_control.internal_users set "
                    "role = 'platform_owner', status = 'inactive' "
                    "where internal_user_id = %s",
                    (second,),
                )


@pytest.mark.postgres
def test_observation_scopes_are_exact_active_agent_ids(
    repository: ControlRepository,
    production_environment,
) -> None:
    viewer = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "viewer-provider-id"),
        "Viewer",
    )
    owner = _seed_internal_user(repository,
        repository.identity_codec.seal("employee", "grantor-provider-id"),
        "Grantor",
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into platform_control.observation_grants "
                "(observation_grant_id, agent_id, viewer_internal_user_id, created_by) "
                "values (%s, %s, %s, %s), (%s, %s, %s, %s), (%s, %s, %s, %s)",
                (
                    uuid4(), "agent-a", viewer, owner,
                    uuid4(), "agent-a-child", viewer, owner,
                    uuid4(), "revoked-agent", viewer, owner,
                ),
            )
            cursor.execute(
                "update platform_control.observation_grants set revoked_at = now(), revoked_by = %s "
                "where agent_id = %s and viewer_internal_user_id = %s",
                (owner, "revoked-agent", viewer),
            )

    assert repository.list_observation_scopes(viewer) == ("agent-a", "agent-a-child")


def test_repository_rejects_replica_database_dsn(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact control app DSN required"):
        ControlRepository(
            "postgresql://app@127.0.0.1/agent_platform",
            identity_codec=_codec(tmp_path),
        )
