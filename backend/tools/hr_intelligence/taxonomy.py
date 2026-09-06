from __future__ import annotations

import re
from dataclasses import dataclass

from .models import NormalizedJob

_SEPARATORS = re.compile(r"\s*(?:[、/,，；;|·]|\s+-\s+)\s*")
_WHITESPACE = re.compile(r"\s+")
_PROVINCES = {
    "安徽",
    "福建",
    "甘肃",
    "广东",
    "贵州",
    "海南",
    "河北",
    "河南",
    "黑龙江",
    "湖北",
    "湖南",
    "吉林",
    "江苏",
    "江西",
    "辽宁",
    "青海",
    "山东",
    "山西",
    "陕西",
    "四川",
    "台湾",
    "云南",
    "浙江",
}
_REGION_ALIASES = {
    "北京市": "北京",
    "上海市": "上海",
    "天津市": "天津",
    "重庆市": "重庆",
    "香港特别行政区": "香港",
    "广西壮族自治区": "广西",
    "Central Singapore": "新加坡",
    "Singapore": "新加坡",
}
_KNOWN_LOCATIONS = {
    "北京",
    "上海",
    "天津",
    "重庆",
    "深圳",
    "东莞",
    "广州",
    "中山",
    "惠州",
    "汕尾",
    "珠海",
    "杭州",
    "宁波",
    "南京",
    "苏州",
    "无锡",
    "常州",
    "成都",
    "武汉",
    "西安",
    "合肥",
    "济南",
    "青岛",
    "九江",
    "柳州",
    "香港",
    "东京",
    "札幌",
    "横滨",
    "新加坡",
    "马来西亚",
    "帕洛阿尔托",
    "圣克拉拉",
    "旧金山",
    "洛杉矶",
    "奥斯汀",
    "伯明翰",
    "曼彻斯特",
    "巴塞罗那",
    "巴黎",
    "慕尼黑",
    "斯图加特",
    "法兰克福",
    "米兰",
    "中国",
    "全国",
    "多地",
    "海外",
    "未公开",
}
_DISTRICT = re.compile(r"(?:区|县|镇|街道)\Z")

