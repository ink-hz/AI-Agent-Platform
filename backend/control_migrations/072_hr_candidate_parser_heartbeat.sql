alter table platform_control.worker_heartbeats
  drop constraint worker_heartbeats_worker_name_check,
  add constraint worker_heartbeats_worker_name_check check (worker_name in (
    'dingtalk-directory-event','agent-brain-step','agent-brain-adapter',
    'agent-brain-reaper','hr-candidate-parser'
  ));

create function platform_control.upsert_brain_worker_heartbeat_v72(
  selected_worker_name text,
  selected_status text,
  selected_last_error_code text,
  selected_last_seen_at timestamptz
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Brain heartbeat caller invalid';
  end if;
  if selected_worker_name not in (
       'agent-brain-step','agent-brain-adapter','agent-brain-reaper',
       'hr-candidate-parser'
     )
     or selected_status not in ('healthy','degraded')
     or selected_last_seen_at is null
     or selected_last_seen_at > clock_timestamp() + interval '10 minutes'
     or (
       selected_last_error_code is not null
       and (
         char_length(selected_last_error_code) not between 1 and 64
         or selected_last_error_code !~ '^[a-z0-9_]+$'
       )
     )
  then
    raise check_violation using message = 'Brain heartbeat invalid';
  end if;
  insert into platform_control.worker_heartbeats (
    worker_name,status,last_error_code,last_seen_at
  ) values (
    selected_worker_name,selected_status,selected_last_error_code,
    selected_last_seen_at
  ) on conflict (worker_name) do update set
    status=excluded.status,
    last_error_code=excluded.last_error_code,
    last_seen_at=excluded.last_seen_at;
  return true;
end
$function$;

revoke all on function platform_control.upsert_brain_worker_heartbeat_v72(
  text,text,text,timestamptz
) from public;

do $migration$
declare selected_brain name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_brain := 'platform_brain_worker';
  elsif current_database() = 'agent_platform_control_preview'
        and current_user = 'platform_control_owner_preview'
  then
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message = 'HR candidate parser heartbeat migration owner/environment mismatch';
  end if;
  execute format(
    'grant execute on function '
    'platform_control.upsert_brain_worker_heartbeat_v72('
    'text,text,text,timestamptz) to %I', selected_brain
  );
end
$migration$;
