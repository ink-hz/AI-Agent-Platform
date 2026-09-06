from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .panorama_repository import PanoramaConflict, PanoramaUnavailable

MAX_PANORAMA_CONTEXT_BYTES = 32 * 1024
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_DEFAULT_TASKS = frozenset({"jd", "jr", "talent_profile", "sourcing_strategy", "position_interview_plan"})
_EXPLICIT_TRIGGERS = ("竞品", "招聘情报", "全景分析", "外部岗位", "关注公司")
_SIGNALS = (
    "光学", "硬件", "结构", "软件", "算法", "制造", "工艺", "质量", "测试",
    "产品", "供应链", "机械", "电子", "嵌入式", "标定", "点云", "深圳", "中山",
    "上海", "东莞", "杭州", "西安", "武汉", "北京", "苏州", "成都", "合肥",
    "社招", "校招", "实习",
)


class PanoramaContextError(RuntimeError):
    pass


class PanoramaContextSource(Protocol):
    def current_bundle(self) -> Mapping[str, object] | None: ...
    def bundle_jobs(self, bundle_id: UUID) -> tuple[Mapping[str, object], ...]: ...
    def bundle_reference_for_turn(self, owner_id: UUID, position_id: UUID, turn_id: UUID) -> Mapping[str, object] | None: ...
    def record_bundle_reference(self, **values) -> Mapping[str, object]: ...


def _time(value: object) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if isinstance(value, str):
        try:
            selected = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if selected.tzinfo is not None:
                return selected
    raise PanoramaContextError("intelligence timestamp invalid")


def _uuid(value: object) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        raise PanoramaContextError("intelligence bundle identity invalid") from None


def _text(value: object, maximum: int = 4096) -> str:
    selected = value.strip() if isinstance(value, str) else ""
    if not selected or "\0" in selected:
        raise PanoramaContextError("intelligence excerpt invalid")
    encoded = selected.encode("utf-8")
    if len(encoded) <= maximum:
        return selected
    clipped = encoded[: maximum - 3]
    while True:
        try:
            return clipped.decode("utf-8") + "…"
        except UnicodeDecodeError:
            clipped = clipped[:-1]


def _mappings(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, Mapping) for item in value):
        raise PanoramaContextError(f"intelligence {label} invalid")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class GroundedExcerpt:
    category: Literal["source_fact", "deterministic_aggregate", "ai_interpretation"]
    text: str
    source_url: str
    evidence_sha256: str
    observed_at: datetime
    bundle_id: UUID

    def __post_init__(self) -> None:
        if self.category not in {"source_fact", "deterministic_aggregate", "ai_interpretation"}:
            raise ValueError("intelligence excerpt category invalid")
        object.__setattr__(self, "text", _text(self.text))
        if not isinstance(self.source_url, str) or not self.source_url.startswith("https://"):
            raise ValueError("intelligence excerpt source invalid")
        if not isinstance(self.evidence_sha256, str) or _SHA256.fullmatch(self.evidence_sha256) is None:
            raise ValueError("intelligence excerpt evidence invalid")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None or not isinstance(self.bundle_id, UUID):
            raise ValueError("intelligence excerpt provenance invalid")

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "text": self.text,
            "source_url": self.source_url,
            "evidence_sha256": self.evidence_sha256,
            "observed_at": self.observed_at.isoformat(),
            "bundle_id": str(self.bundle_id),
        }


