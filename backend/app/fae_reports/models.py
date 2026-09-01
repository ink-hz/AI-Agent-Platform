from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", min_length=1, max_length=80),
]
Dimension = Literal[
    "usage", "business_value", "answer_effectiveness", "insights_improvement"
]
EvidenceKind = Literal["session", "turn", "feedback", "issue"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ReportPeriod(StrictModel):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("naive_timestamp")
        if self.start_at >= self.end_at:
            raise ValueError("invalid_report_period")
        return self


class ReportSource(StrictModel):
    agent_id: Literal["ai-fae-agent"]
    source_kind: Literal["fae"]
    environment: Literal["production"]
    source_snapshot_at: datetime
    session_count: int = Field(ge=0)
    turn_count: int = Field(ge=0)
    feedback_event_count: int = Field(ge=0)
    reviewed_session_count: int = Field(ge=0)

    @field_validator("source_snapshot_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("naive_timestamp")
        return value


class ReportSummary(StrictModel):
    headline: str = Field(min_length=1, max_length=160)
    overview: str = Field(min_length=1, max_length=2000)
    top_finding_ids: list[Identifier] = Field(max_length=5)
    top_recommendation_ids: list[Identifier] = Field(max_length=5)


class ReportMetric(StrictModel):
    metric_id: Identifier
    dimension: Dimension
    label: str = Field(min_length=1, max_length=160)
    value: int | float | dict[str, int | Literal["少于 5"]]
    unit: Literal["count", "ratio", "percent", "milliseconds", "seconds", "distribution"]
    numerator: int | float | None = Field(default=None, ge=0)
    denominator: int | float | None = Field(default=None, gt=0)
    filters: list[str] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=20)
    evidence_artifact_refs: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _shape_matches_unit(self) -> Self:
        if self.unit == "count":
            if type(self.value) is not int or self.numerator is not None:
                raise ValueError("invalid_metric_value")
        elif self.unit in {"ratio", "percent"}:
            if isinstance(self.value, dict) or self.numerator is None or self.denominator is None:
                raise ValueError("invalid_metric_fraction")
        elif self.unit == "distribution":
            if not isinstance(self.value, dict) or not self.value or len(self.value) > 50:
                raise ValueError("invalid_metric_distribution")
            if self.numerator is not None or self.denominator is None:
                raise ValueError("invalid_metric_distribution")
            for key, count in self.value.items():
                if not key or len(key) > 80 or (type(count) is int and count < 0):
                    raise ValueError("invalid_metric_distribution")
        elif isinstance(self.value, dict):
            raise ValueError("invalid_metric_value")
        return self


class ReportEvidence(StrictModel):
    kind: EvidenceKind
    canonical_key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def _scope(self) -> Self:
        if self.kind == "issue":
            from uuid import UUID

            try:
                UUID(self.canonical_key)
            except ValueError as exc:
                raise ValueError("invalid_evidence_scope") from exc
        elif not self.canonical_key.startswith("fae:"):
            raise ValueError("invalid_evidence_scope")
        return self


class ReportFinding(StrictModel):
    finding_id: Identifier
    dimension: Dimension
    severity: Literal["critical", "high", "medium", "low", "opportunity"]
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    root_cause_hypothesis: str = Field(min_length=1, max_length=2000)
    impact_scope: str = Field(min_length=1, max_length=2000)
    metric_ids: list[Identifier] = Field(min_length=1, max_length=20)
    evidence_refs: list[ReportEvidence] = Field(min_length=1, max_length=20)
    recommendation_ids: list[Identifier] = Field(max_length=20)
    linked_issue_ids: list[str] = Field(max_length=20)


class ReportRecommendation(StrictModel):
    recommendation_id: Identifier
    dimension: Dimension
    priority: Literal["p0", "p1", "p2", "p3"]
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=2000)
    proposed_action: str = Field(min_length=1, max_length=2000)
    owner_role: str = Field(min_length=1, max_length=80)
    finding_ids: list[Identifier] = Field(max_length=20)
    success_metric_ids: list[Identifier] = Field(max_length=20)


