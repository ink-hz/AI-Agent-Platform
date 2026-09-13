#!/bin/bash
set -euo pipefail

fail() {
  echo "EXECUTION_JOB_KIND_PREFLIGHT_FAILED database=${database_name:-unknown}" >&2
  exit 1
}

[[ $# -eq 2 || ( $# -eq 3 && "$3" == "--baseline95" ) ]] || fail
baseline95="${3:-}"
postgres_container="$1"
database_name="$2"
[[ -n "$postgres_container" ]] || fail
[[ "$database_name" == "agent_platform_control" \
  || "$database_name" == "agent_platform_control_preview" ]] || fail

psql_readonly=(
  "${HR_MIGRATION_DOCKER:-/usr/bin/docker}" exec
  -e "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000"
  "$postgres_container"
  psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d "$database_name"
)

table_state="$("${psql_readonly[@]}" -c \
  "select concat(
     case when to_regclass('platform_control.execution_jobs') is not null
       then '1' else '0' end, ':',
     case when to_regclass('platform_control.mission_runs') is not null
       then '1' else '0' end, ':',
     case when exists (
       select 1 from information_schema.columns
       where table_schema='platform_control'
         and table_name='execution_jobs'
         and column_name='job_kind'
     ) then '1' else '0' end
   )")" || fail

case "$table_state" in
  0:0:0)
    echo "EXECUTION_JOB_KIND_PREFLIGHT_OK database=$database_name state=fresh"
    exit 0
    ;;
  1:1:1)
    if [[ "$baseline95" == "--baseline95" ]]; then
      # This branch is called only after the host verifies all 001..095 hashes.
      # 091 extends 042's CHECK; legacy bootstrap classification stays unchanged.
      constraint_state="$("${psql_readonly[@]}" -c \
        "select coalesce(bool_and(convalidated and pg_get_constraintdef(oid) =
         'CHECK ((job_kind = ANY (ARRAY[''legacy_brain''::text, ''direct_agent''::text, ''metabot_local''::text, ''worker_direct_v5''::text])))'),false)
         from pg_constraint where conrelid='platform_control.execution_jobs'::regclass
         and conname='execution_jobs_job_kind_v42' and contype='c'")" || fail
      [[ "$constraint_state" == "t" ]] || fail
      invalid_count="$("${psql_readonly[@]}" -c \
        "select count(*) from platform_control.execution_jobs j
         where j.job_kind is null
            or j.job_kind not in ('legacy_brain','direct_agent','metabot_local','worker_direct_v5')
            or (j.job_kind='worker_direct_v5' and (
              j.agent_id is distinct from 'hr-bot' or not exists (
                select 1 from platform_control.direct_command_bindings b
                join platform_control.turn_attempts a using(attempt_id)
                join platform_control.conversation_turns t using(turn_id)
                where b.job_id=j.job_id and a.transport_run_id=j.run_id
                  and b.conversation_id=t.conversation_id
                  and a.executor_kind='worker_direct' and t.execution_owner='worker_direct'
              )
            ))")" || fail
      [[ "$invalid_count" == "0" ]] || fail
      echo "EXECUTION_JOB_KIND_PREFLIGHT_OK database=$database_name state=classified"
      exit 0
    fi
    invalid_count="$("${psql_readonly[@]}" -c \
      "select count(*) from platform_control.execution_jobs
       where job_kind is null
          or job_kind not in ('legacy_brain','direct_agent','metabot_local')")" \
      || fail
    [[ "$invalid_count" == "0" ]] || {
      echo "migration_042_existing_classification_invalid=$invalid_count" >&2
      fail
    }
    echo "EXECUTION_JOB_KIND_PREFLIGHT_OK database=$database_name state=classified"
    exit 0
    ;;
  1:1:0)
    orphan_count="$("${psql_readonly[@]}" -c \
      "select count(*)
       from platform_control.execution_jobs job
       left join platform_control.mission_runs run_row
         on run_row.run_id=job.run_id
       where run_row.run_id is null")" || fail
    unknown_phase_count="$("${psql_readonly[@]}" -c \
      "select count(*)
       from platform_control.execution_jobs job
       join platform_control.mission_runs run_row
         on run_row.run_id=job.run_id
       where run_row.phase is null
          or run_row.phase not in (
            'direct','summary','planning','professional','synthesis'
          )")" \
      || fail
    if [[ "$orphan_count" != "0" || "$unknown_phase_count" != "0" ]]; then
      echo "migration_042_orphan=$orphan_count" >&2
      echo "migration_042_unknown_phase=$unknown_phase_count" >&2
      "${psql_readonly[@]}" -F $'\t' -c \
        "select
           case
             when run_row.run_id is null then 'missing_mission_run'
             else 'unknown_phase'
           end as classification_issue,
           coalesce(run_row.phase,'<missing>') as phase,
           count(*) as affected_rows
         from platform_control.execution_jobs job
         left join platform_control.mission_runs run_row
           on run_row.run_id=job.run_id
         where run_row.run_id is null
            or run_row.phase is null
            or run_row.phase not in (
              'direct','summary','planning','professional','synthesis'
            )
         group by classification_issue,coalesce(run_row.phase,'<missing>')
         order by classification_issue,phase" >&2 || fail
      fail
    fi
    echo "EXECUTION_JOB_KIND_PREFLIGHT_OK database=$database_name state=ready"
    exit 0
    ;;
  *)
    echo "migration_042_schema_state=$table_state" >&2
    fail
    ;;
esac
