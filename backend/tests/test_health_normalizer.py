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


def test_fae_full_inventory():
    raw = {
        "status": "ok",
        "qa_indexed": True,
        "qa_count": 1806,
        "products_loaded": 54,
        "sdk_records": 113,
        "sdk_repos": 8,
        "llm_model": "glm-5.2",
    }
    metrics = normalize("fae", raw)
    labels = {metric["label"]: metric["value"] for metric in metrics}
    assert labels["模型"] == "glm-5.2"
    assert labels["QA 对"] == 1806
    assert labels["产品数"] == 54
    assert labels["SDK 知识"] == 113
    assert labels["SDK 仓库"] == 8
    # 有真实 qa_count 时不再退化成布尔的「QA 索引」标签
    assert "QA 索引" not in labels


def test_fae_qa_count_falls_back_to_indexed_flag():
    # 老版本 FAE 只上报布尔 qa_indexed,没有 qa_count
    metrics = normalize("fae", {"qa_indexed": True, "products_loaded": 7})
    assert {"label": "QA 索引", "value": "已加载"} in metrics


def test_admin_full_inventory():
    raw = {
        "status": "ok",
        "llm_model": "glm-5.2",
        "chunks_loaded": 417,
        "documents_loaded": 8,
        "tables_loaded": 17,
        "policy_facts_loaded": 373,
        "processes_loaded": 65,
        "forms_loaded": 28,
        "contacts_loaded": 15,
    }
    labels = {m["label"]: m["value"] for m in normalize("admin", raw)}
    assert labels["政策条目"] == 373
    assert labels["流程"] == 65
    assert labels["表单"] == 28
    assert labels["联系人"] == 15
    assert labels["表格"] == 17


def test_unknown_type_falls_back_to_generic_empty():
    assert normalize("sales", {"anything": 1}) == []


def test_normalizer_never_raises_on_bad_shape():
    assert normalize("fae", {"qa_indexed": None}) is not None
