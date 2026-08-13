from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import threading
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.control_plane.auth import AuthSecrets, LoginAttempt
from test_control_plane_migration import control_database


@pytest.fixture
def production_environment(control_database):
    return control_database["environments"]["production"]


def _limiter(environment, **overrides):
    from app.control_plane.rate_limit import ControlRateLimiter

    values = {
        "control_database_url": environment["urls"]["platform_control_app"],
        "secrets": AuthSecrets(b"r" * 32, key_version=11),
        "login_starts_per_challenge": 5,
        "challenge_window_seconds": 600,
        "active_login_attempts": 3,
        "edge_login_per_minute": 600,
        "edge_login_burst": 1200,
        "edge_callbacks_per_minute": 1200,
        "oauth_exchange_concurrency": 100,
        "oauth_exchanges_per_minute": 3000,
        "authenticated_reads_per_minute": 300,
        "authenticated_mutations_per_minute": 60,
    }
    values.update(overrides)
    return ControlRateLimiter(**values)


def _attempt(limiter, *, challenge: str, suffix: int = 0) -> LoginAttempt:
    verifier = limiter.secrets.random_token()
    return LoginAttempt(
        attempt_id=uuid4(),
        attempt_kind="qr",
        state_digest=limiter.secrets.digest(
            "oauth-state", limiter.secrets.random_token()
        ),
        state_key_version=limiter.secrets.key_version,
        challenge_digest=limiter.secrets.digest("pkce-verifier", verifier),
        challenge_key_version=limiter.secrets.key_version,
        verifier_ciphertext=limiter.secrets.seal_verifier(verifier),
        return_path="/",
        environment="production",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        browser_challenge_digest=limiter.secrets.digest(
            "browser-challenge", challenge
        ),
        browser_challenge_key_version=limiter.secrets.key_version,
    )


@pytest.mark.postgres
def test_migration_017_is_additive_narrow_versioned_and_revokes_legacy_functions(
    production_environment,
) -> None:
    migration = (
        __import__("pathlib").Path(__file__).parents[1]
        / "control_migrations/017_rate_limit_hardening.sql"
    )
    assert migration.is_file()
    sql = migration.read_text(encoding="utf-8").lower()
    assert "security definer" in sql
    assert "set search_path = pg_catalog, platform_control" in sql
    assert "clock_timestamp()" in sql
    assert "for update skip locked" in sql
    assert "bucket_key_version" in sql
    assert "set lock_timeout" in sql
    assert "delete from platform_control.auth_rate_buckets;" not in sql
    assert "set bucket_key_version=1000000" in sql
    assert "bucket_key_version=1000000" in sql

    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "insert into platform_control.auth_rate_buckets "
                "(bucket_key,bucket_kind,window_started_at) values (%s,'edge_login',now())",
                (b"x" * 32,),
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "update platform_control.auth_rate_buckets set request_count=0"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "select * from platform_control.auth_rate_buckets"
            )

    signatures = (
        "platform_control.create_rate_limited_web_login_attempt_v2(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,bytea,integer,bytea,integer,integer,integer,integer,integer,integer)",
        "platform_control.consume_auth_rate_limit_v2(text,text,bytea,integer,integer,integer)",
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
            "and routine_name in ('create_rate_limited_web_login_attempt_v2',"
            "'consume_auth_rate_limit_v2','maintain_auth_rate_buckets')"
        ).fetchone() == (0,)

        for legacy_signature in (
            "platform_control.create_rate_limited_web_login_attempt(uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,bytea,integer,bytea,integer,integer,integer,integer,integer,integer)",
            "platform_control.consume_auth_rate_limit(text,bytea,integer,integer)",
        ):
            assert connection.execute(
                "select has_function_privilege('platform_control_app',%s,'execute')",
                (legacy_signature,),
            ).fetchone() == (False,)
        maintenance_signature = (
            "platform_control.maintain_auth_rate_buckets(text,integer,integer,integer)"
        )
        assert connection.execute(
            "select has_function_privilege('platform_control_app',%s,'execute')",
            (maintenance_signature,),
        ).fetchone() == (False,)
        assert connection.execute(
            "select has_function_privilege('platform_control_maintenance',%s,'execute')",
            (maintenance_signature,),
        ).fetchone() == (True,)
        assert connection.execute(
            "select has_function_privilege('platform_control_maintenance_preview',%s,'execute')",
            (maintenance_signature,),
        ).fetchone() == (False,)

        columns = connection.execute(
            "select column_name,is_nullable from information_schema.columns "
            "where table_schema='platform_control' and table_name='auth_rate_buckets'"
        ).fetchall()
        assert ("bucket_key_version", "NO") in columns
        primary = connection.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid='platform_control.auth_rate_buckets'::regclass "
            "and contype='p'"
        ).fetchone()[0]
        assert "bucket_key_version" in primary


