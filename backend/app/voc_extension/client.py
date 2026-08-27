"""Bounded HTTP client for the loopback-only VOC workspace service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .identity import VOC_CAPABILITIES, PlatformVocTokenSigner

_MAX_RESPONSE_BYTES = 1_048_576
SELF_SERVICE_CAPABILITIES = frozenset({"voc.submit", "voc.read_self"})


class VocUpstreamUnavailable(RuntimeError):
    """The private VOC service could not be reached in time."""


class VocProtocolError(RuntimeError):
    """The private VOC service violated the bounded BFF contract."""


@dataclass(frozen=True, slots=True)
class VocUpstreamResponse:
    status_code: int
    body: bytes


def _validated_base_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
        private_service = host is not None and (
            ip_address(host).is_loopback or host == "172.29.0.3"
        )
    except ValueError:
        private_service = False
        port = None
    if (
        parsed.scheme != "http"
        or not private_service
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("VOC base URL must be the absolute fixed private HTTP origin")
    return value.rstrip("/")


def _validated_path(path: str) -> str:
    parsed = urlparse(path)
    if (
        not path.startswith("/api/platform/v1/")
        or parsed.path != path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("VOC request path is outside the workspace contract")
    return path


class VocExtensionClient:
    """Call one fixed loopback origin with a freshly signed actor identity."""

    def __init__(
        self,
        base_url: str,
        signer: PlatformVocTokenSigner,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("VOC timeout must be between 1 and 60 seconds")
        self._signer = signer
        self._client = httpx.AsyncClient(
            base_url=_validated_base_url(base_url),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        actor_id: UUID,
        json: Mapping[str, object] | None = None,
        query: Mapping[str, object] | None = None,
        capabilities: frozenset[str] = SELF_SERVICE_CAPABILITIES,
    ) -> VocUpstreamResponse:
        normalized_method = method.upper()
        if normalized_method not in {"GET", "POST", "PATCH"}:
            raise ValueError("VOC request method is not allowed")
        selected_capabilities = frozenset(capabilities)
        if not selected_capabilities or not selected_capabilities.issubset(
            VOC_CAPABILITIES
        ):
            raise ValueError("VOC capabilities are invalid")
        token = self._signer.issue(actor_id, selected_capabilities)
        try:
            async with self._client.stream(
                normalized_method,
                _validated_path(path),
                headers={"Authorization": f"Bearer {token}"},
                json=json,
                params=query,
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise VocProtocolError("voc_response_too_large")
                return VocUpstreamResponse(response.status_code, bytes(body))
        except VocProtocolError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise VocUpstreamUnavailable("voc_unavailable") from None

    async def health(self) -> VocUpstreamResponse:
        """Call only the downstream safe health endpoint without employee identity."""

        try:
            async with self._client.stream("GET", "/health") as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise VocProtocolError("voc_response_too_large")
                return VocUpstreamResponse(response.status_code, bytes(body))
        except VocProtocolError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise VocUpstreamUnavailable("voc_unavailable") from None

    async def aclose(self) -> None:
        await self._client.aclose()
