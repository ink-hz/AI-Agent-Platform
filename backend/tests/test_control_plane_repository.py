from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import UUID, uuid4

import psycopg
import pytest

from app.control_plane.crypto import ProtectedProviderId
from app.control_plane.models import IssuedWebSession
from app.control_plane.repository import (
    ControlRepository,
    IdentityCollisionError,
    IdentityKeyPolicyError,
    _identity_advisory_lock_key,
)
from test_identity_crypto import _codec, _keyring
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
def test_repository_establishes_and_checks_exact_database_transition_policy(
    repository: ControlRepository,
    production_environment,
) -> None:
    protected = repository.identity_codec.seal(
        "employee", "policy-provider-id"
    )

    repository.create_internal_user(protected, "Policy User")
    assert repository.resolve_provider_identity(protected) is not None

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select provider, lookup_transition_versions "
                "from platform_control.provider_identity_key_policies"
            )
            assert cursor.fetchall() == [("dingtalk", [1, 2])]


@pytest.mark.postgres
@pytest.mark.parametrize("operation", ["resolve", "create", "rotate"])
def test_every_identity_transaction_fails_closed_on_database_policy_mismatch(
    repository: ControlRepository,
    production_environment,
    operation: str,
) -> None:
    original = repository.identity_codec.seal(
        "employee", f"policy-mismatch-{operation}-provider-id"
    )
    internal_user_id = repository.create_internal_user(
        original, "Policy Mismatch User"
    )
    rotated = repository.identity_codec.rotate(original)
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
        if operation == "resolve":
            repository.resolve_provider_identity(original)
        elif operation == "create":
            repository.create_internal_user(rotated, "Should Not Change")
        else:
            repository.rotate_provider_identity(
                internal_user_id, original, rotated
            )

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