class ReportCase(StrictModel):
    case_id: Identifier
    dimension: Dimension
    title: str = Field(min_length=1, max_length=160)
    scenario: str = Field(min_length=1, max_length=2000)
    outcome: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[ReportEvidence] = Field(min_length=1, max_length=20)
    business_case_approved: Literal[True]


class ReportFailure(StrictModel):
    stage: Literal["snapshot", "population", "classification", "annotation", "review", "reporting", "publication"]
    code: Literal[
        "snapshot_failed",
        "population_blocked",
        "classification_failed",
        "annotation_incomplete",
        "review_incomplete",
        "report_blocked",
        "publication_failed",
        "sanitized_failure",
    ]
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class FaeAnalysisReport(StrictModel):
    schema_name: Literal["fae.analysis-report"]
    schema_version: Literal["1.0.0"]
    report_id: Annotated[str, StringConstraints(pattern=r"^fae-(weekly|topic)-[a-z0-9][a-z0-9-]{2,63}$")]
    report_version: int = Field(ge=1)
    report_type: Literal["weekly", "topic"]
    status: Literal["ready", "failed"]
    title: str = Field(min_length=1, max_length=160)
    period: ReportPeriod
    data_cutoff_at: datetime
    generated_at: datetime
    analysis_version: Identifier
    source: ReportSource
    summary: ReportSummary | None
    metrics: list[ReportMetric] = Field(max_length=200)
    findings: list[ReportFinding] = Field(max_length=100)
    recommendations: list[ReportRecommendation] = Field(max_length=100)
    cases: list[ReportCase] = Field(max_length=20)
    artifact_digests: dict[str, str]
    failure: ReportFailure | None

    @model_validator(mode="after")
    def _validate_shape_and_references(self) -> Self:
        for value in (self.data_cutoff_at, self.generated_at):
            if value.tzinfo is None:
                raise ValueError("naive_timestamp")
        if self.data_cutoff_at < self.period.end_at or self.generated_at < self.data_cutoff_at:
            raise ValueError("invalid_report_timestamps")
        if self.status == "failed":
            if self.failure is None or self.summary is not None or self.metrics or self.findings or self.recommendations or self.cases or self.artifact_digests:
                raise ValueError("invalid_failed_report")
            return self
        if self.summary is None or self.failure is not None or not self.metrics:
            raise ValueError("invalid_ready_report")
        if {metric.dimension for metric in self.metrics} != {
            "usage", "business_value", "answer_effectiveness", "insights_improvement"
        }:
            raise ValueError("incomplete_report_dimensions")
        required_artifacts = {
            "metrics.json", "claim_ledger.jsonl", "action_backlog.jsonl",
            "executive_summary.md", "full_report.md", "audit_appendix.md", "report.html",
        }
        if set(self.artifact_digests) != required_artifacts or any(
            len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
            for value in self.artifact_digests.values()
        ):
            raise ValueError("invalid_artifact_digests")
        self._references_resolve()
        return self

    def _references_resolve(self) -> None:
        metric_ids = _unique(self.metrics, "metric_id")
        finding_ids = _unique(self.findings, "finding_id")
        recommendation_ids = _unique(self.recommendations, "recommendation_id")
        _unique(self.cases, "case_id")
        assert self.summary is not None
        if not set(self.summary.top_finding_ids) <= finding_ids:
            raise ValueError("unresolved_report_reference")
        if not set(self.summary.top_recommendation_ids) <= recommendation_ids:
            raise ValueError("unresolved_report_reference")
        for finding in self.findings:
            if not set(finding.metric_ids) <= metric_ids or not set(finding.recommendation_ids) <= recommendation_ids:
                raise ValueError("unresolved_report_reference")
        for recommendation in self.recommendations:
            if not set(recommendation.finding_ids) <= finding_ids or not set(recommendation.success_metric_ids) <= metric_ids:
                raise ValueError("unresolved_report_reference")


def _unique(items: list[StrictModel], attribute: str) -> set[str]:
    values = [str(getattr(item, attribute)) for item in items]
    if len(values) != len(set(values)):
        raise ValueError("duplicate_report_id")
    return set(values)
