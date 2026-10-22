"""Freeze caller-declared SDK commit metadata without I/O or service imports."""

import copy
import hashlib
import json
import re
from datetime import datetime


_SCOPE = "eight-repositories-nine-positions-tracked-commits"
_POSITIONS = {
    ".": "OpenOrbbecSDKGroup",
    "FrameProcessor": "FrameProcessor",
    "OBEnhancedDepthFilter": "OBEnhancedDepthFilter",
    "OBFilterLib": "OBFilterLib",
    "OBSDKPrivFilterLib": "OBSDKPrivFilterLib",
    "OpenOrbbecSDK": "OpenOrbbecSDK",
    "OpenOrbbecSDKExt": "OpenOrbbecSDKExt",
    "OpenOrbbecViewer": "OpenOrbbecViewer",
    "OpenOrbbecViewer/third_party/OpenOrbbecSDK": "OpenOrbbecSDK",
}
_EDGES = (
    (".", "FrameProcessor", "FrameProcessor"),
    (".", "OBEnhancedDepthFilter", "OBEnhancedDepthFilter"),
    (".", "OBFilterLib", "OBFilterLib"),
    (".", "OBSDKPrivFilterLib", "OBSDKPrivFilterLib"),
    (".", "OpenOrbbecSDK", "OpenOrbbecSDK"),
    (".", "OpenOrbbecSDKExt", "OpenOrbbecSDKExt"),
    (".", "OpenOrbbecViewer", "OpenOrbbecViewer"),
    ("OpenOrbbecViewer", "third_party/OpenOrbbecSDK",
     "OpenOrbbecViewer/third_party/OpenOrbbecSDK"),
)
_GAPS = (
    "native_gitlab_project_ids_unverified",
    "source_tree_blob_bytes_not_independently_verified",
    "other_gitlinks_and_third_party_sources_not_frozen",
    "untracked_and_ignored_inputs_not_frozen",
    "copied_binary_dependencies_unknown",
    "effective_oem_and_build_parameters_unknown",
    "toolchain_and_environment_unknown",
    "ci_run_attempt_and_artifacts_not_collected",
    "business_acceptance_and_release_approval_not_collected",
)
_POSITION_FIELDS = (
    "path", "repo_ref", "availability", "observed_sha", "head_after",
    "tracked_porcelain", "untracked_paths", "ref_labels", "origin_url",
    "identity_state", "native_project_id",
)
_EDGE_FIELDS = (
    "parent_path", "parent_sha", "entry_path", "child_path", "mode", "object_type",
    "pinned_sha",
)
_SHA = re.compile(r"[0-9a-f]{40}")


class SourceSnapshotError(ValueError):
    """Malformed observation, before semantic quarantine can be evaluated."""

    code: str
    path: str

    def __init__(self, code: str, path: str) -> None:
        super().__init__(code)
        self.code, self.path = code, path


def _require(condition: bool, path: str) -> None:
    if not condition:
        raise SourceSnapshotError("observation_schema", path)


def _object_fields(value: object, fields: tuple, path: str) -> None:
    _require(type(value) is dict, path)
    for field in fields:
        _require(field in value, f"{path}.{field}")
    for field in value:
        _require(type(field) is str, path)
        _require(field in fields, f"{path}.{field}")


def _text(value: object, path: str, *, nonempty: bool = True) -> None:
    _require(type(value) is str, path)
    _require(not nonempty or bool(value), path)
    _require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value), path)


def _sha(value: object, path: str) -> None:
    _text(value, path)
    _require(_SHA.fullmatch(value) is not None, path)


def _check_shape(observation: dict) -> None:
    _object_fields(observation, (
        "schema_version", "observed_at", "scope", "collection", "positions", "gitlinks",
    ), "$")
    _text(observation["schema_version"], "$.schema_version")
    _require(observation["schema_version"] == "b07-s0-observation-1", "$.schema_version")
    _text(observation["observed_at"], "$.observed_at")
    try:
        observed_at = datetime.fromisoformat(observation["observed_at"])
        aware = observed_at.utcoffset() is not None
    except ValueError:
        aware = False
    _require(aware, "$.observed_at")
    _text(observation["scope"], "$.scope")
    _require(observation["scope"] == _SCOPE, "$.scope")

    collection = observation["collection"]
    _object_fields(collection, (
        "network_used", "untracked_content_read", "atomic", "object_check",
    ), "$.collection")
    for field in ("network_used", "untracked_content_read", "atomic"):
        _require(collection[field] is False, f"$.collection.{field}")
    _text(collection["object_check"], "$.collection.object_check")
    _require(collection["object_check"] == "commit_and_gitlink_metadata_only",
             "$.collection.object_check")

    positions = observation["positions"]
    _require(type(positions) is list, "$.positions")
    _require(len(positions) <= 18, "$.positions")
    for index, position in enumerate(positions):
        path = f"$.positions[{index}]"
        _object_fields(position, _POSITION_FIELDS, path)
        for field in ("path", "repo_ref", "availability"):
            _text(position[field], f"{path}.{field}")
        _require(position["availability"] in ("observed", "missing", "unavailable"),
                 f"{path}.availability")
        for field in ("observed_sha", "head_after"):
            if position[field] is not None:
                _sha(position[field], f"{path}.{field}")
            if position["availability"] == "missing":
                _require(position[field] is None, f"{path}.{field}")
        if position["tracked_porcelain"] is not None:
            _text(position["tracked_porcelain"], f"{path}.tracked_porcelain", nonempty=False)
        if position["availability"] == "missing":
            _require(position["tracked_porcelain"] is None, f"{path}.tracked_porcelain")
        for field in ("untracked_paths", "ref_labels"):
            values = position[field]
            _require(type(values) is list, f"{path}.{field}")
            _require(len(values) <= 1000, f"{path}.{field}")
            for item_index, value in enumerate(values):
                _text(value, f"{path}.{field}[{item_index}]")
        if position["origin_url"] is not None:
            _text(position["origin_url"], f"{path}.origin_url")
        _text(position["identity_state"], f"{path}.identity_state")
        _require(position["identity_state"] == "pending_registration", f"{path}.identity_state")
        _require(position["native_project_id"] is None, f"{path}.native_project_id")

    gitlinks = observation["gitlinks"]
    _require(type(gitlinks) is list, "$.gitlinks")
    _require(len(gitlinks) <= 16, "$.gitlinks")
    for index, edge in enumerate(gitlinks):
        path = f"$.gitlinks[{index}]"
        _object_fields(edge, _EDGE_FIELDS, path)
        for field in _EDGE_FIELDS:
            if field in ("parent_sha", "pinned_sha"):
                _sha(edge[field], f"{path}.{field}")
            else:
                _text(edge[field], f"{path}.{field}")


