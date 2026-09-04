from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

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


def _valid_payload(kind: str, payload: object) -> bool:
    if kind == "position_package":
        return _valid_position_package(payload)
    if kind == "candidate_match":
        return _valid_candidate_match(payload)
    if kind == "candidate_interview_plan":
        return _valid_candidate_interview_plan(payload)
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
