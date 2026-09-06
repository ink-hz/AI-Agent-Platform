from __future__ import annotations

import json
import re
from collections.abc import Iterable
from io import BytesIO
from uuid import UUID
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
)

from .panorama_dimensions import (
    compile_panorama_dimensions,
    technical_directions,
)
from .panorama_dimensions import (
    recruitment_track as classify_recruitment_track,
)
from .panorama_models import PanoramaReport, PublicJobSnapshot, TalentSource, thaw_json

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FONT_NAME = "STSong-Light"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_DIRECTION_KEYS = {
    "算法": "algorithm",
    "光学": "optics",
    "硬件": "hardware",
    "结构": "structure",
    "软件": "software",
    "制造工艺": "manufacturing",
    "质量": "quality",
    "产品": "product",
    "供应链": "supply_chain",
    "其他": "other",
}


def _text(value: object, maximum: int = 32767) -> str:
    selected = _CONTROL.sub("", str(value))
    return selected[:maximum]


def _xlsx_text(value: object, maximum: int = 32767) -> str:
    selected = _text(value, maximum)
    return f"'{selected[: maximum - 1]}" if selected.startswith(_FORMULA_PREFIXES) else selected


def recruitment_track(item: PublicJobSnapshot) -> str:
    return classify_recruitment_track(item)


def technical_direction(item: PublicJobSnapshot) -> str:
    return _DIRECTION_KEYS[technical_directions(item)[0]]


def _direction_label(item: PublicJobSnapshot) -> str:
    return "、".join(technical_directions(item))


def _track_label(item: PublicJobSnapshot) -> str:
    return {
        "social": "社招",
        "campus": "校招",
        "intern": "实习",
        "unknown": "待分类",
    }[recruitment_track(item)]


def filter_panorama_snapshots(
    report: PanoramaReport,
    *,
    source_id: UUID | None = None,
    recruitment_track_filter: str | None = None,
    location: str | None = None,
    status: str | None = None,
    technical_direction_filter: str | None = None,
) -> tuple[PublicJobSnapshot, ...]:
    return tuple(
        item
        for item in report.snapshots
        if (source_id is None or item.source_id == source_id)
        and (
            recruitment_track_filter is None
            or recruitment_track(item) == recruitment_track_filter
        )
        and (location is None or item.location == location)
        and (status is None or item.status == status)
        and (
            technical_direction_filter is None
            or technical_direction_filter
            in {_DIRECTION_KEYS[value] for value in technical_directions(item)}
        )
    )


def _channel_snapshots(
    source: TalentSource, snapshots: Iterable[PublicJobSnapshot]
) -> dict[str, list[PublicJobSnapshot]]:
    result = {url: [] for url in source.approved_urls}
    for snapshot in snapshots:
        matches = [
            url
            for url in source.approved_urls
            if snapshot.source_url == url
            or snapshot.source_url.startswith(url.rstrip("/") + "/")
        ]
        if matches:
            result[max(matches, key=len)].append(snapshot)
    return result


def _source_names(report: PanoramaReport) -> dict[object, str]:
    return {source.source_id: source.canonical_name for source in report.sources}


def _company_direction_samples(
    snapshots: tuple[PublicJobSnapshot, ...],
) -> dict[tuple[UUID, str], tuple[str, ...]]:
    newest: dict[tuple[UUID, str], PublicJobSnapshot] = {}
    for item in snapshots:
        key = (item.source_id, item.public_job_key)
        previous = newest.get(key)
        if previous is None or (item.observed_at, str(item.snapshot_id)) > (
            previous.observed_at,
            str(previous.snapshot_id),
        ):
            newest[key] = item
    selected: dict[tuple[UUID, str], list[str]] = {}
    for item in sorted(
        newest.values(),
        key=lambda value: (
            str(value.source_id),
            value.public_job_key,
            value.observed_at,
            str(value.snapshot_id),
        ),
    ):
        for direction in technical_directions(item):
            samples = selected.setdefault((item.source_id, direction), [])
            if len(samples) < 20:
                samples.append(str(item.snapshot_id))
    return {key: tuple(values) for key, values in selected.items()}


