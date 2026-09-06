from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .dimensions import recruitment_track, technical_directions
from .models import NormalizedJob
from .public_documents import PublicIntelligenceDocument
from .taxonomy import secondary_directions

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_KINDS = frozenset(
    {
        "company",
        "track",
        "direction",
        "secondary-direction",
        "comparison",
        "topic",
        "executive-summary",
        "task",
    }
)
_CONFIDENCE = frozenset({"low", "medium", "high"})
_CLAIM_TYPES = frozenset(
    {
        "recruiting_signal",
        "product_route",
        "business_direction",
        "organization_chain",
        "talent_competition",
        "geography",
        "trend",
        "task_guidance",
    }
)
_RESPONSE_V1_KEYS = frozenset(
    {"facts", "inferences", "unknowns", "alternatives", "summary", "confidence"}
)
_RESPONSE_V2_KEYS = frozenset(
    {
        "schema_version",
        "facts",
        "inferences",
        "unknowns",
        "alternatives",
        "recommendations",
        "summary",
        "confidence",
    }
)
_TOPIC_SCOPES = ("product-routes", "talent-competition", "geography", "trends")
_TASK_SCOPES = ("jd-jr", "talent-profile", "sourcing", "resume-review", "interview")
_TARGET_TASKS = frozenset(
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
_USAGE_KEYS = frozenset(
    {
        "unit_id",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "unavailable_reason",
    }
)


class AnalysisContractError(ValueError):
    pass


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
        raise AnalysisContractError("analysis JSON invalid") from None


def _required_text(value: object, maximum: int, label: str) -> str:
    selected = value.strip() if isinstance(value, str) else ""
    if not selected or len(selected) > maximum:
        raise AnalysisContractError(f"{label} invalid")
    return selected


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    job_id: UUID | None
    sha256: str
    source_url: str
    observed_at: datetime
    evidence_kind: str = "job"
    evidence_id: UUID | None = None
    trust_tier: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_kind not in {"job", "public_document"}:
            raise AnalysisContractError("analysis evidence invalid")
        if self.evidence_kind == "job":
            if not isinstance(self.job_id, UUID) or self.trust_tier is not None:
                raise AnalysisContractError("analysis evidence invalid")
            evidence_id = self.job_id if self.evidence_id is None else self.evidence_id
        else:
            if self.job_id is not None or self.trust_tier not in {"primary", "secondary"}:
                raise AnalysisContractError("analysis evidence invalid")
            evidence_id = self.evidence_id
        if not isinstance(evidence_id, UUID):
            raise AnalysisContractError("analysis evidence invalid")
        object.__setattr__(self, "evidence_id", evidence_id)
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise AnalysisContractError("analysis evidence invalid")
        if not isinstance(self.source_url, str) or not self.source_url.startswith(
            "https://"
        ):
            raise AnalysisContractError("analysis evidence invalid")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
        ):
            raise AnalysisContractError("analysis evidence invalid")

    def as_dict(self) -> dict[str, str]:
        result = {
            "evidence_kind": self.evidence_kind,
            "evidence_id": str(self.evidence_id),
            "sha256": self.sha256,
            "source_url": self.source_url,
            "observed_at": self.observed_at.isoformat(),
        }
        if self.job_id is not None:
            result["job_id"] = str(self.job_id)
        if self.trust_tier is not None:
            result["trust_tier"] = self.trust_tier
        return result


@dataclass(frozen=True, slots=True)
class AnalysisUnit:
    bundle_id: UUID
    unit_id: UUID
    kind: str
    scope_key: str
    input_sha256: str
    evidence: tuple[EvidenceReference, ...]
    request_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundle_id, UUID) or not isinstance(self.unit_id, UUID):
            raise AnalysisContractError("analysis identity invalid")
        if self.kind not in _KINDS:
            raise AnalysisContractError("analysis kind invalid")
        _required_text(self.scope_key, 256, "analysis scope")
        if (
            not isinstance(self.input_sha256, str)
            or _SHA256.fullmatch(self.input_sha256) is None
        ):
            raise AnalysisContractError("analysis input hash invalid")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, EvidenceReference) for item in self.evidence
        ):
            raise AnalysisContractError("analysis evidence invalid")
        try:
            request = json.loads(self.request_json)
        except (TypeError, json.JSONDecodeError):
            raise AnalysisContractError("analysis request invalid") from None
        if _canonical_json(request) != self.request_json:
            raise AnalysisContractError("analysis request not canonical")

    def canonical_request_bytes(self) -> bytes:
        return self.request_json.encode("utf-8")


