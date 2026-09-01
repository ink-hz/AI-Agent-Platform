from pathlib import Path

from app.fae_reports.contract import load_report_document
from app.fae_reports.repository import canonical_report_bytes, report_digest

FIXTURE = (
    Path(__file__).parents[2]
    / "contracts/fae-analysis-report/v1/fixtures/valid-ready.json"
)


def test_canonical_report_digest_is_deterministic():
    report = load_report_document(FIXTURE.read_bytes())
    canonical = canonical_report_bytes(report)
    assert canonical == canonical_report_bytes(report)
    assert len(report_digest(report)) == 64
    assert b"\n" not in canonical