def _scoped_content(
    report: PanoramaReport,
    snapshots: tuple[PublicJobSnapshot, ...],
    *,
    filtered: bool,
):
    if not filtered:
        return (
            report.sources,
            report.insight.facts,
            report.insight.inferences,
            report.insight.unknowns,
            report.insight.summary,
            thaw_json(report.insight.direction_clusters),
        )
    source_ids = {item.source_id for item in snapshots}
    snapshot_ids = {str(item.snapshot_id) for item in snapshots}
    sources = tuple(source for source in report.sources if source.source_id in source_ids)
    facts = tuple(
        fact
        for fact in report.insight.facts
        if str(fact["snapshot_id"]) in snapshot_ids
    )
    fact_ids = {str(fact["fact_id"]) for fact in facts}
    inferences = tuple(
        item
        for item in report.insight.inferences
        if all(str(value) in fact_ids for value in item["basis_fact_ids"])
    )
    directions: dict[str, int] = {}
    for item in snapshots:
        label = _direction_label(item)
        directions[label] = directions.get(label, 0) + 1
    return (
        sources,
        facts,
        inferences,
        (),
        f"筛选结果：{len(snapshots)} 条岗位，覆盖 {len(sources)} 家公司。",
        directions,
    )


def _fit_columns(sheet, *, maximum: int = 48) -> None:
    for index, column in enumerate(sheet.columns, start=1):
        width = min(
            maximum, max((len(str(cell.value or "")) for cell in column), default=8) + 2
        )
        sheet.column_dimensions[get_column_letter(index)].width = max(10, width)


def _coverage_rows(
    report: PanoramaReport,
    sources: tuple[TalentSource, ...],
    snapshots: tuple[PublicJobSnapshot, ...],
) -> list[tuple[object, ...]]:
    published = (
        {
            str(item["source_id"]): item
            for item in thaw_json(report.publication.source_coverage)
        }
        if report.publication is not None
        else {}
    )
    rows: list[tuple[object, ...]] = []
    for source in sources:
        coverage = published.get(str(source.source_id), {})
        channel_failures = coverage.get("channel_failures", {})
        channels = _channel_snapshots(
            source, (item for item in snapshots if item.source_id == source.source_id)
        )
        for url, items in channels.items():
            failure = (
                channel_failures.get(url)
                if isinstance(channel_failures, dict)
                else None
            )
            state = (
                "采集失败"
                if failure
                else "已形成岗位证据"
                if items
                else "已检查，本次未发现公开岗位"
                if coverage.get("state") == "succeeded"
                else "未形成岗位证据，待确认"
            )
            rows.append(
                (
                    source.canonical_name,
                    url,
                    len(items),
                    max((item.observed_at for item in items), default=None).isoformat()
                    if items
                    else coverage.get("observed_at", ""),
                    state,
                    failure or coverage.get("error_code", ""),
                )
            )
    return rows


def _sheet(
    workbook: Workbook,
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[object, ...]],
):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(tuple(_xlsx_text(value) for value in row))
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


