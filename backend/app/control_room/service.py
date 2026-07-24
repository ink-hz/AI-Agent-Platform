from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.fleet.catalog import AgentCatalog

from .models import (
    AgentLifecycle,
    AgentRuntime,
    AgentRuntimeView,
    Readiness,
    ReadinessStatus,
    RuntimeEvidence,
)


RUNTIME_STALE_AFTER = timedelta(seconds=120)


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def resolve_readiness(
    *,
    process_status: str,
    channel_status: str,
    active_turns: int | None,
    runtime_fresh: bool,
) -> tuple[ReadinessStatus, str]:
    if process_status == "offline":
        return "Offline", "Runtime process is unavailable"
    if process_status == "degraded" or channel_status in {
        "failed", "reconnecting"
    }:
        return "Limited", "Runtime or primary channel is degraded"
    if process_status not in {"healthy", "online", "active"}:
        return "Unknown", "Runtime state has not been established"
    if not runtime_fresh or channel_status != "connected":
        return "Unknown", "Current channel readiness has not been observed"
    if active_turns is not None and active_turns > 0:
        return "Busy", "Runtime is available and processing work"
    return "Ready", "Runtime and primary channel are available"


class ControlRoomService:
    def __init__(
        self,
        catalog: AgentCatalog,
        cluster_monitor,
        remote_monitor,
        observability_service,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._catalog = catalog
        self._cluster_monitor = cluster_monitor
        self._remote_monitor = remote_monitor
        self._observability = observability_service
        self._now = now

    async def get_runtime(self, agent_id: str) -> AgentRuntimeView | None:
        profiles = {profile.id: profile for profile in self._catalog.all_profiles()}
        profile = profiles.get(agent_id)
        if profile is None:
            return None

        cluster = self._cluster_monitor.snapshot()
        remote = self._remote_monitor.snapshot()
        instance = next(
            (item for item in cluster.instances if item.id == agent_id), None
        )
        if instance is None:
            instance = next(
                (item for item in remote.agents if item.id == agent_id), None
            )

        trace = None
        trace_available = True
        try:
            trace = await self._observability.latest_runtime_observation(agent_id)
        except Exception:
            trace_available = False

        observed_at = _parse_time(
            getattr(instance, "runtime_observed_at", None)
            or getattr(instance, "checked_at", None)
        )
        now = self._now()
        runtime_fresh = (
            observed_at is not None
            and timedelta(0) <= now - observed_at <= RUNTIME_STALE_AFTER
        )
        process_status = getattr(instance, "status", "unknown")
        channel_status = getattr(instance, "channel_status", "unknown")
        active_turns = getattr(instance, "active_turns", None)
        readiness_status, readiness_reason = resolve_readiness(
            process_status=process_status,
            channel_status=channel_status,
            active_turns=active_turns,
            runtime_fresh=runtime_fresh,
        )

        runtime_model = (
            getattr(instance, "observed_model", None)
            or getattr(instance, "model", None)
        )
        declared_model = getattr(instance, "declared_model", None)
        if runtime_model:
            model, model_source = runtime_model, "runtime"
        elif trace is not None and trace.model:
            model, model_source = trace.model, "trace"
        elif declared_model:
            model, model_source = declared_model, "configured"
        else:
            model, model_source = "Model not observed", "unavailable"

        engine = getattr(instance, "engine", None) or (
            trace.engine if trace is not None else None
        )
        backend = getattr(instance, "backend", None) or (
            trace.backend if trace is not None else None
        )
        live_since = _parse_time(profile.live_since)
        last_updated_at = _parse_time(profile.last_updated_at)
        production_runtime_seconds = (
            max(0, int((now - live_since).total_seconds()))
            if live_since is not None else None
        )

        evidence = [RuntimeEvidence(
            kind="process",
            source="health_probe",
            status=process_status,
            observed_at=_parse_time(getattr(instance, "checked_at", None)),
            summary="Process health observation",
        )]
        if observed_at is not None:
            evidence.append(RuntimeEvidence(
                kind="runtime",
                source="runtime_observation",
                status="current" if runtime_fresh else "stale",
                observed_at=observed_at,
                summary="Model, backend, channel, and active-turn observation",
            ))
        if trace is not None:
            evidence.append(RuntimeEvidence(
                kind="trace",
                source="latest_completed_trace",
                status="available",
                observed_at=trace.observed_at,
                summary="Latest completed run runtime evidence",
            ))
        elif not trace_available:
            evidence.append(RuntimeEvidence(
                kind="trace",
                source="latest_completed_trace",
                status="unavailable",
                summary="Trace evidence is temporarily unavailable",
            ))

        return AgentRuntimeView(
            agent_id=agent_id,
            readiness=Readiness(
                status=readiness_status,
                reason=readiness_reason,
                observed_at=observed_at,
                freshness=(
                    "live" if runtime_fresh
                    else "stale" if observed_at is not None
                    else "unavailable"
                ),
            ),
            runtime=AgentRuntime(
                engine=engine,
                model=model,
                model_source=model_source,
                backend=backend,
                channel=getattr(instance, "channel", None),
                channel_status=channel_status,
                active_turns=active_turns,
                process_uptime_seconds=getattr(
                    instance, "uptime_seconds", None
                ),
            ),
            lifecycle=AgentLifecycle(
                live_since=live_since,
                last_updated_at=last_updated_at,
                production_runtime_seconds=production_runtime_seconds,
            ),
            evidence=evidence,
        )
