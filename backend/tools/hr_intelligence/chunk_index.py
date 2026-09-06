from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_EMBEDDED_SHA256 = re.compile(r"(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])")
_MAX_CHUNK_BYTES = 24 * 1024
_TASK_KINDS = {
    "jd-jr": ("jd", "jr"),
    "talent-profile": ("talent_profile",),
    "sourcing": ("sourcing_strategy",),
    "resume-review": ("candidate_match",),
    "interview": ("position_interview_plan", "candidate_interview_plan"),
}


class ChunkIndexError(ValueError):
    pass


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        sorted(
            {
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            }
        )
    )


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    chunk_id: str
    path: str
    heading: str
    byte_start: int
    byte_end: int
    sha256: str
    scope: str
    scope_key: str
    companies: tuple[str, ...] = ()
    tracks: tuple[str, ...] = ()
    job_families: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()
    secondary_directions: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    task_kinds: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    priority: int = 0

    def __post_init__(self) -> None:
        relative = PurePosixPath(self.path)
        if (
            not self.chunk_id
            or relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != "agent"
            or ".." in relative.parts
            or not self.path.endswith(".md")
            or not self.heading.strip()
            or isinstance(self.byte_start, bool)
            or isinstance(self.byte_end, bool)
            or not isinstance(self.byte_start, int)
            or not isinstance(self.byte_end, int)
            or self.byte_start < 0
            or self.byte_end <= self.byte_start
            or self.byte_end - self.byte_start > _MAX_CHUNK_BYTES
            or _SHA256.fullmatch(self.sha256) is None
            or isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
        ):
            raise ChunkIndexError("Markdown chunk invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "path": self.path,
            "heading": self.heading,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "sha256": self.sha256,
            "scope": self.scope,
            "scope_key": self.scope_key,
            "companies": list(self.companies),
            "tracks": list(self.tracks),
            "job_families": list(self.job_families),
            "directions": list(self.directions),
            "secondary_directions": list(self.secondary_directions),
            "locations": list(self.locations),
            "seniority": list(self.seniority),
            "skills": list(self.skills),
            "task_kinds": list(self.task_kinds),
            "evidence_ids": list(self.evidence_ids),
            "priority": self.priority,
        }


def _scope(path: str) -> tuple[str, str, int, tuple[str, ...]]:
    relative = PurePosixPath(path)
    stem = relative.stem
    if path == "agent/index.md":
        return "index", "usage-boundary", 1000, ()
    if path == "agent/executive-brief.md":
        return "executive", "all-companies", 100, ()
    parent = relative.parent.name
    if parent == "companies":
        return "company", stem, 200, ()
    if parent == "directions":
        return "direction", stem, 150, ()
    if parent == "topics":
        return "topic", stem, 140, ()
    if parent == "tasks":
        return "task", stem, 180, _TASK_KINDS.get(stem, ())
    raise ChunkIndexError("Markdown document path invalid")


def _ranges(body: bytes) -> tuple[tuple[int, int, str], ...]:
    if not body or b"\0" in body or b"\r" in body:
        raise ChunkIndexError("Markdown document encoding invalid")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ChunkIndexError("Markdown document encoding invalid") from None
    starts: list[tuple[int, str]] = []
    cursor = 0
    title = "文档说明"
    for line in text.splitlines(keepends=True):
        if line.startswith("# ") and title == "文档说明":
            title = line[2:].strip()
        if line.startswith("## "):
            starts.append((cursor, line[3:].strip()))
        cursor += len(line.encode("utf-8"))
    boundaries = [(0, title), *starts]
    deduplicated: list[tuple[int, str]] = []
    for start, heading in boundaries:
        if deduplicated and deduplicated[-1][0] == start:
            deduplicated[-1] = (start, heading)
        else:
            deduplicated.append((start, heading))
    return tuple(
        (
            start,
            deduplicated[index + 1][0]
            if index + 1 < len(deduplicated)
            else len(body),
            heading,
        )
        for index, (start, heading) in enumerate(deduplicated)
    )