@dataclass(frozen=True, slots=True)
class AnalysisUsage:
    unit_id: UUID
    provider: str
    model: str
    input_tokens: int | str
    output_tokens: int | str
    estimated_cost: str
    unavailable_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "unit_id": str(self.unit_id),
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class AcceptedAnalysis:
    unit: AnalysisUnit
    response_json: str
    response_sha256: str
    usage: AnalysisUsage

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle_id": str(self.unit.bundle_id),
            "unit_id": str(self.unit.unit_id),
            "kind": self.unit.kind,
            "scope_key": self.unit.scope_key,
            "input_sha256": self.unit.input_sha256,
            "evidence": [item.as_dict() for item in self.unit.evidence],
            "request": json.loads(self.unit.request_json),
            "response_sha256": self.response_sha256,
            "response": json.loads(self.response_json),
            "usage": self.usage.as_dict(),
        }


def _job_dict(job: NormalizedJob) -> dict[str, object]:
    return {
        "job_id": str(job.job_id),
        "source_id": str(job.source_id),
        "company_key": job.company_key,
        "public_job_key": job.public_job_key,
        "title": job.title,
        "location": job.location,
        "raw_location": job.raw_location,
        "duty_excerpt": job.duty_excerpt,
        "requirement_excerpt": job.requirement_excerpt,
        "source_url": job.source_url,
        "evidence_sha256": job.evidence_sha256,
        "observed_at": job.observed_at.isoformat(),
        "status": job.status,
    }


def _public_document_dict(
    document: PublicIntelligenceDocument,
) -> dict[str, object]:
    return document.as_dict()


def _prepare_unit(
    bundle_id: UUID,
    kind: str,
    scope_key: str,
    jobs: tuple[NormalizedJob, ...],
    aggregates: Mapping[str, object],
    public_documents: tuple[PublicIntelligenceDocument, ...] = (),
) -> AnalysisUnit:
    if not isinstance(bundle_id, UUID) or kind not in _KINDS:
        raise AnalysisContractError("analysis unit invalid")
    selected_scope = _required_text(scope_key, 256, "analysis scope")
    if not isinstance(jobs, tuple) or any(
        not isinstance(job, NormalizedJob) for job in jobs
    ):
        raise AnalysisContractError("analysis jobs invalid")
    if not isinstance(public_documents, tuple) or any(
        not isinstance(item, PublicIntelligenceDocument)
        for item in public_documents
    ):
        raise AnalysisContractError("analysis public documents invalid")
    request = {
        "schema_version": 2,
        "bundle_id": str(bundle_id),
        "kind": kind,
        "scope_key": selected_scope,
        "instructions": {
            "facts_require_evidence": True,
            "inferences_require_fact_ids": True,
            "alternatives_require_fact_and_inference_ids": True,
            "recommendations_require_fact_ids_and_target_tasks": True,
            "company_specific_signals": True,
            "orbbec_implications": True,
            "contrary_evidence_and_uncertainty": True,
            "insufficient_evidence": "unknown",
            "maximum_claim_count": 100,
        },
        "jobs": [
            _job_dict(job)
            for job in sorted(
                jobs, key=lambda item: (item.company_key, str(item.job_id))
            )
        ],
        "public_documents": [
            _public_document_dict(document)
            for document in sorted(
                public_documents,
                key=lambda item: (
                    item.company_key,
                    item.source_type,
                    item.source_url,
                    str(item.document_id),
                ),
            )
        ],
        "aggregates": json.loads(_canonical_json(aggregates)),
    }
    request_json = _canonical_json(request)
    input_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    unit_id = uuid5(
        NAMESPACE_URL,
        f"orbbec:hr-intelligence:{bundle_id}:{kind}:{selected_scope}:{input_sha256}",
    )
    job_evidence = tuple(
        EvidenceReference(
            job.job_id,
            job.evidence_sha256,
            job.source_url,
            job.observed_at,
        )
        for job in sorted(jobs, key=lambda item: (item.company_key, str(item.job_id)))
    )
    document_evidence = tuple(
        EvidenceReference(
            None,
            document.evidence_sha256,
            document.source_url,
            document.observed_at,
            "public_document",
            document.document_id,
            document.trust_tier,
        )
        for document in sorted(
            public_documents,
            key=lambda item: (
                item.company_key,
                item.source_type,
                item.source_url,
                str(item.document_id),
            ),
        )
    )
    return AnalysisUnit(
        bundle_id,
        unit_id,
        kind,
        selected_scope,
        input_sha256,
        job_evidence + document_evidence,
        request_json,
    )


