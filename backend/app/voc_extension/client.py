"""Bounded HTTP client for the loopback-only VOC workspace service."""

from __future__ import annotations

import json as json_module
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


class VocTaskClient:
    """Synchronous narrow client used by the durable Brain worker."""

    def __init__(
        self,
        base_url: str,
        signer: PlatformVocTokenSigner,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("VOC timeout must be between 1 and 60 seconds")
        self._signer = signer
        self._client = httpx.Client(
            base_url=_validated_base_url(base_url),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    def _request(
        self,
        path: str,
        *,
        actor_id: UUID,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        token = self._signer.issue(actor_id, SELF_SERVICE_CAPABILITIES)
        try:
            with self._client.stream(
                "POST",
                _validated_path(path),
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            ) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise VocProtocolError("voc_response_too_large")
                if response.status_code not in {200, 201}:
                    raise VocProtocolError("voc_action_rejected")
        except VocProtocolError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise VocUpstreamUnavailable("voc_unavailable") from None
        try:
            decoded = json_module.loads(bytes(body))
        except (UnicodeDecodeError, ValueError):
            raise VocProtocolError("voc_response_invalid") from None
        if not isinstance(decoded, dict):
            raise VocProtocolError("voc_response_invalid")
        return dict(decoded)

    def create_draft(
        self, *, actor_id: UUID, request_id: UUID, source_text: str
    ) -> dict[str, object]:
        if (
            not isinstance(actor_id, UUID)
            or not isinstance(request_id, UUID)
            or type(source_text) is not str
            or not source_text.strip()
            or len(source_text) > 4000
        ):
            raise ValueError("VOC draft request invalid")
        value = self._request(
            "/api/platform/v1/drafts",
            actor_id=actor_id,
            payload={"request_id": str(request_id), "source_text": source_text},
        )
        try:
            draft_id = str(UUID(str(value["draft_id"])))
            version = value["version"]
            if type(version) is not int or version <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise VocProtocolError("voc_draft_response_invalid") from None
        return {"draft_id": draft_id, "version": version}

    def submit_draft(
        self,
        *,
        actor_id: UUID,
        draft_id: UUID,
        request_id: UUID,
        expected_version: int,
    ) -> dict[str, object]:
        if (
            not isinstance(actor_id, UUID)
            or not isinstance(draft_id, UUID)
            or not isinstance(request_id, UUID)
            or type(expected_version) is not int
            or expected_version <= 0
        ):
            raise ValueError("VOC submit request invalid")
        value = self._request(
            f"/api/platform/v1/drafts/{draft_id}/submit",
            actor_id=actor_id,
            payload={
                "request_id": str(request_id),
                "expected_version": expected_version,
            },
        )
        voc_no = value.get("voc_no")
        revision = value.get("revision")
        already_submitted = value.get("already_submitted")
        if (
            type(voc_no) is not str
            or not voc_no.startswith("VOC-")
            or type(revision) is not int
            or revision <= 0
            or type(already_submitted) is not bool
        ):
            raise VocProtocolError("voc_submit_response_invalid")
        return {
            "voc_no": voc_no,
            "revision": revision,
            "already_submitted": already_submitted,
        }

    def close(self) -> None:
        self._client.close()
