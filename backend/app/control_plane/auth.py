from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import secrets
import time
from typing import Protocol
from urllib.parse import quote, unquote, urlencode, urlsplit
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .models import (
    AuthContext,
    DirectoryFreshness,
    IdentityMode,
    IssuedWebSession,
    ResolvedLoginIdentity,
)
from .models import Role
from .dsn import validate_control_dsn


class AuthenticationError(RuntimeError):
    """Stable authentication failure that carries no provider or token data."""


@dataclass(frozen=True)
class LoginAttempt:
    attempt_id: UUID
    attempt_kind: str
    state_digest: bytes
    state_key_version: int
    challenge_digest: bytes
    challenge_key_version: int
    verifier_ciphertext: bytes
    return_path: str
    environment: str
    expires_at: datetime
    browser_challenge_digest: bytes | None = None
    browser_challenge_key_version: int | None = None


@dataclass(frozen=True)
class StartedLogin:
    attempt_id: UUID
    state: str
    authorization_url: str
    return_path: str


@dataclass(frozen=True)
class CompletedLogin:
    session: IssuedWebSession
    return_path: str

    @property
    def cookie_token(self) -> str:
        return self.session.cookie_token

    @property
    def csrf_token(self) -> str:
        return self.session.csrf_token

    @property
    def idle_expires_at(self) -> datetime:
        return self.session.idle_expires_at

    @property
    def absolute_expires_at(self) -> datetime:
        return self.session.absolute_expires_at


class AuthRepository(Protocol):
    def create_attempt(self, record: LoginAttempt) -> UUID: ...
    def claim_attempt(
        self, *, state_digest: bytes, environment: str, attempt_kind: str
    ) -> LoginAttempt | None: ...
    def fail_attempt(self, attempt_id: UUID, reason: str) -> None: ...
    def issue_session(self, **values): ...
    def authenticate_session(self, **values): ...
    def revoke_session(self, **values): ...


class AuthSecrets:
    """Purpose-separated hashes for opaque browser authentication values."""

    def __init__(self, key: bytes, *, key_version: int) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("authentication HMAC key must be 256 bits")
        if (
            isinstance(key_version, bool)
            or not isinstance(key_version, int)
            or not 1 <= key_version <= 999_999
        ):
            raise ValueError("authentication HMAC key version invalid")
        self._hmac_key = hmac.digest(
            key, b"orbbec-agent-platform:web-auth:hmac:v1", "sha256"
        )
        self._encryption_key = hmac.digest(
            key, b"orbbec-agent-platform:web-auth:aead:v1", "sha256"
        )
        self.key_version = key_version

    def __repr__(self) -> str:
        return f"AuthSecrets(key=<redacted>, key_version={self.key_version!r})"

    @staticmethod
    def random_token() -> str:
        return secrets.token_urlsafe(32)

    def digest(self, purpose: str, token: str) -> bytes:
        if purpose not in {
            "oauth-state", "pkce-verifier", "session", "csrf",
            "browser-challenge",
        }:
            raise ValueError("authentication hash purpose invalid")
        if not isinstance(token, str) or not token:
            raise ValueError("authentication token invalid")
        return hmac.digest(
            self._hmac_key,
            b"orbbec-agent-platform:auth:v1:" + purpose.encode("ascii") + b":" + token.encode("utf-8"),
            "sha256",
        )

    def rate_digest(self, kind: str, value: str) -> bytes:
        if kind not in {
            "edge_login", "edge_callback", "oauth_exchange",
            "authenticated_read", "authenticated_mutation",
        }:
            raise ValueError("rate key purpose invalid")
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("rate key invalid")
        return hmac.digest(
            self._hmac_key,
            b"orbbec-agent-platform:rate:v1:" + kind.encode("ascii")
            + b":" + value.encode("ascii"),
            "sha256",
        )

    def issue_browser_challenge(self) -> str:
        payload = int(time.time()).to_bytes(8, "big") + secrets.token_bytes(24)
        signature = hmac.digest(
            self._hmac_key,
            b"orbbec-agent-platform:browser-challenge:v1:" + payload,
            "sha256",
        )
        return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")

    def browser_challenge_digest(self, token: str, *, ttl_seconds: int = 600) -> bytes:
        try:
            if not isinstance(token, str) or not token or "=" in token:
                raise ValueError
            raw = base64.b64decode(
                token + "=" * (-len(token) % 4),
                altchars=b"-_",
                validate=True,
            )
            if len(raw) != 64:
                raise ValueError
            payload, signature = raw[:32], raw[32:]
            expected = hmac.digest(
                self._hmac_key,
                b"orbbec-agent-platform:browser-challenge:v1:" + payload,
                "sha256",
            )
            issued_at = int.from_bytes(payload[:8], "big")
            age = int(time.time()) - issued_at
            if not hmac.compare_digest(signature, expected) or age < -30 or age > ttl_seconds:
                raise ValueError
            return self.digest("browser-challenge", token)
        except (ValueError, TypeError, UnicodeError):
            raise AuthenticationError("login challenge invalid") from None

    def matches(self, purpose: str, token: str, expected: bytes) -> bool:
        try:
            actual = self.digest(purpose, token)
            return isinstance(expected, bytes) and hmac.compare_digest(actual, expected)
        except (TypeError, ValueError, UnicodeError):
            return False

    def seal_verifier(self, verifier: str) -> bytes:
        if not isinstance(verifier, str) or len(verifier) < 43:
            raise ValueError("PKCE verifier invalid")
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._encryption_key).encrypt(
            nonce,
            verifier.encode("ascii"),
            b"orbbec-agent-platform:pkce-verifier:v1",
        )

    def open_verifier(self, ciphertext: bytes) -> str:
        try:
            if not isinstance(ciphertext, bytes) or len(ciphertext) < 29:
                raise ValueError
            verifier = AESGCM(self._encryption_key).decrypt(
                ciphertext[:12],
                ciphertext[12:],
                b"orbbec-agent-platform:pkce-verifier:v1",
            ).decode("ascii")
            if len(verifier) < 43:
                raise ValueError
            return verifier
        except Exception:
            raise AuthenticationError("login attempt invalid") from None


