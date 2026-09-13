"""Read-only live dependencies for an already assembled HR process.

No model call, migration, admission, configuration reload or shared API restart.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from .config import check_schema_ready, load_hr_agent_settings
from .knowledge import KnowledgeReleases


def unavailable(blocker="assembly_unavailable"):
    return {"ready": False, "new_admission_enabled": False, "phase": None,
            "blockers": [blocker], "checked_at": datetime.now(timezone.utc).isoformat()}


class _BoundedConnection:
    """Enforce an aggregate query budget even across schema readiness queries."""
    def __init__(self, connection):
        self.connection = connection
        self.deadline = monotonic() + 3

    def execute(self, query, *args, **kwargs):
        remaining = int((self.deadline - monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError("readiness deadline")
        self.connection.execute("select set_config('statement_timeout',%s,true)",
                                (str(min(remaining, 1500)),))
        return self.connection.execute(query, *args, **kwargs)


def _file_digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("configuration identity unavailable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HrReadiness:
    def __init__(self, settings, environment, connection_factory, *, role="api", assembled=True):
        self.settings = settings
        self.environment = dict(environment)
        self.connect = connection_factory
        self.role = role
        self.assembled = assembled
        release = self.environment.get("PLATFORM_RELEASE_SHA", "")
        self.release_sha = release if re.fullmatch(r"[0-9a-f]{40}", release) else None
        self.initial_files = None
        self.initial_knowledge = None
        try:
            self.initial_files = self._files()
            self.initial_knowledge = self._knowledge()
        except Exception:  # fail closed; construction must not crash optional shared API
            pass

    def _files(self):
        names = {key: value for key, value in self.environment.items()
                 if value and key.endswith("_FILE") and (
                     key.startswith("PLATFORM_HR_AGENT_") or "DATABASE_URL_FILE" in key
                     or key == "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE")}
        names["provider_credential"] = self.settings.provider_profile["credential_file"]
        return {key: _file_digest(value) for key, value in names.items()}

    def _knowledge(self):
        knowledge = KnowledgeReleases(self.settings.knowledge_dir)
        knowledge.check()
        return knowledge.metadata()

    def check(self):
        blockers = []
        if not self.assembled or not self.settings.enabled:
            blockers.append("assembly_unavailable")
        if self.release_sha is None:
            blockers.append("release_unavailable")
        try:
            current = load_hr_agent_settings(self.environment)
            if current != self.settings or self.initial_files is None or self._files() != self.initial_files:
                raise ValueError("configuration drift")
        except Exception:
            blockers.append("configuration_unavailable")
        knowledge = self.initial_knowledge
        try:
            if knowledge is None or self._knowledge() != knowledge:
                raise ValueError("knowledge drift")
        except Exception:
            blockers.append("knowledge_unavailable")
        phase = None
        try:
            with self.connect() as connection, connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL lock_timeout='500ms'")
                connection.execute("SET LOCAL statement_timeout='1500ms'")
                bounded = _BoundedConnection(connection)
                if not check_schema_ready(lambda: nullcontext(bounded)):
                    raise ValueError("schema unavailable")
                row = bounded.execute("select phase from platform_control.hr_execution_cutover where singleton").fetchone()
                if row is None or row[0] not in {"legacy", "draining_legacy", "cloud", "draining_cloud"}:
                    raise ValueError("gate unavailable")
                phase = row[0]
        except Exception:
            blockers.append("database_unavailable")
        # Hash only configuration identity; never return paths, keys or provider bodies.
        runtime_files = {k: v for k, v in (self.initial_files or {}).items()
                         if k.startswith("PLATFORM_HR_AGENT_") or k == "provider_credential"}
        runtime_sha = hashlib.sha256(json.dumps(runtime_files, sort_keys=True).encode()).hexdigest()
        return {
            "ready": not blockers, "role": self.role,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase, "new_admission_enabled": not blockers and phase == "cloud",
            "release_sha": self.release_sha,
            "configuration_sha256": self.settings.configuration_revision,
            "runtime_identity_sha256": runtime_sha,
            "knowledge_sha256": (knowledge or {}).get("role_manifest_sha"),
            "knowledge_release": (knowledge or {}).get("knowledge_release"),
            "blockers": blockers,
        }
