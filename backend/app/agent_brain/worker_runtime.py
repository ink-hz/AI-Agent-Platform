from __future__ import annotations

import argparse
import os
import re
import secrets
import signal
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

import httpx
import psycopg

from app.agent_brain.action_service import ActionCommandService
from app.agent_brain.adapters.base import AdapterRegistry
from app.agent_brain.adapters.http_task import HttpTaskAdapter
from app.agent_brain.adapters.metabot_local import MetaBotLocalAdapter
from app.agent_brain.adapters.reference import ReferenceAdapter
from app.agent_brain.adapters.voc import VocBrainAdapter
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
from app.agent_brain.task_identity import SignedTaskTokenIssuer
from app.attachments.grant_service import AttachmentGrantService, TaskGrantRepository
from app.control_plane.crypto import IdentityKeyring
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec
from app.execution_relay.repository import (
    ExecutionRelayError,
    ExecutionRelayRepository,
)
from app.hr.candidate_parser_queue import CandidateParserQueue
from app.hr.candidate_parser_runtime import (
    CandidateParserRuntime,
    PostgresCandidateParserResultReader,
)
from app.hr.candidate_repository import CandidateRepository
from app.local_secrets import read_secret_file
from app.voc_extension.client import VocTaskClient
from app.voc_extension.identity import PlatformVocTokenSigner

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
        agent_checks: Mapping[str, Callable[[], bool]] | None = None,
        freshness_seconds: int = 60,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        # Duck-typed like RuntimeAgentRegistry's own collaborators, so the projection
        # can be tested without standing up a relay DSN.
        selected_checks = dict(agent_checks or {})
        if not hasattr(relay, "has_active_worker") or any(
            type(agent_id) is not str or not agent_id or not callable(check)
            for agent_id, check in selected_checks.items()
        ):
            raise ValueError("execution relay required")
        if type(freshness_seconds) is not int or freshness_seconds <= 0:
            raise ValueError("relay freshness invalid")
        if not callable(now):
            raise ValueError("relay clock invalid")
        self._relay = relay
        self._agent_checks = selected_checks
        self._freshness_seconds = freshness_seconds
        self._now = now

    def for_agent(self, agent_id: str) -> AgentHealthObservation | None:
        if not isinstance(agent_id, str) or not agent_id:
            return None
        check = self._agent_checks.get(agent_id)
        if check is not None:
            try:
                available = check()
            except Exception:
                return None
            if type(available) is not bool:
                return None
            return AgentHealthObservation(
                state="online" if available else "offline",
                sampled_at=self._now(),
                latency_p50_ms=None,
                latency_p95_ms=None,
                sample_count=0,
            )
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


def _candidate_parser_worker_id() -> str:
    configured = os.getenv("PLATFORM_CANDIDATE_PARSER_WORKER_ID", "").strip()
    candidate = configured or "candidate-parser.primary"
    if len(candidate) > 64 or _WORKER_ID.fullmatch(candidate) is None:
        raise RuntimeError("Candidate parser worker configuration unavailable")
    return candidate


def _required_path(name: str) -> Path:
    value = os.getenv(name, "")
    path = Path(value)
    if not value or not path.is_absolute():
        raise RuntimeError("Brain worker configuration unavailable")
    return path


def register_http_task_adapters(
    adapters: AdapterRegistry,
    client: httpx.Client,
    *,
    environ: Mapping[str, str] = os.environ,
) -> tuple[str, ...]:
    """Register configured internal HTTP Agents without changing Catalog exposure."""

    definitions = (
        (
            "fae_http",
            "PLATFORM_FAE_TASK_BASE_URL",
            "ai-fae-agent",
            ("fae.answer",),
        ),
        (
            "admin_http",
            "PLATFORM_ADMIN_TASK_BASE_URL",
            "ai-admin-agent",
            (
                "feedback.own.read",
                "lodging.read",
                "service_catalog.read",
                "shuttle.read",
            ),
        ),
    )
    selected = tuple(
        definition
        for definition in definitions
        if environ.get(definition[1], "").strip()
    )
    if not selected:
        return ()
    key_path = environ.get("PLATFORM_TASK_SIGNING_PRIVATE_KEY_FILE", "").strip()
    key_id = environ.get("PLATFORM_TASK_SIGNING_KEY_ID", "").strip()
    if not key_path or not key_id:
        raise RuntimeError("HTTP Task Adapter configuration unavailable")
    try:
        issuer = SignedTaskTokenIssuer.from_file(key_path, kid=key_id)
    except (RuntimeError, ValueError) as error:
        raise RuntimeError("HTTP Task Adapter configuration unavailable") from error
    registered: list[str] = []
    for kind, environment_name, agent_id, scopes in selected:
        try:
            adapter = HttpTaskAdapter(
                client,
                base_url=environ[environment_name].strip(),
                token_issuer=issuer,
                agent_id=agent_id,
                audience=agent_id,
                authorized_scopes=scopes,
            )
        except ValueError as error:
            raise RuntimeError("HTTP Task Adapter configuration unavailable") from error
        adapters.register(kind, adapter)
        registered.append(kind)
    return tuple(registered)