def build_panorama_xlsx(
    report: PanoramaReport,
    snapshots: tuple[PublicJobSnapshot, ...] | None = None,
    *,
    filtered: bool = False,
) -> bytes:
    names = _source_names(report)
    selected_snapshots = report.snapshots if snapshots is None else snapshots
    sources, facts, inferences, unknowns, summary, directions = _scoped_content(
        report, selected_snapshots, filtered=filtered
    )
    dimensions = (
        compile_panorama_dimensions(selected_snapshots)
        if selected_snapshots
        else None
    )
    direction_samples = _company_direction_samples(selected_snapshots)
    workbook = Workbook()
    workbook.remove(workbook.active)
    overview = workbook.create_sheet("总览")
    overview.append(("全景分析版本", report.insight.version_number))
    overview.append(("分析时间", _xlsx_text(report.insight.created_at.isoformat())))
    if report.publication is not None:
        overview.append(("发布版本", _xlsx_text(str(report.publication.publication_id))))
        overview.append(("原始数据批次", _xlsx_text(str(report.publication.batch_id))))
        overview.append(("发布时间", _xlsx_text(report.publication.published_at.isoformat())))
        overview.append(("来源覆盖", _xlsx_text(report.publication.coverage_state)))
    overview.append(("分析模型", _xlsx_text(report.insight.model_version)))
    overview.append(
        ("覆盖公司", _xlsx_text("、".join(source.canonical_name for source in sources)))
    )
    overview.append(("公开岗位记录", len(selected_snapshots)))
    overview.append(("摘要", _xlsx_text(summary)))
    overview.append(
        (
            "研发方向",
            _xlsx_text(
                json.dumps(
                    directions,
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
        "原始岗位",
        ("公司", "岗位", "招聘类型", "技术方向", "地点", "状态", "完整职责", "完整要求", "来源", "观测时间", "内容SHA-256", "岗位快照ID", "观测ID"),
        [
            (
                names.get(item.source_id, "关注公司"),
                item.title,
                _track_label(item),
                _direction_label(item),
                item.location,
                item.status,
                item.duty_excerpt,
                item.requirement_excerpt,
                item.source_url,
                item.observed_at.isoformat(),
                item.content_sha256,
                item.snapshot_id,
                item.observation_id or item.origin_request_id,
            )
            for item in selected_snapshots
        ],
    )
    _sheet(
        workbook,
        "来源覆盖",
        ("公司", "渠道地址", "岗位命中数", "最近观测", "证据状态", "失败代码"),
        _coverage_rows(report, sources, selected_snapshots),
    )
    _sheet(
        workbook,
        "AI分析",
        ("类型", "内容", "依据事实ID", "公开来源", "观测时间"),
        [
            ("摘要", summary, "", "", report.insight.created_at.isoformat()),
            *[("公开事实", fact["text"], fact["fact_id"], fact["source_url"], fact["observed_at"]) for fact in facts],
            *[("AI推断", item["text"], "、".join(str(value) for value in item["basis_fact_ids"]), "", "") for item in inferences],
            *[("仍待确认", item["text"], "", "", "") for item in unknowns],
        ],
    )
    if dimensions is not None:
        structure_rows: list[tuple[object, ...]] = []
        for layer, values in (
            ("招聘类型", dimensions["tracks"]),
            ("技术方向", dimensions["directions"]),
            ("岗位族", dimensions["job_families"]),
            ("资历层级", dimensions["seniority"]),
            ("学历", dimensions["education"]),
            ("地点", dimensions["locations"]),
        ):
            structure_rows.extend(
                ("全部公司", layer, key, count, "")
                for key, count in values.items()
            )
        structure_rows.extend(
            ("全部公司", "显式技术栈", item["name"], item["job_count"], "")
            for item in dimensions["skills"]
        )
        for source_id, metrics in dimensions["company_matrix"].items():
            company = names.get(UUID(source_id), "关注公司")
            for direction, count in metrics["directions"].items():
                structure_rows.append(
                    (
                        company,
                        "公司 × 技术方向",
                        direction,
                        count,
                        "、".join(
                            direction_samples.get((UUID(source_id), direction), ())
                        ),
                    )
                )
        _sheet(
            workbook,
            "招聘结构",
            ("公司", "分析层", "维度", "去重岗位数", "样本岗位快照ID"),
            structure_rows,
        )
    _sheet(
        workbook,
        "证据索引",
        ("公司", "来源URL", "采集次数", "状态", "失败代码", "SHA-256", "归档定位", "类型", "字节数", "标准化岗位数", "观测时间"),
        [
            (
                names.get(item.source_id, "关注公司"), item.source_url,
                item.attempt_number, item.state, item.error_code or "",
                item.evidence_sha256 or "", item.evidence_locator or "",
                item.evidence_mime or "", item.evidence_size_bytes or 0,
                item.normalized_job_count, item.observed_at.isoformat(),
            )
            for item in report.evidence_attempts
            if item.source_id in {source.source_id for source in sources}
        ],
    )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    selected = _CONTROL.sub("", str(value))
    return Paragraph(escape(selected).replace("\n", "<br/>"), style)


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#6B7F93"))
    canvas.drawRightString(
        A4[0] - 18 * mm, 9 * mm, f"Orbbec HR Agent | 第 {document.page} 页"
    )
    canvas.restoreState()


def _representative_pdf_snapshots(
    snapshots: tuple[PublicJobSnapshot, ...], *, limit: int = 120
) -> tuple[PublicJobSnapshot, ...]:
    if len(snapshots) <= limit:
        return snapshots
    groups: dict[UUID, list[PublicJobSnapshot]] = {}
    for item in snapshots:
        groups.setdefault(item.source_id, []).append(item)
    quota = max(1, limit // len(groups))
    selected: list[PublicJobSnapshot] = []
    selected_ids: set[UUID] = set()
    ordered_groups = sorted(groups.items(), key=lambda entry: str(entry[0]))
    for _, values in ordered_groups:
        ordered = sorted(
            values,
            key=lambda item: (
                recruitment_track(item),
                technical_directions(item),
                item.title,
                item.public_job_key,
                str(item.snapshot_id),
            ),
        )
        buckets: set[tuple[str, tuple[str, ...]]] = set()
        company_selected: list[PublicJobSnapshot] = []
        for item in ordered:
            bucket = (recruitment_track(item), technical_directions(item))
            if bucket in buckets:
                continue
            buckets.add(bucket)
            company_selected.append(item)
            if len(company_selected) == quota:
                break
        if len(company_selected) < quota:
            chosen = {item.snapshot_id for item in company_selected}
            company_selected.extend(
                item
                for item in ordered
                if item.snapshot_id not in chosen
            )
        for item in company_selected[:quota]:
            selected.append(item)
            selected_ids.add(item.snapshot_id)
    if len(selected) < limit:
        selected.extend(
            item
            for item in sorted(
                snapshots,
                key=lambda value: (
                    str(value.source_id),
                    value.title,
                    value.public_job_key,
                    str(value.snapshot_id),
                ),
            )
            if item.snapshot_id not in selected_ids
        )
    return tuple(selected[:limit])


def build_panorama_pdf(
    report: PanoramaReport,
    snapshots: tuple[PublicJobSnapshot, ...] | None = None,
    *,
    filtered: bool = False,
) -> bytes:
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
    selected_snapshots = report.snapshots if snapshots is None else snapshots
    sources, facts, inferences, unknowns, summary, directions = _scoped_content(
        report, selected_snapshots, filtered=filtered
    )
    dimensions = (
        compile_panorama_dimensions(selected_snapshots)
        if selected_snapshots
        else None
    )
    appendix_snapshots = _representative_pdf_snapshots(selected_snapshots)
    story = [
        _paragraph(f"招聘情报全景分析 - 第 {report.insight.version_number} 版", title),
        Spacer(1, 7 * mm),
        _paragraph(summary, body),
        Spacer(1, 4 * mm),
        _paragraph(
            f"覆盖公司：{'、'.join(source.canonical_name for source in sources) or '无匹配公司'}",
            small,
        ),
        _paragraph(
            f"分析时间：{report.insight.created_at.isoformat()} | 公开岗位记录：{len(selected_snapshots)}",
            small,
        ),
        _paragraph("AI 分析", heading),
        _paragraph("研发与业务方向", body),
        _paragraph(
            json.dumps(
                directions,
                ensure_ascii=False,
                sort_keys=True,
            ),
            body,
        ),
        *(
            [
                _paragraph("代码统计的招聘结构", heading),
                _paragraph(
                    "招聘类型：" + json.dumps(dimensions["tracks"], ensure_ascii=False),
                    body,
                ),
                _paragraph(
                    "岗位族：" + json.dumps(dimensions["job_families"], ensure_ascii=False),
                    body,
                ),
                _paragraph(
                    "资历层级：" + json.dumps(dimensions["seniority"], ensure_ascii=False),
                    body,
                ),
                _paragraph(dimensions["trend"]["message"], body),
                _paragraph("；".join(dimensions["interpretation_limits"]), small),
            ]
            if dimensions is not None
            else []
        ),
        _paragraph("公开事实（可核验）", heading),
    ]
    for fact in facts:
        story.extend(
            (
                _paragraph(f"- {fact['text']}", body),
                _paragraph(
                    f"来源：{fact['source_url']} | 观测：{fact['observed_at']}", small
                ),
                Spacer(1, 2 * mm),
            )
        )
    story.append(_paragraph("AI 推断（非原始事实）", heading))
    story.extend(
        _paragraph(
            f"- {item['text']}（依据：{'、'.join(str(value) for value in item['basis_fact_ids'])}）",
            body,
        )
        for item in inferences
    )
    story.append(_paragraph("仍待确认", heading))
    story.extend(
        _paragraph(f"- {item['text']}", body) for item in unknowns
    )
    story.extend((PageBreak(), _paragraph("原始岗位数据附录", heading)))
    if not selected_snapshots:
        story.append(_paragraph("当前筛选条件下没有岗位记录。", body))
    elif len(appendix_snapshots) < len(selected_snapshots):
        story.append(
            _paragraph(
                f"PDF 展示 {len(appendix_snapshots)} 个代表岗位；完整 "
                f"{len(selected_snapshots)} 条原始岗位请下载 Excel。代表岗位按公司、"
                "招聘类型与技术方向分层选取。",
                body,
            )
        )
    for item in appendix_snapshots:
        story.extend(
            (
                _paragraph(
                    f"{names.get(item.source_id, '关注公司')} | {item.title}", body
                ),
                _paragraph(
                    f"{_track_label(item)} | {_direction_label(item)} | {item.location} | {item.status}",
                    small,
                ),
                _paragraph(f"职责：{item.duty_excerpt}", small),
                _paragraph(f"要求：{item.requirement_excerpt}", small),
                _paragraph(
                    f"来源：{item.source_url} | 观测：{item.observed_at.isoformat()}",
                    small,
                ),
                Spacer(1, 3 * mm),
            )
        )
    story.extend((Spacer(1, 5 * mm), _paragraph("来源覆盖", heading)))
    for company, url, count, observed_at, state, error_code in _coverage_rows(
        report, sources, selected_snapshots
    ):
        story.append(
            _paragraph(f"{company} | {state} | 岗位命中数：{count}", body)
        )
        story.append(_paragraph(f"来源：{url}", small))
        if observed_at:
            story.append(_paragraph(f"最近观测：{observed_at}", small))
        if error_code:
            story.append(_paragraph(f"失败代码：{error_code}", small))
        story.append(Spacer(1, 3 * mm))
    story.append(_paragraph("原始来源证据索引", heading))
    for item in report.evidence_attempts:
        if item.source_id not in {source.source_id for source in sources}:
            continue
        story.extend(
            (
                _paragraph(
                    f"{names.get(item.source_id, '关注公司')} | {item.source_url}",
                    body,
                ),
                _paragraph(
                    f"SHA-256: {item.evidence_sha256 or '无'} | "
                    f"{item.evidence_mime or '未知类型'} | "
                    f"{item.evidence_size_bytes or 0} bytes | "
                    f"观测：{item.observed_at.isoformat()}",
                    small,
                ),
                Spacer(1, 2 * mm),
            )
        )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


__all__ = [
    "build_panorama_pdf",
    "build_panorama_xlsx",
    "filter_panorama_snapshots",
    "recruitment_track",
    "technical_direction",
]
