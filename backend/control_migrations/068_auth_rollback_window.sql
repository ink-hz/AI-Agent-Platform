revoke all on function platform_control.consume_attempt_and_issue_session_v22(
  uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app:='platform_control_app';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview'
  then
    selected_app:='platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='auth rollback window owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_brain_worker','platform_control_migrator_preview',
    'platform_control_app_preview','platform_directory_worker_preview',
    'platform_stream_ingest_preview','platform_audit_append_preview',
    'platform_control_maintenance_preview','platform_brain_worker_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.consume_attempt_and_issue_session_v22('
      'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.consume_attempt_and_issue_session_v22('
    'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) to %I',
    selected_app
  );
end
$migration$;
