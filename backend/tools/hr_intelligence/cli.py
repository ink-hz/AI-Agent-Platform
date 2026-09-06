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
) -> tuple[dict[str, Mapping[str, object]], dict[UUID, NormalizedJob]]:
    coverage_path = work / "source-coverage.json"
    jobs_path = work / "normalized-jobs.jsonl"
    if not coverage_path.is_file():
        return {}, {}
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
    return companies, {job.job_id: job for job in jobs}


async def _collect_async(bundle_id: UUID, *, resume: bool = False) -> int:
    work = _work(bundle_id)
    catalog = _read_json(work / "source-catalog.json")
    companies = _catalog_companies(catalog)
    archive = EvidenceArchive(work / "evidence")
    previous_coverage, previous_jobs = (
        _resumable_collection(work) if resume else ({}, {})
    )
    coverage: list[dict[str, object]] = []
    all_jobs: dict[UUID, NormalizedJob] = dict(previous_jobs)
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
                    "limitations": (
                        []
                        if state in {"succeeded", "empty_confirmed"}
                        else ["一个或多个公开招聘渠道未能完成采集"]
                    ),
                }
            )
    jobs = tuple(all_jobs.values())
    _write_jobs(work / "normalized-jobs.jsonl", jobs)
    _write_json(
        work / "source-coverage.json",
        {"schema_version": 1, "companies": coverage},
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
        for job in jobs:
            require_evidence(job.evidence_sha256, "normalized job evidence invalid")
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
                UUID(str(item["job_id"])),
                str(item["sha256"]),
                str(item["source_url"]),
                datetime.fromisoformat(str(item["observed_at"])),
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
