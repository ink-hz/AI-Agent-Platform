"""Persistent SDK grants, requests and review history."""

from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


class SdkRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="app")
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        try:
            return self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
                row_factory=dict_row,
            )
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    @staticmethod
    def _unavailable(_error):
        raise HTTPException(503, "sdk_store_unavailable") from None

    def role_for(self, actor: UUID) -> str | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select role from sdk.members where internal_user_id = %s", (actor,)
                ).fetchone()
            return row["role"] if row else None
        except psycopg.Error as error:
            self._unavailable(error)

    def members(self) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select internal_user_id, role from sdk.members order by granted_at desc limit 500"
                ).fetchall()
            return [{"internal_user_id": str(row["internal_user_id"]), "role": row["role"]} for row in rows]
        except psycopg.Error as error:
            self._unavailable(error)

    def grant_member(self, actor: UUID, target: UUID, role: str, request_id: UUID) -> dict:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    inserted = connection.execute(
                        "insert into sdk.member_events(actor_id,request_id,target_user_id,role) values (%s,%s,%s,%s) "
                        "on conflict do nothing returning request_id",
                        (actor, request_id, target, role),
                    ).fetchone()
                    if not inserted:
                        prior = connection.execute(
                            "select target_user_id,role from sdk.member_events where actor_id=%s and request_id=%s",
                            (actor, request_id),
                        ).fetchone()
                        if prior["target_user_id"] != target or prior["role"] != role:
                            raise HTTPException(409, "sdk_request_id_collision")
                        return {"internal_user_id": str(target), "role": role}
                    connection.execute(
                        "insert into sdk.members(internal_user_id,role,granted_by) values (%s,%s,%s) "
                        "on conflict (internal_user_id) do update set role=excluded.role,granted_by=excluded.granted_by,granted_at=clock_timestamp()",
                        (target, role, actor),
                    )
            return {"internal_user_id": str(target), "role": role}
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(404, "sdk_member_not_found") from None
        except psycopg.Error as error:
            self._unavailable(error)

    def reserve(self, actor: UUID, request_id: UUID, digest: str, kind: str, story_id: int | None = None, payload: dict | None = None, *, plan_row_id: int | None = None) -> tuple[dict, bool]:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    inserted = connection.execute(
                        "insert into sdk.write_intents(actor_id,request_id,digest,kind,story_id,request_payload,plan_row_id,state) "
                        "values (%s,%s,%s,%s,%s,%s,%s,'pending') on conflict (actor_id,request_id) do nothing "
                        "returning state,digest,result,kind,story_id,request_payload,plan_row_id",
                        (actor, request_id, digest, kind, story_id, psycopg.types.json.Jsonb(payload or {}), plan_row_id),
                    ).fetchone()
                    if inserted:
                        return dict(inserted), True
                    row = connection.execute(
                        "select state,digest,result,kind,story_id,request_payload,plan_row_id from sdk.write_intents where actor_id=%s and request_id=%s for update",
                        (actor, request_id),
                    ).fetchone()
                    if row and row["state"] == "rejected" and row["digest"] == digest:
                        connection.execute(
                            "update sdk.write_intents set state='pending',updated_at=clock_timestamp() where actor_id=%s and request_id=%s",
                            (actor, request_id),
                        )
                        return {**dict(row), "state": "pending"}, True
            if row is None:
                raise HTTPException(503, "sdk_store_unavailable")
            return dict(row), False
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_plan_row_already_converting") from None
        except psycopg.Error as error:
            self._unavailable(error)

    def get_intent(self, actor: UUID, request_id: UUID) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select state,result,kind,story_id,plan_row_id,created_at,digest,request_payload from sdk.write_intents where actor_id=%s and request_id=%s",
                    (actor, request_id),
                ).fetchone()
            return dict(row) if row else None
        except psycopg.Error as error:
            self._unavailable(error)

    def list_reconciliation(self) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select actor_id,request_id,kind,story_id,request_payload,created_at from sdk.write_intents "
                    "where state='reconcile_required' or (state='pending' and created_at <= clock_timestamp()-interval '60 seconds') "
                    "order by created_at limit 500"
                ).fetchall()
            return [{**dict(row), "actor_id": str(row["actor_id"]), "request_id": str(row["request_id"]), "created_at": row["created_at"].isoformat()} for row in rows]
        except psycopg.Error as error:
            self._unavailable(error)

    def bind_reconciliation(self, admin: UUID, admin_request_id: UUID, actor: UUID, request_id: UUID, external_id: int) -> dict:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    prior = connection.execute(
                        "select target_actor_id,target_request_id,external_id from sdk.reconciliation_events "
                        "where admin_actor_id=%s and admin_request_id=%s",
                        (admin, admin_request_id),
                    ).fetchone()
                    if prior:
                        if prior["target_actor_id"] != actor or prior["target_request_id"] != request_id or prior["external_id"] != external_id:
                            raise HTTPException(409, "sdk_request_id_collision")
                        return {"id": external_id}
                    intent = connection.execute(
                        "select kind,story_id,state,created_at <= clock_timestamp()-interval '60 seconds' as stale "
                        "from sdk.write_intents where actor_id=%s and request_id=%s for update",
                        (actor, request_id),
                    ).fetchone()
                    if not intent or (intent["state"] != "reconcile_required" and not (intent["state"] == "pending" and intent["stale"])):
                        raise HTTPException(409, "sdk_reconcile_required")
                    connection.execute(
                        "insert into sdk.reconciliation_events(admin_actor_id,admin_request_id,target_actor_id,target_request_id,external_id) "
                        "values (%s,%s,%s,%s,%s)",
                        (admin, admin_request_id, actor, request_id, external_id),
                    )
                    if intent["kind"] == "create_task":
                        connection.execute(
                            "insert into sdk.task_links(story_id,task_id,actor_id,request_id) values (%s,%s,%s,%s)",
                            (intent["story_id"], external_id, actor, request_id),
                        )
                    connection.execute(
                        "update sdk.write_intents set state='succeeded',result=%s::jsonb,updated_at=clock_timestamp() "
                        "where actor_id=%s and request_id=%s",
                        (psycopg.types.json.Jsonb({"id": external_id}), actor, request_id),
                    )
            return {"id": external_id}
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_external_id_collision") from None
        except psycopg.Error as error:
            self._unavailable(error)

    def complete(self, actor: UUID, request_id: UUID, result: dict) -> None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "update sdk.write_intents set state='succeeded',result=%s::jsonb,updated_at=clock_timestamp() "
                    "where actor_id=%s and request_id=%s and state='pending' returning request_id",
                    (psycopg.types.json.Jsonb(result), actor, request_id),
                ).fetchone()
                if row is None:
                    raise HTTPException(409, "sdk_reconcile_required")
        except psycopg.Error as error:
            self._unavailable(error)

    def reconcile(self, actor: UUID, request_id: UUID) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "update sdk.write_intents set state='reconcile_required',updated_at=clock_timestamp() "
                    "where actor_id=%s and request_id=%s and state='pending'",
                    (actor, request_id),
                )
        except psycopg.Error as error:
            self._unavailable(error)

    def reject(self, actor: UUID, request_id: UUID, reason: str) -> None:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        "update sdk.write_intents set state='rejected',updated_at=clock_timestamp() "
                        "where actor_id=%s and request_id=%s and state='pending' returning request_id",
                        (actor, request_id),
                    ).fetchone()
                    if row is None:
                        raise HTTPException(409, "sdk_reconcile_required")
                    connection.execute(
                        "insert into sdk.write_rejection_events(actor_id,request_id,reason) values (%s,%s,%s)",
                        (actor, request_id, reason),
                    )
        except psycopg.Error as error:
            self._unavailable(error)

    def add_review(self, actor: UUID, story_id: int, story_version: str, decision: str, reason: str, request_id: UUID) -> dict:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    # Serialize review changes with approval/release transactions through candidate_state.
                    connection.execute(
                        "select s.candidate_id from sdk.candidate_state s join sdk.candidates c on c.id=s.candidate_id "
                        "where c.story_id=%s order by s.candidate_id for update of s",
                        (story_id,),
                    ).fetchall()
                    row = connection.execute(
                        "insert into sdk.review_events(actor_id,request_id,story_id,story_version,decision,reason) "
                        "values (%s,%s,%s,%s,%s,%s) returning created_at",
                        (actor, request_id, story_id, story_version, decision, reason),
                    ).fetchone()
                    result = {"actor_id": str(actor), "story_id": story_id, "story_version": story_version, "decision": decision, "reason": reason, "request_id": str(request_id), "created_at": row["created_at"].isoformat()}
                    completed = connection.execute(
                        "update sdk.write_intents set state='succeeded',result=%s::jsonb,updated_at=clock_timestamp() "
                        "where actor_id=%s and request_id=%s and state='pending' and kind='review_requirement' returning request_id",
                        (psycopg.types.json.Jsonb(result), actor, request_id),
                    ).fetchone()
                    if completed is None:
                        raise HTTPException(409, "sdk_reconcile_required")
                    connection.execute(
                        "update sdk.candidate_state s set evidence_version=evidence_version+1 "
                        "from sdk.candidates c where c.id=s.candidate_id and c.story_id=%s",
                        (story_id,),
                    )
            return result
        except psycopg.Error as error:
            self._unavailable(error)

    def list_reviews(self, story_id: int, page: int = 1) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select actor_id,request_id,story_id,story_version,decision,reason,created_at "
                    "from sdk.review_events where story_id=%s order by created_at desc,request_id desc limit 50 offset %s",
                    (story_id, (page - 1) * 50),
                ).fetchall()
            return [{**dict(row), "actor_id": str(row["actor_id"]), "request_id": str(row["request_id"]), "created_at": row["created_at"].isoformat()} for row in reversed(rows)]
        except psycopg.Error as error:
            self._unavailable(error)

    def latest_review(self, story_id: int) -> dict | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select actor_id,request_id,story_id,story_version,decision,reason,created_at "
                    "from sdk.review_events where story_id=%s order by created_at desc,request_id desc limit 1",
                    (story_id,),
                ).fetchone()
            return {**dict(row), "actor_id": str(row["actor_id"]), "request_id": str(row["request_id"]), "created_at": row["created_at"].isoformat()} if row else None
        except psycopg.Error as error:
            self._unavailable(error)

    def add_task_link(self, actor: UUID, story_id: int, task_id: int, request_id: UUID) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    "insert into sdk.task_links(story_id,task_id,actor_id,request_id) "
                    "values (%s,%s,%s,%s)",
                    (story_id, task_id, actor, request_id),
                )
        except psycopg.Error as error:
            self._unavailable(error)

    def complete_task(self, actor: UUID, story_id: int, task_id: int, request_id: UUID) -> None:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    connection.execute(
                        "insert into sdk.task_links(story_id,task_id,actor_id,request_id) values (%s,%s,%s,%s)",
                        (story_id, task_id, actor, request_id),
                    )
                    row = connection.execute(
                        "update sdk.write_intents set state='succeeded',result=%s::jsonb,updated_at=clock_timestamp() "
                        "where actor_id=%s and request_id=%s and kind='create_task' and story_id=%s and state='pending' returning request_id",
                        (psycopg.types.json.Jsonb({"id": task_id}), actor, request_id, story_id),
                    ).fetchone()
                    if row is None:
                        raise HTTPException(409, "sdk_reconcile_required")
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "sdk_external_id_collision") from None
        except psycopg.Error as error:
            self._unavailable(error)

    def list_tasks(self, story_id: int) -> list[dict]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select story_id,task_id from sdk.task_links where story_id=%s order by task_id",
                    (story_id,),
                ).fetchall()
            return [dict(row) for row in rows]
        except psycopg.Error as error:
            self._unavailable(error)
