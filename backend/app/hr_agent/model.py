"""Direct, single-endpoint model transport and provider event normalization."""

from __future__ import annotations

import json
import asyncio
import queue
import threading
import stat
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .types import ModelEvent, ModelReply, ModelRequest, ToolCall, Usage


class ModelError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ModelTransportError(ModelError):
    pass


class ModelProtocolError(ModelError):
    pass


class ModelPort(Protocol):
    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    revision: str
    protocol: str
    endpoint: str
    model: str
    credential_file: Path
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        try:
            parsed = urlparse(self.endpoint)
            port = parsed.port
        except (ValueError,TypeError):
            raise ValueError('invalid provider profile') from None
        if (
            not self.profile_id
            or not self.revision
            or self.protocol not in {"openai_chat_sse", "anthropic_messages_sse"}
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and port == 0)
            or not self.model
            or not isinstance(self.credential_file, Path)
            or not self.credential_file.is_absolute()
            or not (0 < self.timeout_seconds <= 120)
        ):
            raise ValueError("invalid provider profile")


def _credential(path: Path) -> str:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_symlink() or not path.is_file() or mode & 0o077:
            raise ValueError
        value = path.read_text(encoding="utf-8").strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError):
        raise ModelTransportError("configuration_unavailable") from None


class ConfiguredHttpModelPort:
    """Makes exactly one HTTP request to the configured profile endpoint."""

    def __init__(self, profile: ProviderProfile) -> None:
        self._profile = profile
        # Keep HTTP library diagnostics at WARNING even if an application enables
        # broad debug logging; request headers and bodies are never ours to log.
        for name in ("httpx", "httpcore"):
            import logging

            logging.getLogger(name).setLevel(logging.WARNING)

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> ConfiguredHttpModelPort:
        required = {
            "id",
            "revision",
            "protocol",
            "endpoint",
            "model",
            "credential_file",
            "tokenizer",
            "context_window_tokens",
        }
        allowed = required | {"timeout_seconds"}
        if (
            not isinstance(value, dict)
            or set(value) - allowed
            or not required.issubset(value)
            or not isinstance(value["context_window_tokens"], int)
            or isinstance(value["context_window_tokens"], bool)
            or value["context_window_tokens"] <= 0
            or not isinstance(value["tokenizer"], str)
            or not value["tokenizer"]
        ):
            raise ValueError("invalid provider profile")
        return cls(
            ProviderProfile(
                profile_id=str(value["id"]),
                revision=str(value["revision"]),
                protocol=str(value["protocol"]),
                endpoint=str(value["endpoint"]),
                model=str(value["model"]),
                credential_file=Path(str(value["credential_file"])),
                timeout_seconds=float(value.get("timeout_seconds", 120)),
            )
        )

    @property
    def profile_revision(self) -> str:
        return self._profile.revision

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        if request.profile_id != self._profile.profile_id:
            raise ModelTransportError("configuration_unavailable")
        timeout = min(120.0, self._profile.timeout_seconds, request.deadline_seconds)
        if timeout <= 0:
            raise ModelTransportError("transport_error")
        credential = _credential(self._profile.credential_file)
        headers, body = self._wire_request(request, credential)
        deadline_at = time.monotonic() + timeout
        lines = _http_lines(self._profile.endpoint, headers, body, deadline_at)
        try:
            yield from self._normalize(lines, deadline_at)
        finally:
            lines.close()

    def _wire_request(
        self, request: ModelRequest, credential: str
    ) -> tuple[dict[str, str], dict]:
        messages = [dict(item) for item in request.messages]
        tools = [dict(item) for item in request.tools]
        if self._profile.protocol == "openai_chat_sse":
            return (
                {
                    "Authorization": f"Bearer {credential}",
                    "Accept": "text/event-stream",
                },
                {
                    "model": self._profile.model,
                    "messages": messages,
                    "tools": tools,
                    "max_tokens": request.max_output_tokens,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
        system, messages = _anthropic_messages(messages)
        return (
            {
                "x-api-key": credential,
                "anthropic-version": "2023-06-01",
                "Accept": "text/event-stream",
            },
            {
                "model": self._profile.model,
                "messages": messages,
                "system": system,
                "tools": [_anthropic_tool(tool) for tool in tools],
                "max_tokens": request.max_output_tokens,
                "stream": True,
            },
        )

    def _normalize(
        self, lines: Iterable[str], deadline_at: float
    ) -> Iterator[ModelEvent]:
        bounded = _deadline_lines(lines, deadline_at)
        events = (_openai_events(bounded) if self._profile.protocol == 'openai_chat_sse'
                  else _anthropic_events(bounded))
        for event in events:
            if event.type == 'usage':
                yield ModelEvent('usage', {'provider_protocol': self._profile.protocol, 'raw': event.payload})
            else:
                yield event


def _http_lines(endpoint, headers, body, deadline_at):
    """Cancel the whole request at one deadline, including partial SSE lines.

    Async socket cancellation runs in an owned thread so the public model port
    remains an iterator usable by the independent synchronous Worker.
    """
    output = queue.SimpleQueue()
    state = {}

    async def fetch():
        state['loop'] = asyncio.get_running_loop()
        state['task'] = asyncio.current_task()
        try:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(timeout=httpx.Timeout(remaining), follow_redirects=False) as client:
                    async with client.stream('POST', endpoint, headers=headers, json=body) as response:
                        if response.status_code == 429:
                            raise ModelTransportError('rate_limited')
                        if response.status_code in {401,403}:
                            raise ModelTransportError('provider_refused')
                        if not 200 <= response.status_code < 300:
                            raise ModelTransportError('transport_error')
                        async for line in response.aiter_lines():
                            output.put(('line',line))
        except ModelError as error:
            output.put(('error',error))
        except (TimeoutError,httpx.HTTPError,OSError,UnicodeError):
            output.put(('error',ModelTransportError('transport_error')))
        except asyncio.CancelledError:
            pass
        except Exception:
            # Configuration/serialization/library failures must not print a
            # background-thread traceback containing endpoint or provider data.
            output.put(('error',ModelTransportError('transport_error')))
        finally:
            output.put(('done',None))

    thread = threading.Thread(target=lambda: asyncio.run(fetch()), name='hr-model-http', daemon=True)
    thread.start()
    try:
        while True:
            remaining = deadline_at-time.monotonic()
            if remaining <= 0:
                raise ModelTransportError('transport_error')
            try:
                kind,value = output.get(timeout=remaining)
            except queue.Empty:
                raise ModelTransportError('transport_error') from None
            if kind == 'error':
                raise value
            if kind == 'done':
                return
            yield value
    finally:
        loop,task=state.get('loop'),state.get('task')
        if thread.is_alive() and loop is not None and task is not None:
            try:loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:pass # Loop already closed after complete response.
        thread.join(timeout=0.1)


def _anthropic_messages(messages):
    """Convert canonical text/tool messages without dropping tool identities."""
    system=[]
    converted=[]
    pending=set()
    seen=set()
    try:
        for message in messages:
            role=message['role']
            content=message.get('content')
            if role == 'system':
                if converted or not isinstance(content,str):raise ValueError()
                if content:system.append({'type':'text','text':content})
                continue
            blocks=[]
            if role == 'tool':
                identity=message['tool_call_id']
                if identity not in pending or not isinstance(content,str):raise ValueError()
                pending.remove(identity)
                blocks=[{'type':'tool_result','tool_use_id':identity,'content':content}]
                role='user'
            elif role in {'user','assistant'}:
                if pending:raise ValueError()
                if content is not None and not isinstance(content,str):raise ValueError()
                if content:blocks.append({'type':'text','text':content})
                calls=message.get('tool_calls',[])
                if not isinstance(calls,list) or (calls and role!='assistant'):raise ValueError()
                for call in calls:
                    if call.get('type')!='function':raise ValueError()
                    identity=call['id'];function=call['function']
                    if not isinstance(identity,str) or not identity or identity in seen:raise ValueError()
                    name=function['name'];arguments=function['arguments']
                    if isinstance(arguments,str):arguments=json.loads(arguments)
                    if not isinstance(arguments,dict) or not isinstance(name,str) or not name:raise ValueError()
                    blocks.append({'type':'tool_use','id':identity,'name':name,'input':arguments})
                    pending.add(identity);seen.add(identity)
            else:raise ValueError()
            if not blocks:raise ValueError()
            if converted and converted[-1]['role']==role:
                converted[-1]['content'].extend(blocks)
            else:converted.append({'role':role,'content':blocks})
        if pending or not converted:raise ValueError()
        return system,converted
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ModelProtocolError('invalid_response') from None


def _deadline_lines(lines: Iterable[str], deadline_at: float) -> Iterator[str]:
    for line in lines:
        if time.monotonic() >= deadline_at:
            raise ModelTransportError("transport_error")
        yield line


def _json_object(data: str) -> dict:
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ModelProtocolError("invalid_response") from None


def _openai_events(lines: Iterable[str]) -> Iterator[ModelEvent]:
    complete = False
    for line in lines:
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            raise ModelProtocolError("invalid_response")
        data = line[5:].strip()
        if data == "[DONE]":
            complete = True
            break
        item = _json_object(data)
        usage = item.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ModelProtocolError("invalid_response")
            yield ModelEvent("usage", dict(usage))
        choices = item.get("choices", [])
        if not isinstance(choices, list):
            raise ModelProtocolError("invalid_response")
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(
                choice.get("delta", {}), dict
            ):
                raise ModelProtocolError("invalid_response")
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise ModelProtocolError("invalid_response")
                yield ModelEvent("text_delta", {"text": content})
            calls = delta.get("tool_calls", [])
            if not isinstance(calls, list):
                raise ModelProtocolError("invalid_response")
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("index"), int):
                    raise ModelProtocolError("invalid_response")
                function = call.get("function", {})
                if not isinstance(function, dict):
                    raise ModelProtocolError("invalid_response")
                payload = {"index": call["index"]}
                for source, target in (("id", "provider_call_id"),):
                    if source in call:
                        payload[target] = call[source]
                if "name" in function:
                    payload["name"] = function["name"]
                if "arguments" in function:
                    payload["arguments_delta"] = function["arguments"]
                yield ModelEvent("tool_delta", payload)
            stop = choice.get("finish_reason")
            if stop is not None:
                if not isinstance(stop, str) or not stop:
                    raise ModelProtocolError("invalid_response")
                if stop == "content_filter":
                    raise ModelTransportError("provider_refused")
                yield ModelEvent("stop", {"reason": stop})
    if not complete:
        raise ModelProtocolError("incomplete_response")


def _anthropic_events(lines: Iterable[str]) -> Iterator[ModelEvent]:
    complete = False
    blocks: dict[int, dict[str, object]] = {}
    for line in lines:
        if not line or line.startswith(("event:", ":")):
            continue
        if not line.startswith("data:"):
            raise ModelProtocolError("invalid_response")
        item = _json_object(line[5:].strip())
        kind = item.get("type")
        if kind == "message_start":
            usage = (
                item.get("message", {}).get("usage")
                if isinstance(item.get("message"), dict)
                else None
            )
            if usage:
                yield ModelEvent("usage", dict(usage))
        elif kind == "content_block_start":
            index, block = item.get("index"), item.get("content_block")
            if not isinstance(index, int) or not isinstance(block, dict):
                raise ModelProtocolError("invalid_response")
            blocks[index] = block
            if block.get("type") == "tool_use":
                initial = block.get("input", {})
                if not isinstance(initial, dict):
                    raise ModelProtocolError("invalid_response")
                yield ModelEvent(
                    "tool_delta",
                    {
                        "index": index,
                        "provider_call_id": block.get("id"),
                        "name": block.get("name"),
                        "arguments_delta": json.dumps(initial, separators=(",", ":"))
                        if initial
                        else "",
                    },
                )
        elif kind == "content_block_delta":
            index, delta = item.get("index"), item.get("delta")
            if not isinstance(index, int) or not isinstance(delta, dict):
                raise ModelProtocolError("invalid_response")
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                yield ModelEvent("text_delta", {"text": delta["text"]})
            elif delta.get("type") == "input_json_delta" and isinstance(
                delta.get("partial_json"), str
            ):
                yield ModelEvent(
                    "tool_delta",
                    {"index": index, "arguments_delta": delta["partial_json"]},
                )
        elif kind == "message_delta":
            delta, usage = item.get("delta"), item.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ModelProtocolError("invalid_response")
                yield ModelEvent("usage", dict(usage))
            if isinstance(delta, dict) and delta.get("stop_reason"):
                if delta["stop_reason"] == "refusal":
                    raise ModelTransportError("provider_refused")
                yield ModelEvent("stop", {"reason": delta["stop_reason"]})
        elif kind == "message_stop":
            complete = True
            break
        elif kind == "error":
            raise ModelTransportError("provider_refused")
    if not complete:
        raise ModelProtocolError("incomplete_response")


def collect_reply(events: Iterable[ModelEvent]) -> ModelReply:
    text: list[str] = []
    calls: dict[int, dict[str, object]] = {}
    raw_usage: dict[str, object] = {}
    stop_reason: str | None = None
    usage_protocol: str | None = None
    for event in events:
        if event.type == "text_delta":
            value = event.payload.get("text")
            if not isinstance(value, str):
                raise ModelProtocolError("invalid_response")
            text.append(value)
        elif event.type == "tool_delta":
            index = event.payload.get("index")
            if not isinstance(index, int) or index < 0:
                raise ModelProtocolError("invalid_response")
            call = calls.setdefault(index, {"arguments": ""})
            for key in ("provider_call_id", "name"):
                if key in event.payload:
                    value = event.payload[key]
                    if (
                        not isinstance(value, str)
                        or not value
                        or (key in call and call[key] != value)
                    ):
                        raise ModelProtocolError("invalid_response")
                    call[key] = value
            fragment = event.payload.get("arguments_delta", "")
            if not isinstance(fragment, str):
                raise ModelProtocolError("invalid_response")
            call["arguments"] = str(call["arguments"]) + fragment
        elif event.type == "usage":
            payload = event.payload
            if 'provider_protocol' in payload:
                protocol = payload['provider_protocol']
                if protocol not in {'openai_chat_sse','anthropic_messages_sse'} or not isinstance(payload.get('raw'),dict):
                    raise ModelProtocolError('invalid_response')
                if usage_protocol is not None and usage_protocol != protocol:
                    raise ModelProtocolError('invalid_response')
                usage_protocol = protocol
                payload = payload['raw']
            raw_usage.update(payload)
        elif event.type == "stop":
            reason = event.payload.get("reason")
            if stop_reason is not None or not isinstance(reason, str) or not reason:
                raise ModelProtocolError("invalid_response")
            stop_reason = reason
        else:
            raise ModelProtocolError("invalid_response")
    if stop_reason is None:
        raise ModelProtocolError("incomplete_response")
    if stop_reason in {'length','max_tokens','max_output_tokens','pause_turn'}:
        raise ModelProtocolError('incomplete_response')
    if stop_reason in {'refusal','content_filter'}:
        raise ModelTransportError('provider_refused')
    tool_calls: list[ToolCall] = []
    for index in sorted(calls):
        call = calls[index]
        try:
            encoded_arguments = str(call["arguments"]) or "{}"
            arguments = json.loads(encoded_arguments)
            if not isinstance(arguments, dict):
                raise TypeError
            tool_calls.append(
                ToolCall(str(call["provider_call_id"]), str(call["name"]), arguments)
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ModelProtocolError("incomplete_response") from None
    final_text = "".join(text)
    if not final_text and not tool_calls:
        raise ModelProtocolError("invalid_response")
    tool_stop_reasons = {"tool_calls", "tool_use"}
    if bool(tool_calls) != (stop_reason in tool_stop_reasons):
        raise ModelProtocolError("invalid_response")
    input_total, output_total = _usage_totals(raw_usage, usage_protocol)
    usage = Usage(
        dict(raw_usage) or None,
        input_total,
        output_total,
        "reported" if input_total is not None and output_total is not None else "unknown",
    )
    return ModelReply(final_text, tuple(tool_calls), stop_reason, usage)


def _anthropic_tool(tool: dict) -> dict:
    function = tool.get("function") if tool.get("type") == "function" else None
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise ModelTransportError("configuration_unavailable")
    converted = {
        "name": function["name"],
        "input_schema": function.get("parameters", {"type": "object"}),
    }
    if isinstance(function.get("description"), str):
        converted["description"] = function["description"]
    return converted


def _usage_totals(raw: dict, protocol: str | None) -> tuple[int | None, int | None]:
    """Normalize native non-overlapping counters; retain raw nested details.

    OpenAI prompt/completion totals already include cached/reasoning tokens.
    Anthropic input_tokens excludes its cache creation and cache read buckets.
    Untagged events are supported for internal scripted model fixtures only.
    """
    if protocol is None:
        protocol = 'openai_chat_sse' if any(k in raw for k in ('prompt_tokens','completion_tokens')) else 'anthropic_messages_sse'

    def counter(name, default=None):
        value=raw.get(name,default)
        if value is not None and (type(value) is not int or value < 0):
            raise ModelProtocolError('invalid_response')
        return value

    if protocol == 'openai_chat_sse':
        return counter('prompt_tokens'),counter('completion_tokens')
    input_parts=[counter('input_tokens'),counter('cache_creation_input_tokens',0),counter('cache_read_input_tokens',0)]
    input_total=sum(input_parts) if all(value is not None for value in input_parts) else None
    return input_total,counter('output_tokens')
