from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from .chunk_index import MarkdownChunk, build_chunk_index, validate_chunk_index

_COMPANY_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DIRECTION_SLUGS = {
    "光学": "optics",
    "硬件": "hardware",
    "结构": "structure",
    "软件": "software",
    "算法": "algorithm",
    "制造工艺": "manufacturing-process",
    "质量": "quality",
    "产品": "product",
    "供应链": "supply-chain",
    "其他": "other",
}
_TOPIC_FILES = {
    "social": "social-recruiting",
    "campus": "campus-recruiting",
    "intern": "internships",
    "product-routes": "product-routes",
    "talent-competition": "talent-competition",
    "geography": "geography",
    "trends": "trends",
}
_TASK_FILES = {
    "jd": "jd-jr",
    "jr": "jd-jr",
    "talent_profile": "talent-profile",
    "sourcing_strategy": "sourcing",
    "candidate_match": "resume-review",
    "position_interview_plan": "interview",
    "candidate_interview_plan": "interview",
}
_TASK_TITLES = {
    "jd-jr": "JD 与 JR 情报使用手册",
    "talent-profile": "人才画像情报使用手册",
    "sourcing": "人才搜寻情报使用手册",
    "resume-review": "简历分析情报使用手册",
    "interview": "面试设计情报使用手册",
}


class MarkdownContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentMarkdownPackage:
    files: Mapping[str, bytes]
    chunks: tuple[MarkdownChunk, ...]


def _text(value: object, *, maximum: int = 16_384) -> str:
    selected = value.strip() if isinstance(value, str) else ""
    if not selected or len(selected) > maximum or _CONTROL.search(selected):
        raise MarkdownContractError("Markdown text invalid")
    return html.escape(selected, quote=False)


def _mappings(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MarkdownContractError(f"Markdown {label} invalid")
    selected = tuple(value)
    if any(not isinstance(item, Mapping) for item in selected):
        raise MarkdownContractError(f"Markdown {label} invalid")
    return selected


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MarkdownContractError(f"Markdown {label} invalid")
    return tuple(_text(item, maximum=4096) for item in value)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, UnicodeError, ValueError):
        raise MarkdownContractError("Markdown chunk index invalid") from None


def _front_matter(
    *,
    bundle_id: UUID,
    observed_at: datetime,
    scope: str,
    scope_key: str,
    confidence: str,
    coverage: str,
    evidence_count: int,
) -> list[str]:
    if confidence not in {"low", "medium", "high"}:
        raise MarkdownContractError("Markdown confidence invalid")
    if coverage not in {
        "succeeded",
        "empty_confirmed",
        "partial",
        "failed",
        "not_observed",
    }:
        raise MarkdownContractError("Markdown coverage invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", scope):
        raise MarkdownContractError("Markdown scope invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,255}", scope_key):
        raise MarkdownContractError("Markdown scope key invalid")
    if isinstance(evidence_count, bool) or not isinstance(evidence_count, int):
        raise MarkdownContractError("Markdown evidence count invalid")
    return [
        "---",
        "schema_version: 1",
        f"bundle_id: {bundle_id}",
        f"observed_at: {observed_at.isoformat()}",
        f"scope: {scope}",
        f"scope_key: {scope_key}",
        f"confidence: {confidence}",
        f"coverage: {coverage}",
        f"evidence_count: {evidence_count}",
        "---",
        "",
    ]


def _evidence_keys(unit: Mapping[str, object]) -> set[tuple[str, str, str]]:
    selected = set()
    for item in _mappings(unit.get("evidence", []), "analysis evidence"):
        sha256 = item.get("sha256")
        source_url = item.get("source_url")
        observed_at = item.get("observed_at")
        if (
            not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
            or not isinstance(source_url, str)
            or not source_url.startswith("https://")
            or not isinstance(observed_at, str)
            or not observed_at
        ):
            raise MarkdownContractError("Markdown analysis evidence invalid")
        selected.add((sha256, source_url, observed_at))
    return selected


def _response(unit: Mapping[str, object]) -> Mapping[str, object]:
    response = unit.get("response")
    if not isinstance(response, Mapping):
        raise MarkdownContractError("Markdown analysis response invalid")
    return response