@pytest.mark.postgres
def test_challenge_rejection_does_not_consume_or_touch_edge_bucket(
    production_environment,
) -> None:
    from app.control_plane.rate_limit import RateLimitExceeded

    limiter = _limiter(production_environment)
    challenge = limiter.issue_browser_challenge()
    edge_ip = "203.0.113.146"
    limiter.create_login_attempt(
        _attempt(limiter, challenge=challenge), edge_ip=edge_ip
    )
    edge_key = limiter.bucket_digest("edge_login", edge_ip)
    with psycopg.connect(production_environment["admin"]) as connection:
        before = connection.execute(
            "select request_count,token_balance,updated_at from "
            "platform_control.auth_rate_buckets where bucket_kind='edge_login' "
            "and bucket_key=%s and bucket_key_version=%s",
            (edge_key, limiter.secrets.key_version),
        ).fetchone()

    with pytest.raises(RateLimitExceeded):
        limiter.create_login_attempt(
            _attempt(limiter, challenge=challenge), edge_ip=edge_ip
        )

    with psycopg.connect(production_environment["admin"]) as connection:
        after = connection.execute(
            "select request_count,token_balance,updated_at from "
            "platform_control.auth_rate_buckets where bucket_kind='edge_login' "
            "and bucket_key=%s and bucket_key_version=%s",
            (edge_key, limiter.secrets.key_version),
        ).fetchone()
    assert after == before


@pytest.mark.postgres
def test_login_start_is_atomic_challenge_first_and_three_active_maximum(
    production_environment,
) -> None:
    from app.control_plane.rate_limit import RateLimitExceeded

    limiter = _limiter(production_environment)
    challenge = limiter.issue_browser_challenge()
    barrier = threading.Barrier(8)
    outcomes: list[str] = []

    def start(index: int) -> None:
        barrier.wait()
        try:
            limiter.create_login_attempt(
                _attempt(limiter, challenge=challenge, suffix=index),
                edge_ip="203.0.113.44",
            )
            outcomes.append("accepted")
        except RateLimitExceeded as error:
            assert 1 <= error.retry_after <= 600
            outcomes.append("limited")

    threads = [threading.Thread(target=start, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("accepted") <= 3
    assert outcomes.count("limited") >= 5
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.login_attempts "
            "where browser_challenge_hash=%s and consumed_at is null",
            (limiter.secrets.digest("browser-challenge", challenge),),
        ).fetchone()[0] == outcomes.count("accepted")


@pytest.mark.postgres
def test_challenge_backoff_and_five_per_ten_minutes_use_database_time(
    production_environment,
) -> None:
    from app.control_plane.rate_limit import RateLimitExceeded

    limiter = _limiter(production_environment)
    challenge = limiter.issue_browser_challenge()
    limiter.create_login_attempt(
        _attempt(limiter, challenge=challenge), edge_ip="203.0.113.45"
    )
    with pytest.raises(RateLimitExceeded) as limited:
        limiter.create_login_attempt(
            _attempt(limiter, challenge=challenge), edge_ip="203.0.113.45"
        )
    assert limited.value.retry_after >= 1

    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "update platform_control.login_attempts set created_at=created_at-interval '1 minute', "
            "consumed_at=now() where browser_challenge_hash=%s",
            (limiter.secrets.digest("browser-challenge", challenge),),
        )
    for index in range(4):
        limiter.create_login_attempt(
            _attempt(limiter, challenge=challenge, suffix=index),
            edge_ip="203.0.113.45",
        )
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "update platform_control.login_attempts set created_at=created_at-interval '1 minute', "
                "consumed_at=coalesce(consumed_at,now()) "
                "where browser_challenge_hash=%s",
                (limiter.secrets.digest("browser-challenge", challenge),),
            )
    with pytest.raises(RateLimitExceeded) as exhausted:
        limiter.create_login_attempt(
            _attempt(limiter, challenge=challenge), edge_ip="203.0.113.45"
        )
    assert 1 <= exhausted.value.retry_after <= 600