def prepare_company_unit(
    bundle_id: UUID,
    company_key: str,
    jobs: tuple[NormalizedJob, ...],
    aggregates: Mapping[str, object],
    *,
    public_documents: tuple[PublicIntelligenceDocument, ...] = (),
) -> AnalysisUnit:
    if any(job.company_key != company_key for job in jobs):
        raise AnalysisContractError("analysis company scope invalid")
    if any(document.company_key != company_key for document in public_documents):
        raise AnalysisContractError("analysis company scope invalid")
    return _prepare_unit(
        bundle_id,
        "company",
        company_key,
        jobs,
        aggregates,
        public_documents,
    )


def prepare_units(
    bundle_id: UUID,
    jobs: tuple[NormalizedJob, ...],
    aggregates: Mapping[str, object],
    *,
    public_documents: tuple[PublicIntelligenceDocument, ...] = (),
    kinds: tuple[str, ...] = (
        "company",
        "track",
        "direction",
        "secondary-direction",
        "topic",
        "executive-summary",
        "task",
    ),
    company_keys: tuple[str, ...] | None = None,
) -> tuple[AnalysisUnit, ...]:
    if (
        not isinstance(kinds, tuple)
        or not kinds
        or len(set(kinds)) != len(kinds)
        or any(kind not in _KINDS for kind in kinds)
    ):
        raise AnalysisContractError("analysis kinds invalid")
    if company_keys is not None and (
        not isinstance(company_keys, tuple)
        or any(not isinstance(key, str) or not key.strip() for key in company_keys)
    ):
        raise AnalysisContractError("analysis company keys invalid")
    if not isinstance(public_documents, tuple) or any(
        not isinstance(item, PublicIntelligenceDocument)
        for item in public_documents
    ):
        raise AnalysisContractError("analysis public documents invalid")
    companies = tuple(
        sorted(
            set(company_keys)
            if company_keys is not None
            else {job.company_key for job in jobs}
        )
    )
    units: list[AnalysisUnit] = []
    for kind in (
        "company",
        "track",
        "direction",
        "secondary-direction",
        "comparison",
        "topic",
        "executive-summary",
        "task",
    ):
        if kind not in kinds:
            continue
        if kind == "company":
            units.extend(
                prepare_company_unit(
                    bundle_id,
                    company,
                    tuple(job for job in jobs if job.company_key == company),
                    aggregates,
                    public_documents=tuple(
                        document
                        for document in public_documents
                        if document.company_key == company
                    ),
                )
                for company in companies
            )
            continue
        if kind == "track":
            tracks = tuple(sorted({recruitment_track(job) for job in jobs}))
            units.extend(
                _prepare_unit(
                    bundle_id,
                    kind,
                    track,
                    tuple(job for job in jobs if recruitment_track(job) == track),
                    aggregates,
                    tuple(
                        document
                        for document in public_documents
                        if document.company_key
                        in {
                            job.company_key
                            for job in jobs
                            if recruitment_track(job) == track
                        }
                    ),
                )
                for track in tracks
            )
            continue
        if kind == "direction":
            directions = tuple(
                sorted(
                    {
                        direction
                        for job in jobs
                        for direction in technical_directions(job)
                    }
                )
            )
            units.extend(
                _prepare_unit(
                    bundle_id,
                    kind,
                    direction,
                    tuple(
                        job for job in jobs if direction in technical_directions(job)
                    ),
                    aggregates,
                    tuple(
                        document
                        for document in public_documents
                        if document.company_key
                        in {
                            job.company_key
                            for job in jobs
                            if direction in technical_directions(job)
                        }
                    ),
                )
                for direction in directions
            )
            continue
        if kind == "secondary-direction":
            directions = tuple(
                dict.fromkeys(
                    direction
                    for job in jobs
                    for direction in secondary_directions(job)
                )
            )
            units.extend(
                _prepare_unit(
                    bundle_id,
                    kind,
                    direction,
                    tuple(
                        job
                        for job in jobs
                        if direction in secondary_directions(job)
                    ),
                    aggregates,
                    tuple(
                        document
                        for document in public_documents
                        if document.company_key
                        in {
                            job.company_key
                            for job in jobs
                            if direction in secondary_directions(job)
                        }
                    ),
                )
                for direction in directions
            )
            continue
        if kind == "topic":
            units.extend(
                _prepare_unit(
                    bundle_id, kind, scope, jobs, aggregates, public_documents
                )
                for scope in _TOPIC_SCOPES
            )
            continue
        if kind == "task":
            units.extend(
                _prepare_unit(
                    bundle_id, kind, scope, jobs, aggregates, public_documents
                )
                for scope in _TASK_SCOPES
            )
            continue
        units.append(
            _prepare_unit(
                bundle_id,
                kind,
                "all-companies",
                jobs,
                aggregates,
                public_documents,
            )
        )
    return tuple(units)