def validate_return_path(value: str | None, *, route_prefix: str) -> str:
    selected = route_prefix if value is None else value
    if not isinstance(selected, str) or not selected.startswith("/"):
        raise ValueError("return path invalid")
    try:
        decoded = unquote(selected, errors="strict")
    except (UnicodeError, ValueError):
        raise ValueError("return path invalid") from None
    parsed = urlsplit(selected)
    segments = decoded.replace("\\", "/").split("/")
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\\" in selected
        or "%" in selected
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded)
        or any(character in {"\u2028", "\u2029"} for character in decoded)
        or any(segment in {".", ".."} for segment in segments)
        or selected.startswith("//")
    ):
        raise ValueError("return path invalid")
    normalized_prefix = route_prefix if route_prefix.endswith("/") else route_prefix + "/"
    if normalized_prefix != "/" and not (
        selected == normalized_prefix[:-1] or selected.startswith(normalized_prefix)
    ):
        raise ValueError("return path invalid")
    return selected


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def build_qr_authorization_url(
    *, app_key: str, callback_url: str, state: str, code_challenge: str
) -> str:
    query = urlencode(
        {
            "client_id": app_key,
            "response_type": "code",
            "scope": "openid corpid",
            "redirect_uri": callback_url,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "consent",
        },
        quote_via=quote,
    )
    return f"https://login.dingtalk.com/oauth2/auth?{query}"


def cookie_policy(mode: IdentityMode, route_prefix: str) -> dict[str, object]:
    if mode is IdentityMode.PRODUCTION and route_prefix != "/":
        raise ValueError("production Cookie path invalid")
    if mode is IdentityMode.PREVIEW and route_prefix != "/_preview/dingtalk-r1/":
        raise ValueError("preview Cookie path invalid")
    return {"httponly": True, "secure": True, "samesite": "lax", "path": route_prefix}


