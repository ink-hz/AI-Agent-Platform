alter table platform_control.execution_jobs
  add column stop_requested_status text,
  add column stop_acknowledged_at timestamptz,
  add constraint execution_jobs_stop_status_v30 check (
    stop_requested_status is null
    or stop_requested_status in ('cancelled','interrupted')
  ),
  add constraint execution_jobs_stop_delivery_v30 check (
    (
      stop_requested_status is null
      and stop_acknowledged_at is null
    ) or (
      stop_requested_status = 'cancelled'
      and cancel_requested
      and lease_worker_id is not null
      and status in ('leased','dispatched','running','cancelled')
    ) or (
      stop_requested_status = 'interrupted'
      and cancel_requested
      and lease_worker_id is not null
      and status = 'interrupted'
    )
  );

update platform_control.execution_jobs
set stop_requested_status='cancelled'
where cancel_requested
  and status in ('leased','dispatched','running');

update platform_control.execution_jobs
set stop_requested_status='interrupted'
where cancel_requested
  and status='interrupted'
  and lease_worker_id is not null;

create index execution_jobs_pending_stop_v30
  on platform_control.execution_jobs (lease_worker_id,run_id)
  where stop_requested_status is not null
    and stop_acknowledged_at is null;

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

  if not has_table_privilege(
    selected_app,
    'platform_control.execution_jobs',
    'select,insert,update'
  ) then
    raise insufficient_privilege using
      message = 'execution relay app privileges unavailable';
  end if;
end
$migration$;