def _claim_lines(unit: Mapping[str, object]) -> tuple[list[str], set[str]]:
    response = _response(unit)
    available = _evidence_keys(unit)
    fact_ids: set[str] = set()
    evidence_hashes: set[str] = set()
    lines = ["## 可引用事实", ""]
    facts = _mappings(response.get("facts", []), "facts")
    if not facts:
        lines.append("- 当前没有可引用事实。")
    for ordinal, fact in enumerate(facts, start=1):
        fact_id = _text(fact.get("fact_id") or f"F-{ordinal}", maximum=128)
        raw_sha = fact.get("evidence_sha256")
        raw_url = fact.get("source_url")
        raw_time = fact.get("observed_at")
        if (
            not isinstance(raw_sha, str)
            or not isinstance(raw_url, str)
            or not isinstance(raw_time, str)
            or (raw_sha, raw_url, raw_time) not in available
        ):
            raise MarkdownContractError("Markdown fact evidence invalid")
        fact_ids.add(html.unescape(fact_id))
        evidence_hashes.add(raw_sha)
        lines.append(
            f"- **事实 {fact_id}：** {_text(fact.get('text'))} "
            f"([来源]({_text(raw_url, maximum=2048)})；"
            f"SHA-256 `{raw_sha}`；观察时间 {_text(raw_time, maximum=128)})"
        )
    lines.extend(["", "## 技术与产品路线研判", ""])
    inference_ids: set[str] = set()
    inferences = _mappings(response.get("inferences", []), "inferences")
    if not inferences:
        lines.append("- 当前证据不足，未形成 AI 研判。")
    for ordinal, inference in enumerate(inferences, start=1):
        inference_id = _text(
            inference.get("inference_id") or f"I-{ordinal}", maximum=128
        )
        basis = _strings(inference.get("basis_fact_ids", []), "inference basis")
        if not basis or any(html.unescape(item) not in fact_ids for item in basis):
            raise MarkdownContractError("Markdown inference evidence invalid")
        inference_ids.add(html.unescape(inference_id))
        lines.append(
            f"- **研判 {inference_id}：** {_text(inference.get('text'))} "
            f"（依据：{', '.join(basis)}）"
        )
    lines.extend(["", "## 人才竞争信号", ""])
    recommendations = _mappings(
        response.get("recommendations", []), "recommendations"
    )
    if not recommendations:
        lines.append("- 当前分析未提供可执行招聘建议。")
    for ordinal, recommendation in enumerate(recommendations, start=1):
        recommendation_id = _text(
            recommendation.get("recommendation_id") or f"R-{ordinal}",
            maximum=128,
        )
        basis = _strings(
            recommendation.get("basis_fact_ids", []), "recommendation basis"
        )
        if not basis or any(html.unescape(item) not in fact_ids for item in basis):
            raise MarkdownContractError("Markdown recommendation evidence invalid")
        tasks = _strings(
            recommendation.get("target_tasks", []), "recommendation tasks"
        )
        lines.append(
            f"- **建议 {recommendation_id}：** "
            f"{_text(recommendation.get('text'))} "
            f"（依据：{', '.join(basis)}；适用任务：{', '.join(tasks)}）"
        )
    lines.extend(["", "## 未知项与替代解释", ""])
    unknowns = _strings(response.get("unknowns", []), "unknowns")
    if not unknowns:
        lines.append("- 当前分析未登记额外未知项。")
    for ordinal, unknown in enumerate(unknowns, start=1):
        kind = str(unit.get("kind", "scope"))
        scope_key = str(unit.get("scope_key", "scope"))
        lines.append(f"- **未知 U-{kind}-{scope_key}-{ordinal}：** {unknown}")
    alternatives = response.get("alternatives", [])
    if not isinstance(alternatives, Sequence) or isinstance(
        alternatives, (str, bytes)
    ):
        raise MarkdownContractError("Markdown alternatives invalid")
    for ordinal, raw in enumerate(alternatives, start=1):
        if isinstance(raw, str):
            alternative_id = f"X-{ordinal}"
            text = _text(raw)
            basis: tuple[str, ...] = ()
            challenged: tuple[str, ...] = ()
        elif isinstance(raw, Mapping):
            alternative_id = _text(
                raw.get("alternative_id") or f"X-{ordinal}", maximum=128
            )
            text = _text(raw.get("text"))
            basis = _strings(raw.get("basis_fact_ids", []), "alternative basis")
            challenged = _strings(
                raw.get("challenged_inference_ids", []), "challenged inferences"
            )
            if any(html.unescape(item) not in fact_ids for item in basis) or any(
                html.unescape(item) not in inference_ids for item in challenged
            ):
                raise MarkdownContractError("Markdown alternative evidence invalid")
        else:
            raise MarkdownContractError("Markdown alternatives invalid")
        suffix = []
        if basis:
            suffix.append(f"依据：{', '.join(basis)}")
        if challenged:
            suffix.append(f"挑战：{', '.join(challenged)}")
        details = f"（{'；'.join(suffix)}）" if suffix else ""
        lines.append(f"- **替代解释 {alternative_id}：** {text}{details}")
    lines.extend(["", "## 证据", ""])
    if not evidence_hashes:
        lines.append("- 当前没有已接受的证据引用。")
    for sha256, source_url, observed_at in sorted(available):
        if sha256 in evidence_hashes:
            lines.append(
                f"- `{sha256}`｜[原始来源]({_text(source_url, maximum=2048)})｜"
                f"{_text(observed_at, maximum=128)}"
            )
    lines.append("")
    return lines, evidence_hashes