@pytest.mark.postgres
def test_authenticated_users_are_isolated_and_nat_is_only_coarse_edge_key(
    production_environment,
) -> None:
    from app.control_plane.rate_limit import RateLimitExceeded

    limiter = _limiter(
        production_environment,
        authenticated_reads_per_minute=2,
        authenticated_mutations_per_minute=1,
    )
    first = uuid4()
    second = uuid4()
    limiter.check_authenticated(first, mutation=False)
    limiter.check_authenticated(first, mutation=False)
    with pytest.raises(RateLimitExceeded):
        limiter.check_authenticated(first, mutation=False)
    limiter.check_authenticated(second, mutation=False)

    limiter.check_authenticated(first, mutation=True)
    with pytest.raises(RateLimitExceeded):
        limiter.check_authenticated(first, mutation=True)
    limiter.check_authenticated(second, mutation=True)

    # Sharing one corporate NAT does not merge either user's authenticated key.
    assert limiter.bucket_digest("authenticated_read", str(first)) != limiter.bucket_digest(
        "edge_login", "198.51.100.20"
    )


@pytest.mark.postgres
def test_two_limiter_instances_share_one_atomic_edge_bucket_and_bound_growth(
    production_environment,
) -> None:
    from app.control_plane.rate_limit import RateLimitExceeded

    first = _limiter(
        production_environment,
        edge_login_per_minute=1,
        edge_login_burst=1,
    )
    second = _limiter(
        production_environment,
        edge_login_per_minute=1,
        edge_login_burst=1,
    )
    with psycopg.connect(production_environment["admin"]) as connection:
        attempts_before = connection.execute(
            "select count(*) from platform_control.login_attempts"
        ).fetchone()[0]
    barrier = threading.Barrier(20)
    outcomes: list[str] = []

    def start(index: int) -> None:
        limiter = first if index % 2 else second
        challenge = limiter.issue_browser_challenge()
        barrier.wait()
        try:
            limiter.create_login_attempt(
                _attempt(limiter, challenge=challenge),
                edge_ip="198.51.100.88",
            )
            outcomes.append("accepted")
        except RateLimitExceeded:
            outcomes.append("limited")

    threads = [threading.Thread(target=start, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("accepted") == 1
    assert outcomes.count("limited") == 19
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.login_attempts"
        ).fetchone() == (attempts_before + 1,)
        assert connection.execute(
            "select count(*) from platform_control.auth_rate_buckets "
            "where bucket_kind='edge_login' and bucket_key=%s",
            (first.bucket_digest("edge_login", "198.51.100.88"),),
        ).fetchone() == (1,)


@pytest.mark.postgres
def test_request_decision_never_cleans_stale_rows_and_maintenance_is_bounded(
    production_environment,
) -> None:
    limiter = _limiter(production_environment)
    with psycopg.connect(production_environment["admin"]) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                "insert into platform_control.auth_rate_buckets "
                "(bucket_key,bucket_key_version,bucket_kind,window_started_at,request_count,token_balance,updated_at) "
                "values (%s,1,'authenticated_read',now()-interval '2 days',0,0,now()-interval '2 days')",
                [((index + 1_000).to_bytes(32, "big"),) for index in range(250)],
            )

    limiter.check_authenticated(uuid4(), mutation=False)

    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.auth_rate_buckets "
            "where updated_at < now()-interval '1 day'"
        ).fetchone() == (250,)
        assert connection.execute(
            "select indexdef from pg_indexes where schemaname='platform_control' "
            "and indexname='auth_rate_buckets_cleanup'"
        ).fetchone()[0].endswith("USING btree (updated_at)")

    maintenance_url = production_environment["urls"]["platform_control_maintenance"]
    with psycopg.connect(maintenance_url) as connection:
        deleted = connection.execute(
            "select platform_control.maintain_auth_rate_buckets(%s,%s,%s,%s)",
            ("production", limiter.secrets.key_version, 86_400, 100),
        ).fetchone()[0]
    assert deleted == 100
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.auth_rate_buckets "
            "where updated_at < now()-interval '1 day'"
        ).fetchone() == (150,)