def _validate_response(
    unit: AnalysisUnit,
    response: Mapping[str, object],
) -> str:
    try:
        request_version = json.loads(unit.request_json).get("schema_version")
    except (AttributeError, json.JSONDecodeError):
        raise AnalysisContractError("analysis request invalid") from None
    response_keys = _RESPONSE_V1_KEYS if request_version == 1 else _RESPONSE_V2_KEYS
    if not isinstance(response, Mapping) or set(response) != response_keys:
        raise AnalysisContractError("analysis response schema invalid")
    if request_version not in {1, 2} or (
        request_version == 2 and response.get("schema_version") != 2
    ):
        raise AnalysisContractError("analysis response schema invalid")
    facts = response.get("facts")
    inferences = response.get("inferences")
    unknowns = response.get("unknowns")
    alternatives = response.get("alternatives")
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes)):
        raise AnalysisContractError("analysis facts invalid")
    if not isinstance(inferences, Sequence) or isinstance(inferences, (str, bytes)):
        raise AnalysisContractError("analysis inferences invalid")
    if not isinstance(unknowns, Sequence) or isinstance(unknowns, (str, bytes)):
        raise AnalysisContractError("analysis unknowns invalid")
    if not isinstance(alternatives, Sequence) or isinstance(alternatives, (str, bytes)):
        raise AnalysisContractError("analysis alternatives invalid")
    available = {
        (item.sha256, item.source_url, item.observed_at.isoformat())
        for item in unit.evidence
    }
    fact_ids: set[str] = set()
    for fact in facts:
        if not isinstance(fact, Mapping) or set(fact) != {
            "fact_id",
            "text",
            "evidence_sha256",
            "source_url",
            "observed_at",
        }:
            raise AnalysisContractError("analysis facts invalid")
        fact_id = _required_text(fact.get("fact_id"), 128, "analysis fact")
        _required_text(fact.get("text"), 4096, "analysis fact")
        if fact_id in fact_ids:
            raise AnalysisContractError("analysis fact identity invalid")
        fact_ids.add(fact_id)
        evidence_key = (
            fact.get("evidence_sha256"),
            fact.get("source_url"),
            fact.get("observed_at"),
        )
        if evidence_key not in available:
            raise AnalysisContractError("analysis evidence invalid")
    inference_ids: set[str] = set()
    inference_keys = (
        {"text", "basis_fact_ids"}
        if request_version == 1
        else {"inference_id", "claim_type", "text", "basis_fact_ids"}
    )
    for ordinal, inference in enumerate(inferences, start=1):
        if not isinstance(inference, Mapping) or set(inference) != inference_keys:
            raise AnalysisContractError("analysis inference invalid")
        inference_id = (
            f"legacy-inference-{ordinal}"
            if request_version == 1
            else _required_text(
                inference.get("inference_id"), 128, "analysis inference identity"
            )
        )
        if inference_id in inference_ids:
            raise AnalysisContractError("analysis inference identity invalid")
        inference_ids.add(inference_id)
        if request_version == 2 and inference.get("claim_type") not in _CLAIM_TYPES:
            raise AnalysisContractError("analysis inference type invalid")
        _required_text(inference.get("text"), 4096, "analysis inference")
        basis = inference.get("basis_fact_ids")
        if (
            not isinstance(basis, list)
            or not basis
            or any(not isinstance(item, str) or item not in fact_ids for item in basis)
        ):
            raise AnalysisContractError("analysis inference basis invalid")
    for value in unknowns:
        _required_text(value, 4096, "analysis limitation")
    if request_version == 1:
        for value in alternatives:
            _required_text(value, 4096, "analysis limitation")
    else:
        alternative_ids: set[str] = set()
        for alternative in alternatives:
            if not isinstance(alternative, Mapping) or set(alternative) != {
                "alternative_id",
                "text",
                "basis_fact_ids",
                "challenged_inference_ids",
            }:
                raise AnalysisContractError("analysis alternative invalid")
            alternative_id = _required_text(
                alternative.get("alternative_id"),
                128,
                "analysis alternative identity",
            )
            if alternative_id in alternative_ids:
                raise AnalysisContractError("analysis alternative identity invalid")
            alternative_ids.add(alternative_id)
            _required_text(alternative.get("text"), 4096, "analysis alternative")
            basis = alternative.get("basis_fact_ids")
            challenged = alternative.get("challenged_inference_ids")
            if (
                not isinstance(basis, list)
                or not basis
                or any(item not in fact_ids for item in basis)
                or not isinstance(challenged, list)
                or not challenged
                or any(item not in inference_ids for item in challenged)
            ):
                raise AnalysisContractError("analysis alternative basis invalid")
        recommendations = response.get("recommendations")
        if not isinstance(recommendations, Sequence) or isinstance(
            recommendations, (str, bytes)
        ):
            raise AnalysisContractError("analysis recommendations invalid")
        recommendation_ids: set[str] = set()
        for recommendation in recommendations:
            if not isinstance(recommendation, Mapping) or set(recommendation) != {
                "recommendation_id",
                "text",
                "basis_fact_ids",
                "target_tasks",
            }:
                raise AnalysisContractError("analysis recommendation invalid")
            recommendation_id = _required_text(
                recommendation.get("recommendation_id"),
                128,
                "analysis recommendation identity",
            )
            if recommendation_id in recommendation_ids:
                raise AnalysisContractError("analysis recommendation identity invalid")
            recommendation_ids.add(recommendation_id)
            _required_text(
                recommendation.get("text"), 4096, "analysis recommendation"
            )
            basis = recommendation.get("basis_fact_ids")
            tasks = recommendation.get("target_tasks")
            if (
                not isinstance(basis, list)
                or not basis
                or any(item not in fact_ids for item in basis)
                or not isinstance(tasks, list)
                or not tasks
                or any(item not in _TARGET_TASKS for item in tasks)
            ):
                raise AnalysisContractError("analysis recommendation basis invalid")
    _required_text(response.get("summary"), 32768, "analysis summary")
    if response.get("confidence") not in _CONFIDENCE:
        raise AnalysisContractError("analysis confidence invalid")
    return _canonical_json(response)


