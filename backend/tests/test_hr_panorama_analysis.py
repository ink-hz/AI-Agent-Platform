from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.agent_brain.model_adapter import BrainModelManifest, BrainModelResponse
from app.hr.panorama_analysis import (
    ConfiguredPanoramaModel,
    PanoramaAnalysisError,
    PanoramaAnalyzer,
    validate_analysis,
)
from app.hr.panorama_models import PublicJobSnapshot

NOW = datetime(2026, 9, 6, 8, tzinfo=timezone.utc)


def snapshot(*, title: str = "高级结构工程师") -> PublicJobSnapshot:
    return PublicJobSnapshot(
        snapshot_id=uuid4(),
        owner_id=uuid4(),
        origin_request_id=uuid4(),
        run_id=None,
        source_id=uuid4(),
        public_job_key="structure-1",
        title=title,
        location="深圳",
        duty_excerpt="负责精密结构研发和量产导入",
        requirement_excerpt="五年以上结构设计经验",
        source_url="https://example.com/jobs/structure-1",
        observed_at=NOW,
        content_sha256="a" * 64,
        status="open",
        created_at=NOW,
        production_batch_id=uuid4(),
    )


def analysis_for(selected: PublicJobSnapshot) -> dict[str, object]:
    return {
        "facts": [
            {
                "fact_id": "fact-1",
                "text": "公开招聘高级结构工程师",
                "snapshot_id": str(selected.snapshot_id),
                "observation_id": str(selected.origin_request_id),
                "source_url": selected.source_url,
                "observed_at": selected.observed_at.isoformat(),
            }
        ],
        "inferences": [
            {"text": "精密结构与量产导入是投入重点", "basis_fact_ids": ["fact-1"]}
        ],
        "unknowns": [{"text": "实际 HC 与预算未公开"}],
        "direction_clusters": {"结构": 1, "制造工艺": 1},
        "summary": "结构研发与制造协同需求明确。",
    }


def test_validator_rejects_an_inference_without_basis_facts() -> None:
    selected = snapshot()
    payload = analysis_for(selected)
    payload["inferences"] = [{"text": "正在扩张", "basis_fact_ids": []}]

    with pytest.raises(PanoramaAnalysisError, match="basis"):
        validate_analysis(payload, snapshots=(selected,))


def test_validator_rejects_a_fact_with_an_unknown_source_url() -> None:
    selected = snapshot()
    payload = analysis_for(selected)
    payload["facts"][0]["source_url"] = "https://forged.example/jobs"  # type: ignore[index]

    with pytest.raises(PanoramaAnalysisError, match="evidence"):
        validate_analysis(payload, snapshots=(selected,))


def test_validator_rejects_a_fact_with_a_forged_observation() -> None:
    selected = snapshot()
    payload = analysis_for(selected)
    payload["facts"][0]["observation_id"] = str(uuid4())  # type: ignore[index]

    with pytest.raises(PanoramaAnalysisError, match="evidence"):
        validate_analysis(payload, snapshots=(selected,))


class Model:
    version = "gpt-research-v1"

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def generate_json(self, stage: str, payload: dict[str, object]):
        self.calls.append((stage, payload))
        return self.response


@pytest.mark.asyncio
async def test_analyzer_uses_complete_raw_jobs_and_records_model_identity() -> None:
    selected = snapshot()
    model = Model(analysis_for(selected))
    analyzer = PanoramaAnalyzer(model)

    result = await analyzer.analyze_company("测试公司", (selected,))

    assert result.model_version == "gpt-research-v1"
    assert result.facts[0]["snapshot_id"] == str(selected.snapshot_id)
    stage, payload = model.calls[0]
    assert stage == "company"
    assert payload["company"] == "测试公司"
    assert payload["jobs"][0]["duty_excerpt"] == selected.duty_excerpt  # type: ignore[index]
    requirements = "\n".join(payload["analysis_requirements"])
    assert "招聘资源集中度" in requirements
    assert "产品路线信号" in requirements
    assert "与奥比中光岗位的启示" in requirements


