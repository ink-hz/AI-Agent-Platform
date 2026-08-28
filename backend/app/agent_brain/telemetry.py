from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OUTCOMES = frozenset(
    {
        "resolved",
        "partially_completed",
        "safe_abstained",
        "failed",
        "cancelled",
        "interrupted",
    }
)
_WAIT_SOURCES = frozenset({"post_commit", "event_append", "reaper"})
_WAIT_RESULTS = frozenset({"immediate", "pending", "serialization_retry_exhausted"})

# Release-manifest accounting rates. They are deliberately data-only estimates;
# billing reconciliation remains outside the runtime and may use different rates.
_INPUT_COST_PER_TOKEN = 5.0 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 25.0 / 1_000_000
_CACHE_CREATE_COST_PER_TOKEN = 10.0 / 1_000_000
_CACHE_READ_COST_PER_TOKEN = 0.5 / 1_000_000


def _counter(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 10**15


@dataclass(frozen=True)
class BrainStepCounters:
    step_seq: int
    resume_ordinal: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    recovery_count: int = 0
    duplicate_event_count: int = 0
    truncation_count: int = 0
    omission_count: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.step_seq) is not int
            or self.step_seq < 1
            or type(self.resume_ordinal) is not int
            or self.resume_ordinal < 0
            or not all(
                _counter(value)
                for value in (
                    self.duration_ms,
                    self.input_tokens,
                    self.output_tokens,
                    self.cache_creation_input_tokens,
                    self.cache_read_input_tokens,
                    self.recovery_count,
                    self.duplicate_event_count,
                    self.truncation_count,
                    self.omission_count,
                )
            )
        ):
            raise ValueError("Brain telemetry step invalid")


@dataclass(frozen=True)
class BrainTurnSnapshot:
    turn_id: UUID
    model_config_version: str
    model_id: str
    prompt_version: str
    system_prompt_sha256: str
    task_count: int
    batch_count: int
    queue_duration_ms: int
    run_duration_ms: int
    settle_duration_ms: int
    steps: tuple[BrainStepCounters, ...]
    outcome: str
    fallback_used: bool
    reason_code: str | None
    wait_settlements: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.turn_id, UUID)
            or not all(
                isinstance(value, str) and _SAFE_ID.fullmatch(value)
                for value in (
                    self.model_config_version,
                    self.model_id,
                    self.prompt_version,
                )
            )
            or _SHA256.fullmatch(self.system_prompt_sha256) is None
            or not all(
                _counter(value)
                for value in (
                    self.task_count,
                    self.batch_count,
                    self.queue_duration_ms,
                    self.run_duration_ms,
                    self.settle_duration_ms,
                )
            )
            or type(self.steps) is not tuple
            or any(not isinstance(step, BrainStepCounters) for step in self.steps)
            or tuple(step.step_seq for step in self.steps)
            != tuple(range(1, len(self.steps) + 1))
            or self.outcome not in _OUTCOMES
            or type(self.fallback_used) is not bool
            or type(self.wait_settlements) is not tuple
            or len(self.wait_settlements) > 10**6
            or any(
                type(item) is not tuple
                or len(item) != 2
                or item[0] not in _WAIT_SOURCES
                or item[1] not in _WAIT_RESULTS
                for item in self.wait_settlements
            )
            or (
                self.reason_code is not None
                and (
                    type(self.reason_code) is not str
                    or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.reason_code) is None
                )
            )
        ):
            raise ValueError("Brain telemetry snapshot invalid")


@dataclass(frozen=True)
class CachePathTelemetry:
    step_count: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cache_hit_rate: float | None
    estimated_cost: float


@dataclass(frozen=True)
class BrainTurnTelemetry:
    turn_id: UUID
    model_config_version: str
    model_id: str
    prompt_version: str
    system_prompt_sha256: str
    step_count: int
    task_count: int
    batch_count: int
    queue_duration_ms: int
    run_duration_ms: int
    settle_duration_ms: int
    continuous_steps: CachePathTelemetry
    first_waiting_agents_resume: CachePathTelemetry
    later_waiting_agents_resumes: CachePathTelemetry
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    recovery_count: int
    duplicate_event_count: int
    truncation_count: int
    omission_count: int
    outcome: str
    fallback_used: bool
    reason_code: str | None
    wait_settlement_sources: dict[str, int]
    wait_settlement_results: dict[str, int]
    immediate_settlement_count: int
    immediate_settlement_step_count: int
    wait_immediate_settlement_rate: float | None

    def as_public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["turn_id"] = str(self.turn_id)
        return value


def _cache_path(steps: tuple[BrainStepCounters, ...]) -> CachePathTelemetry:
    input_tokens = sum(step.input_tokens for step in steps)
    output_tokens = sum(step.output_tokens for step in steps)
    cache_create = sum(step.cache_creation_input_tokens for step in steps)
    cache_read = sum(step.cache_read_input_tokens for step in steps)
    denominator = input_tokens + cache_read
    return CachePathTelemetry(
        step_count=len(steps),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
        cache_hit_rate=(cache_read / denominator if denominator else None),
        estimated_cost=(
            input_tokens * _INPUT_COST_PER_TOKEN
            + output_tokens * _OUTPUT_COST_PER_TOKEN
            + cache_create * _CACHE_CREATE_COST_PER_TOKEN
            + cache_read * _CACHE_READ_COST_PER_TOKEN
        ),
    )


