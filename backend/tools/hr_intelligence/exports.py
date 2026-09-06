from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from io import BytesIO
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_FONT_NAME = "STSong-Light"
_SHEETS = ("原始岗位", "AI分析", "来源覆盖", "证据索引", "成本记录")


def _text(value: object, maximum: int = 32767) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        selected = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        selected = str(value)
    return _CONTROL.sub("", selected)[:maximum]


def _xlsx_text(value: object) -> str:
    selected = _text(value)
    if selected.startswith(_FORMULA_PREFIXES):
        return "'" + selected[:32766]
    return selected


def _rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def build_markdown(report: Mapping[str, object]) -> bytes:
    jobs = _rows(report.get("jobs"))
    coverage = _rows(report.get("coverage"))
    analyses = _rows(report.get("analysis"))
    usage = _rows(report.get("usage"))
    evidence = _rows(report.get("evidence"))
    lines = [
        "# HR 招聘全景情报",
        "",
        f"- Bundle ID：`{_text(report.get('bundle_id'))}`",
        f"- 生成时间：{_text(report.get('generated_at'))}",
        f"- 原始岗位数：{len(jobs)}",
        "",
        "## 来源覆盖",
        "",
    ]
    lines.extend(
        f"- {_text(item.get('company_key'))}: {_text(item.get('state'))}"
        for item in coverage
    )
    lines.extend(["", "## 确定性聚合", "", "```json"])
    lines.append(
        json.dumps(
            report.get("aggregates", {}),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    lines.extend(["```", "", "## AI 分析", ""])
    if analyses:
        for item in analyses:
            response = item.get("response", item)
            if isinstance(response, Mapping):
                lines.extend(
                    [
                        f"### {_text(item.get('scope_key', item.get('kind', '分析'))) or '分析'}",
                        "",
                        _text(response.get("summary")) or "未提供总结",
                        "",
                        "证据化事实：",
                    ]
                )
                for fact in _rows(response.get("facts")):
                    lines.append(
                        "- "
                        f"{_text(fact.get('text'))} "
                        f"([来源]({_text(fact.get('source_url'))})；"
                        f"SHA-256 `{_text(fact.get('evidence_sha256'))}`)"
                    )
                unknowns = response.get("unknowns")
                if isinstance(unknowns, Sequence) and not isinstance(
                    unknowns, (str, bytes)
                ):
                    lines.extend(["", "未知与限制："])
                    lines.extend(f"- {_text(value)}" for value in unknowns)
                lines.append("")
    else:
        lines.extend(["尚无已接受的 AI 分析。", ""])
    lines.extend(["## 原始岗位", ""])
    for item in jobs:
        lines.append(
            "- "
            f"{_text(item.get('company_key'))}｜{_text(item.get('title'))}｜"
            f"{_text(item.get('location'))}｜[原始来源]({_text(item.get('source_url'))})｜"
            f"`{_text(item.get('evidence_sha256'))}`"
        )
    lines.extend(["", "## 证据索引", ""])
    lines.extend(
        f"- `{_text(item.get('sha256'))}`｜{_text(item.get('source_url'))}"
        for item in evidence
    )
    lines.extend(["", "## 成本记录", "", "```json"])
    lines.append(json.dumps(usage, ensure_ascii=False, sort_keys=True, indent=2))
    lines.extend(["```", ""])
    return "\n".join(lines).encode("utf-8")


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_text(value)).replace("\n", "<br/>"), style)


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawCentredString(A4[0] / 2, 10 * mm, f"{document.page}")
    canvas.restoreState()


