create function platform_control.read_current_account_employee_profile_v40(
  selected_session_id uuid
) returns table(
  internal_user_id uuid,
  generation_id uuid,
  member_key uuid,
  real_name_ciphertext bytea,
  real_name_nonce bytea,
  real_name_encryption_key_version integer,
  mobile_ciphertext bytea,
  mobile_nonce bytea,
  mobile_encryption_key_version integer,
  primary_department_ciphertext bytea,
  primary_department_nonce bytea,
  primary_department_encryption_key_version integer
)
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  select session.internal_user_id,
    member.generation_id,
    member.member_key,
    member.real_name_ciphertext,
    member.real_name_nonce,
    member.real_name_encryption_key_version,
    member.mobile_ciphertext,
    member.mobile_nonce,
    member.mobile_encryption_key_version,
    member.primary_department_ciphertext,
    member.primary_department_nonce,
    member.primary_department_encryption_key_version
  from platform_control.web_sessions session
  left join platform_control.directory_state state on state.singleton
  left join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete' and generation.source_schema_version=3
  left join platform_control.directory_members member
    on member.generation_id=generation.generation_id
   and member.internal_user_id=session.internal_user_id
   and member.status='active'
  where session.session_id=selected_session_id
    and session.revoked_at is null
    and session.idle_expires_at>clock_timestamp()
    and session.absolute_expires_at>clock_timestamp();
$function$;

revoke all on function
  platform_control.read_current_account_employee_profile_v40(uuid)
from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='account employee profile projection owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function '
      'platform_control.read_current_account_employee_profile_v40(uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'revoke select on platform_control.directory_members from %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.read_current_account_employee_profile_v40(uuid) to %I',
    selected_app
  );
end
$migration$;