@pytest.mark.postgres
def test_bucket_key_versions_are_isolated_and_exact_ceilings_hold(
    production_environment,
) -> None:
    app_url = production_environment["urls"]["platform_control_app"]
    key = b"v" * 32
    with psycopg.connect(app_url) as connection:
        first = connection.execute(
            "select * from platform_control.consume_auth_rate_limit_v2(%s,%s,%s,%s,%s,%s)",
            ("production", "edge_callback", key, 41, 1, 1),
        ).fetchone()
        second = connection.execute(
            "select * from platform_control.consume_auth_rate_limit_v2(%s,%s,%s,%s,%s,%s)",
            ("production", "edge_callback", key, 42, 1, 1),
        ).fetchone()
    assert first == (True, 0)
    assert second == (True, 0)

    callback_key = b"c" * 32
    exchange_key = b"e" * 32
    with psycopg.connect(app_url) as connection:
        callback = connection.execute(
            "select decision.allowed,count(*) from generate_series(1,1201) series "
            "cross join lateral platform_control.consume_auth_rate_limit_v2("
            "'production','edge_callback',%s,43,1200+series*0,1200) decision "
            "group by decision.allowed order by decision.allowed",
            (callback_key,),
        ).fetchall()
        exchange = connection.execute(
            "select decision.allowed,count(*) from generate_series(1,3001) series "
            "cross join lateral platform_control.consume_auth_rate_limit_v2("
            "'production','oauth_exchange',%s,43,3000+series*0,3000) decision "
            "group by decision.allowed order by decision.allowed",
            (exchange_key,),
        ).fetchall()
    assert callback == [(False, 1), (True, 1200)]
    assert exchange == [(False, 1), (True, 3000)]


@pytest.mark.postgres
def test_legacy_unversioned_bucket_is_adopted_without_reset(
    production_environment,
) -> None:
    key = b"l" * 32
    with psycopg.connect(production_environment["admin"]) as connection:
        connection.execute(
            "insert into platform_control.auth_rate_buckets "
            "(bucket_key,bucket_key_version,bucket_kind,window_started_at,"
            "request_count,token_balance,updated_at) values "
            "(%s,1000000,'edge_callback',to_timestamp("
            "floor(extract(epoch from clock_timestamp())/60)*60),1,0,"
            "clock_timestamp())",
            (key,),
        )
    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(app_url) as connection:
        decision = connection.execute(
            "select * from platform_control.consume_auth_rate_limit_v2("
            "%s,%s,%s,%s,%s,%s)",
            ("production", "edge_callback", key, 77, 1, 1),
        ).fetchone()
    assert decision[0] is False
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select bucket_key_version,request_count from "
            "platform_control.auth_rate_buckets where bucket_key=%s",
            (key,),
        ).fetchone() == (77, 1)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "arguments",
    [
        (None, "edge_callback", b"x" * 32, 1, 1, 1),
        ("production", None, b"x" * 32, 1, 1, 1),
        ("production", "edge_callback", None, 1, 1, 1),
        ("production", "edge_callback", b"x" * 31, 1, 1, 1),
        ("production", "edge_callback", b"x" * 32, None, 1, 1),
        ("production", "edge_callback", b"x" * 32, 0, 1, 1),
        ("production", "edge_callback", b"x" * 32, 2_147_483_647, 1, 1),
        ("production", "edge_callback", b"x" * 32, 1, None, 1),
        ("production", "edge_callback", b"x" * 32, 1, 0, 1),
        ("production", "edge_callback", b"x" * 32, 1, 1201, 1201),
        ("preview", "edge_callback", b"x" * 32, 1, 1, 1),
    ],
)
def test_security_definer_rejects_invalid_inputs_without_mutation(
    production_environment, arguments
) -> None:
    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(production_environment["admin"]) as connection:
        before = connection.execute(
            "select count(*) from platform_control.auth_rate_buckets"
        ).fetchone()
    with psycopg.connect(app_url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "select * from platform_control.consume_auth_rate_limit_v2(%s,%s,%s,%s,%s,%s)",
                arguments,
            )
    with psycopg.connect(production_environment["admin"]) as connection:
        after = connection.execute(
            "select count(*) from platform_control.auth_rate_buckets"
        ).fetchone()
    assert after == before


