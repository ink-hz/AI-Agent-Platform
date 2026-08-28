from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Literal

import httpx

from app.agent_brain.model_adapter import (
    BrainModelAdapter,
    BrainModelRequest,
    BrainModelResponse,
    BrainUsage,
    ProviderInterrupted,
    ProviderRefused,
    ProviderTruncated,
    ProviderUnavailable,
    ThinkingDelta,
)


_THINKING_FLUSH_SECONDS = 0.5
_THINKING_FLUSH_BYTES = 4096


class _ThinkingCoalescer:
    """Batch thinking deltas before handing them to the persistence callback.

    The Provider emits a thinking delta every few tokens and the Brain persists each
    one synchronously inside the stream read loop: a fresh database connection, two
    locking selects, a decrypt of the whole accumulated summary, a re-encrypt of the
    whole summary, an update and a commit. The conversation stream shows the user the
    accumulated snapshot on a one-second poll, so anything finer is invisible while
    still stalling the read loop.

    delta_seq counts flushes, not Provider deltas, which keeps the repository's
    strictly-consecutive contract intact.
    """

    def __init__(
        self,
        sink: Callable[[ThinkingDelta], None],
        *,
        now: Callable[[], float],
        flush_seconds: float = _THINKING_FLUSH_SECONDS,
        flush_bytes: int = _THINKING_FLUSH_BYTES,
    ) -> None:
        self._sink = sink
        self._now = now
        self._flush_seconds = flush_seconds
        self._flush_bytes = flush_bytes
        self._pending: dict[int, list[str]] = {}
        self._pending_bytes: dict[int, int] = {}
        self._sequences: dict[int, int] = {}
        self._opened_at: dict[int, float] = {}

    def add(self, index: int, text: str, provider_id: str) -> None:
        if index not in self._pending:
            self._pending[index] = []
            self._pending_bytes[index] = 0
            self._opened_at[index] = self._now()
        self._pending[index].append(text)
        self._pending_bytes[index] += len(text.encode("utf-8"))
        if (
            self._pending_bytes[index] >= self._flush_bytes
            or self._now() - self._opened_at[index] >= self._flush_seconds
        ):
            self.flush(index, provider_id)

    def flush(self, index: int, provider_id: str | None) -> None:
        if provider_id is None or not self._pending.get(index):
            return
        text = "".join(self._pending[index])
        self._pending[index] = []
        self._pending_bytes[index] = 0
        self._opened_at[index] = self._now()
        sequence = self._sequences.get(index, 0) + 1
        self._sequences[index] = sequence
        self._sink(ThinkingDelta(index, sequence, text, provider_id))

    def flush_all(self, provider_id: str | None) -> None:
        for index in sorted(self._pending):
            self.flush(index, provider_id)


