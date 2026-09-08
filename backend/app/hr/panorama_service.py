from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .company_intelligence import project_companies, project_company
from .intelligence_documents import IntelligenceDocumentStore, VerifiedDocument
from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_COVERAGE_STATES = frozenset({"succeeded", "empty_confirmed", "partial", "failed", "not_observed"})
_FORMATS = {"pdf": "report.pdf", "xlsx": "report.xlsx", "md": "report.md"}


class PanoramaReadRepository(Protocol):
    def current_bundle(self) -> Mapping[str, object] | None: ...
    def list_bundles(self, *, limit: int = 100) -> tuple[Mapping[str, object], ...]: ...
    def bundle(self, bundle_id: UUID) -> Mapping[str, object]: ...
    def bundle_jobs(self, bundle_id: UUID) -> tuple[Mapping[str, object], ...]: ...
    def current_company_directory(self) -> Mapping[str, object] | None: ...
    def company_bundle(self, company_key: str, *, bundle_id: UUID | None = None) -> Mapping[str, object]: ...
    def company_identity(self, company_key: str, *, bundle_id: UUID | None = None) -> UUID: ...
    def bundle_company_jobs(
        self, bundle_id: UUID, company_key: str, *, offset: int, limit: int,
        location: str | None = None, status: str | None = None,
    ) -> tuple[tuple[Mapping[str, object], ...], int]: ...


class PanoramaDocumentReader(Protocol):
    def read_document(self, bundle_id: UUID, name: str) -> VerifiedDocument: ...
    def read_evidence(self, bundle_id: UUID, sha256: str) -> VerifiedDocument: ...


def _iso(value: object) -> str:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.isoformat()
    if isinstance(value, str):
        try:
            selected = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if selected.tzinfo is not None:
                return value
    raise PanoramaUnavailable("published intelligence timestamp invalid")


def _uuid(value: object, label: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        raise PanoramaUnavailable(f"published intelligence {label} invalid") from None


def _mapping_list(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, Mapping) for item in value):
        raise PanoramaUnavailable(f"published intelligence {label} invalid")
    return tuple(value)


def _source_id(company_key: str, _jobs: tuple[Mapping[str, object], ...]) -> UUID:
    if not company_key:
        raise PanoramaUnavailable("published intelligence company key invalid")
    return uuid5(NAMESPACE_URL, f"orbbec:hr-intelligence:source:{company_key}")


def _coverage_state(coverage: tuple[Mapping[str, object], ...]) -> str:
    states = {str(item.get("state")) for item in coverage}
    if not states or not states <= _COVERAGE_STATES:
        raise PanoramaUnavailable("published intelligence coverage invalid")
    if len(states) == 1:
        return next(iter(states))
    if states <= {"succeeded", "empty_confirmed"}:
        return "succeeded"
    return "partial"


def _summary(analysis: tuple[Mapping[str, object], ...]) -> str:
    for preferred in ("executive-summary", "comparison", "company", "track", "direction"):
        for unit in analysis:
            response = unit.get("response")
            if unit.get("kind") == preferred and isinstance(response, Mapping):
                value = response.get("summary")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return "本版包含已核验的公开岗位与来源覆盖，暂无可用 AI 分析。"


