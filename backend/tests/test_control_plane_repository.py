from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.crypto import ProtectedProviderId
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


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("create_login_attempt", ("qr", "state", 300), {}),
        ("consume_login_attempt", ("state",), {}),
        ("create_web_session", (uuid4(), 28_800, 86_400), {}),
        ("rotate_web_session", ("cookie", 28_800), {}),
        ("revoke_user_sessions", (uuid4(), "logout"), {}),
    ],
)
def test_legacy_unbound_session_entry_points_fail_closed_without_database_access(
    repository: ControlRepository,
    monkeypatch,
    method_name,
    args,
    kwargs,
) -> None:
    monkeypatch.setattr(
        repository,
        "_connection",
        lambda: pytest.fail("legacy Session API reached the database"),
    )

    with pytest.raises(
        ControlRepositoryError, match="secure web authentication flow required"
    ):
        getattr(repository, method_name)(*args, **kwargs)


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
