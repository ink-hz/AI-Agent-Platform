from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.crypto import ProtectedProviderId
from app.control_plane.models import IssuedWebSession
from app.control_plane.repository import (
    ControlRepository,
    IdentityCollisionError,
)
from test_identity_crypto import _codec
from test_control_plane_migration import control_database


@pytest.fixture
def production_environment(control_database):
    return control_database["environments"]["production"]


@pytest.fixture
def repository(production_environment, tmp_path: Path) -> ControlRepository:
    return ControlRepository(
        production_environment["urls"]["platform_control_app"],
        identity_codec=_codec(tmp_path),
    )


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


@pytest.mark.postgres
def test_create_internal_user_atomically_resolves_same_provider_identity(
    repository: ControlRepository,
) -> None:
    first = repository.identity_codec.seal("employee", "synthetic-provider-id")
    second = repository.identity_codec.seal("employee", "synthetic-provider-id")

    first_user = repository.create_internal_user(first, "Synthetic User")
    second_user = repository.create_internal_user(second, "Changed Display Name")

    assert second_user == first_user
    assert repository.resolve_provider_identity(second) == first_user
    assert not hasattr(repository, "resolve_user_by_display_name")
    assert not hasattr(repository, "find_by_display_name")


@pytest.mark.postgres
def test_create_internal_user_rejects_lookup_collision_without_leaking_values(
    repository: ControlRepository,
) -> None:
    protected = repository.identity_codec.seal("employee", "synthetic-provider-id")
    repository.create_internal_user(protected, "Synthetic User")
    collision = ProtectedProviderId(
        subject_kind=protected.subject_kind,
        lookup_hmac=protected.lookup_hmac,
        lookup_key_version=protected.lookup_key_version,
        ciphertext=b"unrelated-ciphertext",
        encryption_key_version=protected.encryption_key_version,
    )

    with pytest.raises(IdentityCollisionError, match="provider identity collision") as caught:
        repository.create_internal_user(collision, "Different User")

    assert "Synthetic User" not in str(caught.value)
    assert "Different User" not in str(caught.value)
    assert protected.lookup_hmac.hex() not in str(caught.value)


@pytest.mark.postgres
def test_identity_rotation_preserves_internal_user_id_and_previous_lookup_window(
    repository: ControlRepository,
) -> None:
    provider_id = "rotation-provider-id"
    active = repository.identity_codec.seal("employee", provider_id)
    previous_lookup = dict(repository.identity_codec.lookup_candidates(
        "employee", provider_id
    ))[1]
    original = ProtectedProviderId(
        subject_kind=active.subject_kind,
        lookup_hmac=previous_lookup,
        lookup_key_version=1,
        ciphertext=active.ciphertext,
        encryption_key_version=active.encryption_key_version,
    )
    internal_user_id = repository.create_internal_user(original, "Synthetic User")
    rotated = repository.identity_codec.rotate(original)

    repository.rotate_provider_identity(internal_user_id, original, rotated)

    assert repository.resolve_provider_identity(rotated) == internal_user_id
    assert repository.resolve_provider_identity(original) == internal_user_id


@pytest.mark.postgres
def test_identity_rotation_rejects_a_lookup_not_derived_from_ciphertext(
    repository: ControlRepository,
) -> None:
    original = repository.identity_codec.seal(
        "employee", "tampered-rotation-provider-id"
    )
    internal_user_id = repository.create_internal_user(original, "Rotation User")
    tampered = ProtectedProviderId(
        subject_kind=original.subject_kind,
        lookup_hmac=b"x" * 32,
        lookup_key_version=original.lookup_key_version,
        ciphertext=original.ciphertext,
        encryption_key_version=original.encryption_key_version,
    )

    with pytest.raises(IdentityCollisionError, match="provider identity collision"):
        repository.rotate_provider_identity(internal_user_id, original, tampered)

    assert repository.resolve_provider_identity(original) == internal_user_id


@pytest.mark.postgres
def test_web_session_persists_only_hashes_and_uses_database_expiry(
    repository: ControlRepository,
    production_environment,
) -> None:
    user_id = repository.create_internal_user(
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
    user_id = repository.create_internal_user(
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
    assert repository.rotate_web_session(issued.cookie_token, 60, 120) is None


@pytest.mark.postgres
def test_session_rotation_and_revocation_are_atomic(
    repository: ControlRepository,
    production_environment,
) -> None:
    user_id = repository.create_internal_user(
        repository.identity_codec.seal("employee", "revoke-provider-id"),
        "Revoke User",
    )
    first = repository.create_web_session(user_id, 60, 120)
    rotated = repository.rotate_web_session(first.cookie_token, 60, 120)

    assert rotated is not None
    assert rotated.session_id != first.session_id
    assert rotated.cookie_token != first.cookie_token
    assert repository.rotate_web_session(first.cookie_token, 60, 120) is None
    assert repository.revoke_user_sessions(user_id, "security-test") == 1
    assert repository.rotate_web_session(rotated.cookie_token, 60, 120) is None
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select revoked_reason from platform_control.web_sessions "
                "where session_id = %s",
                (rotated.session_id,),
            )
            assert cursor.fetchone() == ("security-test",)


@pytest.mark.postgres
def test_database_enforces_exactly_one_active_owner(
    repository: ControlRepository,
    production_environment,
) -> None:
    first = repository.create_internal_user(
        repository.identity_codec.seal("employee", "owner-one-provider-id"),
        "Owner One",
    )
    second = repository.create_internal_user(
        repository.identity_codec.seal("employee", "owner-two-provider-id"),
        "Owner Two",
    )
    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update platform_control.internal_users set role = 'platform_owner' "
                "where internal_user_id = %s",
                (first,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                cursor.execute(
                    "update platform_control.internal_users set role = 'platform_owner' "
                    "where internal_user_id = %s",
                    (second,),
                )


@pytest.mark.postgres
def test_observation_scopes_are_exact_active_agent_ids(
    repository: ControlRepository,
    production_environment,
) -> None:
    viewer = repository.create_internal_user(
        repository.identity_codec.seal("employee", "viewer-provider-id"),
        "Viewer",
    )
    owner = repository.create_internal_user(
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
    with pytest.raises(ValueError, match="control database DSN required"):
        ControlRepository(
            "postgresql://app@127.0.0.1/agent_platform",
            identity_codec=_codec(tmp_path),
        )
