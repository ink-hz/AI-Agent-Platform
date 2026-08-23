from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import inspect

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import psycopg
import pytest
from starlette.datastructures import Headers

from app.execution_relay import worker_auth
from app.execution_relay.worker_auth import (
    WorkerAuthenticationError,
    WorkerIdentity,
    WorkerRequestSigner,
    WorkerRequestVerifier,
)
from test_control_plane_migration import control_database


NOW = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)
BODY_LIMIT = 1_048_576
HEADER_NAMES = {
    "X-Orbbec-Worker-Id",
    "X-Orbbec-Worker-Key-Id",
    "X-Orbbec-Worker-Timestamp",
    "X-Orbbec-Worker-Nonce",
    "X-Orbbec-Worker-Signature",
}


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _assert_authentication_failure(call, *protected_values: str) -> None:
    with pytest.raises(WorkerAuthenticationError) as error:
        call()

    assert str(error.value) == "worker authentication failed"
    for value in protected_values:
        assert value not in str(error.value)


def _raw_headers(
    values: dict[str, str],
    *,
    transform=lambda name: name,
    extra: tuple[tuple[str, str], ...] = (),
) -> Headers:
    return Headers(
        raw=[
            (transform(name).encode("ascii"), value.encode("ascii"))
            for name, value in values.items()
        ]
        + [
            (name.encode("ascii"), value.encode("ascii"))
            for name, value in extra
        ]
    )


@pytest.fixture()
def auth_database(control_database):
    environment = control_database["environments"]["production"]
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    revoked_private_key = Ed25519PrivateKey.generate()
    revoked_public_key = revoked_private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.execution_events")
        connection.execute("delete from platform_control.execution_jobs")
        connection.execute(
            "delete from platform_control.execution_worker_nonces"
        )
        connection.execute("delete from platform_control.execution_worker_keys")
        connection.execute("delete from platform_control.execution_workers")
        connection.execute(
            "insert into platform_control.execution_workers "
            "(worker_id,allowed_agent_ids,status,revoked_at) values "
            "('worker-a',array['hr-bot','fae-bot'],'active',null),"
            "('worker-b',array['hr-bot'],'active',null),"
            "('worker-revoked',array['hr-bot'],'revoked',now())"
        )
        connection.execute(
            "insert into platform_control.execution_worker_keys "
            "(worker_id,key_id,public_key,status,revoked_at) values "
            "('worker-a','worker-v1',%s,'active',null),"
            "('worker-revoked','worker-v1',%s,'revoked',now())",
            (public_key, revoked_public_key),
        )
    yield environment, private_key, revoked_private_key
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute("delete from platform_control.execution_events")
        connection.execute("delete from platform_control.execution_jobs")
        connection.execute(
            "delete from platform_control.execution_worker_nonces"
        )
        connection.execute("delete from platform_control.execution_worker_keys")
        connection.execute("delete from platform_control.execution_workers")


@pytest.fixture()
def signer(auth_database):
    _, private_key, _ = auth_database
    return WorkerRequestSigner("worker-a", "worker-v1", private_key)


@pytest.fixture()
def verifier(auth_database):
    environment, _, _ = auth_database
    return WorkerRequestVerifier(
        environment["urls"]["platform_control_app"]
    )


def test_signer_emits_the_exact_canonical_request_contract(
    auth_database, signer, monkeypatch
) -> None:
    _, private_key, _ = auth_database
    nonce = bytes(range(32))
    monkeypatch.setattr(worker_auth.secrets, "token_bytes", lambda size: nonce)
    body = b'{"agent_id":"hr-bot"}'

    headers = signer.sign(
        "POST", "/api/v1/execution-worker/lease?wait=1", body, now=NOW
    )

    assert set(headers) == HEADER_NAMES
    assert headers["X-Orbbec-Worker-Id"] == "worker-a"
    assert headers["X-Orbbec-Worker-Key-Id"] == "worker-v1"
    assert headers["X-Orbbec-Worker-Timestamp"] == str(int(NOW.timestamp()))
    assert headers["X-Orbbec-Worker-Nonce"] == (
        base64.urlsafe_b64encode(nonce).rstrip(b"=").decode("ascii")
    )
    assert "=" not in headers["X-Orbbec-Worker-Nonce"]
    assert "=" not in headers["X-Orbbec-Worker-Signature"]
    canonical = (
        "orbbec-agent-worker-v1\n"
        "POST\n"
        "/api/v1/execution-worker/lease?wait=1\n"
        f"{int(NOW.timestamp())}\n"
        f"{headers['X-Orbbec-Worker-Nonce']}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    ).encode("utf-8")
    private_key.public_key().verify(
        _b64decode(headers["X-Orbbec-Worker-Signature"]), canonical
    )


def test_signer_never_emits_a_negative_timestamp(signer) -> None:
    before_unix_epoch = datetime(1969, 12, 31, 23, 59, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="worker signing request invalid"):
        signer.sign("POST", "/request", b"", now=before_unix_epoch)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/request"),
        ("POST GET", "/request"),
        ("POST/GET", "/request"),
        ("POST\rGET", "/request"),
        ("POST\nGET", "/request"),
        ("POST\0GET", "/request"),
        ("", "/request"),
        ("POST", "/first\r/second"),
        ("POST", "/first\n/second"),
        ("POST", "/first\0/second"),
    ],
)
def test_signer_rejects_ambiguous_method_or_path(
    signer, method: str, path: str
) -> None:
    with pytest.raises(ValueError, match="worker signing request invalid"):
        signer.sign(method, path, b"", now=NOW)