def _render_unit(
    *,
    bundle_id: UUID,
    generated_at: datetime,
    scope: str,
    scope_key: str,
    title: str,
    coverage: str,
    unit: Mapping[str, object] | None,
) -> bytes:
    if unit is None:
        lines = _front_matter(
            bundle_id=bundle_id,
            observed_at=generated_at,
            scope=scope,
            scope_key=scope_key,
            confidence="low",
            coverage=coverage,
            evidence_count=0,
        )
        lines.extend(
            [
                f"# {_text(title)}",
                "",
                "## 核心判断",
                "",
                "当前证据不足，尚未形成可引用的专项分析。",
                "",
                "## 未知项与替代解释",
                "",
                "- 当前 Bundle 未提供该范围的已接受分析。",
                "",
            ]
        )
        return "\n".join(lines).encode("utf-8")
    response = _response(unit)
    confidence = response.get("confidence", "low")
    evidence_count = len(_evidence_keys(unit))
    lines = _front_matter(
        bundle_id=bundle_id,
        observed_at=generated_at,
        scope=scope,
        scope_key=scope_key,
        confidence=str(confidence),
        coverage=coverage,
        evidence_count=evidence_count,
    )
    lines.extend(
        [
            f"# {_text(title)}",
            "",
            "## 核心判断",
            "",
            _text(response.get("summary")),
            "",
        ]
    )
    claims, _evidence = _claim_lines(unit)
    lines.extend(claims)
    return "\n".join(lines).encode("utf-8")


def _safe_key(value: object, label: str) -> str:
    selected = value.strip() if isinstance(value, str) else ""
    if _COMPANY_KEY.fullmatch(selected) is None:
        raise MarkdownContractError(f"Markdown {label} invalid")
    return selected


def _positive_keys(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        raise MarkdownContractError(f"Markdown {label} invalid")
    selected: list[str] = []
    for key, count in value.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise MarkdownContractError(f"Markdown {label} invalid")
        if count > 0:
            selected.append(key.strip())
    return sorted(set(selected))


def _secondary_slug(value: str) -> str:
    parent = value.split("/", 1)[0]
    prefix = _DIRECTION_SLUGS.get(parent, "other")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}--{digest}"


