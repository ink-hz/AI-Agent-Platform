from io import BytesIO

from openpyxl import load_workbook

from tools.hr_intelligence.exports import build_markdown, build_pdf, build_xlsx


def _report_input():
    return {
        "bundle_id": "00000000-0000-4000-8000-000000000001",
        "generated_at": "2026-09-06T08:00:00+00:00",
        "jobs": [
            {
                "company_key": "hesai",
                "title": "算法工程师",
                "location": "上海",
                "source_url": "https://example.com/jobs/1",
                "evidence_sha256": "a" * 64,
            }
        ],
        "coverage": [{"company_key": "hesai", "state": "succeeded"}],
        "analysis": [],
        "usage": [],
        "aggregates": {"tracks": {"social": 1}},
        "evidence": [{"sha256": "a" * 64, "source_url": "https://example.com/jobs"}],
    }


def test_exports_preserve_raw_jobs_analysis_sources_and_cost_layers() -> None:
    report_input = _report_input()

    markdown = build_markdown(report_input)
    pdf = build_pdf(report_input)
    xlsx = build_xlsx(report_input)
    workbook = load_workbook(BytesIO(xlsx), read_only=True)

    markdown_text = markdown.decode("utf-8")
    assert "原始岗位" in markdown_text
    assert "AI 分析" in markdown_text
    assert "来源覆盖" in markdown_text
    assert pdf.startswith(b"%PDF-")
    assert workbook.sheetnames == [
        "原始岗位",
        "AI分析",
        "来源覆盖",
        "证据索引",
        "成本记录",
    ]