def build_pdf(report: Mapping[str, object]) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="HR 招聘全景情报",
    )
    sample = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN",
        parent=sample["Title"],
        fontName=_FONT_NAME,
        fontSize=20,
        leading=28,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#102A43"),
    )
    heading = ParagraphStyle(
        "HeadingCN",
        parent=sample["Heading2"],
        fontName=_FONT_NAME,
        fontSize=14,
        leading=20,
        spaceBefore=8,
        spaceAfter=6,
        textColor=colors.HexColor("#0F766E"),
    )
    body = ParagraphStyle(
        "BodyCN",
        parent=sample["BodyText"],
        fontName=_FONT_NAME,
        fontSize=9,
        leading=14,
        textColor=colors.HexColor("#263238"),
    )
    story = [
        _paragraph("HR 招聘全景情报", title),
        Spacer(1, 5 * mm),
        _paragraph(f"Bundle ID：{_text(report.get('bundle_id'))}", body),
        _paragraph(f"生成时间：{_text(report.get('generated_at'))}", body),
        Spacer(1, 4 * mm),
        _paragraph("来源覆盖", heading),
    ]
    coverage = _rows(report.get("coverage"))
    if coverage:
        data = [[_paragraph("公司", body), _paragraph("状态", body)]]
        data.extend(
            [
                _paragraph(item.get("company_key"), body),
                _paragraph(item.get("state"), body),
            ]
            for item in coverage
        )
        table = Table(data, colWidths=(90 * mm, 70 * mm), repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DFF5F2")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    story.extend([Spacer(1, 4 * mm), _paragraph("AI 分析", heading)])
    analyses = _rows(report.get("analysis"))
    if not analyses:
        story.append(_paragraph("尚无已接受的 AI 分析。", body))
    for item in analyses:
        response = item.get("response", item)
        if isinstance(response, Mapping):
            story.append(
                _paragraph(item.get("scope_key", item.get("kind", "分析")), heading)
            )
            story.append(_paragraph(response.get("summary", ""), body))
            for fact in _rows(response.get("facts")):
                story.append(
                    _paragraph(
                        f"• {fact.get('text', '')}｜{fact.get('source_url', '')}｜"
                        f"SHA-256 {fact.get('evidence_sha256', '')}",
                        body,
                    )
                )
    story.extend([PageBreak(), _paragraph("原始岗位与证据", heading)])
    for item in _rows(report.get("jobs")):
        story.append(
            _paragraph(
                f"{item.get('company_key', '')}｜{item.get('title', '')}｜"
                f"{item.get('location', '')}｜{item.get('source_url', '')}｜"
                f"SHA-256 {item.get('evidence_sha256', '')}",
                body,
            )
        )
    story.extend([Spacer(1, 4 * mm), _paragraph("成本记录", heading)])
    story.append(_paragraph(report.get("usage", []), body))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


def _fit_columns(sheet, maximum: int = 56) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        width = min(
            max((len(_text(cell.value)) for cell in column), default=8) + 2, maximum
        )
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_sheet(
    sheet, rows: list[Mapping[str, object]], headers: tuple[str, ...]
) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F766E")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in rows:
        sheet.append([_xlsx_text(row.get(header)) for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    _fit_columns(sheet)


def build_xlsx(report: Mapping[str, object]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    jobs = _rows(report.get("jobs"))
    analysis = _rows(report.get("analysis"))
    coverage = _rows(report.get("coverage"))
    evidence = _rows(report.get("evidence"))
    usage = _rows(report.get("usage"))
    specifications = (
        (
            "原始岗位",
            jobs,
            (
                "company_key",
                "title",
                "location",
                "duty_excerpt",
                "requirement_excerpt",
                "source_url",
                "evidence_sha256",
                "observed_at",
                "status",
            ),
        ),
        (
            "AI分析",
            analysis,
            ("kind", "scope_key", "input_sha256", "response_sha256", "response"),
        ),
        (
            "来源覆盖",
            coverage,
            ("company_key", "state", "observed_at", "job_count", "limitations"),
        ),
        (
            "证据索引",
            evidence,
            ("sha256", "source_url", "mime", "size_bytes", "locator"),
        ),
        (
            "成本记录",
            usage,
            (
                "unit_id",
                "provider",
                "model",
                "input_tokens",
                "output_tokens",
                "estimated_cost",
                "unavailable_reason",
            ),
        ),
    )
    for name, rows, headers in specifications:
        _write_sheet(workbook.create_sheet(name), rows, headers)
    workbook.properties.title = "HR 招聘全景情报"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


__all__ = ["build_markdown", "build_pdf", "build_xlsx"]
