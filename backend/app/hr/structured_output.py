from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from .panorama_models import canonical_panorama_url

_MAX_DECODED_BYTES = 512 * 1024
_ENVELOPE = re.compile(r"<!-- platform-hr-v1:(.*?) -->", re.DOTALL)
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_ENVELOPE_KEYS = frozenset({"schema_version", "kind", "payload"})
_POSITION_PACKAGE_KEYS = frozenset({"title", "modules"})
_POSITION_MODULE_KEYS = frozenset({"mission", "jd", "jr"})
_TEXT_MODULE_KEYS = frozenset({"text"})
_CANDIDATE_MATCH_KEYS = frozenset(
    {
        "summary",
        "dimensions",
        "evidence",
        "gaps",
        "risks",
        "unknowns",
        "verification_questions",
    }
)
_INTERVIEW_PLAN_KEYS = frozenset({"title", "questions"})
_INTERVIEW_QUESTION_KEYS = frozenset(
    {
        "verification_goal",
        "candidate_reason",
        "question",
        "follow_ups",
        "strong_evidence",
        "risk_signals",
    }
)
_POSITION_TASK_SCHEMAS = {
    "jd": frozenset({"text", "change_summary", "unknowns", "evidence_refs"}),
    "jr": frozenset({"responsibilities", "must_have", "preferred", "trainable", "evaluation_criteria", "unknowns", "evidence_refs"}),
    "talent_profile": frozenset({"dimensions", "priorities", "counter_examples", "unknowns", "evidence_refs"}),
    "sourcing_strategy": frozenset({"target_sources", "keywords", "exclusions", "unknowns", "evidence_refs"}),
    "position_interview_plan": frozenset({"dimensions", "questions", "follow_ups", "evaluation_anchors", "unknowns", "evidence_refs"}),
}
_PANORAMA_REPORT_KEYS = frozenset(
    {
        "companies",
        "jobs",
        "facts",
        "direction_clusters",
        "inferences",
        "unknowns",
        "summary",
    }
)
_PANORAMA_COMPANY_KEYS = frozenset(
    {"source_id", "canonical_name", "approved_urls", "status", "error_code"}
)
_PANORAMA_JOB_KEYS = frozenset(
    {
        "company",
        "public_job_key",
        "title",
        "location",
        "duty_excerpt",
        "requirement_excerpt",
        "source_url",
        "observed_at",
        "content_sha256",
    }
)
_PANORAMA_FACT_KEYS = frozenset(
    {
        "fact_id",
        "text",
        "company",
        "public_job_key",
        "source_url",
        "observed_at",
    }
)
_PANORAMA_INFERENCE_KEYS = frozenset({"text", "basis_fact_ids"})
_PANORAMA_UNKNOWN_KEYS = frozenset({"text"})
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")

HR_WORKFLOW_CONTRACT_V1 = """HR structured-output contract v1:
For a complete岗位需求/JD/JR answer, first provide the full human-readable Markdown answer. Then append exactly one hidden `position_package` envelope using `<!-- platform-hr-v1:<unpadded-base64url-canonical-json> -->`. Its payload has exactly `title` and `modules`; `modules` has exactly `mission`, `jd`, and `jr`, each with exactly a non-empty `text` value. Do not append an envelope for ordinary questions, incomplete answers, or clarification turns."""


@dataclass(frozen=True, slots=True)
class HrStructuredEnvelope:
    kind: str
    payload: Mapping[str, object]
    visible_markdown: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_object(value: object) -> dict[str, object] | None:
    if type(value) is not dict or any(type(key) is not str for key in value):
        return None
    return value


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def _bounded_text(value: object, maximum: int) -> bool:
    return _nonempty_text(value) and len(value) <= maximum  # type: ignore[arg-type]


def _aware_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not 20 <= len(value) <= 35:
        return False
    try:
        selected = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return selected.tzinfo is not None


def _canonical_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return canonical_panorama_url(value) == value
    except ValueError:
        return False


def _valid_position_package(payload: object) -> bool:
    package = _json_object(payload)
    if package is None or set(package) != _POSITION_PACKAGE_KEYS:
        return False
    if not _nonempty_text(package["title"]):
        return False
    modules = _json_object(package["modules"])
    if modules is None or set(modules) != _POSITION_MODULE_KEYS:
        return False
    return all(
        (module := _json_object(modules[name])) is not None
        and set(module) == _TEXT_MODULE_KEYS
        and _nonempty_text(module["text"])
        for name in _POSITION_MODULE_KEYS
    )


def _valid_candidate_match(payload: object) -> bool:
    match = _json_object(payload)
    return match is not None and set(match) == _CANDIDATE_MATCH_KEYS


def _valid_candidate_interview_plan(payload: object) -> bool:
    plan = _json_object(payload)
    if (
        plan is None
        or set(plan) != _INTERVIEW_PLAN_KEYS
        or not _nonempty_text(plan["title"])
        or not isinstance(plan["questions"], list)
    ):
        return False
    return all(
        (question := _json_object(item)) is not None
        and set(question) == _INTERVIEW_QUESTION_KEYS
        for item in plan["questions"]
    )


