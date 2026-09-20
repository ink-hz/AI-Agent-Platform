from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import validate_panorama
from .seed import PANORAMA_SEED


class PanoramaConflict(RuntimeError):
    pass


class PanoramaUnavailable(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class PanoramaStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS panorama_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL CHECK(revision>=0),
                    published_json TEXT NOT NULL,
                    draft_json TEXT,
                    previous_json TEXT
                );
                CREATE TABLE IF NOT EXISTS panorama_history (
                    revision INTEGER PRIMARY KEY,
                    operation TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    published_json TEXT NOT NULL,
                    draft_json TEXT,
                    previous_json TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO panorama_state(singleton,revision,published_json,draft_json,previous_json) VALUES(1,0,?,NULL,NULL)",
                (_json(PANORAMA_SEED),),
            )
            connection.commit()
            return connection
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise PanoramaUnavailable("panorama state unavailable") from error

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return {
                "revision": int(row["revision"]),
                "published": validate_panorama(json.loads(row["published_json"])),
                "draft": validate_panorama(json.loads(row["draft_json"]))
                if row["draft_json"] is not None
                else None,
                "previous": validate_panorama(json.loads(row["previous_json"]))
                if row["previous_json"] is not None
                else None,
            }
        except Exception as error:
            raise PanoramaUnavailable("panorama state corrupt") from error

    def read(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM panorama_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama state unavailable")
            return self._decode(row)

    def _write(
        self, expected_revision: int, operation: str, actor: str, transform
    ) -> dict[str, Any]:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision is invalid")
        if not actor:
            raise ValueError("actor is invalid")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM panorama_state WHERE singleton=1"
                ).fetchone()
                state = self._decode(row)
                if state["revision"] != expected_revision:
                    raise PanoramaConflict("panorama revision conflict")
                next_state = transform(deepcopy(state))
                revision = expected_revision + 1
                values = (
                    _json(next_state["published"]),
                    _json(next_state["draft"])
                    if next_state["draft"] is not None
                    else None,
                    _json(next_state["previous"])
                    if next_state["previous"] is not None
                    else None,
                )
                connection.execute(
                    "UPDATE panorama_state SET revision=?,published_json=?,draft_json=?,previous_json=? WHERE singleton=1",
                    (revision, *values),
                )
                connection.execute(
                    "INSERT INTO panorama_history(revision,operation,actor,published_json,draft_json,previous_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (revision, operation, actor, *values, _now()),
                )
                connection.commit()
                next_state["revision"] = revision
                return next_state
            except PanoramaConflict:
                connection.rollback()
                raise
            except (sqlite3.Error, TypeError, ValueError) as error:
                connection.rollback()
                if isinstance(error, ValueError):
                    raise
                raise PanoramaUnavailable("panorama state unavailable") from error

    def save_draft(
        self, expected_revision: int, data: dict[str, Any], *, actor: str
    ) -> dict[str, Any]:
        validated = validate_panorama(data)
        return self._write(
            expected_revision,
            "save_draft",
            actor,
            lambda state: {**state, "draft": validated},
        )

    def delete_draft(self, expected_revision: int, *, actor: str) -> dict[str, Any]:
        return self._write(
            expected_revision,
            "delete_draft",
            actor,
            lambda state: {**state, "draft": None},
        )

    @staticmethod
    def _release(data: dict[str, Any]) -> dict[str, Any]:
        released = deepcopy(data)
        released["version"] = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "."
            + uuid4().hex[:8]
        )
        released["updated_at"] = _now()
        return validate_panorama(released)

    def publish(self, expected_revision: int, *, actor: str) -> dict[str, Any]:
        def transform(state):
            if state["draft"] is None:
                raise ValueError("no panorama draft")
            return {
                **state,
                "previous": state["published"],
                "published": self._release(state["draft"]),
                "draft": None,
            }

        return self._write(expected_revision, "publish", actor, transform)

    def restore(self, expected_revision: int, *, actor: str) -> dict[str, Any]:
        def transform(state):
            if state["previous"] is None:
                raise ValueError("no previous panorama")
            return {
                **state,
                "previous": state["published"],
                "published": self._release(state["previous"]),
                "draft": None,
            }

        return self._write(expected_revision, "restore", actor, transform)
