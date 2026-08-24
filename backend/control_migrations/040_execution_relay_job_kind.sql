alter table platform_control.execution_jobs
  add column job_kind text;

update platform_control.execution_jobs job
set job_kind = case
  when run_row.phase = 'direct' then 'direct_agent'
  else 'legacy_brain'
end
from platform_control.mission_runs run_row
where run_row.run_id = job.run_id;

do $migration$
begin
  if exists (
    select 1 from platform_control.execution_jobs where job_kind is null
  ) then
    raise check_violation using
      message = 'execution relay jobs require manual job-kind classification';
  end if;
end
$migration$;

alter table platform_control.execution_jobs
  alter column job_kind set default 'legacy_brain',
  alter column job_kind set not null,
  add constraint execution_jobs_job_kind_v40 check (
    job_kind in ('legacy_brain','direct_agent','metabot_local')
  );

create index execution_jobs_kind_status_created_v40
  on platform_control.execution_jobs (job_kind,status,created_at,job_id);
