create function platform_control.ensure_first_execution_worker_v33(
  selected_worker_id text,
  selected_key_id text,
  selected_public_key bytea,
  selected_allowed_agent_ids text[],
  selected_change_reference text,
  selected_request_id uuid
) returns text
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  worker_row platform_control.execution_workers%rowtype;
  key_row platform_control.execution_worker_keys%rowtype;
  key_count integer;
  was_absent boolean;
  worker_found boolean;
begin
  if selected_worker_id <> 'agentops-mac-primary'
     or selected_key_id !~ '^worker-v[1-9][0-9]*$'
     or octet_length(selected_public_key) <> 32
     or selected_allowed_agent_ids is distinct from array[
       'hr-bot','fae-bot','marketing-prospecting-bot',
       'marketing-inbound-bot','marketing-voice-bot',
       'marketing-intelligence-bot','marketing-gtm-bot','agent-brain-bot'
     ]::text[]
     or selected_change_reference is distinct from 'AGENT_BRAIN_BOOTSTRAP_001'
     or selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
  then
    raise check_violation using message='first worker bootstrap input invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  perform pg_advisory_xact_lock(
    hashtextextended('execution-worker:' || selected_worker_id, 0)
  );
  select * into worker_row
    from platform_control.execution_workers
    where worker_id=selected_worker_id for update;
  worker_found := found;
  select count(*) into key_count
    from platform_control.execution_worker_keys
    where worker_id=selected_worker_id;
  was_absent := not worker_found and key_count=0;
  if was_absent then
    perform platform_control.register_execution_worker_v28(
      selected_worker_id,selected_key_id,selected_public_key,
      selected_allowed_agent_ids,selected_change_reference,selected_request_id
    );
  end if;

  select * into worker_row
    from platform_control.execution_workers
    where worker_id=selected_worker_id for update;
  select * into key_row
    from platform_control.execution_worker_keys
    where worker_id=selected_worker_id and key_id=selected_key_id for update;
  select count(*) into key_count
    from platform_control.execution_worker_keys
    where worker_id=selected_worker_id;
  if worker_row.worker_id is null
     or worker_row.status <> 'active'
     or worker_row.revoked_at is not null
     or worker_row.allowed_agent_ids is distinct from selected_allowed_agent_ids
     or key_row.key_id is null
     or key_row.status <> 'active'
     or key_row.revoked_at is not null
     or key_row.public_key is distinct from selected_public_key
     or key_count <> 1
  then
    raise check_violation using message='first worker bootstrap state mismatch';
  end if;
  return case when was_absent then 'registered' else 'existing' end;
end
$function$;

create function platform_control.grant_agent_brain_acceptance_v33(
  selected_grant_id uuid,
  selected_member_id uuid,
  selected_actor_id uuid,
  selected_change_reference text,
  selected_request_id uuid
) returns table(hr_allowed boolean, marketing_gtm_denied boolean)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  actor_role text;
  actor_status text;
  member_role text;
  member_status text;
  granted_id uuid;
  grant_row platform_control.agent_use_grants%rowtype;
  existing_audit platform_control.audit_events%rowtype;
  expected_details jsonb;
