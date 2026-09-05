from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from fixtures.conversation_attachments.build_fixtures import _pdf as build_test_pdf

from app.hr.structured_output import extract_hr_envelope

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "hr_p0"
EXPECTED_RESUMES = {
    "resume-adjacent.md",
    "resume-invalid.txt",
    "resume-strong.md",
}
EXPECTED_RESULTS = {"panorama-result.json", "recruiting-results.json"}
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
MAINLAND_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _load_result_fixture(name: str) -> dict[str, object]:
    document = json.loads((FIXTURE_ROOT / name).read_text("utf-8"))
    assert isinstance(document, dict)
    return document


def test_resume_fixtures_are_synthetic_and_contain_no_real_contact_details() -> None:
    paths = tuple(sorted(FIXTURE_ROOT.glob("resume-*")))

    assert {path.name for path in paths} == EXPECTED_RESUMES
    for path in paths:
        text = path.read_text("utf-8")
        assert "SYNTHETIC TEST DATA" in text
        assert not EMAIL.search(text)
        assert not MAINLAND_MOBILE.search(text)


def test_result_fixtures_are_synthetic_and_all_hidden_envelopes_validate() -> None:
    assert {
        path.name for path in FIXTURE_ROOT.glob("*-result*.json")
    } == EXPECTED_RESULTS

    for filename in sorted(EXPECTED_RESULTS):
        document = _load_result_fixture(filename)
        assert document["fixture_label"] == "SYNTHETIC TEST DATA"
        results = document["results"]
        assert isinstance(results, list) and results
        for result in results:
            assert isinstance(result, dict)
            kind = result["kind"]
            markdown = result["markdown"]
            assert isinstance(kind, str)
            assert isinstance(markdown, str)
            parsed = extract_hr_envelope(markdown, kind)
            assert parsed is not None
            assert parsed.kind == kind
            assert parsed.visible_markdown.strip()


def test_deterministic_result_urls_only_use_reserved_example_com() -> None:
    urls: list[str] = []
    for filename in sorted(EXPECTED_RESULTS):
        document = _load_result_fixture(filename)
        for result in document["results"]:
            parsed = extract_hr_envelope(result["markdown"], result["kind"])
            assert parsed is not None
            urls.extend(
                value
                for value in _strings(parsed.payload)
                if value.startswith(("https://", "http://"))
            )

    assert urls
    assert all(urlsplit(url).hostname == "example.com" for url in urls)


def test_interview_artifact_helper_generates_a_pdf_fixture() -> None:
    artifact = build_test_pdf()

    assert artifact.startswith(b"%PDF-")
    assert artifact.rstrip().endswith(b"%%EOF")