@pytest.mark.postgres
def test_login_security_definer_rejects_every_null_parameter_without_mutation(
    production_environment,
) -> None:
    signature = (
        "select * from platform_control.create_rate_limited_web_login_attempt_v2("
        + ",".join(["%s"] * 19)
        + ")"
    )
    baseline = [
        uuid4(), "qr", b"s" * 32, 7, b"p" * 32, 7, b"v" * 40, "/",
        "production", 300, b"b" * 32, 7, b"e" * 32, 7, 5, 600, 3, 600, 1200,
    ]
    app_url = production_environment["urls"]["platform_control_app"]
    with psycopg.connect(production_environment["admin"]) as connection:
        before = connection.execute(
            "select (select count(*) from platform_control.login_attempts),"
            "(select count(*) from platform_control.auth_rate_buckets)"
        ).fetchone()
    for index in range(len(baseline)):
        invalid = baseline.copy()
        invalid[index] = None
        with psycopg.connect(app_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(signature, invalid)
    with psycopg.connect(production_environment["admin"]) as connection:
        after = connection.execute(
            "select (select count(*) from platform_control.login_attempts),"
            "(select count(*) from platform_control.auth_rate_buckets)"
        ).fetchone()
    assert after == before


@pytest.mark.postgres
def test_rate_bucket_maintenance_skips_locked_rows_without_convoy(
    production_environment,
) -> None:
    for index in range(101):
        with psycopg.connect(production_environment["admin"]) as connection:
            connection.execute(
                "insert into platform_control.auth_rate_buckets "
                "(bucket_key,bucket_key_version,bucket_kind,window_started_at,updated_at) "
                "values (%s,9,'authenticated_read',now()-interval '2 days',"
                "now()-interval '2 days')",
                [(index + 50_000).to_bytes(32, "big")],
            )
    maintenance_url = production_environment["urls"]["platform_control_maintenance"]
    with psycopg.connect(production_environment["admin"]) as locker:
        locker.execute(
            "select 1 from platform_control.auth_rate_buckets "
            "where bucket_key=%s for update",
            [(50_000).to_bytes(32, "big")],
        )
        with psycopg.connect(maintenance_url) as maintenance:
            deleted = maintenance.execute(
                "select platform_control.maintain_auth_rate_buckets(%s,%s,%s,%s)",
                ("production", 1, 86_400, 100),
            ).fetchone()[0]
    assert deleted == 100
    with psycopg.connect(production_environment["admin"]) as connection:
        assert connection.execute(
            "select count(*) from platform_control.auth_rate_buckets "
            "where bucket_key_version=9"
        ).fetchone() == (1,)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_provider_breaker_runs_more_than_100_tasks_at_exact_capacity_and_releases(
    production_environment,
) -> None:
    limiter = _limiter(production_environment)
    active = 0
    maximum = 0
    lock = asyncio.Lock()
    all_slots_filled = asyncio.Event()
    release = asyncio.Event()

    async def worker(index: int) -> None:
        nonlocal active, maximum
        async with limiter.provider_exchange():
            async with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 100:
                    all_slots_filled.set()
            try:
                await all_slots_filled.wait()
                if index == 0:
                    raise RuntimeError("synthetic")
                await release.wait()
            finally:
                async with lock:
                    active -= 1

    tasks = [asyncio.create_task(worker(index)) for index in range(150)]
    await asyncio.wait_for(all_slots_filled.wait(), timeout=10)
    assert maximum == 100
    tasks[1].cancel()
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert maximum == 100
    assert any(isinstance(result, RuntimeError) for result in results)

    async with limiter.provider_exchange():
        pass


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_provider_semaphore_is_shared_across_limiter_instances_in_one_loop(
    production_environment,
) -> None:
    first = _limiter(production_environment, oauth_exchange_concurrency=1)
    second = _limiter(production_environment, oauth_exchange_concurrency=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_first() -> None:
        async with first.provider_exchange():
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_first())
    await entered.wait()
    second_entered = asyncio.Event()

    async def enter_second() -> None:
        async with second.provider_exchange():
            second_entered.set()

    second_task = asyncio.create_task(enter_second())
    await asyncio.sleep(0)
    assert not second_entered.is_set()
    release.set()
    await task
    await asyncio.wait_for(second_task, timeout=1)
    assert second_entered.is_set()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_two_limiter_instances_share_exact_100_provider_slots(
    production_environment,
) -> None:
    limiters = [_limiter(production_environment), _limiter(production_environment)]
    active = 0
    maximum = 0
    lock = asyncio.Lock()
    filled = asyncio.Event()
    release = asyncio.Event()

    async def worker(index: int) -> None:
        nonlocal active, maximum
        async with limiters[index % 2].provider_exchange():
            async with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 100:
                    filled.set()
            await release.wait()
            async with lock:
                active -= 1

    tasks = [asyncio.create_task(worker(index)) for index in range(150)]
    await asyncio.wait_for(filled.wait(), timeout=10)
    assert maximum == 100
    release.set()
    await asyncio.gather(*tasks)
    assert maximum == 100


def test_provider_semaphore_can_be_recreated_in_a_new_event_loop() -> None:
    from app.control_plane.rate_limit import _process_semaphore

    async def identity():
        return _process_semaphore(100)

    first = asyncio.run(identity())
    second = asyncio.run(identity())
    assert first is not second


def test_database_failure_is_fail_closed_without_raw_key_in_error() -> None:
    from app.control_plane.rate_limit import ControlRateLimiter, RateLimitUnavailable

    def broken(*args, **kwargs):
        raise psycopg.OperationalError("dsn/raw-user/raw-token")

    limiter = ControlRateLimiter(
        control_database_url="postgresql://platform_control_app@127.0.0.1/agent_platform_control",
        secrets=AuthSecrets(b"z" * 32, key_version=1),
        connect=broken,
    )
    with pytest.raises(RateLimitUnavailable) as failure:
        limiter.check_authenticated(uuid4(), mutation=False)
    assert str(failure.value) == "rate limit unavailable"


def test_rate_limit_response_is_generic_integer_retry_after() -> None:
    from app.control_plane.rate_limit import RateLimitExceeded, rate_limit_response

    response = rate_limit_response(RateLimitExceeded(7))
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.body == b'{"detail":"request rate limited"}'
    assert b"203.0.113" not in response.body


def test_auth_rate_key_version_policy_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="key version"):
        AuthSecrets(b"k" * 32, key_version=0)
    with pytest.raises(ValueError, match="key version"):
        AuthSecrets(b"k" * 32, key_version=1_000_000)


