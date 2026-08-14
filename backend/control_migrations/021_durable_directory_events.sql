create table platform_control.worker_heartbeats (
  worker_name text primary key check (
    worker_name in ('dingtalk-directory-event')
  ),
  status text not null check (status in ('healthy','degraded')),
  last_error_code text check (
    last_error_code is null or length(last_error_code) between 1 and 64
  ),
  last_seen_at timestamptz not null
);

create table platform_control.directory_event_subject_state (
  subject_kind text not null check (subject_kind='employee'),
  lookup_key_version integer not null check (lookup_key_version > 0),
  lookup_hmac bytea not null check (octet_length(lookup_hmac)=32),
  last_event_at timestamptz not null,
  last_event_key text not null check (
    length(last_event_key)=64 and last_event_key ~ '^[0-9a-f]{64}$'
  ),
  last_event_type text not null check (last_event_type='user_leave_org'),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (subject_kind,lookup_key_version,lookup_hmac)
);

create function platform_control.insert_stream_event_v21(
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

create function platform_control.apply_directory_departure_v21(
  selected_lookup_version integer,
  selected_lookup_hmac bytea,
  selected_event_at timestamptz,
  selected_event_key text
) returns text
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  previous record;
  selected_internal_user_id uuid;
begin
  if selected_lookup_version is null or selected_lookup_version <= 0
     or selected_lookup_hmac is null
     or octet_length(selected_lookup_hmac) <> 32
     or selected_event_at is null
     or selected_event_at > clock_timestamp() + interval '10 minutes'
     or selected_event_key is null
     or length(selected_event_key) <> 64
     or selected_event_key !~ '^[0-9a-f]{64}$'
  then
    raise check_violation using message='directory event invalid';
  end if;

  perform platform_control.lock_dingtalk_identity_directory();
  select last_event_at,last_event_key into previous
    from platform_control.directory_event_subject_state
    where subject_kind='employee'
      and lookup_key_version=selected_lookup_version
      and lookup_hmac=selected_lookup_hmac
    for update;

  if found then
    if previous.last_event_key=selected_event_key then
      return 'already_applied';
    end if;
    if previous.last_event_at > selected_event_at
       or (
         previous.last_event_at=selected_event_at
         and previous.last_event_key > selected_event_key
       )
    then
      return 'stale';
    end if;
  end if;

  select identity.internal_user_id into selected_internal_user_id
    from platform_control.provider_identities identity
    where identity.subject_kind='employee'
      and identity.lookup_key_version=selected_lookup_version
      and identity.lookup_hmac=selected_lookup_hmac
    for update;

  insert into platform_control.directory_event_subject_state (
    subject_kind,lookup_key_version,lookup_hmac,last_event_at,
    last_event_key,last_event_type,updated_at
  ) values (
    'employee',selected_lookup_version,selected_lookup_hmac,selected_event_at,
    selected_event_key,'user_leave_org',clock_timestamp()
  ) on conflict (subject_kind,lookup_key_version,lookup_hmac) do update set
    last_event_at=excluded.last_event_at,
    last_event_key=excluded.last_event_key,
    last_event_type=excluded.last_event_type,
    updated_at=excluded.updated_at;

  if selected_internal_user_id is null then
    return 'member_not_found';
  end if;

  update platform_control.internal_users
    set status='inactive',
        locally_invalidated_at=coalesce(locally_invalidated_at,clock_timestamp()),
        updated_at=clock_timestamp()
    where internal_user_id=selected_internal_user_id;
  update platform_control.web_sessions
    set revoked_at=coalesce(revoked_at,clock_timestamp()),
        revoked_reason=coalesce(revoked_reason,'dingtalk_departure')
    where internal_user_id=selected_internal_user_id and revoked_at is null;
  return 'applied';
exception when no_data_found or too_many_rows then
  raise check_violation using message='directory event ambiguous';
end
$function$;

revoke all on table platform_control.worker_heartbeats from public;
revoke all on table platform_control.directory_event_subject_state from public;
revoke all on function platform_control.insert_stream_event_v21(
  text,text,bytea,integer
) from public;
revoke all on function platform_control.apply_directory_departure_v21(
  integer,bytea,timestamptz,text
) from public;

do $migration$
declare
  selected_app name;
  selected_directory name;
  selected_stream name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app:='platform_control_app';
    selected_directory:='platform_directory_worker';
    selected_stream:='platform_stream_ingest';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then
    selected_app:='platform_control_app_preview';
    selected_directory:='platform_directory_worker_preview';
    selected_stream:='platform_stream_ingest_preview';
  else
    raise insufficient_privilege using
      message='directory event owner/environment mismatch';
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
      'revoke all on platform_control.worker_heartbeats from %I',role_name
    );
    execute format(
      'revoke all on platform_control.directory_event_subject_state from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.insert_stream_event_v21('
      'text,text,bytea,integer) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.apply_directory_departure_v21('
      'integer,bytea,timestamptz,text) from %I',role_name
    );
  end loop;

  execute format(
    'revoke insert on platform_control.stream_inbox from %I',selected_stream
  );
  execute format(
    'revoke usage,select on sequence platform_control.stream_inbox_inbox_id_seq '
    'from %I',selected_stream
  );
  execute format(
    'grant execute on function platform_control.insert_stream_event_v21('
    'text,text,bytea,integer) to %I',selected_stream
  );
  execute format(
    'grant select on platform_control.worker_heartbeats to %I',selected_app
  );
  execute format(
    'grant select,insert,update on platform_control.worker_heartbeats to %I',
    selected_directory
  );
  execute format(
    'grant execute on function platform_control.apply_directory_departure_v21('
    'integer,bytea,timestamptz,text) to %I',selected_directory
  );
end
$migration$;