@dataclass(frozen=True, slots=True)
class PanoramaContextFragment:
    bundle_id: UUID | None
    insight_version_id: UUID | None
    observed_at: datetime | None
    status: Literal["available", "partial", "unavailable"]
    source_facts: tuple[GroundedExcerpt, ...]
    aggregates: tuple[GroundedExcerpt, ...]
    interpretations: tuple[GroundedExcerpt, ...]
    unknowns: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"available", "partial", "unavailable"}:
            raise ValueError("intelligence context status invalid")
        if (self.bundle_id is None) != (self.insight_version_id is None) or self.bundle_id != self.insight_version_id:
            raise ValueError("intelligence context identity invalid")
        if self.bundle_id is None:
            if self.status != "unavailable" or self.observed_at is not None or any((self.source_facts, self.aggregates, self.interpretations)):
                raise ValueError("unavailable intelligence context invalid")
        elif self.observed_at is None or self.observed_at.tzinfo is None:
            raise ValueError("intelligence context timestamp invalid")
        for collection, category in ((self.source_facts, "source_fact"), (self.aggregates, "deterministic_aggregate"), (self.interpretations, "ai_interpretation")):
            if not isinstance(collection, tuple) or any(item.category != category or item.bundle_id != self.bundle_id for item in collection):
                raise ValueError("intelligence context excerpts invalid")
        if not isinstance(self.unknowns, tuple) or any(not isinstance(item, str) or not item.strip() for item in self.unknowns):
            raise ValueError("intelligence context unknowns invalid")
        if len(json.dumps(self.as_prompt_document(), ensure_ascii=False, separators=(",", ":")).encode()) > MAX_PANORAMA_CONTEXT_BYTES:
            raise ValueError("intelligence context too large")

    def as_prompt_document(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "intelligence_status": self.status,
            "bundle_id": str(self.bundle_id) if self.bundle_id else None,
            "insight_version_id": str(self.insight_version_id) if self.insight_version_id else None,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "source_facts": [item.as_dict() for item in self.source_facts],
            "deterministic_aggregates": [item.as_dict() for item in self.aggregates],
            "ai_interpretations": [item.as_dict() for item in self.interpretations],
            "unknowns": list(self.unknowns),
            "usage_boundary": {
                "source_facts": "可作为公开事实引用，但必须保留来源与观测时间",
                "deterministic_aggregates": "仅表示当前 Bundle 的代码统计",
                "ai_interpretations": "必须明确标注为 AI 解读",
                "unknowns": "不得改写为否定事实",
                "position_write": "仅可形成草稿，需用户确认后写入岗位库",
            },
        }

    @classmethod
    def from_prompt_document(cls, value: object) -> PanoramaContextFragment:
        if not isinstance(value, Mapping):
            raise PanoramaContextError("intelligence context document invalid")
        try:
            bundle_id = None if value.get("bundle_id") is None else _uuid(value.get("bundle_id"))
            insight_id = None if value.get("insight_version_id") is None else _uuid(value.get("insight_version_id"))
            observed_at = None if value.get("observed_at") is None else _time(value.get("observed_at"))
            def excerpts(key: str, category: str) -> tuple[GroundedExcerpt, ...]:
                return tuple(GroundedExcerpt(
                    category, str(item["text"]), str(item["source_url"]),
                    str(item["evidence_sha256"]), _time(item["observed_at"]),
                    _uuid(item["bundle_id"]),
                ) for item in _mappings(value.get(key), key))
            return cls(
                bundle_id, insight_id, observed_at, str(value.get("intelligence_status")),
                excerpts("source_facts", "source_fact"),
                excerpts("deterministic_aggregates", "deterministic_aggregate"),
                excerpts("ai_interpretations", "ai_interpretation"),
                tuple(_text(item, 4096) for item in value.get("unknowns", [])),
            )
        except (KeyError, TypeError, ValueError):
            raise PanoramaContextError("intelligence context document invalid") from None


def _terms(query: str, position_context: Mapping[str, object] | None) -> tuple[str, ...]:
    values = [query]
    if position_context is not None:
        if not isinstance(position_context, Mapping):
            raise ValueError("panorama position context invalid")
        for value in position_context.values():
            values.extend(value if isinstance(value, (list, tuple)) else (value,))
    joined = " ".join(value for value in values if isinstance(value, str))
    selected = [signal for signal in _SIGNALS if signal.casefold() in joined.casefold()]
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", joined):
        if token.casefold() not in {value.casefold() for value in selected}:
            selected.append(token)
    return tuple(selected[:64])


def _job_text(job: Mapping[str, object], company: str) -> str:
    return " ".join(str(job.get(key, "")) for key in ("title", "location", "duty_excerpt", "requirement_excerpt")) + f" {company}"


def _track(job: Mapping[str, object]) -> str:
    text = _job_text(job, "").casefold() + " " + str(job.get("source_url", "")).casefold()
    if "实习" in text or "intern" in text:
        return "实习"
    if "校招" in text or "campus" in text or "应届" in text:
        return "校招"
    return "社招"


def _excerpt(category: str, text: str, job: Mapping[str, object], bundle_id: UUID) -> GroundedExcerpt:
    return GroundedExcerpt(
        category, _text(text), _text(job.get("source_url"), 2048),
        _text(job.get("evidence_sha256"), 64), _time(job.get("observed_at")), bundle_id,
    )