_SECONDARY_RULES = (
    ("光学/发射", re.compile(r"激光器|光源|发射|vcsel", re.IGNORECASE)),
    ("光学/接收", re.compile(r"光电探测|接收光学|spad|apd", re.IGNORECASE)),
    ("光学/镜头", re.compile(r"镜头|lens|光学设计|zemax|code\s*v", re.IGNORECASE)),
    ("光学/镀膜", re.compile(r"镀膜|coating", re.IGNORECASE)),
    ("光学/杂散光", re.compile(r"杂散光", re.IGNORECASE)),
    ("光学/标定", re.compile(r"光学标定|相机标定|标定算法", re.IGNORECASE)),
    ("硬件/电子", re.compile(r"模拟电路|数字电路|电子设计", re.IGNORECASE)),
    ("硬件/嵌入式硬件", re.compile(r"嵌入式硬件|单片机|mcu", re.IGNORECASE)),
    ("硬件/PCB", re.compile(r"(?<![a-z])pcb(?:a)?(?![a-z])", re.IGNORECASE)),
    ("硬件/FPGA", re.compile(r"(?<![a-z])fpga(?![a-z])|verilog", re.IGNORECASE)),
    ("硬件/芯片", re.compile(r"芯片|soc|asic", re.IGNORECASE)),
    ("硬件/器件", re.compile(r"器件|元器件", re.IGNORECASE)),
    ("硬件/电源", re.compile(r"电源|power\s*supply", re.IGNORECASE)),
    ("结构/机械", re.compile(r"机械|结构设计", re.IGNORECASE)),
    ("结构/材料", re.compile(r"材料|高分子|金属材料", re.IGNORECASE)),
    ("结构/模具", re.compile(r"模具|注塑模", re.IGNORECASE)),
    ("结构/热设计", re.compile(r"热设计|散热|热仿真", re.IGNORECASE)),
    ("结构/整机", re.compile(r"整机|系统结构", re.IGNORECASE)),
    ("结构/自动化设备", re.compile(r"自动化设备|非标设备", re.IGNORECASE)),
    ("软件/嵌入式", re.compile(r"嵌入式软件|固件|firmware", re.IGNORECASE)),
    ("软件/驱动", re.compile(r"驱动开发|device\s*driver", re.IGNORECASE)),
    ("软件/中间件", re.compile(r"中间件|middleware|ros\d?", re.IGNORECASE)),
    ("软件/客户端", re.compile(r"客户端|android|ios|桌面端", re.IGNORECASE)),
    ("软件/后端平台", re.compile(r"后端|服务端|微服务|java|golang", re.IGNORECASE)),
    ("软件/AI基础设施", re.compile(r"ai平台|模型部署|cuda|pytorch|tensorflow", re.IGNORECASE)),
    ("软件/工具链", re.compile(r"工具链|编译器|sdk", re.IGNORECASE)),
    ("算法/点云", re.compile(r"点云|point\s*cloud", re.IGNORECASE)),
    ("算法/SLAM", re.compile(r"(?<![a-z])slam(?![a-z])|定位建图|定位、建图", re.IGNORECASE)),
    ("算法/感知", re.compile(r"环境感知|目标检测|目标跟踪|多传感器融合", re.IGNORECASE)),
    ("算法/影像", re.compile(r"影像算法|图像算法|计算摄影|isp", re.IGNORECASE)),
    ("算法/具身智能", re.compile(r"具身|大模型机器人|vla", re.IGNORECASE)),
    ("算法/运动控制", re.compile(r"运动控制|轨迹规划|动力学", re.IGNORECASE)),
    ("算法/3D生成", re.compile(r"3d生成|三维生成|重建算法", re.IGNORECASE)),
    ("算法/数据算法", re.compile(r"数据算法|数据闭环|数据挖掘", re.IGNORECASE)),
    ("制造工艺/SMT", re.compile(r"(?<![a-z])smt(?![a-z])", re.IGNORECASE)),
    ("制造工艺/装配", re.compile(r"装配|组装工艺", re.IGNORECASE)),
    ("制造工艺/镀膜", re.compile(r"镀膜工艺", re.IGNORECASE)),
    ("制造工艺/注塑", re.compile(r"注塑", re.IGNORECASE)),
    ("制造工艺/机加工", re.compile(r"机加工|cnc", re.IGNORECASE)),
    ("制造工艺/自动化", re.compile(r"自动化产线|生产自动化", re.IGNORECASE)),
    ("制造工艺/DFM", re.compile(r"(?<![a-z])dfm(?![a-z])|可制造性", re.IGNORECASE)),
    ("制造工艺/良率", re.compile(r"良率|yield", re.IGNORECASE)),
    ("质量/研发验证", re.compile(r"研发验证|设计验证|dv测试", re.IGNORECASE)),
    ("质量/软件测试", re.compile(r"软件测试|测试开发|自动化测试", re.IGNORECASE)),
    ("质量/硬件测试", re.compile(r"硬件测试|电气测试", re.IGNORECASE)),
    ("质量/PQE", re.compile(r"(?<![a-z])pqe(?![a-z])", re.IGNORECASE)),
    ("质量/DQE", re.compile(r"(?<![a-z])dqe(?![a-z])", re.IGNORECASE)),
    ("质量/供应商质量", re.compile(r"供应商质量|(?<![a-z])sqe(?![a-z])", re.IGNORECASE)),
    ("质量/可靠性", re.compile(r"可靠性|可靠度", re.IGNORECASE)),
    ("质量/失效分析", re.compile(r"失效分析|failure\s*analysis", re.IGNORECASE)),
)


@dataclass(frozen=True, slots=True)
class NormalizedLocation:
    raw: str
    normalized: str
    parts: tuple[str, ...]
    valid: bool


def _clean(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("location must be text")
    selected = _WHITESPACE.sub(" ", value.replace("\u200b", "")).strip()
    if not selected or len(selected) > 1000:
        raise ValueError("location invalid")
    return selected


def _normalize_part(value: str) -> str | None:
    selected = _REGION_ALIASES.get(value, value)
    if selected.endswith("省") and selected[:-1] in _PROVINCES:
        return selected[:-1]
    if selected.endswith("市"):
        selected = selected[:-1]
    if _DISTRICT.search(selected):
        return None
    return _REGION_ALIASES.get(selected, selected)


def normalize_location(raw: str) -> NormalizedLocation:
    selected = _clean(raw)
    raw_parts = tuple(part for part in _SEPARATORS.split(selected) if part)
    normalized_parts = tuple(
        dict.fromkeys(
            normalized
            for part in raw_parts
            if (normalized := _normalize_part(part)) is not None
        )
    )
    cities = tuple(part for part in normalized_parts if part not in _PROVINCES)
    chosen = cities or normalized_parts
    if chosen and all(part in _KNOWN_LOCATIONS or part in _PROVINCES for part in chosen):
        return NormalizedLocation(selected, "、".join(chosen), chosen, True)
    return NormalizedLocation(selected, "未规范", (), False)


def secondary_directions(job: NormalizedJob) -> tuple[str, ...]:
    if not isinstance(job, NormalizedJob):
        raise TypeError("normalized job required")
    text = f"{job.title} {job.duty_excerpt} {job.requirement_excerpt}"
    return tuple(name for name, pattern in _SECONDARY_RULES if pattern.search(text))


__all__ = ["NormalizedLocation", "normalize_location", "secondary_directions"]
