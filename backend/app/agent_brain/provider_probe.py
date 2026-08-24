from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import httpx

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import (
    BrainModelAdapter,
    BrainModelManifest,
    BrainRequestBuilder,
)
from app.agent_brain.prompt import BrainPromptIntegrityError, BrainSystemPrompt


class ProviderCapabilityError(RuntimeError):
    pass


def run_probe(
    manifest_path: Path,
    *,
    system_prompt: str,
    provider: BrainModelAdapter,
) -> dict[str, object]:
    manifest = BrainModelManifest.load(manifest_path)
    builder = BrainRequestBuilder(manifest)
    base_messages = ({"role": "user", "content": "probe"},)
    normal = provider.complete(
        builder.build(
            messages=base_messages,
            step_seq=1,
            system_prompt=system_prompt,
        )
    )
    forced = provider.complete(
        builder.build(
            messages=base_messages,
            step_seq=2,
            system_prompt=system_prompt,
            tool_choice={"type": "tool", "name": "submit_answer"},
        )
    )
    if not any(
        block.get("type") == "tool_use"
        and block.get("name") == "submit_answer"
        for block in forced.content_blocks
    ):
        raise ProviderCapabilityError("forced_tool_choice unsupported")
    responses = [normal, forced]
    for effort in ("medium", "high", "xhigh"):
        responses.append(
            provider.complete(
                builder.build(
                    messages=(
                        {"role": "user", "content": "probe"},
                        {
                            "role": "system",
                            "content": "capability-version=probe",
                            "cache_anchor": "capability",
                        },
                    ),
                    step_seq=len(responses) + 1,
                    system_prompt=system_prompt,
                    effort=effort,
                )
            )
        )
    if any(
        block.get("type") == "thinking" and block.get("thinking")
        for response in responses
        for block in response.content_blocks
    ):
        raise ProviderCapabilityError("omitted_thinking unsupported")
    if not all(response.stop_reason == "tool_use" for response in responses):
        raise ProviderCapabilityError("streaming tool use unsupported")
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    prompt_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    return {
        "manifest_sha256": manifest_digest,
        "system_prompt_sha256": prompt_digest,
        "provider_request_ids": [response.provider_request_id for response in responses],
        "supported": {
            "streaming": True,
            "forced_tool_choice": True,
            "omitted_thinking": True,
            "mid_conversation_system": True,
            "one_hour_cache": True,
            "one_million_context": manifest.context_window == 1_000_000,
        },
        "efforts": ["medium", "high", "xhigh"],
        "stable_cache_ttl": manifest.stable_cache_ttl,
        "rolling_cache_ttl": manifest.rolling_cache_ttl,
        "usage": [response.usage.__dict__ if hasattr(response.usage, "__dict__") else {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
            "cache_read_input_tokens": response.usage.cache_read_input_tokens,
        } for response in responses],
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--system-prompt", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    arguments = parser.parse_args()
    provider_base_url = os.getenv("PLATFORM_BRAIN_PROVIDER_BASE_URL", "").strip()
    provider_api_key_file = os.getenv(
        "PLATFORM_BRAIN_PROVIDER_API_KEY_FILE", ""
    ).strip()
    if not provider_base_url or not provider_api_key_file:
        raise ProviderCapabilityError("Brain model runtime disabled")
    manifest = BrainModelManifest.load(arguments.manifest)
    try:
        system_prompt = BrainSystemPrompt.load(
            arguments.system_prompt,
            expected_sha256=manifest.system_prompt_sha256,
        ).text
    except BrainPromptIntegrityError:
        raise ProviderCapabilityError("system prompt unavailable") from None
    with httpx.Client(timeout=httpx.Timeout(330.0, connect=10.0)) as client:
        provider = AnthropicMessagesAdapter.from_secret_file(
            base_url=provider_base_url,
            api_key_file=provider_api_key_file,
            client=client,
        )
        evidence = run_probe(
            arguments.manifest,
            system_prompt=system_prompt,
            provider=provider,
        )
    output = arguments.evidence_out
    if not output.is_absolute() or output.exists():
        raise ProviderCapabilityError("evidence output path invalid")
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(output)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProviderCapabilityError("evidence output unavailable") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
