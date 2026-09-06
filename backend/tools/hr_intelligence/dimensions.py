from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from urllib.parse import urlsplit

from app.hr.panorama_models import PublicJobSnapshot

from .models import NormalizedJob
from .taxonomy import normalize_location, secondary_directions

JobRecord = PublicJobSnapshot | NormalizedJob
_JOB_TYPES = (PublicJobSnapshot, NormalizedJob)

_TRACK_PATTERNS = {
    "intern": re.compile(r"实习|(?<![a-z])intern(?:ship)?(?![a-z])", re.IGNORECASE),
    "campus": re.compile(
        r"校招|校园招聘|应届|毕业生|\d{2}届|(?<![a-z])(?:campus|graduate)(?![a-z])",
        re.IGNORECASE,
    ),
    "social": re.compile(
        r"社招|社会招聘|社会人才|(?<![a-z])experienced(?![a-z])|"
        r"(?:^|[/_.-])social(?:$|[/_.?#-])|social[-_/]?recruitment|professional-hire",
        re.IGNORECASE,
    ),
}

_DIRECTIONS = {
    "光学": re.compile(r"光学|镜头|成像|光机|光电|zemax|code\s*v", re.IGNORECASE),
    "硬件": re.compile(
        r"硬件|电子|电路|pcb|pcba|emc|ems|esd|fpga|soc|芯片|射频", re.IGNORECASE
    ),
    "结构": re.compile(r"结构|机械|机电|模具|公差|cad|cae|solidworks", re.IGNORECASE),
    "软件": re.compile(
        r"软件|前端|后端|客户端|嵌入式|固件|操作系统|java|c\+\+|python|golang",
        re.IGNORECASE,
    ),
    "算法": re.compile(
        r"算法|人工智能|机器学习|深度学习|计算机视觉|点云|slam|标定|(?<![a-z])ai(?![a-z])",
        re.IGNORECASE,
    ),
    "制造工艺": re.compile(
        r"制造|工艺|生产|量产|试产|装配|注塑|钣金|cnc|良率", re.IGNORECASE
    ),
    "质量": re.compile(
        r"质量|测试|可靠性|(?<![a-z])(?:dqe|sqe|qe)(?![a-z])|失效分析|认证",
        re.IGNORECASE,
    ),
    "产品": re.compile(
        r"产品经理|产品规划|产品设计|需求分析|用户体验|ux|id设计", re.IGNORECASE
    ),
    "供应链": re.compile(r"供应链|采购|计划|pmc|物流|物料", re.IGNORECASE),
}

_SUPPLEMENTAL_DIRECTIONS = {
    "光学": re.compile(r"光学|镜头|成像|光机|光电|zemax|code\s*v", re.IGNORECASE),
    "硬件": re.compile(
        r"硬件|电子|电路|pcb|pcba|emc|esd|fpga|芯片|射频", re.IGNORECASE
    ),
    "结构": re.compile(
        r"结构设计|机械设计|模具|公差|solidworks|creo|catia", re.IGNORECASE
    ),
    "软件": re.compile(r"嵌入式|固件|操作系统|java|c\+\+|python|golang", re.IGNORECASE),
    "算法": re.compile(
        r"算法|机器学习|深度学习|计算机视觉|点云|slam|标定", re.IGNORECASE
    ),
    "制造工艺": re.compile(
        r"制造工艺|生产工艺|装配工艺|注塑|钣金|cnc|良率", re.IGNORECASE
    ),
    "质量": re.compile(
        r"可靠性|(?<![a-z])(?:dqe|sqe|qe)(?![a-z])|失效分析|认证", re.IGNORECASE
    ),
    "产品": re.compile(r"产品规划|产品经理|用户体验|ux|id设计", re.IGNORECASE),
    "供应链": re.compile(r"供应链|采购|pmc|物流|物料", re.IGNORECASE),
}

