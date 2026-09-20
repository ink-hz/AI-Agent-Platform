from __future__ import annotations

import hashlib
import html
import io
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1920
HEIGHT = 1080

PANORAMA: dict[str, Any] = {
    "version": "2026-09-20.v1",
    "updated_at": "2026-09-20",
    "title": "奥比中光 AI 工程全景",
    "context": {
        "period": "2026 年 1—6 月",
        "metrics": [
            {"label": "营业收入", "value": "4.38 亿元｜同比 +0.49%"},
            {"label": "研发投入", "value": "1.11 亿元｜同比 +22.17%"},
            {"label": "归母净利润", "value": "0.42 亿元｜同比 -30.82%"},
        ],
        "observation": "收入基本持平、研发加码、利润下降。经营数字不等于 AI 成效。",
        "judgment": "项目判断：效率与质量优先；不将利润变化归因于研发或 AI。",
        "source_ids": ["FIN-H1-2026-P9", "FIN-H1-2026-P32"],
    },
    "revenue": {
        "period": "2025 年主营业务收入（年报 p.49）",
        "denominator_cents": 93_504_482_249,
        "segments": [
            {"id": "consumer", "label": "消费级应用设备", "amount_cents": 58_372_580_971},
            {"id": "sensor", "label": "3D 视觉传感器", "amount_cents": 29_093_471_169},
            {"id": "industrial", "label": "工业级应用设备", "amount_cents": 2_554_096_095},
            {"id": "other", "label": "其他", "amount_cents": 3_484_334_014},
        ],
        "note": "四项按同一分母原金额计算；工业级毛利率 66.79%，不作为收入占比或投资优先级。",
        "source_ids": ["FIN-2025-P49"],
    },
    "domains": [
        {
            "id": "market", "title": "市场与客户", "subtitle": "客户形态与反馈入口",
            "items": ["机器人", "三维扫描", "生物识别", "AIoT", "工业三维测量"],
            "detail": ["官网方案分类不是收入结构或内部事业部。", "VOC 已有草稿、确认与查询能力；最新发布证据仍待补齐。"],
            "status": "VOC 历史运行｜健康未核｜成效基线待补", "source_ids": ["PROD-SOLUTIONS", "ASSET-VOC"],
            "related_ids": ["products", "delivery"], "actions": ["voc"],
        },
        {
            "id": "products", "title": "产品与交付", "subtitle": "从芯片到软件与系统",
            "items": ["LS635 芯片", "Gemini／Femto／Astra 相机", "行业整机", "工业测量系统", "Orbbec SDK"],
            "detail": ["产品族覆盖芯片、视觉组件、终端、测量系统与软件。", "型号、固件、SDK、主机和系统需按组合核实。"],
            "status": "公开产品底稿已核；生命周期与责任待确认", "source_ids": ["PROD-INVENTORY"],
            "related_ids": ["technology", "supply", "delivery"], "actions": ["notes", "fae"],
        },
        {
            "id": "technology", "title": "核心技术", "subtitle": "深度引擎、光学、算法与 SDK",
            "items": ["自研深度引擎／ASIC", "结构光／双目／iToF／dToF", "光学与标定", "深度算法", "固件／SDK"],
            "detail": ["不同技术维度不能从单一型号外推。", "FAE 产品证据可作为兼容矩阵的起点。"],
            "status": "能力线索已核；内部版本与责任待补", "source_ids": ["PROD-TECH", "ASSET-FAE"],
            "related_ids": ["products", "delivery"], "actions": ["fae", "notes"],
        },
        {
            "id": "supply", "title": "供应与生产", "subtitle": "制造、装配、标定与测试",
            "items": ["芯片委外制造", "定制光学料件", "模组组装", "标定与量产", "成品测试"],
            "detail": ["供应、制造与系统边界需内部资料确认。", "未取得实际系统、责任与质量基线。"],
            "status": "资料缺口｜不表示没有业务", "source_ids": ["PAN-DOMAINS"],
            "related_ids": ["products", "delivery", "quality"], "actions": [],
        },
        {
            "id": "delivery", "title": "交付与服务", "subtitle": "设计导入到问题闭环",
            "items": ["设计导入", "系统集成", "测量验收", "技术支持", "问题反馈"],
            "detail": ["FAE 回答、VOC 入库和客户问题解决是不同结果。", "现有证据不证明两套系统已形成完整闭环。"],
            "status": "FAE／VOC 历史运行｜健康未核｜成效基线待补", "source_ids": ["ASSET-FAE", "ASSET-VOC"],
            "related_ids": ["market", "products", "technology"], "actions": ["fae", "voc"],
        },
    ],
    "support": [
        {"id": "hr", "title": "人才／HR", "subtitle": "岗位与人才工作流", "items": ["岗位", "资源", "候选批次", "面试记录"], "detail": ["已有历史部署与任务记录；不等于成功交付。"], "status": "历史运行｜健康未核｜成效基线待补", "source_ids": ["ASSET-HR"], "related_ids": ["organization"], "actions": ["hr"]},
        {"id": "office", "title": "行政／ADMIN", "subtitle": "问答与事务流程", "items": ["制度问答", "班车", "住宿", "车辆", "通知"], "detail": ["AI 问答与事务流程分别记账。"], "status": "历史运行｜健康未核｜成效基线待补", "source_ids": ["ASSET-ADMIN"], "related_ids": ["organization"], "actions": ["office"]},
        {"id": "finance", "title": "财务", "subtitle": "口径、成本与收益核定", "items": ["经营口径", "投入成本", "收益归因"], "detail": ["经营数据不是 AI 成效或可支配预算。"], "status": "公开经营口径已核；内部系统待确认", "source_ids": ["FIN-2025-P49", "PAN-DOMAINS"], "related_ids": ["quality"], "actions": []},
        {"id": "quality", "title": "质量", "subtitle": "基线、复核与结果", "items": ["验收规范", "问题复核", "效果基线"], "detail": ["建设阶段、运行状态和业务成效分别表达。"], "status": "业务效果基线未建立", "source_ids": ["ASSET-FAE", "PAN-DOMAINS"], "related_ids": ["technology", "supply", "delivery"], "actions": ["review"]},
        {"id": "legal", "title": "法务", "subtitle": "数据用途与授权边界", "items": ["资料用途", "保存范围", "授权记录"], "detail": ["未取得内部职责与系统授权材料。"], "status": "待确认", "source_ids": ["ORG-SCOPE"], "related_ids": ["organization"], "actions": ["governance"]},
        {"id": "organization", "title": "组织与制度", "subtitle": "部门树、职责与流程依据", "items": ["部门级快照", "职责文件", "流程制度"], "detail": ["首轮只需部门标识、名称、父级及快照元数据，不读取个人字段。"], "status": "读取用途与展示范围待确认", "source_ids": ["ORG-SCOPE"], "related_ids": ["hr", "office", "legal"], "actions": ["identity", "access"]},
    ],
    "shared": {
        "title": "共用能力",
        "actions": ["brain", "agents", "missions", "sessions", "operations", "review", "activity", "identity", "governance", "access", "account", "agent-admin", "notes"],
        "status": "复用现有身份、任务、会话、运行与治理边界",
    },
    "asks": [
        {"owner": "产品／研发", "request": "指定一条代表产品链的业务牵头人，确认问题范围与职责依据。"},
        {"owner": "平台／HR", "request": "确认一次只读部门快照的用途、字段、保存及展示范围；职责映射另需正式材料和业务确认。"},
        {"owner": "IT／产品／财务", "request": "确认内部系统清单、责任方及可提供的数据范围；盘点配合不等同接口授权。"},
    ],
    "sources": [
        {"id": "FIN-H1-2026-P9", "label": "2026 半年报 p.9", "document": "finance"},
        {"id": "FIN-H1-2026-P32", "label": "2026 半年报 p.32", "document": "finance"},
        {"id": "FIN-2025-P49", "label": "2025 年报 p.49", "document": "finance"},
        {"id": "PROD-INVENTORY", "label": "产品族清单 §1、3", "document": "products"},
        {"id": "PROD-SOLUTIONS", "label": "产品族清单 §4.2", "document": "products"},
        {"id": "PROD-TECH", "label": "产品族清单 §4.1", "document": "products"},
        {"id": "PAN-DOMAINS", "label": "全景领域卡", "document": "domains"},
        {"id": "ASSET-HR", "label": "已有 AI 资产 §3.2", "document": "assets"},
        {"id": "ASSET-ADMIN", "label": "已有 AI 资产 §3.3", "document": "assets"},
        {"id": "ASSET-FAE", "label": "已有 AI 资产 §3.4", "document": "assets"},
        {"id": "ASSET-VOC", "label": "已有 AI 资产 §3.5", "document": "assets"},
        {"id": "ORG-SCOPE", "label": "组织取证范围", "document": "overview"},
    ],
}


