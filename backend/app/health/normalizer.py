"""Map heterogeneous agent /health payloads into a uniform metrics list."""


def _fae(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if "qa_indexed" in raw:
        metrics.append(
            {"label": "QA 索引", "value": "已加载" if raw["qa_indexed"] else "未加载"}
        )
    if "products_loaded" in raw:
        metrics.append({"label": "产品数", "value": raw["products_loaded"]})
    return metrics


def _admin(raw: dict) -> list[dict]:
    metrics: list[dict] = []
    if raw.get("llm_model"):
        metrics.append({"label": "模型", "value": raw["llm_model"]})
    if "chunks_loaded" in raw:
        metrics.append({"label": "知识块", "value": raw["chunks_loaded"]})
    if "documents_loaded" in raw:
        metrics.append({"label": "文档数", "value": raw["documents_loaded"]})
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
