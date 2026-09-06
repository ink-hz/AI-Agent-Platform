from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from .agent_markdown import compile_agent_markdown
from .analysis_units import AcceptedAnalysis
from .chunk_index import MarkdownChunk, validate_chunk_index
from .exports import build_markdown, build_pdf, build_xlsx
from .models import NormalizedJob

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_REQUIRED_V1_TOP_LEVEL = frozenset(
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
_REQUIRED_V2_TOP_LEVEL = _REQUIRED_V1_TOP_LEVEL | {"agent"}
_CHUNK_KEYS = frozenset(
    {
        "chunk_id",
        "path",
        "heading",
        "byte_start",
        "byte_end",
        "sha256",
        "scope",
        "scope_key",
        "companies",
        "tracks",
        "job_families",
        "directions",
        "secondary_directions",
        "locations",
        "seniority",
        "skills",
        "task_kinds",
        "evidence_ids",
        "priority",
    }
)


class BundleVerificationError(ValueError):
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
        raise BundleVerificationError("bundle JSON invalid") from None


def _json_bytes(value: object) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


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


@dataclass(frozen=True, slots=True)
class BundleInputs:
    bundle_id: UUID
    generated_at: datetime
    source_catalog: Mapping[str, object]
    source_coverage: Mapping[str, object]
    jobs: tuple[NormalizedJob, ...]
    aggregates: Mapping[str, object]
    analyses: tuple[AcceptedAnalysis, ...]
    evidence_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.bundle_id, UUID):
            raise BundleVerificationError("bundle identity invalid")
        if (
            not isinstance(self.generated_at, datetime)
            or self.generated_at.tzinfo is None
        ):
            raise BundleVerificationError("bundle generation time invalid")
        if not isinstance(self.source_catalog, Mapping) or not isinstance(
            self.source_coverage, Mapping
        ):
            raise BundleVerificationError("bundle sources invalid")
        if not isinstance(self.jobs, tuple) or any(
            not isinstance(job, NormalizedJob) for job in self.jobs
        ):
            raise BundleVerificationError("bundle jobs invalid")
        if not isinstance(self.analyses, tuple) or any(
            not isinstance(item, AcceptedAnalysis) for item in self.analyses
        ):
            raise BundleVerificationError("bundle analysis invalid")
        if any(item.unit.bundle_id != self.bundle_id for item in self.analyses):
            raise BundleVerificationError("bundle analysis identity invalid")
        root = Path(self.evidence_root)
        if not root.is_absolute():
            raise BundleVerificationError("bundle evidence root must be absolute")
        object.__setattr__(self, "evidence_root", root.resolve())


@dataclass(frozen=True, slots=True)
class VerifiedBundle:
    bundle_id: UUID
    path: Path
    manifest_sha256: str
    job_count: int
    document_index: Mapping[str, Mapping[str, object]]
    schema_version: int
    agent_document_index: Mapping[str, Mapping[str, object]]