def content_hash() -> str:
    return hashlib.sha256(json.dumps(PANORAMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _percent(amount: int) -> str:
    return f"{amount / PANORAMA['revenue']['denominator_cents'] * 100:.2f}%"


@dataclass(frozen=True)
class ExportPrimitive:
    kind: str
    x: int
    y: int
    width: int = 0
    height: int = 0
    fill: str = ""
    stroke: str = ""
    radius: int = 0
    text: str = ""
    size: int = 14
    bold: bool = False
    color: str = "#10233d"
    wrap_units: int = 0
    align: str = "left"


def export_layout(generated_at: str | None = None) -> tuple[ExportPrimitive, ...]:
    """Build the single presentation model consumed by both export formats."""
    generated_at = generated_at or _generated_at()
    items: list[ExportPrimitive] = []

    def rect(x: int, y: int, width: int, height: int, fill: str, *, stroke: str = "", radius: int = 0) -> None:
        items.append(ExportPrimitive("rect", x, y, width, height, fill, stroke, radius))

    def text(x: int, y: int, value: str, *, size: int = 14, bold: bool = False,
             color: str = "#10233d", wrap_units: int = 0,
             align: str = "left") -> None:
        items.append(ExportPrimitive("text", x, y, text=value, size=size, bold=bold,
                                     color=color, wrap_units=wrap_units, align=align))

    rect(0, 0, WIDTH, HEIGHT, "#f5f8fb")
    text(60, 32, "AI ENGINEERING PANORAMA", size=11, bold=True, color="#1767d2")
    text(60, 49, PANORAMA["title"], size=34, bold=True)
    text(60, 89, PANORAMA["context"]["observation"], size=14, color="#63748b")
    text(1860, 38, f"内容版本 {PANORAMA['version']}", size=12, color="#63748b", align="right")
    text(1860, 59, f"数据时间 {PANORAMA['updated_at']}", size=12, color="#63748b", align="right")
    text(1860, 80, f"生成时间 {generated_at}", size=12, color="#63748b", align="right")

    rect(60, 115, 710, 190, "#ffffff", stroke="#dbe6ee", radius=14)
    text(82, 133, "01", size=11, bold=True, color="#1767d2")
    text(118, 130, "为什么现在做 AI", size=18, bold=True)
    text(118, 153, PANORAMA["context"]["period"], size=12, color="#63748b")
    for index, metric in enumerate(PANORAMA["context"]["metrics"]):
        metric_x = 82 + index * 220
        rect(metric_x, 181, 3, 50, "#2e93c3")
        text(metric_x + 11, 181, metric["value"], size=16, bold=True, wrap_units=20)
        text(metric_x + 11, 220, metric["label"], size=11, color="#63748b")
    rect(82, 250, 666, 38, "#edf8f7", radius=8)
    text(94, 258, PANORAMA["context"]["judgment"], size=12, bold=True,
         color="#087379", wrap_units=78)

    rect(790, 115, 1070, 190, "#ffffff", stroke="#dbe6ee", radius=14)
    text(812, 133, "02", size=11, bold=True, color="#1767d2")
    text(848, 130, "收入构成", size=18, bold=True)
    text(848, 153, PANORAMA["revenue"]["period"] + " · 同尺度", size=12, color="#63748b")
    x = 812
    colors = ("#1767d2", "#07999a", "#5ac4b1", "#93a8bb")
    for segment, color in zip(PANORAMA["revenue"]["segments"], colors, strict=True):
        width = round(1026 * segment["amount_cents"] / PANORAMA["revenue"]["denominator_cents"])
        rect(x, 178, width, 26, color)
        x += width
    for index, (segment, color) in enumerate(zip(PANORAMA["revenue"]["segments"], colors, strict=True)):
        col_x = 812 + (index % 2) * 510
        row_y = 215 + (index // 2) * 23
        rect(col_x, row_y + 4, 9, 9, color, radius=2)
        text(col_x + 17, row_y, f"{segment['label']}  {_percent(segment['amount_cents'])}", size=12, bold=True)
    text(812, 261, f"主营业务收入分母 {PANORAMA['revenue']['denominator_cents'] / 100:,.2f} 元｜" + PANORAMA["revenue"]["note"],
         size=11, color="#63748b", wrap_units=94)

    text(60, 324, "03", size=11, bold=True, color="#1767d2")
    text(96, 319, "业务价值链与 AI 覆盖", size=19, bold=True)
    text(330, 325, "五类为分析归组；建设、运行与业务效果分别表达", size=12, color="#63748b")
    card_width = 344
    for index, domain in enumerate(PANORAMA["domains"]):
        card_x = 60 + index * 360
        rect(card_x, 353, card_width, 274, "#ffffff", stroke="#dbe6ee", radius=10)
        text(card_x + 16, 370, domain["title"], size=18, bold=True)
        text(card_x + 16, 397, domain["subtitle"], size=11, color="#63748b", wrap_units=36)
        for line_index, value in enumerate(domain["items"]):
            rect(card_x + 16, 427 + line_index * 27, 312, 22, "#edf2f6", radius=5)
            text(card_x + 24, 430 + line_index * 27, value, size=12, color="#405b75", wrap_units=30)
        text(card_x + 16, 569, domain["status"], size=11, bold=True,
             color="#08777d", wrap_units=31)

    text(60, 645, "管理支撑", size=15, bold=True)
    for index, support in enumerate(PANORAMA["support"]):
        card_x = 60 + index * 300
        rect(card_x, 669, 284, 94, "#ffffff", stroke="#dbe6ee", radius=10)
        text(card_x + 13, 681, support["title"], size=14, bold=True)
        text(card_x + 13, 704, " · ".join(support["items"]), size=10, color="#405b75", wrap_units=31)
        text(card_x + 13, 728, support["status"], size=10, bold=True,
             color="#08777d", wrap_units=31)
    rect(60, 775, 1800, 48, "#12365b", radius=10)
    text(80, 787, "PLATFORM LAYER  ·  " + PANORAMA["shared"]["title"], size=15,
         bold=True, color="#ffffff")
    text(530, 789, PANORAMA["shared"]["status"], size=12, color="#bcd4df")

    rect(60, 837, 1800, 112, "#ffffff", stroke="#dbe6ee", radius=14)
    text(80, 853, "04  请管理层协调", size=17, bold=True)
    text(270, 857, "提议 · 尚未获批", size=11, color="#63748b")
    for index, ask in enumerate(PANORAMA["asks"]):
        ask_x = 80 + index * 588
        rect(ask_x, 884, 3, 48, "#58b9b2")
        text(ask_x + 12, 882, ask["owner"], size=11, bold=True, color="#08777d")
        text(ask_x + 12, 902, ask["request"], size=11, wrap_units=51)

    text(60, 964, "来源编号与页面映射", size=12, bold=True, color="#405b75")
    for index, source in enumerate(PANORAMA["sources"]):
        col_x = 60 + (index % 3) * 600
        row_y = 986 + (index // 3) * 18
        text(col_x, row_y, f"{source['id']}｜{source['label']} → {source['document']}",
             size=10, color="#63748b", wrap_units=68)
    text(60, 1060, "内容 SHA-256 " + content_hash(), size=9, color="#7b8b9c")
    return tuple(items)


def _wrap(value: str, max_units: int) -> list[str]:
    if not max_units:
        return [value]
    lines: list[str] = []
    current = ""
    units = 0.0
    for character in value:
        weight = 1.0 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 0.55
        if current and units + weight > max_units:
            lines.append(current)
            current, units = "", 0.0
        current += character
        units += weight
    if current:
        lines.append(current)
    return lines


def render_svg() -> bytes:
    blocks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<style>text{font-family:"Noto Sans CJK SC",system-ui,sans-serif}</style>',
    ]
    for item in export_layout():
        if item.kind == "rect":
            stroke = f' stroke="{item.stroke}" stroke-width="1"' if item.stroke else ""
            blocks.append(f'<rect x="{item.x}" y="{item.y}" width="{item.width}" height="{item.height}" fill="{item.fill}" rx="{item.radius}"{stroke}/>')
            continue
        lines = _wrap(item.text, item.wrap_units)
        weight = "700" if item.bold else "400"
        anchor = ' text-anchor="end"' if item.align == "right" else ""
        blocks.append(f'<text aria-label="{html.escape(item.text, quote=True)}" x="{item.x}" y="{item.y + item.size}" font-size="{item.size}" font-weight="{weight}" fill="{item.color}"{anchor}>')
        for index, line in enumerate(lines):
            dy = "0" if index == 0 else str(round(item.size * 1.25))
            blocks.append(f'<tspan x="{item.x}" dy="{dy}">{html.escape(line)}</tspan>')
        blocks.append('</text>')
    blocks.append('</svg>')
    return "".join(blocks).encode()


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("PNG export requires a CJK font (install fonts-noto-cjk)")


def render_png() -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f5f8fb")
    draw = ImageDraw.Draw(image)
    for item in export_layout():
        if item.kind == "rect":
            draw.rounded_rectangle(
                (item.x, item.y, item.x + item.width, item.y + item.height),
                radius=item.radius, fill=item.fill, outline=item.stroke or None, width=1,
            )
            continue
        lines = _wrap(item.text, item.wrap_units)
        font = _font(item.size, item.bold)
        x = item.x
        if item.align == "right":
            x -= max(draw.textlength(line, font=font) for line in lines)
        draw.multiline_text(
            (x, item.y), "\n".join(lines), font=font,
            fill=item.color, spacing=round(item.size * .25),
        )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
