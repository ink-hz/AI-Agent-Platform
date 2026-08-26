"""Map heterogeneous agent /health payloads into a uniform metrics list."""


def _fae(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if raw.get("llm_model"):
        metrics.append({"label": "模型", "value": raw["llm_model"]})
    # 优先展示真实 QA 对数量;老版本只上报布尔 qa_indexed 时退化为状态文案。
    if "qa_count" in raw:
        metrics.append({"label": "QA 对", "value": raw["qa_count"]})
    elif "qa_indexed" in raw:
        metrics.append(
            {"label": "QA 索引", "value": "已加载" if raw["qa_indexed"] else "未加载"}
        )
    if "products_loaded" in raw:
        metrics.append({"label": "产品数", "value": raw["products_loaded"]})
    if "sdk_records" in raw:
        metrics.append({"label": "SDK 知识", "value": raw["sdk_records"]})
    if "sdk_repos" in raw:
        metrics.append({"label": "SDK 仓库", "value": raw["sdk_repos"]})
    return metrics


def _admin(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if raw.get("llm_model"):
        metrics.append({"label": "模型", "value": raw["llm_model"]})
    # (health 字段, 展示标签) —— 只展示存在的字段,缺失则跳过。
    for field, label in (
        ("documents_loaded", "文档数"),
        ("chunks_loaded", "知识块"),
        ("policy_facts_loaded", "政策条目"),
        ("processes_loaded", "流程"),
        ("forms_loaded", "表单"),
        ("contacts_loaded", "联系人"),
        ("tables_loaded", "表格"),
    ):
        if field in raw:
            metrics.append({"label": label, "value": raw[field]})
    return metrics


def _generic(raw: dict) -> list[dict]:
    return []


_NORMALIZERS = {"fae": _fae, "admin": _admin, "generic": _generic}


def normalize(health_type: str, raw: dict) -> list[dict]:
    fn = _NORMALIZERS.get(health_type, _generic)
    try:
        return fn(raw)
    except Exception:
        return []