def test_rate_bucket_key_is_independent_of_identity_lookup_rotation() -> None:
    from app.control_plane.rate_limit import ControlRateLimiter

    first = ControlRateLimiter(
        control_database_url=(
            "postgresql://platform_control_app@127.0.0.1/agent_platform_control"
        ),
        secrets=AuthSecrets(b"a" * 32, key_version=1),
        rate_secrets=AuthSecrets(b"r" * 32, key_version=23),
    )
    rotated_identity = ControlRateLimiter(
        control_database_url=(
            "postgresql://platform_control_app@127.0.0.1/agent_platform_control"
        ),
        secrets=AuthSecrets(b"b" * 32, key_version=2),
        rate_secrets=AuthSecrets(b"r" * 32, key_version=23),
    )

    assert first.bucket_digest("edge_callback", "203.0.113.50") == (
        rotated_identity.bucket_digest("edge_callback", "203.0.113.50")
    )
    assert first.rate_secrets.key_version == rotated_identity.rate_secrets.key_version


def test_browser_challenge_is_signed_expiring_and_not_client_selectable(
    monkeypatch,
) -> None:
    from app.control_plane.auth import AuthenticationError

    secrets = AuthSecrets(b"b" * 32, key_version=2)
    monkeypatch.setattr("app.control_plane.auth.time.time", lambda: 1_000)
    challenge = secrets.issue_browser_challenge()
    digest = secrets.browser_challenge_digest(challenge, ttl_seconds=600)
    assert digest == secrets.digest("browser-challenge", challenge)

    replacement = ("A" if challenge[0] != "A" else "B") + challenge[1:]
    with pytest.raises(AuthenticationError, match="challenge invalid"):
        secrets.browser_challenge_digest(replacement, ttl_seconds=600)
    monkeypatch.setattr("app.control_plane.auth.time.time", lambda: 1_601)
    with pytest.raises(AuthenticationError, match="challenge invalid"):
        secrets.browser_challenge_digest(challenge, ttl_seconds=600)