@pytest.mark.asyncio
async def test_analysis_can_be_regenerated_without_mutating_raw_snapshots() -> None:
    selected = snapshot()
    original = selected
    first = await PanoramaAnalyzer(Model(analysis_for(selected))).compile_report(
        (selected,), company_analyses=(), topic_analyses=()
    )
    stronger_payload = analysis_for(selected)
    stronger_payload["summary"] = "更强模型给出的独立新分析。"
    second = await PanoramaAnalyzer(Model(stronger_payload)).compile_report(
        (selected,), company_analyses=(), topic_analyses=()
    )

    assert first.summary != second.summary
    assert selected == original
    assert first.snapshot_ids == second.snapshot_ids == (selected.snapshot_id,)


class HierarchicalModel:
    version = "gpt-research-hierarchical-v1"

    def __init__(self, snapshots: tuple[PublicJobSnapshot, ...]) -> None:
        self.snapshots = {str(item.snapshot_id): item for item in snapshots}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _snapshot_id(self, value: object) -> str | None:
        if isinstance(value, dict):
            selected = value.get("snapshot_id")
            if isinstance(selected, str) and selected in self.snapshots:
                return selected
            for nested in value.values():
                found = self._snapshot_id(nested)
                if found:
                    return found
        if isinstance(value, list):
            for nested in value:
                found = self._snapshot_id(nested)
                if found:
                    return found
        return None

    async def generate_json(self, stage: str, payload: dict[str, object]):
        self.calls.append((stage, payload))
        selected_id = self._snapshot_id(payload)
        assert selected_id is not None
        return analysis_for(self.snapshots[selected_id])


@pytest.mark.asyncio
async def test_large_company_analysis_is_hierarchical_and_never_drops_raw_jobs() -> None:
    base = snapshot()
    snapshots = tuple(
        replace(
            base,
            snapshot_id=uuid4(),
            origin_request_id=uuid4(),
            public_job_key=f"job-{index}",
            title=f"研发岗位 {index}",
            duty_excerpt="完整岗位职责" * 3000,
            requirement_excerpt="完整任职要求" * 3000,
        )
        for index in range(24)
    )
    model = HierarchicalModel(snapshots)

    result = await PanoramaAnalyzer(model).analyze_company("大型研发公司", snapshots)

    assert len(model.calls) >= 3
    assert all(
        len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        <= 786_432
        for _, payload in model.calls
    )
    raw_job_ids = {
        job["snapshot_id"]
        for _, payload in model.calls
        for job in payload.get("jobs", [])
    }
    assert raw_job_ids == {str(item.snapshot_id) for item in snapshots}
    assert result.snapshot_ids == tuple(item.snapshot_id for item in snapshots)


class BrainAdapter:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = []

    def complete(self, request, *, on_thinking_delta=None):
        self.requests.append(request)
        return BrainModelResponse(
            provider_request_id="provider-request-1",
            content_blocks=(
                {
                    "type": "text",
                    "text": __import__("json").dumps(self.payload, ensure_ascii=False),
                },
            ),
            stop_reason="end_turn",
        )


@pytest.mark.asyncio
async def test_configured_model_adapter_uses_deployment_manifest() -> None:
    selected = snapshot()
    brain = BrainAdapter(analysis_for(selected))
    manifest = BrainModelManifest(
        config_version="brain-opus5-v1",
        provider_kind="anthropic_compatible",
        model_id="claude-opus-5",
        context_profile="opus_1m",
        context_window=1_000_000,
        thinking_type="adaptive",
        thinking_display="summarized",
        thinking_effort="xhigh",
        thinking_effort_routing="xhigh",
        max_output_tokens=65_536,
        max_answer_bytes=65_536,
        prompt_cache_enabled=True,
        stable_cache_ttl="1h",
        rolling_cache_ttl="5m",
        system_prompt_sha256="a" * 64,
    )
    model = ConfiguredPanoramaModel(brain, manifest)

    response = await model.generate_json("report", {"jobs": []})

    assert response["summary"] == analysis_for(selected)["summary"]
    assert model.version == "anthropic_compatible:claude-opus-5:brain-opus5-v1"
    request = brain.requests[0]
    assert request.model_id == "claude-opus-5"
    assert request.effort == "xhigh"
    assert request.tools == ()