_FAMILIES = (
    (
        "quality",
        re.compile(
            r"质量|测试|可靠性|(?<![a-z])(?:dqe|sqe|qe)(?![a-z])|失效分析",
            re.IGNORECASE,
        ),
    ),
    ("manufacturing", re.compile(r"制造|工艺|生产|量产|试产|装配|cnc", re.IGNORECASE)),
    ("supply_chain", re.compile(r"供应链|采购|计划|pmc|物流|物料", re.IGNORECASE)),
    (
        "product",
        re.compile(r"产品经理|产品规划|产品设计|用户体验|ux|id设计", re.IGNORECASE),
    ),
    (
        "sales_marketing",
        re.compile(r"销售|市场|品牌|商务|渠道|电商|客户经理", re.IGNORECASE),
    ),
    ("operations", re.compile(r"运营|技术支持|售后|项目经理|交付", re.IGNORECASE)),
    ("corporate", re.compile(r"人力|招聘|财务|法务|行政|审计|秘书", re.IGNORECASE)),
    (
        "research_development",
        re.compile(r"研发|工程师|开发|算法|研究|设计师|架构师", re.IGNORECASE),
    ),
)

_SKILLS = {
    "Zemax": re.compile(r"(?<![a-z])zemax(?![a-z])", re.IGNORECASE),
    "Code V": re.compile(r"(?<![a-z])code\s*v(?![a-z])", re.IGNORECASE),
    "C++": re.compile(r"(?<![a-z])c\+\+(?![a-z+])", re.IGNORECASE),
    "Python": re.compile(r"(?<![a-z])python(?![a-z])", re.IGNORECASE),
    "Java": re.compile(r"(?<![a-z])java(?![a-z])", re.IGNORECASE),
    "Go": re.compile(r"(?<![a-z])golang(?![a-z])|(?<![a-z])go语言", re.IGNORECASE),
    "MATLAB": re.compile(r"(?<![a-z])matlab(?![a-z])", re.IGNORECASE),
    "ROS": re.compile(r"(?<![a-z])ros\d?(?![a-z0-9])", re.IGNORECASE),
    "SLAM": re.compile(r"(?<![a-z])slam(?![a-z])", re.IGNORECASE),
    "OpenCV": re.compile(r"(?<![a-z])opencv(?![a-z])", re.IGNORECASE),
    "PyTorch": re.compile(r"(?<![a-z])pytorch(?![a-z])", re.IGNORECASE),
    "TensorFlow": re.compile(r"(?<![a-z])tensorflow(?![a-z])", re.IGNORECASE),
    "CUDA": re.compile(r"(?<![a-z])cuda(?![a-z])", re.IGNORECASE),
    "FPGA": re.compile(r"(?<![a-z])fpga(?![a-z])", re.IGNORECASE),
    "Verilog": re.compile(r"(?<![a-z])(?:system)?verilog(?![a-z])", re.IGNORECASE),
    "PCB": re.compile(r"(?<![a-z])pcb(?:a)?(?![a-z])", re.IGNORECASE),
    "CAD": re.compile(r"(?<![a-z])cad(?![a-z])", re.IGNORECASE),
    "SolidWorks": re.compile(r"(?<![a-z])solidworks(?![a-z])", re.IGNORECASE),
    "Creo": re.compile(r"(?<![a-z])creo(?![a-z])", re.IGNORECASE),
    "CATIA": re.compile(r"(?<![a-z])catia(?![a-z])", re.IGNORECASE),
    "Linux": re.compile(r"(?<![a-z])linux(?![a-z])", re.IGNORECASE),
    "Docker": re.compile(r"(?<![a-z])docker(?![a-z])", re.IGNORECASE),
    "Kubernetes": re.compile(
        r"(?<![a-z])kubernetes|(?<![a-z])k8s(?![a-z])", re.IGNORECASE
    ),
}

