from __future__ import annotations

import json
import re
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

from .panorama_models import PanoramaReport, thaw_json

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FONT_NAME = "STSong-Light"


def _text(value: object, maximum: int = 32767) -> str:
    selected = _CONTROL.sub("", str(value))
    return selected[:maximum]


def _source_names(report: PanoramaReport) -> dict[object, str]:
    return {source.source_id: source.canonical_name for source in report.sources}


def _fit_columns(sheet, *, maximum: int = 48) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        width = min(
            maximum, max((len(str(cell.value or "")) for cell in column), default=8) + 2
        )
        sheet.column_dimensions[get_column_letter(index)].width = max(10, width)


def _sheet(
    workbook: Workbook,
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(tuple(_text(value) for value in row))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="185FA9")
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    _fit_columns(sheet)
    return sheet


def build_panorama_xlsx(report: PanoramaReport) -> bytes:
    names = _source_names(report)
    workbook = Workbook()
    workbook.remove(workbook.active)
    overview = workbook.create_sheet("总览")
    overview.append(("全景分析版本", report.insight.version_number))
    overview.append(("分析时间", report.insight.created_at.isoformat()))
    overview.append(
        ("覆盖公司", "、".join(source.canonical_name for source in report.sources))
    )
    overview.append(("公开岗位记录", len(report.snapshots)))
    overview.append(("摘要", _text(report.insight.summary)))
    overview.append(
        (
            "研发方向",
            _text(
                json.dumps(
                    thaw_json(report.insight.direction_clusters),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
        )
    )
    overview.column_dimensions["A"].width = 18
    overview.column_dimensions["B"].width = 90
    for cell in overview["A"]:
        cell.font = Font(bold=True, color="17345C")
    for row in overview.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    _sheet(
        workbook,
        "岗位明细",
        ("公司", "岗位", "地点", "状态", "职责", "要求", "来源", "观测时间"),
        [
            (
                names.get(item.source_id, "关注公司"),
                item.title,
                item.location,
                item.status,
                item.duty_excerpt,
                item.requirement_excerpt,
                item.source_url,
                item.observed_at.isoformat(),
            )
            for item in report.snapshots
        ],
    )
    _sheet(
        workbook,
        "情报来源",
        ("公司", "渠道地址"),
        [
            (source.canonical_name, url)
            for source in report.sources
            for url in source.approved_urls
        ],
    )
    _sheet(
        workbook,
        "公开事实",
        ("事实", "来源", "观测时间"),
        [
            (fact["text"], fact["source_url"], fact["observed_at"])
            for fact in report.insight.facts
        ],
    )
    _sheet(
        workbook,
        "AI推断",
        ("推断", "依据事实ID"),
        [
            (item["text"], "、".join(str(value) for value in item["basis_fact_ids"]))
            for item in report.insight.inferences
        ],
    )
    _sheet(
        workbook,
        "待确认",
        ("未知项",),
        [(item["text"],) for item in report.insight.unknowns],
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _paragraph(value: object, style: ParagraphStyle, maximum: int = 8000) -> Paragraph:
    return Paragraph(escape(_text(value, maximum)).replace("\n", "<br/>"), style)


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#6B7F93"))
    canvas.drawRightString(
        A4[0] - 18 * mm, 9 * mm, f"Orbbec HR Agent | 第 {document.page} 页"
    )
    canvas.restoreState()


def build_panorama_pdf(report: PanoramaReport) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=17 * mm,
        title=f"招聘情报全景分析 - 第 {report.insight.version_number} 版",
        author="Orbbec HR Agent",
    )
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "ChineseTitle",
        parent=base["Title"],
        fontName=_FONT_NAME,
        fontSize=22,
        leading=30,
        textColor=colors.HexColor("#17345C"),
        alignment=TA_CENTER,
    )
    heading = ParagraphStyle(
        "ChineseHeading",
        parent=base["Heading2"],
        fontName=_FONT_NAME,
        fontSize=15,
        leading=21,
        textColor=colors.HexColor("#185FA9"),
        spaceBefore=10,
        spaceAfter=7,
    )
    body = ParagraphStyle(
        "ChineseBody",
        parent=base["BodyText"],
        fontName=_FONT_NAME,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#203A54"),
        wordWrap="CJK",
    )
    small = ParagraphStyle(
        "ChineseSmall",
        parent=body,
        fontSize=8,
        leading=12,
        textColor=colors.HexColor("#536B84"),
    )
    names = _source_names(report)
    story = [
        _paragraph(f"招聘情报全景分析 - 第 {report.insight.version_number} 版", title),
        Spacer(1, 7 * mm),
        _paragraph(report.insight.summary, body),
        Spacer(1, 4 * mm),
        _paragraph(
            f"覆盖公司：{'、'.join(source.canonical_name for source in report.sources)}",
            small,
        ),
        _paragraph(
            f"分析时间：{report.insight.created_at.isoformat()} | 公开岗位记录：{len(report.snapshots)}",
            small,
        ),
        _paragraph("研发与业务方向", heading),
        _paragraph(
            json.dumps(
                thaw_json(report.insight.direction_clusters),
                ensure_ascii=False,
                sort_keys=True,
            ),
            body,
        ),
        _paragraph("公开事实", heading),
    ]
    for fact in report.insight.facts:
        story.extend(
            (
                _paragraph(f"- {fact['text']}", body),
                _paragraph(
                    f"来源：{fact['source_url']} | 观测：{fact['observed_at']}", small
                ),
                Spacer(1, 2 * mm),
            )
        )
    story.append(_paragraph("AI 推断", heading))
    story.extend(
        _paragraph(
            f"- {item['text']}（依据：{'、'.join(str(value) for value in item['basis_fact_ids'])}）",
            body,
        )
        for item in report.insight.inferences
    )
    story.append(_paragraph("仍待确认", heading))
    story.extend(
        _paragraph(f"- {item['text']}", body) for item in report.insight.unknowns
    )
    story.extend((PageBreak(), _paragraph("岗位明细", heading)))
    rows = [
        [
            _paragraph("公司", small),
            _paragraph("岗位与地点", small),
            _paragraph("职责与要求", small),
        ]
    ]
    for item in report.snapshots:
        rows.append(
            [
                _paragraph(names.get(item.source_id, "关注公司"), small),
                _paragraph(f"{item.title}\n{item.location} | {item.status}", small),
                _paragraph(
                    f"职责：{_text(item.duty_excerpt, 1800)}\n要求：{_text(item.requirement_excerpt, 1800)}\n来源：{item.source_url}\n观测：{item.observed_at.isoformat()}",
                    small,
                ),
            ]
        )
    jobs = Table(rows, colWidths=(29 * mm, 47 * mm, 98 * mm), repeatRows=1)
    jobs.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9F2FB")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8D6E4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(jobs)
    story.extend((Spacer(1, 5 * mm), _paragraph("情报来源矩阵", heading)))
    for source in report.sources:
        story.append(_paragraph(source.canonical_name, body))
        story.extend(_paragraph(f"- {url}", small) for url in source.approved_urls)
        story.append(Spacer(1, 3 * mm))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


__all__ = ["build_panorama_pdf", "build_panorama_xlsx"]
