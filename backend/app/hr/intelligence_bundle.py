from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_REQUIRED_TOP_LEVEL = frozenset(
    {
        "manifest.json",
        "source-catalog.json",
        "source-coverage.json",
        "raw-evidence-index.json",
        "normalized-jobs.jsonl",
        "aggregates.json",
        "analysis.json",
        "analysis-usage.json",
        "report.md",
        "report.pdf",
        "report.xlsx",
        "checksums.sha256",
        "evidence",
    }
)
_COVERAGE_STATES = frozenset(
    {"succeeded", "empty_confirmed", "partial", "failed", "not_observed"}
)


class BundleVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedImportBundle:
    bundle_id: UUID
    path: Path
    bundle_locator: str
    manifest_sha256: str
    generated_at: datetime
    manifest: Mapping[str, object]
    catalog: Mapping[str, object]
    coverage: Mapping[str, object]
    jobs: tuple[Mapping[str, object], ...]
    aggregates: Mapping[str, object]
    analysis: tuple[Mapping[str, object], ...]
    usage: tuple[Mapping[str, object], ...]
    evidence_index: tuple[Mapping[str, object], ...]


def _json(path: Path, expected_type: type) -> object:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise BundleVerificationError("bundle JSON too large")
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BundleVerificationError("bundle JSON invalid") from None
    if not isinstance(value, expected_type):
        raise BundleVerificationError("bundle JSON shape invalid")
    return value


def _checksums(root: Path) -> dict[str, str]:
    selected: dict[str, str] = {}
    try:
        lines = (root / "checksums.sha256").read_text("utf-8").splitlines()
    except (OSError, UnicodeError):
        raise BundleVerificationError("bundle checksum index invalid") from None
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or _SHA256.fullmatch(parts[0]) is None:
            raise BundleVerificationError("bundle checksum index invalid")
        relative = PurePosixPath(parts[1])
        if relative.is_absolute() or ".." in relative.parts or parts[1] in selected:
            raise BundleVerificationError("bundle checksum path invalid")
        selected[parts[1]] = parts[0]
    return selected


def _mapping_tuple(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise BundleVerificationError(f"bundle {label} invalid")
    return tuple(value)


def verify_import_bundle(
    path: str | Path,
    *,
    expected_bundle_id: UUID | None = None,
) -> VerifiedImportBundle:
    raw_path = Path(path)
    if raw_path.is_symlink() or not raw_path.is_dir():
        raise BundleVerificationError("bundle unavailable")
    root = raw_path.resolve()
    top_level = {item.name for item in root.iterdir()}
    if top_level != _REQUIRED_TOP_LEVEL:
        raise BundleVerificationError("bundle file set invalid")
    files = tuple(item for item in root.rglob("*") if item.is_file())
    if any(item.is_symlink() for item in root.rglob("*")):
        raise BundleVerificationError("bundle symlink forbidden")
    if len(files) > 100_020:
        raise BundleVerificationError("bundle file count invalid")
    entries = _checksums(root)
    actual = {
        item.relative_to(root).as_posix()
        for item in files
        if item.name != "checksums.sha256"
    }
    if set(entries) != actual:
        raise BundleVerificationError("bundle checksum coverage invalid")
    for relative, expected in entries.items():
        file_path = root / relative
        if file_path.stat().st_size > 64 * 1024 * 1024:
            raise BundleVerificationError("bundle file too large")
        if hashlib.sha256(file_path.read_bytes()).hexdigest() != expected:
            raise BundleVerificationError("bundle checksum mismatch")
    manifest = _json(root / "manifest.json", dict)
    catalog = _json(root / "source-catalog.json", dict)
    coverage = _json(root / "source-coverage.json", dict)
    aggregates = _json(root / "aggregates.json", dict)
    analysis = _mapping_tuple(_json(root / "analysis.json", list), "analysis")
    usage = _mapping_tuple(_json(root / "analysis-usage.json", list), "usage")
    evidence_index = _mapping_tuple(
        _json(root / "raw-evidence-index.json", list),
        "evidence index",
    )
    try:
        jobs = tuple(
            json.loads(line)
            for line in (root / "normalized-jobs.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BundleVerificationError("bundle jobs invalid") from None
    if any(not isinstance(job, Mapping) for job in jobs):
        raise BundleVerificationError("bundle jobs invalid")
    try:
        bundle_id = UUID(str(manifest["bundle_id"]))
        generated_at = datetime.fromisoformat(str(manifest["generated_at"]))
        document_index = manifest["document_index"]
    except (KeyError, TypeError, ValueError):
        raise BundleVerificationError("bundle manifest invalid") from None
    if (
        manifest.get("schema_version") != 1
        or generated_at.tzinfo is None
        or not isinstance(document_index, Mapping)
        or manifest.get("company_count") != len(catalog.get("companies", []))
        or manifest.get("job_count") != len(jobs)
        or manifest.get("analysis_count") != len(analysis)
        or manifest.get("evidence_count") != len(evidence_index)
    ):
        raise BundleVerificationError("bundle manifest invalid")
    if root.name != str(bundle_id):
        raise BundleVerificationError("bundle directory identity mismatch")
    if expected_bundle_id is not None and bundle_id != expected_bundle_id:
        raise BundleVerificationError("bundle identity mismatch")
    companies = catalog.get("companies")
    coverage_companies = coverage.get("companies")
    if (
        not isinstance(companies, list)
        or not 1 <= len(companies) <= 100
        or not isinstance(coverage_companies, list)
        or len(coverage_companies) != len(companies)
    ):
        raise BundleVerificationError("bundle source coverage invalid")
    for item in coverage_companies:
        if not isinstance(item, Mapping) or item.get("state") not in _COVERAGE_STATES:
            raise BundleVerificationError("bundle source coverage invalid")
    evidence_by_hash = {}
    for item in evidence_index:
        sha256 = item.get("sha256")
        locator = item.get("locator")
        if (
            not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
            or locator != f"evidence/sha256/{sha256[:2]}/{sha256}"
        ):
            raise BundleVerificationError("bundle evidence index invalid")
        evidence_by_hash[sha256] = item
    for job in jobs:
        sha256 = job.get("evidence_sha256")
        if sha256 not in evidence_by_hash:
            raise BundleVerificationError("bundle job evidence invalid")
        try:
            UUID(str(job["job_id"]))
            UUID(str(job["source_id"]))
        except (KeyError, TypeError, ValueError):
            raise BundleVerificationError("bundle job identity invalid") from None
        if not str(job.get("source_url", "")).startswith("https://"):
            raise BundleVerificationError("bundle job source invalid")
    for name in ("report.md", "report.pdf", "report.xlsx"):
        record = document_index.get(name)
        if (
            not isinstance(record, Mapping)
            or record.get("sha256") != entries.get(name)
            or record.get("size_bytes") != (root / name).stat().st_size
        ):
            raise BundleVerificationError("bundle document index invalid")
    return VerifiedImportBundle(
        bundle_id=bundle_id,
        path=root,
        bundle_locator=f"bundles/{bundle_id}",
        manifest_sha256=hashlib.sha256(
            (root / "manifest.json").read_bytes()
        ).hexdigest(),
        generated_at=generated_at,
        manifest=manifest,
        catalog=catalog,
        coverage=coverage,
        jobs=jobs,
        aggregates=aggregates,
        analysis=analysis,
        usage=usage,
        evidence_index=evidence_index,
    )


__all__ = ["BundleVerificationError", "VerifiedImportBundle", "verify_import_bundle"]
