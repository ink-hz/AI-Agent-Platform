from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

METRIC_FIELDS = ("job_count", "directions", "secondary_directions", "locations", "tracks", "seniority", "job_families", "skills", "sample_snapshot_ids")
RESPONSE_FIELDS = ("summary", "confidence", "facts", "inferences", "recommendations", "alternatives", "unknowns")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PanoramaUnavailable(f"published company intelligence {label} invalid")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise PanoramaUnavailable(f"published company intelligence {label} invalid")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PanoramaUnavailable(f"published company intelligence {label} invalid")
    return value


def _iso(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else _text(value, "timestamp")


def _bundle_id(record: Mapping[str, object]) -> UUID:
    value = record.get("bundle_id")
    if not isinstance(value, UUID):
        raise PanoramaUnavailable("published company intelligence bundle_id invalid")
    return value


def _catalog(record: Mapping[str, object]) -> Sequence[object]:
    return _sequence(_mapping(record.get("source_catalog"), "catalog").get("companies"), "catalog companies")


def _coverage_by_company(record: Mapping[str, object]) -> dict[str, Mapping[str, Any]]:
    result = {}
    raw = _mapping(record.get("source_coverage"), "coverage")
    for item in _sequence(raw.get("companies"), "coverage companies"):
        selected = _mapping(item, "coverage company")
        result[_text(selected.get("company_key"), "coverage company_key")] = selected
    return result


def _units_by_company(record: Mapping[str, object]) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for item in _sequence(record.get("analysis"), "analysis"):
        unit = _mapping(item, "analysis unit")
        if unit.get("kind") == "company":
            result.setdefault(_text(unit.get("scope_key"), "analysis scope_key"), []).append(unit)
    return result


def _coverage(item: Mapping[str, Any] | None) -> dict[str, object] | None:
    if item is None:
        return None
    observed_at = item.get("observed_at")
    job_count = item.get("job_count")
    if job_count is not None and (isinstance(job_count, bool) or not isinstance(job_count, int)):
        raise PanoramaUnavailable("published company intelligence coverage job_count invalid")
    return {
        "state": _text(item.get("state"), "coverage state"),
        "observed_at": None if observed_at is None else _iso(observed_at),
        "job_count": job_count,
        "limitations": list(_sequence(item.get("limitations", []), "coverage limitations")),
        "document_limitations": list(_sequence(item.get("document_limitations", []), "coverage document limitations")),
    }


def _summary(company: Mapping[str, Any], coverage: Mapping[str, Any] | None, units: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    summary = None
    if units:
        selected = _mapping(units[0].get("response"), "analysis response").get("summary")
        summary = None if selected is None else _text(selected, "analysis summary")
    return {
        "company_key": _text(company.get("company_key"), "company_key"),
        "canonical_name": _text(company.get("canonical_name"), "canonical_name"),
        "aliases": [_text(value, "company alias") for value in _sequence(company.get("aliases", []), "aliases")],
        "summary": summary,
        "coverage": _coverage(coverage),
    }


def _unit(unit: Mapping[str, Any]) -> dict[str, object]:
    response = _mapping(unit.get("response"), "analysis response")
    if not all(field in response for field in RESPONSE_FIELDS):
        raise PanoramaUnavailable("published company intelligence response invalid")
    return {
        "unit_id": _text(unit.get("unit_id"), "analysis unit_id"),
        "kind": "company",
        "scope_key": _text(unit.get("scope_key"), "analysis scope_key"),
        "response": {field: response[field] for field in RESPONSE_FIELDS},
    }


def project_companies(record: Mapping[str, object]) -> dict[str, object]:
    coverage, units = _coverage_by_company(record), _units_by_company(record)
    items = []
    for raw in _catalog(record):
        company = _mapping(raw, "catalog company")
        key = _text(company.get("company_key"), "company_key")
        items.append(_summary(company, coverage.get(key), units.get(key, ())))
    return {"bundle_id": str(_bundle_id(record)), "generated_at": _iso(record.get("generated_at")), "items": items, "topics": {"state": "available" if record.get("topics_available") or _mapping(record.get("source_catalog"), "catalog").get("topics") else "metadata_missing"}}


def project_company(record: Mapping[str, object], company_key: str) -> dict[str, object]:
    coverage, units = _coverage_by_company(record), _units_by_company(record)
    selected = next((_mapping(raw, "catalog company") for raw in _catalog(record) if _mapping(raw, "catalog company").get("company_key") == company_key), None)
    if selected is None:
        raise PanoramaNotFound("company not found in panorama bundle")
    selected_units = units.get(company_key, ())
    metrics = None
    aggregates = _mapping(record.get("aggregates"), "aggregates")
    if aggregates.get("schema_version") == 3:
        raw_metrics = _mapping(aggregates.get("company_matrix"), "company matrix").get(company_key)
        if raw_metrics is not None:
            source = _mapping(raw_metrics, "company metrics")
            if not all(field in source for field in METRIC_FIELDS):
                raise PanoramaUnavailable("published company intelligence metrics invalid")
            metrics = {field: source[field] for field in METRIC_FIELDS}
    return {
        "bundle_id": str(_bundle_id(record)), "generated_at": _iso(record.get("generated_at")),
        "company": _summary(selected, coverage.get(company_key), selected_units),
        "units": [_unit(unit) for unit in selected_units], "metrics": metrics,
        "related_topics": list(_sequence(record.get("related_topics", []), "related topics")),
    }


__all__ = ["METRIC_FIELDS", "RESPONSE_FIELDS", "project_companies", "project_company"]
