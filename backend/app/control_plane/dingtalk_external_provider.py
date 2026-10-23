from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn
from urllib.parse import urlencode

import httpx

from .dingtalk import _LOG_SECRETS, _install_provider_log_filter
from .partner_models import PartnerIdentityError, VerifiedProviderSubject

_LOG = logging.getLogger(__name__)
_AUTHORIZATION_URL = "https://oapi.dingtalk.com/connect/qrconnect"
_SNS_URL = "https://oapi.dingtalk.com/sns/getuserinfo_bycode"
_REDIRECT_URIS = frozenset(
    {
        "https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback",
    }
)
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+\Z")
_CALLBACK_PROOF_TTL = timedelta(seconds=60)
_VERIFICATION_LEASE_TTL = timedelta(hours=8)
_MAX_CALLBACK_PROOFS = 1024


def _required_ascii(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or _VISIBLE_ASCII.fullmatch(value) is None
    ):
        raise ValueError
    return value


def _required_nickname(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value != value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _recent_proof(proof: object, *, now: datetime, ttl: timedelta) -> bool:
    if not isinstance(proof, datetime) or proof.tzinfo is None:
        return False
    try:
        if proof.utcoffset() is None:
            return False
        return timedelta(0) <= now - proof < ttl
    except (TypeError, ValueError, OverflowError):
        return False


class DingTalkExternalIdentityProvider:
    """Explicit website SNS identity with a bounded local verification lease.

    ``check_subject`` proves recent OAuth authentication, not live DingTalk
    revocation status. Local subject and grant revocation belongs to the broker
    and access service. No user access or refresh token is retained.
    """

    kind = "dingtalk_external"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        verified_at_for_subject: Callable[[str], datetime | None],
        now: Callable[[], datetime] = _utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            self._client_id = _required_ascii(client_id, maximum=512)
            self._client_secret = _required_ascii(client_secret, maximum=4096)
            if (
                redirect_uri not in _REDIRECT_URIS
                or not callable(verified_at_for_subject)
                or not callable(now)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("dingtalk_external_configuration_invalid") from None
        self._redirect_uri = redirect_uri
        self._verified_at_for_subject = verified_at_for_subject
        self._now = now
        self._subject_prefix = (
            hashlib.sha256(client_id.encode("utf-8")).hexdigest() + ":"
        )
        self._callback_proofs: OrderedDict[str, datetime] = OrderedDict()
        _install_provider_log_filter()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return "DingTalkExternalIdentityProvider(credentials=<redacted>)"

    def _current_time(self) -> datetime:
        try:
            now = self._now()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise ValueError
            return now.astimezone(UTC)
        except Exception:  # noqa: BLE001 - clock failures cross a redacted boundary
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def begin_auth(self, state: str) -> str:
        try:
            _required_ascii(state, maximum=1024)
        except ValueError:
            raise PartnerIdentityError("partner_auth_invalid", 401) from None
        return (
            _AUTHORIZATION_URL
            + "?"
            + urlencode(
                {
                    "appid": self._client_id,
                    "redirect_uri": self._redirect_uri,
                    "response_type": "code",
                    "scope": "snsapi_login",
                    "state": state,
                }
            )
        )

    @staticmethod
    def _callback_code(callback: Mapping[str, str]) -> str:
        # HTTP callers should also reject duplicates before consuming OAuth state.
        # Preserve this boundary when a multidict is passed directly to the adapter.
        try:
            if not isinstance(callback, Mapping):
                raise TypeError
            getlist = getattr(callback, "getlist", None)
            if callable(getlist) and any(len(getlist(key)) != 1 for key in callback):
                raise ValueError
            if set(callback) != {"state", "code"}:
                raise ValueError
            _required_ascii(callback.get("state"), maximum=1024)
            code = _required_ascii(callback.get("code"), maximum=4096)
            return code
        except Exception:  # noqa: BLE001 - callback identity material must not escape
            raise PartnerIdentityError("partner_auth_invalid", 401) from None

    @staticmethod
    def _exchange_failure(
        reason: str, *, http_status: int = -1, provider_errcode: int = -1
    ) -> NoReturn:
        # Call sites supply only fixed enums and bounded integers, never provider text.
        _LOG.warning(
            "dingtalk_external_exchange_failed reason=%s http_status=%d provider_errcode=%d",
            reason,
            http_status,
            provider_errcode,
        )
        raise PartnerIdentityError("partner_identity_unavailable") from None

    async def _exchange(self, code: str) -> dict:
        now = self._current_time()
        timestamp = str(
            (now - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
        )
        if int(timestamp) <= 0:
            self._exchange_failure("invalid_clock")
        # Official personal-login signature contract: HMAC-SHA256(secret, timestamp),
        # then Base64. httpx params performs the single required URL encoding.
        signature = base64.b64encode(
            hmac.new(
                self._client_secret.encode("utf-8"),
                timestamp.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        context = _LOG_SECRETS.set((self._client_secret, code, signature))
        try:
            try:
                # Temporary authorization codes are single use; never retry.
                response = await self._client.post(
                    _SNS_URL,
                    params={
                        "accessKey": self._client_id,
                        "timestamp": timestamp,
                        "signature": signature,
                    },
                    json={"tmp_auth_code": code},
                )
            except Exception:  # noqa: BLE001 - exception URLs may contain signatures
                self._exchange_failure("request_failed")
            if response.status_code != 200:
                self._exchange_failure("http_error", http_status=response.status_code)
            try:
                payload = response.json()
            except Exception:  # noqa: BLE001 - no provider body escapes
                self._exchange_failure("invalid_json", http_status=200)
            if not isinstance(payload, dict):
                self._exchange_failure("invalid_payload", http_status=200)
            errcode = payload.get("errcode")
            if type(errcode) is not int or not -(2**31) <= errcode < 2**31:
                self._exchange_failure("invalid_errcode", http_status=200)
            if errcode != 0:
                self._exchange_failure(
                    "provider_error", http_status=200, provider_errcode=errcode
                )
            if (
                "code" in payload
                or "error" in payload
                or not isinstance(payload.get("user_info"), dict)
            ):
                self._exchange_failure("invalid_payload", http_status=200, provider_errcode=0)
            return payload["user_info"]
        finally:
            _LOG_SECRETS.reset(context)

    def _evict_callback_proofs(self, now: datetime) -> None:
        for subject, proof in tuple(self._callback_proofs.items()):
            if not _recent_proof(proof, now=now, ttl=_CALLBACK_PROOF_TTL):
                del self._callback_proofs[subject]

    async def finish_auth(self, callback: Mapping[str, str]) -> VerifiedProviderSubject:
        code = self._callback_code(callback)
        profile = await self._exchange(code)
        try:
            union_id = _required_ascii(profile.get("unionid"), maximum=512)
            nickname = _required_nickname(profile.get("nick"))
        except ValueError:
            self._exchange_failure("invalid_identity", http_status=200, provider_errcode=0)
        verified_at = self._current_time()
        subject = self._subject_prefix + union_id
        self._evict_callback_proofs(verified_at)
        self._callback_proofs[subject] = verified_at
        self._callback_proofs.move_to_end(subject)
        while len(self._callback_proofs) > _MAX_CALLBACK_PROOFS:
            self._callback_proofs.popitem(last=False)
        return VerifiedProviderSubject(
            provider_kind=self.kind,
            provider_subject=subject,
            verified_at=verified_at,
            display_name=nickname,
        )

    async def check_subject(
        self, provider_subject: str
    ) -> Literal["active", "inactive"]:
        if not isinstance(provider_subject, str) or not provider_subject.startswith(
            self._subject_prefix
        ):
            return "inactive"
        try:
            _required_ascii(provider_subject[len(self._subject_prefix) :], maximum=512)
        except ValueError:
            return "inactive"
        now = self._current_time()
        self._evict_callback_proofs(now)
        if provider_subject in self._callback_proofs:
            return "active"
        try:
            proof = self._verified_at_for_subject(provider_subject)
        except Exception:  # noqa: BLE001 - persistence failures cross a redacted boundary
            raise PartnerIdentityError("partner_identity_unavailable") from None
        return (
            "active"
            if _recent_proof(proof, now=now, ttl=_VERIFICATION_LEASE_TTL)
            else "inactive"
        )

    async def aclose(self) -> None:
        self._callback_proofs.clear()
        await self._client.aclose()
