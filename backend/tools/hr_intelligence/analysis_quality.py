from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .analysis_units import (
    AcceptedAnalysis,
    AnalysisContractError,
)

_NORMALIZE_TEXT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+", re.IGNORECASE)
_TASK_TARGETS = {
    "jd-jr": frozenset({"jd", "jr"}),
    "talent-profile": frozenset({"talent_profile"}),
    "sourcing": frozenset({"sourcing_strategy"}),
    "resume-review": frozenset({"candidate_match"}),
    "interview": frozenset(
        {"position_interview_plan", "candidate_interview_plan"}
    ),
}


@dataclass(frozen=True, slots=True)
class AnalysisQualityReport:
    unit_count: int
    factual_claim_count: int
    inference_count: int
    recommendation_count: int
    duplicate_pairs: tuple[tuple[str, str], ...]
    passed: bool


def _response(analysis: AcceptedAnalysis) -> dict[str, object]:
    if not isinstance(analysis, AcceptedAnalysis):
        raise TypeError("accepted analysis required")
    try:
        value = json.loads(analysis.response_json)
    except (TypeError, json.JSONDecodeError):
        raise AnalysisContractError("analysis quality response invalid") from None
    if not isinstance(value, dict):
        raise AnalysisContractError("analysis quality response invalid")
    return value


def _items(response: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = response.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise AnalysisContractError(f"analysis quality {key} invalid")
    return tuple(value)


def _normalized(value: object) -> str:
    return _NORMALIZE_TEXT.sub("", str(value)).casefold()


def _generic_duplicate_pairs(
    company_inferences: tuple[tuple[str, str], ...], *, threshold: float
) -> tuple[tuple[str, str], ...]:
    pairs: set[tuple[str, str]] = set()
    for ordinal, (left_company, left_text) in enumerate(company_inferences):
        left = _normalized(left_text)
        if len(left) < 12:
            continue
        for right_company, right_text in company_inferences[ordinal + 1 :]:
            if left_company == right_company:
                continue
            right = _normalized(right_text)
            if len(right) < 12:
                continue
            if SequenceMatcher(None, left, right, autojunk=False).ratio() >= threshold:
                pairs.add(tuple(sorted((left_company, right_company))))
    return tuple(sorted(pairs))


def validate_analysis_set(
    analyses: tuple[AcceptedAnalysis, ...],
) -> AnalysisQualityReport:
    if not isinstance(analyses, tuple) or any(
        not isinstance(item, AcceptedAnalysis) for item in analyses
    ):
        raise TypeError("accepted analysis tuple required")
    unit_ids = [item.unit.unit_id for item in analyses]
    if len(unit_ids) != len(set(unit_ids)):
        raise AnalysisContractError("analysis quality duplicate unit")

    factual_claim_count = 0
    inference_count = 0
    recommendation_count = 0
    company_inferences: list[tuple[str, str]] = []
    for analysis in analyses:
        response = _response(analysis)
        facts = _items(response, "facts")
        inferences = _items(response, "inferences")
        recommendations = _items(response, "recommendations")
        factual_claim_count += len(facts)
        inference_count += len(inferences)
        recommendation_count += len(recommendations)

        if response.get("schema_version") == 2:
            alternatives = _items(response, "alternatives")
            if analysis.unit.kind == "company" and inferences and not alternatives:
                raise AnalysisContractError("analysis company alternative required")
            if analysis.unit.kind == "task" and facts:
                expected = _TASK_TARGETS.get(analysis.unit.scope_key)
                if expected is None or not any(
                    expected.intersection(recommendation.get("target_tasks", []))
                    for recommendation in recommendations
                ):
                    raise AnalysisContractError("analysis task recommendation required")
        if analysis.unit.kind == "company":
            company_inferences.extend(
                (analysis.unit.scope_key, str(inference.get("text", "")))
                for inference in inferences
            )

    duplicate_pairs = _generic_duplicate_pairs(
        tuple(company_inferences), threshold=0.88
    )
    if duplicate_pairs:
        raise AnalysisContractError("analysis generic duplicate")
    return AnalysisQualityReport(
        unit_count=len(analyses),
        factual_claim_count=factual_claim_count,
        inference_count=inference_count,
        recommendation_count=recommendation_count,
        duplicate_pairs=duplicate_pairs,
        passed=True,
    )


__all__ = ["AnalysisQualityReport", "validate_analysis_set"]
