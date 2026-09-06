from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from .analysis_quality import validate_analysis_set
from .analysis_units import (
    AnalysisContractError,
    AnalysisUnit,
    EvidenceReference,
    accept_unit_response,
    analysis_cache_hit,
    load_accepted,
    prepare_units,
    save_accepted,
)
from .bundle import BundleInputs, build_bundle, verify_bundle
from .collectors import CollectionError, PublicSourceCollector, SourceTarget
from .dimensions import compile_dimensions
from .evidence import EvidenceArchive
from .models import NormalizedJob
from .normalize import normalize_jobs
from .paths import local_data_root
from .public_documents import (
    PublicDocumentCollectionError,
    PublicDocumentTarget,
    PublicIntelligenceDocument,
    collect_public_document,
)
from .retrieval_eval import (
    LocalBundleRetrievalProbe,
    evaluate_retrieval,
    load_cases,
)

_COVERAGE_STATES = frozenset(
    {"succeeded", "empty_confirmed", "partial", "failed", "not_observed"}
)


def _company_source_id(company_key: str) -> UUID:
    selected = company_key.strip() if isinstance(company_key, str) else ""
    if not selected:
        raise ValueError("company key invalid")
    return uuid5(NAMESPACE_URL, f"orbbec:hr-intelligence:source:{selected}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_json(path: Path, value: object) -> None:
    body = (_canonical_json(value) + "\n").encode("utf-8")
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


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"invalid JSON: {path.name}") from None


def _absolute_file(raw: str, label: str) -> Path:
    selected = Path(raw)
    if not selected.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    selected = selected.resolve()
    if not selected.is_file():
        raise ValueError(f"{label} file unavailable")
    return selected


def _bundle_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except (TypeError, ValueError):
        raise ValueError("bundle id invalid") from None


def _work(bundle_id: UUID) -> Path:
    return local_data_root() / "work" / str(bundle_id)


def _built(bundle_id: UUID) -> Path:
    return local_data_root() / "bundles" / str(bundle_id)


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