def test_signer_preserves_valid_method_and_path_text(
    auth_database, signer
) -> None:
    _, private_key, _ = auth_database
    path = "/request/%E4%B8%AD?line=%0A&value=a+b%2Fc"

    headers = signer.sign("M-SEARCH", path, b"", now=NOW)

    canonical = (
        "orbbec-agent-worker-v1\n"
        "M-SEARCH\n"
        f"{path}\n"
        f"{int(NOW.timestamp())}\n"
        f"{headers['X-Orbbec-Worker-Nonce']}\n"
        f"{hashlib.sha256(b'').hexdigest()}"
    ).encode("utf-8")
    private_key.public_key().verify(
        _b64decode(headers["X-Orbbec-Worker-Signature"]), canonical
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/request"),
        ("POST\nGET", "/request"),
        ("POST", "/first\r/second"),
        ("POST", "/first\n/second"),
        ("POST", "/first\0/second"),
    ],
)
def test_verifier_rejects_ambiguous_canonical_fields_before_database_access(
    auth_database, method: str, path: str
) -> None:
    environment, _, _ = auth_database
    database_accessed = False

    def forbidden_connect(*args, **kwargs):
        nonlocal database_accessed
        database_accessed = True
        raise AssertionError("database must not be accessed")

    verifier = WorkerRequestVerifier(
        environment["urls"]["platform_control_app"],
        connect=forbidden_connect,
    )
    headers = {
        "X-Orbbec-Worker-Id": "worker-a",
        "X-Orbbec-Worker-Key-Id": "worker-v1",
        "X-Orbbec-Worker-Timestamp": str(int(NOW.timestamp())),
        "X-Orbbec-Worker-Nonce": base64.urlsafe_b64encode(b"n" * 32)
        .rstrip(b"=")
        .decode("ascii"),
        "X-Orbbec-Worker-Signature": base64.urlsafe_b64encode(b"s" * 64)
        .rstrip(b"=")
        .decode("ascii"),
    }

    _assert_authentication_failure(
        lambda: verifier.verify(method, path, b"", headers, now=NOW)
    )
    assert database_accessed is False


@pytest.mark.postgres
def test_valid_signature_returns_frozen_worker_identity(
    signer, verifier
) -> None:
    body = b'{"lease":true}'
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/lease", body, now=NOW
    )

    identity = verifier.verify(
        "POST",
        "/api/v1/execution-worker/lease",
        body,
        headers,
        now=NOW,
    )

    assert identity == WorkerIdentity(
        worker_id="worker-a",
        key_id="worker-v1",
        allowed_agent_ids=("hr-bot", "fae-bot"),
    )
    with pytest.raises(FrozenInstanceError):
        identity.worker_id = "changed"


@pytest.mark.postgres
@pytest.mark.parametrize(
    "transform",
    [
        str.lower,
        lambda name: "".join(
            character.upper() if index % 2 else character.lower()
            for index, character in enumerate(name)
        ),
    ],
    ids=["lowercase", "mixed-case"],
)
def test_authentication_header_names_are_case_insensitive(
    signer, verifier, transform
) -> None:
    body = b"case-insensitive-headers"
    signed = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )
    headers = _raw_headers(signed, transform=transform)

    assert verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        body,
        headers,
        now=NOW,
    ).worker_id == "worker-a"


@pytest.mark.postgres
def test_duplicate_authentication_header_is_rejected(signer, verifier) -> None:
    body = b"duplicate-auth-header"
    signed = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )
    headers = _raw_headers(
        signed,
        extra=(("x-OrBbEc-WoRkEr-Id", "worker-a"),),
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        )
    )