def _valid_position_task(kind: str, payload: object) -> bool:
    value = _json_object(payload)
    schema = _POSITION_TASK_SCHEMAS.get(kind)
    if value is None or schema is None or set(value) != schema:
        return False
    if kind == "jd":
        return _bounded_text(value["text"], 131072) and all(
            _text_list(value[key], maximum=1000)
            for key in ("change_summary", "unknowns", "evidence_refs")
        )
    if kind == "jr":
        fields = ("responsibilities", "must_have", "preferred", "trainable", "evaluation_criteria", "unknowns", "evidence_refs")
        return all(_text_list(value[key], maximum=1000) for key in fields) and bool(value["responsibilities"]) and bool(value["must_have"]) and bool(value["evaluation_criteria"])
    if kind == "talent_profile":
        dimensions = _json_object(value["dimensions"])
        return dimensions is not None and bool(dimensions) and all(_nonempty_text(key) for key in dimensions) and all(
            _text_list(value[key], maximum=1000)
            for key in ("priorities", "counter_examples", "unknowns", "evidence_refs")
        ) and bool(value["priorities"])
    if kind == "sourcing_strategy":
        fields = ("target_sources", "keywords", "exclusions", "unknowns", "evidence_refs")
        return all(_text_list(value[key], maximum=1000) for key in fields) and bool(value["target_sources"]) and bool(value["keywords"])
    dimensions = _json_object(value["dimensions"])
    return dimensions is not None and bool(dimensions) and all(
        _text_list(value[key], maximum=1000)
        for key in ("questions", "follow_ups", "evaluation_anchors", "unknowns", "evidence_refs")
    ) and bool(value["questions"]) and bool(value["evaluation_anchors"])


def _text_list(value: object, *, maximum: int, allow_empty: bool = True) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and len(value) <= maximum
        and all(_nonempty_text(item) for item in value)
    )


def _valid_panorama_report(payload: object) -> bool:
    report = _json_object(payload)
    if (
        report is None
        or set(report) != _PANORAMA_REPORT_KEYS
        or not _bounded_text(report["summary"], 32768)
        or not isinstance(report["companies"], list)
        or not 1 <= len(report["companies"]) <= 100
        or not isinstance(report["jobs"], list)
        or len(report["jobs"]) > 1000
        or not isinstance(report["facts"], list)
        or len(report["facts"]) > 1000
        or not isinstance(report["direction_clusters"], dict)
        or len(report["direction_clusters"]) > 1000
        or not isinstance(report["inferences"], list)
        or len(report["inferences"]) > 1000
        or not isinstance(report["unknowns"], list)
        or len(report["unknowns"]) > 1000
    ):
        return False
    companies: set[str] = set()
    completed_companies: set[str] = set()
    for item in report["companies"]:
        company = _json_object(item)
        if (
            company is None
            or set(company) != _PANORAMA_COMPANY_KEYS
            or not _nonempty_text(company["source_id"])
            or not _bounded_text(company["canonical_name"], 500)
            or not _text_list(company["approved_urls"], maximum=20, allow_empty=False)
            or any(len(url) > 2048 for url in company["approved_urls"])
            or any(not _canonical_url(url) for url in company["approved_urls"])
            or company["status"] not in {"completed", "failed"}
            or (company["status"] == "completed" and company["error_code"] is not None)
            or (
                company["status"] == "failed"
                and company["error_code"] != "SEARCH_UNAVAILABLE"
            )
            or company["canonical_name"] in companies
        ):
            return False
        companies.add(company["canonical_name"])
        if company["status"] == "completed":
            completed_companies.add(company["canonical_name"])
    jobs: dict[tuple[str, str], tuple[str, str]] = {}
    for item in report["jobs"]:
        job = _json_object(item)
        if (
            job is None
            or set(job) != _PANORAMA_JOB_KEYS
            or not _bounded_text(job["company"], 500)
            or not _bounded_text(job["public_job_key"], 512)
            or not _bounded_text(job["title"], 1000)
            or not _bounded_text(job["location"], 1000)
            or not _bounded_text(job["duty_excerpt"], 32768)
            or not _bounded_text(job["requirement_excerpt"], 32768)
            or not _bounded_text(job["source_url"], 2048)
            or not _canonical_url(job["source_url"])
            or not _aware_timestamp(job["observed_at"])
            or not isinstance(job["content_sha256"], str)
            or _SHA256.fullmatch(job["content_sha256"]) is None
            or job["company"] not in completed_companies
            or (job["company"], job["public_job_key"]) in jobs
        ):
            return False
        jobs[(job["company"], job["public_job_key"])] = (
            job["source_url"],
            job["observed_at"],
        )
    if completed_companies and not jobs:
        return False
    fact_ids: set[str] = set()
    fact_companies: set[str] = set()
    for item in report["facts"]:
        fact = _json_object(item)
        if (
            fact is None
            or set(fact) != _PANORAMA_FACT_KEYS
            or not _bounded_text(fact["fact_id"], 128)
            or not _bounded_text(fact["text"], 8000)
            or not _bounded_text(fact["company"], 500)
            or not _bounded_text(fact["public_job_key"], 512)
            or not _bounded_text(fact["source_url"], 2048)
            or not _canonical_url(fact["source_url"])
            or not _aware_timestamp(fact["observed_at"])
            or (fact["company"], fact["public_job_key"]) not in jobs
            or jobs[(fact["company"], fact["public_job_key"])]
            != (fact["source_url"], fact["observed_at"])
            or fact["fact_id"] in fact_ids
        ):
            return False
        fact_ids.add(fact["fact_id"])
        fact_companies.add(fact["company"])
    if completed_companies and not fact_ids:
        return False
    evidence_companies = {company for company, _job_key in jobs} | fact_companies
    if not completed_companies.issubset(evidence_companies):
        return False
    for item in report["inferences"]:
        inference = _json_object(item)
        if (
            inference is None
            or set(inference) != _PANORAMA_INFERENCE_KEYS
            or not _bounded_text(inference["text"], 8000)
            or not _text_list(
                inference["basis_fact_ids"], maximum=100, allow_empty=False
            )
            or any(value not in fact_ids for value in inference["basis_fact_ids"])
            or len(set(inference["basis_fact_ids"])) != len(inference["basis_fact_ids"])
        ):
            return False
    return all(
        (unknown := _json_object(item)) is not None
        and set(unknown) == _PANORAMA_UNKNOWN_KEYS
        and _bounded_text(unknown["text"], 8000)
        for item in report["unknowns"]
    )


