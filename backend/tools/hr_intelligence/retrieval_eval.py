from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.hr.intelligence_markdown import VerifiedMarkdownChunk
from app.hr.panorama_context import PanoramaContextFragment, PanoramaContextProvider

from .bundle import verify_bundle

_CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_TASK_KINDS = frozenset(
    {
        "general_chat",
        "jd",
        "jr",
        "talent_profile",
        "sourcing_strategy",
        "candidate_match",
        "position_interview_plan",
        "candidate_interview_plan",
    }
)
_ROUTING_FIELDS = (
    "tracks",
    "job_families",
    "directions",
    "secondary_directions",
    "locations",
    "seniority",
    "skills",
    "task_kinds",
)


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    case_id: str
    task_kind: str
    query: str
    position_context: Mapping[str, object]
    expected_companies: tuple[str, ...]
    expected_tags: tuple[str, ...]
    forbidden_companies: tuple[str, ...]
    requires_evidence: bool
    coverage_scenario: str | None

    def __post_init__(self) -> None:
        if _CASE_ID.fullmatch(self.case_id) is None:
            raise ValueError("retrieval case identity invalid")
        if self.task_kind not in _TASK_KINDS:
            raise ValueError("retrieval case task kind invalid")
        if not isinstance(self.query, str) or not self.query.strip() or "\0" in self.query:
            raise ValueError("retrieval case query invalid")
        if not isinstance(self.position_context, Mapping):
            raise TypeError("retrieval case position context invalid")
        for values in (
            self.expected_companies,
            self.expected_tags,
            self.forbidden_companies,
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError("retrieval case expectations invalid")
        if not isinstance(self.requires_evidence, bool):
            raise TypeError("retrieval case evidence expectation invalid")
        if self.coverage_scenario not in {None, "partial_source"}:
            raise ValueError("retrieval case coverage scenario invalid")


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    prompt_document: Mapping[str, object]
    selected_companies: frozenset[str]
    selected_tags: frozenset[str]
    selected_evidence_ids: frozenset[str]
    known_evidence_ids: frozenset[str]


class RetrievalProbe(Protocol):
    def retrieve(
        self, case: RetrievalCase, *, replay: bool = False
    ) -> RetrievalObservation: ...


@dataclass(frozen=True, slots=True)
class RetrievalEvaluation:
    case_count: int
    task_kinds: frozenset[str]
    context_relevance: Decimal
    provenance_coverage: Decimal
    fabricated_source_count: int
    replay_mismatch_count: int
    failures: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return (
            self.case_count >= 40
            and self.task_kinds == _TASK_KINDS
            and self.context_relevance >= Decimal("0.90")
            and self.provenance_coverage == Decimal(1)
            and self.fabricated_source_count == 0
            and self.replay_mismatch_count == 0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "case_count": self.case_count,
            "task_kinds": sorted(self.task_kinds),
            "context_relevance": str(self.context_relevance),
            "provenance_coverage": str(self.provenance_coverage),
            "fabricated_source_count": self.fabricated_source_count,
            "replay_mismatch_count": self.replay_mismatch_count,
            "accepted": self.accepted,
            "failures": list(self.failures),
        }


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"retrieval case {label} invalid")
    return tuple(value)


