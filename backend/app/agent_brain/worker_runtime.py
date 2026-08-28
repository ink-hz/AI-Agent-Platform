from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import signal
import time
from typing import Callable, Literal, cast

import httpx
import psycopg

from app.agent_brain.adapters.base import AdapterRegistry
from app.agent_brain.adapters.metabot_local import MetaBotLocalAdapter
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.authorization import AgentUseAuthorization
from app.agent_brain.loop_repository import BrainLoopRepository
from app.agent_brain.loop_runtime import BrainLoopRuntime
from app.agent_brain.model_adapter import BrainModelManifest, BrainRequestBuilder
from app.agent_brain.prompt import BrainSystemPrompt
from app.agent_brain.runtime_registry import (
    AgentHealthObservation,
    RuntimeAgentRegistry,
)
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec
from app.execution_relay.repository import (
    ExecutionRelayError,
    ExecutionRelayRepository,
)
from app.local_secrets import read_secret_file


WorkerMode = Literal["brain", "adapter", "reaper", "all"]

# Mirrors the brain_steps.lease_worker_id check in control migration 041; a test
# asserts the generated identity is accepted by the repository validator.
_WORKER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# A single Opus 5 Step routinely runs past a minute, and expire_leases returns any
# expired Step to the queue, so the lease must outlive a normal call. The runtime
# also renews it while the response streams.
_STEP_LEASE_SECONDS = 180


def validate_worker_mode(value: str) -> WorkerMode:
    if value not in {"brain", "adapter", "reaper", "all"}:
        raise ValueError("Brain worker mode invalid")
    return value  # type: ignore[return-value]


