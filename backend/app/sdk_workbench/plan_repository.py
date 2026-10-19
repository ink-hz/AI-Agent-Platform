"""SDK annual plan source snapshots through the restricted app role."""

from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn

from .plan_csv import ParsedPlan


_ROW_SELECT = (
    "select r.id,r.import_id,r.record_number,r.physical_line,r.kind,r.module,r.cells,"
    "i.sha256,i.filename,w.state as conversion_state,w.result->>'id' as story_id,"
    "w.actor_id,w.request_id from sdk.plan_rows r "
    "join sdk.plan_imports i on i.id=r.import_id "
    "left join sdk.write_intents w on w.plan_row_id=r.id "
)


class PlanRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        validate_control_dsn(database_url, purpose="app")
        self._url = database_url
        self._connect = connect

    def _connection(self):
        try:
            return self._connect(self._url, connect_timeout=3, options="-c statement_timeout=10000", row_factory=dict_row)
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    @staticmethod
    def _batch(row: dict) -> dict:
        return {**dict(row), "uploaded_by": str(row["uploaded_by"]), "created_at": row["created_at"].isoformat()}

    @staticmethod
    def _row(row: dict) -> dict:
        result = dict(row)
        result["story_id"] = int(result["story_id"]) if result["story_id"] is not None else None
        for key in ("actor_id", "request_id"):
            result[key] = str(result[key]) if result[key] is not None else None
        return result

    def import_plan(self, actor: UUID, filename: str, parsed: ParsedPlan) -> dict:
        try:
            with self._connection() as connection:
                with connection.transaction():
                    inserted = connection.execute(
                        "insert into sdk.plan_imports(uploaded_by,filename,sha256,row_count,task_count,module_count) "
                        "values (%s,%s,%s,%s,%s,%s) on conflict (sha256) do nothing "
                        "returning id,uploaded_by,filename,sha256,row_count,task_count,module_count,created_at",
                        (actor, filename, parsed.sha256, len(parsed.rows), parsed.task_count, parsed.module_count),
                    ).fetchone()
                    if inserted is None:
                        prior = connection.execute(
                            "select id,uploaded_by,filename,sha256,row_count,task_count,module_count,created_at "
                            "from sdk.plan_imports where sha256=%s", (parsed.sha256,),
                        ).fetchone()
                        if prior is None:
                            raise HTTPException(503, "sdk_store_unavailable")
                        return self._batch(prior)
                    for row in parsed.rows:
                        connection.execute(
                            "insert into sdk.plan_rows(import_id,record_number,physical_line,kind,module,cells) "
                            "values (%s,%s,%s,%s,%s,%s)",
                            (inserted["id"], row.record_number, row.physical_line, row.kind, row.module,
                             psycopg.types.json.Jsonb(list(row.cells))),
                        )
            return self._batch(inserted)
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def list_batches(self, *, page: int = 1, page_size: int = 20) -> dict:
        try:
            with self._connection() as connection:
                total = connection.execute("select count(*) as count from sdk.plan_imports").fetchone()["count"]
                rows = connection.execute(
                    "select id,uploaded_by,filename,sha256,row_count,task_count,module_count,created_at "
                    "from sdk.plan_imports order by id desc limit %s offset %s",
                    (page_size, (page - 1) * page_size),
                ).fetchall()
            return {"items": [self._batch(row) for row in rows], "page": page, "total": total}
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def list_rows(self, import_id: int, *, page: int = 1, page_size: int = 50,
                  module: str | None = None, priority: str | None = None, owner: str | None = None) -> dict:
        filters = "r.import_id=%s and (%s::text is null or r.module=%s) and (%s::text is null or r.cells->>6=%s) and (%s::text is null or r.cells->>2=%s)"
        params = (import_id, module, module, priority, priority, owner, owner)
        try:
            with self._connection() as connection:
                batch = connection.execute("select 1 from sdk.plan_imports where id=%s", (import_id,)).fetchone()
                if not batch:
                    raise HTTPException(404, "sdk_plan_import_not_found")
                total = connection.execute(f"select count(*) as count from sdk.plan_rows r where {filters}", params).fetchone()["count"]
                rows = connection.execute(
                    _ROW_SELECT + f"where {filters} order by r.record_number limit %s offset %s",
                    (*params, page_size, (page - 1) * page_size),
                ).fetchall()
            return {"items": [self._row(row) for row in rows], "page": page, "total": total}
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None

    def row(self, row_id: int) -> dict:
        try:
            with self._connection() as connection:
                row = connection.execute(_ROW_SELECT + "where r.id=%s", (row_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "sdk_plan_row_not_found")
            return self._row(row)
        except psycopg.Error:
            raise HTTPException(503, "sdk_store_unavailable") from None