def _evidence_index(inputs: BundleInputs) -> list[dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    references = [
        (job.evidence_sha256, job.source_url, job.observed_at.isoformat())
        for job in inputs.jobs
    ]
    references.extend(
        (
            evidence.sha256,
            evidence.source_url,
            evidence.observed_at.isoformat(),
        )
        for analysis in inputs.analyses
        for evidence in analysis.unit.evidence
    )
    for sha256, source_url, observed_at in references:
        path = (
            inputs.evidence_root
            / "sha256"
            / sha256[:2]
            / sha256
        )
        if not path.is_file():
            raise BundleVerificationError("bundle evidence missing")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha256:
            raise BundleVerificationError("bundle evidence checksum mismatch")
        mime = _evidence_mime(path, sha256, len(body))
        current = selected.setdefault(
            sha256,
            {
                "sha256": sha256,
                "source_url": source_url,
                "observed_at": observed_at,
                "mime": mime,
                "size_bytes": len(body),
                "locator": f"evidence/sha256/{sha256[:2]}/{sha256}",
            },
        )
        if current["source_url"] != source_url:
            current["source_url"] = min(str(current["source_url"]), source_url)
    return [selected[key] for key in sorted(selected)]


def _evidence_mime(path: Path, sha256: str, size_bytes: int) -> str:
    metadata_paths = sorted(path.parent.glob(f"{sha256}.metadata.*.json"))
    if not metadata_paths:
        return "application/octet-stream"
    body = metadata_paths[0].read_bytes()
    expected_metadata_sha = metadata_paths[0].name.removeprefix(
        f"{sha256}.metadata."
    ).removesuffix(".json")
    if hashlib.sha256(body).hexdigest() != expected_metadata_sha:
        raise BundleVerificationError("bundle evidence metadata checksum mismatch")
    try:
        metadata = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BundleVerificationError("bundle evidence metadata invalid") from None
    mime = metadata.get("mime") if isinstance(metadata, Mapping) else None
    if (
        not isinstance(mime, str)
        or not mime.strip()
        or len(mime) > 255
        or metadata.get("sha256") != sha256
        or metadata.get("size_bytes") != size_bytes
    ):
        raise BundleVerificationError("bundle evidence metadata invalid")
    return mime.strip()


def _input_fingerprint(
    inputs: BundleInputs,
    jobs: list[dict[str, object]],
    analyses: list[dict[str, object]],
    evidence: list[dict[str, object]],
) -> str:
    body = _canonical_json(
        {
            "bundle_id": str(inputs.bundle_id),
            "generated_at": inputs.generated_at.isoformat(),
            "source_catalog": inputs.source_catalog,
            "source_coverage": inputs.source_coverage,
            "jobs": jobs,
            "aggregates": inputs.aggregates,
            "analyses": analyses,
            "evidence": evidence,
            "bundle_schema_version": 2,
            "agent_markdown_version": 1,
        }
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _atomic_write(path: Path, body: bytes) -> None:
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


def _write_checksums(staging: Path) -> None:
    entries = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        if relative == "checksums.sha256":
            continue
        entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
    _atomic_write(staging / "checksums.sha256", ("\n".join(entries) + "\n").encode())


def build_bundle(inputs: BundleInputs, *, root: str | Path) -> Path:
    if not isinstance(inputs, BundleInputs):
        raise TypeError("bundle inputs required")
    selected_root = Path(root)
    if not selected_root.is_absolute():
        raise BundleVerificationError("bundle root must be absolute")
    selected_root = selected_root.resolve()
    final = selected_root / str(inputs.bundle_id)
    jobs = [
        _job_dict(job)
        for job in sorted(
            inputs.jobs,
            key=lambda item: (item.company_key, item.public_job_key, str(item.job_id)),
        )
    ]
    analyses = [
        item.as_dict()
        for item in sorted(inputs.analyses, key=lambda value: str(value.unit.unit_id))
    ]
    usage = [item.usage.as_dict() for item in inputs.analyses]
    evidence = _evidence_index(inputs)
    input_sha256 = _input_fingerprint(inputs, jobs, analyses, evidence)
    if final.exists():
        verified = verify_bundle(final, strict=True)
        manifest = json.loads((final / "manifest.json").read_text("utf-8"))
        if manifest.get("input_sha256") != input_sha256:
            raise BundleVerificationError("bundle is immutable")
        return verified.path
    selected_root.mkdir(parents=True, exist_ok=True)
    staging = selected_root / f".{inputs.bundle_id}.{uuid4().hex}.building"
    staging.mkdir(mode=0o750)
    try:
        _atomic_write(
            staging / "source-catalog.json", _json_bytes(inputs.source_catalog)
        )
        _atomic_write(
            staging / "source-coverage.json", _json_bytes(inputs.source_coverage)
        )
        _atomic_write(
            staging / "normalized-jobs.jsonl",
            b"".join(_json_bytes(job) for job in jobs),
        )
        _atomic_write(staging / "aggregates.json", _json_bytes(inputs.aggregates))
        _atomic_write(staging / "analysis.json", _json_bytes(analyses))
        _atomic_write(staging / "analysis-usage.json", _json_bytes(usage))
        _atomic_write(staging / "raw-evidence-index.json", _json_bytes(evidence))
        for item in evidence:
            sha256 = str(item["sha256"])
            source = inputs.evidence_root / "sha256" / sha256[:2] / sha256
            destination = staging / str(item["locator"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        report_input = {
            "bundle_id": str(inputs.bundle_id),
            "generated_at": inputs.generated_at.isoformat(),
            "jobs": jobs,
            "coverage": inputs.source_coverage.get("companies", []),
            "analysis": analyses,
            "usage": usage,
            "aggregates": inputs.aggregates,
            "evidence": evidence,
        }
        agent_package = compile_agent_markdown(
            bundle_id=inputs.bundle_id,
            generated_at=inputs.generated_at,
            catalog=inputs.source_catalog,
            coverage=inputs.source_coverage,
            aggregates=inputs.aggregates,
            analyses=tuple(analyses),
        )
        for relative, body in agent_package.files.items():
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(destination, body)
        reports = {
            "report.md": build_markdown(report_input),
            "report.pdf": build_pdf(report_input),
            "report.xlsx": build_xlsx(report_input),
        }
        for name, body in reports.items():
            _atomic_write(staging / name, body)
        document_index = {
            name: {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "mime": {
                    "report.md": "text/markdown; charset=utf-8",
                    "report.pdf": "application/pdf",
                    "report.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }[name],
            }
            for name, body in reports.items()
        }
        agent_document_index = {
            name: {
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "mime": (
                    "application/json"
                    if name.endswith(".json")
                    else "text/markdown; charset=utf-8"
                ),
            }
            for name, body in sorted(agent_package.files.items())
        }
        manifest = {
            "schema_version": 2,
            "bundle_id": str(inputs.bundle_id),
            "generated_at": inputs.generated_at.isoformat(),
            "input_sha256": input_sha256,
            "job_count": len(jobs),
            "company_count": len(inputs.source_catalog.get("companies", [])),
            "analysis_count": len(analyses),
            "evidence_count": len(evidence),
            "document_index": document_index,
            "agent_document_index": agent_document_index,
            "agent_chunk_count": len(agent_package.chunks),
        }
        _atomic_write(staging / "manifest.json", _json_bytes(manifest))
        _write_checksums(staging)
        verify_bundle(staging, strict=True, expected_bundle_id=inputs.bundle_id)
        os.replace(staging, final)
        return verify_bundle(final, strict=True).path
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _checksum_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in (path / "checksums.sha256").read_text("utf-8").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or _SHA256.fullmatch(parts[0]) is None:
            raise BundleVerificationError("bundle checksum index invalid")
        relative = PurePosixPath(parts[1])
        if relative.is_absolute() or ".." in relative.parts or parts[1] in entries:
            raise BundleVerificationError("bundle checksum path invalid")
        entries[parts[1]] = parts[0]
    return entries


def verify_bundle(
    path: str | Path,
    *,
    strict: bool = False,
    expected_bundle_id: UUID | None = None,
) -> VerifiedBundle:
    selected = Path(path).resolve()
    if not selected.is_dir():
        raise BundleVerificationError("bundle unavailable")
    try:
        manifest = json.loads((selected / "manifest.json").read_text("utf-8"))
        schema_version = manifest["schema_version"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise BundleVerificationError("bundle manifest invalid") from None
    if schema_version not in {1, 2}:
        raise BundleVerificationError("bundle manifest invalid")
    required = (
        _REQUIRED_V1_TOP_LEVEL
        if schema_version == 1
        else _REQUIRED_V2_TOP_LEVEL
    )
    top_level = {item.name for item in selected.iterdir()}
    if not required.issubset(top_level):
        raise BundleVerificationError("bundle incomplete")
    if strict and top_level != required:
        raise BundleVerificationError("bundle contains unexpected files")
    entries = _checksum_entries(selected)
    actual_files = {
        item.relative_to(selected).as_posix()
        for item in selected.rglob("*")
        if item.is_file() and item.name != "checksums.sha256"
    }
    if set(entries) != actual_files:
        raise BundleVerificationError("bundle checksum coverage invalid")
    for relative, expected in entries.items():
        actual = hashlib.sha256((selected / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise BundleVerificationError("bundle checksum mismatch")
    try:
        bundle_id = UUID(str(manifest["bundle_id"]))
        job_count = int(manifest["job_count"])
        document_index = manifest["document_index"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise BundleVerificationError("bundle manifest invalid") from None
    if not isinstance(document_index, Mapping):
        raise BundleVerificationError("bundle manifest invalid")
    if expected_bundle_id is not None and bundle_id != expected_bundle_id:
        raise BundleVerificationError("bundle identity mismatch")
    if selected.name != str(bundle_id) and expected_bundle_id is None:
        raise BundleVerificationError("bundle directory identity mismatch")
    for name in ("report.md", "report.pdf", "report.xlsx"):
        document = document_index.get(name)
        if not isinstance(document, Mapping):
            raise BundleVerificationError("bundle document index invalid")
        if (
            document.get("sha256")
            != hashlib.sha256((selected / name).read_bytes()).hexdigest()
        ):
            raise BundleVerificationError("bundle document checksum mismatch")
    agent_document_index: Mapping[str, Mapping[str, object]] = {}
    if schema_version == 2:
        raw_agent_index = manifest.get("agent_document_index")
        if not isinstance(raw_agent_index, Mapping):
            raise BundleVerificationError("bundle Agent document index invalid")
        agent_files = {
            item.relative_to(selected).as_posix()
            for item in (selected / "agent").rglob("*")
            if item.is_file()
        }
        if set(raw_agent_index) != agent_files:
            raise BundleVerificationError("bundle Agent document index invalid")
        for name, raw_record in raw_agent_index.items():
            if not isinstance(name, str) or not isinstance(raw_record, Mapping):
                raise BundleVerificationError("bundle Agent document index invalid")
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "agent"
                or ".." in relative.parts
            ):
                raise BundleVerificationError("bundle Agent document path invalid")
            body = selected.joinpath(*relative.parts).read_bytes()
            if (
                raw_record.get("sha256") != hashlib.sha256(body).hexdigest()
                or raw_record.get("size_bytes") != len(body)
            ):
                raise BundleVerificationError("bundle Agent document checksum mismatch")
        try:
            raw_chunks = json.loads(
                (selected / "agent/chunk-index.json").read_text("utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise BundleVerificationError("bundle Agent chunk index invalid") from None
        if (
            not isinstance(raw_chunks, list)
            or manifest.get("agent_chunk_count") != len(raw_chunks)
            or not raw_chunks
        ):
            raise BundleVerificationError("bundle Agent chunk index invalid")
        chunks = []
        for item in raw_chunks:
            if not isinstance(item, Mapping) or set(item) != _CHUNK_KEYS:
                raise BundleVerificationError("bundle Agent chunk index invalid")
            try:
                chunks.append(
                    MarkdownChunk(
                        **{
                            **item,
                            **{
                                key: tuple(item[key])
                                for key in (
                                    "companies",
                                    "tracks",
                                    "job_families",
                                    "directions",
                                    "secondary_directions",
                                    "locations",
                                    "seniority",
                                    "skills",
                                    "task_kinds",
                                    "evidence_ids",
                                )
                            },
                        }
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise BundleVerificationError(
                    "bundle Agent chunk index invalid"
                ) from None
        markdown_files = {
            name: selected.joinpath(*PurePosixPath(name).parts).read_bytes()
            for name in agent_files
            if name.endswith(".md")
        }
        try:
            validate_chunk_index(markdown_files, tuple(chunks))
        except ValueError:
            raise BundleVerificationError("bundle Agent chunk index invalid") from None
        agent_document_index = {
            str(name): value
            for name, value in raw_agent_index.items()
            if isinstance(value, Mapping)
        }
    return VerifiedBundle(
        bundle_id=bundle_id,
        path=selected,
        manifest_sha256=hashlib.sha256(
            (selected / "manifest.json").read_bytes()
        ).hexdigest(),
        job_count=job_count,
        document_index=document_index,
        schema_version=schema_version,
        agent_document_index=agent_document_index,
    )


__all__ = [
    "BundleInputs",
    "BundleVerificationError",
    "VerifiedBundle",
    "build_bundle",
    "verify_bundle",
]