def _project(record: Mapping[str, object], jobs: tuple[Mapping[str, object], ...], *, summary_only: bool = False) -> dict[str, object]:
    bundle_id = _uuid(record.get("bundle_id"), "bundle")
    generated_at = _iso(record.get("generated_at"))
    imported_at = _iso(record.get("imported_at"))
    manifest_sha256 = record.get("manifest_sha256")
    if not isinstance(manifest_sha256, str) or _SHA256.fullmatch(manifest_sha256) is None:
        raise PanoramaUnavailable("published intelligence manifest invalid")
    catalog = record.get("source_catalog")
    coverage_doc = record.get("source_coverage")
    if not isinstance(catalog, Mapping) or not isinstance(coverage_doc, Mapping):
        raise PanoramaUnavailable("published intelligence sources invalid")
    companies = _mapping_list(catalog.get("companies"), "companies")
    coverage = _mapping_list(coverage_doc.get("companies"), "coverage")
    analysis = _mapping_list(record.get("analysis"), "analysis")
    usage = _mapping_list(record.get("analysis_usage"), "analysis usage")
    by_company = {str(item.get("company_key")): item for item in companies}
    coverage_by_company = {str(item.get("company_key")): item for item in coverage}
    if len(by_company) != len(companies) or len(coverage_by_company) != len(coverage) or set(by_company) != set(coverage_by_company):
        raise PanoramaUnavailable("published intelligence company coverage invalid")
    source_ids = {key: _source_id(key, jobs) for key in by_company}
    projected_coverage = []
    for key, company in by_company.items():
        item = coverage_by_company[key]
        state = item.get("state")
        if state not in _COVERAGE_STATES:
            raise PanoramaUnavailable("published intelligence coverage state invalid")
        urls = company.get("approved_urls")
        if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
            raise PanoramaUnavailable("published intelligence source URLs invalid")
        channels = item.get("channels")
        failed_channels = {
            str(channel.get("source_url")): str(channel.get("error_code"))
            for channel in channels
            if isinstance(channels, list) and isinstance(channel, Mapping)
            and channel.get("state") == "failed" and channel.get("error_code")
        } if isinstance(channels, list) else {}
        job_count = item.get("job_count")
        entry: dict[str, object] = {
            "source_id": str(source_ids[key]), "state": state,
            "observed_at": _iso(item.get("observed_at") or generated_at), "source_urls": urls,
            "job_count": 0 if job_count is None else int(job_count),
        }
        for field in ("error_code", "channel_failures"):
            if field in item:
                entry[field] = item[field]
        if failed_channels:
            entry["channel_failures"] = failed_channels
            if state == "failed":
                entry["error_code"] = next(iter(failed_channels.values()))
        elif state == "failed" and "error_code" not in entry:
            entry["error_code"] = "source_unavailable"
        projected_coverage.append(entry)
    publication = {
        "publication_id": str(bundle_id), "bundle_id": str(bundle_id),
        "batch_id": str(bundle_id), "insight_version_id": str(bundle_id),
        "manifest_sha256": manifest_sha256,
        "coverage_state": _coverage_state(coverage),
        "source_coverage": projected_coverage,
        "generated_at": generated_at, "published_at": imported_at,
    }
    if summary_only:
        insight_summary = _summary(analysis)
        return {"publication": publication, "insight": {
            "insight_version_id": str(bundle_id), "version_number": int(record.get("schema_version", 1)),
            "selected_source_ids": [str(source_ids[key]) for key in by_company],
            "summary": insight_summary, "created_at": generated_at,
        }}
    snapshots = []
    job_by_evidence: dict[tuple[str, str], dict[str, object]] = {}
    for job in jobs:
        company_key = str(job.get("company_key"))
        if company_key not in source_ids:
            raise PanoramaUnavailable("published intelligence job company invalid")
        job_id = _uuid(job.get("job_id"), "job")
        evidence_sha = str(job.get("evidence_sha256"))
        source_url = str(job.get("source_url"))
        snapshot = {
            "snapshot_id": str(job_id), "run_id": None, "production_batch_id": str(bundle_id),
            "observation_id": str(job_id), "source_id": str(source_ids[company_key]),
            "public_job_key": job.get("public_job_key"), "title": job.get("title"), "location": job.get("location"),
            "duty_excerpt": job.get("duty_excerpt"), "requirement_excerpt": job.get("requirement_excerpt"),
            "source_url": source_url, "observed_at": _iso(job.get("observed_at")),
            "content_sha256": evidence_sha, "status": job.get("status", "unknown"), "created_at": generated_at,
        }
        snapshots.append(snapshot)
        job_by_evidence.setdefault((evidence_sha, source_url), snapshot)
    facts: list[dict[str, object]] = []
    inferences: list[dict[str, object]] = []
    unknowns: list[dict[str, str]] = []
    model_versions: set[str] = set()
    for unit in analysis:
        unit_id = str(_uuid(unit.get("unit_id"), "analysis unit"))
        response = unit.get("response")
        if not isinstance(response, Mapping):
            raise PanoramaUnavailable("published intelligence analysis invalid")
        local_fact_ids: set[str] = set()
        for fact in _mapping_list(response.get("facts", []), "facts"):
            local_id = str(fact.get("fact_id"))
            matched = job_by_evidence.get((str(fact.get("evidence_sha256")), str(fact.get("source_url"))))
            if matched is None:
                continue
            fact_id = f"{unit_id}:{local_id}"
            local_fact_ids.add(local_id)
            facts.append({"fact_id": fact_id, "text": fact.get("text"), "snapshot_id": matched["snapshot_id"], "observation_id": matched["observation_id"], "source_url": fact.get("source_url"), "observed_at": _iso(fact.get("observed_at"))})
        for inference in _mapping_list(response.get("inferences", []), "inferences"):
            basis = inference.get("basis_fact_ids")
            if isinstance(basis, list) and basis and all(str(value) in local_fact_ids for value in basis):
                inferences.append({"text": inference.get("text"), "basis_fact_ids": [f"{unit_id}:{value}" for value in basis]})
        raw_unknowns = response.get("unknowns", [])
        if isinstance(raw_unknowns, list):
            unknowns.extend({"text": value} for value in raw_unknowns if isinstance(value, str) and value.strip())
        embedded_usage = unit.get("usage")
        if isinstance(embedded_usage, Mapping) and isinstance(embedded_usage.get("model"), str):
            model_versions.add(str(embedded_usage["model"]))
    sources = [{
        "source_id": str(source_ids[key]), "source_kind": "company", "canonical_name": company.get("canonical_name"),
        "aliases": company.get("aliases", []), "approved_urls": company.get("approved_urls", []), "active": True,
        "created_at": generated_at, "updated_at": generated_at,
    } for key, company in by_company.items()]
    evidence = []
    for item in _mapping_list(record.get("evidence_index"), "evidence index"):
        sha = str(item.get("sha256")); url = str(item.get("source_url")); matched = job_by_evidence.get((sha, url))
        if matched is None:
            continue
        evidence.append({"source_id": matched["source_id"], "source_url": url, "attempt_number": 1, "state": "succeeded", "error_code": None,
            "sha256": sha, "mime": item.get("mime"), "size_bytes": item.get("size_bytes"),
            "normalized_job_count": sum(1 for job in jobs if job.get("evidence_sha256") == sha), "observed_at": _iso(item.get("observed_at"))})
    insight = {
        "insight_version_id": str(bundle_id), "run_id": None, "production_batch_id": str(bundle_id),
        "version_number": int(record.get("schema_version", 1)), "selected_source_ids": [str(source_ids[key]) for key in by_company],
        "snapshot_ids": [item["snapshot_id"] for item in snapshots], "facts": facts, "inferences": inferences, "unknowns": unknowns,
        "direction_clusters": record.get("aggregates"), "summary": _summary(analysis), "source_conversation_id": None, "source_turn_id": None,
        "agent_id": "hr-local-intelligence-factory", "model_version": ", ".join(sorted(model_versions)) or "analysis unavailable", "created_at": generated_at,
    }
    return {"publication": publication, "insight": insight, "sources": sources, "snapshots": snapshots, "evidence": evidence, "analysis_usage": list(usage)}