class _RouteLimiter:
    def __init__(self) -> None:
        self.authenticated: list[tuple[object, bool]] = []

    def check_authenticated(self, user_id, *, mutation: bool) -> None:
        self.authenticated.append((user_id, mutation))


@pytest.mark.asyncio
async def test_unknown_callback_is_edge_limited_before_state_and_provider_exchange() -> None:
    from contextlib import asynccontextmanager
    from app.control_plane.auth import AuthenticationError, DingTalkWebAuth
    from test_web_session_security import Provider, SessionRepository

    class Limiter:
        challenge_window_seconds = 600

        def __init__(self) -> None:
            self.callbacks = 0
            self.exchanges = 0

        def check_callback(self, edge_ip) -> None:
            self.callbacks += 1

        @asynccontextmanager
        async def provider_exchange(self):
            self.exchanges += 1
            yield

    limiter = Limiter()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=SessionRepository(),
        secrets=AuthSecrets(b"s" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        rate_limiter=limiter,
    )

    with pytest.raises(AuthenticationError, match="attempt invalid"):
        await auth.complete_qr("unknown", "secret-code", "203.0.113.10")
    assert limiter.callbacks == 1
    assert limiter.exchanges == 0
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_callback_edge_rejection_does_not_burn_a_valid_state() -> None:
    from contextlib import asynccontextmanager
    from app.control_plane.auth import DingTalkWebAuth
    from app.control_plane.rate_limit import RateLimitExceeded
    from test_web_session_security import Provider, SessionRepository

    repository = SessionRepository()

    class Limiter:
        challenge_window_seconds = 600

        def __init__(self) -> None:
            self.reject = True

        def create_login_attempt(self, record, *, edge_ip):
            return repository.create_attempt(record)

        def check_callback(self, edge_ip) -> None:
            if self.reject:
                raise RateLimitExceeded(1)

        @asynccontextmanager
        async def provider_exchange(self):
            yield

    limiter = Limiter()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"t" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        rate_limiter=limiter,
    )
    challenge = auth.issue_browser_challenge()
    started = auth.start_qr("/", challenge, "203.0.113.11")

    with pytest.raises(RateLimitExceeded):
        await auth.complete_qr(started.state, "secret-code", "203.0.113.11")
    assert repository.claims == 0
    assert provider.calls == 0

    limiter.reject = False
    await auth.complete_qr(started.state, "secret-code", "203.0.113.11")
    assert repository.claims == 1
    assert provider.calls == 1


def test_real_callback_route_returns_retry_after_without_burning_state(
    tmp_path, monkeypatch
) -> None:
    from contextlib import asynccontextmanager
    from ipaddress import ip_network
    from app.control_plane.auth import DingTalkWebAuth
    from app.control_plane.rate_limit import RateLimitExceeded
    from test_dingtalk_auth_api import _app
    from test_web_session_security import Provider, SessionRepository

    repository = SessionRepository()

    class Limiter:
        challenge_window_seconds = 600

        def __init__(self) -> None:
            self.reject = True

        def create_login_attempt(self, record, *, edge_ip):
            return repository.create_attempt(record)

        def check_callback(self, edge_ip) -> None:
            if self.reject:
                raise RateLimitExceeded(7)

        @asynccontextmanager
        async def provider_exchange(self):
            yield

    limiter = Limiter()
    provider = Provider()
    auth = DingTalkWebAuth(
        repository=repository,
        secrets=AuthSecrets(b"u" * 32, key_version=1),
        qr_login=provider.complete,
        in_client_login=provider.complete,
        environment="production",
        route_prefix="/",
        public_base_url="https://agent.example.test",
        app_key="test-app",
        rate_limiter=limiter,
        trusted_proxy_networks=(ip_network("127.0.0.1/32"),),
    )
    challenge = auth.issue_browser_challenge()
    started = auth.start_qr("/", challenge, "203.0.113.12")
    client = TestClient(
        _app(tmp_path, monkeypatch, auth),
        base_url="https://agent.example.test",
        client=("127.0.0.1", 44001),
    )
    headers = {
        "X-Real-IP": "203.0.113.12",
        "X-Forwarded-For": "203.0.113.12",
        "X-Forwarded-Proto": "https",
    }
    first = client.get(
        f"/api/v1/auth/dingtalk/callback?state={started.state}&code=secret-code",
        headers=headers,
        follow_redirects=False,
    )
    assert first.status_code == 429
    assert first.headers["Retry-After"] == "7"
    assert repository.claims == 0
    assert provider.calls == 0

    limiter.reject = False
    second = client.get(
        f"/api/v1/auth/dingtalk/callback?state={started.state}&code=secret-code",
        headers=headers,
        follow_redirects=False,
    )
    assert second.status_code == 302
    assert repository.claims == 1
    assert provider.calls == 1


