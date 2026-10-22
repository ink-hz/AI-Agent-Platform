"""Validate a content release once and serve one immutable reading snapshot."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
from pathlib import Path, PurePosixPath


DOCUMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"knowledge", "process", "analysis", "design", "acceptance", "execution"}
_SUMMARY_FIELDS = ("id", "title", "kind", "group", "level", "baseline_id", "content_state",
                   "runtime_validation", "source_path", "source_sha256", "review_status")
_GROUP_TITLES = {
    "00-navigation": "阅读导航", "01-system": "系统架构", "02-public-api": "公共API",
    "03-runtime": "运行时", "04-pipeline": "Pipeline", "05-device": "设备",
    "06-platform": "平台适配", "07-frame-stream": "帧与数据流", "08-processing": "处理与滤波",
    "09-control": "设备控制", "10-media": "录制与回放", "11-extensions": "扩展",
    "12-viewer": "Viewer", "13-engineering": "工程", "14-scenarios": "场景与排障",
    "15-reference": "精确参考", "16-evidence": "证据与维护", "_meta": "机器索引与语义证据",
    "root": "知识库总览", "process": "开发流程",
    "analysis": "现状分析", "design": "平台设计", "acceptance": "验收证据", "execution": "执行记录",
}


class ContentUnavailable(ValueError):
    """A release is absent or fails validation; carries no filesystem detail."""


def _require(condition: bool) -> None:
    if not condition:
        raise ContentUnavailable("sdk_admin_content_unavailable")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _relative_path(value) -> bool:
    return (_text(value) and "\\" not in value and not PurePosixPath(value).is_absolute()
            and ".." not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _read_at(directory: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        return stream.read()


def _open_root(root: Path) -> int:
    """Walk with directory descriptors so symlink parents cannot redirect reads."""
    path = root.absolute()
    _require(".." not in path.parts)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_json_constant(value):
    raise ContentUnavailable("sdk_admin_content_unavailable")


class SdkAdminRepository:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._snapshot = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot
            try:
                directory = _open_root(self.root)
                try:
                    manifest_bytes = _read_at(directory, "manifest.json")
                    declared_hash = _read_at(directory, "manifest.sha256").decode("ascii").strip()
                    digest = hashlib.sha256(manifest_bytes).hexdigest()
                    _require(bool(_SHA256.fullmatch(declared_hash)) and digest == declared_hash)
                    manifest = json.loads(manifest_bytes, object_pairs_hook=_json_object,
                                          parse_constant=_invalid_json_constant)
                    self._validate_manifest(manifest)
                    documents_directory = os.open("documents", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        documents = {}
                        searchable = {}
                        for row in manifest["documents"]:
                            self._validate_document(row)
                            document_id = row["id"]
                            _require(document_id not in documents)
                            body = _read_at(documents_directory, f"{document_id}.md")
                            _require(hashlib.sha256(body).hexdigest() == row["sha256"])
                            markdown = body.decode("utf-8")
                            summary = {key: row[key] for key in _SUMMARY_FIELDS}
                            documents[document_id] = {**summary, "markdown": markdown, "sha256": row["sha256"],
                                                      "conditions": row["conditions"], "links": row["links"]}
                            searchable[document_id] = _normalized("\n".join((document_id, row["title"], markdown)))
                        for row in documents.values():
                            _require(all(link["document_id"] in documents for link in row["links"].values()))
                        _require(all(document_id in documents for document_id in manifest["featured_ids"]))
                    finally:
                        os.close(documents_directory)
                finally:
                    os.close(directory)
                self._snapshot = (manifest, digest, documents, searchable)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                raise ContentUnavailable("sdk_admin_content_unavailable") from None
            return self._snapshot

    @staticmethod
    def _validate_manifest(manifest):
        _require(isinstance(manifest, dict) and type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1)
        _require(all(_text(manifest.get(key)) for key in ("release_id", "baseline_id", "generated_at")))
        _require(isinstance(manifest.get("documents"), list) and isinstance(manifest.get("sources"), list))
        _require(isinstance(manifest.get("featured_ids"), list)
                 and all(isinstance(item, str) and DOCUMENT_ID.fullmatch(item) for item in manifest["featured_ids"]))
        _require(isinstance(manifest.get("ai"), dict) and manifest["ai"].get("available") is False
                 and _text(manifest["ai"].get("reason")))
        source_ids = set()
        for source in manifest["sources"]:
            _require(isinstance(source, dict) and all(_text(source.get(key)) for key in ("id", "label", "state", "description")))
            _require(source["id"] not in source_ids)
            source_ids.add(source["id"])
            _require("href" not in source or isinstance(source["href"], str))

    @staticmethod
    def _validate_document(row):
        _require(isinstance(row, dict))
        _require(all(key in row for key in _SUMMARY_FIELDS))
        _require(isinstance(row["id"], str) and bool(DOCUMENT_ID.fullmatch(row["id"])))
        _require(all(_text(row[key]) for key in ("title", "kind", "group", "content_state", "runtime_validation", "review_status")))
        _require(row["kind"] in _KINDS and _relative_path(row["source_path"]))
        _require(all(row[key] is None or isinstance(row[key], str) for key in ("level", "baseline_id")))
        _require(row.get("file") == f'documents/{row["id"]}.md')
        _require(all(isinstance(row.get(key), str) and _SHA256.fullmatch(row[key]) for key in ("source_sha256", "sha256")))
        _require(isinstance(row.get("conditions"), dict) and isinstance(row.get("links"), dict))
        for href, link in row["links"].items():
            _require(isinstance(href, str) and isinstance(link, dict) and isinstance(link.get("document_id"), str)
                     and bool(DOCUMENT_ID.fullmatch(link["document_id"])) and isinstance(link.get("fragment"), str))

    def catalog(self) -> dict:
        manifest, digest, documents, _ = self._load()
        groups = {}
        for row in documents.values():
            groups[row["group"]] = groups.get(row["group"], 0) + 1
        navigation = sum(row["kind"] == "knowledge" and row["content_state"] == "navigation" for row in documents.values())
        knowledge = sum(row["kind"] == "knowledge" for row in documents.values())
        sources = [{key: row[key] for key in ("id", "label", "state", "description", "href") if key in row}
                   for row in manifest["sources"]]
        return copy.deepcopy({key: manifest[key] for key in ("release_id", "baseline_id", "generated_at", "featured_ids")} | {
            "manifest_sha256": digest, "counts": {"knowledge_pages": knowledge - navigation,
            "knowledge_navigation": navigation, "documents": len(documents)},
            "groups": [{"id": group, "title": _GROUP_TITLES.get(group, f"知识专题（{group}）"), "count": groups[group]} for group in sorted(groups)],
            "sources": sources, "ai": {"available": False, "reason": manifest["ai"]["reason"]}})

    def documents(self, *, q: str = "", kind: str | None = None, group: str | None = None,
                  offset: int = 0, limit: int = 30) -> dict:
        manifest, _, documents, searchable = self._load()
        query = _normalized(q.strip())
        matched = [row for document_id, row in sorted(documents.items())
                   if (not kind or row["kind"] == kind) and (not group or row["group"] == group)
                   and (not query or query in searchable[document_id])]
        return copy.deepcopy({"release_id": manifest["release_id"], "items": [
            {key: row[key] for key in _SUMMARY_FIELDS} for row in matched[offset:offset + limit]],
            "total": len(matched), "offset": offset, "limit": limit})

    def document(self, document_id: str) -> dict | None:
        _, _, documents, _ = self._load()
        return copy.deepcopy(documents.get(document_id))
