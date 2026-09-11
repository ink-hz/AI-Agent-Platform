from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

# Project before materializing rows: producer request/evidence payloads can be
# hundreds of MB. jsonb_array_elements would spill those discarded fields to
# disk, and correlated topic lookups would repeat that work for every topic.
_DISPLAY_ANALYSIS_SQL = (
    "(select coalesce(jsonb_agg(to_jsonb(display_unit)),'[]'::jsonb) "
    "from jsonb_to_recordset(original.analysis) "
    "as display_unit(unit_id text,kind text,scope_key text,response jsonb)) analysis"
)


class PanoramaRepositoryError(RuntimeError):
    pass


class PanoramaNotFound(PanoramaRepositoryError):
    pass


class PanoramaConflict(PanoramaRepositoryError):
    pass


class PanoramaUnavailable(PanoramaRepositoryError):
    pass


def _identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("panorama identifier invalid")
    return value


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("panorama limit invalid")
    return value


def _company_key(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("panorama company key invalid")
    return value


def _bundle(row: Mapping[str, Any]) -> dict[str, object]:
    required = {
        "bundle_id",
        "owner_internal_user_id",
        "manifest_sha256",
        "bundle_locator",
        "schema_version",
        "generated_at",
        "company_count",
        "job_count",
        "analysis_count",
        "evidence_count",
        "manifest",
        "source_catalog",
        "source_coverage",
        "aggregates",
        "analysis",
        "analysis_usage",
        "evidence_index",
        "document_index",
        "imported_at",
    }
    if not required.issubset(row):
        raise PanoramaUnavailable("published intelligence record invalid")
    result = {key: row[key] for key in required}
    result["agent_chunk_index"] = row.get("agent_chunk_index", [])
    result["agent_document_index"] = row.get("agent_document_index", {})
    return result


class PanoramaRepository:
    """Read-only repository for Owner-approved intelligence Bundles."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        connection: Callable[[], object] | None = None,
    ) -> None:
        if connection is None and (
            not isinstance(database_url, str) or not database_url.strip()
        ):
            raise ValueError("panorama database URL required")
        if connection is not None and not callable(connection):
            raise TypeError("panorama connection factory invalid")
        self._database_url = (
            database_url.strip() if isinstance(database_url, str) else None
        )
        self._connection_factory = connection

    @contextmanager
    def _connection(self):
        if self._connection_factory is not None:
            with self._connection_factory() as connection:
                yield connection
            return
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            yield connection

    @staticmethod
    def _raise(error: Exception, action: str) -> None:
        if isinstance(error, psycopg.errors.NoDataFound):
            raise PanoramaNotFound(f"panorama {action} not found") from None
        if isinstance(
            error, (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation)
        ):
            raise PanoramaConflict(f"panorama {action} conflict") from None
        raise PanoramaUnavailable(f"panorama {action} unavailable") from error

    def current_bundle(self) -> Mapping[str, object] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_current_intelligence_bundle_v85()"
                ).fetchone()
            return None if row is None else _bundle(row)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "current bundle")

    def current_company_directory(self) -> Mapping[str, object] | None:
        query = (
            "with source as materialized (select bundle_id,generated_at,source_catalog,source_coverage,"
            + _DISPLAY_ANALYSIS_SQL
            + " from platform_hr.read_current_intelligence_bundle_v85() original) "
            "select bundle_id,generated_at,(jsonb_array_length(coalesce(source_catalog->'topics','[]'::jsonb)) > 0) topics_available,"
            "jsonb_build_object('schema_version',source_catalog->'schema_version','companies',"
            "coalesce((select jsonb_agg(jsonb_build_object('company_key',company->'company_key',"
            "'canonical_name',company->'canonical_name','aliases',company->'aliases')) "
            "from jsonb_array_elements(source_catalog->'companies') company),'[]'::jsonb)) source_catalog,"
            "jsonb_build_object('schema_version',source_coverage->'schema_version','companies',"
            "coalesce((select jsonb_agg(jsonb_build_object('company_key',coverage->'company_key',"
            "'state',coverage->'state','observed_at',coverage->'observed_at','job_count',coverage->'job_count',"
            "'limitations',coverage->'limitations','document_limitations',coverage->'document_limitations')) "
            "from jsonb_array_elements(source_coverage->'companies') coverage),'[]'::jsonb)) source_coverage,"
            "coalesce((select jsonb_agg(jsonb_build_object('unit_id',unit->'unit_id','kind',unit->'kind',"
            "'scope_key',unit->'scope_key','response',jsonb_build_object('summary',unit->'response'->'summary'))) "
            "from jsonb_array_elements(analysis) unit where unit->>'kind'='company'),'[]'::jsonb) analysis "
            "from source"
        )
        try:
            with self._connection() as connection:
                return connection.execute(query, ()).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "current company directory")

    def company_bundle(self, company_key: str, *, bundle_id: UUID | None = None) -> Mapping[str, object]:
        selected_company_key = _company_key(company_key)
        if bundle_id is None:
            source, parameters = "platform_hr.read_current_intelligence_bundle_v85()", (selected_company_key,)
        else:
            _identifier(bundle_id)
            source, parameters = "platform_hr.read_intelligence_bundle_v85(%s)", (selected_company_key, bundle_id)
        response_fields = (
            "'summary',unit->'response'->'summary','confidence',unit->'response'->'confidence',"
            "'facts',unit->'response'->'facts','inferences',unit->'response'->'inferences',"
            "'recommendations',unit->'response'->'recommendations','alternatives',unit->'response'->'alternatives',"
            "'unknowns',unit->'response'->'unknowns'"
        )
        query = (
            "with requested as (select %s::text company_key), source as materialized (select bundle_id,generated_at,"
            "source_catalog,source_coverage,aggregates," + _DISPLAY_ANALYSIS_SQL + " from " + source + " original) "
            "select bundle_id,generated_at,"
            "coalesce((select jsonb_agg(jsonb_build_object('topic_id',topic->'topic_id','title',topic->'title','summary',"
            "(select string_agg(unit->'response'->>'summary',chr(10)||chr(10) order by declared.ordinality) from jsonb_array_elements_text(topic->'unit_ids') with ordinality declared(unit_id,ordinality) join jsonb_array_elements(source.analysis) unit on unit->>'unit_id'=declared.unit_id))) "
            "from jsonb_array_elements(coalesce(source_catalog->'topics','[]'::jsonb)) topic "
            "where exists (select 1 from jsonb_array_elements(topic->'discussed_companies') relation "
            "where relation->>'company_key'=requested.company_key)),'[]'::jsonb) related_topics,"
            "jsonb_build_object('schema_version',source_catalog->'schema_version','companies',"
            "coalesce((select jsonb_agg(company) from jsonb_array_elements(source_catalog->'companies') company "
            "where company->>'company_key'=requested.company_key),'[]'::jsonb)) source_catalog,"
            "jsonb_build_object('schema_version',source_coverage->'schema_version','companies',"
            "coalesce((select jsonb_agg(coverage) from jsonb_array_elements(source_coverage->'companies') coverage "
            "where coverage->>'company_key'=requested.company_key),'[]'::jsonb)) source_coverage,"
            "coalesce((select jsonb_agg(jsonb_build_object('unit_id',unit->'unit_id','kind',unit->'kind',"
            "'scope_key',unit->'scope_key','response',jsonb_build_object(" + response_fields + "))) "
            "from jsonb_array_elements(analysis) unit where unit->>'kind'='company' "
            "and unit->>'scope_key'=requested.company_key),'[]'::jsonb) analysis,"
            "jsonb_build_object('schema_version',aggregates->'schema_version','company_matrix',"
            "case when aggregates->'company_matrix' ? requested.company_key then "
            "jsonb_build_object(requested.company_key,aggregates->'company_matrix'->requested.company_key) "
            "else '{}'::jsonb end) aggregates from source cross join requested "
            "where exists (select 1 from jsonb_array_elements(source_catalog->'companies') company "
            "where company->>'company_key'=requested.company_key)"
        )
        try:
            with self._connection() as connection:
                row = connection.execute(query, parameters).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama company not found")
            return row
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "company bundle")

    def topic_bundle(self, topic_id: str | None = None, *, bundle_id: UUID | None = None) -> Mapping[str, object] | None:
        if topic_id is not None:
            _company_key(topic_id)
        if bundle_id is None:
            source, parameters = "platform_hr.read_current_intelligence_bundle_v85()", (topic_id,)
        else:
            _identifier(bundle_id)
            source, parameters = "platform_hr.read_intelligence_bundle_v85(%s)", (topic_id, bundle_id)
        response = "jsonb_build_object('summary',unit->'response'->'summary')" if topic_id is None else (
            "jsonb_build_object(" + ",".join(f"'{key}',unit->'response'->'{key}'" for key in
                ("summary", "confidence", "facts", "inferences", "recommendations", "alternatives", "unknowns")) + ")"
        )
        query = (
            "with requested as (select %s::text topic_id), source as materialized (select bundle_id,generated_at,source_catalog,"
            + _DISPLAY_ANALYSIS_SQL + " from " + source + " original), "
            "selected as (select source.*,coalesce((select jsonb_agg(topic) from jsonb_array_elements(coalesce(source_catalog->'topics','[]'::jsonb)) topic "
            "where requested.topic_id is null or topic->>'topic_id'=requested.topic_id),'[]'::jsonb) topics from source cross join requested) "
            "select bundle_id,generated_at,"
            "jsonb_build_object('companies',coalesce((select jsonb_agg(jsonb_build_object('company_key',company->'company_key','canonical_name',company->'canonical_name')) "
            "from jsonb_array_elements(source_catalog->'companies') company),'[]'::jsonb)) "
            "|| case when source_catalog ? 'topics' then jsonb_build_object('topics',topics) else '{}'::jsonb end source_catalog,"
            "coalesce((select jsonb_agg(jsonb_build_object('unit_id',unit->'unit_id','kind',unit->'kind','scope_key',unit->'scope_key','response'," + response + ")) "
            "from jsonb_array_elements(analysis) unit where unit->>'kind' in ('topic','track') "
            "and exists (select 1 from jsonb_array_elements(topics) topic where topic->'unit_ids' ? (unit->>'unit_id'))),'[]'::jsonb) analysis from selected"
        )
        try:
            with self._connection() as connection:
                return connection.execute(query, parameters).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "topic bundle")

    def company_identity(self, company_key: str, *, bundle_id: UUID | None = None) -> UUID:
        selected_company_key = _company_key(company_key)
        if bundle_id is None:
            source, parameters = "platform_hr.read_current_intelligence_bundle_v85()", (selected_company_key,)
        else:
            _identifier(bundle_id)
            source, parameters = "platform_hr.read_intelligence_bundle_v85(%s)", (selected_company_key, bundle_id)
        query = (
            "with requested as (select %s::text company_key) select bundle_id from " + source + " source "
            "cross join requested where exists (select 1 from jsonb_array_elements(source_catalog->'companies') company "
            "where company->>'company_key'=requested.company_key)"
        )
        try:
            with self._connection() as connection:
                row = connection.execute(query, parameters).fetchone()
            if row is None or not isinstance(row.get("bundle_id"), UUID):
                raise PanoramaNotFound("panorama company not found")
            return row["bundle_id"]
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "company identity")

    def current_context_bundle(self) -> Mapping[str, object] | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select bundle_id,owner_internal_user_id,manifest_sha256,"
                    "bundle_locator,schema_version,generated_at,source_catalog,"
                    "source_coverage,agent_chunk_index,agent_document_index "
                    "from platform_hr.read_current_intelligence_bundle_v85()"
                ).fetchone()
            return row
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "current context bundle")

    def list_bundles(self, *, limit: int = 100) -> tuple[Mapping[str, object], ...]:
        selected_limit = _limit(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.list_intelligence_bundles_v85(%s)",
                    (selected_limit,),
                ).fetchall()
            return tuple(_bundle(row) for row in rows)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundles")

    def bundle(self, bundle_id: UUID) -> Mapping[str, object]:
        _identifier(bundle_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_v85(%s)",
                    (bundle_id,),
                ).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama bundle not found")
            return _bundle(row)
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle")

    def bundle_document_metadata(self, bundle_id: UUID) -> Mapping[str, object]:
        _identifier(bundle_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select bundle_id,bundle_locator,document_index,evidence_index,"
                    "agent_document_index from "
                    "platform_hr.read_intelligence_bundle_v85(%s)",
                    (bundle_id,),
                ).fetchone()
            if row is None:
                raise PanoramaNotFound("panorama bundle not found")
            return row
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle document metadata")

    def bundle_jobs(self, bundle_id: UUID) -> tuple[Mapping[str, object], ...]:
        _identifier(bundle_id)
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_jobs_v85(%s)",
                    (bundle_id,),
                ).fetchall()
            jobs = tuple(row.get("job") for row in rows)
            if any(not isinstance(job, Mapping) for job in jobs):
                raise ValueError("panorama jobs invalid")
            return jobs
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "jobs")

    def bundle_company_jobs(
        self, bundle_id: UUID, company_key: str, *, offset: int, limit: int,
        location: str | None = None, status: str | None = None,
    ) -> tuple[tuple[Mapping[str, object], ...], int]:
        _identifier(bundle_id)
        selected_company_key = _company_key(company_key)
        selected_limit = _limit(limit)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
            raise ValueError("panorama offset invalid")
        if any(value is not None and (not isinstance(value, str) or not value) for value in (location, status)):
            raise ValueError("panorama job filter invalid")
        if status is not None and status not in {"open", "closed", "unknown"}:
            raise ValueError("panorama job status invalid")
        clauses = ["company_key=%s"]
        parameters: list[object] = [bundle_id, selected_company_key]
        if location is not None:
            clauses.append("job->>'location'=%s")
            parameters.append(location)
        if status is not None:
            clauses.append("job->>'status'=%s")
            parameters.append(status)
        parameters.extend((offset, selected_limit))
        query = (
            "with filtered as (select job,job_id from "
            "platform_hr.read_intelligence_bundle_jobs_v85(%s) where "
            + " and ".join(clauses)
            + "), page as (select job from filtered order by job_id offset %s limit %s) "
            "select coalesce((select jsonb_agg(job) from page),'[]'::jsonb) as items,"
            "(select count(*) from filtered) as total"
        )
        try:
            with self._connection() as connection:
                row = connection.execute(query, tuple(parameters)).fetchone()
            if not isinstance(row, Mapping):
                raise TypeError("panorama company jobs invalid")
            raw_items, total = row.get("items"), row.get("total")
            if not isinstance(raw_items, list) or isinstance(total, bool) or not isinstance(total, int):
                raise TypeError("panorama company jobs invalid")
            items = tuple(raw_items)
            if any(not isinstance(item, Mapping) for item in items):
                raise ValueError("panorama company jobs invalid")
            return items, total
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "company jobs")

    def bundle_reference_for_turn(
        self, owner_id: UUID, position_id: UUID, turn_id: UUID
    ) -> Mapping[str, object] | None:
        for value in (owner_id, position_id, turn_id):
            _identifier(value)
        try:
            with self._connection() as connection:
                return connection.execute(
                    "select * from platform_hr."
                    "read_intelligence_bundle_reference_for_turn_v86(%s,%s,%s)",
                    (owner_id, position_id, turn_id),
                ).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle task reference")

    def record_bundle_reference(
        self,
        *,
        reference_id: UUID,
        owner_id: UUID,
        client_request_id: UUID,
        position_id: UUID,
        turn_id: UUID,
        bundle_id: UUID,
        observed_at: object,
        context_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        for value in (
            reference_id, owner_id, client_request_id, position_id, turn_id, bundle_id
        ):
            _identifier(value)
        if not isinstance(context_document, Mapping):
            raise TypeError("panorama bundle context invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr.create_intelligence_bundle_reference_v86("
                    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        reference_id, owner_id, client_request_id, position_id,
                        turn_id, bundle_id, observed_at,
                        json.dumps(context_document, ensure_ascii=False),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable("panorama bundle task reference unavailable")
            return row
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle task reference")

    def conversation_bundle_reference_for_turn(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
    ) -> Mapping[str, object] | None:
        for value in (owner_id, conversation_id, turn_id):
            _identifier(value)
        try:
            with self._connection() as connection:
                return connection.execute(
                    "select * from platform_hr."
                    "read_conversation_intelligence_reference_v87(%s,%s,%s)",
                    (owner_id, conversation_id, turn_id),
                ).fetchone()
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "conversation bundle reference")

    def record_conversation_bundle_reference(
        self,
        *,
        reference_id: UUID,
        owner_id: UUID,
        client_request_id: UUID,
        conversation_id: UUID,
        turn_id: UUID,
        bundle_id: UUID,
        observed_at: object,
        context_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        for value in (
            reference_id,
            owner_id,
            client_request_id,
            conversation_id,
            turn_id,
            bundle_id,
        ):
            _identifier(value)
        if not isinstance(context_document, Mapping):
            raise TypeError("panorama conversation context invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select (platform_hr."
                    "create_conversation_intelligence_reference_v87("
                    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb)).*",
                    (
                        reference_id,
                        owner_id,
                        client_request_id,
                        conversation_id,
                        turn_id,
                        bundle_id,
                        observed_at,
                        json.dumps(context_document, ensure_ascii=False),
                    ),
                ).fetchone()
            if row is None:
                raise PanoramaUnavailable(
                    "panorama conversation bundle reference unavailable"
                )
            return row
        except PanoramaRepositoryError:
            raise
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "conversation bundle reference")

    def bundle_by_manifest(
        self,
        owner_id: UUID,
        manifest_sha256: str,
    ) -> Mapping[str, object] | None:
        _identifier(owner_id)
        if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
            raise TypeError("panorama manifest hash invalid")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "select * from platform_hr.read_intelligence_bundle_by_manifest_v85(%s,%s)",
                    (owner_id, manifest_sha256),
                ).fetchone()
            return None if row is None else _bundle(row)
        except (KeyError, TypeError, ValueError, psycopg.Error) as error:
            self._raise(error, "bundle manifest")

    @staticmethod
    def canonical_payload(record: Mapping[str, object]) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


__all__ = [
    "PanoramaConflict",
    "PanoramaNotFound",
    "PanoramaRepository",
    "PanoramaRepositoryError",
    "PanoramaUnavailable",
]