def _validate_usage(unit: AnalysisUnit, raw: Mapping[str, object]) -> AnalysisUsage:
    if not isinstance(raw, Mapping) or set(raw) != _USAGE_KEYS:
        raise AnalysisContractError("analysis usage schema invalid")
    try:
        unit_id = UUID(str(raw.get("unit_id")))
    except (TypeError, ValueError):
        raise AnalysisContractError("analysis usage identity invalid") from None
    if unit_id != unit.unit_id:
        raise AnalysisContractError("analysis usage identity invalid")
    provider = _required_text(raw.get("provider"), 128, "analysis provider")
    model = _required_text(raw.get("model"), 256, "analysis model")
    tokens = (raw.get("input_tokens"), raw.get("output_tokens"))
    estimated = raw.get("estimated_cost")
    reason = raw.get("unavailable_reason")
    if tokens == ("unavailable", "unavailable") and estimated == "unavailable":
        unavailable_reason = _required_text(reason, 1000, "usage unavailable reason")
    else:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in tokens
        ):
            raise AnalysisContractError("analysis token usage invalid")
        if not isinstance(estimated, str):
            raise AnalysisContractError("analysis cost invalid")
        try:
            cost = Decimal(estimated)
        except InvalidOperation:
            raise AnalysisContractError("analysis cost invalid") from None
        if not cost.is_finite() or cost < 0:
            raise AnalysisContractError("analysis cost invalid")
        if reason is not None:
            raise AnalysisContractError("analysis usage reason invalid")
        unavailable_reason = None
    return AnalysisUsage(
        unit_id,
        provider,
        model,
        tokens[0],
        tokens[1],
        str(estimated),
        unavailable_reason,
    )