class PanoramaContextProvider:
    def __init__(self, source: PanoramaContextSource, **_compatibility) -> None:
        if any(not callable(getattr(source, method, None)) for method in (
            "current_bundle", "bundle_jobs", "bundle_reference_for_turn", "record_bundle_reference",
        )):
            raise TypeError("panorama context source invalid")
        self._source = source

    def for_turn(
        self,
        owner_id: UUID,
        position_id: UUID,
        query: str,
        turn_id: UUID,
        *,
        task_kind: str | None = None,
        position_context: Mapping[str, object] | None = None,
    ) -> PanoramaContextFragment | None:
        if any(not isinstance(value, UUID) for value in (owner_id, position_id, turn_id)) or not isinstance(query, str) or not query.strip():
            raise ValueError("panorama context request invalid")
        if task_kind not in _DEFAULT_TASKS and not any(trigger in query for trigger in _EXPLICIT_TRIGGERS):
            return None
        existing = self._source.bundle_reference_for_turn(owner_id, position_id, turn_id)
        if existing is not None:
            return PanoramaContextFragment.from_prompt_document(existing.get("context_document"))
        record = self._source.current_bundle()
        if record is None:
            return PanoramaContextFragment(None, None, None, "unavailable", (), (), (), ("当前没有已发布招聘情报",))
        bundle_id = _uuid(record.get("bundle_id"))
        generated_at = _time(record.get("generated_at"))
        jobs = _mappings(self._source.bundle_jobs(bundle_id), "jobs")
        catalog = record.get("source_catalog")
        coverage_doc = record.get("source_coverage")
        analysis = _mappings(record.get("analysis"), "analysis")
        if not isinstance(catalog, Mapping) or not isinstance(coverage_doc, Mapping):
            raise PanoramaUnavailable("published intelligence context unavailable")
        companies = _mappings(catalog.get("companies"), "companies")
        coverage = _mappings(coverage_doc.get("companies"), "coverage")
        names = {str(item.get("company_key")): str(item.get("canonical_name", item.get("company_key", ""))) for item in companies}
        terms = _terms(query, position_context)
        explicitly_named = {
            key for key, name in names.items()
            if name and (name.casefold() in query.casefold() or any(str(alias).casefold() in query.casefold() for alias in next((item.get("aliases", []) for item in companies if item.get("company_key") == key), [])))
        }
        ranked = []
        for job in jobs:
            company_key = str(job.get("company_key")); text = _job_text(job, names.get(company_key, company_key))
            score = sum(1 for term in terms if term.casefold() in text.casefold()) + (20 if company_key in explicitly_named else 0)
            if score > 0:
                ranked.append((score, str(job.get("job_id")), job))
        selected = tuple(item[2] for item in sorted(ranked, key=lambda value: (-value[0], value[1]))[:12])
        unknowns: list[str] = []
        if not selected:
            unknowns.append("当前 Bundle 没有与本岗位匹配的公开岗位证据")
        source_facts = tuple(
            _excerpt("source_fact", f"{names.get(str(job.get('company_key')), '关注公司')}｜{job.get('title')}｜{job.get('location')}｜职责：{job.get('duty_excerpt')}｜要求：{job.get('requirement_excerpt')}", job, bundle_id)
            for job in selected
        )
        aggregates: list[GroundedExcerpt] = []
        if selected:
            representative = selected[0]
            for label, values in (
                ("公司", Counter(names.get(str(job.get("company_key")), str(job.get("company_key"))) for job in selected)),
                ("地点", Counter(str(job.get("location")) for job in selected)),
                ("招聘类型", Counter(_track(job) for job in selected)),
                ("技术方向", Counter(signal for signal in _SIGNALS[:12] for job in selected if signal in _job_text(job, ""))),
            ):
                if values:
                    text = f"与当前岗位相关的{label}分布：" + "、".join(f"{key} {count}" for key, count in sorted(values.items()))
                    aggregates.append(_excerpt("deterministic_aggregate", text, representative, bundle_id))
        selected_urls = {str(job.get("source_url")) for job in selected}
        interpretations: list[GroundedExcerpt] = []
        for unit in analysis:
            response = unit.get("response")
            if not isinstance(response, Mapping):
                continue
            facts = {str(item.get("fact_id")): item for item in _mappings(response.get("facts", []), "analysis facts") if str(item.get("source_url")) in selected_urls}
            for inference in _mappings(response.get("inferences", []), "analysis inferences"):
                basis = inference.get("basis_fact_ids")
                if not isinstance(basis, list) or not basis or any(str(value) not in facts for value in basis):
                    continue
                fact = facts[str(basis[0])]
                matched = next((job for job in selected if job.get("source_url") == fact.get("source_url") and job.get("evidence_sha256") == fact.get("evidence_sha256")), None)
                if matched is not None:
                    interpretations.append(_excerpt("ai_interpretation", str(inference.get("text")), matched, bundle_id))
            if facts:
                raw_unknowns = response.get("unknowns", [])
                if isinstance(raw_unknowns, list):
                    unknowns.extend(value.strip() for value in raw_unknowns if isinstance(value, str) and value.strip())
        states = {str(item.get("state")) for item in coverage}
        status = "partial" if not selected or states & {"partial", "failed", "not_observed"} else "available"
        fragment = PanoramaContextFragment(
            bundle_id, bundle_id,
            max((_time(job.get("observed_at")) for job in selected), default=generated_at),
            status, source_facts, tuple(aggregates[:8]), tuple(interpretations[:8]), tuple(dict.fromkeys(unknowns))[:20],
        )
        reference_id = uuid5(NAMESPACE_URL, f"orbbec:hr-intelligence:task-reference:{owner_id}:{position_id}:{turn_id}")
        try:
            recorded = self._source.record_bundle_reference(
                reference_id=reference_id,
                owner_id=owner_id,
                client_request_id=reference_id,
                position_id=position_id,
                turn_id=turn_id,
                bundle_id=bundle_id,
                observed_at=fragment.observed_at,
                context_document=fragment.as_prompt_document(),
            )
        except PanoramaConflict:
            recorded = self._source.bundle_reference_for_turn(
                owner_id, position_id, turn_id
            )
            if recorded is None:
                raise PanoramaContextError(
                    "intelligence bundle task reference conflict"
                ) from None
        return PanoramaContextFragment.from_prompt_document(recorded.get("context_document"))


__all__ = ["MAX_PANORAMA_CONTEXT_BYTES", "GroundedExcerpt", "PanoramaContextError", "PanoramaContextFragment", "PanoramaContextProvider"]