def freeze_source_snapshot(observation: dict) -> dict:
    """No I/O, no mutation; validate declared local observations only."""
    _check_shape(observation)
    issues = set()
    position_groups = {path: [] for path in _POSITIONS}
    for index, position in enumerate(observation["positions"]):
        path = position["path"]
        if path not in position_groups:
            issues.add((f"positions[{index}]", "unexpected_location"))
        else:
            position_groups[path].append(position)

    unique_positions = {}
    for path, group in position_groups.items():
        if not group:
            issues.add((path, "missing_location"))
            continue
        if len(group) > 1:
            issues.add((path, "duplicate_location"))
            continue
        position = unique_positions[path] = group[0]
        if position["repo_ref"] != _POSITIONS[path]:
            issues.add((path, "repository_identity_conflict"))
        if position["availability"] != "observed":
            issues.add((path, "location_" + position["availability"]))
            continue
        before, after = position["observed_sha"], position["head_after"]
        if before is None or after is None:
            issues.add((path, "head_not_observed"))
        elif before != after:
            issues.add((path, "head_changed_during_observation"))
        tracked = position["tracked_porcelain"]
        if tracked is None:
            issues.add((path, "tracked_status_unknown"))
        elif tracked:
            issues.add((path, "tracked_dirty"))

    edge_groups = {key: [] for key in _EDGES}
    for index, edge in enumerate(observation["gitlinks"]):
        key = (edge["parent_path"], edge["entry_path"], edge["child_path"])
        if key not in edge_groups:
            issues.add((f"gitlinks[{index}]", "unexpected_gitlink"))
        else:
            edge_groups[key].append(edge)

    unique_edges = []
    for (parent, entry, child), group in edge_groups.items():
        if not group:
            issues.add((child, "missing_gitlink"))
            continue
        if len(group) > 1:
            issues.add((child, "duplicate_gitlink"))
            continue
        edge = group[0]
        unique_edges.append(edge)
        if edge["mode"] != "160000" or edge["object_type"] != "commit":
            issues.add((child, "gitlink_type_mismatch"))
        parent_position = unique_positions.get(parent)
        if parent_position is not None and parent_position["observed_sha"] is not None:
            if edge["parent_sha"] != parent_position["observed_sha"]:
                issues.add((child, "gitlink_parent_mismatch"))
        child_position = unique_positions.get(child)
        if child_position is not None and child_position["observed_sha"] is not None:
            if edge["pinned_sha"] != child_position["observed_sha"]:
                issues.add((child, "gitlink_checkout_mismatch"))

    sorted_issues = [{"code": code, "path": path} for path, code in sorted(issues)]
    content = snapshot_id = None
    if not sorted_issues:
        content = {
            "schema_version": "b07-s0-source-1",
            "scope": _SCOPE,
            "positions": [
                {"path": path, "repo_ref": position["repo_ref"],
                 "commit_sha": position["observed_sha"]}
                for path, position in sorted(unique_positions.items())
            ],
            "gitlinks": copy.deepcopy(sorted(
                unique_edges, key=lambda edge: (
                    edge["parent_path"], edge["entry_path"], edge["child_path"],
                ),
            )),
        }
        canonical_bytes = json.dumps(content, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(",", ":")).encode("utf-8")
        snapshot_id = "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
    return {
        "schema_version": "b07-s0-report-1",
        "raw_observation": copy.deepcopy(observation),
        "identity_verification": "caller_supplied_repo_refs_not_native_gitlab_verified",
        "source_state": "quarantined" if sorted_issues else "frozen",
        "source_snapshot_id": snapshot_id,
        "source_content": content,
        "issues": sorted_issues,
        "gaps": list(_GAPS),
        "build_ready": False,
        "promotion_eligible": False,
    }
