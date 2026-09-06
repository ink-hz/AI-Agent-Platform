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
    "量化公开岗位数并按光学、硬件、结构、软件、算法、制造工艺等技术方向识别招聘资源集中度；岗位数不得等同于HC、预算或实际研发投入",
    "从岗位职责和要求提取可核验的技术栈、能力组合、资历层级、团队接口与量产阶段信号",
    "识别产品路线信号、业务场景、客户行业和区域布局，并为每个路线判断给出事实链",
    "区分社招、校招和实习；只有存在跨期证据时才判断岗位新增、消失或团队形态变化",
    "横向比较各公司的共同技术主题、差异化能力、竞争性人才需求和潜在组织建设重点",
    "给出与奥比中光岗位的启示，并转化为JD、JR、人才画像、搜寻策略和面试方案可直接使用的建议，但不得把建议写成事实",
    "主动寻找反证和替代解释，明确未知项、来源覆盖缺口、样本偏差以及不能从公开岗位推出的结论",
)
_TOP_LEVEL_KEYS = {
    "facts",
    "inferences",
    "unknowns",
    "direction_clusters",
    "summary",
}
_MAX_PROMPT_BYTES = 786_432
_TARGET_PROMPT_BYTES = 589_824


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
分析不是复述岗位清单：先做定量分布和能力结构，再建立“岗位事实→技术/产品信号→替代解释→
对奥比中光招聘动作”的证据链。招聘帖数量只是公开需求信号，不代表HC、预算、产量或真实投入。
需要深度比较技术方向、技术栈、资历层级、招聘资源集中度、产品和业务路线、区域布局、社招校招、
跨期岗位变化、竞争人才需求及对奥比中光的启示；主动指出反证，证据不足的内容必须进入 unknowns。
当输入是分块或多阶段分析时，必须综合所有分块，保留关键事实引用，不得只根据第一个分块作答。"""

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
        or len(snapshots) > 10000
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
        base = {
            "company": company_name.strip(),
            "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
            "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
        }
        prompt = {**base, "jobs": [_job_payload(item) for item in snapshots]}
        if _canonical_size(prompt) <= _MAX_PROMPT_BYTES:
            return await self._analyze("company", prompt, snapshots)
        chunks = self._snapshot_chunks(snapshots, base)
        analyses = []
        for index, chunk in enumerate(chunks, start=1):
            analyses.append(
                await self._analyze(
                    "company",
                    {
                        **base,
                        "phase": "evidence_chunk",
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "jobs": [_job_payload(item) for item in chunk],
                    },
                    chunk,
                )
            )
        return await self._synthesize(
            "company",
            {
                **base,
                "phase": "company_synthesis",
                "total_job_count": len(snapshots),
            },
            "chunk_analyses",
            tuple(analyses),
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
        base = {
            "topic": topic,
            "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
            "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
        }
        prompt = {
            **base,
            "jobs": [_job_payload(item) for item in snapshots],
            "company_analyses": [
                _analysis_payload(value) for value in company_analyses
            ],
        }
        if _canonical_size(prompt) <= _MAX_PROMPT_BYTES:
            return await self._analyze("topic", prompt, snapshots)
        chunks = self._snapshot_chunks(snapshots, base)
        chunk_analyses = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_analyses.append(
                await self._analyze(
                    "topic",
                    {
                        **base,
                        "phase": "evidence_chunk",
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "jobs": [_job_payload(item) for item in chunk],
                    },
                    chunk,
                )
            )
        return await self._synthesize(
            "topic",
            {
                **base,
                "phase": "topic_synthesis",
                "total_job_count": len(snapshots),
            },
            "analysis_parts",
            tuple(chunk_analyses),
            snapshots,
        )

    async def compile_report(
        self,
        snapshots: tuple[PublicJobSnapshot, ...],
        *,
        company_analyses: tuple[PanoramaAnalysis, ...],
        topic_analyses: tuple[PanoramaAnalysis, ...],
    ) -> PanoramaAnalysis:
        base = {
            "phase": "cross_company_report",
            "total_job_count": len(snapshots),
            "company_count": len({item.source_id for item in snapshots}),
            "analysis_requirements": list(ANALYSIS_REQUIREMENTS),
            "output_contract": "facts/inferences/unknowns/direction_clusters/summary",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        analysis_parts = company_analyses + topic_analyses
        direct_prompt = {
            **base,
            "jobs": [_job_payload(item) for item in snapshots],
            "company_analyses": [
                _analysis_payload(value) for value in company_analyses
            ],
            "topic_analyses": [
                _analysis_payload(value) for value in topic_analyses
            ],
        }
        if _canonical_size(direct_prompt) <= _MAX_PROMPT_BYTES:
            return await self._analyze("report", direct_prompt, snapshots)
        if analysis_parts:
            return await self._synthesize(
                "report",
                base,
                "analysis_parts",
                analysis_parts,
                snapshots,
            )
        chunks = self._snapshot_chunks(snapshots, base)
        chunk_analyses = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_analyses.append(
                await self._analyze(
                    "report",
                    {
                        **base,
                        "phase": "evidence_chunk",
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "jobs": [_job_payload(item) for item in chunk],
                    },
                    chunk,
                )
            )
        return await self._synthesize(
            "report",
            base,
            "analysis_parts",
            tuple(chunk_analyses),
            snapshots,
        )

    @staticmethod
    def _snapshot_chunks(
        snapshots: tuple[PublicJobSnapshot, ...], base: dict[str, object]
    ) -> tuple[tuple[PublicJobSnapshot, ...], ...]:
        chunks: list[tuple[PublicJobSnapshot, ...]] = []
        current: list[PublicJobSnapshot] = []
        for snapshot in snapshots:
            candidate = [*current, snapshot]
            prompt = {
                **base,
                "phase": "evidence_chunk",
                "chunk_index": len(chunks) + 1,
                "chunk_count": len(snapshots),
                "jobs": [_job_payload(item) for item in candidate],
            }
            if current and _canonical_size(prompt) > _TARGET_PROMPT_BYTES:
                chunks.append(tuple(current))
                current = [snapshot]
            else:
                current = candidate
            if _canonical_size(
                {**base, "jobs": [_job_payload(item) for item in current]}
            ) > _MAX_PROMPT_BYTES:
                raise PanoramaAnalysisError("analysis evidence item too large")
        if current:
            chunks.append(tuple(current))
        return tuple(chunks)

    async def _synthesize(
        self,
        stage: str,
        base: dict[str, object],
        key: str,
        analyses: tuple[PanoramaAnalysis, ...],
        snapshots: tuple[PublicJobSnapshot, ...],
    ) -> PanoramaAnalysis:
        if not analyses:
            raise PanoramaAnalysisError("analysis synthesis input invalid")
        pending = analyses
        level = 1
        while True:
            groups: list[tuple[PanoramaAnalysis, ...]] = []
            current: list[PanoramaAnalysis] = []
            for analysis in pending:
                candidate = [*current, analysis]
                prompt = {
                    **base,
                    "synthesis_level": level,
                    key: [_analysis_payload(item) for item in candidate],
                }
                if current and _canonical_size(prompt) > _TARGET_PROMPT_BYTES:
                    groups.append(tuple(current))
                    current = [analysis]
                else:
                    current = candidate
                if _canonical_size(
                    {
                        **base,
                        "synthesis_level": level,
                        key: [_analysis_payload(item) for item in current],
                    }
                ) > _MAX_PROMPT_BYTES:
                    raise PanoramaAnalysisError("analysis synthesis item too large")
            if current:
                groups.append(tuple(current))
            synthesized = []
            for group in groups:
                snapshot_ids = {
                    snapshot_id
                    for analysis in group
                    for snapshot_id in analysis.snapshot_ids
                }
                selected = tuple(
                    item for item in snapshots if item.snapshot_id in snapshot_ids
                )
                synthesized.append(
                    await self._analyze(
                        stage,
                        {
                            **base,
                            "synthesis_level": level,
                            key: [_analysis_payload(item) for item in group],
                        },
                        selected,
                    )
                )
            if len(synthesized) == 1:
                return synthesized[0]
            pending = tuple(synthesized)
            level += 1
            if level > 16:
                raise PanoramaAnalysisError("analysis synthesis did not converge")

    async def _analyze(
        self,
        stage: str,
        prompt: dict[str, object],
        snapshots: tuple[PublicJobSnapshot, ...],
    ) -> PanoramaAnalysis:
        _snapshot_index(snapshots)
        if _canonical_size(prompt) > _MAX_PROMPT_BYTES:
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
