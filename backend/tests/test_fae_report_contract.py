from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.fae_reports.contract import (
    CONTRACT_SHA256,
    ReportContractError,
    load_report_document,
    schema_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fae-analysis-report" / "v1" / "fixtures"


def test_ready_fixture_is_strict_and_covers_the_four_report_dimensions() -> None:
    report = load_report_document((FIXTURES / "valid-ready.json").read_bytes())

    assert report.schema_name == "fae.analysis-report"
    assert report.schema_version == "1.0.0"
    assert report.status == "ready"
    assert {metric.dimension for metric in report.metrics} == {
        "usage",
        "business_value",
        "answer_effectiveness",
        "insights_improvement",
    }
    assert report.findings[0].metric_ids[0] in {
        metric.metric_id for metric in report.metrics
    }


def test_failed_fixture_contains_no_report_content() -> None:
    report = load_report_document((FIXTURES / "valid-failed.json").read_bytes())

    assert report.status == "failed"
    assert report.summary is None
    assert report.metrics == []
    assert report.findings == []
    assert report.recommendations == []


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("raw_content", "forbidden_content_field"),
        ("unresolved_reference", "unresolved_report_reference"),
        ("failed_with_findings", "invalid_failed_report"),
        ("unbounded_array", "report_limit_exceeded"),
    ],
)
def test_invalid_contract_shapes_are_rejected(mutation: str, code: str) -> None:
    ready = json.loads((FIXTURES / "valid-ready.json").read_text("utf-8"))
    if mutation == "raw_content":
        ready["question"] = "raw"
    elif mutation == "unresolved_reference":
        ready["findings"][0]["metric_ids"] = ["missing.metric"]
    elif mutation == "failed_with_findings":
        failed = json.loads((FIXTURES / "valid-failed.json").read_text("utf-8"))
        failed["findings"] = ready["findings"]
        ready = failed
    elif mutation == "unbounded_array":
        ready["metrics"][0]["filters"] = [f"filter-{index}" for index in range(21)]
    with pytest.raises(ReportContractError, match=code):
        load_report_document(json.dumps(ready, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"schema_name":"x","schema_name":"y"}', "duplicate_json_key"),
        (b'{"schema_name":NaN}', "nonstandard_json_number"),
        (b'{"schema_name":"fae.analysis-report","schema_version":"9.0.0"}', "unsupported_report_schema"),
    ],
)
def test_parser_rejects_ambiguous_or_unsupported_json(payload: bytes, code: str) -> None:
    with pytest.raises(ReportContractError, match=code):
        load_report_document(payload)


def test_document_size_is_bounded_before_json_decode() -> None:
    with pytest.raises(ReportContractError, match="report_limit_exceeded"):
        load_report_document(b" " * (5 * 1024 * 1024 + 1))


def test_ready_fixture_matches_checked_in_json_schema() -> None:
    from jsonschema import Draft202012Validator

    schema_path = FIXTURES.parent / "schema.json"
    schema = json.loads(schema_path.read_text("utf-8"))
    payload = json.loads((FIXTURES / "valid-ready.json").read_text("utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    assert schema_sha256() == CONTRACT_SHA256