def load_cases(path: str | Path) -> tuple[RetrievalCase, ...]:
    selected = Path(path)
    if not selected.is_absolute():
        raise ValueError("retrieval cases path must be absolute")
    try:
        document = json.loads(selected.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("retrieval cases unavailable") from None
    if not isinstance(document, Mapping) or document.get("schema_version") != 1:
        raise ValueError("retrieval cases schema invalid")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("retrieval cases invalid")
    cases: list[RetrievalCase] = []
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise TypeError("retrieval case invalid")
        try:
            position_context = raw["position_context"]
            if not isinstance(position_context, Mapping):
                raise TypeError
            cases.append(
                RetrievalCase(
                    case_id=str(raw["case_id"]),
                    task_kind=str(raw["task_kind"]),
                    query=str(raw["query"]),
                    position_context=dict(position_context),
                    expected_companies=_strings(
                        raw["expected_companies"], "companies"
                    ),
                    expected_tags=_strings(raw["expected_tags"], "tags"),
                    forbidden_companies=_strings(
                        raw["forbidden_companies"], "forbidden companies"
                    ),
                    requires_evidence=raw["requires_evidence"],
                    coverage_scenario=(
                        str(raw["coverage_scenario"])
                        if raw.get("coverage_scenario") is not None
                        else None
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("retrieval case invalid") from None
    if len({item.case_id for item in cases}) != len(cases):
        raise ValueError("retrieval case identity invalid")
    return tuple(cases)


def evaluate_retrieval(
    provider: RetrievalProbe,
    cases: tuple[RetrievalCase, ...],
) -> RetrievalEvaluation:
    if not callable(getattr(provider, "retrieve", None)):
        raise TypeError("retrieval probe invalid")
    if not isinstance(cases, tuple) or not cases or any(
        not isinstance(item, RetrievalCase) for item in cases
    ):
        raise TypeError("retrieval cases invalid")
    relevant = 0
    provenance_total = 0
    provenance_valid = 0
    fabricated: set[str] = set()
    replay_mismatches = 0
    failures: list[str] = []
    for case in cases:
        observation = provider.retrieve(case)
        missing_companies = set(case.expected_companies) - set(
            observation.selected_companies
        )
        missing_tags = set(case.expected_tags) - set(observation.selected_tags)
        forbidden = set(case.forbidden_companies) & set(
            observation.selected_companies
        )
        markdown = observation.prompt_document.get("markdown_context")
        schema_version = observation.prompt_document.get("schema_version")
        reasons: list[str] = []
        if schema_version != 3 or not isinstance(markdown, str) or not markdown.strip():
            reasons.append("Markdown context unavailable")
        if missing_companies:
            reasons.append(f"missing companies={sorted(missing_companies)}")
        if missing_tags:
            reasons.append(f"missing tags={sorted(missing_tags)}")
        if forbidden:
            reasons.append(f"forbidden companies={sorted(forbidden)}")
        if case.requires_evidence and not observation.selected_evidence_ids:
            reasons.append("evidence required")
        if (
            case.coverage_scenario == "partial_source"
            and observation.prompt_document.get("intelligence_status") != "partial"
        ):
            reasons.append("partial-source status missing")
        if not reasons:
            relevant += 1
        else:
            failures.append(f"{case.case_id}: {'; '.join(reasons)}")

        provenance_total += len(observation.selected_evidence_ids)
        valid = observation.selected_evidence_ids & observation.known_evidence_ids
        provenance_valid += len(valid)
        fabricated.update(
            observation.selected_evidence_ids - observation.known_evidence_ids
        )
        replay = provider.retrieve(case, replay=True)
        if dict(replay.prompt_document) != dict(observation.prompt_document):
            replay_mismatches += 1
            failures.append(f"{case.case_id}: pinned replay mismatch")
    count = len(cases)
    relevance = Decimal(relevant) / Decimal(count)
    provenance = (
        Decimal(provenance_valid) / Decimal(provenance_total)
        if provenance_total
        else Decimal(1)
    )
    return RetrievalEvaluation(
        case_count=count,
        task_kinds=frozenset(item.task_kind for item in cases),
        context_relevance=relevance,
        provenance_coverage=provenance,
        fabricated_source_count=len(fabricated),
        replay_mismatch_count=replay_mismatches,
        failures=tuple(failures),
    )


class _LocalSource:
    def __init__(self, record: Mapping[str, object], jobs: tuple[Mapping[str, object], ...]):
        self.record = record
        self.jobs = jobs
        self.available = True
        self.position_references: dict[UUID, Mapping[str, object]] = {}
        self.conversation_references: dict[UUID, Mapping[str, object]] = {}

    def current_bundle(self):
        return self.record if self.available else None

    def bundle_jobs(self, bundle_id):
        return self.jobs

    def bundle_reference_for_turn(self, owner_id, position_id, turn_id):
        return self.position_references.get(turn_id)

    def record_bundle_reference(self, **values):
        record = {"context_document": values["context_document"]}
        self.position_references[values["turn_id"]] = record
        return record

    def conversation_bundle_reference_for_turn(
        self, owner_id, conversation_id, turn_id
    ):
        return self.conversation_references.get(turn_id)

    def record_conversation_bundle_reference(self, **values):
        record = {"context_document": values["context_document"]}
        self.conversation_references[values["turn_id"]] = record
        return record


class _LocalMarkdownStore:
    def __init__(self, root: Path, documents: Mapping[str, Mapping[str, object]]):
        self.root = root
        self.documents = documents

    def read_chunk(self, bundle_id: UUID, record: Mapping[str, object]):
        path = record.get("path")
        if not isinstance(path, str):
            raise TypeError("retrieval chunk path invalid")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or path not in self.documents:
            raise ValueError("retrieval chunk path invalid")
        body = self.root.joinpath(*relative.parts).read_bytes()
        document = self.documents[path]
        if hashlib.sha256(body).hexdigest() != document.get("sha256"):
            raise ValueError("retrieval document checksum mismatch")
        try:
            start = int(record["byte_start"])
            end = int(record["byte_end"])
            expected = str(record["sha256"])
            chunk_id = str(record["chunk_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("retrieval chunk invalid") from None
        selected = body[start:end]
        if start < 0 or end <= start or hashlib.sha256(selected).hexdigest() != expected:
            raise ValueError("retrieval chunk checksum mismatch")
        return VerifiedMarkdownChunk(
            chunk_id=chunk_id,
            path=path,
            sha256=expected,
            text=selected.decode("utf-8"),
        )


class LocalBundleRetrievalProbe:
    """Exercise the production retriever against one strictly verified local Bundle."""

    def __init__(self, bundle: str | Path) -> None:
        verified = verify_bundle(bundle, strict=True)
        if verified.schema_version != 2:
            raise ValueError("retrieval evaluation requires Bundle v2")
        root = verified.path
        manifest = json.loads((root / "manifest.json").read_text("utf-8"))
        chunks = json.loads((root / "agent/chunk-index.json").read_text("utf-8"))
        record = {
            "bundle_id": verified.bundle_id,
            "generated_at": manifest["generated_at"],
            "schema_version": 2,
            "manifest_sha256": verified.manifest_sha256,
            "source_catalog": json.loads(
                (root / "source-catalog.json").read_text("utf-8")
            ),
            "source_coverage": json.loads(
                (root / "source-coverage.json").read_text("utf-8")
            ),
            "aggregates": json.loads((root / "aggregates.json").read_text("utf-8")),
            "analysis": json.loads((root / "analysis.json").read_text("utf-8")),
            "agent_chunk_index": chunks,
            "agent_document_index": verified.agent_document_index,
        }
        jobs = tuple(
            json.loads(line)
            for line in (root / "normalized-jobs.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        )
        evidence = json.loads((root / "raw-evidence-index.json").read_text("utf-8"))
        self._known_evidence_ids = frozenset(
            str(item["sha256"])
            for item in evidence
            if isinstance(item, Mapping)
            and isinstance(item.get("sha256"), str)
            and _SHA256.fullmatch(str(item["sha256"])) is not None
        )
        self._chunks = {
            str(item["chunk_id"]): item
            for item in chunks
            if isinstance(item, Mapping) and isinstance(item.get("chunk_id"), str)
        }
        self._source = _LocalSource(record, jobs)
        self._provider = PanoramaContextProvider(
            self._source,
            markdown_store=_LocalMarkdownStore(root, verified.agent_document_index),
        )

    def retrieve(
        self, case: RetrievalCase, *, replay: bool = False
    ) -> RetrievalObservation:
        owner_id = uuid5(NAMESPACE_URL, "orbbec:hr-intelligence:retrieval-owner")
        turn_id = uuid5(NAMESPACE_URL, f"orbbec:hr-intelligence:eval:{case.case_id}")
        position_id = uuid5(
            NAMESPACE_URL, f"orbbec:hr-intelligence:eval-position:{case.case_id}"
        )
        conversation_id = uuid5(
            NAMESPACE_URL, f"orbbec:hr-intelligence:eval-conversation:{case.case_id}"
        )
        previous = self._source.available
        coverage = self._source.record.get("source_coverage")
        changed_state: tuple[dict[str, object], object] | None = None
        if case.coverage_scenario == "partial_source" and isinstance(
            coverage, Mapping
        ):
            companies = coverage.get("companies")
            if isinstance(companies, list) and companies and isinstance(
                companies[0], dict
            ):
                changed_state = (companies[0], companies[0].get("state"))
                companies[0]["state"] = "partial"
        self._source.available = not replay
        try:
            fragment = (
                self._provider.for_conversation_turn(
                    owner_id, conversation_id, case.query, turn_id
                )
                if case.task_kind == "general_chat"
                else self._provider.for_turn(
                    owner_id,
                    position_id,
                    case.query,
                    turn_id,
                    task_kind=case.task_kind,
                    position_context=case.position_context,
                )
            )
        finally:
            self._source.available = previous
            if changed_state is not None:
                company, state = changed_state
                company["state"] = state
        if not isinstance(fragment, PanoramaContextFragment):
            return RetrievalObservation({}, frozenset(), frozenset(), frozenset(), self._known_evidence_ids)
        selected = [
            self._chunks.get(str(item.get("chunk_id"))) for item in fragment.chunks
        ]
        selected = [item for item in selected if isinstance(item, Mapping)]
        companies = frozenset(
            value
            for item in selected
            for value in item.get("companies", [])
            if isinstance(value, str)
        )
        tags = frozenset(
            value
            for item in selected
            for field in _ROUTING_FIELDS
            for value in item.get(field, [])
            if isinstance(value, str)
        )
        evidence_ids = frozenset(
            value
            for item in fragment.chunks
            for value in item.get("evidence_ids", [])
            if isinstance(value, str)
        )
        return RetrievalObservation(
            fragment.as_prompt_document(),
            companies,
            tags,
            evidence_ids,
            self._known_evidence_ids,
        )


__all__ = [
    "LocalBundleRetrievalProbe",
    "RetrievalCase",
    "RetrievalEvaluation",
    "RetrievalObservation",
    "evaluate_retrieval",
    "load_cases",
]