@pytest.mark.postgres
def test_duplicate_unrelated_headers_are_ignored(signer, verifier) -> None:
    body = b"duplicate-unrelated-headers"
    signed = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )
    headers = _raw_headers(
        signed,
        extra=(
            ("Cookie", "first=1"),
            ("cookie", "second=2"),
            ("Accept", "application/json"),
            ("accept", "application/problem+json"),
        ),
    )

    assert verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        body,
        headers,
        now=NOW,
    ).worker_id == "worker-a"


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/api/v1/execution-worker/lease?wait=1", b"signed-body"),
        ("POST", "/api/v1/execution-worker/lease?wait=2", b"signed-body"),
        ("POST", "/api/v1/execution-worker/lease?wait=1", b"changed-body"),
    ],
)
def test_method_path_or_body_changes_fail(
    signer, verifier, method: str, path: str, body: bytes
) -> None:
    headers = signer.sign(
        "POST",
        "/api/v1/execution-worker/lease?wait=1",
        b"signed-body",
        now=NOW,
    )

    _assert_authentication_failure(
        lambda: verifier.verify(method, path, body, headers, now=NOW),
        headers["X-Orbbec-Worker-Signature"],
        body.decode("ascii"),
    )


@pytest.mark.postgres
@pytest.mark.parametrize("offset", [-60, 60])
def test_timestamp_window_is_inclusive(
    signer, verifier, offset: int
) -> None:
    signed_at = NOW + timedelta(seconds=offset)
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", b"", now=signed_at
    )

    assert verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        b"",
        headers,
        now=NOW,
    ).worker_id == "worker-a"


@pytest.mark.postgres
@pytest.mark.parametrize("offset", [-61, 61])
def test_timestamp_outside_window_fails(
    signer, verifier, offset: int
) -> None:
    headers = signer.sign(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        b"",
        now=NOW + timedelta(seconds=offset),
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            b"",
            headers,
            now=NOW,
        )
    )


@pytest.mark.postgres
@pytest.mark.parametrize("timestamp", ["+1787281200", "01787281200", "-1", "1.0"])
def test_timestamp_must_be_canonical_unsigned_base_ten(
    signer, verifier, timestamp: str
) -> None:
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", b"", now=NOW
    )
    headers["X-Orbbec-Worker-Timestamp"] = timestamp

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            b"",
            headers,
            now=NOW,
        )
    )


@pytest.mark.postgres
def test_reused_nonce_fails_and_is_persisted_once(
    auth_database, signer, verifier
) -> None:
    environment, _, _ = auth_database
    body = b"replay-protected"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )
    verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        body,
        headers,
        now=NOW,
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        )
    )
    with psycopg.connect(environment["admin"]) as connection:
        row = connection.execute(
            "select count(*),min(expires_at) "
            "from platform_control.execution_worker_nonces "
            "where worker_id='worker-a'"
        ).fetchone()
    assert row == (1, NOW + timedelta(seconds=60))


@pytest.mark.postgres
def test_concurrent_nonce_reuse_has_exactly_one_success(
    signer, verifier
) -> None:
    body = b"concurrent-replay"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )

    def verify_once():
        try:
            return verifier.verify(
                "POST",
                "/api/v1/execution-worker/heartbeat",
                body,
                headers,
                now=NOW,
            )
        except WorkerAuthenticationError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: verify_once(), range(2)))

    assert sum(isinstance(result, WorkerIdentity) for result in results) == 1
    assert sum(
        isinstance(result, WorkerAuthenticationError) for result in results
    ) == 1


@pytest.mark.postgres
def test_revoked_worker_fails(auth_database) -> None:
    environment, _, revoked_private_key = auth_database
    signer = WorkerRequestSigner(
        "worker-revoked", "worker-v1", revoked_private_key
    )
    verifier = WorkerRequestVerifier(
        environment["urls"]["platform_control_app"]
    )
    body = b"revoked-worker-request"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        ),
        headers["X-Orbbec-Worker-Signature"],
        body.decode("ascii"),
    )


@pytest.mark.postgres
def test_key_id_mismatch_fails(auth_database, verifier) -> None:
    _, private_key, _ = auth_database
    signer = WorkerRequestSigner("worker-a", "worker-v2", private_key)
    body = b"wrong-key-id"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        ),
        headers["X-Orbbec-Worker-Key-Id"],
        body.decode("ascii"),
    )


@pytest.mark.postgres
def test_active_worker_with_revoked_key_fails(auth_database, verifier) -> None:
    environment, _, _ = auth_database
    revoked_private_key = Ed25519PrivateKey.generate()
    revoked_public_key = revoked_private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_worker_keys "
            "(worker_id,key_id,public_key,status,revoked_at) "
            "values ('worker-a','worker-v2',%s,'revoked',now())",
            (revoked_public_key,),
        )
    signer = WorkerRequestSigner(
        "worker-a", "worker-v2", revoked_private_key
    )
    body = b"revoked-key-request"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        ),
        headers["X-Orbbec-Worker-Signature"],
        body.decode("ascii"),
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("header", "malformed"),
    [
        ("X-Orbbec-Worker-Nonce", "protected+nonce/value=="),
        ("X-Orbbec-Worker-Nonce", "bm9uY2U="),
        ("X-Orbbec-Worker-Signature", "protected+signature/value=="),
        ("X-Orbbec-Worker-Signature", "c2lnbmF0dXJl="),
    ],
)
def test_base64url_values_must_be_canonical_unpadded_and_exact_length(
    signer, verifier, header: str, malformed: str
) -> None:
    body = b"protected-malformed-body"
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )
    headers[header] = malformed

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            body,
            headers,
            now=NOW,
        ),
        malformed,
        body.decode("ascii"),
    )


