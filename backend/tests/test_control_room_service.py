from datetime import datetime, timedelta, timezone

import pytest

from app.cluster.models import (
    ClusterSnapshot,
    ClusterSummary,
    InstanceStatus,
    SourceStatus,
)
from app.control_room.service import ControlRoomService, resolve_readiness
from app.fleet.catalog import AgentCatalog, AgentProfile
from app.observability.models import RuntimeObservation
from app.remote_health.models import RemoteHealthSnapshot


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


def catalog() -> AgentCatalog:
    return AgentCatalog(
        {
            "marketing-inbound-bot": AgentProfile(
                id="marketing-inbound-bot",
                name="Marketing Inbound",
                domain="Marketing",
                description="Inbound marketing workflows.",
                glyph="IN",
                accent="inbound",
                visibility="business",
                live_since="2026-07-16T08:00:00+00:00",
                live_since_basis="earliest_session",
                last_updated_at="2026-07-23T08:00:00+00:00",
                last_updated_basis="repository_history",
            ),
        },
        {},
        set(),
    )


def local_instance(**overrides) -> InstanceStatus:
    values = {
        "id": "marketing-inbound-bot",
        "name": "marketing-inbound-bot",
        "pm2_name": "metabot-marketing-inbound",
        "port": 9103,
        "status": "healthy",
        "uptime_seconds": 60,
        "checked_at": NOW.isoformat(),
        "engine": "claude",
        "declared_model": "configured-model",
        "observed_model": "runtime-model",
        "backend": "pty",
        "channel": "Feishu",
        "channel_status": "connected",
        "active_turns": 0,
        "runtime_observed_at": NOW.isoformat(),
    }
    values.update(overrides)
    return InstanceStatus(**values)


class SnapshotMonitor:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class Observations:
    def __init__(self, observation=None, *, fail=False):
        self.observation = observation
        self.fail = fail

    async def latest_runtime_observation(self, _agent_id):
        if self.fail:
            raise RuntimeError("database secret details")
        return self.observation


def service(instance=None, observation=None, *, observation_fail=False):
    instances = [] if instance is None else [instance]
    cluster = ClusterSnapshot(
        summary=ClusterSummary(
            total=len(instances), healthy=len(instances), degraded=0,
            offline=0, checking=0,
        ),
        source=SourceStatus(healthy=True, checked_at=NOW.isoformat()),
        instances=instances,
    )
    remote = RemoteHealthSnapshot(healthy=True, agents=[])
    return ControlRoomService(
        catalog(),
        SnapshotMonitor(cluster),
        SnapshotMonitor(remote),
        Observations(observation, fail=observation_fail),
        now=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("process", "channel", "active_turns", "expected"),
    [
        ("healthy", "connected", 0, "Ready"),
        ("healthy", "connected", 1, "Busy"),
        ("healthy", "failed", 0, "Limited"),
        ("offline", "connected", 0, "Offline"),
        ("healthy", "unknown", 0, "Unknown"),
    ],
)
def test_readiness_truth_table(process, channel, active_turns, expected):
    assert resolve_readiness(
        process_status=process,
        channel_status=channel,
        active_turns=active_turns,
        runtime_fresh=True,
    )[0] == expected


def test_stale_runtime_cannot_establish_ready():
    assert resolve_readiness(
        process_status="healthy",
        channel_status="connected",
        active_turns=0,
        runtime_fresh=False,
    )[0] == "Unknown"


@pytest.mark.asyncio
async def test_fresh_runtime_model_wins_and_lifecycle_ignores_process_restart():
    trace = RuntimeObservation(
        agent_id="marketing-inbound-bot",
        source_kind="metabot",
        engine="claude",
        backend="sdk",
        model="trace-model",
        observed_at=NOW - timedelta(minutes=1),
    )

    result = await service(local_instance(uptime_seconds=5), trace).get_runtime(
        "marketing-inbound-bot"
    )

    assert result is not None
    assert result.runtime.model == "runtime-model"
    assert result.runtime.model_source == "runtime"
    assert result.runtime.backend == "pty"
    assert result.runtime.process_uptime_seconds == 5
    assert result.lifecycle.production_runtime_seconds == 8 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_trace_then_configured_then_unavailable_model_precedence():
    trace = RuntimeObservation(
        agent_id="marketing-inbound-bot",
        source_kind="metabot",
        engine="claude",
        backend="sdk",
        model="trace-model",
        observed_at=NOW - timedelta(minutes=1),
    )
    trace_result = await service(
        local_instance(observed_model=None), trace
    ).get_runtime("marketing-inbound-bot")
    configured_result = await service(
        local_instance(observed_model=None), None
    ).get_runtime("marketing-inbound-bot")
    unavailable_result = await service(
        local_instance(observed_model=None, declared_model=None), None
    ).get_runtime("marketing-inbound-bot")

    assert trace_result.runtime.model == "trace-model"
    assert trace_result.runtime.model_source == "trace"
    assert configured_result.runtime.model == "configured-model"
    assert configured_result.runtime.model_source == "configured"
    assert unavailable_result.runtime.model == "Model not observed"
    assert unavailable_result.runtime.model_source == "unavailable"


@pytest.mark.asyncio
async def test_partial_trace_failure_preserves_runtime_and_sanitizes_evidence():
    result = await service(
        local_instance(), observation_fail=True
    ).get_runtime("marketing-inbound-bot")

    assert result is not None
    assert result.readiness.status == "Ready"
    assert result.lifecycle.live_since is not None
    serialized = result.model_dump_json()
    assert "database secret details" not in serialized
    assert "workdir" not in serialized.lower()


@pytest.mark.asyncio
async def test_unknown_agent_returns_none():
    assert await service(local_instance()).get_runtime("missing-agent") is None
