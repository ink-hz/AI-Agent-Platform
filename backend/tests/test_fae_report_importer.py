import json
from pathlib import Path

import pytest
from app.fae_reports.importer import FaeReportImporter, ReportImportError

FIXTURE = (
    Path(__file__).parents[2]
    / "contracts/fae-analysis-report/v1/fixtures/valid-ready.json"
)


class RecordingRepository:
    def __init__(self):
        self.calls = []

    def import_report(self, report, payload, *, digest, actor):
        self.calls.append((report, payload, digest, actor))
        return {
            "report_pk": "00000000-0000-0000-0000-000000000001",
            "result": "imported",
        }


def test_importer_accepts_private_regular_contract_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(FIXTURE.read_bytes())
    path.chmod(0o600)
    repository = RecordingRepository()

    result = FaeReportImporter(repository).import_path(
        path.resolve(), actor="service:fae-report-importer"
    )

    assert result.report_id == "fae-weekly-2026-w35"
    assert result.report_version == 1
    assert result.result == "imported"
    assert len(result.payload_digest) == 64
    assert repository.calls[0][3] == "service:fae-report-importer"


def test_importer_rejects_symlink_and_untrusted_actor(tmp_path):
    target = tmp_path / "report.json"
    target.write_bytes(FIXTURE.read_bytes())
    target.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(target)

    importer = FaeReportImporter(RecordingRepository())
    with pytest.raises(ReportImportError, match="report_path_invalid"):
        importer.import_path(link, actor="service:fae-report-importer")
    with pytest.raises(ReportImportError, match="report_actor_invalid"):
        importer.import_path(target.resolve(), actor="root")


def test_importer_digest_is_jcs_stable(tmp_path):
    original = json.loads(FIXTURE.read_text(encoding="utf-8"))
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    second.write_text(
        json.dumps(original, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    first.chmod(0o600)
    second.chmod(0o600)

    repository = RecordingRepository()
    importer = FaeReportImporter(repository)
    left = importer.import_path(first.resolve(), actor="service:fae-report-importer")
    right = importer.import_path(second.resolve(), actor="service:fae-report-importer")
    assert left.payload_digest == right.payload_digest
