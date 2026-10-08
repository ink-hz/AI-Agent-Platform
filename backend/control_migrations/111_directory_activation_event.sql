create or replace function platform_control.insert_stream_event_v21(
  selected_event_key text,
  selected_event_type text,
  selected_encrypted_payload bytea,
  selected_encryption_key_version integer
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  affected bigint;
begin
  if selected_event_key is null
     or length(selected_event_key) <> 64
     or selected_event_key !~ '^[0-9a-f]{64}$'
     or selected_event_type is null
     or selected_event_type not in (
       'user_add_org','user_modify_org','user_leave_org','org_user_active',
       'user_active_org',
       'org_dept_create','org_dept_modify','org_dept_remove','unapproved'
     )
     or selected_encrypted_payload is null
     or octet_length(selected_encrypted_payload) not between 28 and 262172
     or selected_encryption_key_version is null
     or selected_encryption_key_version <= 0
  then
    raise check_violation using message='stream event invalid';
  end if;
  insert into platform_control.stream_inbox (
    event_key,event_type,encrypted_payload,encryption_key_version
  ) values (
    selected_event_key,selected_event_type,selected_encrypted_payload,
    selected_encryption_key_version
  ) on conflict(event_key) do nothing;
  get diagnostics affected = row_count;
  return affected=1;
end
$function$;