class AnthropicMessagesAdapter(BrainModelAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        auth_scheme: Literal["x-api-key", "bearer"] = "x-api-key",
        client: httpx.Client,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(base_url, str)
            or not base_url.startswith("https://")
            or not isinstance(api_key, str)
            or not api_key
            or auth_scheme not in {"x-api-key", "bearer"}
            or not isinstance(client, httpx.Client)
            or not callable(now)
        ):
            raise ValueError("Anthropic Adapter configuration invalid")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._auth_scheme = auth_scheme
        self._client = client
        self._now = now

    @classmethod
    def from_secret_file(
        cls,
        *,
        base_url: str,
        api_key_file: str,
        auth_scheme: Literal["x-api-key", "bearer"] = "x-api-key",
        client: httpx.Client,
    ) -> AnthropicMessagesAdapter:
        try:
            path = Path(api_key_file)
            metadata = path.lstat()
            if (
                not path.is_absolute()
                or path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
            ):
                raise ValueError
            api_key = path.read_text(encoding="utf-8").strip()
            if not api_key or "\n" in api_key or "\r" in api_key:
                raise ValueError
        except (OSError, UnicodeError, ValueError):
            raise ValueError("Anthropic API key file invalid") from None
        return cls(
            base_url=base_url,
            api_key=api_key,
            auth_scheme=auth_scheme,
            client=client,
        )

    def __repr__(self) -> str:
        return "AnthropicMessagesAdapter(base_url=<redacted>, api_key=<redacted>)"

    def _auth_headers(self) -> dict[str, str]:
        if self._auth_scheme == "bearer":
            return {"authorization": f"Bearer {self._api_key}"}
        return {"x-api-key": self._api_key}

    def complete(
        self,
        request: BrainModelRequest,
        *,
        on_thinking_delta: Callable[[ThinkingDelta], None] | None = None,
    ) -> BrainModelResponse:
        if not isinstance(request, BrainModelRequest):
            raise ValueError("Brain model request required")
        for attempt in range(2):
            saw_event = False
            try:
                with self._client.stream(
                    "POST",
                    f"{self._base_url}/v1/messages",
                    json=request.provider_body(),
                    headers={
                        **self._auth_headers(),
                        "anthropic-version": "2023-06-01",
                        "accept": "text/event-stream",
                    },
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt == 0:
                            continue
                        raise ProviderUnavailable()
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ProviderUnavailable()
                    events: list[dict[str, object]] = []
                    provider_id: str | None = None
                    coalescer = _ThinkingCoalescer(
                        on_thinking_delta or (lambda _delta: None), now=self._now
                    )
                    try:
                        for line in response.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                event = json.loads(line[6:])
                            except (UnicodeError, json.JSONDecodeError):
                                raise ProviderInterrupted() from None
                            if type(event) is not dict:
                                raise ProviderInterrupted()
                            saw_event = True
                            events.append(event)
                            event_type = event.get("type")
                            if event_type == "message_start":
                                message = event.get("message")
                                if not isinstance(message, dict) or not isinstance(
                                    message.get("id"), str
                                ):
                                    raise ProviderInterrupted()
                                provider_id = message["id"]
                            elif event_type == "content_block_delta":
                                index = event.get("index")
                                delta = event.get("delta")
                                if (
                                    type(index) is int
                                    and isinstance(delta, dict)
                                    and delta.get("type") == "thinking_delta"
                                ):
                                    text = delta.get("thinking")
                                    if provider_id is None or type(text) is not str:
                                        raise ProviderInterrupted()
                                    if not text:
                                        continue
                                    coalescer.add(index, text, provider_id)
                            elif event_type == "content_block_stop":
                                index = event.get("index")
                                if type(index) is int:
                                    coalescer.flush(index, provider_id)
                    finally:
                        # Persist whatever thinking already arrived even when the
                        # stream is interrupted; before coalescing every delta was
                        # already durable at this point.
                        coalescer.flush_all(provider_id)
                    return _aggregate_events(events)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                if saw_event or attempt == 1:
                    raise ProviderInterrupted() if saw_event else ProviderUnavailable()
                continue
            except (ProviderRefused, ProviderTruncated, ProviderUnavailable):
                raise
            except ProviderInterrupted:
                raise
            except httpx.HTTPError:
                if saw_event or attempt == 1:
                    raise ProviderInterrupted() if saw_event else ProviderUnavailable()
        raise ProviderUnavailable()


def _aggregate_events(events: list[dict[str, object]]) -> BrainModelResponse:
    provider_id: str | None = None
    blocks: dict[int, dict[str, object]] = {}
    partial_json: dict[int, str] = {}
    stop_reason: str | None = None
    stop_details: dict[str, object] | None = None
    usage = {name: 0 for name in (
        "input_tokens", "output_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )}
    stopped = False
    try:
        for event in events:
            event_type = event.get("type")
            if event_type == "message_start":
                message = event["message"]
                if not isinstance(message, dict) or not isinstance(message.get("id"), str):
                    raise ValueError
                provider_id = message["id"]
                _merge_usage(usage, message.get("usage"))
            elif event_type == "content_block_start":
                index = event["index"]
                block = event["content_block"]
                if type(index) is not int or not isinstance(block, dict):
                    raise ValueError
                blocks[index] = dict(block)
                if block.get("type") == "tool_use":
                    partial_json[index] = ""
            elif event_type == "content_block_delta":
                index = event["index"]
                delta = event["delta"]
                if type(index) is not int or index not in blocks or not isinstance(delta, dict):
                    raise ValueError
                delta_type = delta.get("type")
                if delta_type == "thinking_delta":
                    blocks[index]["thinking"] = str(blocks[index].get("thinking", "")) + delta["thinking"]
                elif delta_type == "signature_delta":
                    blocks[index]["signature"] = str(blocks[index].get("signature", "")) + delta["signature"]
                elif delta_type == "input_json_delta":
                    partial_json[index] += delta["partial_json"]
                elif delta_type == "text_delta":
                    blocks[index]["text"] = str(blocks[index].get("text", "")) + delta["text"]
                else:
                    raise ValueError
            elif event_type == "content_block_stop":
                index = event["index"]
                if index in partial_json:
                    if partial_json[index]:
                        blocks[index]["input"] = json.loads(partial_json[index])
            elif event_type == "message_delta":
                delta = event.get("delta")
                if not isinstance(delta, dict):
                    raise ValueError
                stop_reason = delta.get("stop_reason")
                details = delta.get("stop_details")
                stop_details = details if isinstance(details, dict) else None
                _merge_usage(usage, event.get("usage"))
            elif event_type == "message_stop":
                stopped = True
            elif event_type == "ping":
                continue
            else:
                raise ValueError
        if provider_id is None or not stopped or not isinstance(stop_reason, str):
            raise ValueError
        ordered = tuple(blocks[index] for index in sorted(blocks))
        if stop_reason == "refusal":
            category = stop_details.get("category") if stop_details else None
            raise ProviderRefused(category if isinstance(category, str) else None)
        if stop_reason == "max_tokens":
            raise ProviderTruncated()
        return BrainModelResponse(
            provider_request_id=provider_id,
            content_blocks=ordered,
            stop_reason=stop_reason,
            stop_details=stop_details,
            usage=BrainUsage(**usage),
        )
    except (ProviderRefused, ProviderTruncated):
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ProviderInterrupted() from None


def _merge_usage(target: dict[str, int], value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError
    for key in target:
        candidate = value.get(key)
        if candidate is not None:
            if type(candidate) is not int or candidate < 0:
                raise ValueError
            target[key] = candidate
