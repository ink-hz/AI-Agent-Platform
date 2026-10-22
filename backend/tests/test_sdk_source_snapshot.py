"""Offline source snapshot contract; no app factory or service fixtures."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.sdk_workbench.source_snapshot import SourceSnapshotError, freeze_source_snapshot


POSITIONS = [
    (".", "OpenOrbbecSDKGroup"),
    ("FrameProcessor", "FrameProcessor"),
    ("OBEnhancedDepthFilter", "OBEnhancedDepthFilter"),
    ("OBFilterLib", "OBFilterLib"),
    ("OBSDKPrivFilterLib", "OBSDKPrivFilterLib"),
    ("OpenOrbbecSDK", "OpenOrbbecSDK"),
    ("OpenOrbbecSDKExt", "OpenOrbbecSDKExt"),
    ("OpenOrbbecViewer", "OpenOrbbecViewer"),
    ("OpenOrbbecViewer/third_party/OpenOrbbecSDK", "OpenOrbbecSDK"),
]


def valid_observation():
    positions = [
        dict(path=path, repo_ref=repo, availability="observed",
             observed_sha=format(i + 1, "040x"), head_after=format(i + 1, "040x"),
             tracked_porcelain="", untracked_paths=[], ref_labels=[], origin_url=None,
             identity_state="pending_registration", native_project_id=None)
        for i, (path, repo) in enumerate(POSITIONS)
    ]
    by_path = {p["path"]: p for p in positions}
    edges = []
    for child, _ in POSITIONS[1:]:
        parent = "OpenOrbbecViewer" if "/" in child else "."
        entry = "third_party/OpenOrbbecSDK" if "/" in child else child
        edges.append(dict(parent_path=parent, parent_sha=by_path[parent]["observed_sha"],
                          entry_path=entry, child_path=child, mode="160000",
                          object_type="commit", pinned_sha=by_path[child]["observed_sha"]))
    return dict(schema_version="b07-s0-observation-1", observed_at="2026-10-22T15:00:00+08:00",
                scope="eight-repositories-nine-positions-tracked-commits",
                collection=dict(network_used=False, untracked_content_read=False, atomic=False,
                                object_check="commit_and_gitlink_metadata_only"),
                positions=positions, gitlinks=edges)


def independent_id(content):
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_fixed_scope_allows_two_sdk_commits():
    original = valid_observation()
    before = copy.deepcopy(original)
    report = freeze_source_snapshot(original)
    assert report["source_state"] == "frozen"
    assert report["issues"] == []
    assert len(report["source_content"]["positions"]) == 9
    assert len({p["repo_ref"] for p in report["source_content"]["positions"]}) == 8
    assert report["source_snapshot_id"] == independent_id(report["source_content"])
    assert report["build_ready"] is False and report["promotion_eligible"] is False
    assert original == before
    report["raw_observation"]["positions"].clear()
    assert original == before


@pytest.mark.parametrize("change", ["order", "time", "labels", "origin", "untracked"])
def test_metadata_and_order_do_not_change_identity(change):
    original = valid_observation()
    altered = copy.deepcopy(original)
    if change == "order":
        altered["positions"].reverse()
        altered["gitlinks"].reverse()
    elif change == "time":
        altered["observed_at"] = "2026-10-23T02:00:00Z"
    elif change == "labels":
        altered["positions"][0]["ref_labels"] = ["refs/tags/版本", "refs/heads/main"]
    elif change == "origin":
        altered["positions"][0]["origin_url"] = "ssh://alias/group.git"
    else:
        altered["positions"][0]["untracked_paths"] = ["docs/", "unknown.bin"]
    report = freeze_source_snapshot(altered)
    assert report["issues"] == []
    assert report["raw_observation"] == altered
    assert report["source_snapshot_id"] == freeze_source_snapshot(original)["source_snapshot_id"]


def test_commit_and_matching_parent_pin_change_identity():
    observation = valid_observation()
    before = freeze_source_snapshot(observation)["source_snapshot_id"]
    observation["positions"][1]["observed_sha"] = "a" * 40
    observation["positions"][1]["head_after"] = "a" * 40
    observation["gitlinks"][0]["pinned_sha"] = "a" * 40
    report = freeze_source_snapshot(observation)
    assert report["source_state"] == "frozen"
    assert report["source_snapshot_id"] != before
    assert report["source_snapshot_id"] == independent_id(report["source_content"])


@pytest.mark.parametrize("change,code,path", [
    ("delete_position", "missing_location", "FrameProcessor"),
    ("missing", "location_missing", "FrameProcessor"),
    ("unavailable", "location_unavailable", "FrameProcessor"),
    ("unavailable_partial", "location_unavailable", "FrameProcessor"),
    ("observed_sha_null", "head_not_observed", "FrameProcessor"),
    ("head_after_null", "head_not_observed", "FrameProcessor"),
    ("tracked_null", "tracked_status_unknown", "FrameProcessor"),
    ("position_duplicate", "duplicate_location", "FrameProcessor"),
    ("position_conflicting_duplicate", "duplicate_location", "FrameProcessor"),
    ("repo", "repository_identity_conflict", "FrameProcessor"),
    ("unknown_position", "unexpected_location", "positions[9]"),
    ("delete_edge", "missing_gitlink", "FrameProcessor"),
    ("edge_duplicate", "duplicate_gitlink", "FrameProcessor"),
    ("edge_conflicting_duplicate", "duplicate_gitlink", "FrameProcessor"),
    ("unknown_edge", "unexpected_gitlink", "gitlinks[8]"),
    ("mode", "gitlink_type_mismatch", "FrameProcessor"),
    ("object_type", "gitlink_type_mismatch", "FrameProcessor"),
    ("parent_sha", "gitlink_parent_mismatch", "FrameProcessor"),
    ("pinned_sha", "gitlink_checkout_mismatch", "FrameProcessor"),
    ("dirty", "tracked_dirty", "FrameProcessor"),
    ("drift", "head_changed_during_observation", "FrameProcessor"),
])
def test_semantic_conflicts_are_quarantined_without_losing_raw(change, code, path):
    observation = valid_observation()
    position, edge = observation["positions"][1], observation["gitlinks"][0]
    if change == "delete_position":
        del observation["positions"][1]
    elif change in ("missing", "unavailable", "unavailable_partial"):
        position["availability"] = "missing" if change == "missing" else "unavailable"
        position["head_after"] = None
        if change != "unavailable_partial":
            position["observed_sha"] = position["tracked_porcelain"] = None
    elif change.endswith("_null"):
        position[{"observed_sha_null": "observed_sha", "head_after_null": "head_after",
                  "tracked_null": "tracked_porcelain"}[change]] = None
    elif change.startswith("position_"):
        duplicate = copy.deepcopy(position)
        if change == "position_conflicting_duplicate":
            duplicate["repo_ref"] = "wrong"
            duplicate["observed_sha"] = "f" * 40
        observation["positions"].append(duplicate)
    elif change == "repo":
        position["repo_ref"] = "wrong"
    elif change == "unknown_position":
        extra = copy.deepcopy(position)
        extra["path"] = "unknown"
        observation["positions"].append(extra)
    elif change == "delete_edge":
        del observation["gitlinks"][0]
    elif change.startswith("edge_"):
        duplicate = copy.deepcopy(edge)
        if change == "edge_conflicting_duplicate":
            duplicate["pinned_sha"] = "f" * 40
            duplicate["mode"] = "100644"
        observation["gitlinks"].append(duplicate)
    elif change == "unknown_edge":
        extra = copy.deepcopy(edge)
        extra["entry_path"] = "unknown"
        observation["gitlinks"].append(extra)
    elif change in ("mode", "object_type"):
        edge[change] = "wrong"
    elif change in ("parent_sha", "pinned_sha"):
        edge[change] = "f" * 40
    elif change == "dirty":
        position["tracked_porcelain"] = " M source.cpp"
    elif change == "drift":
        position["head_after"] = "f" * 40
    before = copy.deepcopy(observation)
    report = freeze_source_snapshot(observation)
    assert report["issues"] == [{"code": code, "path": path}]
    assert report["source_state"] == "quarantined"
    assert report["source_snapshot_id"] is None and report["source_content"] is None
    assert report["build_ready"] is False and report["promotion_eligible"] is False
    assert observation == before and report["raw_observation"] == before


def test_unavailable_position_with_two_observed_heads_reports_drift():
    observation = valid_observation()
    position = observation["positions"][1]
    position["availability"] = "unavailable"
    position["head_after"] = "f" * 40
    before = copy.deepcopy(observation)

    report = freeze_source_snapshot(observation)

    assert report["issues"] == [
        {"code": "head_changed_during_observation", "path": "FrameProcessor"},
        {"code": "location_unavailable", "path": "FrameProcessor"},
    ]
    assert report["source_state"] == "quarantined"
    assert report["raw_observation"] == before
    assert report["raw_observation"]["positions"][1]["availability"] == "unavailable"
    assert (
        report["raw_observation"]["positions"][1]["observed_sha"]
        == before["positions"][1]["observed_sha"]
    )
    assert report["raw_observation"]["positions"][1]["head_after"] == "f" * 40
    assert report["source_content"] is None and report["source_snapshot_id"] is None
    assert report["build_ready"] is False and report["promotion_eligible"] is False
    assert observation == before


def test_duplicate_positions_do_not_supply_parent_or_child_comparisons():
    observation = valid_observation()
    observation["positions"].append(copy.deepcopy(observation["positions"][0]))
    observation["positions"].append(copy.deepcopy(observation["positions"][1]))
    observation["gitlinks"][0]["parent_sha"] = "f" * 40
    observation["gitlinks"][0]["pinned_sha"] = "f" * 40
    observation["positions"][2]["tracked_porcelain"] = " M other.cpp"
    report = freeze_source_snapshot(observation)
    assert report["issues"] == [
        {"code": "duplicate_location", "path": "."},
        {"code": "duplicate_location", "path": "FrameProcessor"},
        {"code": "tracked_dirty", "path": "OBEnhancedDepthFilter"},
    ]


def test_multiple_issues_sort_and_deduplicate():
    observation = valid_observation()
    observation["positions"][1].update(observed_sha=None, head_after=None, tracked_porcelain=None)
    observation["positions"][2]["tracked_porcelain"] = " M x"
    observation["gitlinks"][0].update(mode="wrong", object_type="wrong")
    assert freeze_source_snapshot(observation)["issues"] == [
        {"code": "gitlink_type_mismatch", "path": "FrameProcessor"},
        {"code": "head_not_observed", "path": "FrameProcessor"},
        {"code": "tracked_status_unknown", "path": "FrameProcessor"},
        {"code": "tracked_dirty", "path": "OBEnhancedDepthFilter"},
    ]


@pytest.mark.parametrize("change,path", [
    ("top_list", "$"), ("missing_key", "$.observed_at"), ("unknown_key", "$.extra"),
    ("uppercase_sha", "$.positions[1].observed_sha"),
    ("short_sha", "$.positions[1].observed_sha"),
    ("bool_sha", "$.positions[1].observed_sha"),
    ("availability", "$.positions[1].availability"),
    ("project_id", "$.positions[1].native_project_id"),
    ("identity", "$.positions[1].identity_state"),
    ("naive_time", "$.observed_at"), ("invalid_time", "$.observed_at"),
    ("surrogate", "$.positions[1].origin_url"),
    ("too_many_positions", "$.positions"), ("too_many_edges", "$.gitlinks"),
    ("missing_nonnull", "$.positions[1].observed_sha"),
    ("collection_network_used", "$.collection.network_used"),
    ("collection_untracked_content_read", "$.collection.untracked_content_read"),
    ("collection_atomic", "$.collection.atomic"),
    ("positions_tuple", "$.positions"), ("position_unknown", "$.positions[1].extra"),
    ("empty_path", "$.positions[1].path"),
    ("empty_untracked", "$.positions[1].untracked_paths[0]"),
    ("many_labels", "$.positions[1].ref_labels"),
    ("edge_null_sha", "$.gitlinks[0].parent_sha"),
])
def test_shape_errors_have_exact_code_and_json_path(change, path):
    observation = valid_observation()
    position = observation["positions"][1]
    if change == "top_list":
        observation = []
    elif change == "missing_key":
        del observation["observed_at"]
    elif change == "unknown_key":
        observation["extra"] = None
    elif change in ("uppercase_sha", "short_sha", "bool_sha"):
        position["observed_sha"] = {"uppercase_sha": "A" * 40, "short_sha": "a" * 39,
                                    "bool_sha": True}[change]
    elif change == "availability":
        position["availability"] = "unknown"
    elif change == "project_id":
        position["native_project_id"] = 123
    elif change == "identity":
        position["identity_state"] = "verified"
    elif change in ("naive_time", "invalid_time"):
        observation["observed_at"] = "2026-10-22T12:00:00" if change == "naive_time" else "bad"
    elif change == "surrogate":
        position["origin_url"] = "\ud800"
    elif change == "too_many_positions":
        observation["positions"] *= 3
    elif change == "too_many_edges":
        observation["gitlinks"] *= 3
    elif change == "missing_nonnull":
        position["availability"] = "missing"
    elif change.startswith("collection_"):
        observation["collection"][change.removeprefix("collection_")] = 0
    elif change == "positions_tuple":
        observation["positions"] = tuple(observation["positions"])
    elif change == "position_unknown":
        position["extra"] = None
    elif change == "empty_path":
        position["path"] = ""
    elif change == "empty_untracked":
        position["untracked_paths"] = [""]
    elif change == "many_labels":
        position["ref_labels"] = ["label"] * 1001
    elif change == "edge_null_sha":
        observation["gitlinks"][0]["parent_sha"] = None
    before = copy.deepcopy(observation)
    with pytest.raises(SourceSnapshotError) as caught:
        freeze_source_snapshot(observation)
    assert caught.value.code == "observation_schema"
    assert caught.value.path == path
    assert str(caught.value) == "observation_schema"
    assert observation == before


def test_report_shape_and_no_mutable_sharing():
    observation = valid_observation()
    report = freeze_source_snapshot(observation)
    assert set(report) == {"schema_version", "raw_observation", "identity_verification",
                           "source_state", "source_snapshot_id", "source_content", "issues",
                           "gaps", "build_ready", "promotion_eligible"}
    assert report["schema_version"] == "b07-s0-report-1"
    assert report["identity_verification"] == "caller_supplied_repo_refs_not_native_gitlab_verified"
    assert report["source_content"]["schema_version"] == "b07-s0-source-1"
    assert all(set(p) == {"path", "repo_ref", "commit_sha"}
               for p in report["source_content"]["positions"])
    report["source_content"]["gitlinks"][0]["mode"] = "changed"
    assert observation["gitlinks"][0]["mode"] == "160000"
    assert report["raw_observation"]["gitlinks"][0]["mode"] == "160000"
    report["gaps"].clear()
    assert len(freeze_source_snapshot(observation)["gaps"]) == 9


def test_explicit_real_observation():
    explicit_path = os.environ.get("SDK_SOURCE_OBSERVATION")
    if not explicit_path:
        pytest.fail("SDK_SOURCE_OBSERVATION must explicitly name the real observation")
    path = Path(explicit_path)
    if not path.is_file():
        pytest.fail("SDK_SOURCE_OBSERVATION does not name an existing file")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "761abd73b1a0bbdbe20a4e3ddf3267dbc10747157ee46c22dea388336f153444"
    observation = json.loads(raw)
    report = freeze_source_snapshot(observation)
    assert report["raw_observation"] == observation
    assert len(observation["positions"]) == 9
    assert len({p["repo_ref"] for p in observation["positions"]}) == 8
    assert len(observation["gitlinks"]) == 8
    assert report["source_state"] == "frozen" and report["issues"] == []
    assert next(p for p in report["raw_observation"]["positions"] if p["path"] == ".")["untracked_paths"] == ["docs/"]
    assert report["source_snapshot_id"] == independent_id(report["source_content"])
    assert report["build_ready"] is False and report["promotion_eligible"] is False


def test_real_observation_without_explicit_environment_fails(monkeypatch):
    monkeypatch.delenv("SDK_SOURCE_OBSERVATION", raising=False)
    with pytest.raises(pytest.fail.Exception, match="must explicitly name"):
        test_explicit_real_observation()
