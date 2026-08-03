from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .credentials import Credential, CredentialResolver, CredentialUnavailable


@dataclass(frozen=True)
class ReplayInput:
    issue_id: UUID
    issue_link_id: UUID
    agent_id: str
    question: str
    prior_turns: list[dict]
    attachment_manifest: list[dict]


@dataclass
class RuntimeExchange:
    target_safe: bool
    health: dict
    expected_version: str
    expected_git_sha: str
    execution_status: str
    answer: str
    sources: list[dict]
    done: dict
    trace_id: str


@dataclass(frozen=True)
class RuntimeGateResult:
    passed: bool
    reason: str = ""


class ReplayRun(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


def _loop(done: dict) -> dict:
    value = done.get("loop", {})
    return value if isinstance(value, dict) else {}


def evaluate_runtime_gate(exchange: RuntimeExchange) -> RuntimeGateResult:
    if not exchange.target_safe:
        return RuntimeGateResult(False, "unsafe_replay_target")
    build = exchange.health.get("build")
    if (
        not isinstance(build, dict)
        or build.get("available") is not True
        or build.get("git_sha") != exchange.expected_git_sha
        or (
            exchange.expected_version
            and build.get("release_name") != exchange.expected_version
        )
    ):
        return RuntimeGateResult(False, "build_identity_mismatch")
    loop = _loop(exchange.done)
    if (
        exchange.done.get("protocol_error")
        or loop.get("protocol_error")
        or exchange.done.get("request_error")
        or exchange.done.get("error")
    ):
        return RuntimeGateResult(False, "protocol_error")
    if exchange.done.get("fallback_used") is not False:
        return RuntimeGateResult(False, "fallback_used")
    if loop.get("truncation_rounds", 0) != 0:
        return RuntimeGateResult(False, "truncated")
    if exchange.execution_status != "succeeded":
        return RuntimeGateResult(False, "protocol_error")
    if not exchange.answer.strip():
        return RuntimeGateResult(False, "empty_answer")
    if not exchange.trace_id.strip():
        return RuntimeGateResult(False, "trace_missing")
    echo = loop.get("provider_model_echo")
    actual_model = loop.get("actual_provider_model", "")
    configured_model = loop.get("configured_model", "")
    if (
        not isinstance(echo, dict)
        or echo.get("complete") is not True
        or echo.get("consistent") is not True
        or not actual_model
    ):
        return RuntimeGateResult(False, "model_echo_unavailable")
    if not configured_model or actual_model != configured_model:
        return RuntimeGateResult(False, "actual_model_mismatch")
    return RuntimeGateResult(True)


def parse_sse(lines: Iterable[str]) -> list[dict]:
    events: list[dict] = []
    event_name = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = "message"
            return
        raw = "\n".join(data_lines)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"raw": raw, "protocol_error": "invalid_json"}
        events.append({"event": event_name, "data": data})
        event_name = "message"
        data_lines = []

    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if line == "":
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    flush()
    return events


