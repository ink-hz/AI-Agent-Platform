"""Bounded aggregate-only HR handover inventory.

This tool has no migration or arbitrary SQL mode. It never reads row identities or
content fields and always rolls its transaction back.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg


@dataclass(frozen=True)
class QuerySpec:
    name: str
    relation: str
    columns: tuple[str, ...]
    sql: str
    state_columns: tuple[str, ...] = ()
    allowed_states: dict[str, frozenset[str | None]] | None = None
    dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _spec(name, relation, columns, statement, states=(), allowed=None, dependencies=()):
    return QuerySpec(
        name, relation, tuple(columns), statement, tuple(states), allowed,
        tuple((selected, tuple(required)) for selected, required in dependencies),
    )


POSITION_STATES = {
    "source_kind": frozenset({"official_site", "manual"}),
    "internal_status": frozenset({"draft", "active", "archived"}),
    "official_status": frozenset({None, "active", "stale", "suspected_inactive", "inactive"}),
}
ATTACHMENT_STATES = frozenset({
    "uploading", "validating", "scanning", "ready", "quarantined", "rejected", "deleted"
})
WORK_STATES = frozenset({
    "queued", "running", "waiting_user", "waiting_budget", "completed", "cancelled",
    "failed", "blocked",
})
EXECUTION_STATES = frozenset({
    "queued", "leased", "dispatched", "running", "completed", "failed", "cancelled",
    "interrupted",
})


QUERY_REGISTRY = (
    _spec("old_positions", "platform_hr.positions",
          ("source_kind", "internal_status", "official_status"),
          "select source_kind,internal_status,official_status,count(source_kind)::bigint as count "
          "from platform_hr.positions group by source_kind,internal_status,official_status",
          POSITION_STATES, POSITION_STATES),
    _spec("old_candidates", "platform_hr.candidates", ("candidate_id",),
          "select count(candidate_id)::bigint as count from platform_hr.candidates"),
    _spec("old_candidate_relations", "platform_hr.position_candidates", ("position_candidate_id",),
          "select count(position_candidate_id)::bigint as count from platform_hr.position_candidates"),
    _spec("old_candidate_documents", "platform_hr.candidate_documents", ("document_id",),
          "select count(document_id)::bigint as count from platform_hr.candidate_documents"),
    _spec("old_candidate_drafts", "platform_hr.candidate_drafts", ("state",),
          "select state,count(state)::bigint as count from platform_hr.candidate_drafts group by state",
          ("state",), {"state": frozenset({"pending", "processing", "ready", "failed", "confirmed", "dismissed"})}),
    _spec("old_parse_attempts", "platform_hr.candidate_draft_processing_attempts", ("state",),
          "select state,count(state)::bigint as count from platform_hr.candidate_draft_processing_attempts group by state",
          ("state",), {"state": frozenset({"processing", "completed", "failed", "expired"})}),
    _spec("new_candidates", "platform_hr_agent.candidates", ("candidate_id",),
          "select count(candidate_id)::bigint as count from platform_hr_agent.candidates"),
    _spec("new_candidate_relations", "platform_hr_agent.candidate_positions", ("position_id",),
          "select count(position_id)::bigint as count from platform_hr_agent.candidate_positions"),
    _spec("new_candidate_documents", "platform_hr_agent.candidate_documents", ("document_id",),
          "select count(document_id)::bigint as count from platform_hr_agent.candidate_documents"),
    _spec("new_candidate_intake", "platform_hr_agent.candidate_intake_items", ("state",),
          "select state,count(state)::bigint as count from platform_hr_agent.candidate_intake_items group by state",
          ("state",), {"state": frozenset({"queued", "parsing", "profiling", "awaiting_review", "failed", "confirmed"})}),
    _spec("new_material_parses", "platform_hr_agent.material_parses", ("state",),
          "select state,count(state)::bigint as count from platform_hr_agent.material_parses group by state",
          ("state",), {"state": frozenset({"queued", "processing", "ready", "failed", "unsupported"})}),
    _spec("attachments", "platform_attachments.attachments", ("state", "source_kind"),
          "select state,source_kind,count(state)::bigint as count from platform_attachments.attachments "
          "group by state,source_kind", ("state", "source_kind"),
          {"state": ATTACHMENT_STATES, "source_kind": frozenset({"user_input", "agent_output"})}),
    _spec("old_position_artifacts", "platform_hr.position_artifacts", ("artifact_id",),
          "select count(artifact_id)::bigint as count from platform_hr.position_artifacts"),
    _spec("old_artifact_versions", "platform_attachments.artifact_versions", ("state", "result_status"),
          "select state,result_status,count(state)::bigint as count from platform_attachments.artifact_versions "
          "group by state,result_status", ("state", "result_status"),
          {"state": ATTACHMENT_STATES, "result_status": frozenset({"pending", "succeeded", "failed"})}),
    _spec("old_candidate_analyses", "platform_hr.candidate_analysis_versions", ("analysis_version_id",),
          "select count(analysis_version_id)::bigint as count from platform_hr.candidate_analysis_versions"),
    _spec("old_context_versions", "platform_hr.position_context_versions", ("state",),
          "select state,count(state)::bigint as count from platform_hr.position_context_versions group by state",
          ("state",), {"state": frozenset({"draft", "confirmed", "superseded"})}),
    _spec("old_result_projections", "platform_hr.hr_task_result_projections", ("state",),
          "select state,count(state)::bigint as count from platform_hr.hr_task_result_projections group by state",
          ("state",), {"state": frozenset({"pending", "processing", "completed", "failed"})}),
    _spec("old_result_intents", "platform_control.result_artifact_intents", ("status",),
          "select status,count(status)::bigint as count from platform_control.result_artifact_intents group by status",
          ("status",), {"status": frozenset({"pending", "ready", "failed"})}),
    _spec("new_results", "platform_hr_agent.results", ("kind",),
          "select kind,count(kind)::bigint as count from platform_hr_agent.results group by kind",
          ("kind",), {"kind": frozenset({
              "role_calibration", "jd", "requirements", "standard_proposal", "sourcing",
              "candidate_assessment", "interview_plan", "interview_record", "retrospective", "research",
          })}),
    _spec("new_result_revisions", "platform_hr_agent.result_revisions", ("revision_id",),
          "select count(revision_id)::bigint as count from platform_hr_agent.result_revisions"),
    _spec("new_standards", "platform_hr_agent.standards", ("position_id",),
          "select count(position_id)::bigint as count from platform_hr_agent.standards"),
    _spec("new_standard_revisions", "platform_hr_agent.standard_revisions", ("revision_id",),
          "select count(revision_id)::bigint as count from platform_hr_agent.standard_revisions"),
    _spec("new_works", "platform_hr_agent.works", ("state", "phase", "answer_state"),
          "select state,phase,answer_state,count(state)::bigint as count from platform_hr_agent.works "
          "group by state,phase,answer_state", ("state", "phase", "answer_state"),
          {"state": WORK_STATES, "phase": frozenset({"research", "finalizing"}),
           "answer_state": frozenset({"none", "partial", "ended"})}),
    _spec("old_execution_jobs", "platform_control.execution_jobs", ("agent_id", "status"),
          "select case when agent_id='hr-bot' then 'hr' else 'other' end as scope,"
          "status,count(agent_id)::bigint as count from platform_control.execution_jobs group by scope,status",
          ("scope", "status"), {"scope": frozenset({"hr", "other"}), "status": EXECUTION_STATES}),
    _spec("old_hr_queued_age", "platform_control.execution_jobs",
          ("agent_id", "status", "cancel_requested", "created_at"),
          "select case when created_at>now()-interval '15 minutes' then 'under_15m' "
          "when created_at>now()-interval '1 hour' then '15m_to_1h' "
          "when created_at>now()-interval '24 hours' then '1h_to_24h' else '24h_plus' end as age,"
          "cancel_requested,count(agent_id)::bigint as count from platform_control.execution_jobs "
          "where agent_id='hr-bot' and status='queued' group by age,cancel_requested",
          ("age", "cancel_requested"),
          {"age": frozenset({"under_15m", "15m_to_1h", "1h_to_24h", "24h_plus"}),
           "cancel_requested": frozenset({True, False})}),
    _spec("active_hr_workers", "platform_control.execution_workers",
          ("allowed_agent_ids", "status", "last_seen_at"),
          "select case when last_seen_at is null then 'never' "
          "when last_seen_at>now()-interval '15 minutes' then 'under_15m' "
          "when last_seen_at>now()-interval '1 hour' then '15m_to_1h' "
          "when last_seen_at>now()-interval '24 hours' then '1h_to_24h' else '24h_plus' end as last_seen_age,"
          "count(status)::bigint as count from platform_control.execution_workers "
          "where status='active' and 'hr-bot'=any(allowed_agent_ids) group by last_seen_age",
          ("last_seen_age",), {"last_seen_age": frozenset({"never", "under_15m", "15m_to_1h", "1h_to_24h", "24h_plus"})}),
    _spec("old_hr_turn_attempts", "platform_control.turn_attempts",
          ("attempt_id", "turn_id", "status", "executor_kind"),
          "select a.status,a.executor_kind,count(a.turn_id)::bigint as count "
          "from platform_control.turn_attempts a join platform_control.conversation_turns t on t.turn_id=a.turn_id "
          "join platform_control.conversations c on c.conversation_id=t.conversation_id "
          "left join platform_control.direct_command_bindings b on b.attempt_id=a.attempt_id "
          "left join platform_control.execution_jobs j on j.job_id=b.job_id "
          "where (c.mode='direct_agent' and c.direct_agent_id='hr-bot') or j.agent_id='hr-bot' "
          "group by a.status,a.executor_kind",
          ("status", "executor_kind"),
          {"status": frozenset({"queued", "running", "reconciling", "completed", "failed", "cancelled", "interrupted"}),
           "executor_kind": frozenset({"legacy_api_v1", "worker_direct"})},
          (("platform_control.conversation_turns", ("turn_id", "conversation_id")),
           ("platform_control.conversations", ("conversation_id", "mode", "direct_agent_id")),
           ("platform_control.direct_command_bindings", ("attempt_id", "job_id")),
           ("platform_control.execution_jobs", ("job_id", "agent_id")))),
    _spec("new_candidate_position_refs", "platform_hr_agent.candidate_positions",
          ("position_id", "owner_id"),
          "select resolution,count(resolution)::bigint as count from (select case "
          "when p.position_id is not null then 'resolvable' when any_p.position_id is not null "
          "then 'wrong_owner' else 'missing' end as resolution "
          "from platform_hr_agent.candidate_positions r left join platform_hr.positions p "
          "on p.position_id=r.position_id and p.owner_internal_user_id=r.owner_id "
          "left join platform_hr.positions any_p on any_p.position_id=r.position_id) refs group by resolution",
          ("resolution",), {"resolution": frozenset({"resolvable", "wrong_owner", "missing"})},
          (("platform_hr.positions", ("position_id", "owner_internal_user_id")),)),
    _spec("new_result_link_refs", "platform_hr_agent.result_links", ("object_kind", "object_id", "owner_id"),
          "select case when l.object_kind='position' then 'position' else 'unsupported' end as kind,"
          "case when l.object_kind<>'position' then 'unsupported' when p.position_id is not null then 'resolvable' "
          "when any_p.position_id is not null then 'wrong_owner' else 'missing' end as resolution,"
          "count(l.object_kind)::bigint as count from platform_hr_agent.result_links l "
          "left join platform_hr.positions p on l.object_kind='position' and p.position_id=case "
          "when l.object_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
          "then l.object_id::uuid else null end and p.owner_internal_user_id=l.owner_id "
          "left join platform_hr.positions any_p on l.object_kind='position' and any_p.position_id=case "
          "when l.object_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
          "then l.object_id::uuid else null end "
          "group by kind,resolution", ("kind", "resolution"),
          {"kind": frozenset({"position", "unsupported"}),
           "resolution": frozenset({"resolvable", "wrong_owner", "missing", "unsupported"})},
          (("platform_hr.positions", ("position_id", "owner_internal_user_id")),)),
    _spec("new_reference_edges", "platform_hr_agent.reference_edges", ("source_kind",),
          "select source_kind,count(source_kind)::bigint as count from platform_hr_agent.reference_edges group by source_kind",
          ("source_kind",), {"source_kind": frozenset({"material", "method", "result", "intelligence", "standard"})}),
    _spec("intelligence_bundles", "platform_hr.intelligence_bundles", ("bundle_id",),
          "select count(bundle_id)::bigint as count from platform_hr.intelligence_bundles"),
    _spec("intelligence_jobs", "platform_hr.intelligence_bundle_jobs", ("job_id",),
          "select count(job_id)::bigint as count from platform_hr.intelligence_bundle_jobs"),
    _spec("intelligence_current", "platform_hr.intelligence_current_publication", ("bundle_id",),
          "select count(bundle_id)::bigint as count from platform_hr.intelligence_current_publication"),
)


def _relation_parts(relation: str) -> tuple[str, str]:
    return tuple(relation.split(".", 1))  # type: ignore[return-value]


def _check_relation(connection, relation: str, required: tuple[str, ...]) -> tuple[str | None, bool]:
    schema_name, table_name = _relation_parts(relation)
    row = connection.execute(
        "select c.relrowsecurity,c.relforcerowsecurity,r.rolbypassrls,(c.relowner=r.oid) "
        "from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace "
        "join pg_catalog.pg_roles r on r.rolname=current_user "
        "where n.nspname=%s and c.relname=%s and c.relkind in ('r','p','v','m')",
        (schema_name, table_name),
    ).fetchone()
    present = row is not None
    if not present:
        return "missing_table", False
    columns = {
        row[0] for row in connection.execute(
            "select a.attname from pg_catalog.pg_attribute a join pg_catalog.pg_class c on c.oid=a.attrelid "
            "join pg_catalog.pg_namespace n on n.oid=c.relnamespace where n.nspname=%s and c.relname=%s "
            "and a.attnum>0 and not a.attisdropped", (schema_name, table_name),
        )
    }
    if any(column not in columns for column in required):
        return "missing_column", False
    table_readable = connection.execute(
        "select has_table_privilege(current_user,%s,'SELECT')", (relation,),
    ).fetchone()[0]
    columns_readable = all(
        connection.execute(
            "select has_column_privilege(current_user,%s,%s,'SELECT')",
            (relation, column),
        ).fetchone()[0]
        for column in required
    )
    readable = table_readable or columns_readable
    if not readable:
        return "unreadable", False
    rls_limited = bool(row[0] and not row[2] and (not row[3] or row[1]))
    return None, rls_limited


def _precheck(connection, spec: QuerySpec) -> tuple[str | None, str]:
    limited = False
    for relation, columns in ((spec.relation, spec.columns), *spec.dependencies):
        blocked, relation_limited = _check_relation(connection, relation, columns)
        if blocked:
            return blocked, "scope_limited"
        limited = limited or relation_limited
    return None, "scope_limited" if limited else "complete"


def _safe_value(spec: QuerySpec, column: str, value):
    allowed = (spec.allowed_states or {}).get(column, frozenset())
    return value if value in allowed else "unknown"


def _execute_one(connection, spec: QuerySpec) -> dict:
    connection.execute("savepoint hr_inventory_item")
    try:
        blocked, scope = _precheck(connection, spec)
        if blocked:
            return {"name": spec.name, "relation": spec.relation, "status": blocked}
        cursor = connection.execute(spec.sql)
        names = [column.name for column in cursor.description]
        combined: dict[str, dict] = {}
        total = 0
        for row in cursor.fetchall():
            record = dict(zip(names, row))
            count = int(record.pop("count"))
            total += count
            state = {
                key: _safe_value(spec, key, record[key]) for key in spec.state_columns
            }
            key = json.dumps(state, sort_keys=True)
            combined.setdefault(key, {"state": state, "count": 0})["count"] += count
        groups = [combined[key] for key in sorted(combined)]
        result = {"name": spec.name, "relation": spec.relation, "status": "ok", "scope": scope, "total": total}
        if spec.state_columns:
            result["groups"] = groups
        return result
    except psycopg.Error:
        connection.execute("rollback to savepoint hr_inventory_item")
        return {"name": spec.name, "relation": spec.relation, "status": "query_error"}
    finally:
        connection.execute("release savepoint hr_inventory_item")


def run_inventory(connection_factory: Callable):
    connection = connection_factory()
    try:
        connection.execute("begin read only")
        connection.execute("set local statement_timeout='15s'")
        connection.execute("set local lock_timeout='2s'")
        connection.execute("set local idle_in_transaction_session_timeout='30s'")
        transaction_read_only = connection.execute("show transaction_read_only").fetchone()[0] == "on"
        if not transaction_read_only:
            raise RuntimeError("read_only_transaction_required")
        assets = [_execute_one(connection, spec) for spec in QUERY_REGISTRY]
        diagnostics = {
            name: sum(item["status"] == name for item in assets)
            for name in ("missing_table", "missing_column", "unreadable", "query_error")
        }
        return {
            "schema_version": 1,
            "transaction": "read_only_rolled_back",
            "transaction_read_only": transaction_read_only,
            "assets": assets,
            "diagnostics": diagnostics,
        }
    finally:
        connection.rollback()
        connection.close()


def _safe_input(path: Path) -> Path:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ValueError("dsn_file_unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("dsn_file_unsafe")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise ValueError("dsn_file_mode")
    return path


def _write_output(path: Path, payload: str) -> None:
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("output_path_unsafe")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate-only HR handover inventory")
    parser.add_argument("--dsn-file", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        dsn_path = _safe_input(args.dsn_file)
        dsn = dsn_path.read_text(encoding="utf-8").strip()
        if not dsn:
            raise ValueError("dsn_file_empty")
        report = run_inventory(lambda: psycopg.connect(dsn))
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            _write_output(args.output, payload)
        else:
            print(payload, end="")
    except (ValueError, OSError, psycopg.Error, RuntimeError):
        parser.error("inventory_failed")


if __name__ == "__main__":
    main()
