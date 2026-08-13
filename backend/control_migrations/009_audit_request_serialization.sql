create function platform_control.audit_terminal_result(selected_request_id uuid)
returns text
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  select event.result
  from platform_control.audit_events event
  where event.request_id = selected_request_id
    and event.result in ('completed', 'failed')
  limit 1
$function$;

revoke all on function platform_control.audit_terminal_result(uuid) from public;

do $migration$
declare
  selected_audit name;
begin
  selected_audit := case current_database()
    when 'agent_platform_control' then 'platform_audit_append'
    when 'agent_platform_control_preview' then 'platform_audit_append_preview'
    else null
  end;
  if selected_audit is null then
    raise exception 'unsupported control database: %', current_database();
  end if;
  execute format(
    'grant execute on function platform_control.audit_terminal_result(uuid) to %I',
    selected_audit
  );
end
$migration$;
