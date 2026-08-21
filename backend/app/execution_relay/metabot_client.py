from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .models import RelayJobPayload


_APPROVED_AGENT_IDS = frozenset(
    {
        "hr-bot",
        "marketing-prospecting-bot",
        "marketing-inbound-bot",
        "marketing-voice-bot",
        "fae-bot",
        "marketing-gtm-bot",
        "marketing-intelligence-bot",
    }
)
_CONFIGURATION_INVALID = "metabot configuration invalid"
_REQUEST_FAILED = "metabot request failed"
_OWNER_FILE_LIMIT = 16_384


class MetaBotClientError(RuntimeError):
    """Stable MetaBot boundary failure without request or secret content."""


def _configuration_error() -> MetaBotClientError:
    return MetaBotClientError(_CONFIGURATION_INVALID)


def _read_secret_file(path: Path) -> str:
    parent_descriptor: int | None = None
    file_descriptor: int | None = None
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError
        common_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        parent_descriptor = os.open(
            candidate.parent,
            common_flags | no_follow | getattr(os, "O_DIRECTORY", 0),
        )
        parent_status = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_status.st_mode)
            or stat.S_IMODE(parent_status.st_mode) != 0o700
            or parent_status.st_uid != os.geteuid()
        ):
            raise ValueError
        file_descriptor = os.open(
            candidate.name,
            common_flags | no_follow,
            dir_fd=parent_descriptor,
        )
        file_status = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or stat.S_IMODE(file_status.st_mode) != 0o600
            or file_status.st_uid != os.geteuid()
            or file_status.st_size > _OWNER_FILE_LIMIT
        ):
            raise ValueError
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(
                file_descriptor,
                min(4096, _OWNER_FILE_LIMIT + 1 - size),
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _OWNER_FILE_LIMIT:
                raise ValueError
        secret = b"".join(chunks).decode("utf-8").strip()
        if (
            not secret
            or "\x00" in secret
            or "\r" in secret
            or "\n" in secret
        ):
            raise ValueError
        return secret
    except (OSError, UnicodeError, TypeError, ValueError):
        raise _configuration_error() from None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


@dataclass(frozen=True)
class MetaBotRuntimeMap:
    _ports: Mapping[str, int]

    def __post_init__(self) -> None:
        try:
            ports = dict(self._ports)
            if set(ports) != _APPROVED_AGENT_IDS:
                raise ValueError
            if any(
                isinstance(port, bool)
                or not isinstance(port, int)
                or not 1 <= port <= 65535
                for port in ports.values()
            ) or len(set(ports.values())) != len(ports):
                raise ValueError
        except (TypeError, ValueError):
            raise _configuration_error() from None
        object.__setattr__(self, "_ports", MappingProxyType(ports))

    @classmethod
    def from_contract(cls, path: Path) -> MetaBotRuntimeMap:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or isinstance(payload.get("schemaVersion"), bool)
                or payload.get("schemaVersion") != 2
                or not isinstance(payload.get("bots"), list)
            ):
                raise ValueError
            ports: dict[str, int] = {}
            used_ports: set[int] = set()
            for entry in payload["bots"]:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if name not in _APPROVED_AGENT_IDS:
                    continue
                if name in ports:
                    raise ValueError
                instance = entry.get("instance")
                if not isinstance(instance, dict):
                    raise ValueError
                port = instance.get("apiPort")
                if (
                    isinstance(port, bool)
                    or not isinstance(port, int)
                    or not 1 <= port <= 65535
                    or port in used_ports
                ):
                    raise ValueError
                ports[name] = port
                used_ports.add(port)
            if set(ports) != _APPROVED_AGENT_IDS:
                raise ValueError
            return cls(ports)
        except MetaBotClientError:
            raise
        except (
            OSError,
            UnicodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            raise _configuration_error() from None

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ports))

    def port_for(self, agent_id: str) -> int:
        try:
            return self._ports[agent_id]
        except (KeyError, TypeError):
            raise _configuration_error() from None


class MetaBotClient:
    def __init__(
        self,
        runtime_map: MetaBotRuntimeMap,
        bearer_secret_file: Path,
    ) -> None:
        if not isinstance(runtime_map, MetaBotRuntimeMap):
            raise _configuration_error()
        self._runtime_map = runtime_map
        self._bearer_secret = _read_secret_file(bearer_secret_file)

    def __repr__(self) -> str:
        return "MetaBotClient(runtime_map=<configured>, bearer_secret=<redacted>)"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": f"Bearer {self._bearer_secret}"},
        )

    @staticmethod
    def _callback_is_loopback(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "http"
                and parsed.hostname == "127.0.0.1"
                and parsed.port is not None
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
            )
        except (TypeError, ValueError):
            return False

    def start_run(self, payload: RelayJobPayload, event_callback_url: str) -> None:
        try:
            if (
                not isinstance(payload, RelayJobPayload)
                or not isinstance(event_callback_url, str)
                or not self._callback_is_loopback(event_callback_url)
            ):
                raise ValueError
            port = self._runtime_map.port_for(payload.agent_id)
            request_json = {
                "runId": str(payload.run_id),
                "conversationId": str(payload.conversation_id),
                "triggerMessageId": str(payload.trigger_message_id),
                "targetBot": payload.agent_id,
                "prompt": payload.prompt,
                "eventCallbackUrl": event_callback_url,
                "executionChatId": (
                    f"platform-{payload.conversation_id}-{payload.agent_id}"
                ),
                "userId": "platform-user",
                "maxTurns": payload.max_turns,
            }
            with self._client() as client:
                response = client.post(
                    f"http://127.0.0.1:{port}/api/core-chat/runs",
                    json=request_json,
                )
            if response.status_code != 202:
                raise ValueError
            result = response.json()
            if (
                not isinstance(result, dict)
                or result.get("status") != "accepted"
                or result.get("runId") != str(payload.run_id)
                or result.get("targetBot") != payload.agent_id
            ):
                raise ValueError
        except Exception:
            raise MetaBotClientError(_REQUEST_FAILED) from None

    def cancel_run(self, run_id: UUID, agent_id: str) -> None:
        try:
            if not isinstance(run_id, UUID) or not isinstance(agent_id, str):
                raise ValueError
            port = self._runtime_map.port_for(agent_id)
            with self._client() as client:
                response = client.post(
                    f"http://127.0.0.1:{port}/api/core-chat/runs/{run_id}/cancel"
                )
            if response.status_code != 200:
                raise ValueError
            result = response.json()
            if not isinstance(result, dict) or result.get("runId") != str(run_id):
                raise ValueError
        except Exception:
            raise MetaBotClientError(_REQUEST_FAILED) from None