def _origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if any(character in parsed.hostname for character in "<> "):
        return None
    return (
        parsed.scheme,
        parsed.hostname.casefold(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReplayRunner:
    def __init__(
        self,
        repository,
        registry,
        *,
        http_client=None,
        credential_resolver: CredentialResolver | None = None,
        request_timeout: float = 1200,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.http_client = http_client or httpx.Client()
        self._owns_http_client = http_client is None
        self.credential_resolver = credential_resolver or CredentialResolver()
        self.request_timeout = request_timeout

    def close(self) -> None:
        if self._owns_http_client:
            self.http_client.close()

    @staticmethod
    def validate_attachment(manifest: dict) -> bool:
        if manifest.get("approved_for_dev") is not True:
            return False
        raw_path = manifest.get("path")
        expected = manifest.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected, str):
            return False
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            return False
        path = Path(raw_path)
        if not path.is_file() or path.is_symlink():
            return False
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == expected

    @staticmethod
    def _static_target_safe(
        target,
        production_url: str,
        production_health_url: str,
    ) -> bool:
        dev_origin = _origin(target.api_base)
        health_origin = _origin(target.health_url)
        prod_origin = _origin(production_url)
        prod_health_origin = _origin(production_health_url)
        if (
            dev_origin is None
            or health_origin is None
            or prod_origin is None
            or prod_health_origin is None
        ):
            return False
        if dev_origin != health_origin:
            return False
        if dev_origin in {prod_origin, prod_health_origin}:
            return False
        return dev_origin[1] not in {prod_origin[1], prod_health_origin[1]}

    def _health_safe(
        self,
        target,
        production_health_url: str,
        credential: Credential,
    ) -> tuple[bool, dict]:
        try:
            dev_response = self.http_client.get(
                target.health_url,
                headers=credential.headers(),
                timeout=10,
            )
            dev_response.raise_for_status()
            prod_response = self.http_client.get(
                production_health_url,
                timeout=10,
            )
            prod_response.raise_for_status()
            dev_health = dev_response.json()
            prod_health = prod_response.json()
        except Exception:
            return False, {}
        if not isinstance(dev_health, dict) or not isinstance(prod_health, dict):
            return False, {}
        dev_environment = str(dev_health.get("environment", "")).casefold()
        prod_environment = str(prod_health.get("environment", "")).casefold()
        safe = (
            dev_health.get("status") == "ok"
            and prod_health.get("status") == "ok"
            and dev_environment in {"dev", "development", "test"}
            and prod_environment == "production"
            and dev_environment != prod_environment
        )
        return safe, dev_health if safe else {}

    def _finish_blocked(
        self,
        replay_id: UUID,
        *,
        reason: str,
        actor: str,
        health: dict | None = None,
        expected: dict | None = None,
        safety_stage: str = "",
    ) -> ReplayRun:
        health = health or {}
        expected = expected or {}
        build = health.get("build") if isinstance(health.get("build"), dict) else {}
        result = {
            "actual_version": build.get("release_name", ""),
            "actual_git_sha": build.get("git_sha", ""),
            "configured_model": health.get("llm_model", ""),
            "actual_model": "",
            "actual_model_source": "",
            "answer": "",
            "sources": [],
            "done": {"safety_stage": safety_stage} if safety_stage else {},
            "trace_id": "",
            "duration_ms": 0,
            "execution_status": "blocked",
            "runtime_gate": "failed",
            "runtime_failure_reason": reason,
        }
        return ReplayRun(
            self.repository.finish_replay(
                replay_id,
                result,
                actor=actor,
            )
        )

    def _upload_attachments(
        self,
        target,
        credential: Credential,
        manifests: list[dict],
    ) -> list[str] | None:
        if not manifests:
            return []
        if not all(self.validate_attachment(item) for item in manifests):
            return None
        files = []
        handles = []
        try:
            for manifest in manifests:
                path = Path(manifest["path"])
                handle = path.open("rb")
                handles.append(handle)
                media_type = manifest.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files.append(("files", (path.name, handle, media_type)))
            response = self.http_client.post(
                f"{target.api_base.rstrip('/')}/attachments",
                files=files,
                headers=credential.headers(),
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        finally:
            for handle in handles:
                handle.close()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        ids = [
            item.get("attachment", {}).get("attachment_id")
            for item in results
            if isinstance(item, dict) and item.get("ok") is True
        ]
        if len(ids) != len(manifests) or not all(ids):
            return None
        return ids

    def _chat(
        self,
        target,
        credential: Credential,
        *,
        question: str,
        session_id: str | None,
        request_id: str,
        attachment_ids: list[str],
    ) -> dict:
        payload = {
            "session_id": session_id,
            "message": question,
            "channel": "fae",
            "client_request_id": request_id[:128],
            "attachment_ids": attachment_ids,
        }
        with self.http_client.stream(
            "POST",
            f"{target.api_base.rstrip('/')}/chat",
            json=payload,
            headers=credential.headers(),
            timeout=self.request_timeout,
        ) as response:
            response.raise_for_status()
            events = parse_sse(response.iter_lines())
        session = next(
            (
                event["data"].get("session_id")
                for event in events
                if event["event"] == "session" and isinstance(event["data"], dict)
            ),
            None,
        )
        answer = "".join(
            str(event["data"].get("delta", ""))
            for event in events
            if event["event"] == "text_delta" and isinstance(event["data"], dict)
        )
        source_events = [
            event["data"] for event in events if event["event"] == "sources"
        ]
        sources = source_events[-1] if source_events and isinstance(source_events[-1], list) else []
        done_events = [
            event["data"]
            for event in events
            if event["event"] == "done" and isinstance(event["data"], dict)
        ]
        done = done_events[-1] if done_events else {"protocol_error": "done_event_missing"}
        if any(
            isinstance(event["data"], dict) and event["data"].get("protocol_error")
            for event in events
        ) or any(event["event"] == "error" for event in events):
            done["protocol_error"] = "invalid_sse_event"
        return {
            "session_id": session or done.get("session_id"),
            "answer": answer,
            "sources": sources,
            "done": done,
        }

    def run(
        self,
        issue_link_id: UUID,
        *,
        idempotency_key: str,
        actor: str,
    ) -> ReplayRun:
        replay_input = self.repository.load_replay_input(issue_link_id)
        if replay_input is None:
            raise ValueError("active issue link not found")
        deployment = self.repository.get_verified_deployment(replay_input.issue_id)
        agent = self.registry.get_agent_by_flywheel_id(replay_input.agent_id)
        target = agent.replay_targets[0] if agent and len(agent.replay_targets) == 1 else None
        production_url = (agent.api_base or agent.health.url) if agent else ""
        expected = {
            "target_url_fingerprint": _fingerprint(target.api_base if target else ""),
            "expected_version": (deployment or {}).get("version", ""),
            "expected_git_sha": (deployment or {}).get("git_sha", ""),
            "question": replay_input.question,
            "context_snapshot": replay_input.prior_turns,
            "attachment_manifest": replay_input.attachment_manifest,
        }
        self.repository.expire_stale_replays(
            issue_link_id,
            timeout_seconds=self.request_timeout,
            actor=actor,
        )
        record, created = self.repository.create_or_get_replay(
            issue_link_id,
            idempotency_key=idempotency_key,
            expected=expected,
            actor=actor,
        )
        if not created:
            return ReplayRun(record)
        replay_id = record["id"]
        if (
            target is None
            or deployment is None
            or not replay_input.question.strip()
        ):
            return self._finish_blocked(
                replay_id,
                reason="missing_replay_input",
                actor=actor,
                expected=expected,
                safety_stage="replay_input",
            )
        if not self._static_target_safe(
            target,
            production_url,
            agent.health.url,
        ):
            return self._finish_blocked(
                replay_id,
                reason="unsafe_replay_target",
                actor=actor,
                expected=expected,
                safety_stage="static_target",
            )
        try:
            credential = self.credential_resolver.resolve(target.credential_ref)
        except CredentialUnavailable:
            return self._finish_blocked(
                replay_id,
                reason="unsafe_replay_target",
                actor=actor,
                expected=expected,
                safety_stage="credential",
            )
        target_safe, health = self._health_safe(
            target,
            agent.health.url,
            credential,
        )
        if not target_safe:
            return self._finish_blocked(
                replay_id,
                reason="unsafe_replay_target",
                actor=actor,
                expected=expected,
                safety_stage="health_identity",
            )
        attachment_ids = self._upload_attachments(
            target,
            credential,
            replay_input.attachment_manifest,
        )
        if attachment_ids is None:
            return self._finish_blocked(
                replay_id,
                reason="missing_replay_input",
                actor=actor,
                health=health,
                expected=expected,
                safety_stage="attachment_input",
            )

        started = monotonic()
        session_id = None
        try:
            for index, turn in enumerate(replay_input.prior_turns):
                prior = self._chat(
                    target,
                    credential,
                    question=turn["question"],
                    session_id=session_id,
                    request_id=f"{idempotency_key}-context-{index}",
                    attachment_ids=[],
                )
                session_id = prior["session_id"]
                if not session_id or prior["done"].get("protocol_error"):
                    raise RuntimeError("prior turn replay failed")
            exchange = self._chat(
                target,
                credential,
                question=replay_input.question,
                session_id=session_id,
                request_id=idempotency_key,
                attachment_ids=attachment_ids,
            )
            execution_status = "succeeded"
        except Exception:
            exchange = {
                "answer": "",
                "sources": [],
                "done": {"protocol_error": "request_or_stream_failed"},
                "session_id": session_id,
            }
            execution_status = "failed"
        duration_ms = int((monotonic() - started) * 1000)
        done = exchange["done"]
        trace_id = str(done.get("trace_id", ""))
        runtime = RuntimeExchange(
            target_safe=True,
            health=health,
            expected_version=expected["expected_version"],
            expected_git_sha=expected["expected_git_sha"],
            execution_status=execution_status,
            answer=exchange["answer"],
            sources=exchange["sources"],
            done=done,
            trace_id=trace_id,
        )
        gate = evaluate_runtime_gate(runtime)
        loop = _loop(done)
        build = health.get("build", {})
        result = {
            "actual_version": build.get("release_name", ""),
            "actual_git_sha": build.get("git_sha", ""),
            "configured_model": loop.get("configured_model", health.get("llm_model", "")),
            "actual_model": loop.get("actual_provider_model", ""),
            "actual_model_source": "provider_message_start" if loop.get("actual_provider_model") else "",
            "answer": exchange["answer"],
            "sources": exchange["sources"],
            "done": done,
            "trace_id": trace_id,
            "duration_ms": duration_ms,
            "execution_status": execution_status,
            "runtime_gate": "passed" if gate.passed else "failed",
            "runtime_failure_reason": gate.reason,
        }
        return ReplayRun(
            self.repository.finish_replay(replay_id, result, actor=actor)
        )
