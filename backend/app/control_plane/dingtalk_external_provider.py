from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx

from .partner_models import PartnerIdentityError, VerifiedProviderSubject

_AUTHORIZATION_URL = "https://login.dingtalk.com/oauth2/auth"
_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
_PROFILE_URL = "https://api.dingtalk.com/v1.0/contact/users/me"
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
    """Personal OAuth identity with a bounded local verification lease.

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
                    "client_id": self._client_id,
                    "redirect_uri": self._redirect_uri,
                    "response_type": "code",
                    "scope": "openid",
                    "state": state,
                    "prompt": "consent",
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
            if "error" in callback:
                raise ValueError
            _required_ascii(callback.get("state"), maximum=1024)
            code = _required_ascii(callback.get("authCode"), maximum=4096)
            if "code" in callback and callback["code"] != code:
                raise ValueError
            return code
        except Exception:  # noqa: BLE001 - callback identity material must not escape
            raise PartnerIdentityError("partner_auth_invalid", 401) from None

    async def _request(self, method: str, url: str, **kwargs: object) -> dict:
        try:
            # Authorization codes are single use; neither operation is retried.
            response = await self._client.request(method, url, **kwargs)
            if response.status_code != 200:
                raise ValueError
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            if any(key in payload for key in ("code", "errcode", "error")):
                raise ValueError
            return payload
        except Exception:  # noqa: BLE001 - do not expose requests, tokens or provider bodies
            raise PartnerIdentityError("partner_identity_unavailable") from None

    def _evict_callback_proofs(self, now: datetime) -> None:
        for subject, proof in tuple(self._callback_proofs.items()):
            if not _recent_proof(proof, now=now, ttl=_CALLBACK_PROOF_TTL):
                del self._callback_proofs[subject]

    async def finish_auth(self, callback: Mapping[str, str]) -> VerifiedProviderSubject:
        code = self._callback_code(callback)
        token_response = await self._request(
            "POST",
            _TOKEN_URL,
            json={
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        try:
            token = _required_ascii(token_response.get("accessToken"), maximum=4096)
        except ValueError:
            raise PartnerIdentityError("partner_identity_unavailable") from None
        profile = await self._request(
            "GET",
            _PROFILE_URL,
            headers={
                "x-acs-dingtalk-access-token": token,
            },
        )
        try:
            union_id = _required_ascii(profile.get("unionId"), maximum=512)
            nickname = _required_nickname(profile.get("nick"))
        except ValueError:
            raise PartnerIdentityError("partner_identity_unavailable") from None
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
