from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from ipaddress import IPv4Address, IPv6Address, ip_address
import math
import threading
from typing import Callable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from starlette.responses import JSONResponse

from .auth import AuthSecrets, LoginAttempt
from .dsn import validate_control_dsn


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, min(86_400, int(math.ceil(retry_after))))
        super().__init__("request rate limited")


class RateLimitUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("rate limit unavailable")


def rate_limit_response(error: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        {"detail": "request rate limited"},
        status_code=429,
        headers={
            "Retry-After": str(error.retry_after),
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


_SEMAPHORE_LOCK = threading.Lock()
_PROCESS_EXCHANGE_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def _process_semaphore(capacity: int) -> asyncio.Semaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _PROCESS_EXCHANGE_SEMAPHORES.get(capacity)
        if semaphore is None:
            semaphore = asyncio.Semaphore(capacity)
            _PROCESS_EXCHANGE_SEMAPHORES[capacity] = semaphore
        return semaphore


class ControlRateLimiter:
    """Database-atomic abuse controls; all persisted keys are keyed digests."""

    def __init__(
        self,
        *,
        control_database_url: str,
        secrets: AuthSecrets,
        login_starts_per_challenge: int = 5,
        challenge_window_seconds: int = 600,
        active_login_attempts: int = 3,
        edge_login_per_minute: int = 600,
        edge_login_burst: int = 1200,
        edge_callbacks_per_minute: int = 1200,
        oauth_exchange_concurrency: int = 100,
        oauth_exchanges_per_minute: int = 3000,
        authenticated_reads_per_minute: int = 300,
        authenticated_mutations_per_minute: int = 60,
        connect: Callable = psycopg.connect,
    ) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        limits = (
            login_starts_per_challenge,
            challenge_window_seconds,
            active_login_attempts,
            edge_login_per_minute,
            edge_login_burst,
            edge_callbacks_per_minute,
            oauth_exchange_concurrency,
            oauth_exchanges_per_minute,
            authenticated_reads_per_minute,
            authenticated_mutations_per_minute,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in limits
        ):
            raise ValueError("rate limit configuration invalid")
        if (
            login_starts_per_challenge > 5
            or challenge_window_seconds != 600
            or active_login_attempts > 3
            or edge_login_per_minute > 600
            or edge_login_burst < edge_login_per_minute
            or edge_login_burst > 1200
            or edge_callbacks_per_minute > 1200
            or oauth_exchange_concurrency > 100
            or oauth_exchanges_per_minute > 3000
            or authenticated_reads_per_minute > 300
            or authenticated_mutations_per_minute > 60
        ):
            raise ValueError("rate limit configuration exceeds security ceiling")
        self.environment = parsed.environment
        self._database_url = control_database_url
        self._connect = connect
        self.secrets = secrets
        self.login_starts_per_challenge = login_starts_per_challenge
        self.challenge_window_seconds = challenge_window_seconds
        self.active_login_attempts = active_login_attempts
        self.edge_login_per_minute = edge_login_per_minute
        self.edge_login_burst = edge_login_burst
        self.edge_callbacks_per_minute = edge_callbacks_per_minute
        self.oauth_exchange_concurrency = oauth_exchange_concurrency
        self.oauth_exchanges_per_minute = oauth_exchanges_per_minute
        self.authenticated_reads_per_minute = authenticated_reads_per_minute
        self.authenticated_mutations_per_minute = authenticated_mutations_per_minute

    def __repr__(self) -> str:
        return (
            f"ControlRateLimiter(environment={self.environment!r}, "
            "database=<redacted>, secrets=<redacted>)"
        )

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def issue_browser_challenge(self) -> str:
        return self.secrets.issue_browser_challenge()

    def bucket_digest(self, kind: str, value: str) -> bytes:
        return self.secrets.rate_digest(kind, value)

    @staticmethod
    def _canonical_ip(value: str | IPv4Address | IPv6Address) -> str:
        try:
            parsed = (
                value
                if isinstance(value, (IPv4Address, IPv6Address))
                else ip_address(value)
            )
        except ValueError:
            raise RateLimitUnavailable() from None
        if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        return parsed.compressed

    def create_login_attempt(
        self,
        record: LoginAttempt,
        *,
        edge_ip: str | IPv4Address | IPv6Address,
    ) -> UUID:
        if (
            record.browser_challenge_digest is None
            or record.browser_challenge_key_version != self.secrets.key_version
        ):
            raise RateLimitUnavailable()
        edge_key = self.bucket_digest("edge_login", self._canonical_ip(edge_ip))
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.create_rate_limited_web_login_attempt("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        record.attempt_id,
                        record.attempt_kind,
                        record.state_digest,
                        record.state_key_version,
                        record.challenge_digest,
                        record.challenge_key_version,
                        record.verifier_ciphertext,
                        record.return_path,
                        record.environment,
                        300,
                        record.browser_challenge_digest,
                        record.browser_challenge_key_version,
                        edge_key,
                        self.secrets.key_version,
                        self.login_starts_per_challenge,
                        self.challenge_window_seconds,
                        self.active_login_attempts,
                        self.edge_login_per_minute,
                        self.edge_login_burst,
                    ),
                ).fetchone()
            if row is None:
                raise RateLimitUnavailable()
            if not row["allowed"]:
                raise RateLimitExceeded(row["retry_after"])
            return row["attempt_id"]
        except RateLimitExceeded:
            raise
        except (psycopg.Error, KeyError, TypeError, ValueError):
            raise RateLimitUnavailable() from None

    def _consume(self, kind: str, value: str, rate: int) -> None:
        key = self.bucket_digest(kind, value)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.consume_auth_rate_limit(%s,%s,%s,%s)",
                    (kind, key, rate, rate),
                ).fetchone()
            if row is None:
                raise RateLimitUnavailable()
            if not row["allowed"]:
                raise RateLimitExceeded(row["retry_after"])
        except RateLimitExceeded:
            raise
        except (psycopg.Error, KeyError, TypeError, ValueError):
            raise RateLimitUnavailable() from None

    def check_callback(self, edge_ip: str | IPv4Address | IPv6Address) -> None:
        self._consume(
            "edge_callback",
            self._canonical_ip(edge_ip),
            self.edge_callbacks_per_minute,
        )

    def check_authenticated(self, internal_user_id: UUID, *, mutation: bool) -> None:
        if not isinstance(internal_user_id, UUID):
            raise RateLimitUnavailable()
        kind = "authenticated_mutation" if mutation else "authenticated_read"
        rate = (
            self.authenticated_mutations_per_minute
            if mutation
            else self.authenticated_reads_per_minute
        )
        self._consume(kind, str(internal_user_id), rate)

    @asynccontextmanager
    async def provider_exchange(self):
        semaphore = _process_semaphore(self.oauth_exchange_concurrency)
        acquired = False
        try:
            await semaphore.acquire()
            acquired = True
            self._consume(
                "oauth_exchange",
                f"{self.environment}:global",
                self.oauth_exchanges_per_minute,
            )
            yield
        finally:
            if acquired:
                semaphore.release()