_DIRECTION_KEYS = tuple(_DIRECTIONS) + ("其他",)
_SECONDARY_DIRECTION_KEYS = (
    "光学/发射", "光学/接收", "光学/镜头", "光学/镀膜", "光学/杂散光", "光学/标定",
    "硬件/电子", "硬件/嵌入式硬件", "硬件/PCB", "硬件/FPGA", "硬件/芯片", "硬件/器件", "硬件/电源",
    "结构/机械", "结构/材料", "结构/模具", "结构/热设计", "结构/整机", "结构/自动化设备",
    "软件/嵌入式", "软件/驱动", "软件/中间件", "软件/客户端", "软件/后端平台", "软件/AI基础设施", "软件/工具链",
    "算法/点云", "算法/SLAM", "算法/感知", "算法/影像", "算法/具身智能", "算法/运动控制", "算法/3D生成", "算法/数据算法",
    "制造工艺/SMT", "制造工艺/装配", "制造工艺/镀膜", "制造工艺/注塑", "制造工艺/机加工", "制造工艺/自动化", "制造工艺/DFM", "制造工艺/良率",
    "质量/研发验证", "质量/软件测试", "质量/硬件测试", "质量/PQE", "质量/DQE", "质量/供应商质量", "质量/可靠性", "质量/失效分析",
)
_TRACK_KEYS = ("social", "campus", "intern", "unknown")
_FAMILY_KEYS = tuple(key for key, _ in _FAMILIES) + ("other",)
_SENIORITY_KEYS = ("senior", "mid", "junior", "graduate", "unspecified")
_EDUCATION_KEYS = ("doctorate", "master", "bachelor", "college", "unspecified")


def _job_text(item: JobRecord) -> str:
    return f"{item.title} {item.duty_excerpt} {item.requirement_excerpt}"


def _track(item: JobRecord, text: str) -> str:
    url = item.source_url.casefold()
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    path = parsed.path
    if hostname.endswith(".bysjy.com.cn") and path == "/detail/career":
        return "campus"
    if hostname == "kwh0jtf778.jobs.feishu.cn":
        if path == "/index" or path.startswith("/index/"):
            return "social"
        if path == "/229043" or path.startswith("/229043/"):
            return "campus"
        if path == "/073183" or path.startswith("/073183/"):
            return "intern"
    if hostname.endswith(".zhiye.com") and path in {"", "/"}:
        return "social"
    if hostname == "www.elegoo.com.cn" and path.startswith("/index/join/"):
        return "social"
    if re.search(r"/gwtd1(?:-\d+)?\.html$", path) or re.search(
        r"(?:^|[/_.-])(?:social(?:eng|[-_/]?recruitment)?|experienced|professional-hire)(?:$|[/_.?#-])",
        url,
    ):
        return "social"
    if re.search(r"/gwtd(?:-\d+)?\.html$", path) or re.search(
        r"(?:^|[/_.-])(?:campus|graduate)(?:[-_/]?recruitment)?(?:$|[/_.?#-])",
        url,
    ):
        return "campus"
    if re.search(r"(?:^|[/_.-])intern(?:ship|recruitment)?(?:$|[/_.?#-])", url):
        return "intern"
    selected = f"{item.title} {text}"
    for key in ("intern", "campus", "social"):
        if _TRACK_PATTERNS[key].search(selected):
            return key
    return "unknown"


def recruitment_track(item: JobRecord) -> str:
    if not isinstance(item, _JOB_TYPES):
        raise TypeError("public job snapshot required")
    return _track(item, _job_text(item))


def technical_directions(item: JobRecord) -> tuple[str, ...]:
    if not isinstance(item, _JOB_TYPES):
        raise TypeError("public job snapshot required")
    title_selected = tuple(
        key for key, pattern in _DIRECTIONS.items() if pattern.search(item.title)
    )
    text = _job_text(item)
    if title_selected:
        supplemental = tuple(
            key
            for key, pattern in _SUPPLEMENTAL_DIRECTIONS.items()
            if key not in title_selected and pattern.search(text)
        )
        selected = (*title_selected, *supplemental)
    else:
        selected = tuple(
            key for key, pattern in _DIRECTIONS.items() if pattern.search(text)
        )
    return selected or ("其他",)


def _seniority(track: str, text: str) -> str:
    if track in {"campus", "intern"}:
        return "graduate"
    if re.search(
        r"高级|资深|专家|负责人|总监|架构师|首席|[5-9]\s*年|\d{2,}\s*年|[五六七八九十]\s*年",
        text,
    ):
        return "senior"
    if re.search(r"中级|[3-4]\s*年|[三四]\s*年", text):
        return "mid"
    if re.search(r"初级|助理|[1-2]\s*年|[一二两]\s*年", text):
        return "junior"
    return "unspecified"


def _education(text: str) -> str:
    for key, pattern in (
        ("college", r"大专|专科"),
        ("bachelor", r"本科|学士"),
        ("master", r"硕士|研究生"),
        ("doctorate", r"博士"),
    ):
        if re.search(pattern, text):
            return key
    return "unspecified"