class DingTalkWebAuth:
    IDLE_SECONDS = 28_800
    ABSOLUTE_SECONDS = 86_400

    def __init__(
        self,
        *,
        repository: AuthRepository,
        secrets: AuthSecrets,
        qr_login: Callable[[str], Awaitable[UUID]],
        in_client_login: Callable[[str], Awaitable[UUID]],
        environment: str,
        route_prefix: str,
        public_base_url: str,
        app_key: str,
        corp_id: str = "",
        state_ttl_seconds: int = 300,
        mode: IdentityMode | None = None,
        cookie_name: str | None = None,
        rate_limiter=None,
        trusted_proxy_networks=(),
        close_callbacks: tuple[Callable[[], Awaitable[None]], ...] = (),
        hard_stale_audit: Callable[[UUID, str, str], None] | None = None,
        warning_after_seconds: int = 28_800,
        hard_stale_after_seconds: int = 86_400,
    ) -> None:
        if environment not in {"production", "preview"}:
            raise ValueError("authentication environment invalid")
        if state_ttl_seconds != 300:
            raise ValueError("OAuth state lifetime must be five minutes")
        self.repository = repository
        self.secrets = secrets
        self.qr_login = qr_login
        self.in_client_login = in_client_login
        self.environment = environment
        self.route_prefix = route_prefix
        self.public_base_url = public_base_url.rstrip("/")
        self.app_key = app_key
        self.corp_id = corp_id
        self.state_ttl_seconds = state_ttl_seconds
        self.mode = mode or (IdentityMode.PREVIEW if environment == "preview" else IdentityMode.PRODUCTION)
        self.cookie_name = cookie_name or (
            "platform_preview_session" if self.mode is IdentityMode.PREVIEW else "__Host-platform_session"
        )
        self.csrf_cookie_name = (
            "platform_preview_csrf"
            if self.mode is IdentityMode.PREVIEW
            else "__Host-platform_csrf"
        )
        self.challenge_cookie_name = (
            "platform_preview_login_challenge"
            if self.mode is IdentityMode.PREVIEW
            else "__Host-platform_login_challenge"
        )
        self.rate_limiter = rate_limiter
        self.trusted_proxy_networks = tuple(trusted_proxy_networks)
        self._close_callbacks = close_callbacks
        self.hard_stale_audit = hard_stale_audit
        self.warning_after_seconds = warning_after_seconds
        self.hard_stale_after_seconds = hard_stale_after_seconds

    async def aclose(self) -> None:
        for callback in self._close_callbacks:
            await callback()

    def _path(self, path: str) -> str:
        return path if self.route_prefix == "/" else self.route_prefix.rstrip("/") + path

    def issue_browser_challenge(self, current: str | None = None) -> str:
        if current:
            try:
                self.secrets.browser_challenge_digest(
                    current, ttl_seconds=self.rate_limiter.challenge_window_seconds
                    if self.rate_limiter is not None else 600,
                )
                return current
            except AuthenticationError:
                pass
        return self.secrets.issue_browser_challenge()

    def start_qr(
        self,
        return_path: str | None,
        browser_challenge: str | None = None,
        edge_ip=None,
    ) -> StartedLogin:
        safe_return = validate_return_path(return_path, route_prefix=self.route_prefix)
        state = self.secrets.random_token()
        verifier = self.secrets.random_token()
        browser_digest = None
        browser_version = None
        if self.rate_limiter is not None:
            browser_digest = self.secrets.browser_challenge_digest(
                browser_challenge or "",
                ttl_seconds=self.rate_limiter.challenge_window_seconds,
            )
            browser_version = self.secrets.key_version
        attempt = LoginAttempt(
            attempt_id=uuid4(),
            attempt_kind="qr",
            state_digest=self.secrets.digest("oauth-state", state),
            state_key_version=self.secrets.key_version,
            challenge_digest=self.secrets.digest("pkce-verifier", verifier),
            challenge_key_version=self.secrets.key_version,
            verifier_ciphertext=self.secrets.seal_verifier(verifier),
            return_path=safe_return,
            environment=self.environment,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.state_ttl_seconds),
            browser_challenge_digest=browser_digest,
            browser_challenge_key_version=browser_version,
        )
        if self.rate_limiter is None:
            self.repository.create_attempt(attempt)
        else:
            self.rate_limiter.create_login_attempt(attempt, edge_ip=edge_ip)
        callback = self.public_base_url + self._path("/api/v1/auth/dingtalk/callback")
        return StartedLogin(
            attempt.attempt_id,
            state,
            build_qr_authorization_url(
                app_key=self.app_key,
                callback_url=callback,
                state=state,
                code_challenge=_pkce_challenge(verifier),
            ),
            safe_return,
        )

    def _claim(self, state: str, attempt_kind: str) -> LoginAttempt:
        try:
            digest = self.secrets.digest("oauth-state", state)
        except (TypeError, ValueError, UnicodeError):
            raise AuthenticationError("login attempt invalid") from None
        attempt = self.repository.claim_attempt(
            state_digest=digest,
            environment=self.environment,
            attempt_kind=attempt_kind,
        )
        if attempt is None:
            raise AuthenticationError("login attempt invalid")
        return attempt

    async def _complete(
        self, attempt: LoginAttempt, code: str, login, *, edge_ip=None
    ) -> CompletedLogin:
        from .rate_limit import RateLimitExceeded, RateLimitUnavailable

        try:
            verifier = self.secrets.open_verifier(attempt.verifier_ciphertext)
            if (
                attempt.challenge_key_version != self.secrets.key_version
                or not self.secrets.matches(
                    "pkce-verifier", verifier, attempt.challenge_digest
                )
            ):
                raise AuthenticationError("login attempt invalid")
            if self.rate_limiter is None:
                resolved_identity = await login(code, verifier)
            else:
                async with self.rate_limiter.provider_exchange():
                    resolved_identity = await login(code, verifier)
        except (RateLimitExceeded, RateLimitUnavailable):
            self.repository.fail_attempt(attempt.attempt_id, "provider_exchange_failed")
            raise
        except Exception:
            self.repository.fail_attempt(attempt.attempt_id, "provider_exchange_failed")
            raise AuthenticationError("login unavailable") from None
        cookie_token = self.secrets.random_token()
        csrf_token = self.secrets.random_token()
        if isinstance(resolved_identity, ResolvedLoginIdentity):
            internal_user_id = resolved_identity.internal_user_id
            hard_stale_read_only = resolved_identity.hard_stale_read_only
        elif isinstance(resolved_identity, UUID):
            internal_user_id = resolved_identity
            hard_stale_read_only = False
        else:
            self.repository.fail_attempt(
                attempt.attempt_id, "provider_exchange_failed"
            )
            raise AuthenticationError("login unavailable")
        result = self.repository.issue_session(
            attempt_id=attempt.attempt_id,
            internal_user_id=internal_user_id,
            token_digest=self.secrets.digest("session", cookie_token),
            token_key_version=self.secrets.key_version,
            csrf_digest=self.secrets.digest("csrf", csrf_token),
            csrf_key_version=self.secrets.key_version,
            idle_seconds=self.IDLE_SECONDS,
            absolute_seconds=self.ABSOLUTE_SECONDS,
            hard_stale_read_only=hard_stale_read_only,
        )
        if result is None:
            raise AuthenticationError("login unavailable")
        session_id, idle_expires_at, absolute_expires_at = result
        if hard_stale_read_only:
            try:
                if self.hard_stale_audit is None:
                    raise AuthenticationError("required audit unavailable")
                self.hard_stale_audit(internal_user_id, "login", "self")
            except Exception:
                try:
                    self.repository.revoke_session(
                        session_id=session_id,
                        reason="hard_stale_audit_failed",
                    )
                except Exception:
                    pass
                raise AuthenticationError("required audit unavailable") from None
        return CompletedLogin(
            IssuedWebSession(
                session_id=session_id,
                cookie_token=cookie_token,
                csrf_token=csrf_token,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
            ),
            attempt.return_path,
        )

    async def complete_qr(self, state: str, code: str, edge_ip=None) -> CompletedLogin:
        if self.rate_limiter is not None:
            self.rate_limiter.check_callback(edge_ip)
        return await self._complete(
            self._claim(state, "qr"), code, self.qr_login, edge_ip=edge_ip
        )

    async def complete_in_client(
        self, code: str, browser_challenge: str | None = None, edge_ip=None
    ) -> CompletedLogin:
        if self.rate_limiter is not None:
            self.rate_limiter.check_callback(edge_ip)
        # In-client auth codes are also serialized through a backend-only random
        # one-time attempt; callers cannot select or reuse a browser flow.
        state = self.secrets.random_token()
        verifier = self.secrets.random_token()
        browser_digest = None
        browser_version = None
        if self.rate_limiter is not None:
            browser_digest = self.secrets.browser_challenge_digest(
                browser_challenge or "",
                ttl_seconds=self.rate_limiter.challenge_window_seconds,
            )
            browser_version = self.secrets.key_version
        attempt = LoginAttempt(
            uuid4(), "in_client", self.secrets.digest("oauth-state", state),
            self.secrets.key_version, self.secrets.digest("pkce-verifier", verifier),
            self.secrets.key_version, self.secrets.seal_verifier(verifier),
            self.route_prefix, self.environment,
            datetime.now(UTC) + timedelta(seconds=self.state_ttl_seconds),
            browser_digest,browser_version,
        )
        if self.rate_limiter is None:
            self.repository.create_attempt(attempt)
        else:
            self.rate_limiter.create_login_attempt(attempt, edge_ip=edge_ip)
        claimed = self._claim(state, "in_client")
        return await self._complete(
            claimed, code, self.in_client_login, edge_ip=edge_ip
        )

    def authenticate(self, cookie_token: str):
        try:
            token_digest = self.secrets.digest("session", cookie_token)
        except (TypeError, ValueError, UnicodeError):
            return None
        return self.repository.authenticate_session(
            token_digest=token_digest,
            token_key_version=self.secrets.key_version,
            idle_seconds=self.IDLE_SECONDS,
        )

    def verify_csrf(self, submitted: str, expected_digest: bytes | str) -> bool:
        if isinstance(expected_digest, bytes):
            return self.secrets.matches("csrf", submitted, expected_digest)
        return isinstance(submitted, str) and isinstance(expected_digest, str) and hmac.compare_digest(submitted, expected_digest)

    def logout(self, context: AuthContext) -> None:
        if not self.repository.revoke_session(session_id=context.session_id, reason="logout"):
            raise AuthenticationError("session unavailable")

    def account_snapshot(self, context: AuthContext) -> dict[str, object]:
        snapshot = self.repository.account_snapshot(context.internal_user_id)
        freshness = self.repository.directory_freshness(
            warning_after_seconds=self.warning_after_seconds,
            hard_stale_after_seconds=self.hard_stale_after_seconds,
        )
        return {
            **snapshot,
            "directory_freshness": freshness.value,
        }


