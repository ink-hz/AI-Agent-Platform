create table platform_control.content_key_canaries (
  key_version integer primary key check (key_version > 0),
  canary_ciphertext bytea not null
    check (octet_length(canary_ciphertext) between 29 and 1048576),
  created_at timestamptz not null default now()
);

revoke all on table platform_control.content_key_canaries from public;

do $migration$
declare
  selected_app text;
  other_role text;
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
    'grant select,insert on platform_control.content_key_canaries to %I',
    selected_app
  );

  foreach other_role in array array[
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
    if other_role <> selected_app then
      execute format(
        'revoke all on table platform_control.content_key_canaries from %I',
        other_role
      );
    end if;
  end loop;
end
$migration$;
