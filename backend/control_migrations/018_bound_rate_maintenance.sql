create or replace function platform_control.purge_expired_control_state()
returns table (
  audit_events bigint,
  login_attempts bigint,
  web_sessions bigint,
  rate_buckets bigint
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  audit_events := platform_control.retain_audit_events(
    clock_timestamp() - interval '365 days'
  );
  delete from platform_control.login_attempts where expires_at < now();
  get diagnostics login_attempts = row_count;
  delete from platform_control.web_sessions where absolute_expires_at < now();
  get diagnostics web_sessions = row_count;
  -- Rate buckets are intentionally excluded. The maintenance CLI calls the
  -- bounded, SKIP LOCKED function from migration 017 separately.
  rate_buckets := 0;
  return next;
end
$function$;

revoke all on function platform_control.purge_expired_control_state()
from public;

do $migration$
declare
  selected_maintenance name;
  role_name text;
begin
  selected_maintenance := case current_database()
    when 'agent_platform_control' then 'platform_control_maintenance'
    when 'agent_platform_control_preview' then 'platform_control_maintenance_preview'
    else null
  end;
  if selected_maintenance is null then
    raise insufficient_privilege using message='unsupported control environment';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.purge_expired_control_state() '
      'from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.purge_expired_control_state() '
    'to %I',selected_maintenance
  );
end
$migration$;
