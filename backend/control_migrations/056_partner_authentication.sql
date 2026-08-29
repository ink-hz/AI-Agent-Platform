create function platform_control.create_partner_login_attempt_v56(
  selected_login_attempt_id uuid,
  selected_provider_kind text,
  selected_state_digest bytea,
  selected_state_key_version integer
) returns uuid
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  attempt_created_at timestamptz;
begin
  perform platform_control.require_partner_app_v54();
  if selected_login_attempt_id is null
     or selected_provider_kind is null
     or selected_provider_kind=''
     or selected_provider_kind<>btrim(selected_provider_kind)
     or length(selected_provider_kind)>128
     or position(':' in selected_provider_kind)<>0
     or selected_state_digest is null
     or octet_length(selected_state_digest)<>32
     or selected_state_key_version is null
     or selected_state_key_version<=0
  then
    raise check_violation using message='partner login attempt invalid';
  end if;
  attempt_created_at := clock_timestamp();
  insert into platform_control.partner_login_attempts(
    login_attempt_id,provider_kind,state_digest,state_key_version,status,
    created_at,expires_at
  ) values (
    selected_login_attempt_id,selected_provider_kind,selected_state_digest,
    selected_state_key_version,'pending',attempt_created_at,
    attempt_created_at+interval '10 minutes'
  );
  return selected_login_attempt_id;
exception
  when unique_violation then
    raise check_violation using message='partner login attempt conflict';
end
$function$;

create function platform_control.consume_partner_login_attempt_v56(
  selected_provider_kind text,
  selected_state_digest bytea,
  selected_state_key_version integer
) returns text
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  attempt platform_control.partner_login_attempts%rowtype;
begin
  perform platform_control.require_partner_app_v54();
  if selected_provider_kind is null
     or selected_provider_kind=''
     or selected_provider_kind<>btrim(selected_provider_kind)
     or length(selected_provider_kind)>128
     or position(':' in selected_provider_kind)<>0
     or selected_state_digest is null
     or octet_length(selected_state_digest)<>32
     or selected_state_key_version is null
     or selected_state_key_version<=0
  then
    return 'invalid';
  end if;
  select candidate.* into attempt
  from platform_control.partner_login_attempts candidate
  where candidate.provider_kind=selected_provider_kind
    and candidate.state_digest=selected_state_digest
    and candidate.state_key_version=selected_state_key_version
  for update;
  if attempt.login_attempt_id is null then
    return 'invalid';
  end if;
  if attempt.status<>'pending' then
    return 'replay';
  end if;
  if attempt.expires_at<=clock_timestamp() then
    update platform_control.partner_login_attempts candidate
    set status='expired',consumed_at=clock_timestamp()
    where candidate.login_attempt_id=attempt.login_attempt_id;
    return 'expired';
  end if;
  update platform_control.partner_login_attempts candidate
  set status='consumed',consumed_at=clock_timestamp()
  where candidate.login_attempt_id=attempt.login_attempt_id;
  return 'consumed';
end
$function$;

revoke all on function platform_control.create_partner_login_attempt_v56(
  uuid,text,bytea,integer
) from public;
revoke all on function platform_control.consume_partner_login_attempt_v56(
  text,bytea,integer
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  selected_app := case
    when current_database()='agent_platform_control'
      and current_user='platform_control_owner'
      then 'platform_control_app'
    when current_database()='agent_platform_control_preview'
      and current_user='platform_control_owner_preview'
      then 'platform_control_app_preview'
    else null
  end;
  if selected_app is null then
    raise insufficient_privilege using
      message='Partner authentication migration owner invalid';
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
      'revoke all on function platform_control.create_partner_login_attempt_v56(uuid,text,bytea,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.consume_partner_login_attempt_v56(text,bytea,integer) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.create_partner_login_attempt_v56(uuid,text,bytea,integer) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.consume_partner_login_attempt_v56(text,bytea,integer) to %I',
    selected_app
  );
end
$migration$;