def _family(title: str, text: str) -> str:
    for key, pattern in _FAMILIES:
        if pattern.search(title):
            return key
    if not re.search(r"研发|工程师|开发|算法|研究|设计师|架构师", title):
        for key, pattern in _FAMILIES:
            if pattern.search(text):
                return key
    return "other"


def _locations(value: str) -> tuple[str, ...]:
    normalized = normalize_location(value)
    if not normalized.valid:
        return ()
    return normalized.parts or (normalized.normalized,)


def _secondary(item: JobRecord) -> tuple[str, ...]:
    if isinstance(item, NormalizedJob):
        return secondary_directions(item)
    surrogate = NormalizedJob(
        job_id=item.snapshot_id,
        source_id=item.source_id,
        company_key="legacy-source",
        public_job_key=item.public_job_key,
        title=item.title,
        location=item.location,
        duty_excerpt=item.duty_excerpt,
        requirement_excerpt=item.requirement_excerpt,
        source_url=item.source_url,
        evidence_sha256=item.content_sha256,
        observed_at=item.observed_at,
        status=item.status,
    )
    return secondary_directions(surrogate)


def _company_key(item: JobRecord) -> str:
    return item.company_key if isinstance(item, NormalizedJob) else str(item.source_id)


def _counts(keys: tuple[str, ...], selected: Counter[str]) -> dict[str, int]:
    return {key: selected.get(key, 0) for key in keys}