begin
  select role::text,status into actor_role,actor_status
    from platform_control.internal_users where internal_user_id=selected_actor_id;
  select role::text,status into member_role,member_status
    from platform_control.internal_users where internal_user_id=selected_member_id;
  if actor_role not in ('platform_owner','platform_admin')
     or actor_status is distinct from 'active'
     or member_role is distinct from 'member'
     or member_status is distinct from 'active'
     or selected_change_reference is distinct from 'AGENT_BRAIN_ACCEPTANCE_001'
     or selected_grant_id is null
     or selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
  then
    raise check_violation using message='acceptance grant identity invalid';
  end if;
  if actor_role = 'platform_owner' then
    granted_id := platform_control.grant_agent_use_scope_v29(
      selected_grant_id,'hr-bot','user',selected_member_id,null,false,
      selected_actor_id,selected_change_reference,selected_request_id
    );
  else
    expected_details := jsonb_build_object(
      'agent_id','hr-bot','grant_id',selected_grant_id,
      'include_descendants',false,'reference',selected_change_reference,
      'target_department_key',null,'target_internal_user_id',selected_member_id,
      'target_kind','user'
    );
    perform pg_advisory_xact_lock(
      hashtextextended('agent-use-request:' || selected_request_id::text,0)
    );
    select * into existing_audit from platform_control.audit_events
      where audit_event_id=selected_request_id for update;
    if found then
      if existing_audit.actor_internal_user_id is distinct from selected_actor_id
         or existing_audit.event_type <> 'agent_use_scope_granted'
         or existing_audit.target_type <> 'agent_use_scope'
         or existing_audit.target_internal_id <> selected_grant_id::text
         or existing_audit.request_id <> selected_request_id
         or existing_audit.result <> 'completed'
         or existing_audit.reason_code <> 'offline_maintenance'
         or existing_audit.sanitized_before_after is distinct from expected_details
      then
        raise check_violation using message='acceptance grant request collision';
      end if;
    else
      perform pg_advisory_xact_lock(
        hashtextextended('agent-use-grant:' || selected_grant_id::text,0)
      );
      insert into platform_control.audit_events (
        audit_event_id,actor_internal_user_id,event_type,target_type,
        target_internal_id,request_id,result,reason_code,sanitized_before_after
      ) values (
        selected_request_id,selected_actor_id,'agent_use_scope_granted',
        'agent_use_scope',selected_grant_id::text,selected_request_id,
        'completed','offline_maintenance',expected_details
      );
      insert into platform_control.agent_use_grants (
        agent_use_grant_id,agent_id,target_kind,target_internal_user_id,
        target_department_key,include_descendants,created_by,created_audit_event_id
      ) values (
        selected_grant_id,'hr-bot','user',selected_member_id,null,false,
        selected_actor_id,selected_request_id
      );
    end if;
    granted_id := selected_grant_id;
  end if;
  if granted_id is distinct from selected_grant_id then
    raise check_violation using message='acceptance grant result invalid';
  end if;
  select * into grant_row
    from platform_control.agent_use_grants
    where agent_use_grant_id=selected_grant_id
    for update;
  if grant_row.agent_use_grant_id is null
     or grant_row.agent_id <> 'hr-bot'
     or grant_row.target_kind <> 'user'
     or grant_row.target_internal_user_id is distinct from selected_member_id
     or grant_row.target_department_key is not null
     or grant_row.include_descendants
     or grant_row.created_by is distinct from selected_actor_id
     or grant_row.created_audit_event_id is distinct from selected_request_id
     or grant_row.revoked_at is not null
     or grant_row.revoked_by is not null
     or grant_row.revoked_audit_event_id is not null
  then
    raise check_violation using message='acceptance grant state mismatch';
  end if;
  hr_allowed := platform_control.has_agent_use_scope_v29(
    selected_member_id,'hr-bot'
  );
  marketing_gtm_denied := not platform_control.has_agent_use_scope_v29(
    selected_member_id,'marketing-gtm-bot'
  );
  if not hr_allowed or not marketing_gtm_denied then
    raise check_violation using message='acceptance grant verification failed';
  end if;
  return next;
end
$function$;

revoke all on function platform_control.ensure_first_execution_worker_v33(
  text,text,bytea,text[],text,uuid
) from public;
revoke all on function platform_control.grant_agent_brain_acceptance_v33(
  uuid,uuid,uuid,text,uuid
) from public;

do $migration$
declare
  selected_suffix text;
  selected_maintenance text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then selected_suffix := '';
    when 'platform_control_owner_preview' then selected_suffix := '_preview';
    else raise insufficient_privilege using
      message='control migration must run as an approved owner role';
  end case;
  selected_maintenance := 'platform_control_maintenance' || selected_suffix;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.ensure_first_execution_worker_v33('
      'text,text,bytea,text[],text,uuid) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.grant_agent_brain_acceptance_v33('
      'uuid,uuid,uuid,text,uuid) from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.ensure_first_execution_worker_v33('
    'text,text,bytea,text[],text,uuid) to %I',selected_maintenance
  );
  execute format(
    'grant execute on function platform_control.grant_agent_brain_acceptance_v33('
    'uuid,uuid,uuid,text,uuid) to %I',selected_maintenance
  );
end
$migration$;
