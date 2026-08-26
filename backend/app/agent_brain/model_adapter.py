from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable, Literal

from app.agent_brain.tool_protocol import BRAIN_TOOL_SCHEMAS


_MANIFEST_FIELDS = {
    "config_version",
    "provider_kind",
    "model_id",
    "context_profile",
    "context_window",
    "thinking_type",
    "thinking_display",
    "thinking_effort",
    "max_output_tokens",
    "max_answer_bytes",
    "prompt_cache_enabled",
    "stable_cache_ttl",
    "rolling_cache_ttl",
    "system_prompt_sha256",
}


class BrainModelError(RuntimeError):
    """Sanitized Provider boundary error."""


class ProviderUnavailable(BrainModelError):
    def __init__(self) -> None:
        super().__init__("Brain model Provider unavailable")


class ProviderInterrupted(BrainModelError):
    def __init__(self) -> None:
        super().__init__("Brain model stream interrupted")


class ProviderTruncated(BrainModelError):
    def __init__(self) -> None:
        super().__init__("Brain model output truncated")


class ProviderRefused(BrainModelError):
    def __init__(self, category: str | None) -> None:
        self.category = category if isinstance(category, str) and category else None
        super().__init__("Brain model request refused")


@dataclass(frozen=True, slots=True)
class BrainModelManifest:
    config_version: str
    provider_kind: Literal["anthropic_compatible"]
    model_id: Literal["claude-opus-5"]
    context_profile: Literal["opus_1m"]
    context_window: int
    thinking_type: Literal["adaptive"]
    thinking_display: Literal["summarized"]
    thinking_effort: Literal["medium", "high", "xhigh"]
    max_output_tokens: int
    max_answer_bytes: int
    prompt_cache_enabled: bool
    stable_cache_ttl: Literal["1h"]
    rolling_cache_ttl: Literal["5m"]
    system_prompt_sha256: str

    @classmethod
    def load(cls, path: Path) -> BrainModelManifest:
        try:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
            if type(value) is not dict or set(value) != _MANIFEST_FIELDS:
                raise ValueError
            manifest = cls(**value)
            if (
                manifest.config_version != "brain-opus5-v1"
                or manifest.context_window != 1_000_000
                or manifest.max_output_tokens != 65_536
                or manifest.max_answer_bytes != 65_536
                or manifest.prompt_cache_enabled is not True
                or len(manifest.system_prompt_sha256) != 64
                or any(character not in "0123456789abcdef" for character in manifest.system_prompt_sha256)
            ):
                raise ValueError
            return manifest
        except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
            raise ValueError("Brain model manifest invalid") from None


@dataclass(frozen=True, slots=True)
class BrainUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    block_index: int
    delta_seq: int
    text: str = field(repr=False)
    provider_run_ref: str


@dataclass(frozen=True, slots=True)
class BrainModelResponse:
    provider_request_id: str
    content_blocks: tuple[dict[str, object], ...] = field(repr=False)
    stop_reason: str
    stop_details: dict[str, object] | None = field(default=None, repr=False)
    usage: BrainUsage = field(default_factory=BrainUsage)

    @property
    def public_blocks(self) -> tuple[dict[str, object], ...]:
        return tuple(
            block for block in self.content_blocks if block.get("type") != "thinking"
        )


@dataclass(frozen=True, slots=True)
class CacheBreakpoint:
    layer: Literal["tools", "system", "capability", "rolling"]
    ttl: Literal["1h", "5m"]


@dataclass(frozen=True, slots=True)
class BrainModelRequest:
    model_id: str
    max_tokens: int
    thinking_display: str
    effort: str
    tools: tuple[dict[str, object], ...]
    system: tuple[dict[str, object], ...]
    messages: tuple[dict[str, object], ...]
    cache_breakpoints: tuple[CacheBreakpoint, ...]
    tool_choice: dict[str, str] | None = None

    @property
    def tools_json(self) -> str:
        return _canonical_json(self.tools)

    def provider_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "stream": True,
            "thinking": {"type": "adaptive", "display": self.thinking_display},
            "output_config": {"effort": self.effort},
            "tools": list(self.tools),
            "system": list(self.system),
            "messages": list(self.messages),
        }
        if self.tool_choice is not None:
            body["tool_choice"] = self.tool_choice
        return body