@pytest.mark.parametrize("operation", ["resolve", "create"])
@pytest.mark.parametrize("tamper", ["lookup", "version"])
def test_repository_rejects_supplied_lookup_not_derived_from_plaintext_before_query(
    tmp_path: Path,
    operation: str,
    tamper: str,
) -> None:
    connect_calls = 0

    def reject_query(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("database must not be queried")

    repository = ControlRepository(
        "postgresql://unused@127.0.0.1/agent_platform_control",
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
        if operation == "resolve":
            repository.resolve_provider_identity(malformed)
        else:
            repository.create_internal_user(malformed, "Synthetic User")

    assert connect_calls == 0


@pytest.mark.postgres
def test_adjacent_rollout_nodes_atomically_create_or_resolve_one_identity(
    production_environment,
    tmp_path: Path,
) -> None:
    encryption_keys = {1: b"e" * 32, 2: b"E" * 32, 3: b"f" * 32}
    lookup_keys = {1: b"h" * 32, 2: b"H" * 32, 3: b"i" * 32}

    def rollout_codec(node: str, active_version: int):
        from app.control_plane.crypto import ProviderIdentityCodec

        return ProviderIdentityCodec(
            _keyring(
                tmp_path,
                f"{node}-encryption.json",
                "provider-encryption",
                active_version,
                encryption_keys,
            ),
            _keyring(
                tmp_path,
                f"{node}-hmac.json",
                "provider-lookup-hmac",
                active_version,
                lookup_keys,
                transition_versions=(1, 2, 3),
            ),
        )

    database_url = production_environment["urls"]["platform_control_app"]
    old_repository = ControlRepository(
        database_url, identity_codec=rollout_codec("old", 2)
    )
    new_repository = ControlRepository(
        database_url, identity_codec=rollout_codec("new", 3)
    )
    provider_id = "adjacent-rollout-provider-id"
    start = threading.Barrier(2)

    maintenance_url = production_environment["urls"][
        "platform_control_maintenance"
    ]
    with psycopg.connect(maintenance_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select platform_control.set_provider_identity_key_policy("
                "'dingtalk', array[1,2,3])"
            )

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "create function platform_control.delay_rollout_user_insert() "
                "returns trigger language plpgsql as $$ begin "
                "if new.display_name like 'Rollout Node %' then "
                "perform pg_sleep(0.25); end if; return new; end $$"
            )
            cursor.execute(
                "create trigger delay_rollout_user_insert before insert on "
                "platform_control.internal_users for each row execute function "
                "platform_control.delay_rollout_user_insert()"
            )

    def create(repository: ControlRepository, display_name: str) -> UUID:
        protected = repository.identity_codec.seal("employee", provider_id)
        start.wait(timeout=5)
        return repository.create_internal_user(protected, display_name)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            old_future = executor.submit(
                create, old_repository, "Rollout Node Old"
            )
            new_future = executor.submit(
                create, new_repository, "Rollout Node New"
            )
            old_user = old_future.result(timeout=10)
            new_user = new_future.result(timeout=10)
    finally:
        with psycopg.connect(production_environment["admin"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "drop trigger if exists delay_rollout_user_insert on "
                    "platform_control.internal_users"
                )
                cursor.execute(
                    "drop function if exists "
                    "platform_control.delay_rollout_user_insert()"
                )

    assert old_user == new_user
    assert old_repository.resolve_provider_identity(
        old_repository.identity_codec.seal("employee", provider_id)
    ) == old_user
    assert new_repository.resolve_provider_identity(
        new_repository.identity_codec.seal("employee", provider_id)
    ) == old_user
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(distinct internal_user_id) "
                "from platform_control.provider_identities "
                "where subject_kind = 'employee' and internal_user_id in (%s, %s)",
                (old_user, new_user),
            )
            assert cursor.fetchone() == (1,)


@pytest.mark.postgres
def test_concurrent_mismatched_transition_windows_fail_closed_with_one_mapping(
    production_environment,
    tmp_path: Path,
) -> None:
    encryption_keys = {1: b"e" * 32, 2: b"E" * 32, 3: b"f" * 32}
    lookup_keys = {1: b"h" * 32, 2: b"H" * 32, 3: b"i" * 32}

    def rollout_codec(
        node: str,
        active_version: int,
        transition_versions: tuple[int, ...],
    ):
        from app.control_plane.crypto import ProviderIdentityCodec

        return ProviderIdentityCodec(
            _keyring(
                tmp_path,
                f"mismatch-{node}-encryption.json",
                "provider-encryption",
                active_version,
                encryption_keys,
            ),
            _keyring(
                tmp_path,
                f"mismatch-{node}-hmac.json",
                "provider-lookup-hmac",
                active_version,
                {
                    version: lookup_keys[version]
                    for version in transition_versions
                },
                transition_versions=transition_versions,
            ),
        )

    database_url = production_environment["urls"]["platform_control_app"]
    old_repository = ControlRepository(
        database_url,
        identity_codec=rollout_codec("old", 2, (1, 2)),
    )
    new_repository = ControlRepository(
        database_url,
        identity_codec=rollout_codec("new", 3, (2, 3)),
    )
    provider_id = "mismatched-window-provider-id"
    start = threading.Barrier(2)

    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "delete from platform_control.provider_identity_key_policies "
                "where provider = 'dingtalk'"
            )

    def create(repository: ControlRepository, display_name: str):
        protected = repository.identity_codec.seal("employee", provider_id)
        start.wait(timeout=5)
        try:
            return repository.create_internal_user(protected, display_name)
        except IdentityKeyPolicyError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = (
            executor.submit(create, old_repository, "Mismatch Old"),
            executor.submit(create, new_repository, "Mismatch New"),
        )
        outcomes = [future.result(timeout=10) for future in results]

    assert sum(isinstance(outcome, UUID) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, IdentityKeyPolicyError) for outcome in outcomes) == 1
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select lookup_transition_versions from "
                "platform_control.provider_identity_key_policies "
                "where provider = 'dingtalk'"
            )
            assert cursor.fetchone()[0] in ([1, 2], [2, 3])
            cursor.execute(
                "select count(*), count(distinct internal_user_id) "
                "from platform_control.provider_identities identity "
                "join platform_control.internal_users users using "
                "(internal_user_id) where users.display_name like 'Mismatch %'"
            )
            assert cursor.fetchone() == (1, 1)


def test_create_or_resolve_acquires_every_transition_lock_in_version_hmac_order(
    tmp_path: Path,
) -> None:
    lock_calls: list[int] = []

    class Cursor:
        policy_selected = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters=None):
            self.policy_selected = (
                "select lookup_transition_versions" in statement
            )
            if (
                statement == "select pg_advisory_xact_lock(%s)"
                and parameters != (1229998928,)
            ):
                lock_calls.append(parameters[0])
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            if self.policy_selected:
                return {"lookup_transition_versions": [1, 2]}
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    repository = ControlRepository(
        "postgresql://unused@127.0.0.1/agent_platform_control",
        identity_codec=_codec(tmp_path),
        connect=lambda *args, **kwargs: Connection(),
    )
    protected = repository.identity_codec.seal(
        "employee", "ordered-lock-provider-id"
    )
    candidates = sorted(
        repository.identity_codec.lookup_candidates(
            protected.subject_kind,
            repository.identity_codec.unseal(protected),
        ),
        key=lambda candidate: (candidate[0], candidate[1]),
    )

    repository.create_internal_user(protected, "Synthetic User")

    assert lock_calls == [
        _identity_advisory_lock_key(version, lookup_hmac)
        for version, lookup_hmac in candidates
    ]


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
    assert repository.rotate_web_session(issued.cookie_token, 60) is None


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
    user_id = repository.create_internal_user(
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
def test_database_enforces_at_most_one_active_owner_and_allows_zero(
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
                "select count(*) from platform_control.internal_users "
                "where role = 'platform_owner' and status = 'active'"
            )
            assert cursor.fetchone() == (0,)
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
