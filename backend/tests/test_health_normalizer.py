from app.health.normalizer import normalize


def test_fae_metrics():
    raw = {"status": "ok", "qa_indexed": True, "products_loaded": 42}
    metrics = normalize("fae", raw)
    assert {"label": "QA 索引", "value": "已加载"} in metrics
    assert {"label": "产品数", "value": 42} in metrics


def test_admin_metrics():
    raw = {
        "status": "ok",
        "llm_model": "glm-5.2",
        "chunks_loaded": 12345,
        "documents_loaded": 42,
    }
    metrics = normalize("admin", raw)
    labels = {metric["label"]: metric["value"] for metric in metrics}
    assert labels["模型"] == "glm-5.2"
    assert labels["知识块"] == 12345
    assert labels["文档数"] == 42


def test_unknown_type_falls_back_to_generic_empty():
    assert normalize("sales", {"anything": 1}) == []


def test_normalizer_never_raises_on_bad_shape():
    assert normalize("fae", {"qa_indexed": None}) is not None
