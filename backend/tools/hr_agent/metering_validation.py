"""Bounded synthetic same-gateway metering validation for the HR model port."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.hr_agent.context import estimate_input_tokens
from app.hr_agent.model import ConfiguredHttpModelPort, ModelError
from app.hr_agent.types import ModelRequest, canonical_json

PROFILE = Path("/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json")
USAGE_KEYS = {
    "input_tokens", "output_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "prompt_tokens", "completion_tokens",
}


def _cases() -> list[dict]:
    schema = {"type": "function", "function": {"name": "classify_public_note", "description": "Classify a synthetic public note.", "parameters": {"type": "object", "properties": {"label": {"type": "string", "enum": ["alpha", "beta"]}}, "required": ["label"], "additionalProperties": False}}}
    long_cn = "这是一段用于计量验证的公开合成文本，不包含个人信息、公司资料或业务结论。" * 180
    return [
        {"case": "english", "messages": ({"role": "user", "content": "Reply with one short sentence describing a fictional blue kite."},), "tools": ()},
        {"case": "chinese", "messages": ({"role": "user", "content": "请用一句短句描述一座虚构的绿色小桥。"},), "tools": ()},
        {"case": "mixed_json", "messages": ({"role": "user", "content": 'Summarize this synthetic JSON briefly: {"城市":"示例城","count":7,"active":true}'},), "tools": ()},
        {"case": "tool_schema", "messages": ({"role": "user", "content": "Use the tool to classify this synthetic note as alpha."},), "tools": (schema,)},
        {"case": "multi_turn", "messages": ({"role": "user", "content": "Remember the synthetic word cedar."}, {"role": "assistant", "content": "I will remember cedar."}, {"role": "user", "content": "What was the synthetic word? Answer briefly."}), "tools": ()},
        {"case": "long_chinese", "messages": ({"role": "user", "content": long_cn + "\n请只回复：收到。"},), "tools": ()},
    ]


def prepare_manifest(path: Path, *, profile_revision: str) -> dict:
    requests = []
    for case in _cases():
        payload = {"messages": case["messages"], "tools": case["tools"]}
        characters = sum(len(c) for m in case["messages"] for c in [str(m.get("content", ""))])
        if characters > 12_000:
            raise ValueError("input_character_limit")
        requests.append({
            "case": case["case"], "request_sha256": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
            "unicode_characters": characters, "max_output_tokens": 128, "deadline_seconds": 60,
            "messages": list(case["messages"]), "tools": list(case["tools"]),
        })
    manifest = {"schema": "hr-metering-synthetic-v1", "profile_revision": profile_revision, "call_budget": 6, "requests": requests}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def collect_metering_events(events) -> dict:
    usage: dict[str, int] = {}
    models: list[str] = []
    stop_reason = None
    for event in events:
        if event.type == "usage":
            raw = event.payload.get("raw", event.payload)
            if isinstance(raw, dict):
                metadata = raw.get("_response_metadata")
                if isinstance(metadata, dict) and isinstance(metadata.get("reported_models"), list):
                    models.extend(x for x in metadata["reported_models"] if isinstance(x, str))
                for key in USAGE_KEYS:
                    value = raw.get(key)
                    if type(value) is int and value >= 0:
                        usage[key] = usage.get(key, 0) + value
        elif event.type == "stop" and isinstance(event.payload.get("reason"), str):
            stop_reason = event.payload["reason"]
    if not usage:
        raise ValueError("usage_missing")
    return {"response_model": models[-1] if models else None, "usage": usage, "raw_usage": dict(usage), "stop_reason": stop_reason}


def run(output_dir: Path) -> dict:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    profile = {**profile, "timeout_seconds": min(float(profile.get("timeout_seconds", 60)), 60)}
    port = ConfiguredHttpModelPort.from_mapping(profile)
    manifest = prepare_manifest(output_dir / "request-manifest.json", profile_revision=port.profile_revision)
    attempts = []
    for index, item in enumerate(manifest["requests"], 1):
        messages, tools = tuple(item["messages"]), tuple(item["tools"])
        record = {"attempt": index, "case": item["case"], "request_sha256": item["request_sha256"], "estimated_input_tokens": estimate_input_tokens(messages, tools, str(profile["tokenizer"]))}
        try:
            events = port.stream(ModelRequest(attempt_id=uuid4(), purpose="metering_validation", profile_id=str(profile["id"]), messages=messages, tools=tools, max_output_tokens=128, deadline_seconds=60))
            record.update({"status": "succeeded", **collect_metering_events(events)})
            if record["response_model"] is None:
                record["status"] = "failed"
                record["failure"] = "response_model_missing"
        except (ModelError, ValueError) as exc:
            record.update(status="failed", failure=getattr(exc, "code", type(exc).__name__))
        attempts.append(record)
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "configured_model": profile["model"], "profile_revision": port.profile_revision, "generation_calls": len(attempts), "attempts": attempts}
    (output_dir / "attempts.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = run(args.output_dir)
    print(json.dumps({"generation_calls": result["generation_calls"], "statuses": [x["status"] for x in result["attempts"]]}))


if __name__ == "__main__":
    main()