class BrainRequestBuilder:
    def __init__(self, manifest: BrainModelManifest) -> None:
        if not isinstance(manifest, BrainModelManifest):
            raise ValueError("Brain model manifest required")
        self.manifest = manifest

    def build(
        self,
        *,
        messages: tuple[dict[str, object], ...],
        step_seq: int,
        system_prompt: str,
        tool_choice: dict[str, str] | None = None,
        budget_notice: str | None = None,
        effort: Literal["medium", "high", "xhigh"] | None = None,
    ) -> BrainModelRequest:
        if (
            type(messages) is not tuple
            or type(step_seq) is not int
            or step_seq < 1
            or type(system_prompt) is not str
            or not system_prompt.strip()
        ):
            raise ValueError("Brain model request invalid")
        tools = json.loads(_canonical_json(BRAIN_TOOL_SCHEMAS))
        tools[-1]["cache_control"] = {
            "type": "ephemeral",
            "ttl": self.manifest.stable_cache_ttl,
        }
        system = (
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": self.manifest.stable_cache_ttl,
                },
            },
        )
        rendered_messages = json.loads(_canonical_json(messages))
        breakpoints = [
            CacheBreakpoint("tools", "1h"),
            CacheBreakpoint("system", "1h"),
        ]
        capability_index = next(
            (
                index
                for index, message in enumerate(rendered_messages)
                if message.pop("cache_anchor", None) == "capability"
            ),
            None,
        )
        if capability_index is not None:
            _add_message_cache(rendered_messages[capability_index], "5m")
            breakpoints.append(CacheBreakpoint("capability", "5m"))
        if budget_notice is not None:
            if not isinstance(budget_notice, str) or not budget_notice.strip():
                raise ValueError("budget notice invalid")
            rendered_messages.append({"role": "system", "content": budget_notice})
        if rendered_messages:
            _add_message_cache(rendered_messages[-1], "5m")
            breakpoints.append(CacheBreakpoint("rolling", "5m"))
        if len(breakpoints) > 4:
            raise ValueError("cache breakpoint limit exceeded")
        selected_effort = effort or self.manifest.thinking_effort
        if selected_effort not in {"medium", "high", "xhigh"}:
            raise ValueError("Brain effort invalid")
        if tool_choice is not None and tool_choice != {
            "type": "tool",
            "name": "submit_answer",
        }:
            raise ValueError("Brain tool choice invalid")
        return BrainModelRequest(
            model_id=self.manifest.model_id,
            max_tokens=self.manifest.max_output_tokens,
            thinking_display=self.manifest.thinking_display,
            effort=selected_effort,
            tools=tuple(tools),
            system=system,
            messages=tuple(rendered_messages),
            cache_breakpoints=tuple(breakpoints),
            tool_choice=tool_choice,
        )


class BrainModelAdapter(ABC):
    @abstractmethod
    def complete(
        self,
        request: BrainModelRequest,
        *,
        on_thinking_delta: Callable[[ThinkingDelta], None] | None = None,
    ) -> BrainModelResponse:
        raise NotImplementedError


def _add_message_cache(message: dict[str, object], ttl: str) -> None:
    content = message.get("content")
    cache_control = {"type": "ephemeral", "ttl": ttl}
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
    elif isinstance(content, list) and content:
        if not isinstance(content[-1], dict):
            raise ValueError("message content invalid")
        content[-1]["cache_control"] = cache_control
    else:
        raise ValueError("message content invalid")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, UnicodeError, ValueError):
        raise ValueError("Brain model JSON invalid") from None