class WebSessionRepository:
    """Narrow app-role facade over migration 015 SECURITY DEFINER functions."""

    def __init__(self, control_database_url: str, *, secrets: AuthSecrets, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(control_database_url, purpose="app")
        self._database_url = control_database_url
        self._connect = connect
        self.environment = parsed.environment
        self.secrets = secrets

    def __repr__(self) -> str:
        return f"WebSessionRepository(environment={self.environment!r}, database=<redacted>, secrets=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url, connect_timeout=3,
            options="-c statement_timeout=10000", row_factory=dict_row,
        )

    def create_attempt(self, record: LoginAttempt) -> UUID:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.create_web_login_attempt(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) as attempt_id",
                    (
                        record.attempt_id,record.attempt_kind,record.state_digest,
                        record.state_key_version,record.challenge_digest,
                        record.challenge_key_version,record.verifier_ciphertext,
                        record.return_path,
                        record.environment,300,
                    ),
                ).fetchone()
            if row is None:
                raise AuthenticationError("login unavailable")
            return row["attempt_id"]
        except AuthenticationError:
            raise
        except psycopg.Error:
            raise AuthenticationError("login unavailable") from None

    def claim_attempt(self, *, state_digest: bytes, environment: str, attempt_kind: str) -> LoginAttempt | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.claim_web_login_attempt(%s,%s,%s,%s)",
                    (state_digest,self.secrets.key_version,environment,attempt_kind),
                ).fetchone()
            if row is None:
                return None
            return LoginAttempt(
                attempt_id=row["attempt_id"],attempt_kind=attempt_kind,
                state_digest=state_digest,state_key_version=self.secrets.key_version,
                challenge_digest=bytes(row["challenge_hash"]),
                challenge_key_version=row["challenge_hash_key_version"],
                verifier_ciphertext=bytes(row["verifier_ciphertext"]),
                return_path=row["return_path"],environment=environment,
                expires_at=row["expires_at"],
            )
        except psycopg.Error:
            raise AuthenticationError("login unavailable") from None

    def fail_attempt(self, attempt_id: UUID, reason: str) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "select platform_control.fail_web_login_attempt(%s,%s)",
                    (attempt_id,reason),
                ).fetchone()
        except psycopg.Error:
            raise AuthenticationError("login unavailable") from None

    def issue_session(
        self, *, attempt_id: UUID, internal_user_id: UUID,
        token_digest: bytes, token_key_version: int, csrf_digest: bytes,
        csrf_key_version: int, idle_seconds: int, absolute_seconds: int,
        hard_stale_read_only: bool = False,
    ):
        try:
            session_id = uuid4()
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.consume_attempt_and_issue_session_v22(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        attempt_id,internal_user_id,session_id,token_digest,
                        token_key_version,csrf_digest,csrf_key_version,
                        idle_seconds,absolute_seconds,
                        hard_stale_read_only,
                    ),
                ).fetchone()
            if row is None:
                return None
            return row["session_id"],row["idle_expires_at"],row["absolute_expires_at"]
        except psycopg.Error:
            raise AuthenticationError("login unavailable") from None

    def authenticate_session(self, *, token_digest: bytes, token_key_version: int, idle_seconds: int):
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control.authenticate_web_session_v22(%s,%s,%s)",
                    (token_digest,token_key_version,idle_seconds),
                ).fetchone()
            if row is None:
                return None
            return (
                AuthContext(
                    row["internal_user_id"],Role(row["role"]),row["session_id"],
                    row["hard_stale_read_only"],
                ),
                bytes(row["csrf_hash"]),
            )
        except (psycopg.Error, ValueError):
            return None

    def revoke_session(self, *, session_id: UUID, reason: str) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select platform_control.revoke_web_session(%s,%s) as revoked",
                    (session_id,reason),
                ).fetchone()
            return bool(row and row["revoked"])
        except psycopg.Error:
            raise AuthenticationError("session unavailable") from None

    def account_snapshot(self, internal_user_id: UUID) -> dict[str, object]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select users.display_name, coalesce(scopes.agent_ids, "
                    "array[]::text[]) as observation_agent_ids from "
                    "platform_control.internal_users users left join lateral "
                    "(select array_agg(grants.agent_id order by grants.agent_id) "
                    "as agent_ids from platform_control.observation_grants grants "
                    "where grants.viewer_internal_user_id=users.internal_user_id "
                    "and grants.revoked_at is null) scopes on true where "
                    "users.internal_user_id=%s and users.status='active'",
                    (internal_user_id,),
                ).fetchone()
            if row is None:
                raise AuthenticationError("account unavailable")
            return {
                "display_name": row["display_name"],
                "observation_agent_ids": list(row["observation_agent_ids"]),
            }
        except AuthenticationError:
            raise
        except psycopg.Error:
            raise AuthenticationError("account unavailable") from None

    def directory_freshness(
        self, *, warning_after_seconds: int, hard_stale_after_seconds: int
    ) -> DirectoryFreshness:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_control."
                    "read_active_directory_status_v20()",
                ).fetchone()
            if row is None or row["last_complete_at"] is None:
                return DirectoryFreshness.HARD_STALE
            age_seconds = (
                row["database_now"] - row["last_complete_at"]
            ).total_seconds()
            if age_seconds < 0 or age_seconds >= hard_stale_after_seconds:
                return DirectoryFreshness.HARD_STALE
            if age_seconds >= warning_after_seconds:
                return DirectoryFreshness.WARNING
            return DirectoryFreshness.FRESH
        except (psycopg.Error, ValueError):
            return DirectoryFreshness.HARD_STALE