def test_task6_routes_and_middleware_receive_edge_and_enforce_user_limits(
    tmp_path, monkeypatch
) -> None:
    from ipaddress import ip_network
    from test_dingtalk_auth_api import FakeAuth, _app

    class IntegratedAuth(FakeAuth):
        def __init__(self) -> None:
            super().__init__()
            self.rate_limiter = _RouteLimiter()
            self.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
            self.challenge_cookie_name = "__Host-platform_login_challenge"
            self.edges: list[str] = []

        def issue_browser_challenge(self, current=None):
            return current or "c" * 43

        def start_qr(self, return_path, browser_challenge, edge_ip):
            self.edges.append(str(edge_ip))
            return super().start_qr(return_path)

    auth = IntegratedAuth()
    client = TestClient(
        _app(tmp_path, monkeypatch, auth),
        base_url="https://agent.example.test",
        client=("127.0.0.1", 44001),
    )
    proxy = {
        "X-Real-IP": "203.0.113.90",
        "X-Forwarded-For": "203.0.113.90",
        "X-Forwarded-Proto": "https",
    }
    login = client.get("/login", headers=proxy)
    assert login.status_code == 200
    assert "__Host-platform_login_challenge=" in login.headers["set-cookie"]
    started = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/"},
        headers={**proxy, "Origin": "https://agent.example.test"},
    )
    assert started.status_code == 200
    assert auth.edges == ["203.0.113.90"]

    cookies = {
        auth.cookie_name: "valid-cookie",
        auth.csrf_cookie_name: auth.csrf,
    }
    assert client.get("/api/v1/account", headers=proxy, cookies=cookies).status_code == 200
    assert client.post(
        "/api/v1/auth/logout",
        headers={
            **proxy,
            "Origin": auth.public_base_url,
            "X-CSRF-Token": auth.csrf,
        },
        cookies=cookies,
    ).status_code == 204
    assert auth.rate_limiter.authenticated == [
        (auth.context.internal_user_id, False),
        (auth.context.internal_user_id, True),
    ]


def test_untrusted_spoofed_scheme_cannot_satisfy_origin_or_change_identity(
    tmp_path, monkeypatch
) -> None:
    from ipaddress import ip_network
    from test_dingtalk_auth_api import FakeAuth, _app

    auth = FakeAuth()
    auth.rate_limiter = _RouteLimiter()
    auth.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
    auth.challenge_cookie_name = "__Host-platform_login_challenge"
    client = TestClient(
        _app(tmp_path, monkeypatch, auth),
        base_url="http://agent.example.test",
        client=("198.51.100.70", 44001),
    )
    cookies = {auth.cookie_name: "valid-cookie"}

    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Origin": auth.public_base_url,
            "X-CSRF-Token": auth.csrf,
            "X-Real-IP": "127.0.0.1, bad",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Proto": "https",
            "Forwarded": "for=_hidden;proto=https",
        },
        cookies=cookies,
    )

    assert response.status_code == 403
    assert auth.context.internal_user_id not in {
        "198.51.100.70",
        "127.0.0.1",
    }
