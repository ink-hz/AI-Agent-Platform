import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from app.ai_engineering.models import PanoramaValidationError, validate_panorama
from app.ai_engineering.seed import PANORAMA_SEED
from app.ai_engineering.store import (
    PanoramaConflict,
    PanoramaStore,
    PanoramaUnavailable,
)


def test_seed_has_exact_four_layer_contract_without_financial_main_content():
    data = validate_panorama(deepcopy(PANORAMA_SEED))
    assert set(data) == {
        "version",
        "updated_at",
        "title",
        "layers",
        "nodes",
        "edges",
        "sources",
    }
    assert [layer["kind"] for layer in data["layers"]] == [
        "industry",
        "portfolio",
        "workflow",
        "support",
    ]
    assert [node["id"] for node in data["nodes"] if node["id"] == "robotics"] == [
        "robotics"
    ]
    assert not ({"revenue", "metrics", "denominator_cents", "amount_cents"} & set(data))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["nodes"][0]["actions"].append("arbitrary"),
        lambda p: p["edges"].append(
            {
                "id": "bad-edge",
                "from": "missing",
                "to": "robotics",
                "kind": "supports",
                "label": "bad",
            }
        ),
        lambda p: p["nodes"].append(deepcopy(p["nodes"][0])),
        lambda p: p["nodes"][0].__setitem__("title", "x" * 81),
    ],
)
def test_validation_rejects_unsafe_or_inconsistent_graph(mutation):
    payload = deepcopy(PANORAMA_SEED)
    mutation(payload)
    with pytest.raises(PanoramaValidationError):
        validate_panorama(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["nodes"][0].__setitem__("actions", [[]]),
        lambda p: p["nodes"][0].__setitem__("source_ids", [{}]),
        lambda p: p["layers"][0]["groups"][0].__setitem__("node_ids", [[]]),
        lambda p: p["layers"][0].__setitem__("kind", []),
        lambda p: p["layers"][0]["groups"][0].__setitem__("role", {}),
        lambda p: p["edges"][0].__setitem__("kind", []),
        lambda p: p["edges"][0].__setitem__("from", []),
        lambda p: p["edges"][0].__setitem__("to", {}),
        lambda p: p["sources"][0].__setitem__("document", []),
    ],
)
def test_malformed_nested_values_are_validation_errors(mutation):
    payload = deepcopy(PANORAMA_SEED)
    mutation(payload)
    with pytest.raises(PanoramaValidationError):
        validate_panorama(payload)


def test_seed_labels_and_industry_columns_match_approved_diagram():
    nodes = {node["id"]: node["title"] for node in PANORAMA_SEED["nodes"]}
    assert [
        nodes[node_id] for node_id in ("semiconductor", "optoelectronics", "precision")
    ] == [
        "半导体制造",
        "光电器件",
        "光学与精密制造",
    ]
    assert [
        nodes[node_id]
        for node_id in (
            "chip-product",
            "camera",
            "lidar",
            "smart-vision",
            "industry-systems",
            "software",
        )
    ] == [
        "芯片",
        "视觉模组与相机",
        "激光雷达",
        "智能视觉设备",
        "行业终端与系统",
        "软件与算法",
    ]
    assert [
        nodes[node_id]
        for node_id in (
            "chip-tech",
            "optics",
            "depth-algorithm",
            "sdk-tech",
            "calibration",
        )
    ] == [
        "自研芯片",
        "光学感知",
        "深度算法",
        "固件与 SDK",
        "标定与量产",
    ]
    assert [
        nodes[node_id]
        for node_id in (
            "market-insight",
            "requirements",
            "product-planning",
            "marketing-sales",
            "customer-use",
        )
    ] == [
        "市场洞察",
        "需求定义",
        "产品规划",
        "推广销售",
        "客户应用",
    ]
    assert [
        nodes[node_id]
        for node_id in (
            "research",
            "feasibility",
            "development",
            "manufacturing",
            "integration",
        )
    ] == [
        "技术预研",
        "场景验证",
        "研发验证",
        "量产交付",
        "集成服务",
    ]
    industry = {group["role"]: group for group in PANORAMA_SEED["layers"][0]["groups"]}
    assert industry["upstream"]["columns"] == 1
    assert industry["downstream"]["columns"] == 3


def test_store_draft_publish_restore_conflict_and_restart(tmp_path):
    path = tmp_path / "panorama.sqlite3"
    store = PanoramaStore(path)
    initial = store.read()
    edited = deepcopy(initial["published"])
    edited["title"] = "草稿"
    saved = store.save_draft(initial["revision"], edited, actor="admin")
    assert saved["published"]["title"] != "草稿"
    assert saved["draft"]["title"] == "草稿"
    with pytest.raises(PanoramaConflict):
        store.save_draft(initial["revision"], edited, actor="other")
    restarted = PanoramaStore(path).read()
    assert restarted == saved
    published = store.publish(saved["revision"], actor="admin")
    assert published["published"]["title"] == "草稿"
    assert published["draft"] is None
    assert published["previous"] == initial["published"]
    restored = store.restore(published["revision"], actor="owner")
    assert restored["published"]["title"] == initial["published"]["title"]
    assert restored["previous"]["title"] == "草稿"
    assert restored["published"]["version"] != initial["published"]["version"]


def test_store_records_actor_and_fails_closed_on_corruption(tmp_path):
    path = tmp_path / "panorama.sqlite3"
    store = PanoramaStore(path)
    state = store.read()
    store.save_draft(state["revision"], deepcopy(state["published"]), actor="actor-1")
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "select actor from panorama_history order by revision desc limit 1"
            ).fetchone()[0]
            == "actor-1"
        )
        connection.execute(
            "update panorama_state set published_json='{' where singleton=1"
        )
    with pytest.raises(PanoramaUnavailable):
        PanoramaStore(path).read()


def test_concurrent_writers_serialize_revision_check(tmp_path):
    path = tmp_path / "panorama.sqlite3"
    initial = PanoramaStore(path).read()
    barrier = Barrier(2)

    def save(title):
        data = deepcopy(initial["published"])
        data["title"] = title
        barrier.wait()
        return PanoramaStore(path).save_draft(initial["revision"], data, actor=title)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future
            for future in (executor.submit(save, "甲"), executor.submit(save, "乙"))
        ]
        outcomes = []
        for future in results:
            try:
                outcomes.append(future.result())
            except PanoramaConflict:
                outcomes.append("conflict")
    assert sum(result == "conflict" for result in outcomes) == 1
    state = PanoramaStore(path).read()
    assert state["revision"] == 1
    assert state["published"] == initial["published"]
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("select count(*) from panorama_history").fetchone()[0]
            == 1
        )


def test_svg_escapes_editor_content():
    from app.ai_engineering.exports import render_svg

    payload = deepcopy(PANORAMA_SEED)
    payload["nodes"][0]["title"] = '<script>alert("x")</script>'
    svg = render_svg(validate_panorama(payload)).decode()
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
