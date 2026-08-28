create table platform_control.agent_launch_codes (
  launch_code_id uuid primary key,
  code_hash bytea not null check (octet_length(code_hash)=32),
  code_key_version integer not null check (code_key_version>0),
  source_session_id uuid not null
    references platform_control.web_sessions(session_id) on delete cascade,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id) on delete restrict,
  agent_id text not null check (agent_id='ai-fae-agent'),
  identity_binding_id uuid not null unique,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  unique (code_key_version, code_hash)
);

create index agent_launch_codes_active
  on platform_control.agent_launch_codes(expires_at)
  where consumed_at is null;

create table platform_control.agent_identity_bindings (
  identity_binding_id uuid primary key,
  source_session_id uuid not null
    references platform_control.web_sessions(session_id) on delete cascade,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id) on delete restrict,
  agent_id text not null check (agent_id='ai-fae-agent'),
  created_at timestamptz not null default clock_timestamp(),
  last_validated_at timestamptz,
  revoked_at timestamptz,
  unique (identity_binding_id, agent_id)
);

create index agent_identity_bindings_user
  on platform_control.agent_identity_bindings(internal_user_id,agent_id)
  where revoked_at is null;

create function platform_control.issue_agent_launch_v52(
  selected_code_id uuid,
  selected_code_hash bytea,
  selected_code_key_version integer,
  selected_source_session_id uuid,
  selected_internal_user_id uuid,
  selected_agent_id text,
  selected_binding_id uuid,
  selected_ttl_seconds integer
) returns timestamptz
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_expires_at timestamptz := database_now + interval '60 seconds';
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_code_id is null
     or octet_length(selected_code_hash)<>32
     or selected_code_key_version is null or selected_code_key_version<=0
     or selected_source_session_id is null
     or selected_internal_user_id is null
     or selected_agent_id<>'ai-fae-agent'
     or selected_binding_id is null
     or selected_ttl_seconds<>60
  then
    raise check_violation using message='Agent launch input invalid';
  end if;
  if not exists (
    select 1
    from platform_control.web_sessions session
    join platform_control.internal_users users
      on users.internal_user_id=session.internal_user_id
    where session.session_id=selected_source_session_id
      and session.internal_user_id=selected_internal_user_id
      and session.revoked_at is null
      and session.idle_expires_at>database_now
      and session.absolute_expires_at>database_now
      and session.hard_stale_read_only=false
      and users.status='active'
      and users.locally_invalidated_at is null
      and platform_control.has_agent_use_scope_v29(
        selected_internal_user_id,selected_agent_id
      )
  ) then
    raise insufficient_privilege using message='Agent launch denied';
  end if;
  insert into platform_control.agent_launch_codes(
    launch_code_id,code_hash,code_key_version,source_session_id,
    internal_user_id,agent_id,identity_binding_id,expires_at
  ) values (
    selected_code_id,selected_code_hash,selected_code_key_version,
    selected_source_session_id,selected_internal_user_id,selected_agent_id,
    selected_binding_id,selected_expires_at
  );
  return selected_expires_at;
end
$function$;

create function platform_control.exchange_agent_launch_v52(
  selected_code_hash bytea,
  selected_code_key_version integer
) returns table(
  internal_user_id uuid,
  identity_binding_id uuid,
  agent_id text
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected platform_control.agent_launch_codes%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or octet_length(selected_code_hash)<>32
     or selected_code_key_version is null or selected_code_key_version<=0
  then
    return;
  end if;
  select code.* into selected
  from platform_control.agent_launch_codes code
  where code.code_hash=selected_code_hash
    and code.code_key_version=selected_code_key_version
    and code.consumed_at is null
    and code.expires_at>database_now
  for update;
  if not found or not exists (
    select 1
    from platform_control.web_sessions session
    join platform_control.internal_users users
      on users.internal_user_id=session.internal_user_id
    where session.session_id=selected.source_session_id
      and session.internal_user_id=selected.internal_user_id
      and session.revoked_at is null
      and session.idle_expires_at>database_now
      and session.absolute_expires_at>database_now
      and session.hard_stale_read_only=false
      and users.status='active'
      and users.locally_invalidated_at is null
      and platform_control.has_agent_use_scope_v29(
        selected.internal_user_id,selected.agent_id
      )
  ) then
    return;
  end if;
  insert into platform_control.agent_identity_bindings(
    identity_binding_id,source_session_id,internal_user_id,agent_id
  ) values (
    selected.identity_binding_id,selected.source_session_id,
    selected.internal_user_id,selected.agent_id
  );
  update platform_control.agent_launch_codes code
    set consumed_at=database_now
    where code.launch_code_id=selected.launch_code_id;
  return query select selected.internal_user_id,
    selected.identity_binding_id,selected.agent_id;
end
$function$;

create function platform_control.validate_agent_identity_binding_v52(
  selected_binding_id uuid,
  selected_agent_id text
) returns table(
  internal_user_id uuid,
  identity_binding_id uuid,
  agent_id text
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_binding_id is null
     or selected_agent_id<>'ai-fae-agent'
  then
    return;
  end if;
  return query
  update platform_control.agent_identity_bindings binding
    set last_validated_at=database_now
  from platform_control.web_sessions session,
       platform_control.internal_users users
  where binding.identity_binding_id=selected_binding_id
    and binding.agent_id=selected_agent_id
    and binding.revoked_at is null
    and session.session_id=binding.source_session_id
    and session.internal_user_id=binding.internal_user_id
    and session.revoked_at is null
    and session.idle_expires_at>database_now
    and session.absolute_expires_at>database_now
    and session.hard_stale_read_only=false
    and users.internal_user_id=binding.internal_user_id
    and users.status='active'
    and users.locally_invalidated_at is null
    and platform_control.has_agent_use_scope_v29(
      binding.internal_user_id,binding.agent_id
    )
  returning binding.internal_user_id,binding.identity_binding_id,binding.agent_id;
end
$function$;

revoke all on platform_control.agent_launch_codes from public;
revoke all on platform_control.agent_identity_bindings from public;
revoke all on function platform_control.issue_agent_launch_v52(
  uuid,bytea,integer,uuid,uuid,text,uuid,integer
) from public;
revoke all on function platform_control.exchange_agent_launch_v52(
  bytea,integer
) from public;
revoke all on function platform_control.validate_agent_identity_binding_v52(
  uuid,text
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message='Agent launch migration owner invalid';
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
      'revoke all on platform_control.agent_launch_codes, '
      'platform_control.agent_identity_bindings from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.issue_agent_launch_v52('
    'uuid,bytea,integer,uuid,uuid,text,uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.exchange_agent_launch_v52('
    'bytea,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.validate_agent_identity_binding_v52(uuid,text) to %I',
    selected_app
  );
end
$migration$;