def test_oversized_body_fails_before_database_access(auth_database) -> None:
    environment, _, _ = auth_database
    database_accessed = False

    def forbidden_connect(*args, **kwargs):
        nonlocal database_accessed
        database_accessed = True
        raise AssertionError("database must not be accessed")

    verifier = WorkerRequestVerifier(
        environment["urls"]["platform_control_app"],
        connect=forbidden_connect,
    )
    body = b"protected-body" * (BODY_LIMIT // len(b"protected-body") + 1)

    _assert_authentication_failure(
        lambda: verifier.verify("POST", "/protected-path", body, {}, now=NOW),
        "protected-body",
    )
    assert len(body) > BODY_LIMIT
    assert database_accessed is False


@pytest.mark.postgres
def test_exactly_one_mebibyte_body_is_accepted(signer, verifier) -> None:
    body = b"x" * BODY_LIMIT
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", body, now=NOW
    )

    assert verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        body,
        headers,
        now=NOW,
    ).worker_id == "worker-a"


@pytest.mark.postgres
def test_invalid_signature_does_not_mutate_nonce_rows(
    auth_database, signer, verifier
) -> None:
    environment, _, _ = auth_database
    expired_nonce = b"e" * 32
    with psycopg.connect(environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.execution_worker_nonces "
            "(worker_id,nonce,expires_at) values ('worker-a',%s,%s)",
            (expired_nonce, NOW - timedelta(seconds=1)),
        )
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", b"signed", now=NOW
    )
    signed_nonce = _b64decode(headers["X-Orbbec-Worker-Nonce"])

    _assert_authentication_failure(
        lambda: verifier.verify(
            "POST",
            "/api/v1/execution-worker/heartbeat",
            b"tampered",
            headers,
            now=NOW,
        )
    )
    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select nonce from platform_control.execution_worker_nonces "
            "where worker_id='worker-a' order by nonce"
        ).fetchall()
    assert rows == [(expired_nonce,)]
    assert (signed_nonce,) not in rows


@pytest.mark.postgres
def test_cleanup_deletes_only_expired_nonces_for_authenticated_worker(
    auth_database, signer, verifier
) -> None:
    environment, _, _ = auth_database
    same_expired = b"e" * 32
    same_live = b"l" * 32
    other_expired = b"o" * 32
    with psycopg.connect(environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into platform_control.execution_worker_nonces "
                "(worker_id,nonce,expires_at) values (%s,%s,%s)",
                [
                    (
                        "worker-a",
                        same_expired,
                        NOW - timedelta(microseconds=1),
                    ),
                    ("worker-a", same_live, NOW + timedelta(seconds=1)),
                    ("worker-b", other_expired, NOW - timedelta(seconds=1)),
                ],
            )
    headers = signer.sign(
        "POST", "/api/v1/execution-worker/heartbeat", b"", now=NOW
    )
    new_nonce = _b64decode(headers["X-Orbbec-Worker-Nonce"])

    verifier.verify(
        "POST",
        "/api/v1/execution-worker/heartbeat",
        b"",
        headers,
        now=NOW,
    )

    with psycopg.connect(environment["admin"]) as connection:
        rows = connection.execute(
            "select worker_id,nonce from "
            "platform_control.execution_worker_nonces order by worker_id,nonce"
        ).fetchall()
    assert set(rows) == {
        ("worker-a", same_live),
        ("worker-a", new_nonce),
        ("worker-b", other_expired),
    }


def test_verifier_uses_locking_heartbeat_without_direct_worker_mutation() -> None:
    source = inspect.getsource(WorkerRequestVerifier.verify).lower()

    assert "touch_execution_worker_v28" in source
    assert source.index("touch_execution_worker_v28") < source.index(
        "execution_worker_keys"
    )
    assert source.index(".verify(") < source.index(
        "delete from platform_control.execution_worker_nonces"
    )
    assert source.index("delete from platform_control.execution_worker_nonces") < (
        source.index("insert into platform_control.execution_worker_nonces")
    )
    assert "update platform_control.execution_workers" not in source
    assert "update platform_control.execution_worker_keys" not in source
