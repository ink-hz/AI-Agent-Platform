from __future__ import annotations

import hashlib
import html
import io
import json
from datetime import datetime, timezone
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
            "status": "已有 VOC 资产；效果基线未建立", "source_ids": ["PROD-SOLUTIONS", "ASSET-VOC"],
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
            "status": "已有 FAE／VOC 资产；当前健康本轮未检查", "source_ids": ["ASSET-FAE", "ASSET-VOC"],
            "related_ids": ["market", "products", "technology"], "actions": ["fae", "voc"],
        },
    ],
    "support": [
        {"id": "hr", "title": "人才／HR", "subtitle": "岗位与人才工作流", "items": ["岗位", "资源", "候选批次", "面试记录"], "detail": ["已有历史部署与任务记录；不等于成功交付。"], "status": "已有应用｜当前健康本轮未检查", "source_ids": ["ASSET-HR"], "related_ids": ["organization"], "actions": ["hr"]},
        {"id": "office", "title": "行政／ADMIN", "subtitle": "问答与事务流程", "items": ["制度问答", "班车", "住宿", "车辆", "通知"], "detail": ["AI 问答与事务流程分别记账。"], "status": "已有应用｜效果基线未建立", "source_ids": ["ASSET-ADMIN"], "related_ids": ["organization"], "actions": ["office"]},
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


def render_svg() -> bytes:
    generated = _generated_at()
    esc = lambda value: html.escape(str(value), quote=True)
    blocks: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="1920" height="1080" fill="#07131f"/>',
        '<style>text{font-family:system-ui,"Noto Sans CJK SC",sans-serif;fill:#eef6ff}.muted{fill:#9fb4c8}.card{fill:#102637;stroke:#2d5269;stroke-width:2}.accent{fill:#61d8c6}.warn{fill:#ffc56e}</style>',
        f'<text x="70" y="72" font-size="36" font-weight="700">{esc(PANORAMA["title"])}</text>',
        f'<text x="70" y="108" font-size="17" class="muted">内容版本 {esc(PANORAMA["version"])}｜数据时间 {esc(PANORAMA["updated_at"])}｜生成时间 {esc(generated)}</text>',
        '<rect class="card" x="70" y="140" width="1780" height="155" rx="18"/>',
        f'<text x="100" y="180" font-size="20" class="accent">经营背景｜{esc(PANORAMA["context"]["period"])}</text>',
    ]
    for index, metric in enumerate(PANORAMA["context"]["metrics"]):
        blocks.append(f'<text x="{100 + index * 455}" y="224" font-size="25" font-weight="650">{esc(metric["label"])}  {esc(metric["value"])}</text>')
    blocks.extend([
        f'<text x="100" y="264" font-size="18" class="muted">{esc(PANORAMA["context"]["observation"])} {esc(PANORAMA["context"]["judgment"])}</text>',
        '<text x="70" y="338" font-size="21" font-weight="700">2025 主营收入构成｜同尺度原金额</text>',
    ])
    x = 70
    for segment, color in zip(PANORAMA["revenue"]["segments"], ("#61d8c6", "#58a6ff", "#ffc56e", "#899aab"), strict=True):
        width = round(1780 * segment["amount_cents"] / PANORAMA["revenue"]["denominator_cents"])
        blocks.append(f'<rect x="{x}" y="360" width="{width}" height="42" fill="{color}"/>')
        x += width
    labels = "   ".join(f'{item["label"]} {_percent(item["amount_cents"])}' for item in PANORAMA["revenue"]["segments"])
    blocks.append(f'<text x="70" y="432" font-size="19">{esc(labels)}</text>')
    for index, domain in enumerate(PANORAMA["domains"]):
        card_x = 70 + index * 356
        blocks.extend([
            f'<rect class="card" x="{card_x}" y="475" width="330" height="245" rx="16"/>',
            f'<text x="{card_x + 22}" y="516" font-size="23" font-weight="700">{esc(domain["title"])}</text>',
            f'<text x="{card_x + 22}" y="546" font-size="16" class="muted">{esc(domain["subtitle"])}</text>',
        ])
        for line_index, item in enumerate(domain["items"][:4]):
            blocks.append(f'<text x="{card_x + 22}" y="{582 + line_index * 27}" font-size="17">• {esc(item)}</text>')
        blocks.append(f'<text x="{card_x + 22}" y="696" font-size="14" class="warn">{esc(domain["status"][:20])}</text>')
    support = "｜".join(item["title"] for item in PANORAMA["support"])
    blocks.extend([
        '<rect class="card" x="70" y="750" width="1780" height="82" rx="16"/>',
        f'<text x="95" y="786" font-size="21" font-weight="700">管理支撑</text><text x="230" y="786" font-size="19">{esc(support)}</text>',
        f'<text x="95" y="815" font-size="16" class="muted">共用能力｜{esc(PANORAMA["shared"]["status"])}</text>',
        '<text x="70" y="878" font-size="22" font-weight="700">请管理层协调（提议，尚未获批）</text>',
    ])
    for index, ask in enumerate(PANORAMA["asks"]):
        blocks.append(f'<text x="90" y="{916 + index * 34}" font-size="18"><tspan class="accent">{esc(ask["owner"])}：</tspan>{esc(ask["request"])}</text>')
    source_ids = "、".join(source["id"] for source in PANORAMA["sources"])
    blocks.extend([
        f'<text x="70" y="1038" font-size="13" class="muted">来源编号：{esc(source_ids)}</text>',
        f'<text x="1850" y="1064" text-anchor="end" font-size="12" class="muted">内容 SHA-256 {content_hash()[:16]}…</text>',
        '</svg>',
    ])
    return "".join(blocks).encode()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def render_png() -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#07131f")
    draw = ImageDraw.Draw(image)
    regular = _font(18)
    small = _font(14)
    heading = _font(24, bold=True)
    title = _font(36, bold=True)
    draw.text((70, 45), PANORAMA["title"], font=title, fill="#eef6ff")
    draw.text((70, 95), f"内容版本 {PANORAMA['version']}｜数据时间 {PANORAMA['updated_at']}｜生成时间 {_generated_at()}", font=small, fill="#9fb4c8")
    draw.rounded_rectangle((70, 140, 1850, 295), 18, fill="#102637", outline="#2d5269", width=2)
    draw.text((100, 165), f"经营背景｜{PANORAMA['context']['period']}", font=heading, fill="#61d8c6")
    for index, metric in enumerate(PANORAMA["context"]["metrics"]):
        draw.text((100 + index * 455, 210), f"{metric['label']}  {metric['value']}", font=regular, fill="#eef6ff")
    draw.text((100, 254), PANORAMA["context"]["observation"] + " " + PANORAMA["context"]["judgment"], font=small, fill="#9fb4c8")
    draw.text((70, 320), "2025 主营收入构成｜同尺度原金额", font=heading, fill="#eef6ff")
    x = 70
    for segment, color in zip(PANORAMA["revenue"]["segments"], ("#61d8c6", "#58a6ff", "#ffc56e", "#899aab"), strict=True):
        width = round(1780 * segment["amount_cents"] / PANORAMA["revenue"]["denominator_cents"])
        draw.rectangle((x, 360, x + width, 402), fill=color)
        x += width
    draw.text((70, 415), "   ".join(f"{item['label']} {_percent(item['amount_cents'])}" for item in PANORAMA["revenue"]["segments"]), font=regular, fill="#eef6ff")
    for index, domain in enumerate(PANORAMA["domains"]):
        card_x = 70 + index * 356
        draw.rounded_rectangle((card_x, 475, card_x + 330, 720), 16, fill="#102637", outline="#2d5269", width=2)
        draw.text((card_x + 22, 495), domain["title"], font=heading, fill="#eef6ff")
        draw.text((card_x + 22, 530), domain["subtitle"], font=small, fill="#9fb4c8")
        for line_index, item in enumerate(domain["items"][:4]):
            draw.text((card_x + 22, 570 + line_index * 28), "• " + item, font=regular, fill="#eef6ff")
        draw.text((card_x + 22, 690), domain["status"][:20], font=small, fill="#ffc56e")
    draw.rounded_rectangle((70, 750, 1850, 832), 16, fill="#102637", outline="#2d5269", width=2)
    draw.text((95, 770), "管理支撑｜" + "｜".join(item["title"] for item in PANORAMA["support"]), font=regular, fill="#eef6ff")
    draw.text((95, 802), "共用能力｜" + PANORAMA["shared"]["status"], font=small, fill="#9fb4c8")
    draw.text((70, 858), "请管理层协调（提议，尚未获批）", font=heading, fill="#eef6ff")
    for index, ask in enumerate(PANORAMA["asks"]):
        draw.text((90, 900 + index * 34), f"{ask['owner']}：{ask['request']}", font=regular, fill="#eef6ff")
    draw.text((70, 1025), "来源编号：" + "、".join(source["id"] for source in PANORAMA["sources"]), font=small, fill="#9fb4c8")
    draw.text((70, 1052), "内容 SHA-256 " + content_hash(), font=small, fill="#9fb4c8")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