def _job_from_dict(value: Mapping[str, object]) -> NormalizedJob:
    try:
        return NormalizedJob(
            job_id=UUID(str(value["job_id"])),
            source_id=UUID(str(value["source_id"])),
            company_key=str(value["company_key"]),
            public_job_key=str(value["public_job_key"]),
            title=str(value["title"]),
            location=str(value["location"]),
            raw_location=str(value.get("raw_location", value["location"])),
            duty_excerpt=str(value["duty_excerpt"]),
            requirement_excerpt=str(value["requirement_excerpt"]),
            source_url=str(value["source_url"]),
            evidence_sha256=str(value["evidence_sha256"]),
            observed_at=datetime.fromisoformat(str(value["observed_at"])),
            status=str(value["status"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("normalized job record invalid") from None


def _write_jobs(path: Path, jobs: tuple[NormalizedJob, ...]) -> None:
    body = b"".join(
        (_canonical_json(_job_dict(job)) + "\n").encode("utf-8")
        for job in sorted(
            jobs,
            key=lambda item: (item.company_key, item.public_job_key, str(item.job_id)),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{uuid4().hex}.part"
    try:
        staging.write_bytes(body)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _read_jobs(path: Path) -> tuple[NormalizedJob, ...]:
    try:
        lines = path.read_text("utf-8").splitlines()
        return tuple(_job_from_dict(json.loads(line)) for line in lines if line.strip())
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("normalized jobs invalid") from None


def _public_document_from_dict(
    value: Mapping[str, object],
) -> PublicIntelligenceDocument:
    try:
        return PublicIntelligenceDocument(
            document_id=UUID(str(value["document_id"])),
            company_key=str(value["company_key"]),
            source_type=str(value["source_type"]),  # type: ignore[arg-type]
            source_url=str(value["source_url"]),
            title=str(value["title"]),
            text_excerpt=str(value["text_excerpt"]),
            evidence_sha256=str(value["evidence_sha256"]),
            text_sha256=str(value["text_sha256"]),
            observed_at=datetime.fromisoformat(str(value["observed_at"])),
            trust_tier=str(value["trust_tier"]),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("public document record invalid") from None


def _write_public_documents(
    path: Path, documents: tuple[PublicIntelligenceDocument, ...]
) -> None:
    body = b"".join(
        (_canonical_json(document.as_dict()) + "\n").encode()
        for document in sorted(documents, key=lambda item: str(item.document_id))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.{uuid4().hex}.part"
    try:
        staging.write_bytes(body)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def _read_public_documents(path: Path) -> tuple[PublicIntelligenceDocument, ...]:
    try:
        lines = path.read_text("utf-8").splitlines()
        return tuple(
            _public_document_from_dict(json.loads(line))
            for line in lines
            if line.strip()
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("public documents invalid") from None


def _empty_dimensions() -> dict[str, object]:
    return {
        "schema_version": 3,
        "scope": {
            "snapshot_count": 0,
            "unique_job_count": 0,
            "duplicate_snapshot_count": 0,
            "source_count": 0,
            "observed_from": None,
            "observed_to": None,
        },
        "tracks": {"social": 0, "campus": 0, "intern": 0, "unknown": 0},
        "directions": {},
        "secondary_directions": {},
        "job_families": {},
        "seniority": {},
        "education": {},
        "locations": {},
        "skills": [],
        "company_matrix": {},
        "company_comparison": {},
        "evidence_samples": {
            "directions": {},
            "secondary_directions": {},
            "skills": {},
        },
        "data_quality": {"invalid_locations": {}},
        "trend": {"state": "baseline_only", "message": "基线版本：尚不能判断月度变化"},
        "interpretation_limits": [
            "公开岗位数不等于HC、预算、产量或实际研发投入",
            "未覆盖或采集失败不代表企业没有招聘活动",
        ],
    }


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} invalid")
    return value


def _catalog_companies(catalog: object) -> tuple[Mapping[str, object], ...]:
    selected = _require_mapping(catalog, "source catalog").get("companies")
    if not isinstance(selected, list):
        raise TypeError("source catalog invalid")
    companies = tuple(_require_mapping(item, "source company") for item in selected)
    keys = [str(item.get("company_key", "")) for item in companies]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("source catalog company key invalid")
    return companies


def _initialize(args: argparse.Namespace) -> int:
    bundle_id = _bundle_id(args.bundle_id)
    catalog_path = _absolute_file(args.catalog, "catalog")
    catalog = _read_json(catalog_path)
    _catalog_companies(catalog)
    work = _work(bundle_id)
    if work.exists():
        current = _read_json(work / "source-catalog.json")
        if _canonical_json(current) != _canonical_json(catalog):
            raise ValueError("existing local bundle catalog differs")
        print(bundle_id)
        return 0
    work.mkdir(parents=True, mode=0o750)
    _write_json(work / "source-catalog.json", catalog)
    _write_json(
        work / "state.json",
        {
            "schema_version": 1,
            "bundle_id": str(bundle_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "initialized",
        },
    )
    print(bundle_id)
    return 0


def _resumable_collection(
    work: Path,
) -> tuple[
    dict[str, Mapping[str, object]],
    dict[UUID, NormalizedJob],
    dict[UUID, PublicIntelligenceDocument],
]:
    coverage_path = work / "source-coverage.json"
    jobs_path = work / "normalized-jobs.jsonl"
    if not coverage_path.is_file():
        return {}, {}, {}
    document = _require_mapping(_read_json(coverage_path), "source coverage")
    raw_companies = document.get("companies")
    if not isinstance(raw_companies, list):
        raise TypeError("source coverage invalid")
    companies = {
        str(item.get("company_key")): item
        for raw in raw_companies
        for item in (_require_mapping(raw, "source coverage"),)
        if str(item.get("company_key", ""))
    }
    jobs = _read_jobs(jobs_path) if jobs_path.is_file() else ()
    if not jobs_path.is_file() and any(
        isinstance(item.get("job_count"), int) and item["job_count"] > 0
        for item in companies.values()
    ):
        raise ValueError("resumable normalized jobs unavailable")
    document_path = work / "public-documents.jsonl"
    documents = (
        _read_public_documents(document_path) if document_path.is_file() else ()
    )
    return (
        companies,
        {job.job_id: job for job in jobs},
        {document.document_id: document for document in documents},
    )


async def _collect_async(bundle_id: UUID, *, resume: bool = False) -> int:
    work = _work(bundle_id)
    catalog = _read_json(work / "source-catalog.json")
    companies = _catalog_companies(catalog)
    archive = EvidenceArchive(work / "evidence")
    previous_coverage, previous_jobs, previous_documents = (
        _resumable_collection(work) if resume else ({}, {}, {})
    )
    coverage: list[dict[str, object]] = []
    all_jobs: dict[UUID, NormalizedJob] = dict(previous_jobs)
    all_documents: dict[UUID, PublicIntelligenceDocument] = dict(
        previous_documents
    )
    async with httpx.AsyncClient() as client:
        collector = PublicSourceCollector(client, archive)
        for company in companies:
            company_key = str(company["company_key"])
            company_name = str(company.get("canonical_name", company_key))
            raw_urls = company.get("approved_urls")
            if not isinstance(raw_urls, list) or not raw_urls:
                coverage.append(
                    {
                        "company_key": company_key,
                        "state": "not_observed",
                        "observed_at": None,
                        "job_count": None,
                        "channels": [],
                        "limitations": ["受控来源目录中没有可采集 URL"],
                    }
                )
                continue
            urls = tuple(str(value) for value in raw_urls)
            previous_company = previous_coverage.get(company_key)
            previous_channels = (
                previous_company.get("channels", [])
                if isinstance(previous_company, Mapping)
                else []
            )
            if not isinstance(previous_channels, list):
                raise TypeError("resumable source channels invalid")
            previous_document_channels = (
                previous_company.get("document_channels", [])
                if isinstance(previous_company, Mapping)
                else []
            )
            if not isinstance(previous_document_channels, list):
                raise TypeError("resumable document channels invalid")
            succeeded_channels: dict[tuple[int, str], dict[str, object]] = {}
            for raw_channel in previous_channels:
                channel = _require_mapping(raw_channel, "source channel")
                ordinal = channel.get("ordinal")
                source_url = channel.get("source_url")
                if (
                    channel.get("state") == "succeeded"
                    and isinstance(ordinal, int)
                    and isinstance(source_url, str)
                ):
                    succeeded_channels[(ordinal, source_url)] = dict(channel)
            channels: list[dict[str, object]] = []
            successful = 0
            company_jobs: dict[UUID, NormalizedJob] = {
                job_id: job
                for job_id, job in previous_jobs.items()
                if job.company_key == company_key
            }
            for ordinal, source_url in enumerate(urls):
                preserved = succeeded_channels.get((ordinal, source_url))
                if preserved is not None:
                    successful += 1
                    channels.append(preserved)
                    continue
                source_id = _company_source_id(company_key)
                target = SourceTarget(source_id, company_name, source_url, urls)
                try:
                    result = await collector.collect(target)
                    successful += 1
                    normalized = normalize_jobs(result, company_key=company_key)
                    company_jobs.update({job.job_id: job for job in normalized})
                    channels.append(
                        {
                            "ordinal": ordinal,
                            "source_url": source_url,
                            "state": "succeeded",
                            "observed_at": result.observed_at.isoformat(),
                            "job_count": len(normalized),
                            "evidence_sha256": result.evidence.sha256,
                            "error_code": None,
                        }
                    )
                except (CollectionError, httpx.HTTPError, ValueError) as error:
                    code = (
                        error.code
                        if isinstance(error, CollectionError)
                        else "source_unavailable"
                    )
                    channels.append(
                        {
                            "ordinal": ordinal,
                            "source_url": source_url,
                            "state": "failed",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "job_count": None,
                            "evidence_sha256": (
                                error.evidence.sha256
                                if isinstance(error, CollectionError) and error.evidence
                                else None
                            ),
                            "error_code": code,
                        }
                    )
            document_channels: list[dict[str, object]] = []
            successful_document_channels = {
                (
                    channel.get("ordinal"),
                    channel.get("source_type"),
                    channel.get("source_url"),
                ): dict(channel)
                for raw_channel in previous_document_channels
                for channel in (_require_mapping(raw_channel, "document channel"),)
                if channel.get("state") == "succeeded"
            }
            raw_document_targets = company.get("public_documents", [])
            if not isinstance(raw_document_targets, list):
                raise TypeError("public document targets invalid")
            for ordinal, raw_document_target in enumerate(raw_document_targets):
                configured = _require_mapping(
                    raw_document_target, "public document target"
                )
                target = PublicDocumentTarget(
                    company_key=company_key,
                    source_type=str(configured.get("source_type")),  # type: ignore[arg-type]
                    source_url=str(configured.get("source_url")),
                    trust_tier=str(configured.get("trust_tier")),  # type: ignore[arg-type]
                )
                key = (ordinal, target.source_type, target.source_url)
                preserved = successful_document_channels.get(key)
                preserved_id = (
                    str(preserved.get("document_id"))
                    if preserved is not None
                    else ""
                )
                if preserved is not None and any(
                    str(document_id) == preserved_id
                    for document_id in previous_documents
                ):
                    document_channels.append(preserved)
                    continue
                try:
                    document = await collect_public_document(
                        target, archive=archive, client=client
                    )
                    all_documents[document.document_id] = document
                    document_channels.append(
                        {
                            "ordinal": ordinal,
                            "source_type": target.source_type,
                            "source_url": target.source_url,
                            "trust_tier": target.trust_tier,
                            "state": "succeeded",
                            "observed_at": document.observed_at.isoformat(),
                            "document_id": str(document.document_id),
                            "evidence_sha256": document.evidence_sha256,
                            "error_code": None,
                        }
                    )
                except (
                    PublicDocumentCollectionError,
                    httpx.HTTPError,
                    ValueError,
                ) as error:
                    document_channels.append(
                        {
                            "ordinal": ordinal,
                            "source_type": target.source_type,
                            "source_url": target.source_url,
                            "trust_tier": target.trust_tier,
                            "state": "failed",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "document_id": None,
                            "evidence_sha256": (
                                error.evidence.sha256
                                if isinstance(error, PublicDocumentCollectionError)
                                and error.evidence is not None
                                else None
                            ),
                            "error_code": (
                                error.code
                                if isinstance(error, PublicDocumentCollectionError)
                                else "source_unavailable"
                            ),
                        }
                    )
            all_jobs.update(company_jobs)
            if successful == len(urls):
                state = "succeeded" if company_jobs else "empty_confirmed"
            elif successful:
                state = "partial"
            else:
                state = "failed"
            coverage.append(
                {
                    "company_key": company_key,
                    "state": state,
                    "observed_at": max(
                        str(channel["observed_at"])
                        for channel in channels
                        if channel["observed_at"] is not None
                    ),
                    "job_count": len(company_jobs) if successful else None,
                    "channels": channels,
                    "document_channels": document_channels,
                    "limitations": (
                        []
                        if state in {"succeeded", "empty_confirmed"}
                        else ["一个或多个公开招聘渠道未能完成采集"]
                    ),
                    "document_limitations": (
                        []
                        if all(
                            item["state"] == "succeeded"
                            for item in document_channels
                        )
                        else ["一个或多个公司公开材料渠道未能完成采集"]
                    ),
                }
            )
    jobs = tuple(all_jobs.values())
    _write_jobs(work / "normalized-jobs.jsonl", jobs)
    _write_public_documents(
        work / "public-documents.jsonl", tuple(all_documents.values())
    )
    _write_json(
        work / "source-coverage.json",
        {
            "schema_version": 2 if catalog.get("schema_version") == 2 else 1,
            "companies": coverage,
        },
    )
    _write_json(
        work / "aggregates.json",
        compile_dimensions(jobs) if jobs else _empty_dimensions(),
    )
    state = _require_mapping(_read_json(work / "state.json"), "bundle state")
    _write_json(work / "state.json", dict(state) | {"state": "collected"})
    print(
        _canonical_json(
            {"bundle_id": str(bundle_id), "jobs": len(jobs), "coverage": coverage}
        )
    )
    return 0


def _validate_work(bundle_id: UUID, company_count: int, provenance: bool) -> int:
    work = _work(bundle_id)
    catalog = _read_json(work / "source-catalog.json")
    companies = _catalog_companies(catalog)
    if len(companies) != company_count:
        raise ValueError("source catalog company count mismatch")
    coverage_document = _require_mapping(
        _read_json(work / "source-coverage.json"),
        "source coverage",
    )
    coverage = coverage_document.get("companies")
    if not isinstance(coverage, list) or len(coverage) != company_count:
        raise ValueError("source coverage incomplete")
    states = {
        str(_require_mapping(item, "source coverage").get("state")) for item in coverage
    }
    if not states.issubset(_COVERAGE_STATES):
        raise ValueError("source coverage state invalid")
    jobs = _read_jobs(work / "normalized-jobs.jsonl")
    document_path = work / "public-documents.jsonl"
    documents = (
        _read_public_documents(document_path) if document_path.is_file() else ()
    )
    if provenance:

        def require_evidence(sha256: object, error: str) -> None:
            selected = str(sha256) if isinstance(sha256, str) else ""
            evidence = work / "evidence" / "sha256" / selected[:2] / selected
            if (
                len(selected) != 64
                or not evidence.is_file()
                or hashlib.sha256(evidence.read_bytes()).hexdigest() != selected
            ):
                raise ValueError(error)

        for raw_company in coverage:
            company = _require_mapping(raw_company, "source coverage")
            raw_channels = company.get("channels", [])
            if not isinstance(raw_channels, list):
                raise TypeError("source coverage channels invalid")
            for raw_channel in raw_channels:
                channel = _require_mapping(raw_channel, "source coverage channel")
                sha256 = channel.get("evidence_sha256")
                if sha256 is not None:
                    require_evidence(sha256, "source coverage evidence invalid")
            raw_document_channels = company.get("document_channels", [])
            if not isinstance(raw_document_channels, list):
                raise TypeError("source document channels invalid")
            for raw_channel in raw_document_channels:
                channel = _require_mapping(
                    raw_channel, "source document coverage channel"
                )
                sha256 = channel.get("evidence_sha256")
                if sha256 is not None:
                    require_evidence(
                        sha256, "source document evidence invalid"
                    )
        for job in jobs:
            require_evidence(job.evidence_sha256, "normalized job evidence invalid")
        for document in documents:
            require_evidence(
                document.evidence_sha256, "public document evidence invalid"
            )
    print(
        _canonical_json(
            {
                "bundle_id": str(bundle_id),
                "companies": len(companies),
                "jobs": len(jobs),
            }
        )
    )
    return 0


def _unit_document(unit: AnalysisUnit) -> dict[str, object]:
    return {
        "bundle_id": str(unit.bundle_id),
        "unit_id": str(unit.unit_id),
        "kind": unit.kind,
        "scope_key": unit.scope_key,
        "input_sha256": unit.input_sha256,
        "evidence": [item.as_dict() for item in unit.evidence],
        "request": json.loads(unit.request_json),
    }


def _unit_from_document(value: object) -> AnalysisUnit:
    document = _require_mapping(value, "analysis request")
    try:
        evidence = tuple(
            EvidenceReference(
                (
                    UUID(str(item["job_id"]))
                    if item.get("evidence_kind", "job") == "job"
                    else None
                ),
                str(item["sha256"]),
                str(item["source_url"]),
                datetime.fromisoformat(str(item["observed_at"])),
                str(item.get("evidence_kind", "job")),
                UUID(str(item.get("evidence_id", item.get("job_id")))),
                (
                    str(item["trust_tier"])
                    if item.get("trust_tier") is not None
                    else None
                ),
            )
            for item in (
                _require_mapping(raw, "analysis evidence")
                for raw in document["evidence"]
            )
        )
        return AnalysisUnit(
            UUID(str(document["bundle_id"])),
            UUID(str(document["unit_id"])),
            str(document["kind"]),
            str(document["scope_key"]),
            str(document["input_sha256"]),
            evidence,
            _canonical_json(document["request"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AnalysisContractError("analysis request invalid") from None


def _request_files(work: Path) -> tuple[Path, ...]:
    root = work / "analysis" / "requests"
    return tuple(sorted(root.glob("*.json"))) if root.is_dir() else ()


def _prepare_analysis(args: argparse.Namespace) -> int:
    bundle_id = _bundle_id(args.bundle_id)
    work = _work(bundle_id)
    jobs = _read_jobs(work / "normalized-jobs.jsonl")
    document_path = work / "public-documents.jsonl"
    public_documents = (
        _read_public_documents(document_path) if document_path.is_file() else ()
    )
    aggregates = _require_mapping(_read_json(work / "aggregates.json"), "aggregates")
    catalog = _read_json(work / "source-catalog.json")
    company_keys = tuple(
        str(company["company_key"]) for company in _catalog_companies(catalog)
    )
    kinds = tuple(value.strip() for value in args.units.split(",") if value.strip())
    units = prepare_units(
        bundle_id,
        jobs,
        aggregates,
        public_documents=public_documents,
        kinds=kinds,
        company_keys=company_keys,
    )
    request_root = work / "analysis" / "requests"
    for unit in units:
        _write_json(request_root / f"{unit.unit_id}.json", _unit_document(unit))
    print(_canonical_json({"bundle_id": str(bundle_id), "unit_count": len(units)}))
    return 0


def _analysis_status(args: argparse.Namespace) -> int:
    bundle_id = _bundle_id(args.bundle_id)
    work = _work(bundle_id)
    units = tuple(
        _unit_from_document(_read_json(path)) for path in _request_files(work)
    )
    completed = sum(analysis_cache_hit(work, unit) for unit in units)
    status = {
        "bundle_id": str(bundle_id),
        "total": len(units),
        "completed": completed,
        "pending": len(units) - completed,
    }
    print(_canonical_json(status))
    if args.require_complete and completed != len(units):
        raise AnalysisContractError("analysis units incomplete")
    return 0


def _accept_one(
    work: Path, unit: AnalysisUnit, response_path: Path, usage_path: Path
) -> None:
    response = _require_mapping(_read_json(response_path), "analysis response")
    usage = _require_mapping(_read_json(usage_path), "analysis usage")
    save_accepted(work, accept_unit_response(unit, response, usage))


def _accept_analysis(args: argparse.Namespace) -> int:
    bundle_id = _bundle_id(args.bundle_id)
    work = _work(bundle_id)
    units = {
        _unit_from_document(_read_json(path)).unit_id: _unit_from_document(
            _read_json(path)
        )
        for path in _request_files(work)
    }
    if args.all_ready:
        accepted = 0
        for unit_id, unit in units.items():
            response = work / "analysis" / "responses" / f"{unit_id}.json"
            usage = work / "analysis" / "usage" / f"{unit_id}.json"
            if response.is_file() and usage.is_file():
                _accept_one(work, unit, response, usage)
                accepted += 1
        print(_canonical_json({"bundle_id": str(bundle_id), "accepted": accepted}))
        return 0
    unit_id = UUID(args.unit)
    if unit_id not in units:
        raise AnalysisContractError("analysis unit unavailable")
    _accept_one(
        work,
        units[unit_id],
        _absolute_file(args.response, "response"),
        _absolute_file(args.usage, "usage"),
    )
    print(unit_id)
    return 0


def _build(args: argparse.Namespace) -> int:
    bundle_id = _bundle_id(args.bundle_id)
    work = _work(bundle_id)
    units = tuple(
        _unit_from_document(_read_json(path)) for path in _request_files(work)
    )
    if any(not analysis_cache_hit(work, unit) for unit in units):
        raise AnalysisContractError("analysis units incomplete")
    analyses = tuple(load_accepted(work, unit) for unit in units)
    validate_analysis_set(analyses)
    state = _require_mapping(_read_json(work / "state.json"), "bundle state")
    generated_at = datetime.fromisoformat(str(state["created_at"]))
    inputs = BundleInputs(
        bundle_id=bundle_id,
        generated_at=generated_at,
        source_catalog=_require_mapping(
            _read_json(work / "source-catalog.json"), "source catalog"
        ),
        source_coverage=_require_mapping(
            _read_json(work / "source-coverage.json"), "source coverage"
        ),
        jobs=_read_jobs(work / "normalized-jobs.jsonl"),
        aggregates=_require_mapping(_read_json(work / "aggregates.json"), "aggregates"),
        analyses=analyses,
        evidence_root=(work / "evidence").resolve(),
    )
    path = build_bundle(inputs, root=local_data_root() / "bundles")
    print(path)
    return 0


def _summary(path: Path) -> int:
    if (path / "manifest.json").is_file():
        manifest = _require_mapping(_read_json(path / "manifest.json"), "manifest")
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    state = _require_mapping(_read_json(path / "state.json"), "bundle state")
    coverage = (
        _read_json(path / "source-coverage.json")
        if (path / "source-coverage.json").is_file()
        else None
    )
    jobs = (
        _read_jobs(path / "normalized-jobs.jsonl")
        if (path / "normalized-jobs.jsonl").is_file()
        else ()
    )
    print(
        json.dumps(
            {"state": state, "coverage": coverage, "job_count": len(jobs)},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


def _evaluate_retrieval(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle)
    cases = Path(args.cases)
    if not bundle.is_absolute() or not cases.is_absolute():
        raise ValueError("retrieval evaluation paths must be absolute")
    result = evaluate_retrieval(
        LocalBundleRetrievalProbe(bundle.resolve()),
        load_cases(cases.resolve()),
    )
    print(_canonical_json(result.as_dict()))
    return 0 if result.accepted else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hr-intelligence")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--bundle-id", required=True)
    initialize.add_argument("--catalog", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--bundle-id", required=True)
    collect.add_argument("--resume", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle-id", required=True)
    validate.add_argument("--require-company-count", required=True, type=int)
    validate.add_argument("--require-provenance", action="store_true")
    prepare = commands.add_parser("prepare-analysis")
    prepare.add_argument("--bundle-id", required=True)
    prepare.add_argument(
        "--units",
        default=(
            "company,track,direction,secondary-direction,topic,"
            "executive-summary,task"
        ),
    )
    status = commands.add_parser("analysis-status")
    status.add_argument("--bundle-id", required=True)
    status.add_argument("--require-complete", action="store_true")
    accept = commands.add_parser("accept-analysis")
    accept.add_argument("--bundle-id", required=True)
    accept.add_argument("--unit")
    accept.add_argument("--response")
    accept.add_argument("--usage")
    accept.add_argument("--all-ready", action="store_true")
    build = commands.add_parser("build")
    build.add_argument("--bundle-id", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--strict", action="store_true")
    evaluate = commands.add_parser("evaluate-retrieval")
    evaluate.add_argument("--bundle", required=True)
    evaluate.add_argument("--cases", required=True)
    summary = commands.add_parser("summary")
    group = summary.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle-id")
    group.add_argument("--bundle")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        return _initialize(args)
    if args.command == "collect":
        return asyncio.run(
            _collect_async(_bundle_id(args.bundle_id), resume=args.resume)
        )
    if args.command == "validate":
        return _validate_work(
            _bundle_id(args.bundle_id),
            args.require_company_count,
            args.require_provenance,
        )
    if args.command == "prepare-analysis":
        return _prepare_analysis(args)
    if args.command == "analysis-status":
        return _analysis_status(args)
    if args.command == "accept-analysis":
        if not args.all_ready and not all((args.unit, args.response, args.usage)):
            raise ValueError("unit, response and usage are required")
        if args.all_ready and any((args.unit, args.response, args.usage)):
            raise ValueError("all-ready cannot be combined with one unit")
        return _accept_analysis(args)
    if args.command == "build":
        return _build(args)
    if args.command == "verify":
        path = Path(args.bundle)
        if not path.is_absolute():
            raise ValueError("bundle path must be absolute")
        verified = verify_bundle(path, strict=args.strict)
        print(
            _canonical_json(
                {
                    "bundle_id": str(verified.bundle_id),
                    "manifest_sha256": verified.manifest_sha256,
                    "job_count": verified.job_count,
                }
            )
        )
        return 0
    if args.command == "evaluate-retrieval":
        return _evaluate_retrieval(args)
    if args.command == "summary":
        if args.bundle_id:
            bundle_id = _bundle_id(args.bundle_id)
            path = _built(bundle_id) if _built(bundle_id).is_dir() else _work(bundle_id)
        else:
            path = Path(args.bundle)
            if not path.is_absolute():
                raise ValueError("bundle path must be absolute")
        return _summary(path.resolve())
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    sys.exit(main())