def accept_unit_response(
    unit: AnalysisUnit,
    response: Mapping[str, object],
    usage: Mapping[str, object],
) -> AcceptedAnalysis:
    if not isinstance(unit, AnalysisUnit):
        raise TypeError("analysis unit required")
    response_json = _validate_response(unit, response)
    validated_usage = _validate_usage(unit, usage)
    return AcceptedAnalysis(
        unit,
        response_json,
        hashlib.sha256(response_json.encode("utf-8")).hexdigest(),
        validated_usage,
    )


def _accepted_path(root: Path, unit_id: UUID) -> Path:
    return root.resolve() / "analysis" / "accepted" / f"{unit_id}.json"


def save_accepted(root: str | Path, accepted: AcceptedAnalysis) -> Path:
    if not isinstance(accepted, AcceptedAnalysis):
        raise TypeError("accepted analysis required")
    path = _accepted_path(Path(root), accepted.unit.unit_id)
    body = (_canonical_json(accepted.as_dict()) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != body:
            raise AnalysisContractError("accepted analysis is immutable")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{uuid4().hex}.part"
    try:
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    return path


def analysis_cache_hit(root: str | Path, unit: AnalysisUnit) -> bool:
    if not isinstance(unit, AnalysisUnit):
        raise TypeError("analysis unit required")
    path = _accepted_path(Path(root), unit.unit_id)
    if not path.is_file():
        return False
    try:
        saved = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        saved.get("bundle_id") == str(unit.bundle_id)
        and saved.get("unit_id") == str(unit.unit_id)
        and saved.get("input_sha256") == unit.input_sha256
    )


def load_accepted(root: str | Path, unit: AnalysisUnit) -> AcceptedAnalysis:
    if not isinstance(unit, AnalysisUnit):
        raise TypeError("analysis unit required")
    path = _accepted_path(Path(root), unit.unit_id)
    try:
        saved = json.loads(path.read_text("utf-8"))
        response = saved["response"]
        usage = saved["usage"]
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raise AnalysisContractError("accepted analysis unavailable") from None
    current_evidence = [item.as_dict() for item in unit.evidence]
    legacy_evidence = [
        {
            "job_id": str(item.job_id),
            "sha256": item.sha256,
            "source_url": item.source_url,
            "observed_at": item.observed_at.isoformat(),
        }
        for item in unit.evidence
        if item.evidence_kind == "job" and item.job_id is not None
    ]
    evidence_matches = saved.get("evidence") == current_evidence or (
        json.loads(unit.request_json).get("schema_version") == 1
        and len(legacy_evidence) == len(unit.evidence)
        and saved.get("evidence") == legacy_evidence
    )
    if (
        saved.get("bundle_id") != str(unit.bundle_id)
        or saved.get("unit_id") != str(unit.unit_id)
        or saved.get("input_sha256") != unit.input_sha256
        or saved.get("request") != json.loads(unit.request_json)
        or not evidence_matches
    ):
        raise AnalysisContractError("accepted analysis input mismatch")
    accepted = accept_unit_response(unit, response, usage)
    if saved.get("response_sha256") != accepted.response_sha256:
        raise AnalysisContractError("accepted analysis checksum mismatch")
    return accepted


__all__ = [
    "AcceptedAnalysis",
    "AnalysisContractError",
    "AnalysisUnit",
    "AnalysisUsage",
    "EvidenceReference",
    "accept_unit_response",
    "analysis_cache_hit",
    "load_accepted",
    "prepare_company_unit",
    "prepare_units",
    "save_accepted",
]
