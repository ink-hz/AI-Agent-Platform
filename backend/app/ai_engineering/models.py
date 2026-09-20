from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

ACTIONS = frozenset(
    {
        "brain",
        "agents",
        "missions",
        "sessions",
        "operations",
        "review",
        "activity",
        "identity",
        "governance",
        "access",
        "account",
        "agent-admin",
        "notes",
        "hr",
        "office",
        "voc",
        "fae",
    }
)
DOCUMENTS = frozenset(
    {"overview", "reading", "domains", "finance", "products", "assets"}
)
LAYER_KINDS = ("industry", "portfolio", "workflow", "support")
GROUP_ROLES = frozenset(
    {
        "upstream",
        "company",
        "downstream",
        "products",
        "technology",
        "marketing",
        "delivery",
        "support",
    }
)
EDGE_KINDS = frozenset({"supply", "supports", "feedback"})
ROLES_BY_LAYER = {
    "industry": frozenset({"upstream", "company", "downstream"}),
    "portfolio": frozenset({"products", "technology"}),
    "workflow": frozenset({"marketing", "delivery"}),
    "support": frozenset({"support"}),
}
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


class PanoramaValidationError(ValueError):
    pass


def _fail(message: str) -> None:
    raise PanoramaValidationError(message)


def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{name} has invalid fields")
    return value


def _text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value.strip())
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        _fail(f"{name} is invalid")
    return value


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(f"{name} is invalid")
    return value


def _list(value: Any, name: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail(f"{name} is invalid")
    return value


def validate_panorama(value: Any) -> dict[str, Any]:
    root = _exact(
        value,
        {"version", "updated_at", "title", "layers", "nodes", "edges", "sources"},
        "panorama",
    )
    _text(root["version"], "version", 96)
    _text(root["updated_at"], "updated_at", 64)
    _text(root["title"], "title", 120)
    layers = _list(root["layers"], "layers", 4)
    if len(layers) != 4:
        _fail("exactly four layers required")
    nodes = _list(root["nodes"], "nodes", 80)
    edges = _list(root["edges"], "edges", 240)
    sources = _list(root["sources"], "sources", 80)

    node_ids: set[str] = set()
    for node in nodes:
        item = _exact(
            node, {"id", "title", "subtitle", "detail", "actions", "source_ids"}, "node"
        )
        node_id = _id(item["id"], "node id")
        if node_id in node_ids:
            _fail("duplicate node id")
        node_ids.add(node_id)
        _text(item["title"], "node title", 80)
        _text(item["subtitle"], "node subtitle", 160, empty=True)
        details = _list(item["detail"], "node detail", 12)
        for detail in details:
            _text(detail, "node detail", 240)
        actions = _list(item["actions"], "node actions", len(ACTIONS))
        if (
            any(not isinstance(action, str) for action in actions)
            or len(actions) != len(set(actions))
            or any(action not in ACTIONS for action in actions)
        ):
            _fail("node actions are invalid")
        source_ids = _list(item["source_ids"], "node sources", 20)
        if any(not isinstance(source_id, str) for source_id in source_ids) or len(
            source_ids
        ) != len(set(source_ids)):
            _fail("duplicate node source")
        for source_id in source_ids:
            _id(source_id, "source id")

    layer_ids: set[str] = set()
    group_ids: set[str] = set()
    grouped: list[str] = []
    for index, layer in enumerate(layers):
        item = _exact(layer, {"id", "title", "kind", "groups"}, "layer")
        layer_id = _id(item["id"], "layer id")
        if (
            layer_id in layer_ids
            or not isinstance(item["kind"], str)
            or item["kind"] not in LAYER_KINDS
        ):
            _fail("layer identity or order is invalid")
        layer_ids.add(layer_id)
        _text(item["title"], "layer title", 80)
        groups = _list(item["groups"], "groups", 12)
        if not groups:
            _fail("layer requires a group")
        layer_roles: list[str] = []
        for group in groups:
            entry = _exact(
                group, {"id", "title", "role", "columns", "node_ids"}, "group"
            )
            group_id = _id(entry["id"], "group id")
            if (
                group_id in group_ids
                or not isinstance(entry["role"], str)
                or entry["role"] not in GROUP_ROLES
            ):
                _fail("group identity or role is invalid")
            group_ids.add(group_id)
            layer_roles.append(entry["role"])
            _text(entry["title"], "group title", 80)
            if (
                isinstance(entry["columns"], bool)
                or not isinstance(entry["columns"], int)
                or not 1 <= entry["columns"] <= 8
            ):
                _fail("group columns is invalid")
            members = _list(entry["node_ids"], "group nodes", 40)
            if (
                any(not isinstance(member, str) for member in members)
                or len(members) != len(set(members))
                or any(member not in node_ids for member in members)
            ):
                _fail("group nodes are invalid")
            grouped.extend(members)
        if (
            len(layer_roles) != len(set(layer_roles))
            or set(layer_roles) != ROLES_BY_LAYER[item["kind"]]
        ):
            _fail("layer group roles are invalid")
    if {layer["kind"] for layer in layers} != set(LAYER_KINDS):
        _fail("all four layer kinds are required")
    if len(grouped) != len(set(grouped)) or set(grouped) != node_ids:
        _fail("every node must belong to exactly one group")

    source_ids: set[str] = set()
    for source in sources:
        item = _exact(source, {"id", "label", "document"}, "source")
        source_id = _id(item["id"], "source id")
        if (
            source_id in source_ids
            or not isinstance(item["document"], str)
            or item["document"] not in DOCUMENTS
        ):
            _fail("source identity or document is invalid")
        source_ids.add(source_id)
        _text(item["label"], "source label", 160)
    for node in nodes:
        if any(source_id not in source_ids for source_id in node["source_ids"]):
            _fail("node references missing source")

    edge_ids: set[str] = set()
    for edge in edges:
        item = _exact(edge, {"id", "from", "to", "kind", "label"}, "edge")
        edge_id = _id(item["id"], "edge id")
        from_id = _id(item["from"], "edge from")
        to_id = _id(item["to"], "edge to")
        if (
            edge_id in edge_ids
            or not isinstance(item["kind"], str)
            or item["kind"] not in EDGE_KINDS
        ):
            _fail("edge identity or kind is invalid")
        edge_ids.add(edge_id)
        if from_id not in node_ids or to_id not in node_ids or from_id == to_id:
            _fail("edge endpoint is invalid")
        _text(item["label"], "edge label", 100, empty=True)
    return deepcopy(root)
