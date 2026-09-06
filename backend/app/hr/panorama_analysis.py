from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from app.agent_brain.model_adapter import (
    BrainModelManifest,
    BrainModelRequest,
)

from .panorama_models import (
    CreateTalentInsightVersion,
    PublicJobSnapshot,
    thaw_json,
)

TECHNICAL_DIRECTIONS = frozenset(
    {
        "光学",
        "硬件",
        "结构",
        "软件",
        "算法",
        "制造工艺",
        "质量",
        "产品",
        "供应链",
        "其他",
    }
)
ANALYSIS_REQUIREMENTS = (
    "按技术方向识别招聘资源集中度与变化",
    "识别产品路线信号、业务方向和区域布局",
    "区分社招与校招、岗位新增与消失、团队形态变化",
    "比较公司间的共同点、差异和竞争性人才需求",
    "给出与奥比中光岗位的启示，但不得把推断写成事实",
    "明确未知项、数据覆盖缺口和不能从公开岗位推出的结论",
)
_TOP_LEVEL_KEYS = {
    "facts",
    "inferences",
    "unknowns",
    "direction_clusters",
    "summary",
}


class PanoramaAnalysisError(RuntimeError):
    pass


class PanoramaModelAdapter(Protocol):
    version: str

    async def generate_json(
        self, stage: str, payload: dict[str, object]
    ) -> Mapping[str, object]: ...