def register_voc_action_adapter(
    adapters: AdapterRegistry,
    actions: ActionCommandService,
    *,
    environ: Mapping[str, str] = os.environ,
) -> VocTaskClient | None:
    """Register the private durable VOC Action path only with complete config."""

    base_url = environ.get("PLATFORM_VOC_EXTENSION_BASE_URL", "").strip()
    key_file = environ.get("PLATFORM_VOC_EXTENSION_SIGNING_KEY_FILE", "").strip()
    timeout_value = environ.get("PLATFORM_VOC_EXTENSION_TIMEOUT_SECONDS", "").strip()
    if not any((base_url, key_file, timeout_value)):
        return None
    if (
        not base_url
        or not key_file
        or not timeout_value
        or not isinstance(actions, ActionCommandService)
    ):
        raise RuntimeError("VOC Action Adapter configuration unavailable")
    try:
        timeout = float(timeout_value)
        signer = PlatformVocTokenSigner.from_file(key_file)
        client = VocTaskClient(base_url, signer, timeout_seconds=timeout)
        adapters.register("voc_action", VocBrainAdapter(client, actions))
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        raise RuntimeError("VOC Action Adapter configuration unavailable") from error
    return client


def build_runtime() -> tuple[
    BrainLoopRuntime,
    BrainLoopRepository,
    CandidateParserRuntime,
    httpx.Client,
]:
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
    candidate_queue = CandidateParserQueue(CandidateRepository(database_url))
    candidate_parser_runtime = CandidateParserRuntime(
        candidate_queue,
        PostgresCandidateParserResultReader(database_url, codec),
        worker_id=_candidate_parser_worker_id(),
    )
    action_commands = ActionCommandService(
        database_url,
        content_codec=codec,
        dsn_purpose="brain",
    )
    attachment_grants = AttachmentGrantService(
        TaskGrantRepository(
            database_url,
            content_codec=codec,
            dsn_purpose="brain",
        ),
        None,
    )
    relay = ExecutionRelayRepository(
        database_url, content_codec=codec, dsn_purpose="brain"
    )
    adapters = AdapterRegistry()
    adapters.register("reference", ReferenceAdapter())
    adapters.register("metabot_local", MetaBotLocalAdapter(relay))
    voc_task_client = register_voc_action_adapter(adapters, action_commands)
    register_http_task_adapters(adapters, client)
    authorization = AgentUseAuthorization(database_url, dsn_purpose="brain")
    registry = RuntimeAgentRegistry(
        authorization=authorization,
        health=RelayAgentHealth(
            relay,
            agent_checks=(
                {"voc": voc_task_client.is_healthy}
                if voc_task_client is not None
                else None
            ),
        ),
        registered_adapter_kinds=adapters.registered_kinds,
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
            action_commands=action_commands,
            attachment_grants=attachment_grants,
        ),
        repository,
        candidate_parser_runtime,
        client,
    )


def tick(
    mode: WorkerMode,
    runtime: BrainLoopRuntime,
    repository,
    *,
    candidate_parser_runtime: CandidateParserRuntime | None = None,
) -> int:
    def run_phase(name: str, operation) -> int:
        try:
            changed = operation()
            repository.heartbeat(name, status="healthy")
            return changed
        except Exception:
            try:
                repository.heartbeat(
                    name,
                    status="degraded",
                    error_code="worker_pass_failed",
                )
            except Exception:
                pass
            return 0

    def brain_tick() -> int:
        return int(runtime.advance_one()) + runtime.scan_settled_batches()

    def adapter_tick() -> int:
        return (
            int(runtime.dispatch_one())
            + int(runtime.reconcile_one())
            + runtime.reconcile_adapter_tasks("metabot_local")
            + runtime.reconcile_cancellations()
        )

    def reaper_tick() -> int:
        return (
            runtime.expire_actions()
            + repository.settle_active_waits(limit=100)
            + repository.expire_leases(limit=100)
            + repository.expire_delivery_leases(limit=100)
            + repository.expire_waiting_users(limit=100)
            + repository.terminalize_blocked_tasks(limit=100)
            + repository.expire_task_deadlines(limit=100)
            + repository.erase_expired_model_responses(limit=100)
            + repository.erase_expired_conversations(limit=100)
        )

    changed = 0
    if mode in {"brain", "all"}:
        changed += run_phase("agent-brain-step", brain_tick)
        if candidate_parser_runtime is not None:
            changed += run_phase("hr-candidate-parser", candidate_parser_runtime.tick)
    if mode in {"adapter", "all"}:
        changed += run_phase("agent-brain-adapter", adapter_tick)
    if mode in {"reaper", "all"}:
        changed += run_phase("agent-brain-reaper", reaper_tick)
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
                ((
                    "agent-brain-step", "hr-candidate-parser",
                    "agent-brain-adapter", "agent-brain-reaper",
                ),),
            ).fetchone()[0]
        return 0 if count == 4 else 1
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
        runtime, repository, candidate_parser_runtime, client = build_runtime()
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
                changed = tick(
                    mode,
                    runtime,
                    repository,
                    candidate_parser_runtime=candidate_parser_runtime,
                )
            except Exception:
                for name in (
                    "agent-brain-step",
                    "hr-candidate-parser",
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
