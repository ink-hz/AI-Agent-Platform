from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).parents[1] / "control_migrations"
MIGRATION = MIGRATIONS / "079_hr_panorama_intelligence.sql"
PLAN = (
    Path(__file__).parents[2]
    / "docs/superpowers/plans/2026-09-04-hr-p0-panorama-intelligence.md"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def _migration_objects(sql: str) -> set[str]:
    return set(re.findall(r"create (?:table|function) platform_hr\.([a-z0-9_]+)", sql))


def test_v79_is_the_contiguous_panorama_migration() -> None:
    versions = sorted(
        int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql")
    )

    assert MIGRATION.is_file(), f"missing migration: {MIGRATION}"
    assert versions[-2:] == [78, 79]
    assert len(versions) == len(set(versions))
    assert versions == list(range(1, 80))


def test_migration_defines_the_panorama_data_contract() -> None:
    sql = _sql()

    assert {
        "talent_sources",
        "panorama_runs",
        "panorama_run_sources",
        "public_job_snapshots",
        "public_job_snapshot_requests",
        "public_job_current_snapshots",
        "talent_insight_versions",
        "talent_insight_sources",
        "talent_insight_snapshots",
        "position_insight_retrievals",
        "position_insight_retrieval_versions",
    } <= _migration_objects(sql)
    for column in (
        "company_key text not null",
        "canonical_name text not null",
        "aliases jsonb not null",
        "approved_public_urls jsonb not null",
        "active boolean not null",
        "selected_source_ids uuid[] not null",
        "conversation_id uuid not null",
        "source_failures jsonb not null",
        "public_job_key text not null",
        "duty_excerpt text not null",
        "requirement_excerpt text not null",
        "source_url text not null",
        "observed_at timestamptz not null",
        "content_sha256 text not null",
        "snapshot_ids uuid[] not null",
        "facts jsonb not null",
        "inferences jsonb not null",
        "unknowns jsonb not null",
        "direction_clusters jsonb not null",
        "summary text not null",
        "source_turn_id uuid not null",
        "agent_id text not null",
        "model_version text not null",
        "position_id uuid not null",
        "insight_version_ids uuid[] not null",
        "query_sha256 text not null",
        "retrieved_excerpts jsonb not null",
    ):
        assert column in sql


def test_migration_enforces_owner_exact_references_and_bounded_values() -> None:
    sql = _sql()

    for referenced_table, columns in (
        ("platform_control.conversations", "conversation_id,owner_internal_user_id"),
        ("platform_hr.talent_sources", "source_id,owner_internal_user_id"),
        ("platform_hr.panorama_runs", "run_id,owner_internal_user_id"),
        ("platform_hr.public_job_snapshots", "snapshot_id,owner_internal_user_id"),
        (
            "platform_hr.talent_insight_versions",
            "insight_version_id,owner_internal_user_id",
        ),
        ("platform_hr.positions", "position_id,owner_internal_user_id"),
        ("platform_control.conversation_turns", "conversation_id,turn_id"),
    ):
        column_pattern = columns.replace(",", r",\s*")
        pattern = (
            rf"references {re.escape(referenced_table)}\(\s*"
            rf"{column_pattern}\s*\)"
        )
        assert re.search(pattern, sql)
    assert sql.count("octet_length(") >= 8
    assert sql.count("cardinality(") >= 8
    assert "public_https_url_is_valid_v79" in sql
    assert "jsonb_https_url_array_v79" in sql
    assert "url_is_approved_v79" in sql
    assert "selected_url like" not in sql
    assert re.search(r"left\(\s*selected_url,char_length\(approved_url\)\+1\s*\)", sql)
    assert "selected_port<>'443'" in sql
    assert "selected_url ~* '%(2e|2f|5c)'" in sql
    assert "content_sha256 ~ '^[a-f0-9]{64}$'" in sql
    assert "query_sha256 ~ '^[a-f0-9]{64}$'" in sql
    assert "error_code ~ '^[a-z][a-z0-9_]{0,63}$'" in sql
    assert "reason_code !~ '^[a-z][a-z0-9_]{0,63}$'" in sql
    for forbidden in (
        "page_html",
        "page_text",
        "raw_html",
        "raw_content",
        "credential",
        "secret",
        "access_token",
        "cookie",
        "storage_path",
        "object_key",
    ):
        assert f"{forbidden} text" not in sql


def test_snapshot_identity_currentness_and_insights_are_historical() -> None:
    sql = _sql()

    assert (
        "unique (owner_internal_user_id,source_id,public_job_key,content_sha256)" in sql
    )
    assert "create table platform_hr.public_job_current_snapshots" in sql
    assert "observation_id uuid not null" in sql
    assert "latest_observation_id uuid not null" in sql
    assert (
        "(selected_observed_at,selected_client_request_id)> "
        "(current_observed_at,current_observation_id)" in sql
    )
    assert "update platform_hr.public_job_snapshots" not in sql
    assert "guard_public_job_snapshot_immutability_v79" in sql
    assert "guard_public_job_observation_immutability_v79" in sql
    assert "guard_talent_insight_immutability_v79" in sql
    assert "talent insight version is immutable" in sql
    assert "before update or delete on platform_hr.talent_insight_versions" in sql
    assert (
        sql.count(
            "execute function platform_hr.guard_talent_insight_immutability_v79()"
        )
        >= 1
    )
    assert "populate_talent_insight_links_v79" in sql
    assert "after insert on platform_hr.talent_insight_versions" in sql
    assert "guard_talent_insight_links_v79" in sql
    assert "populate_position_insight_retrieval_links_v79" in sql
    assert "after insert on platform_hr.position_insight_retrievals" in sql
    assert "guard_position_insight_retrieval_immutability_v79" in sql
    assert "guard_position_insight_retrieval_links_v79" in sql
    assert "platform_hr.jsonb_object_size_v79(fact)<>6" in sql
    assert "platform_hr.jsonb_object_size_v79(inference)<>2" in sql
    assert "platform_hr.jsonb_object_size_v79(unknown_item)<>1" in sql
    assert "talent insight fact observation binding invalid" in sql
    assert "delete from platform_hr.public_job_snapshots" not in sql
    assert (
        "state in ( 'queued','running','completed','partially_completed','failed' )"
        in sql
    )
    assert "old.state='queued' and selected_state='running'" in sql
    assert (
        "old.state='running' and selected_state in ( 'completed','partially_completed','failed' )"
        in sql
    )


def test_migration_exposes_only_app_panorama_entrypoints() -> None:
    sql = _sql()
    functions = {
        "create_talent_source_v79",
        "list_talent_sources_v79",
        "create_panorama_run_v79",
        "list_panorama_runs_v79",
        "transition_panorama_run_v79",
        "create_public_job_snapshot_v79",
        "list_public_job_snapshots_v79",
        "create_talent_insight_version_v79",
        "list_talent_insight_versions_v79",
        "create_position_insight_retrieval_v79",
        "list_position_insight_retrievals_v79",
        "read_talent_sources_v79",
        "read_panorama_run_v79",
        "read_public_job_snapshots_v79",
        "read_talent_insight_version_v79",
        "list_talent_insight_versions_page_v79",
    }

    assert functions <= _migration_objects(sql)
    for function in functions:
        assert f"revoke all on function platform_hr.{function}" in sql
        assert f"grant execute on function platform_hr.{function}" in sql
    assert sql.count(
        "session_user not in ('platform_control_app','platform_control_app_preview')"
    ) >= len(functions)
    assert "grant execute on function platform_hr." in sql
    assert "selected_app" in sql
    assert "selected_brain" not in sql
    assert "platform_control_maintenance" not in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql
    assert "revoke all on all tables in schema platform_hr from public" in sql


def test_point_and_keyset_reads_are_owner_scoped_bounded_and_stable() -> None:
    sql = _sql()

    for marker in (
        "source.owner_internal_user_id=selected_owner_internal_user_id",
        "run.owner_internal_user_id=selected_owner_internal_user_id",
        "snapshot.owner_internal_user_id=selected_owner_internal_user_id",
        "insight.owner_internal_user_id=selected_owner_internal_user_id",
        "selected_limit not between 1 and 100",
        "insight.version_number<selected_before_version_number",
        "order by insight.version_number desc,insight.insight_version_id",
        "with ordinality requested",
    ):
        assert marker in sql
    assert "grant select on platform_hr" not in sql


def test_create_and_transition_entrypoints_have_durable_idempotency_guards() -> None:
    sql = _sql()

    for message in (
        "talent source idempotency payload mismatch",
        "panorama run idempotency payload mismatch",
        "public job snapshot idempotency payload mismatch",
        "panorama run transition idempotency payload mismatch",
        "talent insight idempotency payload mismatch",
        "position insight retrieval idempotency payload mismatch",
    ):
        assert message in sql
    assert sql.count("pg_advisory_xact_lock") >= 6
    assert "panorama run transition conflict" in sql
    assert "public_job_snapshot_requests" in sql
    assert "panorama_run_transition_events" in sql


def test_task_4_requires_runtime_public_network_enforcement() -> None:
    plan = " ".join(PLAN.read_text(encoding="utf-8").lower().split())

    for requirement in (
        "parse every approved url before fetch",
        "reject every non-public ip address before connecting",
        "revalidate dns and destination before every redirect",
        "bounded redirect count and approved ports",
        "network egress controls",
        "sql validation cannot prevent dns rebinding",
    ):
        assert requirement in plan
