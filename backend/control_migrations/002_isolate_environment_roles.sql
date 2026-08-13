do $migration$
declare
  selected_suffix text;
  selected_app text;
  selected_directory text;
  selected_stream text;
  selected_audit text;
  selected_maintenance text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then
      selected_suffix := '';
    when 'platform_control_owner_preview' then
      selected_suffix := '_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;

  selected_app := 'platform_control_app' || selected_suffix;
  selected_directory := 'platform_directory_worker' || selected_suffix;
  selected_stream := 'platform_stream_ingest' || selected_suffix;
  selected_audit := 'platform_audit_append' || selected_suffix;
  selected_maintenance := 'platform_control_maintenance' || selected_suffix;

  foreach role_name in array array[
    'platform_control_migrator',
    'platform_control_app',
    'platform_directory_worker',
    'platform_stream_ingest',
    'platform_audit_append',
    'platform_control_maintenance',
    'platform_control_migrator_preview',
    'platform_control_app_preview',
    'platform_directory_worker_preview',
    'platform_stream_ingest_preview',
    'platform_audit_append_preview',
    'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on schema platform_control from %I',
      role_name
    );
    execute format(
      'revoke all on all tables in schema platform_control from %I',
      role_name
    );
    execute format(
      'revoke all on all sequences in schema platform_control from %I',
      role_name
    );
    execute format(
      'revoke all on all functions in schema platform_control from %I',
      role_name
    );
  end loop;

  execute format(
    'grant usage on schema platform_control to %I, %I, %I, %I, %I',
    selected_app,
    selected_directory,
    selected_stream,
    selected_audit,
    selected_maintenance
  );

  execute format(
    'grant select on all tables in schema platform_control to %I',
    selected_app
  );
  execute format(
    'grant insert, update on '
    'platform_control.internal_users, '
    'platform_control.provider_identities, '
    'platform_control.login_attempts, '
    'platform_control.web_sessions, '
    'platform_control.observation_grants, '
    'platform_control.auth_rate_buckets to %I',
    selected_app
  );

  execute format(
    'grant select, insert, update on '
    'platform_control.provider_identities, '
    'platform_control.directory_generations, '
    'platform_control.directory_state, '
    'platform_control.directory_members, '
    'platform_control.directory_departments, '
    'platform_control.department_closure, '
    'platform_control.member_departments, '
    'platform_control.stream_inbox, '
    'platform_control.sync_runs to %I',
    selected_directory
  );
  execute format(
    'grant select on platform_control.internal_users to %I',
    selected_directory
  );
  execute format(
    'grant update ('
    'display_name, status, last_confirmed_generation_id, '
    'locally_invalidated_at, updated_at'
    ') on platform_control.internal_users to %I',
    selected_directory
  );
  execute format(
    'grant delete on '
    'platform_control.directory_members, '
    'platform_control.directory_departments, '
    'platform_control.department_closure, '
    'platform_control.member_departments to %I',
    selected_directory
  );

  execute format(
    'grant insert on platform_control.stream_inbox to %I',
    selected_stream
  );
  execute format(
    'grant usage, select on sequence '
    'platform_control.stream_inbox_inbox_id_seq to %I, %I',
    selected_stream,
    selected_directory
  );
  execute format(
    'grant execute on function platform_control.append_audit_event('
    'uuid, uuid, text, text, text, uuid, text, text, jsonb) to %I',
    selected_audit
  );
  execute format(
    'grant execute on function '
    'platform_control.retain_audit_events(timestamptz) to %I',
    selected_maintenance
  );
end
$migration$;