def build_chunk_index(
    files: Mapping[str, bytes],
    routing: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[MarkdownChunk, ...]:
    if not isinstance(files, Mapping) or not files:
        raise ChunkIndexError("Markdown documents required")
    selected_routing = routing or {}
    chunks: list[MarkdownChunk] = []
    for path in sorted(name for name in files if name.endswith(".md")):
        body = files[path]
        if not isinstance(body, bytes):
            raise ChunkIndexError("Markdown document body invalid")
        scope, scope_key, priority, task_kinds = _scope(path)
        metadata = selected_routing.get(path, {})
        if not isinstance(metadata, Mapping):
            raise ChunkIndexError("Markdown routing metadata invalid")
        for start, end, heading in _ranges(body):
            selected = body[start:end]
            sha256 = hashlib.sha256(selected).hexdigest()
            identity = hashlib.sha256(
                f"{path}\0{heading}\0{sha256}".encode("utf-8")
            ).hexdigest()[:24]
            evidence = tuple(
                sorted(set(_EMBEDDED_SHA256.findall(selected.decode("utf-8"))))
            )
            chunks.append(
                MarkdownChunk(
                    chunk_id=f"md-{identity}",
                    path=path,
                    heading=heading,
                    byte_start=start,
                    byte_end=end,
                    sha256=sha256,
                    scope=str(metadata.get("scope", scope)),
                    scope_key=str(metadata.get("scope_key", scope_key)),
                    companies=_strings(metadata.get("companies")),
                    tracks=_strings(metadata.get("tracks")),
                    job_families=_strings(metadata.get("job_families")),
                    directions=_strings(metadata.get("directions")),
                    secondary_directions=_strings(
                        metadata.get("secondary_directions")
                    ),
                    locations=_strings(metadata.get("locations")),
                    seniority=_strings(metadata.get("seniority")),
                    skills=_strings(metadata.get("skills")),
                    task_kinds=_strings(metadata.get("task_kinds")) or task_kinds,
                    evidence_ids=evidence,
                    priority=int(metadata.get("priority", priority)),
                )
            )
    return tuple(chunks)


def validate_chunk_index(
    files: Mapping[str, bytes], chunks: tuple[MarkdownChunk, ...]
) -> None:
    if not isinstance(files, Mapping) or not isinstance(chunks, tuple) or not chunks:
        raise ChunkIndexError("Markdown chunk index invalid")
    markdown_paths = {path for path in files if path.endswith(".md")}
    if any(
        not isinstance(path, str) or not isinstance(body, bytes)
        for path, body in files.items()
    ):
        raise ChunkIndexError("Markdown files invalid")
    if {chunk.path for chunk in chunks} != markdown_paths:
        raise ChunkIndexError("Markdown chunk coverage invalid")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ChunkIndexError("Markdown chunk identity invalid")
    by_path: dict[str, list[MarkdownChunk]] = defaultdict(list)
    for chunk in chunks:
        if not isinstance(chunk, MarkdownChunk):
            raise ChunkIndexError("Markdown chunk invalid")
        body = files.get(chunk.path)
        if body is None or chunk.byte_end > len(body):
            raise ChunkIndexError("Markdown chunk bounds invalid")
        selected = body[chunk.byte_start : chunk.byte_end]
        if hashlib.sha256(selected).hexdigest() != chunk.sha256:
            raise ChunkIndexError("Markdown chunk checksum mismatch")
        by_path[chunk.path].append(chunk)
    for path, selected in by_path.items():
        ordered = sorted(selected, key=lambda item: item.byte_start)
        cursor = 0
        for chunk in ordered:
            if chunk.byte_start != cursor:
                raise ChunkIndexError("Markdown chunk coverage invalid")
            cursor = chunk.byte_end
        if cursor != len(files[path]):
            raise ChunkIndexError("Markdown chunk coverage invalid")


__all__ = [
    "ChunkIndexError",
    "MarkdownChunk",
    "build_chunk_index",
    "validate_chunk_index",
]