class BrainTelemetry:
    """Build content-free metrics from already normalized runtime counters."""

    def summarize(self, snapshot: BrainTurnSnapshot) -> BrainTurnTelemetry:
        if not isinstance(snapshot, BrainTurnSnapshot):
            raise ValueError("Brain telemetry snapshot required")
        continuous = tuple(step for step in snapshot.steps if step.resume_ordinal == 0)
        first_resume = tuple(
            step for step in snapshot.steps if step.resume_ordinal == 1
        )
        later_resumes = tuple(
            step for step in snapshot.steps if step.resume_ordinal > 1
        )
        all_path = _cache_path(snapshot.steps)
        wait_sources = Counter(source for source, _result in snapshot.wait_settlements)
        wait_results = Counter(result for _source, result in snapshot.wait_settlements)
        immediate = wait_results["immediate"]
        settlement_count = len(snapshot.wait_settlements)
        return BrainTurnTelemetry(
            turn_id=snapshot.turn_id,
            model_config_version=snapshot.model_config_version,
            model_id=snapshot.model_id,
            prompt_version=snapshot.prompt_version,
            system_prompt_sha256=snapshot.system_prompt_sha256,
            step_count=len(snapshot.steps),
            task_count=snapshot.task_count,
            batch_count=snapshot.batch_count,
            queue_duration_ms=snapshot.queue_duration_ms,
            run_duration_ms=snapshot.run_duration_ms,
            settle_duration_ms=snapshot.settle_duration_ms,
            continuous_steps=_cache_path(continuous),
            first_waiting_agents_resume=_cache_path(first_resume),
            later_waiting_agents_resumes=_cache_path(later_resumes),
            input_tokens=all_path.input_tokens,
            output_tokens=all_path.output_tokens,
            cache_creation_input_tokens=all_path.cache_creation_input_tokens,
            cache_read_input_tokens=all_path.cache_read_input_tokens,
            recovery_count=sum(step.recovery_count for step in snapshot.steps),
            duplicate_event_count=sum(
                step.duplicate_event_count for step in snapshot.steps
            ),
            truncation_count=sum(step.truncation_count for step in snapshot.steps),
            omission_count=sum(step.omission_count for step in snapshot.steps),
            outcome=snapshot.outcome,
            fallback_used=snapshot.fallback_used,
            reason_code=snapshot.reason_code,
            wait_settlement_sources=dict(wait_sources),
            wait_settlement_results=dict(wait_results),
            immediate_settlement_count=immediate,
            immediate_settlement_step_count=immediate,
            wait_immediate_settlement_rate=(
                immediate / settlement_count if settlement_count else None
            ),
        )


@dataclass(frozen=True)
class BrainBudgetEvaluation:
    max_steps: int
    turn_count: int
    observed_step_count: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_hit_rate: float | None
    wait_immediate_settlement_rate: float | None
    run_duration_ms: int
    settle_duration_ms: int
    outcomes: dict[str, int]

    def as_public_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_budget_matrix(
    snapshots_by_max_steps: Mapping[int, tuple[BrainTurnSnapshot, ...]],
) -> tuple[BrainBudgetEvaluation, ...]:
    """Aggregate the exact 12/16/24 scripted release matrix without content."""

    if (
        not isinstance(snapshots_by_max_steps, Mapping)
        or set(snapshots_by_max_steps) != {12, 16, 24}
        or any(
            type(snapshots) is not tuple
            or not snapshots
            or any(not isinstance(item, BrainTurnSnapshot) for item in snapshots)
            for snapshots in snapshots_by_max_steps.values()
        )
    ):
        raise ValueError("Brain budget evaluation matrix invalid")
    reports: list[BrainBudgetEvaluation] = []
    telemetry = BrainTelemetry()
    for max_steps in (12, 16, 24):
        summaries = tuple(
            telemetry.summarize(snapshot)
            for snapshot in snapshots_by_max_steps[max_steps]
        )
        if any(summary.step_count > max_steps for summary in summaries):
            raise ValueError("Brain budget evaluation matrix invalid")
        input_tokens = sum(summary.input_tokens for summary in summaries)
        cache_read = sum(summary.cache_read_input_tokens for summary in summaries)
        denominator = input_tokens + cache_read
        settlements = sum(
            sum(summary.wait_settlement_results.values()) for summary in summaries
        )
        immediate = sum(summary.immediate_settlement_count for summary in summaries)
        reports.append(
            BrainBudgetEvaluation(
                max_steps=max_steps,
                turn_count=len(summaries),
                observed_step_count=sum(summary.step_count for summary in summaries),
                input_tokens=input_tokens,
                output_tokens=sum(summary.output_tokens for summary in summaries),
                cache_read_input_tokens=cache_read,
                cache_hit_rate=(cache_read / denominator if denominator else None),
                wait_immediate_settlement_rate=(
                    immediate / settlements if settlements else None
                ),
                run_duration_ms=sum(summary.run_duration_ms for summary in summaries),
                settle_duration_ms=sum(
                    summary.settle_duration_ms for summary in summaries
                ),
                outcomes=dict(Counter(summary.outcome for summary in summaries)),
            )
        )
    return tuple(reports)