def _valid_payload(kind: str, payload: object) -> bool:
    if kind == "position_package":
        return _valid_position_package(payload)
    if kind == "candidate_match":
        return _valid_candidate_match(payload)
    if kind == "candidate_interview_plan":
        return _valid_candidate_interview_plan(payload)
    if kind in _POSITION_TASK_SCHEMAS:
        return _valid_position_task(kind, payload)
    if kind == "panorama_report":
        return _valid_panorama_report(payload)
    return False


def _decode_document(token: str) -> dict[str, object] | None:
    if (
        len(token) > ((_MAX_DECODED_BYTES + 2) // 3) * 4
        or _BASE64URL.fullmatch(token) is None
    ):
        return None
    try:
        decoded = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        if len(decoded) > _MAX_DECODED_BYTES:
            return None

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            document: dict[str, object] = {}
            for key, value in pairs:
                if key in document:
                    raise ValueError("duplicate JSON key")
                document[key] = value
            return document

        document = json.loads(
            decoded.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
        )
        document = _json_object(document)
        if document is None or _canonical_json(document) != decoded:
            return None
        return document
    except (
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return None


def _visible_markdown(markdown: str, match: re.Match[str]) -> str:
    before = markdown[: match.start()]
    if before.endswith("\r\n\r\n"):
        before = before[:-4]
    elif before.endswith("\n\n"):
        before = before[:-2]
    return before + markdown[match.end() :]


def encode_hr_envelope(kind: str, payload: Mapping[str, object]) -> str:
    """Encode one bounded canonical HR structured-output envelope."""
    if not isinstance(kind, str) or not isinstance(payload, Mapping):
        raise TypeError("HR envelope invalid")
    try:
        document = json.loads(
            _canonical_json(
                {"schema_version": 1, "kind": kind, "payload": dict(payload)}
            ).decode("utf-8")
        )
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("HR envelope invalid") from None
    if not isinstance(document, dict) or not _valid_payload(
        kind, document.get("payload")
    ):
        raise ValueError("HR envelope invalid")
    encoded = _canonical_json(document)
    if len(encoded) > _MAX_DECODED_BYTES:
        raise ValueError("HR envelope invalid")
    token = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
    return f"<!-- platform-hr-v1:{token} -->"


def extract_hr_envelope(
    markdown: str, expected_kind: str
) -> HrStructuredEnvelope | None:
    """Return None when no envelope exists; reject malformed or ambiguous envelopes."""
    if not isinstance(markdown, str) or not isinstance(expected_kind, str):
        return None
    matches = tuple(_ENVELOPE.finditer(markdown))
    if not matches:
        return None
    if len(matches) != 1:
        return None
    document = _decode_document(matches[0].group(1))
    if (
        document is None
        or set(document) != _ENVELOPE_KEYS
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or not isinstance(document["kind"], str)
        or document["kind"] != expected_kind
        or not _valid_payload(document["kind"], document["payload"])
    ):
        return None
    payload = _json_object(document["payload"])
    if payload is None:
        return None
    visible_markdown = _visible_markdown(markdown, matches[0])
    return HrStructuredEnvelope(
        kind=document["kind"], payload=payload, visible_markdown=visible_markdown
    )
