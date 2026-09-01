from datetime import UTC, datetime
from pathlib import Path

from app.fae_reports.contract import load_report_document
from app.fae_reports.repository import StoredReport
from app.fae_reports.service import FaeReportService

FIXTURE = Path(__file__).parents[2] / "contracts/fae-analysis-report/v1/fixtures/valid-ready.json"


class Repository:
    def __init__(self):
        report = load_report_document(FIXTURE.read_bytes())
        self.value = StoredReport(
            report_pk="pk",
            report=report,
            payload_digest="a" * 64,
            imported_at=datetime(2026, 8, 31, 9, tzinfo=UTC),
            active_issue_links={},
        )

    def list_reports(self, status=None):
        return [self.value] if status in {None, "ready"} else []

    def get_report(self, report_id, version=None):
        return self.value if report_id == self.value.report.report_id else None


def test_report_service_exposes_structured_four_dimension_report():
    detail = FaeReportService(Repository()).detail("fae-weekly-2026-w35", 1)

    assert detail["status"] == "ready"
    assert {metric["dimension"] for metric in detail["metrics"]} == {
        "usage", "business_value", "answer_effectiveness", "insights_improvement"
    }
    assert detail["publication"]["payload_digest"] == "a" * 64
    assert detail["currentness"] == "current"


def test_latest_returns_none_without_fabricating_data():
    repository = Repository()
    repository.list_reports = lambda status=None: []
    assert FaeReportService(repository).latest() is None

