"""Append-only SDK baseline and evidence persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.control_plane.dsn import validate_control_dsn


def _json(row: dict) -> dict:
    return {key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()}


class EvidenceRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="app")
        self.url = database_url
        self.connect = connect

    def _connection(self):
        try:
            return self.connect(self.url, connect_timeout=3, options="-c statement_timeout=10000", row_factory=dict_row)
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def replay(self, actor: UUID, request_id: UUID, digest: str) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select digest,result from sdk.evidence_requests where actor_id=%s and request_id=%s",
                    (actor, request_id),
                ).fetchone()
            if row and row["digest"] != digest:
                raise HTTPException(409, "sdk_request_id_collision")
            return row["result"] if row else None
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def _write(self, actor: UUID, request_id: UUID, digest: str, action) -> dict:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    inserted = connection.execute(
                        "insert into sdk.evidence_requests(actor_id,request_id,digest,result) "
                        "values (%s,%s,%s,'{}'::jsonb) on conflict do nothing returning request_id",
                        (actor, request_id, digest),
                    ).fetchone()
                    if not inserted:
                        old = connection.execute(
                            "select digest,result from sdk.evidence_requests where actor_id=%s and request_id=%s",
                            (actor, request_id),
                        ).fetchone()
                        if old["digest"] != digest:
                            raise HTTPException(409, "sdk_request_id_collision")
                        return old["result"]
                    result = action(connection)
                    connection.execute(
                        "update sdk.evidence_requests set result=%s where actor_id=%s and request_id=%s",
                        (Jsonb(result), actor, request_id),
                    )
                    return result
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(404, "sdk_evidence_parent_not_found") from None
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def create_candidate(self, actor: UUID, request_id: UUID, digest: str, story_id: int, story_version: str,
                         task_ids: list[int], manifest: dict, targets: list[dict]) -> dict:
        def action(connection):
            row = connection.execute(
                "insert into sdk.candidates(story_id,story_version,task_ids,manifest,targets,creator_id) "
                "values (%s,%s,%s,%s,%s,%s) returning id",
                (story_id, story_version, Jsonb(task_ids), Jsonb(manifest), Jsonb(targets), actor),
            ).fetchone()
            connection.execute("insert into sdk.candidate_state(candidate_id) values (%s)", (row["id"],))
            return {"id": row["id"]}
        return self._write(actor, request_id, digest, action)

    def list_candidates(self, page: int) -> dict:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select c.id,c.story_id,c.story_version,c.creator_id,c.created_at,s.evidence_version "
                    "from sdk.candidates c join sdk.candidate_state s on s.candidate_id=c.id "
                    "order by c.id desc limit 20 offset %s", ((page - 1) * 20,),
                ).fetchall()
                total = connection.execute("select count(*) as total from sdk.candidates").fetchone()["total"]
            return {"items": [_json(dict(row)) for row in rows], "page": page, "total": total}
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def get_candidate(self, candidate_id: int) -> dict | None:
        try:
            with self._connection() as connection:
                candidate = connection.execute(
                    "select c.*,s.evidence_version from sdk.candidates c join sdk.candidate_state s on s.candidate_id=c.id where c.id=%s",
                    (candidate_id,),
                ).fetchone()
                if not candidate:
                    return None
                builds = connection.execute("select * from sdk.builds where candidate_id=%s order by id", (candidate_id,)).fetchall()
                tests = connection.execute(
                    "select t.* from sdk.test_runs t join sdk.builds b on b.id=t.build_id where b.candidate_id=%s order by t.id", (candidate_id,)
                ).fetchall()
                approvals = connection.execute("select * from sdk.approvals where candidate_id=%s order by id", (candidate_id,)).fetchall()
                releases = connection.execute("select * from sdk.releases where candidate_id=%s order by id", (candidate_id,)).fetchall()
            return {**_json(dict(candidate)), "builds": [_json(dict(row)) for row in builds],
                    "tests": [_json(dict(row)) for row in tests], "approvals": [_json(dict(row)) for row in approvals],
                    "releases": [_json(dict(row)) for row in releases]}
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def get_build(self, build_id: int) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute("select * from sdk.builds where id=%s", (build_id,)).fetchone()
            return _json(dict(row)) if row else None
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def _bump(self, connection, candidate_id: int) -> int:
        row = connection.execute(
            "update sdk.candidate_state set evidence_version=evidence_version+1 where candidate_id=%s returning evidence_version",
            (candidate_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "sdk_candidate_not_found")
        return row["evidence_version"]

    def add_build(self, actor: UUID, request_id: UUID, digest: str, candidate_id: int, body: dict) -> dict:
        def action(connection):
            self._bump(connection, candidate_id)
            row = connection.execute(
                "insert into sdk.builds(candidate_id,actor_id,os,arch,status,command,log_uri,artifact_name,artifact_sha256,artifact_size,artifact_uri) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (candidate_id, actor, body["os"], body["arch"], body["status"], body["command"], body["log_uri"],
                 body.get("artifact_name"), body.get("artifact_sha256"), body.get("artifact_size"), body.get("artifact_uri")),
            ).fetchone()
            return {"id": row["id"]}
        return self._write(actor, request_id, digest, action)

    def add_test(self, actor: UUID, request_id: UUID, digest: str, build_id: int, body: dict) -> dict:
        def action(connection):
            build = connection.execute("select candidate_id,status from sdk.builds where id=%s", (build_id,)).fetchone()
            if not build or build["status"] != "success":
                raise HTTPException(409, "sdk_artifact_required")
            self._bump(connection, build["candidate_id"])
            row = connection.execute(
                "insert into sdk.test_runs(build_id,actor_id,suite,status,passed,failed,skipped,report_uri,device,firmware) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (build_id, actor, body["suite"], body["status"], body["passed"], body["failed"], body["skipped"],
                 body["report_uri"], body["device"], body["firmware"]),
            ).fetchone()
            return {"id": row["id"]}
        return self._write(actor, request_id, digest, action)

    def add_approval(self, actor: UUID, request_id: UUID, digest: str, candidate_id: int, version: int, note: str) -> dict:
        def action(connection):
            state = connection.execute("select evidence_version from sdk.candidate_state where candidate_id=%s for update", (candidate_id,)).fetchone()
            if not state or state["evidence_version"] != version:
                raise HTTPException(409, "sdk_evidence_changed")
            row = connection.execute(
                "insert into sdk.approvals(candidate_id,actor_id,evidence_version,note) values (%s,%s,%s,%s) returning id",
                (candidate_id, actor, version, note),
            ).fetchone()
            return {"id": row["id"], "evidence_version": version}
        return self._write(actor, request_id, digest, action)

    def add_release(self, actor: UUID, request_id: UUID, digest: str, candidate_id: int, approval_id: int,
                    version: int, channel: str, rollback_uri: str, released_at: datetime, sha256s: list[str]) -> dict:
        def action(connection):
            state = connection.execute("select evidence_version from sdk.candidate_state where candidate_id=%s for update", (candidate_id,)).fetchone()
            if not state or state["evidence_version"] != version:
                raise HTTPException(409, "sdk_evidence_changed")
            latest = connection.execute("select id,evidence_version from sdk.approvals where candidate_id=%s order by id desc limit 1", (candidate_id,)).fetchone()
            if not latest or latest["id"] != approval_id or latest["evidence_version"] != version:
                raise HTTPException(409, "sdk_approval_required")
            row = connection.execute(
                "insert into sdk.releases(candidate_id,approval_id,actor_id,channel,rollback_uri,released_at,artifact_sha256s) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (candidate_id, approval_id, actor, channel, rollback_uri, released_at, Jsonb(sha256s)),
            ).fetchone()
            return {"id": row["id"]}
        return self._write(actor, request_id, digest, action)