class ConfiguredPanoramaModel:
    """Run panorama analysis through the model selected by deployment config."""

    _SYSTEM = """你是招聘情报首席分析师。输入是代码采集并归档的公开岗位原始数据。
只返回一个严格 JSON 对象，不要 Markdown、代码围栏或额外说明。对象必须且只能包含：
facts、inferences、unknowns、direction_clusters、summary。
事实必须逐条原样引用输入中的 snapshot_id、observation_id、source_url、observed_at；
推断必须引用一个或多个 fact_id。不得把推断冒充事实，不得补造岗位、公司或数字。
需要深度比较技术方向、招聘资源集中度、产品和业务路线、区域布局、社招校招、岗位变化、
竞争人才需求及对奥比中光的启示；证据不足的内容必须进入 unknowns。"""

    def __init__(self, adapter: object, manifest: BrainModelManifest) -> None:
        if not hasattr(adapter, "complete") or not isinstance(
            manifest, BrainModelManifest
        ):
            raise ValueError("configured panorama model invalid")
        self._adapter = adapter
        self._manifest = manifest
        self.version = (
            f"{manifest.provider_kind}:{manifest.model_id}:{manifest.config_version}"
        )

    async def generate_json(
        self, stage: str, payload: dict[str, object]
    ) -> Mapping[str, object]:
        if stage not in {"company", "topic", "report"}:
            raise ValueError("analysis stage invalid")
        prompt = json.dumps(
            {"stage": stage, "input": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        request = BrainModelRequest(
            model_id=self._manifest.model_id,
            max_tokens=min(self._manifest.max_output_tokens, 65_536),
            thinking_display=self._manifest.thinking_display,
            effort=self._manifest.thinking_effort,
            tools=(),
            system=({"type": "text", "text": self._SYSTEM},),
            messages=({"role": "user", "content": prompt},),
            cache_breakpoints=(),
        )
        response = await asyncio.to_thread(self._adapter.complete, request)
        if not hasattr(response, "public_blocks") or response.stop_reason != "end_turn":
            raise PanoramaAnalysisError("analysis model response incomplete")
        text = "".join(
            str(block.get("text", ""))
            for block in response.public_blocks
            if block.get("type") == "text"
        ).strip()
        if not text or len(text.encode()) > self._manifest.max_answer_bytes:
            raise PanoramaAnalysisError("analysis model response invalid")
        try:
            value = json.loads(text)
        except (UnicodeError, json.JSONDecodeError):
            raise PanoramaAnalysisError("analysis model JSON invalid") from None
        if not isinstance(value, dict):
            raise PanoramaAnalysisError("analysis model JSON invalid")
        return value


@dataclass(frozen=True, slots=True)
class PanoramaAnalysis:
    facts: tuple[Mapping[str, object], ...]
    inferences: tuple[Mapping[str, object], ...]
    unknowns: tuple[Mapping[str, object], ...]
    direction_clusters: Mapping[str, object]
    summary: str
    source_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    model_version: str


def _canonical_size(value: object) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
    except (TypeError, UnicodeError, ValueError):
        raise PanoramaAnalysisError("analysis JSON invalid") from None


def _snapshot_index(
    snapshots: tuple[PublicJobSnapshot, ...],
) -> dict[UUID, PublicJobSnapshot]:
    if (
        not isinstance(snapshots, tuple)
        or not snapshots
        or len(snapshots) > 1000
        or any(not isinstance(item, PublicJobSnapshot) for item in snapshots)
        or len({item.snapshot_id for item in snapshots}) != len(snapshots)
    ):
        raise PanoramaAnalysisError("analysis evidence invalid")
    owners = {item.owner_id for item in snapshots}
    batches = {item.production_batch_id for item in snapshots}
    if len(owners) != 1 or len(batches) != 1 or None in batches:
        raise PanoramaAnalysisError("analysis evidence invalid")
    return {item.snapshot_id: item for item in snapshots}


def validate_analysis(
    payload: Mapping[str, object],
    *,
    snapshots: tuple[PublicJobSnapshot, ...],
    model_version: str = "validation-only",
) -> PanoramaAnalysis:
    if not isinstance(payload, Mapping) or set(payload) != _TOP_LEVEL_KEYS:
        raise PanoramaAnalysisError("analysis schema invalid")
    if _canonical_size(payload) > 786_432:
        raise PanoramaAnalysisError("analysis payload too large")
    by_id = _snapshot_index(snapshots)
    raw_facts = payload.get("facts")
    raw_inferences = payload.get("inferences")
    if not isinstance(raw_facts, (list, tuple)) or not raw_facts:
        raise PanoramaAnalysisError("analysis facts invalid")
    fact_ids: set[str] = set()
    for fact in raw_facts:
        if not isinstance(fact, Mapping):
            raise PanoramaAnalysisError("analysis facts invalid")
        try:
            fact_id = str(fact["fact_id"]).strip()
            selected = by_id[UUID(str(fact["snapshot_id"]))]
            observation_id = UUID(str(fact["observation_id"]))
            observed_at = datetime.fromisoformat(
                str(fact["observed_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            raise PanoramaAnalysisError("analysis evidence invalid") from None
        if (
            not fact_id
            or fact_id in fact_ids
            or set(fact)
            != {
                "fact_id",
                "text",
                "snapshot_id",
                "observation_id",
                "source_url",
                "observed_at",
            }
            or observation_id != (selected.observation_id or selected.origin_request_id)
            or fact.get("source_url") != selected.source_url
            or observed_at != selected.observed_at
        ):
            raise PanoramaAnalysisError("analysis evidence binding invalid")
        fact_ids.add(fact_id)
    if not isinstance(raw_inferences, (list, tuple)):
        raise PanoramaAnalysisError("analysis inference invalid")
    for inference in raw_inferences:
        basis = (
            inference.get("basis_fact_ids") if isinstance(inference, Mapping) else None
        )
        if (
            not isinstance(inference, Mapping)
            or set(inference) != {"text", "basis_fact_ids"}
            or not isinstance(basis, (list, tuple))
            or not basis
            or any(
                not isinstance(value, str) or value not in fact_ids for value in basis
            )
        ):
            raise PanoramaAnalysisError("analysis inference basis invalid")
    clusters = payload.get("direction_clusters")
    if not isinstance(clusters, Mapping) or any(
        key not in TECHNICAL_DIRECTIONS
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in clusters.items()
    ):
        raise PanoramaAnalysisError("analysis direction clusters invalid")
    source_ids = tuple(dict.fromkeys(item.source_id for item in snapshots))
    try:
        command = CreateTalentInsightVersion(
            insight_version_id=uuid4(),
            owner_id=snapshots[0].owner_id,
            client_request_id=uuid4(),
            run_id=None,
            selected_source_ids=source_ids,
            snapshot_ids=tuple(by_id),
            facts=tuple(raw_facts),
            inferences=tuple(raw_inferences),
            unknowns=tuple(payload.get("unknowns", ())),  # type: ignore[arg-type]
            direction_clusters=clusters,
            summary=payload.get("summary"),  # type: ignore[arg-type]
            source_conversation_id=None,
            source_turn_id=None,
            agent_id="hr-intelligence-producer",
            model_version=model_version,
            production_batch_id=snapshots[0].production_batch_id,
        )
    except (TypeError, ValueError):
        raise PanoramaAnalysisError("analysis schema invalid") from None
    return PanoramaAnalysis(
        facts=command.facts,
        inferences=command.inferences,
        unknowns=command.unknowns,
        direction_clusters=command.direction_clusters,
        summary=command.summary,
        source_ids=command.selected_source_ids,
        snapshot_ids=command.snapshot_ids,
        model_version=command.model_version,
    )


def _job_payload(snapshot: PublicJobSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": str(snapshot.snapshot_id),
        "observation_id": str(snapshot.observation_id or snapshot.origin_request_id),
        "source_id": str(snapshot.source_id),
        "public_job_key": snapshot.public_job_key,
        "title": snapshot.title,
        "location": snapshot.location,
        "duty_excerpt": snapshot.duty_excerpt,
        "requirement_excerpt": snapshot.requirement_excerpt,
        "source_url": snapshot.source_url,
        "observed_at": snapshot.observed_at.isoformat(),
        "content_sha256": snapshot.content_sha256,
        "status": snapshot.status,
    }


def _analysis_payload(value: PanoramaAnalysis) -> dict[str, object]:
    return {
        "facts": thaw_json(value.facts),
        "inferences": thaw_json(value.inferences),
        "unknowns": thaw_json(value.unknowns),
        "direction_clusters": thaw_json(value.direction_clusters),
        "summary": value.summary,
        "model_version": value.model_version,
    }


class PanoramaAnalyzer:
    def __init__(self, model: PanoramaModelAdapter) -> None:
        version = getattr(model, "version", None)
        if (
            not hasattr(model, "generate_json")
            or not isinstance(version, str)
            or not 1 <= len(version.strip()) <= 160
        ):
            raise ValueError("panorama model adapter invalid")
        self._model = model
        self.model_version = version.strip()

    async def analyze_company(
        self,
        company_name: str,
        snapshots: tuple[PublicJobSnapshot, ...],
    ) -> PanoramaAnalysis:
        if not isinstance(company_name, str) or not company_name.strip():
            raise ValueError("company name required")
        return await self._analyze(
            "company",
            {
                "company": company_name.strip(),
                "jobs": [_job_payload(item) for item in snapshots],
                "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
                "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
            },
            snapshots,
        )

    async def analyze_topic(
        self,
        topic: str,
        snapshots: tuple[PublicJobSnapshot, ...],
        company_analyses: tuple[PanoramaAnalysis, ...],
    ) -> PanoramaAnalysis:
        if topic not in {"社会招聘", "校园招聘"}:
            raise ValueError("analysis topic invalid")
        return await self._analyze(
            "topic",
            {
                "topic": topic,
                "jobs": [_job_payload(item) for item in snapshots],
                "company_analyses": [
                    _analysis_payload(value) for value in company_analyses
                ],
                "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
                "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
            },
            snapshots,
        )

    async def compile_report(
        self,
        snapshots: tuple[PublicJobSnapshot, ...],
        *,
        company_analyses: tuple[PanoramaAnalysis, ...],
        topic_analyses: tuple[PanoramaAnalysis, ...],
    ) -> PanoramaAnalysis:
        return await self._analyze(
            "report",
            {
                "jobs": [_job_payload(item) for item in snapshots],
                "company_analyses": [
                    _analysis_payload(value) for value in company_analyses
                ],
                "topic_analyses": [
                    _analysis_payload(value) for value in topic_analyses
                ],
                "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
                "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            snapshots,
        )

    async def _analyze(
        self,
        stage: str,
        prompt: dict[str, object],
        snapshots: tuple[PublicJobSnapshot, ...],
    ) -> PanoramaAnalysis:
        _snapshot_index(snapshots)
        if _canonical_size(prompt) > 786_432:
            raise PanoramaAnalysisError("analysis prompt too large")
        try:
            response = await self._model.generate_json(stage, prompt)
        except PanoramaAnalysisError:
            raise
        except Exception:  # noqa: BLE001 - provider errors are sanitized at this boundary
            raise PanoramaAnalysisError("analysis model unavailable") from None
        return validate_analysis(
            response,
            snapshots=snapshots,
            model_version=self.model_version,
        )


__all__ = [
    "ANALYSIS_REQUIREMENTS",
    "TECHNICAL_DIRECTIONS",
    "ConfiguredPanoramaModel",
    "PanoramaAnalysis",
    "PanoramaAnalysisError",
    "PanoramaAnalyzer",
    "PanoramaModelAdapter",
    "validate_analysis",
]
