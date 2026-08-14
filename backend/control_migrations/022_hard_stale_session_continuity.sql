create function platform_control.consume_attempt_and_issue_session_v22(
  selected_attempt_id uuid,
  selected_internal_user_id uuid,
  selected_session_id uuid,
  selected_token_hash bytea,
  selected_token_key_version integer,
  selected_csrf_hash bytea,
  selected_csrf_key_version integer,
  selected_idle_seconds integer,
  selected_absolute_seconds integer,
  selected_hard_stale_read_only boolean
) returns table(
  session_id uuid,
  idle_expires_at timestamptz,
  absolute_expires_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_role platform_control.user_role;
  selected_last_complete_at timestamptz;
  actual_hard_stale boolean;
begin
  if selected_hard_stale_read_only is null then return; end if;
  perform platform_control.lock_dingtalk_identity_directory();
  select users.role,state.last_complete_at
    into selected_role,selected_last_complete_at
    from platform_control.internal_users users
    join platform_control.directory_state state on state.singleton
    where users.internal_user_id=selected_internal_user_id
      and users.status='active'
      and users.locally_invalidated_at is null;
  if selected_last_complete_at is null then return; end if;
  actual_hard_stale := (
    selected_last_complete_at <= database_now - interval '24 hours'
  );
  if selected_hard_stale_read_only <> actual_hard_stale
     or (
       actual_hard_stale
       and selected_role not in ('platform_owner','management_viewer')
     )
  then return; end if;

  return query
    select issued.session_id,issued.idle_expires_at,issued.absolute_expires_at
    from platform_control.consume_attempt_and_issue_session(
      selected_attempt_id,selected_internal_user_id,selected_session_id,
      selected_token_hash,selected_token_key_version,selected_csrf_hash,
      selected_csrf_key_version,selected_idle_seconds,selected_absolute_seconds
    ) issued;
  if found then
    update platform_control.web_sessions session
      set hard_stale_read_only=actual_hard_stale
      where session.session_id=selected_session_id;
  end if;
end
$function$;

create function platform_control.authenticate_web_session_v22(
  selected_token_hash bytea,
  selected_token_key_version integer,
  selected_idle_seconds integer
) returns table(
  session_id uuid,
  internal_user_id uuid,
  role text,
  hard_stale_read_only boolean,
  csrf_hash bytea,
  csrf_hash_key_version integer
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
begin
  if octet_length(selected_token_hash) <> 32
     or selected_token_key_version is null
     or selected_token_key_version <= 0
     or selected_idle_seconds <> 28800
  then return; end if;
  perform platform_control.lock_dingtalk_identity_directory();
  return query
  update platform_control.web_sessions session
    set last_seen_at=database_now,
        idle_expires_at=least(
          database_now + interval '28800 seconds',session.absolute_expires_at
        ),
        hard_stale_read_only=(
          state.last_complete_at <= database_now - interval '24 hours'
        )
  from platform_control.internal_users users,
       platform_control.directory_state state,
       platform_control.directory_generations generation,
       platform_control.directory_members member
  where session.token_hash=selected_token_hash
    and session.token_hash_key_version=selected_token_key_version
    and session.revoked_at is null
    and session.idle_expires_at > database_now
    and session.absolute_expires_at > database_now
    and users.internal_user_id=session.internal_user_id
    and users.status='active'
    and users.locally_invalidated_at is null
    and state.singleton
    and state.last_complete_at is not null
    and generation.generation_id=state.active_generation_id
    and generation.status='complete'
    and users.last_confirmed_generation_id=generation.generation_id
    and member.generation_id=generation.generation_id
    and member.internal_user_id=users.internal_user_id
    and member.status='active'
    and (
      state.last_complete_at > database_now - interval '24 hours'
      or users.role in ('platform_owner','management_viewer')
    )
  returning session.session_id,session.internal_user_id,users.role::text,
    session.hard_stale_read_only,session.csrf_hash,
    session.csrf_hash_key_version;
end
$function$;

create function platform_control.owner_change_precondition_v22(
  selected_generation_id uuid,
  selected_target_id uuid,
  accept_stale_generation boolean
) returns table (
  directory_generation_digest text,
  protected_target_lookup_hash text,
  protected_target_lookup_version integer,
  current_owner_internal_user_id uuid,
  current_owner_row_version bigint,
  target_row_version bigint
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_last_complete_at timestamptz;
begin
  if accept_stale_generation is null then
    raise check_violation using message='owner precondition unavailable';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();
  select state.last_complete_at into selected_last_complete_at
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  where state.singleton
    and state.active_generation_id=selected_generation_id;
  if not found then
    raise check_violation using message='owner precondition unavailable';
  end if;
  if selected_last_complete_at <= database_now-interval '24 hours' then
    if not accept_stale_generation then
      raise check_violation using
        message='explicit stale generation acceptance required';
    end if;
  elsif accept_stale_generation then
    raise check_violation using message='stale generation acceptance invalid';
  end if;
  if not exists (
    select 1 from platform_control.internal_users users
    join platform_control.directory_members member
      on member.internal_user_id=users.internal_user_id
     and member.generation_id=selected_generation_id
     and member.status='active'
    where users.internal_user_id=selected_target_id
      and users.status='active'
      and users.locally_invalidated_at is null
  ) then
    raise check_violation using
      message='target unavailable in selected generation';
  end if;
  return query select * from platform_control.owner_change_precondition(
    selected_generation_id,selected_target_id
  );
end
$function$;

create function platform_control.change_platform_owner_v22(
  selected_operation_id uuid,
  selected_operation text,
  selected_target_id uuid,
  selected_generation_id uuid,
  selected_expected_owner_id uuid,
  selected_expected_owner_version bigint,
  selected_expected_target_version bigint,
  selected_audit_event_id uuid,
  accept_stale_generation boolean
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare ignored record;
begin
  if selected_operation='bind' and accept_stale_generation then
    raise check_violation using message='stale generation acceptance invalid';
  end if;
  select * into ignored from platform_control.owner_change_precondition_v22(
    selected_generation_id,selected_target_id,accept_stale_generation
  );
  return platform_control.change_platform_owner_v2(
    selected_operation_id,selected_operation,selected_target_id,
    selected_generation_id,selected_expected_owner_id,
    selected_expected_owner_version,selected_expected_target_version,
    selected_audit_event_id
  );
end
$function$;

create function platform_control.append_hard_stale_access_v22(
  selected_event_id uuid,
  selected_actor_id uuid,
  selected_access_kind text,
  selected_target text,
  selected_request_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_last_complete_at timestamptz;
begin
  if selected_access_kind not in ('login','read')
     or selected_target not in (
       'self','management_user_directory','governance_audit',
       'management_projection'
     )
     or (selected_access_kind='login' and selected_target <> 'self')
     or (selected_access_kind='read' and selected_target = 'self')
  then raise check_violation using message='hard stale audit invalid'; end if;
  select state.last_complete_at into selected_last_complete_at
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.internal_users users
    on users.internal_user_id=selected_actor_id
   and users.status='active'
   and users.locally_invalidated_at is null
   and users.role in ('platform_owner','management_viewer')
   and users.last_confirmed_generation_id=generation.generation_id
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
   and member.internal_user_id=users.internal_user_id
   and member.status='active'
  where state.singleton
    and state.last_complete_at <= clock_timestamp()-interval '24 hours';
  if not found then
    raise check_violation using message='hard stale audit rejected';
  end if;
  insert into platform_control.audit_events(
    audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after
  ) values (
    selected_event_id,selected_actor_id,
    case selected_access_kind when 'login'
      then 'hard_stale_privileged_login_completed'
      else 'hard_stale_privileged_read_completed' end,
    case selected_access_kind when 'login'
      then 'platform_authentication' else 'platform_management' end,
    selected_target,selected_request_id,'completed',
    'privileged_last_generation',
    jsonb_build_object(
      'freshness_reason','hard_stale',
      'last_complete_at',selected_last_complete_at
    )
  );
  return selected_event_id;
end
$function$;

revoke all on function platform_control.consume_attempt_and_issue_session_v22(
  uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean
) from public;
revoke all on function platform_control.authenticate_web_session_v22(
  bytea,integer,integer
) from public;
revoke all on function platform_control.owner_change_precondition_v22(
  uuid,uuid,boolean
) from public;
revoke all on function platform_control.change_platform_owner_v22(
  uuid,text,uuid,uuid,uuid,bigint,bigint,uuid,boolean
) from public;
revoke all on function platform_control.append_hard_stale_access_v22(
  uuid,uuid,text,text,uuid
) from public;

do $migration$
declare selected_app name; selected_migrator name; role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then selected_app:='platform_control_app';
       selected_migrator:='platform_control_migrator';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then selected_app:='platform_control_app_preview';
       selected_migrator:='platform_control_migrator_preview';
  else raise insufficient_privilege using
    message='hard stale session owner/environment mismatch';
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
      'revoke all on function platform_control.consume_attempt_and_issue_session_v22('
      'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.authenticate_web_session_v22('
      'bytea,integer,integer) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.owner_change_precondition_v22('
      'uuid,uuid,boolean) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.change_platform_owner_v22('
      'uuid,text,uuid,uuid,uuid,bigint,bigint,uuid,boolean) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.append_hard_stale_access_v22('
      'uuid,uuid,text,text,uuid) from %I',role_name
    );
  end loop;
  execute format(
    'revoke execute on function platform_control.consume_attempt_and_issue_session('
    'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer) from %I',
    selected_app
  );
  execute format(
    'revoke execute on function platform_control.authenticate_web_session('
    'bytea,integer,integer) from %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.consume_attempt_and_issue_session_v22('
    'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.authenticate_web_session_v22('
    'bytea,integer,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.owner_change_precondition_v22('
    'uuid,uuid,boolean) to %I',selected_migrator
  );
  execute format(
    'grant execute on function platform_control.change_platform_owner_v22('
    'uuid,text,uuid,uuid,uuid,bigint,bigint,uuid,boolean) to %I',
    selected_migrator
  );
  execute format(
    'grant execute on function platform_control.append_hard_stale_access_v22('
    'uuid,uuid,text,text,uuid) to %I',
    case when selected_app='platform_control_app'
      then 'platform_audit_append'
      else 'platform_audit_append_preview' end
  );
end
$migration$;