class PanoramaService:
    """Read-only projection of Owner-approved recruiting intelligence Bundles."""

    def __init__(self, repository: PanoramaReadRepository, *, documents: PanoramaDocumentReader | None = None) -> None:
        for method in ("current_bundle", "list_bundles", "bundle", "bundle_jobs"):
            if not callable(getattr(repository, method, None)):
                raise TypeError("panorama repository invalid")
        if documents is None:
            raise TypeError("panorama document reader required")
        self._repository = repository
        self._documents = documents

    def current_report(self) -> Mapping[str, object] | None:
        record = self._repository.current_bundle()
        return None if record is None else _project(record, self._repository.bundle_jobs(_uuid(record.get("bundle_id"), "bundle")))

    def _company_bundle(self, bundle_id: UUID | None) -> Mapping[str, object] | None:
        if bundle_id is None:
            return self._repository.current_bundle()
        if not isinstance(bundle_id, UUID):
            raise TypeError("panorama bundle identifier invalid")
        return self._repository.bundle(bundle_id)

    def companies(self) -> Mapping[str, object] | None:
        narrow_read = getattr(self._repository, "current_company_directory", None)
        record = narrow_read() if callable(narrow_read) else self._repository.current_bundle()
        return None if record is None else project_companies(record)

    def company(self, company_key: str, *, bundle_id: UUID | None = None) -> Mapping[str, object]:
        if not isinstance(company_key, str) or not company_key:
            raise TypeError("panorama company key invalid")
        narrow_read = getattr(self._repository, "company_bundle", None)
        record = narrow_read(company_key, bundle_id=bundle_id) if callable(narrow_read) else self._company_bundle(bundle_id)
        if record is None:
            raise PanoramaNotFound("panorama company not found")
        return project_company(record, company_key)

    def company_jobs(
        self, company_key: str, *, bundle_id: UUID | None = None,
        offset: int = 0, limit: int = 25, location: str | None = None,
        status: str | None = None,
    ) -> Mapping[str, object]:
        if not isinstance(company_key, str) or not company_key:
            raise TypeError("panorama company key invalid")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("panorama company job offset invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("panorama company job limit invalid")
        if any(value is not None and (not isinstance(value, str) or not value) for value in (location, status)):
            raise ValueError("panorama company job filter invalid")
        if status is not None and status not in {"open", "closed", "unknown"}:
            raise ValueError("panorama company job status invalid")
        narrow_identity = getattr(self._repository, "company_identity", None)
        if callable(narrow_identity):
            selected_bundle_id = narrow_identity(company_key, bundle_id=bundle_id)
        else:
            record = self._company_bundle(bundle_id)
            if record is None:
                raise PanoramaNotFound("panorama company not found")
            project_company(record, company_key)
            selected_bundle_id = _uuid(record.get("bundle_id"), "bundle")
        items, total = self._repository.bundle_company_jobs(
            selected_bundle_id, company_key, offset=offset, limit=limit,
            location=location, status=status,
        )
        return {"bundle_id": str(selected_bundle_id), "company_key": company_key,
                "items": list(items), "total": total, "offset": offset, "limit": limit}

    def list_reports(self, *, limit: int = 100) -> tuple[Mapping[str, object], ...]:
        return tuple(_project(record, (), summary_only=True) for record in self._repository.list_bundles(limit=limit))

    def report(self, bundle_id: UUID) -> Mapping[str, object]:
        if not isinstance(bundle_id, UUID):
            raise TypeError("panorama bundle identifier invalid")
        return _project(self._repository.bundle(bundle_id), self._repository.bundle_jobs(bundle_id))

    def document(self, bundle_id: UUID, format: str) -> VerifiedDocument:
        if not isinstance(bundle_id, UUID) or format not in _FORMATS:
            raise TypeError("panorama document format invalid")
        return self._documents.read_document(bundle_id, _FORMATS[format])

    def evidence_file(self, bundle_id: UUID, sha256: str) -> VerifiedDocument:
        if not isinstance(bundle_id, UUID) or not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise TypeError("panorama evidence identifier invalid")
        return self._documents.read_evidence(bundle_id, sha256)


__all__ = ["IntelligenceDocumentStore", "PanoramaReadRepository", "PanoramaService"]