class SystemHealthAuditWriter:
    def __init__(self, audit_database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(audit_database_url, purpose="audit")
        self.environment = parsed.environment
        self._database_url = audit_database_url
        self._connect = connect

    def __repr__(self) -> str:
        return f"SystemHealthAuditWriter(environment={self.environment!r}, database=<redacted>)"

    def __call__(self, context: AuthContext) -> None:
        if context.role is not Role.PLATFORM_OWNER:
            raise AuthenticationError("system health audit rejected")
        try:
            event_id = uuid4()
            request_id = uuid4()
            with self._connect(
                self._database_url, connect_timeout=3,
                options="-c statement_timeout=10000", row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select platform_control.append_system_health_read(%s,%s,%s) as event_id",
                    (event_id,context.internal_user_id,request_id),
                ).fetchone()
            if row is None or row["event_id"] != event_id:
                raise AuthenticationError("required audit unavailable")
        except AuthenticationError:
            raise
        except psycopg.Error:
            raise AuthenticationError("required audit unavailable") from None


class HardStaleAccessAuditWriter:
    def __init__(self, audit_database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(audit_database_url, purpose="audit")
        self.environment = parsed.environment
        self._database_url = audit_database_url
        self._connect = connect

    def __call__(self, actor: UUID, access_kind: str, target: str) -> None:
        if access_kind not in {"login", "read"}:
            raise AuthenticationError("required audit unavailable")
        try:
            event_id = uuid4()
            with self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    "select platform_control.append_hard_stale_access_v22("
                    "%s,%s,%s,%s,%s) as event_id",
                    (event_id, actor, access_kind, target, uuid4()),
                ).fetchone()
            if row is None or row["event_id"] != event_id:
                raise AuthenticationError("required audit unavailable")
        except AuthenticationError:
            raise
        except psycopg.Error:
            raise AuthenticationError("required audit unavailable") from None