class RelayAgentHealth:
    """Project real execution-relay worker liveness into Brain Agent availability.

    list_agents reported "unknown" for every Agent before this, because the Brain
    worker was wired to a stub health source. The relay already tracks whether the
    local worker hosting an Agent is alive -- the Adapter uses the same call to
    decide whether to dispatch -- so the Brain could not tell a healthy Agent from
    a powered-off Mac.
    """

    def __init__(
        self,
        relay: ExecutionRelayRepository,
        *,
        freshness_seconds: int = 60,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        # Duck-typed like RuntimeAgentRegistry's own collaborators, so the projection
        # can be tested without standing up a relay DSN.
        if not hasattr(relay, "has_active_worker"):
            raise ValueError("execution relay required")
        if type(freshness_seconds) is not int or freshness_seconds <= 0:
            raise ValueError("relay freshness invalid")
        if not callable(now):
            raise ValueError("relay clock invalid")
        self._relay = relay
        self._freshness_seconds = freshness_seconds
        self._now = now

    def for_agent(self, agent_id: str) -> AgentHealthObservation | None:
        if not isinstance(agent_id, str) or not agent_id:
            return None
        try:
            available = self._relay.has_active_worker(
                agent_id, freshness_seconds=self._freshness_seconds
            )
        except ExecutionRelayError:
            return None
        return AgentHealthObservation(
            state="online" if available else "offline",
            sampled_at=self._now(),
            latency_p50_ms=None,
            latency_p95_ms=None,
            sample_count=0,
        )


def _worker_id() -> str:
    """Return a per-process Brain worker identity.

    Every Brain worker shared the constant "platform-brain" before this. Because
    lease_step re-leases any Step whose lease has expired, two workers with the
    same identity could both pass commit_model_step's lease check after each paid
    for the same model call.
    """

    configured = os.getenv("PLATFORM_BRAIN_WORKER_ID", "").strip()
    candidate = configured or f"platform-brain.{secrets.token_hex(4)}"
    if _WORKER_ID.fullmatch(candidate) is None:
        raise RuntimeError("Brain worker configuration unavailable")
    return candidate


def _required_path(name: str) -> Path:
    value = os.getenv(name, "")
    path = Path(value)
    if not value or not path.is_absolute():
        raise RuntimeError("Brain worker configuration unavailable")
    return path


def build_runtime() -> tuple[BrainLoopRuntime, BrainLoopRepository, httpx.Client]:
    database_path = _required_path("PLATFORM_BRAIN_DATABASE_URL_FILE")
    content_path = _required_path("PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE")
    manifest_path = _required_path("PLATFORM_BRAIN_MODEL_MANIFEST")
    prompt_path = _required_path("PLATFORM_BRAIN_SYSTEM_PROMPT")
    api_key_path = _required_path("PLATFORM_BRAIN_PROVIDER_API_KEY_FILE")
    base_url = os.getenv("PLATFORM_BRAIN_PROVIDER_BASE_URL", "").strip()
    auth_scheme_value = os.getenv(
        "PLATFORM_BRAIN_PROVIDER_AUTH_SCHEME", "x-api-key"
    ).strip()
    if auth_scheme_value not in {"x-api-key", "bearer"}:
        raise RuntimeError("Brain worker configuration unavailable")
    auth_scheme = cast(Literal["x-api-key", "bearer"], auth_scheme_value)
    database_url = read_secret_file(str(database_path))
    validate_control_dsn(database_url, purpose="brain")
    keyring = IdentityKeyring.from_file(
        str(content_path),
        expected_purpose="platform-content-encryption",
        expected_key_length=32,
    )
    codec = ContentCodec(keyring)
    manifest = BrainModelManifest.load(manifest_path)
    prompt = BrainSystemPrompt.load(
        prompt_path, expected_sha256=manifest.system_prompt_sha256
    )
    client = httpx.Client(
        timeout=httpx.Timeout(310.0, connect=10.0),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    )
    model = AnthropicMessagesAdapter.from_secret_file(
        base_url=base_url,
        api_key_file=str(api_key_path),
        auth_scheme=auth_scheme,
        client=client,
    )
    repository = BrainLoopRepository(database_url, content_codec=codec)
    relay = ExecutionRelayRepository(
        database_url, content_codec=codec, dsn_purpose="brain"
    )
    adapters = AdapterRegistry()
    adapters.register("reference", ReferenceAdapter())
    adapters.register("metabot_local", MetaBotLocalAdapter(relay))
    authorization = AgentUseAuthorization(database_url, dsn_purpose="brain")
    registry = RuntimeAgentRegistry(
        authorization=authorization,
        health=RelayAgentHealth(relay),
        registered_adapter_kinds=("reference", "metabot_local"),
    )
    return (
        BrainLoopRuntime(
            repository=repository,
            model=model,
            request_builder=BrainRequestBuilder(manifest),
            system_prompt=prompt,
            runtime_registry=registry,
            adapters=adapters,
            worker_id=_worker_id(),
            lease_seconds=_STEP_LEASE_SECONDS,
        ),
        repository,
        client,
    )


def tick(mode: WorkerMode, runtime: BrainLoopRuntime, repository) -> int:
    changed = 0
    if mode in {"brain", "all"}:
        changed += int(runtime.advance_one())
        changed += runtime.scan_settled_batches()
        repository.heartbeat("agent-brain-step", status="healthy")
    if mode in {"adapter", "all"}:
        changed += int(runtime.dispatch_one())
        changed += int(runtime.reconcile_one())
        changed += runtime.reconcile_adapter_tasks("metabot_local")
        changed += runtime.reconcile_cancellations()
        repository.heartbeat("agent-brain-adapter", status="healthy")
    if mode in {"reaper", "all"}:
        changed += repository.expire_leases(limit=100)
        changed += repository.expire_delivery_leases(limit=100)
        changed += repository.expire_waiting_users(limit=100)
        changed += repository.erase_expired_model_responses(limit=100)
        changed += repository.erase_expired_conversations(limit=100)
        repository.heartbeat("agent-brain-reaper", status="healthy")
    return changed


def _healthcheck() -> int:
    try:
        database_url = read_secret_file(
            str(_required_path("PLATFORM_BRAIN_DATABASE_URL_FILE"))
        )
        validate_control_dsn(database_url, purpose="brain")
        with psycopg.connect(
            database_url,
            connect_timeout=3,
            options="-c statement_timeout=3000 -c timezone=UTC",
        ) as connection:
            count = connection.execute(
                "select count(*) from platform_control.worker_heartbeats "
                "where worker_name=any(%s) and status='healthy' "
                "and last_seen_at>clock_timestamp()-interval '60 seconds'",
                (["agent-brain-step", "agent-brain-adapter", "agent-brain-reaper"],),
            ).fetchone()[0]
        return 0 if count == 3 else 1
    except Exception:
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("mode")
    selected = parser.parse_args(argv).mode
    if selected == "healthcheck":
        return _healthcheck()
    try:
        mode = validate_worker_mode(selected)
        runtime, repository, client = build_runtime()
    except Exception:
        return 1
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                changed = tick(mode, runtime, repository)
            except Exception:
                for name in (
                    "agent-brain-step",
                    "agent-brain-adapter",
                    "agent-brain-reaper",
                ):
                    try:
                        repository.heartbeat(
                            name, status="degraded", error_code="worker_pass_failed"
                        )
                    except Exception:
                        pass
                time.sleep(1.0)
                continue
            time.sleep(0.05 if changed else 0.5)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
