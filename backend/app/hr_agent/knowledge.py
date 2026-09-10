"""Immutable retained releases; the current pointer is only for new work."""

import json
import re
from pathlib import Path

from .resources import PublishedKnowledge
from .types import problem


class KnowledgeReleases:
    def __init__(self, directory):
        self.directory = Path(directory)
        # A1's explicit single-publication layout remains usable.
        self.single = (self.directory / "manifest.json").is_file()

    def _release(self, release_id):
        if (
            not isinstance(release_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", release_id)
            or release_id.lower() in ("current", "latest")
        ):
            raise problem("configuration_unavailable", http_status=503)
        path = (
            self.directory if self.single else self.directory / "releases" / release_id
        )
        if self.directory.is_symlink() or (
            not self.single and (self.directory / "releases").is_symlink()
        ):
            raise problem("configuration_unavailable", http_status=503)
        release = PublishedKnowledge(path)
        if release.release_id != release_id:
            raise problem("configuration_unavailable", http_status=503)
        return release

    def current(self):
        if self.single:
            return PublishedKnowledge(self.directory)
        try:
            pointer = self.directory / "current.json"
            if pointer.is_symlink():
                raise ValueError()
            value = json.loads(pointer.read_text())
            if set(value) != {"release_id"}:
                raise ValueError()
            return self._release(value["release_id"])
        except (OSError, ValueError, TypeError):
            raise problem("configuration_unavailable", http_status=503) from None

    def metadata(self):
        return self.current().metadata()

    def validate_record(self, record):
        release = self._release(record["knowledge_release"])
        if any(record[k] != v for k, v in release.metadata().items()):
            raise problem("configuration_unavailable", http_status=503)
        return release

    def exact(self, ref):
        """Resolve a selected historical public ref without substituting latest."""
        current = self.current()
        if any(item["ref"] == ref for item in current.items):
            current.read(ref)
            return current
        if not self.single:
            root = self.directory / "releases"
            if root.is_symlink():
                raise problem("configuration_unavailable", http_status=503)
            for path in sorted(root.iterdir()):
                if path.is_symlink() or not path.is_dir():
                    continue
                release = self._release(path.name)
                if any(item["ref"] == ref for item in release.items):
                    release.read(ref)
                    return release
        raise problem("reference_unavailable", http_status=410)

    def read(self, ref):
        return self.exact(ref).read(ref)

    def check(self):
        self.current().check()

    @property
    def items(self):
        return self.current().items

    @property
    def role(self):
        return self.current().role