def compile_panorama_dimensions(
    snapshots: tuple[JobRecord, ...],
) -> Mapping[str, object]:
    if (
        not isinstance(snapshots, tuple)
        or not snapshots
        or len(snapshots) > 10000
        or any(not isinstance(item, _JOB_TYPES) for item in snapshots)
    ):
        raise ValueError("panorama dimensions evidence invalid")
    unique: dict[tuple[object, str], JobRecord] = {}
    for item in snapshots:
        key = (item.source_id, item.public_job_key)
        previous = unique.get(key)
        if previous is None or (item.observed_at, str(item.snapshot_id)) > (
            previous.observed_at,
            str(previous.snapshot_id),
        ):
            unique[key] = item
    jobs = tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                str(item.source_id),
                item.public_job_key,
                item.observed_at,
                str(item.snapshot_id),
            ),
        )
    )
    tracks: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    secondary: Counter[str] = Counter()
    families: Counter[str] = Counter()
    seniority: Counter[str] = Counter()
    education: Counter[str] = Counter()
    locations: Counter[str] = Counter()
    skills: Counter[str] = Counter()
    evidence: dict[str, dict[str, list[str]]] = {
        "directions": defaultdict(list),
        "secondary_directions": defaultdict(list),
        "skills": defaultdict(list),
    }
    companies: dict[str, dict[str, object]] = {}
    invalid_locations: Counter[str] = Counter()
    for item in jobs:
        text = _job_text(item)
        track = recruitment_track(item)
        selected_directions = technical_directions(item)
        selected_secondary = _secondary(item)
        family = _family(item.title, text)
        level = _seniority(track, text)
        degree = _education(text)
        raw_location = (
            str(item.raw_location)
            if isinstance(item, NormalizedJob)
            else item.location
        )
        normalized_location = normalize_location(raw_location)
        selected_locations = (
            normalized_location.parts
            if normalized_location.valid and normalized_location.parts
            else (
                (normalized_location.normalized,)
                if normalized_location.valid
                else ()
            )
        )
        if not normalized_location.valid:
            invalid_locations[normalized_location.raw] += 1
        selected_skills = tuple(
            key for key, pattern in _SKILLS.items() if pattern.search(text)
        )
        tracks[track] += 1
        families[family] += 1
        seniority[level] += 1
        education[degree] += 1
        directions.update(selected_directions)
        secondary.update(selected_secondary)
        locations.update(selected_locations)
        skills.update(selected_skills)
        for direction in selected_directions:
            if len(evidence["directions"][direction]) < 20:
                evidence["directions"][direction].append(str(item.snapshot_id))
        for direction in selected_secondary:
            if len(evidence["secondary_directions"][direction]) < 20:
                evidence["secondary_directions"][direction].append(
                    str(item.snapshot_id)
                )
        for skill in selected_skills:
            if len(evidence["skills"][skill]) < 20:
                evidence["skills"][skill].append(str(item.snapshot_id))
        company = companies.setdefault(
            _company_key(item),
            {
                "job_count": 0,
                "tracks": Counter(),
                "directions": Counter(),
                "secondary_directions": Counter(),
                "job_families": Counter(),
                "seniority": Counter(),
                "locations": Counter(),
                "skills": Counter(),
                "sample_snapshot_ids": [],
            },
        )
        company["job_count"] += 1
        company["tracks"][track] += 1
        company["directions"].update(selected_directions)
        company["secondary_directions"].update(selected_secondary)
        company["job_families"][family] += 1
        company["seniority"][level] += 1
        company["locations"].update(selected_locations)
        company["skills"].update(selected_skills)
        if len(company["sample_snapshot_ids"]) < 20:
            company["sample_snapshot_ids"].append(str(item.snapshot_id))
    company_matrix = {}
    for source_id, value in companies.items():
        company_matrix[source_id] = {
            "job_count": value["job_count"],
            "tracks": _counts(_TRACK_KEYS, value["tracks"]),
            "directions": _counts(_DIRECTION_KEYS, value["directions"]),
            "secondary_directions": _counts(
                _SECONDARY_DIRECTION_KEYS, value["secondary_directions"]
            ),
            "job_families": _counts(_FAMILY_KEYS, value["job_families"]),
            "seniority": _counts(_SENIORITY_KEYS, value["seniority"]),
            "locations": dict(value["locations"].most_common(20)),
            "skills": dict(value["skills"].most_common(20)),
            "sample_snapshot_ids": value["sample_snapshot_ids"],
        }
    company_comparison = {
        company_key: {
            "absolute": {
                "job_count": value["job_count"],
                "directions": _counts(_DIRECTION_KEYS, value["directions"]),
                "secondary_directions": _counts(
                    _SECONDARY_DIRECTION_KEYS, value["secondary_directions"]
                ),
                "job_families": _counts(_FAMILY_KEYS, value["job_families"]),
            },
            "internal_share": {
                direction: round(value["directions"].get(direction, 0) / value["job_count"], 4)
                for direction in _DIRECTION_KEYS
            },
            "sample_confidence": (
                "high" if value["job_count"] >= 100
                else "medium" if value["job_count"] >= 30
                else "low"
            ),
        }
        for company_key, value in sorted(companies.items())
    }
    return {
        "schema_version": 3,
        "scope": {
            "snapshot_count": len(snapshots),
            "unique_job_count": len(jobs),
            "duplicate_snapshot_count": len(snapshots) - len(jobs),
            "source_count": len({item.source_id for item in jobs}),
            "observed_from": min(item.observed_at for item in jobs).isoformat(),
            "observed_to": max(item.observed_at for item in jobs).isoformat(),
        },
        "tracks": _counts(_TRACK_KEYS, tracks),
        "directions": _counts(_DIRECTION_KEYS, directions),
        "secondary_directions": _counts(_SECONDARY_DIRECTION_KEYS, secondary),
        "job_families": _counts(_FAMILY_KEYS, families),
        "seniority": _counts(_SENIORITY_KEYS, seniority),
        "education": _counts(_EDUCATION_KEYS, education),
        "locations": dict(locations.most_common(100)),
        "skills": [
            {"name": name, "job_count": count}
            for name, count in skills.most_common(100)
        ],
        "company_matrix": company_matrix,
        "company_comparison": company_comparison,
        "evidence_samples": {layer: dict(values) for layer, values in evidence.items()},
        "data_quality": {
            "invalid_locations": dict(invalid_locations.most_common()),
        },
        "trend": {
            "state": "baseline_only",
            "message": "基线版本：尚不能判断月度变化",
        },
        "interpretation_limits": [
            "公开岗位数不等于HC、预算、产量或实际研发投入",
            "未覆盖或采集失败不代表企业没有招聘活动",
            "只有形成跨期可比版本后才判断新增、消失或趋势变化",
        ],
    }


compile_dimensions = compile_panorama_dimensions


__all__ = [
    "compile_dimensions",
    "compile_panorama_dimensions",
    "recruitment_track",
    "technical_directions",
]
