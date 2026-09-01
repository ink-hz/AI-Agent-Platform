from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import FaeAnalysisReport

SCHEMA_NAME = "fae.analysis-report"
SCHEMA_VERSION = "1.0.0"
MAX_REPORT_BYTES = 5 * 1024 * 1024
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "fae-analysis-report"
    / "v1"
    / "schema.json"
)
CONTRACT_SHA256 = "78977bbd207f258951bc020fac79f46f5dc574e7d9a840952359b24dfffae1f8"
_FORBIDDEN_FIELDS = frozenset(
    {
        "question",
        "answer",
        "comment",
        "raw_text",
        "source_id",
        "trace_id",
        "attachment_name",
        "employee_id",
        "email",
        "phone",
        "metadata",
    }
)


class ReportContractError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReportContractError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ReportContractError("nonstandard_json_number")


def _scan_fields(value: Any) -> None:
    if isinstance(value, dict):
        if _FORBIDDEN_FIELDS.intersection(value):
            raise ReportContractError("forbidden_content_field")
        for member in value.values():
            _scan_fields(member)
    elif isinstance(value, list):
        for member in value:
            _scan_fields(member)
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ReportContractError("invalid_unicode") from exc


def load_report_document(payload: bytes) -> FaeAnalysisReport:
    if not isinstance(payload, bytes) or len(payload) > MAX_REPORT_BYTES:
        raise ReportContractError("report_limit_exceeded")
    try:
        raw = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ReportContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReportContractError("invalid_report_json") from exc
    if not isinstance(raw, dict):
        raise ReportContractError("invalid_report_shape")
    if (
        raw.get("schema_name") != SCHEMA_NAME
        or raw.get("schema_version") != SCHEMA_VERSION
    ):
        raise ReportContractError("unsupported_report_schema")
    _scan_fields(raw)
    try:
        # JSON mode preserves strict scalar validation while allowing the RFC 3339
        # strings that JSON necessarily uses for datetime values. Duplicate keys
        # and non-standard numbers have already been rejected above.
        return FaeAnalysisReport.model_validate_json(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        )
    except ValidationError as exc:
        if any(error.get("type") == "too_long" for error in exc.errors()):
            raise ReportContractError("report_limit_exceeded") from None
        messages = " ".join(str(error.get("msg", "")) for error in exc.errors())
        for code in (
            "invalid_failed_report",
            "unresolved_report_reference",
            "report_limit_exceeded",
            "incomplete_report_dimensions",
            "invalid_evidence_scope",
            "invalid_artifact_digests",
        ):
            if code in messages:
                raise ReportContractError(code) from None
        raise ReportContractError("invalid_report_document") from None


def schema_sha256() -> str:
    return sha256(SCHEMA_PATH.read_bytes()).hexdigest()
