alter table platform_control.mission_runs
  add column relay_event_cursor integer not null default 0
    check (relay_event_cursor >= 0);

do $constraint$
declare
  selected_constraint text;
begin
  select constraint_row.conname into selected_constraint
  from pg_catalog.pg_constraint constraint_row
  where constraint_row.conrelid =
        'platform_control.execution_jobs'::regclass
    and constraint_row.contype = 'c'
    and pg_catalog.pg_get_constraintdef(constraint_row.oid)
        like '%lease_worker_id%'
  order by constraint_row.conname
  limit 1;
  if selected_constraint is null then
    raise undefined_object using
      message = 'execution job lease constraint unavailable';
  end if;
  execute format(
    'alter table platform_control.execution_jobs drop constraint %I',
    selected_constraint
  );
end
$constraint$;

alter table platform_control.execution_jobs
  add constraint execution_jobs_lease_shape_v30 check (
    (
      status = 'queued'
      and lease_worker_id is null
      and lease_expires_at is null
    ) or (
      status <> 'queued'
      and lease_worker_id is not null
    ) or (
      status in ('cancelled', 'interrupted')
      and lease_worker_id is null
      and lease_expires_at is null
    )
  );

do $migration$
declare
  selected_app text;
begin
  case current_user
    when 'platform_control_owner' then selected_app := 'platform_control_app';
    when 'platform_control_owner_preview' then
      selected_app := 'platform_control_app_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;

  execute format(
    'grant update (relay_event_cursor) '
    'on platform_control.mission_runs to %I', selected_app
  );
end
$migration$;