def compile_agent_markdown(
    *,
    bundle_id: UUID,
    generated_at: datetime,
    catalog: Mapping[str, object],
    coverage: Mapping[str, object],
    aggregates: Mapping[str, object],
    analyses: tuple[Mapping[str, object], ...],
) -> AgentMarkdownPackage:
    if not isinstance(bundle_id, UUID):
        raise MarkdownContractError("Markdown bundle identity invalid")
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
        raise MarkdownContractError("Markdown generation time invalid")
    if any(not isinstance(value, Mapping) for value in (catalog, coverage, aggregates)):
        raise MarkdownContractError("Markdown inputs invalid")
    if not isinstance(analyses, tuple) or any(
        not isinstance(item, Mapping) for item in analyses
    ):
        raise MarkdownContractError("Markdown analyses invalid")
    companies = _mappings(catalog.get("companies"), "companies")
    coverage_items = _mappings(coverage.get("companies"), "coverage")
    coverage_by_company = {
        _safe_key(item.get("company_key"), "coverage company"): item
        for item in coverage_items
    }
    units: dict[tuple[str, str], Mapping[str, object]] = {}
    for unit in analyses:
        if str(unit.get("bundle_id")) != str(bundle_id):
            raise MarkdownContractError("Markdown analysis bundle mismatch")
        kind = str(unit.get("kind", ""))
        scope_key = str(unit.get("scope_key", ""))
        if not kind or not scope_key or (kind, scope_key) in units:
            raise MarkdownContractError("Markdown analysis identity invalid")
        units[(kind, scope_key)] = unit

    company_matrix = aggregates.get("company_matrix", {})
    if not isinstance(company_matrix, Mapping):
        raise MarkdownContractError("Markdown company matrix invalid")

    files: dict[str, bytes] = {}
    routing: dict[str, Mapping[str, object]] = {}
    for company in sorted(companies, key=lambda item: str(item.get("company_key"))):
        company_key = _safe_key(company.get("company_key"), "company")
        name = _text(company.get("canonical_name"), maximum=256)
        state = str(coverage_by_company.get(company_key, {}).get("state", "not_observed"))
        path = f"agent/companies/{company_key}.md"
        files[path] = _render_unit(
            bundle_id=bundle_id,
            generated_at=generated_at,
            scope="company",
            scope_key=company_key,
            title=f"{html.unescape(name)}招聘情报",
            coverage=state,
            unit=units.get(("company", company_key)),
        )
        raw_company_dimensions = company_matrix.get(company_key, {})
        if not isinstance(raw_company_dimensions, Mapping):
            raise MarkdownContractError("Markdown company dimensions invalid")
        routing[path] = {
            "companies": [company_key],
            "tracks": _positive_keys(
                raw_company_dimensions.get("tracks"), "company tracks"
            ),
            "directions": _positive_keys(
                raw_company_dimensions.get("directions"), "company directions"
            ),
            "secondary_directions": _positive_keys(
                raw_company_dimensions.get("secondary_directions"),
                "company secondary directions",
            ),
            "job_families": _positive_keys(
                raw_company_dimensions.get("job_families"),
                "company job families",
            ),
            "locations": _positive_keys(
                raw_company_dimensions.get("locations"), "company locations"
            ),
            "seniority": _positive_keys(
                raw_company_dimensions.get("seniority"), "company seniority"
            ),
            "skills": _positive_keys(
                raw_company_dimensions.get("skills"), "company skills"
            ),
        }

    directions = aggregates.get("directions", {})
    if not isinstance(directions, Mapping):
        raise MarkdownContractError("Markdown directions invalid")
    for direction in sorted(str(key) for key in directions):
        slug = _DIRECTION_SLUGS.get(direction)
        if slug is None:
            continue
        path = f"agent/directions/{slug}.md"
        files[path] = _render_unit(
            bundle_id=bundle_id,
            generated_at=generated_at,
            scope="direction",
            scope_key=slug,
            title=f"{direction}方向招聘情报",
            coverage="succeeded",
            unit=units.get(("direction", direction)),
        )
        raw_secondary = aggregates.get("secondary_directions", {})
        secondary = _positive_keys(raw_secondary, "secondary directions")
        routing[path] = {
            "directions": [direction],
            "secondary_directions": [
                item for item in secondary if item.startswith(f"{direction}/")
            ],
        }

    for (kind, scope_key), unit in sorted(units.items()):
        if kind != "secondary-direction":
            continue
        if "/" not in scope_key:
            raise MarkdownContractError("Markdown secondary direction invalid")
        direction = scope_key.split("/", 1)[0]
        path = f"agent/directions/{_secondary_slug(scope_key)}.md"
        files[path] = _render_unit(
            bundle_id=bundle_id,
            generated_at=generated_at,
            scope="secondary-direction",
            scope_key=_secondary_slug(scope_key),
            title=f"{scope_key}招聘情报",
            coverage="succeeded",
            unit=unit,
        )
        routing[path] = {
            "scope": "secondary-direction",
            "scope_key": scope_key,
            "directions": [direction],
            "secondary_directions": [scope_key],
            "priority": 175,
        }

    for topic_key, filename in _TOPIC_FILES.items():
        unit = units.get(("track", topic_key)) or units.get(("topic", topic_key))
        if topic_key == "talent-competition":
            unit = unit or units.get(("comparison", "all-companies"))
        path = f"agent/topics/{filename}.md"
        files[path] = _render_unit(
            bundle_id=bundle_id,
            generated_at=generated_at,
            scope="topic",
            scope_key=filename,
            title=f"{filename} 招聘专题",
            coverage="succeeded",
            unit=unit,
        )
        routing[path] = {
            "tracks": [topic_key] if topic_key in {"social", "campus", "intern"} else []
        }

    executive = units.get(("executive-summary", "all-companies"))
    files["agent/executive-brief.md"] = _render_unit(
        bundle_id=bundle_id,
        generated_at=generated_at,
        scope="executive",
        scope_key="all-companies",
        title="招聘情报管理摘要",
        coverage="partial"
        if any(
            str(item.get("state")) in {"partial", "failed", "not_observed"}
            for item in coverage_items
        )
        else "succeeded",
        unit=executive,
    )

    recommendations_by_file: dict[str, list[str]] = {
        filename: [] for filename in _TASK_TITLES
    }
    for unit in analyses:
        response = _response(unit)
        for recommendation in _mappings(
            response.get("recommendations", []), "recommendations"
        ):
            tasks = recommendation.get("target_tasks", [])
            if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
                raise MarkdownContractError("Markdown recommendation tasks invalid")
            for task in tasks:
                filename = _TASK_FILES.get(str(task))
                if filename is not None:
                    recommendations_by_file[filename].append(
                        _text(recommendation.get("text"))
                    )
    for filename, title in _TASK_TITLES.items():
        task_kinds = tuple(
            key for key, value in _TASK_FILES.items() if value == filename
        )
        lines = _front_matter(
            bundle_id=bundle_id,
            observed_at=generated_at,
            scope="task",
            scope_key=filename,
            confidence="medium" if recommendations_by_file[filename] else "low",
            coverage="succeeded",
            evidence_count=0,
        )
        lines.extend(
            [
                f"# {title}",
                "",
                "## 使用边界",
                "",
                "外部招聘情报用于增强判断，不得覆盖用户提供的岗位或候选人事实。",
                "",
                "## 当前任务建议",
                "",
            ]
        )
        suggestions = sorted(set(recommendations_by_file[filename]))
        lines.extend(
            [f"- {item}" for item in suggestions]
            or ["- 当前 Bundle 没有该任务的专项建议。"]
        )
        lines.append("")
        path = f"agent/tasks/{filename}.md"
        files[path] = "\n".join(lines).encode("utf-8")
        routing[path] = {"task_kinds": task_kinds}

    index_lines = _front_matter(
        bundle_id=bundle_id,
        observed_at=generated_at,
        scope="index",
        scope_key="usage-boundary",
        confidence="high",
        coverage="partial"
        if any(
            str(item.get("state")) in {"partial", "failed", "not_observed"}
            for item in coverage_items
        )
        else "succeeded",
        evidence_count=0,
    )
    index_lines.extend(
        [
            "# HR Agent 招聘情报索引",
            "",
            "## 使用边界",
            "",
            "- 公开岗位数量只表示当前 Bundle 的公开招聘信号，不等于 HC、预算或研发投入。",
            "- 事实必须保留来源与观察时间；AI 研判不得改写为公开事实。",
            "- 失败、部分覆盖和未观察来源不代表企业没有招聘。",
            "- 单次基线不能判断月度增长、收缩或资源迁移趋势。",
            "",
            "## 文档索引",
            "",
        ]
    )
    index_lines.extend(f"- `{path}`" for path in sorted(files))
    index_lines.append("")
    files["agent/index.md"] = "\n".join(index_lines).encode("utf-8")
    routing["agent/index.md"] = {"priority": 1000}

    chunks = build_chunk_index(files, routing)
    index = (_canonical_json([item.as_dict() for item in chunks]) + "\n").encode(
        "utf-8"
    )
    package_files = dict(files) | {"agent/chunk-index.json": index}
    validate_chunk_index(package_files, chunks)
    return AgentMarkdownPackage(MappingProxyType(package_files), chunks)


__all__ = [
    "AgentMarkdownPackage",
    "MarkdownContractError",
    "compile_agent_markdown",
]
