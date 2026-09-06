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
_DEFAULT_TASKS = frozenset(
    {
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "candidate_match",
        "position_interview_plan",
        "candidate_interview_plan",
    }
)
_EXPLICIT_TRIGGERS = ("竞品", "招聘情报", "全景分析", "外部岗位", "关注公司")
_GENERAL_TRIGGERS = _EXPLICIT_TRIGGERS + ("招聘", "产品路线", "人才竞争", "友商")
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
    markdown_context: str = ""
    chunks: tuple[Mapping[str, object], ...] = ()
    retrieval_version: str | None = None
    manifest_sha256: str | None = None
    degraded_reason: str | None = None

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
        if not isinstance(self.markdown_context, str) or "\0" in self.markdown_context:
            raise ValueError("intelligence Markdown context invalid")
        if not isinstance(self.chunks, tuple) or any(
            not isinstance(item, Mapping) for item in self.chunks
        ):
            raise ValueError("intelligence Markdown chunks invalid")
        if self.markdown_context and (
            self.retrieval_version != "markdown-v1"
            or not isinstance(self.manifest_sha256, str)
            or _SHA256.fullmatch(self.manifest_sha256) is None
            or not self.chunks
        ):
            raise ValueError("intelligence Markdown provenance invalid")
        if self.degraded_reason is not None and (
            not isinstance(self.degraded_reason, str) or not self.degraded_reason
        ):
            raise ValueError("intelligence degradation invalid")
        if len(json.dumps(self.as_prompt_document(), ensure_ascii=False, separators=(",", ":")).encode()) > MAX_PANORAMA_CONTEXT_BYTES:
            raise ValueError("intelligence context too large")

    def as_prompt_document(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "intelligence_status": self.status,
            "bundle_id": str(self.bundle_id) if self.bundle_id else None,
            "insight_version_id": str(self.insight_version_id) if self.insight_version_id else None,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "source_facts": [item.as_dict() for item in self.source_facts],
            "deterministic_aggregates": [item.as_dict() for item in self.aggregates],
            "ai_interpretations": [item.as_dict() for item in self.interpretations],
            "unknowns": list(self.unknowns),
            "markdown_context": self.markdown_context,
            "chunks": [dict(item) for item in self.chunks],
            "retrieval_version": self.retrieval_version,
            "manifest_sha256": self.manifest_sha256,
            "degraded_reason": self.degraded_reason,
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
                str(value.get("markdown_context", "")),
                _mappings(value.get("chunks", []), "Markdown chunks"),
                (
                    str(value["retrieval_version"])
                    if value.get("retrieval_version") is not None
                    else None
                ),
                (
                    str(value["manifest_sha256"])
                    if value.get("manifest_sha256") is not None
                    else None
                ),
                (
                    str(value["degraded_reason"])
                    if value.get("degraded_reason") is not None
                    else None
                ),
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


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    companies: tuple[str, ...]
    tracks: tuple[str, ...]
    job_families: tuple[str, ...]
    directions: tuple[str, ...]
    secondary_directions: tuple[str, ...]
    locations: tuple[str, ...]
    seniority: tuple[str, ...]
    skills: tuple[str, ...]
    task_kind: str


def _values(chunk: Mapping[str, object], key: str) -> frozenset[str]:
    raw = chunk.get(key, [])
    if not isinstance(raw, (list, tuple)):
        raise PanoramaContextError("intelligence chunk routing invalid")
    return frozenset(
        item.casefold() for item in raw if isinstance(item, str) and item.strip()
    )


def _query_values(values: tuple[str, ...]) -> set[str]:
    return {item.casefold() for item in values}


def _score(chunk: Mapping[str, object], query: RetrievalQuery) -> tuple[int, str]:
    score = 0
    score += 100 * len(_values(chunk, "companies") & _query_values(query.companies))
    score += 50 * len(
        _values(chunk, "secondary_directions")
        & _query_values(query.secondary_directions)
    )
    score += 35 * len(
        _values(chunk, "job_families") & _query_values(query.job_families)
    )
    score += 30 * int(query.task_kind.casefold() in _values(chunk, "task_kinds"))
    score += 25 * len(
        _values(chunk, "directions") & _query_values(query.directions)
    )
    score += 15 * len(_values(chunk, "tracks") & _query_values(query.tracks))
    score += 10 * len(
        _values(chunk, "locations") & _query_values(query.locations)
    )
    score += 5 * len(_values(chunk, "skills") & _query_values(query.skills))
    priority = chunk.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise PanoramaContextError("intelligence chunk priority invalid")
    return (-score - priority, str(chunk.get("chunk_id", "")))


class PanoramaContextProvider:
    def __init__(
        self,
        source: PanoramaContextSource,
        *,
        markdown_store: object | None = None,
        **_compatibility,
    ) -> None:
        if any(
            not callable(getattr(source, method, None))
            for method in (
                "current_bundle",
                "bundle_jobs",
                "bundle_reference_for_turn",
                "record_bundle_reference",
            )
        ):
            raise TypeError("panorama context source invalid")
        if markdown_store is not None and not callable(
            getattr(markdown_store, "read_chunk", None)
        ):
            raise TypeError("intelligence Markdown store invalid")
        self._source = source
        self._markdown_store = markdown_store

    @staticmethod
    def _companies(
        record: Mapping[str, object], query: str
    ) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
        catalog = record.get("source_catalog")
        if not isinstance(catalog, Mapping):
            raise PanoramaUnavailable("published intelligence context unavailable")
        companies = _mappings(catalog.get("companies"), "companies")
        folded = query.casefold()
        named = []
        for company in companies:
            key = str(company.get("company_key", ""))
            canonical_name = company.get("canonical_name")
            names = [canonical_name, *company.get("aliases", [])]
            if isinstance(canonical_name, str):
                shortened = re.sub(
                    r"(?:科技|机器人|创新|股份|有限公司)+$", "", canonical_name
                )
                if shortened:
                    names.append(shortened)
            if key and any(
                isinstance(name, str) and name.casefold() in folded for name in names
            ):
                named.append(key)
        return companies, tuple(sorted(set(named)))

    def _retrieval_query(
        self,
        record: Mapping[str, object],
        query: str,
        position_context: Mapping[str, object] | None,
        task_kind: str,
    ) -> RetrievalQuery:
        values = [query]
        if position_context is not None:
            if not isinstance(position_context, Mapping):
                raise ValueError("panorama position context invalid")
            values.extend(str(value) for value in position_context.values())
        text = " ".join(values)
        _companies, named = self._companies(record, text)
        folded = text.casefold()
        raw_chunks = _mappings(record.get("agent_chunk_index", []), "Agent chunks")

        def available(field: str) -> tuple[str, ...]:
            raw: set[str] = set()
            for chunk in raw_chunks:
                values = chunk.get(field, [])
                if not isinstance(values, (list, tuple)):
                    raise PanoramaContextError("intelligence chunk routing invalid")
                raw.update(
                    item for item in values if isinstance(item, str) and item.strip()
                )
            return tuple(sorted(raw))

        def mentioned(value: str) -> bool:
            candidates = (value, value.rsplit("/", 1)[-1])
            return any(
                len(candidate.strip()) >= 2
                and candidate.strip().casefold() in folded
                for candidate in candidates
            )

        secondary = tuple(
            value for value in available("secondary_directions") if mentioned(value)
        )
        selected_directions = {
            value for value in available("directions") if mentioned(value)
        }
        selected_directions.update(
            value.split("/", 1)[0] for value in secondary if "/" in value
        )
        directions = tuple(sorted(selected_directions))
        locations = tuple(
            value for value in available("locations") if mentioned(value)
        )
        tracks = tuple(
            value
            for token, value in (("社招", "social"), ("校招", "campus"), ("实习", "intern"))
            if token in text
        )
        skills = tuple(
            value for value in available("skills") if mentioned(value)
        )
        job_family_rules = (
            ("quality", ("质量", "测试", "可靠性", "DQE", "SQE")),
            ("manufacturing", ("制造", "工艺", "生产", "量产", "装配")),
            ("supply_chain", ("供应链", "采购", "物流", "物料")),
            ("product", ("产品经理", "产品规划", "用户体验")),
            ("sales_marketing", ("销售", "市场", "品牌", "商务")),
            ("operations", ("运营", "技术支持", "售后", "交付")),
            ("corporate", ("人力", "招聘", "财务", "法务", "行政")),
            (
                "research_development",
                ("研发", "工程师", "开发", "算法", "研究", "设计", "架构"),
            ),
        )
        possible_families = set(available("job_families"))
        job_families = tuple(
            key
            for key, tokens in job_family_rules
            if key in possible_families
            and any(token.casefold() in folded for token in tokens)
        )
        seniority = tuple(
            value
            for token, value in (
                ("高级", "senior"),
                ("资深", "senior"),
                ("专家", "senior"),
                ("中级", "mid"),
                ("初级", "junior"),
                ("校招", "graduate"),
                ("应届", "graduate"),
                ("实习", "graduate"),
            )
            if token in text and value in set(available("seniority"))
        )
        return RetrievalQuery(
            named,
            tracks,
            job_families,
            directions,
            secondary,
            locations,
            tuple(dict.fromkeys(seniority)),
            skills,
            task_kind,
        )

    def _markdown_fragment(
        self,
        record: Mapping[str, object],
        query: str,
        position_context: Mapping[str, object] | None,
        task_kind: str,
    ) -> PanoramaContextFragment:
        if self._markdown_store is None:
            raise PanoramaUnavailable("intelligence Markdown store unavailable")
        raw_chunks = _mappings(record.get("agent_chunk_index"), "Agent chunks")
        bundle_id = _uuid(record.get("bundle_id"))
        manifest_sha256 = record.get("manifest_sha256")
        if (
            not isinstance(manifest_sha256, str)
            or _SHA256.fullmatch(manifest_sha256) is None
        ):
            raise PanoramaUnavailable("published intelligence manifest invalid")
        retrieval_query = self._retrieval_query(
            record, query, position_context, task_kind
        )
        usage = [
            chunk
            for chunk in raw_chunks
            if chunk.get("path") == "agent/index.md"
            and chunk.get("heading") == "使用边界"
        ]
        if not usage:
            usage = [
                chunk
                for chunk in raw_chunks
                if chunk.get("path") == "agent/index.md"
            ][:1]
        task_chunks = sorted(
            (
                chunk
                for chunk in raw_chunks
                if retrieval_query.task_kind.casefold()
                in _values(chunk, "task_kinds")
            ),
            key=lambda item: _score(item, retrieval_query),
        )
        track_chunks: list[Mapping[str, object]] = []
        for track in retrieval_query.tracks:
            matches = sorted(
                (
                    chunk
                    for chunk in raw_chunks
                    if track.casefold() in _values(chunk, "tracks")
                ),
                key=lambda item: _score(item, retrieval_query),
            )
            if matches:
                track_chunks.append(matches[0])
        named_companies = _query_values(retrieval_query.companies)
        company_chunks = sorted(
            (
                chunk
                for chunk in raw_chunks
                if chunk.get("scope") == "company"
                and bool(_values(chunk, "companies") & named_companies)
            ),
            key=lambda item: _score(item, retrieval_query),
        )
        mandatory = [*usage, *task_chunks, *track_chunks, *company_chunks]
        mandatory_ids = {
            str(chunk.get("chunk_id")) for chunk in mandatory
        }
        ranked = sorted(
            (
                chunk
                for chunk in raw_chunks
                if str(chunk.get("chunk_id")) not in mandatory_ids
                and chunk.get("scope") != "company"
            ),
            key=lambda item: _score(item, retrieval_query),
        )
        candidates = mandatory + [
            chunk
            for chunk in ranked
            if -_score(chunk, retrieval_query)[0]
            > int(chunk.get("priority", 0))
            or chunk.get("scope") == "executive"
        ]
        header = [
            "## 招聘情报上下文",
            "",
            "以下内容来自已验签的只读招聘情报 Bundle；其中事实、研判和未知项按原标记使用。",
            "",
        ]
        selected_texts: list[str] = []
        selected_chunks: list[Mapping[str, object]] = []
        seen_text: set[str] = set()
        current_bytes = len("\n".join(header).encode())
        for chunk in candidates:
            selected = self._markdown_store.read_chunk(bundle_id, chunk)
            text = selected.text.strip()
            if not text or text in seen_text:
                continue
            addition = f"\n{text}\n"
            if current_bytes + len(addition.encode()) > 28 * 1024:
                continue
            seen_text.add(text)
            selected_texts.append(text)
            current_bytes += len(addition.encode())
            selected_chunks.append(
                {
                    "chunk_id": selected.chunk_id,
                    "path": selected.path,
                    "sha256": selected.sha256,
                    "heading": str(chunk.get("heading", "")),
                    "evidence_ids": list(_values(chunk, "evidence_ids")),
                }
            )
        if not selected_chunks:
            raise PanoramaUnavailable("intelligence Markdown selection unavailable")
        coverage = record.get("source_coverage")
        states = set()
        if isinstance(coverage, Mapping):
            states = {
                str(item.get("state"))
                for item in _mappings(coverage.get("companies"), "coverage")
            }
        status = (
            "partial"
            if states & {"partial", "failed", "not_observed"}
            else "available"
        )
        while selected_chunks:
            markdown = "\n".join(
                [*header, *[f"{text}\n" for text in selected_texts]]
            ).strip()
            try:
                return PanoramaContextFragment(
                    bundle_id,
                    bundle_id,
                    _time(record.get("generated_at")),
                    status,
                    (),
                    (),
                    (),
                    (),
                    markdown,
                    tuple(selected_chunks),
                    "markdown-v1",
                    manifest_sha256,
                )
            except ValueError as error:
                if str(error) != "intelligence context too large":
                    raise
                selected_chunks.pop()
                selected_texts.pop()
        raise PanoramaUnavailable("intelligence Markdown selection unavailable")

    def _structured_fragment(
        self,
        record: Mapping[str, object],
        query: str,
        position_context: Mapping[str, object] | None,
        *,
        degraded_reason: str | None = None,
    ) -> PanoramaContextFragment:
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
        names = {
            str(item.get("company_key")): str(
                item.get("canonical_name", item.get("company_key", ""))
            )
            for item in companies
        }
        terms = _terms(query, position_context)
        _catalog, explicitly_named = self._companies(record, query)
        ranked = []
        for job in jobs:
            company_key = str(job.get("company_key"))
            text = _job_text(job, names.get(company_key, company_key))
            score = sum(
                1 for term in terms if term.casefold() in text.casefold()
            ) + (20 if company_key in explicitly_named else 0)
            if score > 0:
                ranked.append((score, str(job.get("job_id")), job))
        selected = tuple(
            item[2]
            for item in sorted(ranked, key=lambda value: (-value[0], value[1]))[:12]
        )
        unknowns: list[str] = []
        if not selected:
            unknowns.append("当前 Bundle 没有与本岗位匹配的公开岗位证据")
        source_facts = tuple(
            _excerpt(
                "source_fact",
                f"{names.get(str(job.get('company_key')), '关注公司')}｜"
                f"{job.get('title')}｜{job.get('location')}｜"
                f"职责：{job.get('duty_excerpt')}｜要求：{job.get('requirement_excerpt')}",
                job,
                bundle_id,
            )
            for job in selected
        )
        aggregates: list[GroundedExcerpt] = []
        if selected:
            representative = selected[0]
            counts = (
                (
                    "公司",
                    Counter(
                        names.get(
                            str(job.get("company_key")), str(job.get("company_key"))
                        )
                        for job in selected
                    ),
                ),
                ("地点", Counter(str(job.get("location")) for job in selected)),
                ("招聘类型", Counter(_track(job) for job in selected)),
                (
                    "技术方向",
                    Counter(
                        signal
                        for signal in _SIGNALS[:12]
                        for job in selected
                        if signal in _job_text(job, "")
                    ),
                ),
            )
            for label, values in counts:
                if values:
                    text = f"与当前岗位相关的{label}分布：" + "、".join(
                        f"{key} {count}" for key, count in sorted(values.items())
                    )
                    aggregates.append(
                        _excerpt(
                            "deterministic_aggregate", text, representative, bundle_id
                        )
                    )
        selected_urls = {str(job.get("source_url")) for job in selected}
        interpretations: list[GroundedExcerpt] = []
        for unit in analysis:
            response = unit.get("response")
            if not isinstance(response, Mapping):
                continue
            facts = {
                str(item.get("fact_id")): item
                for item in _mappings(response.get("facts", []), "analysis facts")
                if str(item.get("source_url")) in selected_urls
            }
            for inference in _mappings(
                response.get("inferences", []), "analysis inferences"
            ):
                basis = inference.get("basis_fact_ids")
                if (
                    not isinstance(basis, list)
                    or not basis
                    or any(str(value) not in facts for value in basis)
                ):
                    continue
                fact = facts[str(basis[0])]
                matched = next(
                    (
                        job
                        for job in selected
                        if job.get("source_url") == fact.get("source_url")
                        and job.get("evidence_sha256")
                        == fact.get("evidence_sha256")
                    ),
                    None,
                )
                if matched is not None:
                    interpretations.append(
                        _excerpt(
                            "ai_interpretation",
                            str(inference.get("text")),
                            matched,
                            bundle_id,
                        )
                    )
            if facts:
                raw_unknowns = response.get("unknowns", [])
                if isinstance(raw_unknowns, list):
                    unknowns.extend(
                        value.strip()
                        for value in raw_unknowns
                        if isinstance(value, str) and value.strip()
                    )
        states = {str(item.get("state")) for item in coverage}
        status = (
            "partial"
            if not selected or states & {"partial", "failed", "not_observed"}
            else "available"
        )
        bounded_facts = list(source_facts)
        bounded_aggregates = list(aggregates[:8])
        bounded_interpretations = list(interpretations[:8])
        bounded_unknowns = list(tuple(dict.fromkeys(unknowns))[:20])
        while True:
            try:
                return PanoramaContextFragment(
                    bundle_id,
                    bundle_id,
                    max(
                        (_time(job.get("observed_at")) for job in selected),
                        default=generated_at,
                    ),
                    status,
                    tuple(bounded_facts),
                    tuple(bounded_aggregates),
                    tuple(bounded_interpretations),
                    tuple(bounded_unknowns),
                    degraded_reason=degraded_reason,
                )
            except ValueError as error:
                if str(error) != "intelligence context too large":
                    raise
                if bounded_interpretations:
                    bounded_interpretations.pop()
                elif len(bounded_facts) > 4:
                    bounded_facts.pop()
                elif bounded_aggregates:
                    bounded_aggregates.pop()
                elif len(bounded_unknowns) > 5:
                    bounded_unknowns.pop()
                elif bounded_facts:
                    bounded_facts.pop()
                elif bounded_unknowns:
                    bounded_unknowns.pop()
                else:
                    raise PanoramaUnavailable(
                        "structured intelligence context unavailable"
                    ) from None

    def _build_fragment(
        self,
        record: Mapping[str, object],
        query: str,
        position_context: Mapping[str, object] | None,
        task_kind: str,
    ) -> PanoramaContextFragment:
        if record.get("schema_version") == 2 and record.get("agent_chunk_index"):
            try:
                return self._markdown_fragment(
                    record, query, position_context, task_kind
                )
            except (PanoramaContextError, PanoramaUnavailable, ValueError):
                return self._structured_fragment(
                    record,
                    query,
                    position_context,
                    degraded_reason="markdown_verification_failed",
                )
        return self._structured_fragment(record, query, position_context)

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
        if any(
            not isinstance(value, UUID) for value in (owner_id, position_id, turn_id)
        ) or not isinstance(query, str) or not query.strip():
            raise ValueError("panorama context request invalid")
        if task_kind not in _DEFAULT_TASKS and not any(
            trigger in query for trigger in _EXPLICIT_TRIGGERS
        ):
            return None
        existing = self._source.bundle_reference_for_turn(
            owner_id, position_id, turn_id
        )
        if existing is not None:
            return PanoramaContextFragment.from_prompt_document(
                existing.get("context_document")
            )
        record = self._source.current_bundle()
        if record is None:
            return PanoramaContextFragment(
                None,
                None,
                None,
                "unavailable",
                (),
                (),
                (),
                ("当前没有已发布招聘情报",),
            )
        selected_task = task_kind or "general_chat"
        fragment = self._build_fragment(
            record, query, position_context, selected_task
        )
        bundle_id = _uuid(record.get("bundle_id"))
        reference_id = uuid5(
            NAMESPACE_URL,
            f"orbbec:hr-intelligence:task-reference:"
            f"{owner_id}:{position_id}:{turn_id}",
        )
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
        return PanoramaContextFragment.from_prompt_document(
            recorded.get("context_document")
        )

    def for_conversation_turn(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        query: str,
        turn_id: UUID,
    ) -> PanoramaContextFragment | None:
        if any(
            not isinstance(value, UUID)
            for value in (owner_id, conversation_id, turn_id)
        ) or not isinstance(query, str) or not query.strip():
            raise ValueError("panorama conversation context request invalid")
        read_reference = getattr(
            self._source, "conversation_bundle_reference_for_turn", None
        )
        record_reference = getattr(
            self._source, "record_conversation_bundle_reference", None
        )
        if not callable(read_reference) or not callable(record_reference):
            raise PanoramaUnavailable("conversation intelligence reference unavailable")
        existing = read_reference(owner_id, conversation_id, turn_id)
        if existing is not None:
            return PanoramaContextFragment.from_prompt_document(
                existing.get("context_document")
            )
        record = self._source.current_bundle()
        if record is None:
            return None
        _companies, named = self._companies(record, query)
        if not named and not any(trigger in query for trigger in _GENERAL_TRIGGERS):
            return None
        fragment = self._build_fragment(record, query, None, "general_chat")
        bundle_id = _uuid(record.get("bundle_id"))
        reference_id = uuid5(
            NAMESPACE_URL,
            f"orbbec:hr-intelligence:conversation-reference:"
            f"{owner_id}:{conversation_id}:{turn_id}",
        )
        try:
            recorded = record_reference(
                reference_id=reference_id,
                owner_id=owner_id,
                client_request_id=reference_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                bundle_id=bundle_id,
                observed_at=fragment.observed_at,
                context_document=fragment.as_prompt_document(),
            )
        except PanoramaConflict:
            recorded = read_reference(owner_id, conversation_id, turn_id)
            if recorded is None:
                raise PanoramaContextError(
                    "conversation intelligence reference conflict"
                ) from None
        return PanoramaContextFragment.from_prompt_document(
            recorded.get("context_document")
        )


__all__ = [
    "MAX_PANORAMA_CONTEXT_BYTES",
    "GroundedExcerpt",
    "PanoramaContextError",
    "PanoramaContextFragment",
    "PanoramaContextProvider",
    "RetrievalQuery",
]
